# Per-tenant conversation history — pre-reg (H.0 de #428)

**Fecha:** 2026-05-22
**Operator:** Samuel (en nombre de Simon)
**Estado:** pre-reg pre-implementación, gating de H.1+
**Estimación:** ~2 – 3 días (H.1 → H.7); H.8 (export) deferido
**Epic:** #428 — Per-tenant conversation history del copiloto
**Issue gating:** H.0 — Confirm design + pre-reg (este doc)

## Contexto

El copiloto del epic #400 ya persiste **metadata** por turn en `agent_conversations` (tokens, costo, modelo, latencia, surface, provider, reasoning_tokens) pero **nunca el texto literal** del mensaje del user ni la respuesta del assistant. El comentario en `api/agent/audit.py:38` lo dice explícito:

```python
content_summary: Optional[str] = None,   # redacted; never the raw text
```

La conversación vive **únicamente en React in-memory** (`frontend/src/agent/useAgentStream.ts:67`, `conversationIdRef` con UUID fresh en cada `useAgentStream` mount). Reload de página → conversación perdida. Cierre del browser → conversación perdida. **No hay endpoint para rehidratar history.**

Esto era OK con trading-spacial single-user (Samuel ya conocía sus propios chats). Pero post-cierre de #271 papá Simón (`id=2`) y María (`id=3`) están en producción usando el copiloto. El onboarding wizard (#427) también pipeline. Necesitamos: **historial persistente per-tenant para que cada usuario pueda volver a una conversación anterior** ("¿qué me dijo el copiloto ayer sobre PENDLE?").

### Estado actual relevante (Epic #400 post-Phase 5)

| Componente | Archivo | Estado |
|---|---|---|
| Tabla audit + cost ledger | `agent_conversations` en `db/schema.py:393-535` | Funcionando, NO almacena texto |
| Tabla side-effects | `agent_side_effects` | Funcionando, signed proposals con TTL |
| Tabla quotas | `agent_quotas` | Computed-on-read (sin cron) |
| Write path | `api/agent/audit.py::TurnAuditWrapper.__anext__` | 1 row/turn en MessageEnd o ErrorEvent |
| Hook React | `frontend/src/agent/useAgentStream.ts` | In-memory only, UUID fresco/mount |
| Surfaces | dock, symbol_detail, brief | Cada uno con su propio `useAgentStream` |

### Investigación realizada (resumen del epic body)

| Sistema | Modelo | Veredicto |
|---|---|---|
| MemPalace | Wings/halls jerárquico + ChromaDB verbatim + knowledge graph + hybrid retrieval | Agent-facing (el modelo busca su recuerdo), overkill para nuestra escala |
| Anthropic Memory Tool | `memory_20250818` ops view/create/str_replace/insert/delete/rename | Agent-facing también; presupuesto ~$1/día/tenant no justifica overhead |
| Vercel ai-chatbot | `Message_v2` jsonb, atomic per turn, prefix-based naming, Postgres + Drizzle | **Blueprint más cercano.** Multi-tenant, plano, sin branching. Adoptado adaptado a SQLite. |
| ChatGPT export | Tree con `parent_id` por message (branching) | Overkill — papá/María no editan ni regeneran |
| Redis chat history | Hashes por session con TTL | Stack es single-machine SQLite, no metemos Redis |

### Conclusiones de investigación (ya en epic body, refrescadas)

1. **Almacenamiento verbatim, no summarized.** Costo despreciable < $1/día/tenant.
2. **Tabla plana, no tree, no branching.** KISS.
3. **Atomic insert per turn** — extender el path existente (`TurnAuditWrapper`), no crear uno nuevo.
4. **Sin embeddings / semantic search en v1.** `LIKE %query%` + ordering por `ts DESC` es suficiente.
5. **Retention policy explícita** — 90 días default, computed-on-read TTL (mirror `agent_quotas`).
6. **IDOR-safe por construcción** — `tenant_id` SIEMPRE del JWT, mirror del patrón de `api/positions.py`, `api/user_preferences.py`.

## Decisiones a confirmar (gating de H.1+)

Cada decisión lleva un **default sugerido** + razón. Sin override del operator, se ejecuta el default.

### D.1 — Separación de tablas (agent_messages vs extender agent_conversations)

**Default: SEPARAR.** Nueva tabla `agent_messages` (contenido visible al user) + nueva tabla `agent_conversation_meta` (1 row por conversation, para listado). `agent_conversations` se mantiene como **audit + cost ledger** intocada.

**Razón:** las retention policies son distintas. El audit log es operacional — se mantiene mientras dure el deployment, sirve a `/agent/metrics` y compliance del cobro de quotas. El contenido visible es UX y debe ser borrable per-request del user (90d default). Acoplarlas obliga a meter retention columns + cleanup-on-read en una tabla que hoy no las necesita; complicaría queries de `/agent/metrics` con filtros `WHERE expires_at > NOW()` que no aportan.

**Costo de la separación:** 1 join extra para reconstruir "cuál es el costo total acumulado de esta conversation". No bloqueante — esa query no es hot path; vive en `/agent/metrics` que ya es operacional, no user-facing.

### D.2 — Retention default

**Default: 90 días.** `expires_at = inserted_ts + 90 days` calculado en write path.

**Razón:** alinea con prácticas razonables de chat history retention (ChatGPT free expone una ventana similar; muchos productos enterprise van a 30-180 días). 90 días balancea utility ("¿qué me dijo el copiloto el mes pasado?") con cota de growth.

**Override esperado:** ninguno por ahora. Si papá pide "yo quiero todo, no borres nada" — fácil: subir `RETENTION_DAYS` a 365 o `null` (sin expiración). El default se elige porque a 3 users × ~1KB/message × decenas de messages/day × 365 days seguiría siendo decenas de MB; sostenible. Pero 90 es el default conservador.

### D.3 — Title generation strategy

**Default: primeros 80 chars del primer user message,** truncados con `…` si es más largo. Calculado en el write path la primera vez que se ve `conversation_id`.

**Alternativa rechazada (default explícito):** LLM-generated title via call extra a Claude/DS para summarizar el primer turn → "pretty titles" tipo "Discusión sobre stop loss de PENDLE". Costo extra de tokens × cada nueva conversation. **No vale la pena** para 3 users — gastar centavos para un título un poco más bonito que `"que me dices de PENDLE hoy?"` (que ya es un título perfectamente útil).

**Cuándo revisitar:** si en uso real (papá usando) los títulos derivados son ininteligibles porque sus user messages son muy cortos ("hola", "y BTC?") → mover a LLM-summary asíncrono post-message-end. Issue separable, no bloqueante.

### D.4 — Storage del reasoning chain (DS-R1 chain-of-thought)

**Default: SÍ guardarlo,** en columna `reasoning TEXT` nullable.

**Razón:** el reasoning chain es **valioso para el user** (ver cómo el modelo piensa antes de la respuesta final, especialmente en decisiones de trading). Ya se streamea al frontend (`useAgentStream.ts:211-228 case 'reasoning_delta'`) y se renderiza en collapsible `<details>` panel. Re-hidratación sin reasoning rompería esa UX visualmente — la conversation de hoy mostraría reasoning, la de ayer no.

**Costo:** ~5x el tamaño del assistant text en bytes (R1 reasoning es verbose). Manejable: 1KB × 5 × decenas messages × decenas conversations × 3 users ≈ unos pocos MB total bajo retention 90d.

**Anthropic claude-X no popula reasoning** — la columna es NULL para esos turns. Mismo patrón que `reasoning_tokens` ya en `agent_conversations`.

### D.5 — Delete semantics (soft vs hard)

**Default: SOFT delete.** `DELETE /agent/conversations/{id}` setea `expires_at = NOW()` en `agent_messages` rows + `agent_conversation_meta` row. Cleanup-on-read filtra. No DELETE row físico inmediato.

**Razón:**
- **Compliance / audit:** el cobro de quotas se queda en `agent_conversations` (audit ledger) que NO se toca. Costo cobrado se mantiene auditable independientemente del soft-delete del contenido.
- **Recovery:** si el user clickea "borrar" por error y se da cuenta en minutos, hay ventana para revertir (no implementamos el "undo" en v1 — pero la opción existe sin migración futura).
- **Mirror `agent_quotas` pattern:** cleanup-on-read computed sin cron simplifica deployment.

**Cleanup físico:** opcional via job que haga `DELETE WHERE expires_at < NOW() - 7 days` periódicamente. **NO bloqueante** para v1 — la cláusula WHERE en GETs ya esconde rows expiradas. Si en algún momento `du -sh` muestra growth feo, sumamos el job (separable).

### D.6 — Branching v1

**Default: NO.** Sin edit-and-regenerate, sin parent_id en messages, sin tree structure. Lista plana ordenada por `ts ASC`.

**Razón:** papá y María no van a editar mensajes pasados ni regenerar respuestas alternativas. Es un trading copilot, no un editor de prompts. ChatGPT-style branching agrega complexity (UI + storage + retrieval) sin demanda real para el use case.

**Cuándo revisitar:** si en algún momento llega user feedback explícito tipo "quiero re-preguntar lo mismo a Anthropic en vez de DeepSeek y comparar respuestas" — sí, ese es un caso interesante. Pero hoy es esculpir contra demanda fantasma.

### D.7 — Multi-tenant scope (decisión recordada del memory `feedback_multitenant_default`)

**Locked, no abierta a override:** todos los endpoints H.3 (`GET /conversations`, `GET /conversations/{id}/messages`, `DELETE`, `POST /pin`) DEBEN derivar `tenant_id` del JWT vía `get_current_tenant_id`. Cero parámetros tenant-id-from-body/query/header. 404 si el `conversation_id` no pertenece al `tenant_id` del JWT — NO 403 (no se filtra existencia cross-tenant). Mirror del patrón ya validado en `api/positions.py` + `api/user_preferences.py` + IDOR suite B.7 (#260).

## Schemas propuestos

### `agent_messages` — contenido visible

```sql
CREATE TABLE agent_messages (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id       INTEGER NOT NULL,
  conversation_id TEXT    NOT NULL,
  ts              TEXT    NOT NULL,
  role            TEXT    NOT NULL,   -- 'user' | 'assistant'
  content         TEXT    NOT NULL,   -- raw text, verbatim
  reasoning       TEXT,                -- DS-R1 chain-of-thought (NULL para Anthropic / role=user)
  tool_chips_json TEXT,                -- inline tool-use status chips (mirror del shape de ToolChip en frontend/src/agent/types.ts)
  proposals_json  TEXT,                -- signed proposal envelopes (close_position, etc.); estado terminal en re-hidratación
  expires_at      TEXT NOT NULL        -- retention TTL (computed-on-read)
);
CREATE INDEX idx_agent_messages_tenant_conv_ts ON agent_messages(tenant_id, conversation_id, ts ASC);
CREATE INDEX idx_agent_messages_tenant_ts      ON agent_messages(tenant_id, ts DESC);
CREATE INDEX idx_agent_messages_expires        ON agent_messages(expires_at);
```

### `agent_conversation_meta` — 1 row por conversation

```sql
CREATE TABLE agent_conversation_meta (
  conversation_id TEXT PRIMARY KEY,
  tenant_id       INTEGER NOT NULL,
  title           TEXT,                -- primeros 80 chars del primer user msg, derivado en H.2
  surface         TEXT NOT NULL,       -- 'dock' | 'symbol_detail' | 'brief'
  first_ts        TEXT NOT NULL,
  last_ts         TEXT NOT NULL,
  message_count   INTEGER NOT NULL DEFAULT 0,
  pinned          INTEGER NOT NULL DEFAULT 0,
  expires_at      TEXT NOT NULL        -- mismo TTL que sus messages (90d desde last_ts)
);
CREATE INDEX idx_agent_conv_meta_tenant_last  ON agent_conversation_meta(tenant_id, last_ts DESC);
CREATE INDEX idx_agent_conv_meta_tenant_pinned ON agent_conversation_meta(tenant_id, pinned DESC, last_ts DESC);
```

**Nota sobre `meta.expires_at`:** se computa como `last_ts + 90 days`, no `first_ts + 90 days`. Esto significa que una conversation activa NO expira mientras el user siga conversando. Conversation queda dormida → cuenta 90 días desde el último turn → expira. Refleja mejor "memoria útil".

### Patrón de write — extensión de `TurnAuditWrapper`

En `__anext__` (`api/agent/audit.py:155`), al detectar `MessageEnd`:

1. Audit row a `agent_conversations` (sin cambios).
2. **Nuevo:** Si es el primer turn de este `conversation_id` (CONDICIÓN `INSERT OR IGNORE` en `agent_conversation_meta`), crear meta row con `title = first_80_chars(first_user_message)`. Si ya existe, UPDATE `last_ts`, incrementar `message_count`.
3. **Nuevo:** INSERT user message row + assistant message row en `agent_messages`.

Por qué insertar el user message en el mismo path: el user message ya viaja en el `messages` array del request — accesible vía closure/captura en el wrapper. NO necesita un segundo path de write. Asegura **atomicidad** (los dos messages se persisten juntos o ninguno; failure de DB → log warning, ningún parcial).

**Fail-tolerance:** misma disciplina que `record_turn`: `try/except` swallow + `log.warning`. Una miss de history es annoying; un 500 al user es peor.

## Threat model resumido

### Vectores cubiertos

1. **IDOR cross-tenant read:** user A no puede `GET /conversations/{conv_de_B}/messages`. El IDOR suite (`tests/test_agent_history_idor.py`, nuevo en H.7) verifica:
   - Listar (`GET /conversations`) — sólo conversations de tenant del JWT.
   - Leer (`GET /conversations/{id}/messages`) — 404 si conversation no pertenece al tenant.
   - Borrar (`DELETE`) — 404 idem.
   - Pin (`POST /pin`) — 404 idem.
2. **Storage exhaustion:** caps implícitos por retention (90d) + low traffic. Si crece feo → cleanup job (D.5).
3. **Content leak (filesystem):** no encryption at rest a nivel column en v1. Confiamos en filesystem encryption + acceso SSH limitado (operador único con sudo). Documentado, no implementado. Mismo trade-off que `notify_channels_json` con bot tokens en `user_preferences` (#421).
4. **XSS via stored content:** `agent_messages.content` se renderiza en el dock con el mismo `DockText` actual (`React.Fragment` + split en `**bold**`) que ya escapa por default (React no inyecta HTML raw). Verificar en H.5 que no agreguemos `dangerouslySetInnerHTML` por accidente.
5. **Proposal signature replay:** los `proposals_json` guardados contienen signed payloads con TTL. Los `expires_at` del proposal son independientes del retention de la message — proposals expiran rápido (minutos), la message sigue visible hasta los 90 días. En re-hidratación mostrar el proposal en estado terminal (`expired`, `ok`, `error`, `drift`). El estado se persiste también — si en algún momento el user confirmó el proposal, el dock re-hidratado debe mostrar `ok`, no re-ofrecer el botón confirm.

### Vectores NO cubiertos v1

- **Encryption at rest column-level** del contenido. Trade-off explícito.
- **Per-conversation access control granular** (e.g. "share esta conversation con otro user"). Out of scope — papá y María no comparten.

## Acceptance criteria de H.0 (este sub-issue)

- [x] Pre-reg doc publicado en `docs/superpowers/specs/es/2026-05-22-conversation-history-pre-reg.md`.
- [ ] Operator aprobó los 7 defaults (D.1–D.7) o señaló overrides.
- [ ] PR de H.0 mergeado.

Después: H.1 (schema migration) puede arrancar sin más checks.

## Acceptance criteria del epic completo (recordatorio)

- Papá puede abrir el dock, ver el sidebar "Historial", clickear una conversation de ayer, y leer lo que le dijo el copiloto.
- Papá puede continuar esa conversation (siguiente turn extiende el transcript).
- Papá puede borrar una conversation; ya no aparece en su sidebar.
- Papá NO puede ver conversations de Samuel ni de María (IDOR suite verde).
- Conversation con un proposal `close_position` se re-hidrata mostrando el chip en estado terminal correcto.
- Retention 90d funciona (mensaje creado hace 91 días no se devuelve via GET).
- Storage no crece sin límite (test de carga: 1000 messages → DB size acotado).

## Non-goals (deferred / future)

- **Semantic search** (embeddings + vector search ≈ MemPalace ChromaDB) — sumar SI volume justifica, después de v1 estar en prod estable.
- **Conversation branching** (ChatGPT-style edit & regen) — KISS, papá/María no necesitan.
- **Cross-conversation context** (Anthropic Memory Tool style) — distinto problema, no es UX-history.
- **LLM-summary titles** (vs first-80-chars) — sumar si demand real.
- **Export endpoint** (H.8) — defer hasta que un user lo pida.
- **Encryption at rest column-level** — confiamos en filesystem + SSH.
- **Cleanup job físico** — la cláusula WHERE expires_at oculta rows; sumar job si growth lo justifica.

## Roadmap de sub-issues (H.0 → H.7)

```
H.0  ─┐ pre-reg + confirm (este doc) ────────► PR docs/
       │
H.1  ─┴► schema migration (agent_messages + meta + indexes) ─► PR backend/db
H.2  ───► backend write path (extender TurnAuditWrapper) ────► PR backend
H.3  ───► backend read endpoints (list/messages/delete/pin) ─► PR backend + IDOR
H.4  ───► frontend conversation list sidebar ───────────────► PR frontend
H.5  ───► frontend hidratación + new conversation flow ─────► PR frontend
H.6  ───► retention + cleanup-on-read ──────────────────────► PR backend
H.7  ───► threat model + IDOR tests ────────────────────────► PR tests
H.8  ───► export endpoint (DEFERRED v2) ────────────────────► out of v1
```

Cada H.X puede ser un PR independiente, mergeable en orden. H.3 + H.4 + H.5 forman el bloque user-visible — sólo prende value cuando los tres mergean. H.1 + H.2 + H.6 son backend-only sin UX visible (audit que crece, retention que filtra).

## Referencias técnicas

- Epic #400 (agent production-grade) — base del audit + side_effects + quotas.
- Cierre #271 (multi-tenant unblock 2026-05-16).
- Epic B #253 — modelo de aislamiento per-tenant.
- IDOR suite B.7 #260 — referencia para patrón de tests de cross-tenant.
- `docs/superpowers/specs/es/2026-05-19-trading-copilot-production-grade-pre-reg.md` — §9.1 schema de `agent_conversations`.
- [MemPalace repo](https://github.com/MemPalace/mempalace) — referencia hierarchical/verbatim (descartado).
- [Vercel ai-chatbot Message Flow](https://deepwiki.com/vercel/ai-chatbot/2.3-message-flow-and-persistence) — referencia atomic-insert (adoptado adaptado).
- [Anthropic Memory Tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool) — referencia agent-side memory (no aplica).
