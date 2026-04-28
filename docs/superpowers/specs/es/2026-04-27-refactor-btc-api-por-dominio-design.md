# Refactor btc_api.py por dominio — diseño consolidado

**Epic propuesto:** TBD (a abrir tras review del spec)
**Autor:** Samuel (direction) + Claude (drafting)
**Fecha:** 2026-04-27
**Status:** Draft para review

---

## TL;DR

`btc_api.py` (2618 LOC) mezcla configuración, telegram/webhook, capa DB, ~50 rutas FastAPI sobre 8 dominios distintos, y el thread del scanner. Tras epic #186 (que extrajo la lógica decisional pura a `strategy/`), `btc_api.py` quedó como el siguiente cuello de botella de mantenibilidad.

Este plan rompe `btc_api.py` en módulos por dominio siguiendo el patrón validado en #186, con dos diferencias clave:

1. **Separación híbrida `api/` + `db/`** — cada dominio "pesado" tiene `api/<dominio>.py` (rutas + servicio) y `db/<dominio>.py` (queries). Dominios "ligeros" (config, ohlcv) viven en un solo archivo. La capa DB se aísla porque concentra el riesgo de drift y tiene su propio ciclo de vida (esquema, conexión).
2. **Tests de paridad por endpoint** — antes de mover cada dominio se captura un snapshot de las respuestas HTTP con DB sembrada; tras el move el snapshot debe coincidir bit-a-bit. Mismo principio que `test_strategy_indicators.py` aplicado a `TestClient`.

Resultado objetivo: `btc_api.py` < 200 LOC (solo bootstrap FastAPI), 8 módulos en `api/`, 4 en `db/`, scanner thread en su propio `scanner/runtime.py`. Refactor de `btc_scanner.py` post-#186 queda como issue follow-up con destino diferente (varios módulos van a `strategy/`, otros a infra/CLI).

Beneficios esperados: legibilidad humana (archivos < 500 LOC), editabilidad agéntica (Claude/agentes editan archivos enfocados con menos riesgo), testabilidad (cada dominio tiene tests propios, baseline auditado).

---

## 1. Contexto y motivación

### 1.1 Estado actual

| Archivo | LOC | Estado tras #186 |
|---|---|---|
| `btc_api.py` | 2618 | Prácticamente intacto — concentra rutas, DB, telegram, scanner thread |
| `btc_scanner.py` | 1494 | Post-#186 — ya delgado tras extracción a `strategy/core.py` |
| `strategy/` | ~2700 (split en 9 archivos) | Patrón validado de extracción por responsabilidad |

`btc_api.py` actualmente mezcla 8 dominios sin separación de archivo:

- **config** (164-326): `_deep_merge`, `load_config`, `save_config`, `ConfigUpdate`, `SignalFiltersUpdate`
- **notifications/telegram** (327-403, 1226-1404): `should_notify_signal`, `build_telegram_message`, `push_telegram_direct`, `_send_telegram_raw`
- **CSV/symbols-json export** (384-453, 704-741)
- **positions DB+stops** (496-740, 2200-2257): `db_create_position`, `check_position_stops`, rutas
- **DB layer** (804-1224): `get_db`, `init_db`, `_DictRow`, `save_scan`, `get_scans`, `get_latest_signal`, `get_latest_scan`, `get_signals_summary`
- **webhook publishing** (1308-1436): `push_webhook`
- **scanner thread** (1437-1620): `execute_scan_for_symbol`, `scanner_loop`, `start_scanner_thread`
- **routes FastAPI** (1621-2568): ~50 endpoints sobre signals, config, ohlcv, positions, health, notifications, kill_switch, tune

### 1.2 Problema que este plan resuelve

1. **Legibilidad** — un archivo de 2618 LOC mezcla concerns. Encontrar dónde vive una ruta vs una query DB requiere `grep`.
2. **Editabilidad agéntica** — Claude/agentes producen edits más confiables sobre archivos < 500 LOC; >2000 LOC genera errores de contexto y diffs ruidosos.
3. **Testabilidad** — hoy no hay manera limpia de testear el dominio "positions" sin arrancar la app entera; tras el refactor cada dominio tiene fixtures + parity tests propios.
4. **Drift de constantes** — `LRC_PERIOD`, `RSI_PERIOD`, `LRC_LONG_MAX`, `SCORE_*` están **triplicadas** entre `btc_scanner.py:67-73,412-422`, `strategy/core.py:39-56` y `strategy/sizing.py:8-9` con un comentario explicando la duplicación intencional. Un `strategy/constants.py` compartido elimina el riesgo de un solo golpe.

### 1.3 Restricción: no romper producción

`btc_api.py` se ejecuta como entrypoint (`python btc_api.py`), tiene 628+ tests pasando, y un frontend que hace requests vía nginx → puerto 8000. Cualquier refactor debe:

- Mantener `python btc_api.py` como entrypoint funcional (no romper Docker, Windows scripts).
- Pasar la suite completa después de cada PR.
- No alterar el shape de las respuestas HTTP (contract con frontend).

---

## 2. Objetivos y criterios de éxito

### 2.1 Objetivos primarios

1. **`btc_api.py` ≤ 200 LOC** al final del plan (solo bootstrap FastAPI).
2. **Cada módulo nuevo < 500 LOC** (mayoría < 350).
3. **Cero regresión** — 628+ tests pasan tras cada PR; respuestas HTTP idénticas.
4. **`strategy/constants.py` como única fuente** de constantes de indicadores y score tiers.

### 2.2 Objetivos secundarios

5. Cada dominio tiene tests de paridad propios + fixtures aislados.
6. Reglas de import explícitas y verificadas (anti-cycle, anti-drift).
7. Issue follow-up creado para btc_scanner.py post-#186 cleanup con destino por-pieza definido.

### 2.3 Métricas de éxito

- `wc -l btc_api.py` ≤ 200
- `find api db scanner -name "*.py" | xargs wc -l | sort -n` muestra todos los archivos < 500 LOC
- `pytest tests/ -v` ≥ 628 tests pasando
- `python btc_api.py` arranca y `curl localhost:8000/health` responde 200
- Frontend dashboard renderiza tabs sin cambios

---

## 3. Arquitectura objetivo

### 3.1 Estructura de archivos final

```
trading-spacial/
├── btc_api.py                      ← ≤ 200 LOC: FastAPI app, lifespan, mount routers
├── btc_scanner.py                  ← sin cambios este plan (issue follow-up)
├── strategy/
│   ├── constants.py                ← NUEVO: LRC_*, RSI_*, BB_*, ATR_*, SCORE_*, LRC_LONG_MAX/SHORT_MIN
│   ├── core.py, sizing.py, ...     ← importan de constants.py (drift eliminado)
├── api/                            ← NUEVO: rutas + servicio por dominio
│   ├── __init__.py
│   ├── deps.py                     (~40 LOC)   verify_api_key, dependencias compartidas
│   ├── ohlcv.py                    (~80 LOC)   APIRouter
│   ├── config.py                   (~180 LOC)  APIRouter + load/save/validate
│   ├── telegram.py                 (~200 LOC)  service: build_message, push_*, _send_raw
│   ├── notifications.py            (~80 LOC)   APIRouter (in-app notifications: list/read)
│   ├── positions.py                (~350 LOC)  APIRouter + check_stops + _calc_pnl
│   ├── signals.py                  (~500 LOC)  APIRouter + should_notify_signal + dedup + csv/log appenders
│   ├── kill_switch.py              (~150 LOC)  APIRouter (thin wrapper sobre strategy/)
│   ├── health.py                   (~80 LOC)   APIRouter (thin wrapper sobre health.py)
│   └── tune.py                     (~120 LOC)  APIRouter (thin wrapper)
├── db/                             ← NUEVO: queries + esquema por dominio
│   ├── __init__.py
│   ├── connection.py               (~100 LOC)  get_db, _DictRow, backup_db
│   ├── schema.py                   (~250 LOC)  init_db (CREATE TABLE statements)
│   ├── positions.py                (~150 LOC)  CRUD positions
│   └── signals.py                  (~200 LOC)  save_scan, get_scans, get_latest_*, summary
├── scanner/                        ← NUEVO: scanner loop como servicio
│   ├── __init__.py
│   └── runtime.py                  (~250 LOC)  scanner_loop, execute_scan_for_symbol, start_thread, check_pending_signal_outcomes
└── tests/
    ├── _baselines/                 ← NUEVO: snapshots JSON por dominio
    ├── _baseline_capture.py        ← NUEVO: helper para regenerar snapshots
    ├── test_api_<dominio>_parity.py ← uno por dominio
    └── ... (existentes intactos)
```

### 3.2 Reglas de import (anti-drift)

| Origen | Puede importar | NO puede importar |
|---|---|---|
| `api/*` | `db/*`, `strategy/*`, `scanner/*`, `health.py`, `notifications.py` | `btc_api.py` |
| `db/*` | `strategy/constants.py` (solo si necesita) | `api/*`, `scanner/*` |
| `scanner/runtime.py` | `db/*`, `strategy/*`, `api/telegram.py` (servicio) | `api/*` (routers) |
| `btc_api.py` | `api/*`, `db/connection.py`, `scanner/runtime.py` | — |
| `strategy/*` | `strategy/constants.py` | nada fuera de `strategy/` |

CI verifica las reglas con un test de imports (PR0 incluye `tests/test_import_boundaries.py`).

### 3.3 Decisión: por qué híbrido (no plano, no per-domain package)

| Patrón | Pros | Contras | Decisión |
|---|---|---|---|
| Plano `api/<dominio>.py` (todo junto) | Mínima ceremonia, estilo `strategy/` | DB queries y rutas mezcladas, drift de queries | Rechazado |
| Per-domain package (`positions/{routes,db,service}.py`) | Frontera fuerte | Más archivos, overhead de imports | Rechazado |
| **Híbrido `api/<dominio>.py` + `db/<dominio>.py`** | DB aislada (mayor riesgo de drift), rutas/servicio juntos donde sí se leen juntos | Dos archivos por dominio pesado | **Elegido** |

Razón: `strategy/` pudo ser plano porque era lógica pura sin DB. Aquí el I/O es ineludible, y la capa DB es donde más drift histórico ha ocurrido — separarla compensa el archivo extra.

---

## 4. Plan de PRs

### 4.1 Resumen y dependencias

```
PR0 (foundation)
  ├─→ PR1 (ohlcv)         ─┐
  ├─→ PR2 (config)        ─┤
  └─→ PR3 (telegram)      ─┴─→ PR4 (positions)
                              ─→ PR5 (signals)
                              ─→ PR6 (thin wrappers: kill_switch + health + tune)
                                     ↓
                                   PR7 (scanner/runtime.py + bootstrap final)
```

PR1, PR2, PR3 son paralelizables tras PR0. PR4-PR6 dependen de PR2+PR3 (todos usan config y/o telegram). PR7 depende de todos los anteriores.

### 4.2 PR0 — Foundation

**Alcance:**
- Crear `strategy/constants.py` con `LRC_PERIOD`, `LRC_STDEV`, `RSI_PERIOD`, `BB_PERIOD`, `BB_STDEV`, `VOL_PERIOD`, `ATR_PERIOD`, `ATR_SL_MULT_DEFAULT`, `ATR_TP_MULT_DEFAULT`, `ATR_BE_MULT_DEFAULT`, `LRC_LONG_MAX`, `LRC_SHORT_MIN`, `SCORE_MIN_HALF`, `SCORE_STANDARD`, `SCORE_PREMIUM`.
- Reescribir `btc_scanner.py:67-73,412-422`, `strategy/core.py:39-56`, `strategy/sizing.py:8-9` para importar de `strategy/constants.py`.
- Scaffolding: crear `api/__init__.py`, `api/deps.py` (con `verify_api_key`), `db/__init__.py`, `scanner/__init__.py`, `tests/_baselines/`.
- Extraer capa de conexión DB: `get_db`, `_DictRow`, `backup_db` → `db/connection.py`. `init_db` (CREATE TABLEs) → `db/schema.py`. `btc_api.py` re-exporta para compatibilidad.
- Crear `tests/_baseline_capture.py` (helper para regenerar snapshots JSON desde DB sembrada).
- Crear `tests/test_import_boundaries.py` (verifica reglas de import).
- Smoke test del scanner thread: `tests/test_scanner_smoke.py` (boot + 1 ciclo, ver logs).

**Cobertura de tests:** test_import_boundaries + test que verifica que `btc_scanner.LRC_PERIOD is strategy.constants.LRC_PERIOD` (mismo objeto) tras la migración.

### 4.3 PR1 — ohlcv

**Alcance:** Mover `/ohlcv` route + helpers a `api/ohlcv.py`. Sin DB queries (usa data fetcher del scanner). `btc_api.py` mantiene re-export hasta PR7.

**Tests de paridad:** `tests/test_api_ohlcv_parity.py` con snapshot del response shape para BTC/ETH 1h y 4h.

### 4.4 PR2 — config

**Alcance:**
- Mover a `api/config.py`: rutas `/config GET POST`, modelos `ConfigUpdate`, `SignalFiltersUpdate`, helpers `_deep_merge`, `_load_json_file`, `load_config`, `save_config`, `_strip_secrets`.
- Sin `db/config.py` (config es file-based, no DB).
- `btc_api.py` re-exporta `load_config` para que otros módulos legacy (scanner thread, etc.) sigan importándola hasta migrar.

**Tests de paridad:** GET /config (con secrets stripped), POST /config con deep-merge, validation errors.

### 4.5 PR3 — telegram

**Alcance:**
- Mover a `api/telegram.py`: `build_telegram_message`, `push_telegram_direct`, `_send_telegram_raw`, `push_webhook`.
- Sin `db/` (outbound HTTP only).
- Mock del HTTP push igual que tests existentes.
- **No incluye** `should_notify_signal`, `_is_duplicate_signal`, `_mark_notified` — esos son filtros de signal-domain y van en PR5.

**Tests de paridad:** mock requests; verificar mensaje Telegram tiene exactamente el mismo formato (string match).

### 4.6 PR4 — positions

**Alcance:**
- `api/positions.py`: rutas `/positions GET POST PUT DELETE`, `/positions/{id}/close`, `check_position_stops`, `_calc_pnl`, `_write_position_event_log`, `update_positions_json`.
- `db/positions.py`: `db_create_position`, `db_get_positions`, `db_close_position`, `db_update_position`.
- `btc_api.py` re-exporta `db_create_position` (lo usa scanner thread hasta PR7).

**Tests de paridad:** CRUD completo con DB sembrada (3 posiciones abiertas, 2 cerradas), check_position_stops con SL/TP/BE triggers.

### 4.7 PR5 — signals

**Alcance:**
- `api/signals.py`: rutas `/signals*` (list, performance, latest, latest/message, by id), `should_notify_signal`, `_is_duplicate_signal`, `_mark_notified`, `append_signal_csv`, `append_signal_log`, `update_symbols_json`, `_csv_escape`, `check_pending_signal_outcomes` (versión read-only — la versión que escribe va a `scanner/runtime.py` en PR7).
- `db/signals.py`: `save_scan`, `get_scans`, `get_latest_signal`, `get_latest_scan`, `get_signals_summary`.
- PR más pesado: ~600 LOC moved.

**Tests de paridad:** /signals con varios filtros (since, min_score, only_signals), /signals/latest, /signals/performance, decisión de notificación con varias combinaciones de filtros + score tiers.

### 4.8 PR6 — thin wrappers (kill_switch + health + tune + notifications)

**Alcance:**
- `api/kill_switch.py`: rutas `/kill_switch_recalibrate`, `/kill_switch/recommendations`, `/kill_switch/recommendations/{id}/apply`, `/kill_switch/recommendations/{id}/ignore`, `/kill_switch/decisions`, `/kill_switch/current_state`. Casi todo delega a `strategy/kill_switch_v2*`.
- `api/health.py`: rutas `/health`, `/health/symbols`, `/health/events`, `/health/dashboard`, `/health/reactivate/{symbol}`, `ReactivateRequest`. Delega a `health.py`.
- `api/tune.py`: rutas `/tune/latest`, `/tune/apply`, `/tune/reject`. Delega a tune system.
- `api/notifications.py`: rutas `/notifications` GET, `/notifications/{id}/read` POST, `/notifications/read-all` POST. ~50 LOC, en este mismo PR (no sub-PR — encaja por tamaño y estilo "thin wrapper").

**Tests de paridad:** una prueba GET por dominio + casos auth.

### 4.9 PR7 — scanner/runtime.py + bootstrap final

**Alcance:**
- Crear `scanner/runtime.py` con `scanner_loop`, `execute_scan_for_symbol`, `start_scanner_thread`, `check_pending_signal_outcomes` (versión que escribe).
- Migrar todos los imports legacy en `scanner/runtime.py` para usar `db/*` directo (no re-exports de btc_api).
- Eliminar **todos los re-exports temporales** de `btc_api.py`.
- `btc_api.py` final: FastAPI app, lifespan (on_startup → init_db, start_scanner_thread; on_shutdown → flush), mount de los 9 routers, root endpoint, dependencias compartidas.
- Verificar: `wc -l btc_api.py` ≤ 200.
- Cleanup: borrar funciones huérfanas, validar test suite, smoke manual + frontend.

**Tests de paridad:** test integration que arranca app + simula 1 ciclo de scan + verifica DB writes.

---

## 5. Anatomía de cada PR (plantilla mecánica)

Cada PR de domain (PR1-PR7) sigue esta secuencia:

### Paso 1 — Capturar baseline

```bash
# Pre-refactor, con DB sembrada determinísticamente:
python -m tests._baseline_capture <dominio> > tests/_baselines/<dominio>.json
git add tests/_baselines/<dominio>.json
```

### Paso 2 — Test de paridad (failing primero)

```python
# tests/test_api_<dominio>_parity.py
import json
from fastapi.testclient import TestClient

EXPECTED = json.loads(open("tests/_baselines/<dominio>.json").read())

def test_get_<endpoint>_parity(seeded_client):
    resp = seeded_client.get("/<endpoint>", headers={"X-API-Key": "test"})
    assert resp.status_code == EXPECTED["<endpoint>"]["status"]
    assert resp.json() == EXPECTED["<endpoint>"]["body"]
```

### Paso 3 — Crear módulos nuevos

- Copiar funciones tal-cual desde `btc_api.py` (no reescribir).
- Convertir route group a `APIRouter()` con prefix.
- Mover queries DB a `db/<dominio>.py` y `from db.<dominio> import ...` en `api/<dominio>.py`.

### Paso 4 — Re-export en btc_api.py (intermediate state)

```python
# btc_api.py durante el refactor
from api.positions import router as positions_router
app.include_router(positions_router)

# Re-exports temporales para imports legacy del scanner thread
from db.positions import db_create_position, db_get_positions  # noqa: F401
```

### Paso 5 — Verificar paridad

- `pytest tests/test_api_<dominio>_parity.py -v` pasa.
- `pytest tests/ -v` pasa entera (628+ tests).
- Smoke manual: `python btc_api.py &; curl localhost:8000/<endpoint>; kill %1`.

### Paso 6 — Limpiar btc_api.py

- Borrar el código viejo del dominio (ya está en `api/`+`db/`).
- Mantener re-exports temporales si otros módulos los importan; serán eliminados en PR7.

### Paso 7 — Commit & PR

Mensaje: `refactor(api): extract <dominio> to api/<dominio>.py + db/<dominio>.py`

---

## 6. Estrategia de testing

### 6.1 Tres capas de protección por PR

**a) Tests de paridad (nuevos por dominio)** — Snapshots JSON capturados pre-refactor con DB sembrada determinísticamente. Snapshots viven en `tests/_baselines/<dominio>.json` y son commiteados con el PR. Si cambian, el test falla y se investiga (no se regeneran ciegamente).

**b) Suite completa (existente)** — `pytest tests/ -v` debe pasar antes y después de cada PR. Hoy 628+ tests; cada PR puede agregar 5-15 tests nuevos pero **nunca disminuir el total ni romper existentes**.

**c) Smoke manual (1 minuto por PR)** — `python btc_api.py` arranca; `curl` a endpoints del dominio movido; frontend renderiza tabs.

### 6.2 Cobertura mínima por dominio

- Golden path por endpoint movido.
- 1-2 edge cases existentes (404, unauthenticated, validation error).

### 6.3 Lo que NO se testea

- Performance/latencia (refactor estructural, no funcional).
- Schema DB (no cambia — `db/schema.py` es copia exacta de `init_db()`).
- Telegram delivery real (los tests mockean el HTTP push como hoy).

### 6.4 Snapshot generation

`tests/_baseline_capture.py` (creado en PR0) reutilizable:

```bash
python -m tests._baseline_capture positions > tests/_baselines/positions.json
```

---

## 7. Riesgo y rollback

### 7.1 Registro de riesgo

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| DB connection compartida rompe en threading (scanner_loop + routes) | Media | Alto | PR0 valida que `get_db()` siga creando conexión por-call (no singleton). Test concurrente en PR0. |
| Import cycle `api/* ↔ scanner/runtime.py` | Media | Medio | Regla: scanner/ importa de `api/telegram` (servicios), nunca de routers. Verificado en `tests/test_import_boundaries.py` (PR0). |
| Re-export olvidado rompe scanner_loop silencioso | Alta | Alto | `tests/test_scanner_smoke.py` (PR0) ejecuta 1 ciclo de scan; falla loud si falta export. |
| Estado intermedio dura semanas y otro feature toca btc_api.py | Media | Medio | Plan completo en ~3-4 semanas si se ejecuta seguido. Avisar al equipo (kill-switch v2 sigue activo) y `git rebase` periódico. |
| Snapshot baselines obsoletos por feature legítimo | Baja | Bajo | Documentar en `tests/_baselines/README.md` cómo regenerar. |
| btc_api.py final < 200 LOC pero scanner thread queda enredado | Baja | Medio | PR7 explícitamente dedica tiempo a separar `scanner/runtime.py` con su propia entrypoint y lifecycle. |

### 7.2 Estrategia de rollback

- **Por PR:** cada PR es revertible con `git revert <sha>` porque mantiene re-exports de compatibilidad. Worst case: un revert deja `api/<dominio>.py` huérfano sin importar; safe.
- **Por fase:** si tras PR4 se descubre que el patrón híbrido no escala, revertir PR1-PR4 en orden inverso. PR0 (constants + scaffold) puede quedarse — son archivos nuevos que nadie importa todavía.
- **Stop conditions:** si dos PRs consecutivos requieren > 1 día de debugging post-merge, parar y reevaluar el patrón antes del siguiente PR.

---

## 8. Definition of done

- [ ] `wc -l btc_api.py` ≤ 200
- [ ] `api/` tiene 10 módulos (`deps`, `ohlcv`, `config`, `telegram`, `notifications`, `positions`, `signals`, `kill_switch`, `health`, `tune`); `db/` tiene 4 módulos (`connection`, `schema`, `positions`, `signals`); `scanner/runtime.py` existe
- [ ] `strategy/constants.py` es la única fuente de constantes de indicadores y score tiers
- [ ] 628+ tests pasan (`pytest tests/ -v`)
- [ ] `tests/test_import_boundaries.py` verifica reglas anti-cycle / anti-drift
- [ ] `python btc_api.py` arranca y `curl localhost:8000/health` responde 200
- [ ] Frontend dashboard renderiza tabs sin cambios visuales
- [ ] Issue follow-up creado para btc_scanner.py post-#186 cleanup

---

## 9. Fuera de alcance

### 9.1 Issue follow-up: btc_scanner.py post-#186 cleanup

Crear tras este spec con título tentativo:

> **refactor(scanner): break btc_scanner.py post-#186 leftovers by purpose**
>
> btc_scanner.py (1494 LOC post-#186) mezcla piezas con destinos naturales distintos:
> - `detect_regime` + cache → `strategy/regime.py` (lógica decisional)
> - `detect_bull_engulfing`, `_bear_engulfing`, `detect_rsi_divergence`, `check_trigger_5m*` → `strategy/patterns.py`
> - `resolve_direction_params` → `strategy/direction.py`
> - `get_top_symbols`, `_get_binance_usdt_symbols`, `get_active_symbols` → `markets/symbols.py`
> - `_load_proxy`, `_rate_limit` → `infra/http.py`
> - `fmt`, `save_log`, `main` → `cli/scanner_report.py`
>
> Apuntando a btc_scanner.py < 200 LOC siguiendo el patrón validado en epic #186 y en este refactor de btc_api.py.

### 9.2 Otros refactors NO incluidos

- Renombrar funciones públicas (`db_create_position` → `create_position`): trabajo separado.
- Migrar de SQLite a Postgres: trabajo separado.
- Reorganizar frontend `src/` por dominio: trabajo separado (mucho más chico y ortogonal).
- Tests E2E con Playwright: trabajo separado.

---

## 10. Próximos pasos

1. Review de este spec por Samuel.
2. Si aprobado: invocar skill `superpowers:writing-plans` para generar plan de implementación detallado por PR.
3. Crear issue follow-up para btc_scanner.py post-#186 cleanup.
4. Ejecutar PR0 con superpowers:executing-plans (o subagent-driven-development si se quiere paralelizar PR1-PR3 tras PR0).
