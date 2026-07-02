# Task 6 Report — Freshness Owner Lifespan Thread

**Rama:** feat/baseline-forward-log  
**Commit:** e608490  
**Estado:** COMPLETADO

---

## Lo que se hizo

### Archivos modificados
- `scanner/runtime.py` — +97 líneas netas
- `tests/test_baseline_owner.py` — creado (2 tests)

### Cambios en `scanner/runtime.py`

1. **Imports de baseline** (top-level, tras `sync_loop`):
   - `from scanner.baseline.ensemble import BaselineEnsemble`
   - `from scanner.baseline import store as _baseline_store`

2. **Constantes y helpers** (puntos de inyección para tests):
   - `_BASELINE_PATH` — apunta a `_baseline_store._DEFAULT_PATH`
   - `_BASELINE_INTERVAL_SEC = 3600`
   - `_baseline_universe()` — símbolos activos sin BTCUSDT
   - `_baseline_bar(symbol)` — última barra diaria vía `_fetch_daily_bars`, lazy import
   - `_baseline_today()` — fecha UTC actual como ISO string, lazy import

3. **`baseline_loop(stop_event)`** — el freshness owner (#8):
   - Carga ensemble del disco (o crea uno nuevo si no existe)
   - Solo avanza si `today > ensemble.last_date` (idempotente por fecha)
   - Fetch de barras para el universo completo, ignora símbolos sin barra
   - Llama `ensemble.advance_day(today, bars, list(bars.keys()))`
   - Persiste con `generated_at` ISO (alimenta la frescura del endpoint)
   - `except Exception` por ciclo: loguea y continúa (un fallo no mata el thread)
   - `stop_event.wait(_BASELINE_INTERVAL_SEC)` — interruptible (mismo patrón que sync_loop)

4. **Registro en `start_scanner_thread()`**:
   - `baseline_thread` creado con `target=baseline_loop, name="baseline_loop"`, daemon=True
   - `.start()` + `_managed_threads.append(baseline_thread)` — teardown ordenado garantizado
   - Docstring actualizado: "six managed background threads" + ítem 6

---

## Tests

| Test | Resultado |
|------|-----------|
| `test_baseline_loop_ticks_and_persists` | PASS |
| `test_baseline_thread_registered_in_managed` | PASS |

El test 1 usa monkeypatch sobre los 4 helpers (`_baseline_universe`, `_baseline_bar`, `_baseline_today`, `_BASELINE_PATH`) — sin red, sin DB. Verifica que tras 0.5s el ensemble se persiste con `last_date == "2026-07-02"` y `generated_at` presente.

El test 2 inspecciona el source de `start_scanner_thread` para confirmar que `baseline_loop` y `baseline_thread` están presentes — garantía estática contra el teardown fantasma (#8).

---

## Gate rápido (cierre de MVP)

```
3726 passed, 2 skipped, 0 failed — 96.29s
```

Sin regresiones. Los 2 skips son pre-existentes (no relacionados con baseline).

---

## Constraints satisfechos

- **#8 (freshness owner):** `baseline_loop` es el único dueño nombrado. Corre como lifespan thread registrado en `_managed_threads`. El `generated_at` persistido alimenta `LiveSnapshot` vía `store.load`. Nunca un CLI manual.
- **#3 (forward only):** usa `_fetch_daily_bars` en vivo. Nunca toca `data/holdout/`.
- **#4 (RISK_PER_TRADE):** baseline es paper sizing equal-weight, no toca `RISK_PER_TRADE`.
- **Patrón de loop interrumpible:** `stop_event.wait(_BASELINE_INTERVAL_SEC)` — mismo patrón que `sync_loop`, `screener_loop`.

---

## Commits del MVP completo (base7..head7)

```
e608490  feat(baseline): freshness owner lifespan thread (#8) + registro en managed threads
```

(Los commits de Tasks 1–5 están en la misma rama; este es el cierre de Task 6.)

---

---

## Review Final — correcciones pre-merge (2026-07-02)

**Commit:** (ver abajo — head7 actualizado)

### Fix A — avance hueco en apagón total
`baseline_loop`: `advance_day` + `persist` ahora solo se ejecutan si `bars` no está vacío. Si está vacío, se loguea un warning y no se avanza, dejando que la frescura degrade a `rancio`→`muerto` honestamente.

### Fix B — `_baseline_bar` atrapa errores de red crudos
`_baseline_bar` ahora captura `(requests.RequestException, BinanceUnavailable)` en vez de solo `BinanceUnavailable`. Un error de red transitorio en un símbolo devuelve `None` (se salta) en vez de propagar y abortar el ciclo.

### Fix C — docstring stale en `stop_managed_threads`
Cambiado "five after the liveness fix" → "six after the liveness fix".

### Test nuevo
`test_baseline_loop_skips_on_no_bars` — monkeypatch de `_baseline_bar` → None para todos; verifica que el store no persistió nada (`load` devuelve `(None, None)`).

### Gate
```
3 passed in 1.69s  (tests/test_baseline_owner.py -v)
```

---

## Notas post-MVP

Lo diferido según el brief: la comparación operador-vs-baseline (captura de decisiones reales + scorecard) se construye cuando el papá haya operado N semanas y el baseline tenga historia. Registrar con `mex log` al cerrar.
