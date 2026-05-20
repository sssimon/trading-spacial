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

**Convention formalizada (PR #411 review pickup 4):**

Para agregar un nuevo provider sin cambios estructurales:

1. Sus model IDs **DEBEN** comenzar con el prefijo canónico del vendor:
   - `gpt-` para OpenAI
   - `gemini-` para Google
   - `mistral-` para Mistral
   - `command-` para Cohere
   - `grok-` para xAI
2. Agregar `api/agent/providers/{vendor}_adapter.py` que implementa `LLMProvider`.
3. Update `PROVIDER_BY_PREFIX` registry con la entrada `{vendor_prefix: vendor_name}`.
4. Update `ALLOWED_MODELS` para incluir los model IDs del vendor.
5. Parametrizar los tests §11 con el nuevo `Fake{Vendor}Provider`.

**Estimación por provider adicional:** ~1 día.

**Invariant locked en CI** (test nuevo en `tests/test_provider_registry.py`):
- Cada model id en `ALLOWED_MODELS` matchea exactamente UN prefijo de `PROVIDER_BY_PREFIX`.
- Cada prefijo de `PROVIDER_BY_PREFIX` tiene al menos un model id en `ALLOWED_MODELS` (no prefixes huérfanos).

Si esos invariantes se rompen al agregar provider nuevo, el test catchea antes del deploy.

### 2.7 `/agent/status` con dual-key

Edge case: si default model es `deepseek-chat` pero solo `ANTHROPIC_API_KEY` está set, ¿status reporta enabled=true (porque hay UNA key) o enabled=false (porque la del default no está)?

**Decisión (PR #411 review pickup 3):** status chequea **la key del provider del default model**.

```python
def get_agent_status(cfg=None):
    cfg = cfg or load_config()
    if not cfg.get("agent", {}).get("enabled", False):
        return AgentStatus(enabled=False, reason="agent_disabled")
    # Resolve the default surface's model → its provider → that provider's key.
    default_model = default_model_for_surface("dock")  # any surface works
    provider = get_provider_for_model(default_model)
    if not provider.has_api_key():
        return AgentStatus(enabled=False, reason="agent_disabled")
    # ... breaker checks unchanged ...
```

Razones:
- **Simple:** una sola key check, sin lógica de fallback.
- **Honest:** si el operator dejó solo Anthropic configurado pero el default es DS, mejor reportar disabled ahora que tirar 503 en el primer turn.
- **No fallback silencioso:** un fallback "si el provider del default no tiene key, probá el otro" introduce magia que el operator no esperaba. Si quiere fallback, lo articula explícitamente cambiando defaults en config.json (sin code deploy).

**Per-turn override:** un usuario que pide `model: "claude-opus-4-7"` cuando solo DS está configurado, su request falla con 503 en el moment del `provider.stream(...)` (no en pre-flight). Mensaje friendly `"upstream"` igual. Aceptable.

### 2.8 Circuit breaker — cap global vs per-provider

**Hoy:** `cfg.agent.global_daily_usd_cap = 5.0` es un solo número que aplica al total. Phase 5A.

**Decisión para este epic (PR #411 review pickup 1):** se mantiene **global cap único**.

Razones:
- Visibilidad ya existe — `/agent/metrics.today.by_provider` reportará el breakdown a partir de Fase 4.
- Per-provider caps duplican surface area del config sin ganancia operativa inmediata: si DS spend explota, el global cap trip → operator investiga → quita DS via config flip. Mismo resultado, menos config drift.
- Cuando aparezca demanda real (operator quiere "max $0.50/día en Opus para experimentos pero $5/día total"), eso es **Phase Q+1**: extender el config a `per_provider_daily_caps` + extender `circuit_breaker.is_breaker_tripped` con un loop sobre providers. Estimación: 0.5 días + tests.

Notado como pickup futuro en sección 8.

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
- New env var: `DEEPSEEK_API_KEY`. Status endpoint chequea la key del **provider del default model** (§2.7 decision) — no "ANY key set".
- `FakeDeepSeekProvider` en `tests/_fakes.py` — replica el wire de DeepSeek chat (OpenAI-style delta chunks).

**Tests:**
- Parity test suite: cada test del epic #400 que era Anthropic-only ahora se parametriza con `[anthropic_fake, deepseek_fake]`. Todos pasan en ambos.
- 1 test E2E nuevo: tool_use turn con deepseek-chat completa correctamente (`tool_calls` en wire DS → `ToolUseStart` interno → `tool_use_result` block en messages).

**Riesgo:** medio. La traducción del tool wire format es donde anidan los bugs (DS emite `tool_calls[].function.arguments` como STRING JSON, hay que parsearlo).

### Fase 3 — DeepSeek-R1 + reasoning_delta SSE event (1 día)

**Objetivo:** Surface el razonamiento de R1 en la UI.

**Sub-secuenciamiento (PR #411 review pickup):** Fase 3 ata 2 cambios independientes (UX nueva del reasoning + migración de defaults). Si la UX rompe post-deploy, queda ambiguo si fue UI o routing. La fase se split en **2 PRs separados**:

**PR 3a — Reasoning UX wired** (sin tocar defaults):
- `deepseek_adapter.py` reconoce `delta.reasoning_content` y emite `ReasoningDelta`.
- `streaming.py` agrega `reasoning_delta` al closed enum.
- `frontend/src/agent/types.ts` agrega `AgentReasoningDelta`.
- `frontend/src/agent/useAgentStream.ts` agrega `reasoning` state al `ChatMsg`, lo acumula separado de `text`.
- AgentDock + SymbolDetail renderizan `<details><summary>Razonamiento</summary>{texto}</details>` debajo del bubble cuando hay reasoning.
- Tests: stream parity (`FakeDeepSeekProvider.queue_reasoner_turn(...)`), vitest no-contamination del bubble principal.
- Verificación manual con `model: "deepseek-reasoner"` override por-turn antes del merge.

**PR 3b — Migrate defaults a DS** (separado de UX):
- `SURFACE_MODEL_DEFAULTS` actualizado: defaults migran a DeepSeek (los 5 surfaces). Anthropic queda en `ALLOWED_MODELS` para override manual.
- Snapshot tests de `test_agent_models.py` se actualizan (los frozensets EXPECTED).
- Status endpoint requiere `DEEPSEEK_API_KEY` ahora (vía §2.7 logic, no por cambio del status code).

Si PR 3a tiene bug post-merge, se revierte sin perder lo necesario para que PR 3b ship. Si PR 3b tiene bug post-merge, se revierte sin afectar la UX nueva.

**Riesgo:** medio. R1's reasoning a veces es múltiples kilobytes — necesita streaming chunking que ya tenemos pero hay que confirmar el rendering del frontend no bloquea con detalles cerrados.

### Fase 4 — Cost model migration + dashboard updates (0.5 días)

**Objetivo:** `cost_usd` en audit table refleja el provider correcto. `/agent/metrics` reporta breakdown.

**Entregables:**
- `provider.estimate_cost(model, usage)` reemplaza el `_MODEL_PRICING` global.
- `loop.py` calcula `total_cost_usd` usando el adapter activo (no la dict hardcoded).
- `agent_conversations` schema gana columna `provider TEXT` (idempotent ALTER TABLE).
- `/agent/metrics` agrega `today.by_provider: {anthropic: $..., deepseek: $...}`.
- **(PR #411 review pickup — diferido a post-Fase-3)**: nueva columna `reasoning_tokens INTEGER` en `agent_conversations`, opcional, llenada solo por adapter de DS-reasoner. Permite telemetry "qué porcentaje del cost de un turn analytical fue reasoning vs final content" sin alterar el cost calculation.

**Tests:** las assertions de cost en `test_agent_loop.py` se parametrizan con el provider del adapter — cada uno tiene su pricing table en su módulo.

**Riesgo:** bajo. Mecánico.

### Fase 5 — Rollout (Phase 6 of epic #400 retomada) (0.5 días + 48h bake)

**Objetivo:** flip a producción con DeepSeek como default.

**Entregables:**
- `docs/rollouts/2026-05-DD-multi-provider-flip.md` — runbook NUEVO (no edit del original; el de Phase 6 del epic #400 queda como artefacto histórico del plan inicial). Incluye:
  - Setup de `DEEPSEEK_API_KEY` además de `ANTHROPIC_API_KEY` (la última queda opcional pero útil para fallback manual).
  - Smoke con el nuevo default DS.
  - Reasoning UX verification: usuario abre AutoTune, expande el `<details>`, verifica que el panel muestra texto coherente.
  - Monitor 48h con el script actualizado.
  - **Nota explícita** sobre el cost over-estimate (§6 risks): comparar `today.total_usd` en `/agent/metrics` contra DeepSeek Console al final del bake; diferencia esperada (we don't model DS auto-cache discount).
- `scripts/agent_health_check.py` lee `by_provider` breakdown — separa Anthropic spend de DS spend en el output.
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
| Operator activa DS sin tener DEEPSEEK_API_KEY en .env | baja | medio | Status endpoint chequea **la key del provider del default model** (§2.7) — retorna `agent_disabled` antes de tirar 503 en el primer turn |
| El cost diferencial es menor a lo esperado por DS reasoning más caro de lo previsto | media | bajo | El bake 48h captura el real cost; flip de defaults es config-level (sin code deploy) |
| **Cost `cost_usd` over-estimated vs DeepSeek billing real** (PR #411 review pickup 2) | alta | bajo | DeepSeek auto-cachea el prefijo pero NO reporta cache stats en `usage` — nuestro `estimate_cost()` factura como si todo el input fuera fresh. Adicionalmente, DS aplica off-peak discount ~50% en 3h UTC que nuestro estimate no modela. Net effect: audit + circuit breaker ven **más** spend del que DS factura en realidad. Comportamiento conservador (safe — breaker trippa antes del límite real). Mitigación: runbook de Fase 5 documenta esto explícitamente; operator compara `today.total_usd` vs DeepSeek Console al cierre del bake para calibrar. Si la diferencia es grande, futura iteración modela el discount. |

---

## 7. Lo que NO está en este epic (out-of-scope)

- **OpenAI / Mistral / Gemini.** El epic deja la abstracción lista; agregarlos es Phase Q + 1 día por provider. No los implementamos ahora.
- **Per-tenant provider choice.** Todos los tenants usan los mismos `SURFACE_MODEL_DEFAULTS`. Operator override per-turn vía `model` field ya funciona desde epic #400.
- **Reasoning content como input de `assert_text_grounded`.** El guard sólo opera sobre `content` final. Si R1 razona sobre IDs ungrounded pero el content final está clean, no flageamos (intencional — el reasoning es chain-of-thought, no hablamos al usuario).
- **A/B de Anthropic vs DeepSeek con datos del bake.** Eso es decision-making post-bake; este epic solo provee la infraestructura.

---

## 8. Pickups acumulados que NO bloquean este epic

**Del epic #400 review queue:**

- Live-model safety regression test (`pytest -m live`) — pendiente desde Phase 5B.
- Wire `assert_text_grounded` como production postcheck — pendiente desde Phase 5B.
- Frontend mounts para KillSwitch / AutoTune / Historial — pendiente desde Phase 4.
- `<ProposalConfirm/>` shared component extraction — pendiente desde Phase 4.

**De PR #411 review (futuros, post-flip de este epic):**

- **Per-provider daily caps en el circuit breaker** (§2.8). Hoy el cap es global. Cuando aparezca demanda real ("max $0.50/día en Opus pero $5/día total"), extender `cfg.agent.per_provider_daily_caps: dict[str, float]` + loop en `is_breaker_tripped()`. Estimación: 0.5 días + tests.
- **DeepSeek auto-cache discount + off-peak modeling** en `provider.estimate_cost()` (§6 risk). Requiere observación empírica del ratio real (`/agent/metrics.today.total_usd` vs DeepSeek Console al cierre de bakes) para calibrar el discount factor. Estimación: 1 día (mecánico una vez se tiene el ratio).
- **Telemetry de reasoning tokens** — columna `reasoning_tokens INTEGER` en `agent_conversations` + breakdown `today.reasoning_pct` en `/agent/metrics`. Útil para responder "qué % del cost de un turn analytical es razonamiento" sin tener que revisar logs. Estimación: 0.5 días.

Ninguno bloquea el flip de este epic. Pueden tomarse después del bake.

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

## 10. Decisiones tomadas (post-review de PR #411)

Las 4 decisiones del spec original quedaron resueltas en el review de PR #411:

| # | Decisión | Voto | Razón sumarizada |
|---|---|---|---|
| 1 | Adapter genuino vs OpenAI-compat passthrough | **A (adapter genuino)** | R1 reasoning_content no está en OpenAI spec; cost model per-provider awkward bajo passthrough; ~150 líneas por adapter es manejable |
| 2 | Reasoning como SSE event aparte vs discard | **SSE event aparte** | En surfaces analíticas la transparencia del razonamiento ES valor; discard waste tokens facturados |
| 3 | Migrar defaults a DS en Fase 3 (no Fase 2) | **Fase 3, además split en PR 3a + PR 3b** | Disciplina: opt-in primero con model override, después switch defaults; sub-split de Fase 3 reduce blast radius si la UX del reasoning rompe |
| 4 | Otros providers desde día 1 | **NO** | El abstraction se prueba suficientemente con 2 implementaciones; YAGNI; cuando aparezca demand real, ~1 día por provider siguiendo la convention de §2.6 |

Refinamientos adicionales aplicados:
- §2.6: convention formalizada (prefix matching + CI test que catchea orphans)
- §2.7: nueva sub-sección sobre el `/agent/status` dual-key edge case (check del default's provider key, no fallback mágico)
- §2.8: nueva sub-sección sobre circuit breaker (cap global se mantiene; per-provider caps son pickup futuro)
- §6: nueva fila de riesgo (cost over-estimate por auto-cache + off-peak no modelado en DS)
- Fase 3: split en 2 PRs (3a UX, 3b defaults migration)
- Fase 4: pickup futuro `reasoning_tokens` column anotado
- Fase 5: runbook NUEVO (no edit del de epic #400); cost-vs-billing comparison documentada

## 11. POC pre-Fase-1 — innecesario

El reviewer ofreció armar un POC del `LLMProvider` protocol en un branch separado antes de comprometerse a Fase 1. **Decisión: skip el POC.** Razones:

- La interfaz está bien especificada (§3.2 + §3.3). Cualquier ambigüedad surge en la implementación, no en el design.
- Fase 1 es deliberately mecánica (mover código existente a vivir detrás de la interfaz, sin agregar providers nuevos). Si Fase 1 revela problemas con la interfaz, el costo de iterar es bajo — la rama no se mergea hasta que la interfaz pase los 160 tests existentes.
- POC + Fase 1 son ~50% del mismo trabajo. Saltar al final.
- La puerta queda abierta: si arrancando Fase 1 surge un issue estructural con la interfaz, paramos, ajustamos el spec, y volvemos. No commitment irrevocable.

## 12. Estado actual

**Spec:** aprobado por el operator vía review de PR #411 (PR queda en merge pending — actúa como changelog del epic una vez aprobado).

**Próximo paso:** mergear este PR. Después se abre `feat/copilot-multi-provider-phase-1` y se inicia Fase 1 (provider interface + Anthropic adapter refactor, sin agregar DeepSeek aún).
