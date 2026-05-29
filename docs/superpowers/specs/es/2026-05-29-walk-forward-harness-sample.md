# Walk-forward harness — sample report (demo shape, sin números reales)

**Fecha**: 2026-05-29
**Issue**: #276 — Walk-forward harness (A.4-2)
**Tipo**: Demo de la forma del reporte. **Sin métricas reales.**

---

## ⚠️ AVISO PROMINENTE — LEER ANTES DE CITAR

Este documento es un **ejemplo de forma**, no un resultado de evaluación.

- Cada celda numérica aparece como `n/a` deliberadamente.
- **Ningún número de este archivo proviene de una corrida del harness.** No fue
  ejecutado contra `data/ohlcv.db`, no consumió el snapshot bloqueado
  `data/holdout/`, y no produjo decisión alguna sobre parámetros, símbolos o
  régimen.
- El propósito es congelar la **forma** del JSON que emite
  `walk_forward.py --execute` y el **layout** del reporte humano que se
  publicará cuando el harness se corra de verdad. Forma, no contenido.

**Non-Negotiable #5** (CLAUDE.md): los números de backtest pre-#223/#224 están
inflados; **no citarlos como baseline**. El re-baselining se trabaja en
[#272](https://github.com/sssimon/trading-spacial/issues/272). Hasta que #272
cierre, cualquier número que aparezca en un reporte de walk-forward es
provisional y debe llevar el aviso explícito de "post-#272 re-baseline pending"
o equivalente.

**Non-Negotiable #3** (CLAUDE.md): A.4-3 (evaluación sobre holdout) está
**bloqueada**. Este documento es la antesala de A.4-2 (walk-forward
cross-validation). Ninguna línea de este reporte autoriza, sugiere ni adelanta
la lectura de `data/holdout/`. La bala única muere también con vistazos
parciales.

---

## 1. Inputs (forma)

| Campo | Valor demo |
|---|---|
| `history_start` | `n/a` |
| `history_end` | `n/a` |
| `holdout_start` | `n/a` |
| `initial_train_months` | `n/a` |
| `test_months` | `n/a` |
| `step_months` | `n/a` |
| `warmup_gap_days` | `n/a` |
| `ci_mode` | `n/a` |
| `app_config source` | `n/a` (`load_config()` o `--config-path <file>`) |
| `symbols evaluados` | `n/a` |

---

## 2. Windows computados

`compute_windows(...)` produce una lista ordenada de `Window` dataclasses. En un
reporte real, cada fila refleja un fold; aquí todas las celdas son `n/a`.

| index | train_start | train_end | test_start | test_end | warmup_gap_days |
|---|---|---|---|---|---|
| 0 | `n/a` | `n/a` | `n/a` | `n/a` | `n/a` |
| 1 | `n/a` | `n/a` | `n/a` | `n/a` | `n/a` |
| … | `n/a` | `n/a` | `n/a` | `n/a` | `n/a` |

Invariantes garantizadas por `compute_windows` (cubiertas por
`tests/test_walk_forward_windows.py`):

- **Anclado**: cada `train_start == history_start` (modo `anchored=True`,
  único soportado en este commit).
- **Sin solape**: los rangos de test de folds consecutivos son disjuntos.
- **Exclusión de holdout**: `test_end <= holdout_start` para todo fold.
- **Warmup gap**: `test_start - train_end >= warmup_gap_days`.

---

## 3. Per-window reports (forma de `evaluate_window`)

Cada fold emite un report con esta forma (`tests/test_walk_forward_eval.py`
fija el contrato):

```json
{
  "window_index": "n/a",
  "train_range": {"start": "n/a", "end": "n/a"},
  "test_range":  {"start": "n/a", "end": "n/a"},
  "params": {
    "<symbol>": {
      "atr_sl_mult": "n/a",
      "atr_tp_mult": "n/a",
      "atr_be_mult": "n/a"
    }
  },
  "results": {
    "<symbol>": {
      "n_trades": "n/a",
      "metrics": {
        "total_trades":     "n/a",
        "net_pnl":          "n/a",
        "profit_factor":    "n/a",
        "sharpe_ratio":     "n/a",
        "max_drawdown_pct": "n/a",
        "win_rate":         "n/a",
        "total_return_pct": "n/a"
      },
      "regime_tag": null,
      "error":      null
    }
  },
  "skipped": [
    {"symbol": "n/a", "reason": "no_usable_params"}
  ],
  "is_pnl_by_symbol": {
    "<symbol>": "n/a"
  }
}
```

Notas de forma (no son números, son contratos):

- `regime_tag` es siempre `null` en este commit: el simulador clasifica
  régimen por-trade, no por-run. Un fold-level regime tag requiere una decisión
  agregada (modo? mayoría?) que el harness aún no fija — surfacearla ahora sería
  inventar semántica.
- `error` se popula sólo cuando el runner retorna su sentinel
  (`{"error": ..., "total_trades": 0, "net_pnl": 0, "profit_factor": 0}`) — por
  ejemplo cuando `data/ohlcv.db` no cubre el rango.
- `is_pnl_by_symbol` lo añade el orquestador (no `evaluate_window`) y carga el
  `val_pnl` del lado IS de cada símbolo para que el agregador calcule el ratio
  OOS/IS sin acoplar `evaluate_window` al dict de tuning.

---

## 4. Aggregate summary (forma de `aggregate_run_stats`)

El JSON que `walk_forward.py --execute` imprime en stdout (después del marker
`=== walk-forward summary (JSON) ===`) tiene esta forma:

```json
{
  "n_windows":              "n/a",
  "n_windows_with_trades":  "n/a",
  "n_windows_skipped":      "n/a",
  "n_windows_errored":      "n/a",
  "total_trades":           "n/a",
  "oos_is_ratio": {
    "value":  "n/a",
    "n":      "n/a",
    "metric": "net_pnl",
    "reason": "n/a"
  },
  "cv": {
    "net_pnl":          {"value": "n/a", "n": "n/a"},
    "profit_factor":    {"value": "n/a", "n": "n/a"},
    "sharpe_ratio":     {"value": "n/a", "n": "n/a"},
    "max_drawdown_pct": {"value": "n/a", "n": "n/a"},
    "win_rate":         {"value": "n/a", "n": "n/a"},
    "total_return_pct": {"value": "n/a", "n": "n/a"}
  },
  "best_window":  {"index": "n/a", "metric": "sharpe_ratio", "value": "n/a"},
  "worst_window": {"index": "n/a", "metric": "sharpe_ratio", "value": "n/a"}
}
```

### Decisiones de agregación (defendidas en `walk_forward.py`):

- **OOS metric**: `net_pnl` de `evaluate_window` por (window, símbolo), proyectado
  a través de `_REPORT_METRIC_KEYS`.
- **IS metric (load-bearing)**: el `val_pnl` que `auto_tune.optimize_symbol`
  expone del lado validate — `proposal_detail.val_pnl` cuando la recomendación
  es `CHANGE`, `current_val_pnl` en `KEEP / KEEP_CURRENT / NO_DATA / ERROR`. **No
  hay IS Sharpe**: el optimizer no la expone; manufacturar una requeriría
  re-correr el simulador sobre la slice de train (scope deferido).
- **CV (coefficient of variation)**: `std/|mean|` por métrica a través de los
  windows. Población (`N`), no muestra (`N-1`) — los folds son la población,
  no una muestra. `|mean| < 1e-9` retorna `value: null` con
  `reason: "mean_near_zero"`.
- **Best/worst window**: ranking por `sharpe_ratio` (cuando hay valores
  numéricos), con fallback a `total_return_pct`. La métrica usada se reporta
  en el campo `metric` del bloque.

### Razones por las que `value` puede ser `null`:

| Campo | Reason | Significado |
|---|---|---|
| `cv.*.value` | `no_samples` | Ningún (window, símbolo) produjo valor numérico para esa métrica. |
| `cv.*.value` | `single_sample` | Sólo un dato — CV no está definido con un único punto. |
| `cv.*.value` | `mean_near_zero` | `|mean| < 1e-9` — el ratio sería un disparate inflado. |
| `oos_is_ratio.value` | `is_pnl_not_on_report` | El run no llevó `is_pnl_by_symbol` (e.g. tests con hand-rolled reports). |
| `oos_is_ratio.value` | `no_paired_windows` | Bloque IS presente pero sin pares (IS, OOS) numéricos. |
| `oos_is_ratio.value` | `is_pnl_near_zero` | `|is_total| < 1e-9` — degrada en vez de fabricar un ratio. |
| `best_window` / `worst_window` | `null` (todo el bloque) | Ningún window produjo Sharpe ni `total_return_pct` numéricos. |

---

## 5. Cómo correr el harness (referencia operativa)

**Modo dry-run** (sólo computar windows, no ejecutar):

```bash
python walk_forward.py \
    --history-start 2023-01-01 \
    --history-end   2025-12-31 \
    --holdout-start 2026-01-01 \
    --initial-train-months 12 \
    --test-months 3 \
    --step-months 3 \
    --dry-run
```

**Modo execute** (drive `run_walk_forward` + `aggregate_run_stats`, imprimir
JSON):

```bash
python walk_forward.py \
    --history-start 2023-01-01 \
    --history-end   2025-12-31 \
    --holdout-start 2026-01-01 \
    --initial-train-months 12 \
    --test-months 3 \
    --step-months 3 \
    --execute
```

Sin `--ci-mode`, esto invoca `auto_tune.optimize_symbol` real por (window,
símbolo) — **minutos por símbolo** sobre el grid completo. Para CI smoke
existe el escape hatch:

```bash
python walk_forward.py \
    --history-start 2023-01-01 \
    --history-end   2023-02-01 \
    --holdout-start 2026-01-01 \
    --initial-train-months 12 \
    --test-months 3 \
    --step-months 3 \
    --execute \
    --ci-mode \
    --config-path tests/fixtures/walk_forward_ci_config.json
```

`--ci-mode` reemplaza el tuning por `frozen_params_for_window` — usa los ATR
multipliers ya presentes en `symbol_overrides`. `--config-path` permite drive
un fixture sin pasar por la cadena de merge de `api.config.load_config()`.

---

## ⚠️ AVISO PROMINENTE — REPETIDO AL CIERRE

Repito para que ningún lector apurado se confunda:

- **Ninguno de los `n/a` de este reporte representa una métrica real.**
- **Ninguna decisión de producto, parámetros ni régimen se basa en este
  documento.**
- Las celdas `n/a` existen para **congelar la forma** del reporte que se
  publicará cuando `walk_forward.py --execute` se corra de verdad en una
  ventana post-#272 re-baselineada.
- Citar este archivo como evidencia de cualquier hallazgo (positivo o
  negativo) sobre el sistema es una violación de **Non-Negotiable #5**.
- No tocar `data/holdout/` está cubierto por **Non-Negotiable #3** — este
  reporte explícitamente no autoriza ningún peek.

---

**Versión de la forma**: corresponde al estado del módulo `walk_forward.py` en
el commit que introduce `--execute` (commit 7 de #276). Cuando el shape del
JSON cambie (nuevas métricas, nuevos campos en `oos_is_ratio.reason`, regime
tag agregado por fold), regenerar este sample en una versión nueva y dejar
ésta como histórica.
