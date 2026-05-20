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

### 2.1 Primer turn por el Dock (default: deepseek-chat)

Abrí el dashboard, abrí el dock con el botón ◈, mandá:

> hola

Esperado:
- Streaming text aparece carácter por carácter.
- No hay panel "Razonamiento" (deepseek-chat no genera reasoning).
- No errores en consola del browser ni en el log de btc_api.py.

### 2.2 Verificá el provider en la audit row

```powershell
python -c "import sqlite3; con=sqlite3.connect('signals.db'); con.row_factory=sqlite3.Row; r=con.execute('SELECT model, provider, reasoning_tokens, cost_usd FROM agent_conversations ORDER BY id DESC LIMIT 1').fetchone(); print(dict(r))"
```

Esperado: algo como:
```
{'model': 'deepseek-chat', 'provider': 'deepseek', 'reasoning_tokens': 0, 'cost_usd': 0.0001}
```

`provider='deepseek'` confirma que el migration de Fase 3b + la audit wiring de Fase 4 funcionaron end-to-end. `reasoning_tokens=0` esperado (chat-V3 reporta 0).

### 2.3 Turn analítico con R1 (verifica el panel de razonamiento)

En el AgentDock, mandá:

> qué surface usa razonamiento

(o algo que el modelo conteste con análisis — la pregunta no importa; lo que importa es que la respuesta venga de un surface que defaultea a R1).

Esperado:
- Streaming text aparece.
- DEBAJO del bubble principal aparece `<details>` con summary "razonamiento" colapsado.
- Click en "razonamiento" expande el panel con el chain-of-thought de R1 (puede ser largo — varios kilobytes).
- Cierre con click de nuevo.

**Si NO aparece el panel:** el surface default que estás invocando podría ser `dock` (que es `deepseek-chat`, no R1). El panel solo aparece en surfaces que defaultean a `deepseek-reasoner` — kill_switch / autotune por design. Para forzar R1 en cualquier surface, podés POST directo con `body.model="deepseek-reasoner"`.

### 2.4 Override path (claude-* per-turn)

**Solo si tenés ANTHROPIC_API_KEY configurada.** Sino, saltá esto.

```powershell
curl -X POST http://localhost:8000/agent/conversations/override-test/turn `
  -H "Content-Type: application/json" `
  -H "Cookie: <tu cookie JWT>" `
  --data "{\"surface\":\"dock\",\"model\":\"claude-sonnet-4-6\",\"messages\":[{\"role\":\"user\",\"content\":\"hola desde claude\"}]}"
```

Esperado: streaming responde normalmente. Luego:

```powershell
python -c "import sqlite3; con=sqlite3.connect('signals.db'); con.row_factory=sqlite3.Row; r=con.execute('SELECT model, provider FROM agent_conversations WHERE conversation_id=\"override-test\" ORDER BY id DESC LIMIT 1').fetchone(); print(dict(r))"
```

Esperado: `{'model': 'claude-sonnet-4-6', 'provider': 'anthropic'}`. **Esto confirma que el fix del per-request provider resolution (PR #415 review) funciona:** el override rutea correctamente a Anthropic, no a DS.

### 2.5 Confirmá `/agent/metrics`

```powershell
curl http://localhost:8000/agent/metrics
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
- Cualquier **hallucination detectada** en sample (corré `assert_text_grounded` sobre 10-20 turns al azar).
- **Reasoning content leak**: el panel `<details>` no debería contener info de OTROS tenants. Si lo hace, abort + investigar.

### Abort: cómo flippar

```powershell
# Editá config.json:
#   "agent": { "breaker_open": true }
```

`load_config` re-lee en cada request → cambio toma efecto inmediatamente. Esperado post-flip-breaker:

```
curl /agent/status → {"enabled": false, "reason": "breaker_open"}
```

Para rollback total al estado pre-Fase-5 (Anthropic como default), flippá `enabled: false` Y editá `config.json` para fijar:

```json
{
  "agent": {
    "enabled": false,
    "surface_model_overrides": {
      "dock":          "claude-sonnet-4-6",
      "symbol_detail": "claude-haiku-4-5",
      "kill_switch":   "claude-sonnet-4-6",
      "autotune":      "claude-sonnet-4-6",
      "historial":     "claude-haiku-4-5"
    }
  }
}
```

**NOTA:** la key `surface_model_overrides` no existe todavía en el codebase. Si necesitás este path de rollback, primero implementálo (es un agregado pequeño a `default_model_for_surface()` para chequear el override antes del default).

---

## 4. Success criteria (epic cierra)

Después de 48h sin breach:

- `cache_hit_rate` no aplica de la misma forma (DS no reporta cache stats); verificar que el prefijo del system prompt es estable inspeccionando 2 rows consecutivos.
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

# Sum cost by provider (post-flip pre/post comparison)
python -c "
import sqlite3
con = sqlite3.connect('signals.db')
con.row_factory = sqlite3.Row
for r in con.execute('SELECT COALESCE(provider, \"unknown\") AS p, SUM(cost_usd) AS total, COUNT(*) AS n FROM agent_conversations WHERE ts >= datetime(\"now\", \"-24 hours\") GROUP BY p'):
    print(dict(r))
"
```
