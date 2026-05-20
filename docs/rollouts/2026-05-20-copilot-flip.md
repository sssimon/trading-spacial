# Copilot rollout — flip a producción (Phase 6 of #400)

**Fecha planeada:** martes 2026-05-20, ~10:30 UTC.
**Operator:** Simon.
**Ventana de bake:** 48h.

Este documento es el runbook ejecutable. Tachá cada paso a medida que
lo confirmás.

---

## 0. Pre-flip (~30 min)

### 0.1 Verificá que estás en `main` post-Phase 5B

```
git fetch origin main
git log --oneline -3
```

Esperado: el tip es el merge de PR #409 (Phase 5B) o más reciente.

### 0.2 Secret + API key en el `.env` de prod

```powershell
# AGENT_PROPOSAL_SECRET: 32+ bytes random, separado de JWT_SECRET.
# Generalo así:
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Pegá el output al .env como:
#   AGENT_PROPOSAL_SECRET=<token>

# ANTHROPIC_API_KEY: el que ya tenés en Anthropic Console.
#   ANTHROPIC_API_KEY=sk-ant-...
```

Verificá después que ambos están cargados:

```powershell
python -c "import os; print('PROP:', bool(os.environ.get('AGENT_PROPOSAL_SECRET'))); print('ANTH:', bool(os.environ.get('ANTHROPIC_API_KEY')))"
```

Esperado: `PROP: True` + `ANTH: True`.

### 0.3 Defaults siguen OFF

```powershell
python -c "import json; print(json.load(open('config.defaults.json'))['agent'])"
```

Esperado: `{'enabled': False, 'global_daily_usd_cap': 5.0, 'breaker_open': False}`.

**No edites `config.defaults.json` para el flip.** El override va en
tu `config.json` local (gitignored, per-deploy).

### 0.4 Restart `btc_api.py` para que cargue el schema agent (idempotente)

```powershell
# Para el proceso actual de btc_api.py, después arrancá de nuevo
# normalmente (INICIAR_API.bat o el watchdog).
```

Después verificá que el schema landed:

```powershell
python -c "import sqlite3; con=sqlite3.connect('signals.db'); print([r[0] for r in con.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'agent_%'\")])"
```

Esperado: `['agent_conversations', 'agent_side_effects', 'agent_quotas']`.

### 0.5 Smoke-test con copilot OFF

```powershell
curl http://localhost:8000/agent/status
```

Esperado: `{"enabled": false, "reason": "agent_disabled"}`.

```powershell
curl -X POST http://localhost:8000/agent/conversations/test1/turn -H "Content-Type: application/json" --data "{\"surface\":\"dock\",\"messages\":[{\"role\":\"user\",\"content\":\"hola\"}]}"
```

Esperado: HTTP 503, body `{"detail": "agent_disabled"}`.

Abrí el dashboard en el navegador (http://localhost:3000 o donde corra
el frontend). Esperado: el AgentDock NO aparece (el botón ◈ flotante
está oculto porque `agent/status` devolvió `enabled: false`).

### 0.6 Health check pre-flip

```powershell
python scripts/agent_health_check.py --window 24h
```

Esperado: todas las métricas en `0.0000` con `[OK]`. La DB está vacía
de turns todavía.

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

Si ya existe, agregá el bloque `agent` o flippá `enabled` a `true`.
**No toques `global_daily_usd_cap`** — heredás el `5.0` del default,
es lo que decidiste para la primera semana.

### 1.2 NO necesitás restart de `btc_api.py`

Verificado en código (PR #410 review): `api/agent/config.py:58`
llama `load_config()` dentro de `get_agent_status()`, que se ejecuta
en cada request. `api/config.py:load_config()` abre los archivos JSON
en cada llamada — sin caching. El cambio en `config.json` toma efecto
en el próximo request.

El único caso que requiere restart es el del paso 0.4 (schema
migration), porque eso necesita que `init_db()` corra. Una vez que
las tablas `agent_*` existen, los toggles de `config.json` son
hot-reload por diseño.

### 1.3 Verificá el flip

```powershell
curl http://localhost:8000/agent/status
```

Esperado: `{"enabled": true, "reason": "ok"}`.

---

## 2. Post-flip smoke (~5 min)

### 2.1 Primer turn end-to-end

Abrí el dashboard, abrí el dock con el botón ◈, y mandá:

> hola

Esperado:
- Streaming text aparece carácter por carácter (no de golpe).
- No hay errores en consola del browser.
- El backend log no muestra excepciones.

### 2.2 Segundo turn (verificación de cache)

En la misma conversación, mandá:

> qué posiciones tengo

Esperado:
- Streaming text + tool chip `get_positions` con ✓.
- Respuesta referencia posiciones reales (o "no tienes posiciones abiertas").
- Sin tu intervención, el modelo NO inventa IDs.

### 2.3 Confirmá la audit row + cache tokens

```powershell
python -c "
import sqlite3
con = sqlite3.connect('signals.db')
con.row_factory = sqlite3.Row
for r in con.execute('SELECT conversation_id, role, input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens, cost_usd FROM agent_conversations ORDER BY id DESC LIMIT 5'):
    print(dict(r))
"
```

Esperado:
- Al menos 2 rows con `role='assistant'`.
- La 1ra row (primer turn): `cache_creation_input_tokens > 0`.
- La 2da row (segundo turn): `cache_read_input_tokens > 0`.
- Ambas tienen `cost_usd > 0` y `< 0.10`.

### 2.4 Verificá `/agent/metrics` (admin)

```powershell
# Si tu cookie de admin está en el browser, abrí en una pestaña:
#   http://localhost:8000/agent/metrics
# Si querés desde curl, necesitás pasar la cookie JWT.
```

Esperado: `breaker.tripped: false`, `today.turn_count >= 2`, `today.total_usd > 0`.

### 2.5 Health check 1h post-flip

```powershell
python scripts/agent_health_check.py --window 1h
```

Esperado: todas `[OK]`. Si `cache_hit_rate` está bajo pero `n<5`, la
métrica está en warmup waiver — no alarmes.

---

## 3. Monitor 48h

Corré el health check cada ~6h durante las primeras 48h:

```powershell
python scripts/agent_health_check.py --window 6h    # ventana de 6h
python scripts/agent_health_check.py --window 24h   # ventana de 24h
```

Si querés salida machine-readable:

```powershell
python scripts/agent_health_check.py --window 24h --json
```

### Umbrales de abort

Cualquiera de estos, **sostenido más de 2 horas**, dispara abort:

| Métrica | Threshold | Acción si breach |
|---|---|---|
| `cache_hit_rate` | < 0.50 post-warmup | Investigá: ¿se está rompiendo el cache prefix? Verificá el sistema prompt no cambia turn a turn. |
| `error_rate` | > 0.05 | Mirá `error_breakdown_24h` en `/agent/metrics`. Si es `upstream` saturado, esperá. Si es algo nuevo, abort. |
| `p95_latency_ms` | > 4000 | Verificá nginx + backend logs. Posiblemente turn con muchos hops. |
| `daily_spend_usd` | cerca de $5 | El breaker auto-trips. Decidí: subir cap, o investigar el spike. |

También dispara abort:
- Cualquier incident de **leak de tenant** (audit row con `tenant_id` distinto al del JWT del request).
- Cualquier **hallucination detectada** en sample (corré `assert_text_grounded` sobre 10-20 turns al azar).

### Abort: cómo flippar

```powershell
# Editá config.json:
#   "agent": { "breaker_open": true }
# Restart btc_api.py si no hot-reloadea.
```

Esperado post-flip-breaker:
```
curl /agent/status → {"enabled": false, "reason": "breaker_open"}
```

El cambio NO requiere code deploy. Si querés rollback total a la
versión sin copilot, flippá `enabled: false` en vez de `breaker_open: true`.

---

## 4. Success criteria (avanzar a roll global)

Después de 48h sin breach:

- `cache_hit_rate >= 0.70` (target del spec §14)
- `error_rate < 0.05`
- `p95_latency_ms <= 4000`
- `daily_spend_usd < 5.00`
- `0 hallucinations` en sample de 20+ turns
- `0 tenant leaks`

Si todo verde, el flip se queda. Phase 6 cerrada. Epic #400 done.

Pickups futuros que NO bloquean el cierre del epic:

- Live-model safety regression test (`pytest -m live`) — needs CI budget.
- Wire `assert_text_grounded` como production postcheck (refusa turns con references ungrounded).
- Frontend mounts para KillSwitch / AutoTune / Historial surfaces — backend ya está listo.
- `<ProposalConfirm/>` shared component extraction antes de wirear las 3 superficies.
- Per-surface model override desde admin UI (`global_daily_usd_cap` per-tenant también).

---

## Apéndice — comandos rápidos

```powershell
# Health check
python scripts/agent_health_check.py --window 24h
python scripts/agent_health_check.py --window 1h
python scripts/agent_health_check.py --window 6h --json | jq

# Status
curl http://localhost:8000/agent/status

# Forzar trip del breaker
# (editá config.json, flippá agent.breaker_open=true)

# Ver últimos audit rows
python -c "
import sqlite3
con = sqlite3.connect('signals.db')
con.row_factory = sqlite3.Row
for r in con.execute('SELECT ts, role, tenant_id, surface, conversation_id, latency_ms, cost_usd FROM agent_conversations ORDER BY id DESC LIMIT 10'):
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
```
