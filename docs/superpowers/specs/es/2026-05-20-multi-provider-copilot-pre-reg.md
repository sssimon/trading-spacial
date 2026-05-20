# Multi-provider Copilot — Pre-registro

**Fecha:** 2026-05-20
**Estado:** PROPUESTA — pendiente de aprobación del operator
**Continúa:** `docs/superpowers/specs/es/2026-05-19-trading-copilot-production-grade-pre-reg.md` (epic #400)
**Pausa:** Phase 6 de epic #400 (rollout) queda suspendido hasta que este epic ship.

---

## 0. TL;DR

El copilot de epic #400 está acoplado a Anthropic en 8 puntos concretos del código (SDK directo, event names, tool schema, `cache_control` blocks, cost model, model IDs, surface defaults, FakeAnthropicClient). Este epic introduce una capa de abstracción de provider que deja el flujo del loop intacto y mueve los detalles wire-format a adapters por-provider. Primera implementación: DeepSeek (chat V3 + reasoner R1) per-surface.

**Lo que se reutiliza sin tocar** (~70% del código del epic #400):
- `api/agent/proposals.py` — HMAC + idempotency + TTL (provider-agnostic)
- `api/agent/quotas.py` — daily/monthly spend (lee `cost_usd` del audit table)
- `api/agent/circuit_breaker.py` — sum sobre cost_usd, no le importa el provider
- `api/agent/safety.py` — opera sobre texto, agnóstico
- `api/agent/audit.py` — schema neutral
- `api/agent/router.py` — endpoints HTTP, casi todo agnóstico (el dispatch a propose_handlers no cambia)
- Frontend completo — consume nuestro SSE format, agnóstico
- 160 tests del backend — corren contra el FakeLLMClient unificado (sección 9)

**Lo que se refactoriza** (~30% del código):
- `api/agent/clients.py` — fábrica que devuelve el adapter correcto según el model id solicitado
- `api/agent/loop.py` — habla con la interfaz del provider, no con el SDK de Anthropic
- `api/agent/prompts/system.py` — el `cache_control` deja de ser literal, lo emite el adapter
- `api/agent/models.py` — `ALLOWED_MODELS` se expande, `SURFACE_MODEL_DEFAULTS` puede mapear a IDs de cualquier provider
- `api/agent/tools/registry.py` — la conversión del schema Pydantic → JSON schema delega al adapter
- `api/agent/streaming.py` — recibe un nuevo event type opcional (`reasoning_delta`) para R1

---

## 1. Motivación

Tres razones que el operator articuló o que se derivan de la arquitectura:

1. **Vendor risk.** Quedar pegado a un solo provider deja el sistema expuesto a cambios de pricing, deprecation de modelos, cuotas inesperadas. La cuenta de papá es de un operador único — un quota lock-out de Anthropic con el copilot habilitado degrada UX inmediatamente.

2. **Cost diferential.** DeepSeek-chat V3 a `$0.27/$1.10` per 1M tok (vs Sonnet 4.6 a `$3/$15`) es ~10x más barato a calidad razonable para tareas conversacionales. El total monthly del copilot bajo Anthropic con $5/día = ~$150/mes puede ser ~$15/mes con DeepSeek, **si la calidad y latencia se mantienen**. Eso es decisión que se hace con datos del bake, no a priori.

3. **Per-surface analítico.** DeepSeek-R1 emite `reasoning_content` separado del `content` final. Para surfaces analíticas (AutoTune, KillSwitch) donde el usuario quiere ver el razonamiento ("¿por qué proponen este tune?"), esto es UX nueva que Anthropic no ofrece nativamente.

**Razón explícita que NO motiva este epic:**
- "Anthropic es lento / malo" — no hay evidencia. Sonnet 4.6 + cache hit > 70% target del §14 del epic #400 da p95 < 4s. Phase 6 bake nunca ocurrió, así que la calidad de Anthropic en este sistema en producción es desconocida. El epic se justifica por flexibilidad arquitectural + cost, no por dissatisfaction con Anthropic.

---

## 2. Decisiones clave

### 2.1 Provider interface vs OpenAI-compat passthrough

**Opción A — Adapter genuino por provider.** Cada provider tiene su propio adapter que traduce de nuestra interfaz a su SDK nativo. Más código, más control, soporta features específicos (Anthropic prompt caching, DeepSeek reasoner).

**Opción B — Unificar todo en OpenAI-compat.** DeepSeek ofrece endpoint OpenAI-compatible; podríamos usar `openai` SDK con base_url=DeepSeek y olvidar el SDK nativo. Anthropic NO ofrece OpenAI-compat endpoint nativo. Tendríamos que mantener Anthropic separado igual.

**Decisión: A (adapter genuino).** Razones:
- Anthropic SDK > OpenAI SDK para Anthropic (caching, beta features, mejor streaming events).
- DeepSeek-R1's `reasoning_content` no está en OpenAI's spec, así que la abstracción "OpenAI-compat" no captura R1 plenamente.
- Un adapter por provider es ~150 líneas; un día de trabajo. La complejidad va al adapter, el loop principal se queda limpio.

### 2.2 Cache strategy divergente

Anthropic permite control client-side: `cache_control: {"type": "ephemeral"}` en hasta 4 bloques del system prompt. El operator declara dónde corta el prefijo cached.

DeepSeek tiene auto-caching del prefijo (sin control client-side). El cache es invisible — funciona si el prefijo es estable byte-a-byte, no funciona si cambia.

**Decisión:**
- El adapter de Anthropic emite los 4 bloques con `cache_control` (como hoy).
- El adapter de DeepSeek emite el system prompt como UN SOLO bloque sin `cache_control` (el campo ni se incluye en el wire). La determinismo del prefijo está garantizada por la disciplina del epic #400 (sort_keys, ensure_ascii, separators tightly bound).
- Test §11.6 (cache verification) se split:
  - Anthropic path: `cache_creation_input_tokens > 0` turn 1, `cache_read_input_tokens > 0` turn 2 (igual que hoy).
  - DeepSeek path: el wire del system prompt en turn 1 y turn 2 son byte-idénticos. No hay forma de verificar el cache hit desde nuestro lado (DeepSeek no reporta cache stats en `usage`), pero podemos verificar el invariant que ENABLES el cache (prefijo estable).

### 2.3 Reasoning content (DeepSeek R1)

DeepSeek-reasoner emite tokens de razonamiento ANTES del content final, vía `delta.reasoning_content` en el stream. Los tokens de reasoning se facturan a precio de output ($2.19/1M) — son hasta el ~70% del costo de una respuesta R1 típica.

Tres opciones de UX:
- **Discard**: el adapter consume `reasoning_content` pero NO lo emite al frontend. Pierdes la transparencia pero la UX es idéntica a chat-V3.
- **Surface como event aparte**: nuevo SSE event `reasoning_delta`. El frontend renderiza un panel colapsable "Razonamiento". El usuario decide si lo abre o no.
- **Hybrid**: discard por default; surface bajo un flag `?reasoning=true` per-request.

**Decisión: surface como event aparte.** Razones:
- En surfaces analíticos (AutoTune, KillSwitch) la transparencia del razonamiento es VALOR de la feature, no un costo accidental.
- El frontend ya tiene patrón de chips/panels colapsables (proposal chip, tool_chips).
- Discard es waste de tokens que se pagan igual.
- Hybrid es complejidad sin ganancia clara.

Nuevo SSE event:
```json
{"type": "reasoning_delta", "text": "Veo que el WR20 está en 28%..."}
```

Frontend lo renderiza como `<details><summary>Razonamiento</summary>{texto colapsable}</details>` debajo de la bubble del assistant. Default cerrado.

### 2.4 Cost model per-provider

Pricing source-of-truth en `api/agent/providers/{provider}/pricing.py` (no en `loop.py:_MODEL_PRICING`). Cada adapter sabe sus propios numbers.

Anthropic mantiene la struct actual (in/out + cache_read 0.1× + cache_creation 1.25×).

DeepSeek (cached Jan 2026):
- `deepseek-chat`: $0.27/$1.10 per 1M (in/out). Cache pricing automático no reportado en `usage`.
- `deepseek-reasoner`: $0.55/$2.19 per 1M (in/out). Reasoning tokens facturados como output.

`_estimate_cost_usd(model, usage)` se vuelve `provider.estimate_cost(usage)` — el adapter calcula su propio costo.

### 2.5 Per-surface defaults

Hoy: `SURFACE_MODEL_DEFAULTS` mapea surface → claude model id. Mantener el patrón, agregar DS:

```python
SURFACE_MODEL_DEFAULTS: dict[str, str] = {
    "dock":          "deepseek-chat",      # fast conversacional
    "symbol_detail": "deepseek-chat",      # fast scoped
    "kill_switch":   "deepseek-reasoner",  # analítico
    "autotune":      "deepseek-reasoner",  # analítico
    "historial":     "deepseek-chat",      # pasivo lectura
}
```

`ALLOWED_MODELS` se expande para incluir DS + mantener Anthropic (para fallback / comparación):

```python
ALLOWED_MODELS: frozenset[str] = frozenset({
    "deepseek-chat",
    "deepseek-reasoner",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "claude-opus-4-7",
})
```

Operator override per-turn vía `model: "..."` en `_AgentTurnRequest` sigue funcionando — ahora puede pedir `claude-opus-4-7` aunque el default sea DS.

### 2.6 Provider selection en runtime

¿Cómo dispatcha el `clients.py` factory al adapter correcto?

**Opción A — Por prefijo del model id**: `"claude-*"` → Anthropic, `"deepseek-*"` → DeepSeek. Simple, sin config extra.

**Opción B — Mapping explícito en config**: `cfg.agent.providers = {"deepseek-chat": "deepseek", ...}`. Más explícito pero más config drift.

**Decisión: A (por prefijo).** Adapter registry:
```python
PROVIDER_BY_PREFIX = {
    "claude":   "anthropic",
    "deepseek": "deepseek",
}
```

---

## 3. Arquitectura propuesta

### 3.1 Estructura de archivos

```
api/agent/
  providers/
    __init__.py              # exports the factory
    base.py                  # the LLMProvider interface
    anthropic_adapter.py     # current code, refactored to fit interface
    deepseek_adapter.py      # new
    registry.py              # PROVIDER_BY_PREFIX + get_provider_for_model()
  clients.py                 # now just delegates to providers.registry
  loop.py                    # talks to LLMProvider, not Anthropic SDK
  prompts/system.py          # emits provider-neutral blocks; adapter adds cache_control
  models.py                  # ALLOWED_MODELS expanded; defaults updated
  streaming.py               # +ReasoningDelta event
  tools/registry.py          # tool schema conversion delegated to adapter
```

### 3.2 La interfaz `LLMProvider`

```python
class LLMProvider(Protocol):
    """Provider-agnostic interface the loop consumes."""

    name: str  # "anthropic" | "deepseek" | ...

    def supports_model(self, model: str) -> bool: ...

    def format_system_blocks(self, blocks: list[str]) -> list[dict]:
        """Convert our internal system prompt blocks into the provider's
        wire shape. Anthropic adds cache_control:ephemeral; DeepSeek
        returns a single concatenated text block."""

    def format_tools(self, specs: list[ToolSpec]) -> list[dict]:
        """Convert tool specs into the provider's wire shape.
        Anthropic: {name, description, input_schema}.
        DeepSeek/OpenAI: {type:"function", function:{name, description, parameters}}."""

    async def stream(
        self,
        *,
        model: str,
        system_blocks: list[dict],
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
    ) -> AsyncIterator[LLMEvent]:
        """Open a streaming connection. Yields our internal events:
            TextDelta | ToolUseStart | ToolUseInputDelta | ToolUseEnd |
            ReasoningDelta | UsageUpdate | StreamEnd
        The loop consumes these without knowing the provider."""

    def estimate_cost(self, model: str, usage: dict) -> float:
        """USD cost for the reported usage."""
```

### 3.3 Internal events (lo que adapters yieldean)

```python
@dataclass
class TextDelta:        text: str
@dataclass
class ToolUseStart:     id: str; name: str
@dataclass
class ToolUseInputDelta: id: str; partial_json: str
@dataclass
class ToolUseEnd:       id: str; input_json: str
@dataclass
class ReasoningDelta:   text: str  # only DeepSeek-R1 emits this
@dataclass
class StreamEnd:        stop_reason: str; usage: dict
```

Nota: el loop ya tiene su propio `LoopEvent` (TextDelta + ToolUseStart + ToolUseResult + ProposalEvent + MessageEnd + ErrorEvent). Los `LLMEvent` son una capa más abajo — el loop consume LLMEvents, los traduce a LoopEvents.

### 3.4 Flujo end-to-end de un turn (multi-hop, con DeepSeek-R1)

```
Request hits /agent/conversations/{id}/turn
  → router validates + check_quota
  → get_provider_for_model("deepseek-reasoner") → DeepSeekAdapter
  → DeepSeekAdapter.format_system_blocks(blocks) → [{"text": "..."}]
  → DeepSeekAdapter.format_tools(specs) → [{type:"function", ...}]
  → loop opens DeepSeekAdapter.stream(...)
  → adapter calls deepseek API, translates events:
       "reasoning_content" delta → ReasoningDelta
       "content" delta → TextDelta
       tool_calls → ToolUseStart + InputDelta + End
       stop_reason="tool_calls" → StreamEnd(stop_reason="tool_use")
  → loop yields LoopEvents to streaming.py
  → streaming.py serializes to SSE frames (incl. new "reasoning_delta")
  → frontend renders text + chips + collapsible reasoning panel
```

---

## 4. Phases (deliberadamente pequeñas)

### Fase 1 — Provider interface + Anthropic adapter (1 día)

**Objetivo:** Mover el código existente a vivir detrás de la interfaz, sin agregar DeepSeek todavía. Behaviorally idempotent — todos los tests del epic #400 corren igual.

**Entregables:**
- `api/agent/providers/base.py` — `LLMProvider` protocol + event dataclasses.
- `api/agent/providers/anthropic_adapter.py` — wrap del SDK actual.
- `api/agent/providers/registry.py` — prefijo → adapter mapping.
- `loop.py` refactor — habla con `provider.stream(...)` en vez de `client.messages.stream(...)`.
- `clients.py` se convierte en thin shim que llama a `registry.get_provider_for_model(model)`.

**Tests:** Los 160 existentes corren sin tocar (el FakeAnthropicClient ya replica el wire shape; lo movemos para que implemente `LLMProvider` directamente — pierde el nombre "Fake**Anthropic**" y se vuelve `FakeAnthropicProvider`).

**Riesgo:** bajo. Mecánico. La hard part es no romper nada.

### Fase 2 — DeepSeek adapter (chat V3 only) (1 día)

**Objetivo:** Plug deepseek-chat. R1 se difiere a Fase 3.

**Entregables:**
- `api/agent/providers/deepseek_adapter.py` — implements LLMProvider con HTTP cliente (httpx) contra `api.deepseek.com/v1/chat/completions`. No usamos el SDK de DeepSeek (es OpenAI fork; httpx directo es más simple y deja la cost model bajo nuestro control total).
- `models.py` updated: `ALLOWED_MODELS` incluye `deepseek-chat`. `SURFACE_MODEL_DEFAULTS["dock"]` queda en `claude-sonnet-4-6` por default (no cambiamos defaults aún — primero hay que validar parity).
- New env var: `DEEPSEEK_API_KEY`. Status endpoint sigue chequeando `ANTHROPIC_API_KEY` Y/O `DEEPSEEK_API_KEY` (al menos uno presente para enabled=true).
- `FakeDeepSeekProvider` en `tests/_fakes.py` — replica el wire de DeepSeek chat (OpenAI-style delta chunks).

**Tests:**
- Parity test suite: cada test del epic #400 que era Anthropic-only ahora se parametriza con `[anthropic_fake, deepseek_fake]`. Todos pasan en ambos.
- 1 test E2E nuevo: tool_use turn con deepseek-chat completa correctamente (`tool_calls` en wire DS → `ToolUseStart` interno → `tool_use_result` block en messages).

**Riesgo:** medio. La traducción del tool wire format es donde anidan los bugs (DS emite `tool_calls[].function.arguments` como STRING JSON, hay que parsearlo).

### Fase 3 — DeepSeek-R1 + reasoning_delta SSE event (1 día)

**Objetivo:** Surface el razonamiento de R1 en la UI.

**Entregables:**
- `deepseek_adapter.py` reconoce `delta.reasoning_content` y emite `ReasoningDelta`.
- `streaming.py` agrega `reasoning_delta` al closed enum.
- `frontend/src/agent/types.ts` agrega `AgentReasoningDelta`.
- `frontend/src/agent/useAgentStream.ts` agrega `reasoning` state al `ChatMsg`, lo acumula como `tool_chips`.
- AgentDock + SymbolDetail renderizan `<details><summary>Razonamiento</summary>{texto}</details>` debajo del bubble cuando hay reasoning.
- `SURFACE_MODEL_DEFAULTS` ACTUALIZADO: defaults migran a DeepSeek (los 5 surfaces). Anthropic queda en `ALLOWED_MODELS` para override manual.

**Tests:**
- Stream parity: `FakeDeepSeekProvider.queue_reasoner_turn(...)` produce reasoning + content; loop emits ReasoningDelta + TextDelta correctamente.
- Vitest: reasoning event acumula en el ChatMsg sin contaminar el text principal.

**Riesgo:** medio. R1's reasoning a veces es múltiples kilobytes — necesita streaming chunking que ya tenemos pero hay que confirmar el rendering del frontend no bloquea con detalles cerrados.

### Fase 4 — Cost model migration + dashboard updates (0.5 días)

**Objetivo:** `cost_usd` en audit table refleja el provider correcto. `/agent/metrics` reporta breakdown.

**Entregables:**
- `provider.estimate_cost(model, usage)` reemplaza el `_MODEL_PRICING` global.
- `loop.py` calcula `total_cost_usd` usando el adapter activo (no la dict hardcoded).
- `agent_conversations` schema gana columna `provider TEXT` (idempotent ALTER TABLE).
- `/agent/metrics` agrega `today.by_provider: {anthropic: $..., deepseek: $...}`.

**Tests:** las assertions de cost en `test_agent_loop.py` se parametrizan con el provider del adapter — cada uno tiene su pricing table en su módulo.

**Riesgo:** bajo. Mecánico.

### Fase 5 — Rollout (Phase 6 of epic #400 retomada) (0.5 días + 48h bake)

**Objetivo:** flip a producción con DeepSeek como default.

**Entregables:**
- `docs/rollouts/2026-05-DD-multi-provider-flip.md` — runbook actualizado: setup de DEEPSEEK_API_KEY, smoke con el nuevo default, monitor 48h.
- `scripts/agent_health_check.py` lee `by_provider` breakdown.
- `config.defaults.json` sigue con `agent.enabled=false` (operator opta in via config.json).

**Decisión que NO se toma en este epic:** ¿desactivamos Anthropic completamente, o queda como override on-demand? Recomendación: dejarlo en `ALLOWED_MODELS`. Si DS bake no convence, flip vía override de `cfg.agent.surface_models` (campo nuevo opcional) sin code deploy.

---

## 5. Tests críticos

Heredados del epic #400 §11 — todos siguen aplicando, ahora parametrizados por provider donde tenga sentido:

- §11.1 Safety regression: corre 2 veces (anthropic_fake + deepseek_fake). Misma refusal text esperada.
- §11.2 Tool routing: provider-independent — los snapshot tests de `tools_for_surface()` no cambian.
- §11.3 Tenant isolation: provider-independent.
- §11.4 Hallucination guard: provider-independent.
- §11.5 Idempotency: provider-independent.
- §11.6 Cache verification: SPLIT — Anthropic verifica `cache_*_input_tokens`, DeepSeek verifica byte-stable prefix.
- §11.7 Status: provider-independent.
- §11.8 Graceful failures: corre 2 veces. DS 429 / 503 → mismo `upstream` reason.

Nuevos tests específicos del epic:
- `test_provider_registry`: `claude-sonnet-4-6` → AnthropicAdapter, `deepseek-chat` → DeepSeekAdapter, unknown prefix → ValueError.
- `test_tool_schema_conversion`: una `ToolSpec` se convierte a Anthropic shape y a DeepSeek shape — assert wire bytes exactos.
- `test_deepseek_reasoning_delta`: queued reasoner turn produce ReasoningDelta events que el loop traduce a `reasoning_delta` SSE frames.
- `test_cost_estimate_per_provider`: el mismo `usage` dict produce costos diferentes según el provider (claude vs deepseek).

---

## 6. Riesgos + mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| DeepSeek API rate-limits / 429 más agresivo que Anthropic | media | medio | Adapter retorna `upstream` event con friendly message — UX ya cubre el caso |
| Tool-calling de DS tiene bugs en JSON output (parámetros mal parseados) | media | alto | Validación Pydantic en el dispatch (ya existe) — cae a `is_error: invalid_input` |
| Reasoning content de R1 se vuelve enorme y bloquea el stream | baja | medio | Chunking ya manejado por el adapter; el `<details>` colapsado por default minimiza render cost |
| DeepSeek auto-cache no funciona en práctica (prefijo no es estable) | media | bajo | El test del prefix-stability lo cataloga; si no hay cache hit, el costo se mantiene plano (no hay explosión) |
| Operator activa DS sin tener DEEPSEEK_API_KEY en .env | baja | medio | Status endpoint chequea: si default model es DS y no hay key, retorna `agent_disabled` |
| El cost diferencial es menor a lo esperado por DS reasoning más caro de lo previsto | media | bajo | El bake 48h captura el real cost; flip de defaults es config-level (sin code deploy) |

---

## 7. Lo que NO está en este epic (out-of-scope)

- **OpenAI / Mistral / Gemini.** El epic deja la abstracción lista; agregarlos es Phase Q + 1 día por provider. No los implementamos ahora.
- **Per-tenant provider choice.** Todos los tenants usan los mismos `SURFACE_MODEL_DEFAULTS`. Operator override per-turn vía `model` field ya funciona desde epic #400.
- **Reasoning content como input de `assert_text_grounded`.** El guard sólo opera sobre `content` final. Si R1 razona sobre IDs ungrounded pero el content final está clean, no flageamos (intencional — el reasoning es chain-of-thought, no hablamos al usuario).
- **A/B de Anthropic vs DeepSeek con datos del bake.** Eso es decision-making post-bake; este epic solo provee la infraestructura.

---

## 8. Pickups acumulados que NO bloquean este epic

Del epic #400 review queue:

- Live-model safety regression test (`pytest -m live`) — pendiente desde Phase 5B.
- Wire `assert_text_grounded` como production postcheck — pendiente desde Phase 5B.
- Frontend mounts para KillSwitch / AutoTune / Historial — pendiente desde Phase 4.
- `<ProposalConfirm/>` shared component extraction — pendiente desde Phase 4.

Ninguno bloquea este epic. Pueden tomarse como Phase 6 después del flip multi-provider.

---

## 9. Estimación total

| Fase | Días dev | Riesgo |
|---|---|---|
| 1 — Provider interface + Anthropic adapter | 1.0 | bajo |
| 2 — DeepSeek adapter (chat V3) | 1.0 | medio |
| 3 — DeepSeek R1 + reasoning UX | 1.0 | medio |
| 4 — Cost model migration | 0.5 | bajo |
| 5 — Rollout + 48h bake | 0.5 + 48h | bajo |
| **Total** | **4.0 + bake** | |

---

## 10. Decisión pendiente del operator

Antes de empezar Fase 1 necesito confirmación explícita en estos puntos:

1. ¿Approve la arquitectura adapter-por-provider (sección 2.1)? ¿O preferís evaluar la opción B (OpenAI-compat passthrough)?
2. ¿Approve la decisión de surface reasoning como SSE event aparte (sección 2.3)?
3. ¿Approve el plan de migrar defaults a DS en Fase 3, NO en Fase 2 (validamos parity con Anthropic primero)?
4. ¿Hay otros providers que querés que el spec contemple desde día 1 aunque no se implementen (Gemini? Mistral? Open-router?), o el shape "interfaz + 2 adapters" es suficiente?

Una vez confirmado, abro la rama, escribo Fase 1, y vamos PR-por-PR como con epic #400.
