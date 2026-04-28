# Refactor btc_scanner.py por propósito — diseño consolidado

**Issue:** #225 — refactor(scanner): break btc_scanner.py post-#186 leftovers by purpose
**Autor:** Samuel (direction) + Claude (drafting)
**Fecha:** 2026-04-28
**Status:** Draft para review

---

## TL;DR

Tras epic #186 (extracción de la lógica decisional pura a `strategy/`) y los PRs #226/#227 (split de `btc_api.py` en `api/`+`db/`+`scanner/runtime.py`), `btc_scanner.py` (1485 LOC) es el último monolito relevante. Mezcla piezas con destinos naturales distintos: detector de régimen, patrones de velas, parámetros por dirección, clasificador de tune, helpers HTTP, y la capa de CLI/output.

Este plan rompe `btc_scanner.py` siguiendo el patrón validado en #226, con tres ajustes clave:

1. **Modulos en `strategy/` por responsabilidad funcional** — `regime.py`, `patterns.py`, `direction.py`, `tune.py`, `vol.py`. Más `infra/http.py` para los helpers HTTP de bajo nivel y `cli/scanner_report.py` para el formatter+main del CLI.
2. **Snapshot end-to-end de `scan("BTCUSDT")`** capturado en PR0 como única red de regresión funcional, complementado por tests `is`-identity por pieza movida (catch typos en re-exports). Sin baselines por dominio — los 600+ tests existentes ya cubren los internals.
3. **Protocolo de verificación pre/post por task** — cada PR (y cada task dentro del PR) debe correr el snapshot + suite completa antes de empezar y antes de mergear. Sin verification log, no merge.

Resultado objetivo: `btc_scanner.py` ≈ 510-540 LOC (solo `scan()` + setup + re-exports), reducción ~64% desde los 1485 actuales. El carve-up de `scan()` propiamente dicho (~449 LOC, queda intacto este refactor) está diferido a un follow-up — el target original "<200 LOC" del issue #225 es inalcanzable sin tocar `scan()`, decisión explícita en Q4 del brainstorm.

---

## 1. Contexto y motivación

### 1.1 Estado actual

| Archivo | LOC | Estado |
|---|---|---|
| `btc_scanner.py` | 1485 | Mezcla regime + patterns + direction + tune + vol + http + CLI + scan() |
| `strategy/` | ~2700 (split en 9 archivos) | Patrón validado de extracción por responsabilidad |
| `api/` | 10 módulos | Refactor #226 — patrón híbrido api+db |
| `scanner/runtime.py` | 387 | PR #226 — scanner thread + lifecycle |

`btc_scanner.py` actualmente mezcla las siguientes responsabilidades sin separación:

- **Detector de régimen** (lines 87-235, 675-850): scoring por componente (price/fng/funding/rsi/adx), composición ponderada por modo (global/hybrid/hybrid_momentum), cache JSON con TTL 24h, soft-migration de formato legacy.
- **Patrones de velas e indicadores derivados** (lines 523-668): bull/bear engulfing, divergencia RSI, gatillos 5m LONG/SHORT, etiqueta de score.
- **Parámetros por dirección** (lines 356-407, 857-864): resolución de `atr_sl_mult/tp/be` por símbolo + dirección, métricas auxiliares.
- **Clasificador de tune** (lines 323-353): tier (dedicated/fallback/disabled) por (count, profit_factor) — usado por el script `apply_tune_to_config.py`.
- **Volatilidad diagnóstica** (lines 87-109): Yang-Zhang annualized vol — utilidad diagnóstica, no cableada en sizing.
- **HTTP infra** (lines 481-513): proxy loader, rate limiter, locks compartidos.
- **CLI** (lines 1322-1485, 429-466): formatter de salida humana, append a log, loop principal `main()`, fallback CoinGecko `get_top_symbols`.
- **`scan()`** (lines 867-1315, 448 LOC): orquestación — fetch → evaluate_signal (puro, en strategy/core) → adapt to legacy `rep` dict.

### 1.2 Problema que este plan resuelve

1. **Legibilidad** — un archivo de 1485 LOC mezcla 7 concerns. Encontrar dónde vive `_compute_funding_score` vs `check_trigger_5m_short` requiere `grep`.
2. **Editabilidad agéntica** — Claude/agentes producen edits más confiables sobre archivos < 500 LOC; este monolito ya causó diffs ruidosos en epics anteriores.
3. **Testabilidad** — los tests existentes (60+ archivos importan de `btc_scanner`) están bien, pero los nuevos tests por feature carecen de un home claro. Tras el refactor cada concern tiene un módulo + tests propios.
4. **Inferencia humana del flujo de datos** — hoy hay que saber que el detector de régimen está mezclado con `_REGIME_CACHE_FILE` constants y `_load/_save` helpers en líneas no contiguas (87-235 + 675-850). Tras el refactor, `strategy/regime.py` es la única fuente.

### 1.3 Restricción: no romper producción

`btc_scanner.py` se ejecuta como entrypoint (`python btc_scanner.py`), tiene 60+ call sites de tests y scripts importando funciones específicas, y `scan()` es invocado por `scanner/runtime.py`. Cualquier refactor debe:

- Mantener `python btc_scanner.py [--once] [SYMBOL]` como entrypoint funcional (no romper Docker, scripts Windows, watchdog).
- Pasar la suite completa después de cada PR.
- No alterar la salida de `scan(symbol)` (contract con scanner/runtime.py, btc_api.py, frontend).
- Mantener re-exports de todas las funciones/constantes públicamente importadas hasta que el cleanup PR audite y migre callers.

---

## 2. Objetivos y criterios de éxito

### 2.1 Objetivos primarios

1. **`btc_scanner.py` ≈ 510-540 LOC** al final del plan (scan() ~449 LOC + setup ~25 + re-exports ~50 + constants ~15). Reducción ~64% desde los 1485 actuales. El target "<200 LOC" del issue #225 requiere carve-up de `scan()`, diferido a follow-up.
2. **Cada módulo nuevo < 300 LOC** (mayoría < 150).
3. **Cero regresión** — `scan("BTCUSDT")` produce JSON byte-idéntico pre/post; suite completa pasa tras cada PR.
4. **Re-exports preservan identidad de objeto** — `btc_scanner.X is <new_home>.X` para toda función/dict/global movido.

### 2.2 Objetivos secundarios

5. Cada pieza movida tiene un test `is`-identity en `tests/test_<piece>_reexport.py`.
6. Snapshot end-to-end committeado en `tests/_baselines/scan_btcusdt.json` con fixtures determinísticos.
7. Protocolo pre/post por task explícito en el plan de implementación.
8. Issue follow-up creado (post-#225) para evaluar carve-up de `scan()` (extracción de `scanner/report.py` adapter).

### 2.3 Métricas de éxito

- `wc -l btc_scanner.py` ≤ 540 (esperado: ~510-530)
- `wc -l strategy/regime.py strategy/patterns.py strategy/direction.py strategy/tune.py strategy/vol.py infra/http.py cli/scanner_report.py` muestra todos < 300
- `pytest tests/ -v` ≥ baseline tests pasando (~628+)
- `pytest tests/test_scanner_snapshot.py` verde tras cada PR
- `python btc_scanner.py --once BTCUSDT` ejecuta sin errores y escribe a `logs/signals_log.txt`
- `python btc_api.py` arranca y `curl localhost:8000/health` responde 200 (smoke en PRs que tocan scan())

---

## 3. Arquitectura objetivo

### 3.1 Estructura de archivos final

```
trading-spacial/
├── btc_scanner.py                    ~510-540 LOC: scan() (449) + module setup + re-exports
├── btc_report.py                     UNCHANGED (out of scope)
├── strategy/
│   ├── regime.py                     NEW ~280 LOC
│   ├── patterns.py                   NEW ~120 LOC
│   ├── direction.py                  NEW ~80 LOC
│   ├── tune.py                       NEW ~50 LOC
│   ├── vol.py                        NEW ~40 LOC
│   ├── core.py, sizing.py, indicators.py, constants.py, kill_switch_v2*.py  (existing)
├── infra/
│   ├── __init__.py                   NEW (empty)
│   └── http.py                       NEW ~40 LOC
├── cli/
│   ├── __init__.py                   NEW (empty)
│   └── scanner_report.py             NEW ~180 LOC
├── scanner/
│   └── runtime.py                    UNCHANGED in this refactor
└── tests/
    ├── _fixtures/
    │   ├── scanner_frozen.py         NEW: pytest fixture monkeypatching clock+klines+net
    │   ├── btcusdt_5m.csv            NEW: frozen klines 5m
    │   ├── btcusdt_1h.csv            NEW: frozen klines 1h
    │   ├── btcusdt_4h.csv            NEW: frozen klines 4h
    │   ├── btcusdt_1d.csv            NEW: frozen klines 1d (regime path)
    │   └── scanner_frozen_responses.json  NEW: F&G + funding-rate + exchange-info JSON
    ├── _baselines/
    │   ├── scan_btcusdt.json         NEW: snapshot del rep dict
    │   └── README.md                 NEW: cómo regenerar (con cuidado)
    ├── test_scanner_snapshot.py      NEW: snapshot assertion
    ├── test_<piece>_reexport.py      NEW per PR (7 archivos, ~10 LOC c/u)
    └── ... (existentes intactos)
```

### 3.2 Responsabilidad por módulo

| Módulo | Funciones | Constantes/globales |
|---|---|---|
| `strategy/regime.py` | `detect_regime`, `get_cached_regime`, `detect_regime_for_symbol`, `_compute_price_score`, `_compute_fng_score`, `_compute_funding_score`, `_compute_rsi_score`, `_compute_adx_score`, `_regime_cache_key`, `_compute_local_regime`, `_load_regime_cache`, `_save_regime_cache` | `_REGIME_CACHE_FILE/PATH`, `_REGIME_TTL_SEC`, `_regime_cache` (dict global) |
| `strategy/patterns.py` | `detect_bull_engulfing`, `detect_bear_engulfing`, `detect_rsi_divergence`, `check_trigger_5m`, `check_trigger_5m_short`, `score_label` | — |
| `strategy/direction.py` | `resolve_direction_params`, `metrics_inc_direction_disabled` | `ATR_SL_MULT`, `ATR_TP_MULT`, `ATR_BE_MULT` (aliases de `strategy.constants`) |
| `strategy/tune.py` | `_classify_tune_result` | — |
| `strategy/vol.py` | `annualized_vol_yang_zhang` | `TARGET_VOL_ANNUAL`, `VOL_LOOKBACK_DAYS` |
| `infra/http.py` | `_load_proxy`, `_rate_limit` | `_last_api_call`, `_API_MIN_INTERVAL`, `_api_lock` |
| `cli/scanner_report.py` | `fmt`, `save_log`, `main`, `get_top_symbols` | `LOG_FILE`, `SCAN_INTERVAL`, `STABLECOINS`, `REPO_ROOT` |
| `btc_scanner.py` (final) | `scan` | `DEFAULT_SYMBOLS`, `SYMBOL`, `SCRIPT_DIR`, `SL_PCT`, `TP_PCT`, `COOLDOWN_H`, `TRIGGER_*`, `ADX_THRESHOLD` (display-only en `scan()`) |

### 3.3 Reglas de import (anti-drift)

| Origen | Puede importar | NO puede importar |
|---|---|---|
| `strategy/regime.py` | `infra/http.py`, `strategy/indicators.py`, `data/market_data` | `btc_scanner.py`, `api/*`, `db/*`, `scanner/*`, `cli/*` |
| `strategy/patterns.py` | `strategy/constants.py`, `strategy/indicators.py` | otros `strategy/*`, `btc_scanner.py` |
| `strategy/direction.py` | `strategy/constants.py` | otros `strategy/*`, `btc_scanner.py` |
| `strategy/tune.py` | `numpy` | nada del proyecto |
| `strategy/vol.py` | `numpy`, `pandas` | nada del proyecto |
| `infra/http.py` | stdlib + `requests` | nada del proyecto |
| `cli/scanner_report.py` | `btc_scanner` (para `scan`), `strategy/patterns` (para `score_label`), `infra/http` (para `_load_proxy`), `data/market_data` | `api/*`, `db/*` |
| `btc_scanner.py` (final) | re-exports de los anteriores + lo que ya importaba | — |

`tests/test_import_boundaries.py` (que ya existe del refactor #226) se extiende para validar estas reglas.

### 3.4 Decisión: qué se queda en `btc_scanner.py`

| Pieza | Razón de quedarse |
|---|---|
| `scan()` (~450 LOC) | Carve-up explícitamente diferido a follow-up issue (alcance acotado de #225) |
| `DEFAULT_SYMBOLS` | 8+ callers externos (`scripts/`, `health.py`, `auto_tune.py`, `scanner/runtime.py`, etc.) — migración masiva fuera de alcance |
| `SYMBOL = "BTCUSDT"` | Default arg de `scan(symbol=None)` |
| `SCRIPT_DIR` | `scan()` lo usa para resolver `config.json` path |
| `SL_PCT`, `TP_PCT`, `COOLDOWN_H`, `ADX_THRESHOLD`, `TRIGGER_RSI_RECOVERY`, `TRIGGER_BULLISH_CLOSE` | Display-only constants usados en strings de `scan()`'s `excl` dict — mover los aleja de su único punto de uso sin beneficio |
| `log = logging.getLogger("btc_scanner")` | Naming convention; los tests filtran logs por nombre |
| stdout reconfigure (Windows) | Defensivo; ejecuta solo si `python btc_scanner.py` se invoca directamente |

---

## 4. Plan de PRs

### 4.1 Resumen y dependencias

```
PR0 (foundation)
  ├─→ PR1 (strategy/patterns.py)          ─┐
  ├─→ PR2 (strategy/direction.py)          │
  ├─→ PR3 (strategy/tune.py)               │
  ├─→ PR4 (strategy/vol.py)                ├─→ PR8 (cleanup audit)
  └─→ PR5 (infra/http.py)                  │
        ├─→ PR6 (strategy/regime.py)      ─┤
        └─→ PR7 (cli/scanner_report.py) ───┘
```

PR1-PR5 son paralelizables tras PR0. PR6 depende de PR5 (usa `_rate_limit`). PR7 depende de PR1 (usa `score_label`) y PR5 (usa `_load_proxy`). PR8 depende de todos.

### 4.2 PR0 — Foundation

**Alcance:**
- Crear `infra/__init__.py`, `cli/__init__.py` (vacíos).
- Crear fixture `tests/_fixtures/scanner_frozen.py` con monkeypatching de `datetime`, `md.get_klines`, `md.prefetch`, `requests.get`, `observability.record_decision`, `strategy.kill_switch_v2_shadow.emit_shadow_decision`, y aislamiento de `_REGIME_CACHE_FILE` vía `tmp_path`.
- Capturar fixtures determinísticos: 4 CSVs de klines (BTCUSDT 5m/1h/4h/1d) + 1 JSON con respuestas mockeadas de F&G y funding-rate.
- Capturar baseline `tests/_baselines/scan_btcusdt.json` corriendo `scan("BTCUSDT")` con la fixture aplicada.
- Documentar regeneración en `tests/_baselines/README.md` con warning explícito ("don't unless intentional behavior change; coordinate with reviewer").
- Crear `tests/test_scanner_snapshot.py` con un test que carga el baseline y compara `scan("BTCUSDT") == expected`.

**Cobertura:** snapshot test en verde sobre `main` antes de continuar.

### 4.3 PR1 — `strategy/patterns.py`

**Alcance:** Mover 6 funciones (`detect_bull_engulfing`, `detect_bear_engulfing`, `detect_rsi_divergence`, `check_trigger_5m`, `check_trigger_5m_short`, `score_label`). Re-exports en `btc_scanner.py`. `score_label` lee `SCORE_*` de `strategy.constants` (ya existe).

**Tests nuevos:** `tests/test_patterns_reexport.py` con un test `is`-identity por símbolo movido.

### 4.4 PR2 — `strategy/direction.py`

**Alcance:** Mover `resolve_direction_params`, `metrics_inc_direction_disabled`, aliases `ATR_SL_MULT/TP/BE`. Re-exports.

**Tests nuevos:** `tests/test_direction_reexport.py`. `tests/test_symbol_overrides_resolution.py` se actualiza para importar de `strategy.direction` (la migración del primer caller la hace este PR; los demás llegan vía re-export).

### 4.5 PR3 — `strategy/tune.py`

**Alcance:** Mover `_classify_tune_result`. Migrar `scripts/apply_tune_to_config.py` y `tests/test_tier_classification.py` para importar de `strategy.tune`. Re-export de `btc_scanner._classify_tune_result` mantenido.

**Tests nuevos:** `tests/test_tune_reexport.py`.

### 4.6 PR4 — `strategy/vol.py`

**Alcance:** Mover `annualized_vol_yang_zhang`, `TARGET_VOL_ANNUAL`, `VOL_LOOKBACK_DAYS`. Migrar `tests/test_vol_calc.py`. Re-exports mantenidos.

**Tests nuevos:** `tests/test_vol_reexport.py`.

### 4.7 PR5 — `infra/http.py`

**Alcance:** Mover `_load_proxy`, `_rate_limit`, `_last_api_call`, `_API_MIN_INTERVAL`, `_api_lock`. Re-exports en `btc_scanner.py`. `_load_proxy` resuelve `config.json` vía `REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` (mismo patrón que `cli/scanner_report.py` en PR7) — el path final apunta a `<repo>/config.json`, idéntico al actual.

**Tests nuevos:** `tests/test_http_reexport.py` + un test mínimo de `_rate_limit` (intervalo mínimo respetado).

**Riesgo:** monkeypatching del lock global desde tests existentes. Mitigación: los globals `_last_api_call`, `_api_lock` se re-exportan con identidad preservada.

### 4.8 PR6 — `strategy/regime.py`

**Alcance (el PR más grande):**
- Mover `detect_regime`, `get_cached_regime`, `detect_regime_for_symbol`, los 5 `_compute_*_score` helpers, `_compute_local_regime`, `_regime_cache_key`, `_load_regime_cache`, `_save_regime_cache`.
- Mover constants `_REGIME_CACHE_FILE`, `_REGIME_CACHE_PATH`, `_REGIME_TTL_SEC`.
- Mover el global `_regime_cache` (dict).
- Importar `_rate_limit` de `infra/http`.
- Re-exports completos en `btc_scanner.py` (~14 nombres). Identidad de objeto preservada para `_regime_cache` (mutaciones desde cualquier path visibles).
- Actualizar `tests/_fixtures/scanner_frozen.py` para monkeypatch en `strategy.regime._REGIME_CACHE_FILE` (no en `btc_scanner._REGIME_CACHE_FILE`) — cambio crítico.

**Tests nuevos:** `tests/test_regime_reexport.py` cubriendo los 14 nombres movidos. `tests/test_regime_per_symbol.py` se mantiene tal cual (importa via `btc_scanner` re-export).

**Stop condition específico:** si PR6 requiere >1 día post-merge debugging, parar antes de PR7.

### 4.9 PR7 — `cli/scanner_report.py`

**Alcance:**
- Mover `fmt`, `save_log`, `main`, `get_top_symbols`.
- Mover `LOG_FILE`, `SCAN_INTERVAL`, `STABLECOINS`.
- `LOG_FILE` se computa como `os.path.join(REPO_ROOT, "logs", "signals_log.txt")` donde `REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`. **El path string final es idéntico al actual** — preservación de continuidad de log.
- `btc_scanner.py:__main__` se reescribe a:
  ```python
  if __name__ == "__main__":
      from cli.scanner_report import main
      main()
  ```
- Importa `score_label` de `strategy.patterns` (PR1) y `_load_proxy` de `infra.http` (PR5).

**Tests nuevos:** `tests/test_cli_reexport.py` para los nombres re-exportados desde `btc_scanner` (legacy callers como `tests/test_scanner.py` que importan `LOG_FILE` o `SCAN_INTERVAL`).

**Smoke manual obligatorio:**
```bash
python btc_scanner.py --once BTCUSDT
ls -la logs/signals_log.txt   # confirmar que se escribió al path esperado
```

### 4.10 PR8 — Cleanup audit

**Alcance:** Mirror del rol de PR #227.
- Auditar todos los re-exports de `btc_scanner.py`. Para cada uno, decidir:
  - **Conservar** si tiene callers externos no migrados (la mayoría — DEFAULT_SYMBOLS, calc_*, scan).
  - **Eliminar** si los callers ya migraron (e.g., `_classify_tune_result` si los únicos callers son `apply_tune_to_config.py` migrado en PR3 + tests migrados).
- Validar `wc -l btc_scanner.py` ≤ 540 (esperado ~510-530).
- Validar snapshot + suite completa.
- Smoke del frontend (boot + render de tabs) si es razonable hacerlo.

---

## 5. Mapa de movimientos pieza-a-pieza

| Origen (`btc_scanner.py`) | Líneas | Destino | Notas |
|---|---|---|---|
| Imports + module setup | 18-54 | stays | imports actualizados a re-exportar de los nuevos homes |
| `STABLECOINS` | 61-64 | `cli/scanner_report.py` | usado solo por `get_top_symbols` (CLI) |
| `DEFAULT_SYMBOLS` | 66-69 | **stays** | 8+ callers externos |
| `SYMBOL` | 58 | **stays** | default arg de `scan()` |
| `SCRIPT_DIR` | 71 | **stays** | `scan()` lee `config.json` desde aquí; cada módulo nuevo computa el suyo si lo necesita |
| `LOG_FILE` + `os.makedirs(logs/)` | 72-73 | `cli/scanner_report.py` | solo `save_log` lo escribe |
| `SCAN_INTERVAL = 300` | 75 | `cli/scanner_report.py` | solo el sleep loop de `main()` lo usa |
| `TARGET_VOL_ANNUAL`, `VOL_LOOKBACK_DAYS` | 83-84 | `strategy/vol.py` | con `annualized_vol_yang_zhang` |
| `annualized_vol_yang_zhang` | 87-109 | `strategy/vol.py` | tests existentes: `test_vol_calc.py` |
| `_compute_price_score` | 112-146 | `strategy/regime.py` | regime score component |
| `_compute_fng_score` | 149-151 | `strategy/regime.py` | regime score component |
| `_compute_funding_score` | 154-157 | `strategy/regime.py` | regime score component |
| `_compute_rsi_score` | 160-164 | `strategy/regime.py` | regime score component |
| `_compute_adx_score` | 167-175 | `strategy/regime.py` | regime score component |
| `_regime_cache_key` | 178-182 | `strategy/regime.py` | composite cache key helper |
| `_compute_local_regime` | 185-235 | `strategy/regime.py` | per-symbol scorer |
| `detect_regime_for_symbol` | 238-320 | `strategy/regime.py` | per-symbol entry point |
| `_classify_tune_result` | 323-353 | `strategy/tune.py` | `scripts/apply_tune_to_config.py` migrado |
| `resolve_direction_params` | 356-407 | `strategy/direction.py` | + ATR aliases (líneas 42-44) |
| `SL_PCT`, `TP_PCT`, `COOLDOWN_H` | 411-413 | **stays** | display-only en `scan()`'s exclusions strings |
| `TRIGGER_RSI_RECOVERY`, `TRIGGER_BULLISH_CLOSE` | 418-419 | **stays** | informational module-level constants |
| `ADX_THRESHOLD` | 422 | **stays** | display en `scan()` exclusion E7 |
| `get_top_symbols` | 429-466 | `cli/scanner_report.py` | CLI-only; usa `_load_proxy` de `infra/http` |
| `_load_proxy` | 481-497 | `infra/http.py` | computa su propio `SCRIPT_DIR` |
| `_last_api_call`, `_API_MIN_INTERVAL`, `_api_lock` | 500-502 | `infra/http.py` | globals para `_rate_limit` |
| `_rate_limit` | 505-513 | `infra/http.py` | usado por `detect_regime` (regime), `get_top_symbols` (cli) |
| `detect_bull_engulfing` | 523-534 | `strategy/patterns.py` | |
| `detect_bear_engulfing` | 537-548 | `strategy/patterns.py` | |
| `detect_rsi_divergence` | 554-590 | `strategy/patterns.py` | |
| `score_label` | 593-601 | `strategy/patterns.py` | lee `SCORE_*` de `strategy.constants` |
| `check_trigger_5m` | 608-638 | `strategy/patterns.py` | |
| `check_trigger_5m_short` | 641-668 | `strategy/patterns.py` | |
| `_REGIME_CACHE_FILE/PATH`, `_REGIME_TTL_SEC` | 675-677 | `strategy/regime.py` | regime cache constants |
| `_load_regime_cache` | 680-698 | `strategy/regime.py` | con soft-migration legacy unwrap |
| `_save_regime_cache` | 701-708 | `strategy/regime.py` | |
| `_regime_cache` global | 711 | `strategy/regime.py` | dict module-level |
| `detect_regime` | 714-834 | `strategy/regime.py` | usa `_rate_limit` de `infra/http` |
| `get_cached_regime` | 837-850 | `strategy/regime.py` | |
| `metrics_inc_direction_disabled` | 857-864 | `strategy/direction.py` | |
| **`scan` function** | 867-1315 | **stays** | unchanged este refactor (carve-up = follow-up) |
| `fmt` | 1322-1406 | `cli/scanner_report.py` | |
| `save_log` | 1413-1436 | `cli/scanner_report.py` | |
| `main` | 1443-1485 | `cli/scanner_report.py` | |
| `if __name__ == "__main__":` | 1484-1485 | rewritten | `from cli.scanner_report import main; main()` |

---

## 6. Estrategia de testing y paridad

### 6.1 Tres capas de protección

**a) Snapshot end-to-end** — `tests/_baselines/scan_btcusdt.json`. Capturado en PR0, asserted byte-equal tras cada PR posterior. Cualquier drift es investigation, nunca regeneration silenciosa.

**b) Tests `is`-identity por pieza movida** — un archivo `tests/test_<piece>_reexport.py` por PR. ~10 LOC c/u, cubre todos los nombres movidos asegurando `btc_scanner.X is <new_home>.X`.

**c) Suite completa** — `pytest tests/ -v` debe pasar antes y después de cada PR. Hoy 628+ tests; cada PR puede agregar 5-10 nuevos pero **nunca disminuir el total ni romper existentes**.

### 6.2 Fixture `tests/_fixtures/scanner_frozen.py`

```python
import json
from datetime import datetime, timezone
from pathlib import Path
import pytest
import requests
import pandas as pd
from data import market_data as md

_FIXTURE_DIR = Path(__file__).parent
_RESPONSES = json.load(open(_FIXTURE_DIR / "scanner_frozen_responses.json"))


def _frozen_get_klines(symbol, interval, limit=None, **kw):
    csv_path = _FIXTURE_DIR / f"{symbol.lower()}_{interval}.csv"
    return pd.read_csv(csv_path, parse_dates=["ts"]) if csv_path.exists() else pd.DataFrame()


def _frozen_requests_get(url, **kw):
    class _Resp:
        def __init__(self, payload):
            self._payload = payload; self.ok = True
        def json(self): return self._payload
        def raise_for_status(self): pass
    if "fng" in url:        return _Resp(_RESPONSES["fng"])
    if "fundingRate" in url: return _Resp(_RESPONSES["funding"])
    if "exchangeInfo" in url: return _Resp(_RESPONSES["exchangeInfo"])
    raise RuntimeError(f"unexpected URL in frozen test: {url}")


@pytest.fixture
def frozen_scan(monkeypatch, tmp_path):
    fixed_now = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None): return fixed_now if tz else fixed_now.replace(tzinfo=None)

    # PR0: monkeypatch en btc_scanner.*. Se actualiza a strategy.regime.* en PR6.
    monkeypatch.setattr("btc_scanner.datetime", _FrozenDatetime)
    monkeypatch.setattr(md, "get_klines", _frozen_get_klines)
    monkeypatch.setattr(md, "prefetch", lambda *a, **kw: None)
    monkeypatch.setattr("btc_scanner._REGIME_CACHE_FILE", str(tmp_path / "regime.json"))
    monkeypatch.setattr("btc_scanner._REGIME_CACHE_PATH", str(tmp_path / "regime.json"))
    monkeypatch.setattr("btc_scanner._regime_cache", {})
    monkeypatch.setattr(requests, "get", _frozen_requests_get)
    monkeypatch.setattr("observability.record_decision", lambda **kw: None)
    monkeypatch.setattr(
        "strategy.kill_switch_v2_shadow.emit_shadow_decision", lambda **kw: None)
    yield
```

### 6.3 Test de snapshot

```python
# tests/test_scanner_snapshot.py
import json
from pathlib import Path
from btc_scanner import scan
from tests._fixtures.scanner_frozen import frozen_scan  # noqa: F401

_BASELINE = Path(__file__).parent / "_baselines" / "scan_btcusdt.json"


def test_scan_btcusdt_snapshot_unchanged(frozen_scan):
    rep = scan("BTCUSDT")
    expected = json.loads(_BASELINE.read_text())
    assert rep == expected
```

### 6.4 Patrón `is`-identity por PR

```python
# tests/test_patterns_reexport.py — añadido en PR1
def test_patterns_reexport_identity():
    import btc_scanner
    from strategy import patterns
    assert btc_scanner.detect_bull_engulfing is patterns.detect_bull_engulfing
    assert btc_scanner.detect_bear_engulfing is patterns.detect_bear_engulfing
    assert btc_scanner.detect_rsi_divergence is patterns.detect_rsi_divergence
    assert btc_scanner.check_trigger_5m is patterns.check_trigger_5m
    assert btc_scanner.check_trigger_5m_short is patterns.check_trigger_5m_short
    assert btc_scanner.score_label is patterns.score_label
```

Mismo shape para regime, direction, tune, vol, http, cli.

### 6.5 Lo que NO se testea

- Performance/latencia (refactor estructural).
- Reachability de red (mocks).
- Threading de `scanner_loop` (out of scope — `scanner/runtime.py`).
- Telegram delivery real (ya mockeado en `test_api_telegram_unit.py`).
- Frontend visual (smoke manual opcional en PR8).

---

## 7. Protocolo de verificación pre/post por PR

**Cada PR debe correr este protocolo. Sin verification log en la PR description, no merge.**

### 7.1 Pre-task gate (antes de empezar el trabajo)

```bash
git checkout main && git pull
pytest tests/ -v                               # full suite — green on main
pytest tests/test_scanner_snapshot.py -v      # snapshot — green on main
git rev-parse HEAD                             # record baseline commit
```

Si algún pre-check falla en `main`, **stop**. No empezar work sobre baseline rota.

Revisar el risk register §8 e identificar qué filas se activan con este PR. Listar en la PR description bajo "Risks-touched".

### 7.2 During-task discipline

- Una pieza conceptual por PR. Sin cleanups incidentales.
- Tras cada commit local: `pytest tests/test_scanner_snapshot.py -v`.
- Re-exports añadidos antes de finalizar el move: escribir `from <new_home> import X` en `btc_scanner.py` *primero*, correr snapshot, *luego* eliminar la definición original.

### 7.3 Post-task gate (antes de pedir review / merge)

```bash
# 1. Snapshot — byte-for-byte
pytest tests/test_scanner_snapshot.py -v

# 2. Per-piece identity test
pytest tests/test_<piece>_reexport.py -v

# 3. Full suite
pytest tests/ -v

# 4. CLI smoke (PRs que tocan CLI/scan)
python btc_scanner.py --once BTCUSDT 2>&1 | tee /tmp/scanner_smoke.log
grep -E "(ERROR|Traceback)" /tmp/scanner_smoke.log && echo "FAIL" || echo "OK"

# 5. API boot smoke (PRs que potencialmente afectan scan() callers)
python btc_api.py &
sleep 5
curl -s http://localhost:8000/health | jq .status   # expect "ok"
kill %1

# 6. Re-export sanity por nombre movido
python -c "import btc_scanner; assert btc_scanner.<name1> is not None"

# 7. LOC progress
wc -l btc_scanner.py strategy/<new>.py infra/<new>.py cli/<new>.py
```

PR description incluye una sección "Verification log" con el output pegado de los pasos 1-7.

### 7.4 Risks-touched annotation

Cada PR description tiene este bloque, populated con `[x]` o `N/A`:

```markdown
## Risks-touched (from spec §8)
- [x] Re-export omission                  — mitigado por step 6
- [x] Module-global identity drift        — mitigado por identity test (step 2)
- [ ] Monkeypatch namespace               — N/A este PR (no fixture changes)
- [x] Snapshot regen sin review           — mitigado por step 1
- [ ] Kill switch v2 calibrator           — N/A salvo PR6
- [ ] CLI behavior drift                  — N/A salvo PR7
...
```

### 7.5 Stop conditions

- Dos PRs consecutivos requieren >1 día post-merge debugging → pause + reassess antes del siguiente.
- Snapshot drift no investigable → escalate to user; never silent regen.

---

## 8. Riesgos y rollback

### 8.1 Risk register

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| **Re-export omission** rompe caller silenciosamente | Media | Alto | Identity tests por PR (§6.4); suite completa pre-merge; 60+ existing import sites como cobertura colateral |
| **Module-global identity drift** — `_regime_cache` mutaciones invisibles entre namespaces | Alta si naïve | Alto | `from strategy.regime import _regime_cache` en btc_scanner.py rebinds al mismo dict object. Identity test valida `btc_scanner._regime_cache is strategy.regime._regime_cache` |
| **Monkeypatch via re-export rebinds wrong namespace** | Alta | Alto | Fixture `tests/_fixtures/scanner_frozen.py` parchea el módulo **home** de cada función. Cada PR de move incluye un line-item de update de fixture |
| **`scripts/apply_tune_to_config.py` cron breaks** durante PR3 | Baja | Medio | PR3 mantiene re-export `btc_scanner._classify_tune_result` Y migra el script en el mismo commit atómico |
| **Kill switch v2 calibrator** importa `get_cached_regime` durante PR6 | Media | Alto | Re-export preservado a través de PR8; calibrator (`strategy/kill_switch_v2_calibrator.py:537`) intacto. Migración al import directo opcional en PR6 (cleaner pero más diff) |
| **`python btc_scanner.py` CLI behavior drift** — log file path cambia | Media | Medio | `cli/scanner_report.py` computa `LOG_FILE = os.path.join(REPO_ROOT, "logs", "signals_log.txt")` con `REPO_ROOT = dirname(dirname(__file__))`. Path string idéntico al actual. Smoke step 4 valida |
| **Windows scripts** referencian `btc_scanner.py` | Baja | Bajo | `btc_scanner.py:__main__` delega a `cli.scanner_report.main()`. Entrypoint name unchanged. Smoke en Windows opcional |
| **Snapshot regeneration sin review** masks regression real | Media | Alto | `tests/_baselines/README.md` warning explícito. PR description debe explicar cualquier regeneration intencional |
| **Refactor se estira por semanas** mientras kill switch v2 / otros features tocan btc_scanner.py | Media | Medio | Plan estima ~1-2 semanas seriatim. Periodic `git rebase` por PR. Coordinar via issue thread |
| **PR6 (regime) es el más grande** y concentra riesgo | Media | Alto | Stop condition: si PR6 toma >1 día post-merge debugging, pause antes de PR7. Identity tests cubren 14 nombres |

### 8.2 Estrategia de rollback

- **Per PR.** Cada PR es revertable con `git revert <sha>` porque mantiene re-exports. Worst case: revert deja un archivo huérfano en `strategy/`/`cli/`/`infra/` sin caller — safe.
- **Per phase.** Si PR1-PR5 revelan que el patrón de re-export es brittle, revert en orden inverso. PR0 se queda (baselines + scaffolding vacío).
- **Stop condition.** Dos PRs consecutivos con >1 día post-merge debugging → pause + reassess.

### 8.3 Out-of-band hazards

- Kill switch v2 calibrator corre 00:00 UTC daily. PR6 debería landearse fuera de la ventana de calibración o como mínimo tras un `pytest tests/test_strategy_kill_switch_v2_calibrator.py` clean run.
- Merge inmediatamente antes de market hours requiere monitoring de Simon — `python btc_api.py` boot smoke + tail de `logs/signals_log.txt` por un ciclo.

---

## 9. Definition of done

- [ ] `wc -l btc_scanner.py` ≤ 540
- [ ] 5 módulos nuevos en `strategy/` (`regime`, `patterns`, `direction`, `tune`, `vol`); 1 en `infra/` (`http`); 1 en `cli/` (`scanner_report`)
- [ ] `tests/test_scanner_snapshot.py` verde (snapshot byte-equal pre/post)
- [ ] 7 archivos `tests/test_<piece>_reexport.py` añadidos, todos verdes
- [ ] `pytest tests/ -v` ≥ baseline tests pasando
- [ ] `python btc_scanner.py --once BTCUSDT` ejecuta sin errores y escribe a `logs/signals_log.txt`
- [ ] `python btc_api.py` arranca y `curl localhost:8000/health` responde 200
- [ ] `tests/test_import_boundaries.py` extendido con reglas para `strategy/regime`, `strategy/patterns`, etc.
- [ ] PR8 cleanup: re-exports auditados; los que pueden eliminarse, eliminados; los que persisten, documentados con un comentario `# noqa: backward-compat — N callers`.
- [ ] Issue follow-up creado para evaluar carve-up de `scan()` (extracción `scanner/report.py` adapter).

---

## 10. Fuera de alcance

### 10.1 Carve-up de `scan()` (~448 LOC)

`scan()` mezcla I/O glue (~150 LOC: config load, health state, observability emit, regime fetch, shadow mode, error reporting) con report-shape derivation (~300 LOC: engulfing recompute, LONG/SHORT score branches, exclusions dict, sizing dict, blocks_long/short, estado branches, clean_dict). Una extracción potencial:

```
scanner/report.py — build_report(decision, df1h, df5, df4h, _cfg, _so, regime_data, _health_state) → rep_dict
```

Esta extracción reduce `btc_scanner.py` a ~80-100 LOC pero amplía la superficie de paridad y agrega un call boundary. Decisión: diferida a follow-up issue post-#225 que evalúa si vale la pena ahora que las piezas más obvias están extraídas.

### 10.2 Refactor de `scanner/runtime.py`

Funciones `_get_binance_usdt_symbols` y `get_active_symbols` viven aquí desde PR #226. El issue #225 sugería moverlas a `markets/symbols.py` para consolidar la lógica de símbolos. Decisión: dejarlas en `scanner/runtime.py`. `get_top_symbols` (CLI-only, fallback CoinGecko) va a `cli/scanner_report.py` junto a `main()`. No se crea `markets/`. Si en el futuro se justifica, las dos runtime ones pueden migrar como follow-up.

### 10.3 Refactor de `btc_report.py`

`btc_report.py` (783 LOC) es un generador HTML standalone, distinto en propósito al CLI scanner. No se toca este refactor. Si en el futuro se justifica un `cli/` package unificado, es trabajo separado.

### 10.4 Migraciones de callers

Los 60+ `from btc_scanner import …` call sites se mantienen vía re-exports. La migración masiva a importar directamente de `strategy/regime`, `strategy/patterns`, etc. queda como cleanup opcional post-PR8, no parte de #225.

---

## 11. Próximos pasos

1. Review de este spec por Samuel.
2. Si aprobado: invocar skill `superpowers:writing-plans` para generar plan de implementación detallado por PR (PR0-PR8) con tasks pre/post-verify por step.
3. Ejecutar PR0 con `superpowers:executing-plans` (o `subagent-driven-development` para paralelizar PR1-PR5 tras PR0).
4. Tras PR8: crear issue follow-up para evaluar `scanner/report.py` adapter (carve-up de `scan()`).
