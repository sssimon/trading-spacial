# Copilot multi-provider rollout — flip a producción (Fase 5 del epic post-#400)

**Fecha planeada:** martes 2026-05-20.
**Operator:** Simon.
**Ventana de bake:** 48h.

Este runbook **reemplaza** `docs/rollouts/2026-05-20-copilot-flip.md` (Phase 6 del epic #400) que quedó suspendido cuando el operator pidió la abstracción multi-provider. El runbook anterior es válido como artefacto histórico del plan original; este es el que corre.

**Diferencias vs el runbook anterior:**
- Provider default ahora es **DeepSeek**, no Anthropic.
- Necesitas **dos** API keys: `DEEPSEEK_API_KEY` (para el default) y opcionalmente `ANTHROPIC_API_KEY` (para overrides per-turn a claude-*).
- El smoke incluye verificación del panel de razonamiento R1 (Fase 3a feature).
- El monitor 48h usa `today.by_provider` para distinguir DS vs Anthropic spend.

---

## 0. Pre-flip (~30 min)

### 0.1 Verificá que estás en `main` post-Fase-4

```
git fetch origin main
git log --oneline -3
```

Esperado: el tip es el merge de PR #416 (Fase 4) o más reciente. El epic multi-provider está completo en main.

### 0.2 Secrets en el `.env` de prod

Necesitás los siguientes valores configurados:

```powershell
# AGENT_PROPOSAL_SECRET: 32+ bytes random, separado de JWT_SECRET.
# Si ya lo tenés del rollout anterior, no lo cambies — sigue válido.
python -c "import secrets; print(secrets.token_urlsafe(32))"
#   AGENT_PROPOSAL_SECRET=<token>

# DEEPSEEK_API_KEY: el de tu cuenta en https://platform.deepseek.com/.
#   DEEPSEEK_API_KEY=sk-...
#
# Este es el PROVIDER PRINCIPAL post-Fase-3b. Si no está configurado,
# el copiloto reporta agent_disabled aunque ANTHROPIC_API_KEY esté
# set — el §2.7 status check chequea la key del provider del default
# surface (dock → deepseek-chat).

# ANTHROPIC_API_KEY: opcional, sólo para overrides per-turn a claude-*.
# Si no querés permitir overrides a Anthropic, no la pongas — los
# requests con body.model=claude-* fallarán con 400 model_not_allowed.
#   ANTHROPIC_API_KEY=sk-ant-...
```

Verificá que están cargadas:

```powershell
python -c "import os; print('PROP:', bool(os.environ.get('AGENT_PROPOSAL_SECRET'))); print('DS:', bool(os.environ.get('DEEPSEEK_API_KEY'))); print('ANTH:', bool(os.environ.get('ANTHROPIC_API_KEY')))"
```

Esperado: `PROP: True`, `DS: True`. `ANTH` puede ser True o False según tu decisión sobre overrides.

### 0.3 Defaults siguen OFF + bloque agent presente

```powershell
python -c "import json; print(json.load(open('config.defaults.json'))['agent'])"
```

Esperado: `{'enabled': False, 'global_daily_usd_cap': 5.0, 'breaker_open': False}`. **No edites `config.defaults.json`** — el override del flip va en `config.json` local (gitignored, per-deploy).

### 0.4 Restart `btc_api.py` para que corra el migration de Fase 4

```powershell
# Para el proceso actual de btc_api.py, después arrancalo de nuevo
# normalmente (INICIAR_API.bat o el watchdog).
```

El restart corre `init_db()`, que aplica:
- Columns `provider` + `reasoning_tokens` en `agent_conversations` (idempotent ALTER TABLE)
- Backfill de `provider` desde el `model` prefix para rows pre-Fase-4 (idempotent UPDATE WHERE provider IS NULL)

Verificá:

```powershell
python -c "import sqlite3; con=sqlite3.connect('signals.db'); cols=[r[1] for r in con.execute(\"PRAGMA table_info(agent_conversations)\")]; print([c for c in cols if c in ('provider','reasoning_tokens')])"
```

Esperado: `['provider', 'reasoning_tokens']` (orden puede variar).

Si hay rows pre-Fase-4 (típicamente none — el copilot nunca corrió en prod), verificá que el backfill las tocó:

```powershell
python -c "import sqlite3; con=sqlite3.connect('signals.db'); con.row_factory=sqlite3.Row; n_null=con.execute('SELECT COUNT(*) AS n FROM agent_conversations WHERE provider IS NULL').fetchone(); print('NULL provider rows:', dict(n_null)['n'])"
```

Esperado: `0` (todas backfilled) o el número de rows con `model` que no matchea `claude-%`/`deepseek-%` (typos, dev tests, etc).

### 0.5 Smoke-test con copilot OFF

```powershell
curl http://localhost:8000/agent/status
```

Esperado: `{"enabled": false, "reason": "agent_disabled"}`.

```powershell
curl -X POST http://localhost:8000/agent/conversations/test1/turn `
  -H "Content-Type: application/json" `
  --data "{\"surface\":\"dock\",\"messages\":[{\"role\":\"user\",\"content\":\"hola\"}]}"
```

Esperado: HTTP 503, body `{"detail": "agent_disabled"}`.

Frontend: el Dock no aparece en el dashboard.

### 0.6 Health check pre-flip

```powershell
python scripts/agent_health_check.py --window 24h
```

Esperado: todas las métricas en `0.0000` con `[OK]`. La sección "Spend breakdown by provider" puede estar vacía (sin spend todavía).

**Nota:** `[OK]` pre-flip significa "sin datos suficientes para alarmar", NO "el agente está funcionando". Lo que estás validando acá es que el script corre sin error contra una DB vacía + las queries no rompen. La validación funcional real del agente arranca en §2 post-flip.

---

## 1. El flip (~5 min)

### 1.1 Editá tu `config.json` (gitignored, per-deploy)

Si `config.json` no existe, lo creás:

```json
{
  "agent": {
    "enabled": true
  }
}
```

Si ya existe, agregá el bloque `agent` o flippá `enabled` a `true`. **No toques `global_daily_usd_cap`** — heredás el `5.0` del default; es el cap conservador del bake.

### 1.2 NO necesitás restart de `btc_api.py`

Verificado en código (PR #410 review): `api/agent/config.py` llama `load_config()` dentro de `get_agent_status()`, que se ejecuta en cada request. El cambio en `config.json` toma efecto en el próximo request, no necesita restart.

### 1.3 Verificá el flip

```powershell
curl http://localhost:8000/agent/status
```

Esperado: `{"enabled": true, "reason": "ok"}`.

---

## 2. Post-flip smoke (~10 min)

### 2.0 Obtener cookie JWT (para los curls)

Varios pasos abajo usan `curl` contra endpoints autenticados (`/agent/conversations/{id}/turn`, `/agent/metrics`). Esos requieren la cookie de sesión que el dashboard ya tiene.

```powershell
# 1. Abrí el dashboard normalmente en el browser, logueate.
# 2. Abrí DevTools (F12) → tab Application (Chrome) o Storage (Firefox).
# 3. Cookies → http://localhost:8000 → copiá el value de `access_token`
#    (o como se llame la cookie JWT — chequeá tu setup de auth).
# 4. Guardalo en una variable de PowerShell para reusar:

$AUTH = "access_token=<copy-paste-aquí>"

# Verificá que funciona:
curl http://localhost:8000/auth/me -H "Cookie: $AUTH"
# Esperado: 200 con tu user info. 401 → cookie expirada, refrescala.
```

Si tu setup usa otra cookie name (e.g. `agent_token`, `jwt`), ajustá `$AUTH` correspondientemente. Los curls siguientes referencian `$AUTH`.

### 2.1 Primer turn por el Dock (default: deepseek-chat)

Abrí el dashboard, abrí el dock con el botón ◈, mandá:

> hola

Esperado:
- Streaming text aparece carácter por carácter.
- No hay panel "Razonamiento" (deepseek-chat no genera reasoning).
- No errores en consola del browser ni en el log de btc_api.py.

### 2.2 Verificá el provider en la audit row

`ORDER BY id DESC LIMIT 1` puede picar la row equivocada si tu dashboard polled `/agent/status` u otra cosa generó un row mientras tanto. Si querés precisión exacta, agarrá el `conversation_id` del turn que acabás de hacer (visible en DevTools: Network → `/agent/conversations/<id>/turn`):

```powershell
python -c "
import sqlite3
con = sqlite3.connect('signals.db')
con.row_factory = sqlite3.Row
# Últimas 5 rows — alguna debería ser tu smoke turn.
for r in con.execute(
    'SELECT id, conversation_id, model, provider, reasoning_tokens, cost_usd '
    'FROM agent_conversations ORDER BY id DESC LIMIT 5'
):
    print(dict(r))
"
```

Esperado: al menos una row con shape:
```
{'model': 'deepseek-chat', 'provider': 'deepseek', 'reasoning_tokens': 0, 'cost_usd': 0.0001}
```

`provider='deepseek'` confirma que el migration de Fase 3b + la audit wiring de Fase 4 funcionaron end-to-end. `reasoning_tokens=0` esperado (chat-V3 reporta 0).

### 2.3 Verificá R1 reasoning end-to-end (override directo)

**Importante:** el Dock por default usa `deepseek-chat` (no R1), así que un turn casual por el Dock NO ejercita el path de reasoning. Los surfaces que defaultean a R1 (`kill_switch`, `autotune`) no tienen frontend mount todavía (deferred al post-rollout — ver §4). Para validar que la cadena reasoning_delta → SSE frame → audit.reasoning_tokens funciona, hay que forzar R1 con un override per-turn.

```powershell
# Si querés correr el curl en PowerShell, primero obtené tu cookie JWT
# desde DevTools (ver §2.4 abajo).

curl -X POST http://localhost:8000/agent/conversations/r1-smoke/turn `
  -H "Content-Type: application/json" `
  -H "Cookie: $AUTH" `
  --data "{\"surface\":\"dock\",\"model\":\"deepseek-reasoner\",\"messages\":[{\"role\":\"user\",\"content\":\"razona brevemente sobre si BTC esta caro o barato\"}]}"
```

Esperado en el SSE response (parsealo con `--no-buffer` o miralo en el log):
- Frames `{"type": "reasoning_delta", "text": "..."}` — chain-of-thought del modelo.
- Frames `{"type": "text_delta", "text": "..."}` — respuesta final.
- Frame `{"type": "message_end", "usage": {...}, "cost_usd": ...}` para cerrar.

Verificá la audit row:

```powershell
python -c "
import sqlite3
con = sqlite3.connect('signals.db')
con.row_factory = sqlite3.Row
r = con.execute(
    'SELECT model, provider, output_tokens, reasoning_tokens, cost_usd '
    'FROM agent_conversations WHERE conversation_id = ?',
    ('r1-smoke',),
).fetchone()
print(dict(r))
"
```

Esperado: algo como
```
{'model': 'deepseek-reasoner', 'provider': 'deepseek', 'output_tokens': 400, 'reasoning_tokens': 320, 'cost_usd': 0.0009}
```

`reasoning_tokens > 0` confirma que el adapter parseó `completion_tokens_details.reasoning_tokens`. `output_tokens >= reasoning_tokens` confirma que `completion_tokens` cuenta el total (reasoning + content) per DS contract.

**Verificación del panel UI (deferred parcialmente):** los surfaces que defaultean a R1 (`kill_switch`, `autotune`) no tienen frontend mount todavía — el `<details>Razonamiento</details>` colapsable se renderiza correctamente en `AgentDock` y `SymbolDetail` (probado por vitest), pero verificación visual end-to-end del panel queda hasta que esos mounts existan (futuro epic). Hasta entonces, el operator puede invocar R1 vía override desde la consola pero el render del panel se confirma viendo el `reasoning` field en `result.current.msgs[1]` en DevTools de React, no en un panel visible en el Dock.

### 2.4 Override path (claude-* per-turn)

**Solo si tenés ANTHROPIC_API_KEY configurada.** Sino, saltá esto.

```powershell
curl -X POST http://localhost:8000/agent/conversations/override-test/turn `
  -H "Content-Type: application/json" `
  -H "Cookie: $AUTH" `
  --data "{\"surface\":\"dock\",\"model\":\"claude-sonnet-4-6\",\"messages\":[{\"role\":\"user\",\"content\":\"hola desde claude\"}]}"
```

Esperado: streaming responde normalmente. Luego:

```powershell
python -c "import sqlite3; con=sqlite3.connect('signals.db'); con.row_factory=sqlite3.Row; r=con.execute('SELECT model, provider FROM agent_conversations WHERE conversation_id=\"override-test\" ORDER BY id DESC LIMIT 1').fetchone(); print(dict(r))"
```

Esperado: `{'model': 'claude-sonnet-4-6', 'provider': 'anthropic'}`. **Esto confirma que el fix del per-request provider resolution (PR #415 review) funciona:** el override rutea correctamente a Anthropic, no a DS.

### 2.5 Confirmá `/agent/metrics`

```powershell
# /agent/metrics requiere cookie admin (require_role("admin") gate).
curl http://localhost:8000/agent/metrics -H "Cookie: $AUTH"
```

Esperado en el JSON:
- `breaker.tripped: false`, `breaker.reason: "ok"`
- `today.turn_count >= 2` (o 3 si hiciste el override test)
- `today.by_provider` con buckets `{"deepseek": {...}, "anthropic": {...}}` si hiciste override; solo `deepseek` si no.
- `today.reasoning_tokens > 0` si hiciste el turn R1 de §2.3.

### 2.6 Health check 1h post-flip

```powershell
python scripts/agent_health_check.py --window 1h
```

Esperado: todas las métricas en `[OK]`. La sección "Spend breakdown by provider" muestra cuánto pagaste por provider en la última hora.

**Atención a ventanas distintas:** `/agent/metrics today.by_provider` usa midnight UTC como cutoff; el health script usa rolling N hours. Si el flip lo hiciste cerca de medianoche UTC, las cifras pueden divergir ligeramente — esperado, no es bug.

---

## 3. Monitor 48h

Corré el health check cada ~6h:

```powershell
python scripts/agent_health_check.py --window 6h
python scripts/agent_health_check.py --window 24h
python scripts/agent_health_check.py --window 24h --json
```

### Umbrales de abort (sostenido > 2 horas)

| Métrica | Threshold | Acción si breach |
|---|---|---|
| `cache_hit_rate` | < 0.50 post-warmup | Investigar: ¿el prefijo del system prompt está estable? DS auto-cache funciona si el prefijo no cambia turn-a-turn. |
| `error_rate` | > 0.05 | Mirar `error_breakdown_24h` en `/agent/metrics`. Si es `upstream` (saturated), esperar. Si es nuevo, abort. |
| `p95_latency_ms` | > 4000 | Verificar nginx + backend logs. Para R1 con razonamiento largo puede crecer; si > 6000 sostenido, abort. |
| `daily_spend_usd` | cerca de $5 | Breaker auto-trips. Decidí: subir cap, o investigar el spike. |

### Notas de comparación pre/post migration

**Rolling-cost discontinuity:** durante las primeras 24h post-flip, las queries `SUM(cost_usd)` rolling 24h mezclan rows con Anthropic pricing (pre-flip) + DS pricing (post-flip). Para comparación pre/post limpia:

- **Filtrar por provider** en queries ad-hoc:
  ```sql
  SELECT SUM(cost_usd) FROM agent_conversations
  WHERE ts >= datetime('now', '-24 hours') AND provider = 'deepseek';
  ```
- **O esperar 24+ horas** hasta que el rolling window quede 100% post-flip.

El script `agent_health_check.py` ya separa el breakdown en la sección "Spend breakdown by provider" del output — usá eso para distinguir.

**Cost over-estimate vs DeepSeek Console** (documented en spec §6): nuestro `estimate_cost` para DS trata todo el input como fresh (no modelamos el auto-cache discount ni el off-peak pricing). El audit `today.total_usd` será **mayor** que la cifra real que DS te factura en el dashboard. Conservative — el breaker trippea antes del límite real. Al cierre del bake, comparalas:

- `/agent/metrics today.total_usd` ÷ `DeepSeek Console daily usage` = ratio. Si > 2x, considerar futuro epic para modelar el discount (PR #411 spec §8 pickup).

### Investigar el `'unknown'` bucket

Si `/agent/metrics today.by_provider` muestra una key `"unknown"`:

1. **No es bug funcional** — son rows con `provider IS NULL`. Pueden ser:
   - Pre-Fase-4 rows que el backfill no tocó (model con prefix no-mapeado).
   - Dev tests con model id custom.
   - Typos en `body.model` durante exploración.

2. **Investigación:**
   ```powershell
   python -c "import sqlite3; con=sqlite3.connect('signals.db'); con.row_factory=sqlite3.Row; rows=con.execute(\"SELECT DISTINCT model FROM agent_conversations WHERE provider IS NULL\").fetchall(); [print(dict(r)) for r in rows]"
   ```

3. **Si encontrás un model que debería tener provider asignado** (e.g. nuevo model de Anthropic / DS que no respeta el prefix convention), actualizá la lógica del backfill en `db/schema.py` Y en `api/agent/audit.py:_provider_for_model` Y en `api/agent/providers/registry.py:PROVIDER_NAME_BY_PREFIX` (los 3 sitios — el test `test_provider_mapping_consistent_across_registry_and_audit` te lo recuerda si te olvidás de 1 o 2).

4. **Re-run init_db** para aplicar el backfill actualizado:
   ```powershell
   python -c "import btc_api; btc_api.init_db()"
   ```

### También dispara abort

- Cualquier incident de **leak de tenant** (audit row con `tenant_id` distinto al del JWT del request).
  - **Single-user note:** para este bake (papá solo), el invariant es trivial — todas las rows tienen `tenant_id=1`. La query de detección queda preparada para multi-user futuro: `SELECT tenant_id, COUNT(*) FROM agent_conversations WHERE ts >= datetime('now', '-1 hour') GROUP BY tenant_id;` — en single-user esperás exactamente una fila con `tenant_id=1`; cualquier otra fila es leak.
- Cualquier **hallucination detectada** en sample (corré `assert_text_grounded` sobre 10-20 turns al azar).
- **Reasoning content leak**: el panel `<details>` no debería contener info de OTROS tenants. Si lo hace, abort + investigar.

### Abort: cómo flippar (parcial vs total)

Dos paths según la severidad del breach. **NO hay un path "mid-bake provider swap"** — mezclar "respuesta al incident" con "decisión post-mortem sobre DS-vs-Anthropic" sólo confunde la observación. Si decidís volver a Anthropic después del incident, eso es un epic separado post-mortem que actualiza `SURFACE_MODEL_DEFAULTS` en código y vuelve a flippar limpio.

**Parcial — pausa todo, mantiene config.** Útil cuando el problema es transitorio (DS API saturada, spike de spend) y querés ver si se resuelve solo.

```json
{
  "agent": {
    "enabled":      true,
    "breaker_open": true
  }
}
```

Esperado: `GET /agent/status` → `{"enabled": false, "reason": "breaker_open"}`. El Dock se oculta, todos los `/turn` returns 503. El bake puede reanudar flippando `breaker_open: false`. Audit + quotas state se preservan.

**Total — rollback al estado pre-Fase-5.** Útil cuando el problema es estructural (hallucination grave, tenant leak, comportamiento que no calibra con un breaker).

```json
{
  "agent": {
    "enabled": false
  }
}
```

Esperado: `GET /agent/status` → `{"enabled": false, "reason": "agent_disabled"}`. El Dock se oculta. Audit table se preserva (sirve para forensics del incident).

Después del rollback total, la decisión "volver a DS post-fix vs migrar back a Anthropic" se toma en post-mortem, no in-situ. Eso es un PR aparte que actualiza `SURFACE_MODEL_DEFAULTS` (back a Anthropic) o que ajusta lo que rompió en DS.

`load_config` re-lee en cada request → cualquiera de los 2 cambios toma efecto inmediatamente sin restart de `btc_api.py`.

---

## 4. Success criteria (epic cierra)

Después de 48h sin breach:

- `cache_hit_rate` no aplica de la misma forma (DS no reporta cache stats). Lo que validás es que el system prompt prefix es byte-idéntico turn-a-turn (única palanca para activar el auto-cache de DS):

  ```powershell
  python -c "from api.agent.prompts.system import build_system_blocks; a = build_system_blocks('dock'); b = build_system_blocks('dock'); print('identical:', a == b)"
  ```

  Esperado: `identical: True`. Si `False`, hay drift en el prefix (cualquier byte diferente) y el auto-cache no hits → costo más alto. Investigar antes de aceptar el bake.
- `error_rate < 0.05`
- `p95_latency_ms <= 4000` (puede ser un poco más para surfaces R1 — aceptable si reasoning es largo)
- `daily_spend_usd < 5.00`
- `0 hallucinations` en sample de 20+ turns
- `0 tenant leaks`
- `today.by_provider` muestra el split esperado por surface (kill_switch + autotune en deepseek-reasoner, resto en deepseek-chat)
- Cost comparison: `today.total_usd` overshoot vs DeepSeek Console < 2x

Si todo verde, el flip se queda. **Epic multi-provider cerrado.**

Pickups deferred que NO bloquean el cierre del epic:

- **Operativos** (post-rollout): index parcial sobre `provider IS NULL` para perf del backfill en DBs grandes; modelar el DS auto-cache discount + off-peak pricing en `estimate_cost`; eliminar `_resolve_provider_static` (cleanup nit).
- **Producto** (futuros epics): per-provider daily caps en circuit_breaker.py; frontend admin dashboard para metrics (hoy es curl + health script); per-tenant model overrides en config; KillSwitch/AutoTune/Historial frontend mounts (Fase 6 del epic #400 original); shared `<ProposalConfirm/>` component extract.
- **Documentación**: live-model safety regression suite (`pytest -m live`) cuando haya budget de CI.

---

## Apéndice — comandos rápidos

```powershell
# Status
curl http://localhost:8000/agent/status

# Health check (rolling)
python scripts/agent_health_check.py --window 24h
python scripts/agent_health_check.py --window 6h
python scripts/agent_health_check.py --window 1h
python scripts/agent_health_check.py --window 24h --json | jq

# Provider breakdown only
python scripts/agent_health_check.py --window 24h --json | jq '.spend_by_provider'

# Forzar trip del breaker
# (editá config.json, agent.breaker_open=true)

# Ver últimos turns por provider
python -c "
import sqlite3
con = sqlite3.connect('signals.db')
con.row_factory = sqlite3.Row
for r in con.execute('SELECT ts, role, provider, model, conversation_id, latency_ms, cost_usd, reasoning_tokens FROM agent_conversations ORDER BY id DESC LIMIT 10'):
    print(dict(r))
"

# Ver quotas
python -c "
import sqlite3
con = sqlite3.connect('signals.db')
con.row_factory = sqlite3.Row
for r in con.execute('SELECT * FROM agent_quotas'):
    print(dict(r))
"

# Sum cost by provider (post-flip pre/post comparison).
# NOTE: SQLite treats double-quoted "now" as a column name, not the
# string literal. Use Python to compute the cutoff and pass as a
# parameter (safer than quoting acrobatics inside a PowerShell-quoted
# python -c body).
python -c "
import sqlite3
from datetime import datetime, timedelta, timezone
con = sqlite3.connect('signals.db')
con.row_factory = sqlite3.Row
cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
rows = con.execute(
    'SELECT COALESCE(provider, ?) AS p, '
    '       SUM(cost_usd) AS total, COUNT(*) AS n '
    'FROM agent_conversations WHERE ts >= ? GROUP BY p',
    ('unknown', cutoff),
)
for r in rows:
    print(dict(r))
"
```
