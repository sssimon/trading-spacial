# Probe valle-calidad — Falsificador: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir el falsificador determinista del probe valle-calidad — tratamiento (LONG el valle, hold 20d) vs control (LONG no-valle), diferencia de medias net-of-v3w, block-bootstrap por episodio — que emite GO/NO-GO (PASS/FAIL/UNDERPOWERED) sobre el panel de 187.

**Architecture:** Paquete `tools/valle_calidad_probe/` clonando la forma de `tools/celda4_stat_arb/`: `constants` (parámetros congelados del pre-registro) → `episodes` (detección de episodios valle/no-valle vía el screener A ya mergeado) → `simulate` (P&L net-of-v3w por entrada, reusa el costo v3w de la celda 4) → `bootstrap` (diferencia de medias, block por episodio, seed 42) → `run` (orquestación + gates + poder + `findings.md`/`verdict.json` + fingerprint). Tasks 1-5 se construyen y testean con datos SINTÉTICOS (mini-paneles en tmp DB), así que no dependen del panel real; la corrida terminal sobre el panel de 187 es un paso final manual (post-plan).

**Tech Stack:** Python 3.12, SQLite (read-only sobre `data/program_ohlcv.db`), numpy para el bootstrap, pytest.

**Pre-registro (CONGELADO — leer COMPLETO):** `data/retune/2026-06-11-valle-calidad-probe/PREREGISTRO.md`. Ninguna constante de gate/poder/horizonte se mueve tras ver resultados.

**Reglas no negociables:**
- **Cero holdout (#322):** solo `data/program_ohlcv.db`. JAMÁS `data/holdout/`, `open_holdout`, ni `simulate_strategy` con holdout-window. Un test debe verificar que el código no importa esos símbolos.
- **Determinista:** seed fijo 42, UNA corrida. El falsificador no debe tener ningún `Math.random`/`Date.now`/no-seed. Data-dredging prohibido.
- **Reusar, no reinventar:** la definición de valle viene de `screener.valley_filter.measure_consolidation` (pieza A, ya en main). El costo viene de `tools.celda4_stat_arb.costs` (`derive_tier_cutoffs`, `tier_for_volume`, `v3w_fill_cost`). NO se re-implementan.
- Comentarios/docstrings en español.

**Contrato de barras (compartido):** las funciones puras reciben barras diarias como `list[dict]` con claves `{open_time, open, high, low, close, volume, quote_volume}` — el mismo contrato del screener A. El panel guarda `spot_klines` 1h; el orquestador resamplea a 1d antes de llamar a las puras.

---

### Task 0: Regenerar el panel (pre-requisito de datos — MANUAL, no TDD)

**Esto NO es una tarea de código.** Es un paso de datos pesado. Los tasks 1-5 usan datos sintéticos y NO lo necesitan; solo la corrida terminal lo requiere. Se puede correr en background mientras se construye el falsificador.

- [ ] **Step 1: Regenerar spot + perp del panel**

El `program_ohlcv.db` está vacío en esta máquina (regenerable, gitignored). Baja ~4 años de klines 1h de 187 símbolos spot + los 10 curados en perp (necesarios para la derivación de cutoffs v3w). Es I/O de red pesado (puede tardar; correr en background con `run_in_background`):

```bash
python -m tools.program_ingest.run            # spot_klines (panel 187)
python -m tools.program_ingest.run --futures  # perp_klines + perp_funding (10 curados, ref v3w)
```

(Verificar el flag real de futures con `python -m tools.program_ingest.run --help`; si el ingest de futures es un módulo aparte, usar el que exponga `tools/program_ingest/futures.py`.)

- [ ] **Step 2: Verificar que las tablas quedaron pobladas**

Run:
```bash
python -c "import sqlite3; c=sqlite3.connect('data/program_ohlcv.db'); print('spot symbols:', c.execute('SELECT COUNT(DISTINCT symbol) FROM spot_klines').fetchone()[0]); print('perp symbols:', c.execute('SELECT COUNT(DISTINCT symbol) FROM perp_klines').fetchone()[0])"
```
Expected: spot ~187, perp ≥10. Si spot < 150, el ingest quedó incompleto — re-correr antes de la corrida terminal (NO antes de construir el falsificador; los tasks 1-5 no lo tocan).

No hay commit en esta tarea (la DB es gitignored).

---

### Task 1: Scaffold + constantes congeladas

**Files:**
- Create: `tools/valle_calidad_probe/__init__.py` (vacío)
- Create: `tools/valle_calidad_probe/constants.py`
- Test: `tests/test_valle_probe_constants.py`

- [ ] **Step 1: Write the failing test**

Crear `tests/test_valle_probe_constants.py`:

```python
"""Tests de las constantes congeladas del probe valle-calidad.

Pre-registro: data/retune/2026-06-11-valle-calidad-probe/PREREGISTRO.md.
Estos valores NO se mueven tras ver resultados — el test los fija como fósiles."""
from tools.valle_calidad_probe import constants as K


def test_constantes_del_preregistro():
    assert K.STUDY_START == "2021-01-01"
    assert K.STUDY_END == "2025-04-30"      # frontera pre-holdout en tiempo
    assert K.HOLD_DAYS == 20                # H declarado, sin grid
    assert K.NOTIONAL_USD == 1000.0
    assert K.BOOTSTRAP_ITERS == 10000
    assert K.SEED == 42
    assert K.MIN_EPISODES_VALLE == 30       # umbral de poder (> 10 del F&G)
    assert K.REGIME_SPLIT == "2023-03-01"   # punto medio, solo reporte de robustez
    assert K.DB_PATH == "data/program_ohlcv.db"


def test_reusa_definicion_de_valle_del_screener():
    # La ventana y la banda de consolidación NO se redefinen aquí: se importan
    # del screener A (única fuente de verdad de "qué es un valle").
    from screener.valley_filter import CONSOLIDATION_WINDOW_DAYS, RANGE_BAND_MAX
    assert K.CONSOLIDATION_WINDOW_DAYS == CONSOLIDATION_WINDOW_DAYS
    assert K.RANGE_BAND_MAX == RANGE_BAND_MAX
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_valle_probe_constants.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.valle_calidad_probe'`

- [ ] **Step 3: Write the implementation**

Crear `tools/valle_calidad_probe/__init__.py` vacío. Crear `tools/valle_calidad_probe/constants.py`:

```python
"""Constantes CONGELADAS del probe valle-calidad (sondeo pre-celda).

Pre-registro: data/retune/2026-06-11-valle-calidad-probe/PREREGISTRO.md
(criterios congelados 2026-06-11, ANTES de correr). Ningún valor de gate /
poder / horizonte se mueve tras ver resultados.

La definición de "valle" NO vive aquí: se importa del screener A
(screener.valley_filter), única fuente de verdad. Esto solo re-exporta los
dos parámetros que el probe necesita citar, atados por test a su origen."""
from __future__ import annotations

from screener.valley_filter import CONSOLIDATION_WINDOW_DAYS, RANGE_BAND_MAX

# ── Ventana del estudio (pre-holdout en tiempo, igual que celda 4) ──────────
STUDY_START = "2021-01-01"
STUDY_END = "2025-04-30"

# ── Estrategia pre-declarada ────────────────────────────────────────────────
HOLD_DAYS = 20                 # H, declarado; sin grid de horizontes
NOTIONAL_USD = 1000.0          # notional fijo → pooling $-weighted

# ── Bootstrap (block por episodio) ──────────────────────────────────────────
BOOTSTRAP_ITERS = 10000
SEED = 42

# ── Poder ───────────────────────────────────────────────────────────────────
MIN_EPISODES_VALLE = 30        # < esto ⟹ UNDERPOWERED / INCONCLUSO

# ── Robustez (reporte, NO gate) ─────────────────────────────────────────────
REGIME_SPLIT = "2023-03-01"    # punto medio temporal; parte las dos mitades

# ── Datos ───────────────────────────────────────────────────────────────────
DB_PATH = "data/program_ohlcv.db"

__all__ = [
    "STUDY_START", "STUDY_END", "HOLD_DAYS", "NOTIONAL_USD",
    "BOOTSTRAP_ITERS", "SEED", "MIN_EPISODES_VALLE", "REGIME_SPLIT", "DB_PATH",
    "CONSOLIDATION_WINDOW_DAYS", "RANGE_BAND_MAX",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_valle_probe_constants.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/valle_calidad_probe/__init__.py tools/valle_calidad_probe/constants.py tests/test_valle_probe_constants.py
git commit -m "feat(probe): scaffold valle_calidad_probe + constantes congeladas del pre-registro"
```

---

### Task 2: Detección de episodios valle / no-valle (`episodes.py`)

**Files:**
- Create: `tools/valle_calidad_probe/episodes.py`
- Test: `tests/test_valle_probe_episodes.py`

- [ ] **Step 1: Write the failing tests**

Crear `tests/test_valle_probe_episodes.py`:

```python
"""Tests de la detección de episodios (puro, sintético).

Un episodio = run contiguo de días del mismo estado en_rango. La entrada es el
PRIMER día de cada episodio. Reusa measure_consolidation del screener A."""
from tools.valle_calidad_probe.episodes import detect_episodes


def _bar(t, close, *, high=None, low=None):
    high = high if high is not None else close * 1.005
    low = low if low is not None else close * 0.995
    return {"open_time": t, "open": close, "high": high, "low": low,
            "close": close, "volume": 1000.0, "quote_volume": 1_000_000.0}


def test_serie_en_rango_da_un_episodio_valle():
    # 150 días oscilando ±3% → en rango todo el tiempo evaluable → 1 episodio valle.
    bars = []
    for i in range(150):
        c = 1.0 + (0.03 if i % 2 else -0.03)
        bars.append(_bar(i * 86_400_000, c, high=c * 1.005, low=c * 0.995))
    eps = detect_episodes(bars)
    valles = [e for e in eps if e["tipo"] == "valle"]
    assert len(valles) == 1
    # La entrada cae en el primer día EVALUABLE (>= ventana de consolidación).
    assert valles[0]["entry_idx"] >= 84


def test_transicion_genera_episodios_separados():
    # Primero en rango (±3%), luego tendencia fuerte (sube 1→2) → valle, luego no_valle.
    bars = []
    for i in range(120):
        c = 1.0 + (0.03 if i % 2 else -0.03)
        bars.append(_bar(i * 86_400_000, c, high=c * 1.005, low=c * 0.995))
    for j in range(120, 240):
        c = 1.0 + (j - 119) / 60.0     # tendencia ancha → en_rango False
        bars.append(_bar(j * 86_400_000, c))
    eps = detect_episodes(bars)
    tipos = [e["tipo"] for e in eps]
    assert "valle" in tipos and "no_valle" in tipos
    # Episodios contiguos no se repiten consecutivamente del mismo tipo.
    for a, b in zip(tipos, tipos[1:]):
        assert a != b


def test_serie_corta_sin_historia_no_da_episodios():
    bars = [_bar(i * 86_400_000, 1.0) for i in range(50)]  # < ventana
    assert detect_episodes(bars) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_valle_probe_episodes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.valle_calidad_probe.episodes'`

- [ ] **Step 3: Write the implementation**

Crear `tools/valle_calidad_probe/episodes.py`:

```python
"""Detección de episodios valle / no-valle (puro, sin red).

Para cada día evaluable (índice >= CONSOLIDATION_WINDOW_DAYS) clasifica
en_rango con measure_consolidation del screener A (única fuente de verdad).
Runs contiguos del mismo estado = episodios. La entrada es el PRIMER día de
cada episodio. Pre-registro §"Estrategia"."""
from __future__ import annotations

from screener.valley_filter import measure_consolidation
from .constants import CONSOLIDATION_WINDOW_DAYS


def detect_episodes(bars: list[dict]) -> list[dict]:
    """Devuelve la lista de episodios [{tipo: 'valle'|'no_valle', entry_idx,
    end_idx}], en orden temporal. entry_idx = primer día del run; end_idx =
    último día del run (inclusive). Días con índice < ventana no se clasifican
    (no hay historia suficiente para measure_consolidation)."""
    if len(bars) <= CONSOLIDATION_WINDOW_DAYS:
        return []
    # Serie booleana en_rango[t] para t evaluable.
    estados: list[tuple[int, bool]] = []
    for t in range(CONSOLIDATION_WINDOW_DAYS, len(bars)):
        ventana = bars[: t + 1]                       # measure_consolidation mira los últimos 84
        en_rango = measure_consolidation(ventana)["en_rango"]
        estados.append((t, en_rango))

    episodios: list[dict] = []
    run_tipo: bool | None = None
    run_start = 0
    for idx, (t, en_rango) in enumerate(estados):
        if en_rango != run_tipo:
            if run_tipo is not None:
                episodios.append({
                    "tipo": "valle" if run_tipo else "no_valle",
                    "entry_idx": estados[run_start][0],
                    "end_idx": estados[idx - 1][0],
                })
            run_tipo = en_rango
            run_start = idx
    # Cerrar el último run.
    if run_tipo is not None:
        episodios.append({
            "tipo": "valle" if run_tipo else "no_valle",
            "entry_idx": estados[run_start][0],
            "end_idx": estados[-1][0],
        })
    return episodios
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_valle_probe_episodes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/valle_calidad_probe/episodes.py tests/test_valle_probe_episodes.py
git commit -m "feat(probe): detect_episodes — episodios valle/no-valle vía screener A (puro)"
```

---

### Task 3: P&L net-of-v3w por entrada (`simulate.py`)

**Files:**
- Create: `tools/valle_calidad_probe/simulate.py`
- Test: `tests/test_valle_probe_simulate.py`

- [ ] **Step 1: Write the failing tests**

Crear `tests/test_valle_probe_simulate.py`:

```python
"""Tests del P&L net-of-v3w por entrada (puro, costo inyectado).

El costo v3w real (celda 4) se inyecta como callable para testear la lógica de
simulación sin tocar la calibración ni la DB."""
from tools.valle_calidad_probe.simulate import simulate_entry


def _bar(t, close):
    return {"open_time": t, "open": close, "high": close * 1.01,
            "low": close * 0.99, "close": close, "volume": 1.0,
            "quote_volume": 1_000_000.0}


def _cost_zero(*a, **k):
    return 0.0   # costo cero → net == gross, aísla la aritmética de retorno


def test_long_hold_20d_retorno_y_pnl():
    # Precio sube de 1.0 a 1.10 en 20 días → gross +10%.
    bars = [_bar(i * 86_400_000, 1.0 + 0.10 * (i / 20.0)) for i in range(40)]
    e = simulate_entry(bars, entry_idx=0, tipo="valle", episode_id=0,
                       median_dollar_vol=5_000_000.0, fill_cost=_cost_zero)
    assert abs(e["gross_ret"] - 0.10) < 1e-9
    assert abs(e["net_ret"] - 0.10) < 1e-9          # costo cero
    assert abs(e["pnl_usd"] - 100.0) < 1e-6          # 1000 × 0.10
    assert e["forced_close"] is False
    assert e["tipo"] == "valle" and e["episode_id"] == 0


def test_delisting_antes_de_H_fuerza_cierre():
    # Solo 10 barras tras la entrada (< 20) → cierre forzado al último precio.
    bars = [_bar(i * 86_400_000, 1.0 + 0.05 * (i / 10.0)) for i in range(11)]
    e = simulate_entry(bars, entry_idx=0, tipo="no_valle", episode_id=1,
                       median_dollar_vol=5_000_000.0, fill_cost=_cost_zero)
    assert e["forced_close"] is True
    assert abs(e["gross_ret"] - 0.05) < 1e-9          # salió al último (idx 10)


def test_costo_resta_del_neto():
    bars = [_bar(i * 86_400_000, 1.0) for i in range(40)]   # precio plano → gross 0
    # Costo $3 por fill → 2 fills (entrada+salida) = $6 → net_pnl = -6, net_ret = -0.006.
    e = simulate_entry(bars, entry_idx=0, tipo="valle", episode_id=0,
                       median_dollar_vol=5_000_000.0, fill_cost=lambda *a, **k: 3.0)
    assert abs(e["pnl_usd"] - (-6.0)) < 1e-6
    assert abs(e["net_ret"] - (-0.006)) < 1e-9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_valle_probe_simulate.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Crear `tools/valle_calidad_probe/simulate.py`:

```python
"""Simulación del P&L net-of-v3w de una entrada LONG (puro).

LONG el primer día del episodio, hold HOLD_DAYS; si la serie termina antes
(delisting), cierre forzado al último precio con el peor tier (forced_close).
El costo es 2 fills (entrada + salida); se inyecta como callable para testear
sin la calibración real (en producción = tools.celda4_stat_arb.costs.v3w_fill_cost).
Pre-registro §"Estrategia" y §"Costo (net-of-v3w)"."""
from __future__ import annotations

from .constants import HOLD_DAYS, NOTIONAL_USD


def simulate_entry(bars: list[dict], *, entry_idx: int, tipo: str, episode_id: int,
                   median_dollar_vol: float, fill_cost) -> dict:
    """Simula UNA entrada. `fill_cost(notional, tier, *, median_daily_dollar_vol,
    forced_close)` devuelve el costo $ de un fill. Devuelve el registro de la
    entrada con gross/net y pnl_usd."""
    entry_price = float(bars[entry_idx]["close"])
    target_idx = entry_idx + HOLD_DAYS
    forced = target_idx > len(bars) - 1
    exit_idx = min(target_idx, len(bars) - 1)
    exit_price = float(bars[exit_idx]["close"])

    gross_ret = (exit_price - entry_price) / entry_price if entry_price else 0.0

    # 2 fills: entrada (tier normal) + salida (forced_close si delisting).
    # El tier lo decide el caller via la mediana de dollar-vol (v3w); aquí solo
    # pasamos median_dollar_vol y el flag. El tier name lo resuelve el caller en
    # producción; para el costo inyectado en tests basta el contrato.
    cost_in = fill_cost(NOTIONAL_USD, median_daily_dollar_vol=median_dollar_vol,
                        forced_close=False)
    cost_out = fill_cost(NOTIONAL_USD, median_daily_dollar_vol=median_dollar_vol,
                         forced_close=forced)
    net_pnl = NOTIONAL_USD * gross_ret - (cost_in + cost_out)

    return {
        "tipo": tipo,
        "episode_id": episode_id,
        "entry_idx": entry_idx,
        "entry_ts": int(bars[entry_idx]["open_time"]),   # para el reporte de robustez por mitades
        "exit_idx": exit_idx,
        "forced_close": forced,
        "gross_ret": gross_ret,
        "net_ret": net_pnl / NOTIONAL_USD,
        "pnl_usd": net_pnl,
    }
```

Añadir al test `test_long_hold_20d_retorno_y_pnl` (Step 1 de esta tarea) una línea de aserción: `assert e["entry_ts"] == 0` (entry_idx=0 → open_time 0 en las barras sintéticas).

NOTA para el orquestador (Task 5): el `fill_cost` de producción envuelve `v3w_fill_cost(notional, tier, calibration, median_daily_dollar_vol=..., forced_close=...)`, donde `tier = tier_for_volume(median_dollar_vol, cutoffs)`. El wrapper se arma en `run.py` (cierra sobre `calibration` y `cutoffs`), de modo que `simulate_entry` permanece puro y testeable con costo inyectado.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_valle_probe_simulate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/valle_calidad_probe/simulate.py tests/test_valle_probe_simulate.py
git commit -m "feat(probe): simulate_entry — P&L net-of-v3w LONG hold 20d + forced-close por delisting"
```

---

### Task 4: Block-bootstrap de la diferencia de medias (`bootstrap.py`)

**Files:**
- Create: `tools/valle_calidad_probe/bootstrap.py`
- Test: `tests/test_valle_probe_bootstrap.py`

- [ ] **Step 1: Write the failing tests**

Crear `tests/test_valle_probe_bootstrap.py`:

```python
"""Tests del bootstrap de la diferencia de medias (determinista, seed 42).

Unidad de remuestreo = la entrada (= el episodio: una entrada por episodio).
El estadístico = mean(pnl_valle) − mean(pnl_no_valle)."""
from tools.valle_calidad_probe.bootstrap import bootstrap_diff


def test_diferencia_positiva_clara_excluye_cero():
    # valle netamente mejor que no_valle, sin solape → CI debe excluir cero (+).
    valle = [{"pnl_usd": v} for v in [100.0] * 40]
    no_valle = [{"pnl_usd": v} for v in [0.0] * 40]
    out = bootstrap_diff(valle, no_valle)
    assert out["diff"] == 100.0
    assert out["ci_low"] > 0.0
    assert out["ci_high"] > out["ci_low"]


def test_grupos_iguales_incluyen_cero():
    valle = [{"pnl_usd": v} for v in [10.0, -10.0] * 20]
    no_valle = [{"pnl_usd": v} for v in [10.0, -10.0] * 20]
    out = bootstrap_diff(valle, no_valle)
    assert out["ci_low"] <= 0.0 <= out["ci_high"]


def test_determinista_misma_seed_mismo_ci():
    valle = [{"pnl_usd": float(i)} for i in range(40)]
    no_valle = [{"pnl_usd": float(i) - 5.0} for i in range(40)]
    a = bootstrap_diff(valle, no_valle)
    b = bootstrap_diff(valle, no_valle)
    assert a == b   # seed fijo → byte-idéntico
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_valle_probe_bootstrap.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Crear `tools/valle_calidad_probe/bootstrap.py`:

```python
"""Block-bootstrap de la diferencia de medias valle − no_valle (determinista).

Unidad de remuestreo = la entrada (una por episodio → "block por episodio"
del pre-registro). Resample con reemplazo de cada grupo por separado,
BOOTSTRAP_ITERS veces, seed fijo. CI95 percentil de la diferencia de medias.
Pre-registro §"Métrica"."""
from __future__ import annotations

import numpy as np

from .constants import BOOTSTRAP_ITERS, SEED


def bootstrap_diff(valle: list[dict], no_valle: list[dict]) -> dict:
    """Devuelve {diff, ci_low, ci_high, n_valle, n_no_valle}. diff = diferencia
    de medias puntual; ci_* = percentiles 2.5/97.5 de la distribución bootstrap."""
    v = np.array([e["pnl_usd"] for e in valle], dtype=float)
    n = np.array([e["pnl_usd"] for e in no_valle], dtype=float)
    diff = float(v.mean() - n.mean()) if len(v) and len(n) else 0.0

    rng = np.random.default_rng(SEED)
    diffs = np.empty(BOOTSTRAP_ITERS, dtype=float)
    nv, nn = len(v), len(n)
    for i in range(BOOTSTRAP_ITERS):
        bv = v[rng.integers(0, nv, nv)].mean() if nv else 0.0
        bn = n[rng.integers(0, nn, nn)].mean() if nn else 0.0
        diffs[i] = bv - bn
    ci_low, ci_high = np.percentile(diffs, [2.5, 97.5])
    return {"diff": diff, "ci_low": float(ci_low), "ci_high": float(ci_high),
            "n_valle": nv, "n_no_valle": nn}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_valle_probe_bootstrap.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/valle_calidad_probe/bootstrap.py tests/test_valle_probe_bootstrap.py
git commit -m "feat(probe): bootstrap_diff — diferencia de medias por episodio, seed 42 determinista"
```

---

### Task 5: Orquestador + gates + dictamen (`run.py`)

**Files:**
- Create: `tools/valle_calidad_probe/run.py`
- Test: `tests/test_valle_probe_run.py`

- [ ] **Step 1: Write the failing tests**

Crear `tests/test_valle_probe_run.py`. El test construye un mini-panel sintético en una DB temporal con la tabla `spot_klines`, inyecta un `fill_cost` cero y un `cutoffs`/`calibration` triviales, y verifica el veredicto + los candados:

```python
"""Tests del orquestador (mini-panel sintético en tmp DB, costo inyectado).

NO toca el panel real ni la calibración v3 real — el objetivo es la lógica de
gate/poder/dictamen, no los números de mercado."""
import sqlite3

from tools.valle_calidad_probe.run import evaluate_verdict


def test_gate_underpowered_si_pocos_episodios():
    # 5 entradas valle (< MIN_EPISODES_VALLE=30) → UNDERPOWERED.
    valle = [{"pnl_usd": 50.0} for _ in range(5)]
    no_valle = [{"pnl_usd": 0.0} for _ in range(40)]
    v = evaluate_verdict(valle, no_valle)
    assert v["verdict"] == "UNDERPOWERED"
    assert v["n_episodios_valle"] == 5


def test_gate_pass_si_diferencia_positiva_excluye_cero():
    valle = [{"pnl_usd": 100.0} for _ in range(40)]
    no_valle = [{"pnl_usd": 0.0} for _ in range(40)]
    v = evaluate_verdict(valle, no_valle)
    assert v["verdict"] == "PASS"
    assert v["ci_low"] > 0.0


def test_gate_fail_si_ci_incluye_cero():
    valle = [{"pnl_usd": x} for x in ([10.0, -10.0] * 20)]
    no_valle = [{"pnl_usd": x} for x in ([10.0, -10.0] * 20)]
    v = evaluate_verdict(valle, no_valle)
    assert v["verdict"] == "FAIL"


def test_no_importa_holdout():
    # Candado #322: el módulo run no debe referenciar holdout en absoluto.
    import tools.valle_calidad_probe.run as runmod
    import inspect
    src = inspect.getsource(runmod)
    assert "holdout" not in src.lower()
    assert "open_holdout" not in src
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_valle_probe_run.py -v`
Expected: FAIL — `ModuleNotFoundError` / `cannot import name 'evaluate_verdict'`

- [ ] **Step 3: Write the implementation**

Crear `tools/valle_calidad_probe/run.py`. Separa la lógica de gate (`evaluate_verdict`, pura y testeable) de la orquestación con I/O (`main`):

```python
"""Orquestador del probe valle-calidad (sondeo pre-celda).

Carga el panel (read-only), deriva cutoffs v3w (celda 4), detecta episodios
por símbolo (screener A), simula cada entrada net-of-v3w, agrega los dos
grupos, corre el bootstrap de la diferencia y aplica los gates del
pre-registro. Escribe findings.md + verdict.json + descriptivos.

UNA corrida, determinista, cero holdout. Pre-registro:
data/retune/2026-06-11-valle-calidad-probe/PREREGISTRO.md.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from .bootstrap import bootstrap_diff
from .constants import (
    DB_PATH, MIN_EPISODES_VALLE, NOTIONAL_USD, REGIME_SPLIT, STUDY_END, STUDY_START,
)

_OUT_DIR = "data/retune/2026-06-11-valle-calidad-probe"


def evaluate_verdict(valle: list[dict], no_valle: list[dict]) -> dict:
    """Aplica los gates del pre-registro. PURA: no toca disco ni red.

    UNDERPOWERED si n_valle < MIN_EPISODES_VALLE. Si no: PASS si diff>0 y el
    CI95 excluye cero por el lado positivo (ci_low>0); FAIL en otro caso."""
    boot = bootstrap_diff(valle, no_valle)
    n_valle = boot["n_valle"]
    base = {
        "n_episodios_valle": n_valle,
        "n_episodios_no_valle": boot["n_no_valle"],
        "diff": boot["diff"],
        "ci_low": boot["ci_low"],
        "ci_high": boot["ci_high"],
    }
    if n_valle < MIN_EPISODES_VALLE:
        return {**base, "verdict": "UNDERPOWERED"}
    if boot["diff"] > 0 and boot["ci_low"] > 0:
        return {**base, "verdict": "PASS"}
    return {**base, "verdict": "FAIL"}


def _resample_daily(rows: list[tuple]) -> list[dict]:
    """Resamplea klines 1h (open_time_ms, o, h, l, c, vol, quote_vol) a barras
    DIARIAS del contrato del screener. Agrupa por día UTC: open=primer open,
    high=max, low=min, close=último close, volumen sumado."""
    by_day: dict[int, list[tuple]] = {}
    for r in rows:
        day = int(r[0]) // 86_400_000
        by_day.setdefault(day, []).append(r)
    bars = []
    for day in sorted(by_day):
        g = by_day[day]
        bars.append({
            "open_time": day * 86_400_000,
            "open": float(g[0][1]), "high": max(float(x[2]) for x in g),
            "low": min(float(x[3]) for x in g), "close": float(g[-1][4]),
            "volume": sum(float(x[5]) for x in g),
            "quote_volume": sum(float(x[6]) for x in g),
        })
    return bars


def input_fingerprint(con, study_end_ms: int) -> dict:
    """Huella del input para el dictamen: conteo de filas y símbolos del panel
    dentro de la ventana, para que el verdict sea reproducible/auditable."""
    n_rows = con.execute(
        "SELECT COUNT(*) FROM spot_klines WHERE open_time <= ?", (study_end_ms,)
    ).fetchone()[0]
    n_sym = con.execute(
        "SELECT COUNT(DISTINCT symbol) FROM spot_klines WHERE open_time <= ?",
        (study_end_ms,)
    ).fetchone()[0]
    return {"spot_rows_in_window": int(n_rows), "spot_symbols": int(n_sym),
            "study_window": [STUDY_START, STUDY_END]}


def main(*, db_path: str = DB_PATH, out_dir: str = _OUT_DIR) -> dict:
    """Corrida terminal. Construye los grupos sobre el panel real, evalúa,
    escribe el dictamen. Importa el costo v3w de la celda 4 aquí (no a nivel de
    módulo) para que los tests de evaluate_verdict no requieran la calibración."""
    from backtest_costs import load_calibration
    from tools.celda4_stat_arb.costs import (
        derive_tier_cutoffs, tier_for_volume, v3w_fill_cost,
    )
    from .episodes import detect_episodes
    from .simulate import simulate_entry

    def _to_ms(s: str) -> int:
        return int(datetime.strptime(s, "%Y-%m-%d")
                   .replace(tzinfo=timezone.utc).timestamp() * 1000)

    study_start_ms, study_end_ms = _to_ms(STUDY_START), _to_ms(STUDY_END)
    calibration = load_calibration()
    cutoffs = derive_tier_cutoffs(db_path)

    valle: list[dict] = []
    no_valle: list[dict] = []
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as con:
        fp = input_fingerprint(con, study_end_ms)
        symbols = [r[0] for r in con.execute(
            "SELECT DISTINCT symbol FROM spot_klines").fetchall()]
        for sym in symbols:
            rows = con.execute(
                "SELECT open_time, open, high, low, close, volume, quote_volume "
                "FROM spot_klines WHERE symbol=? AND open_time>=? AND open_time<=? "
                "ORDER BY open_time", (sym, study_start_ms, study_end_ms)).fetchall()
            bars = _resample_daily(rows)
            eps = detect_episodes(bars)
            if not eps:
                continue
            # mediana de dollar-volume diario del símbolo (asigna tier v3w).
            vols = sorted(b["quote_volume"] for b in bars)
            mv = vols[len(vols) // 2] if vols else 0.0
            tier = tier_for_volume(mv, cutoffs)

            def _fill_cost(notional, *, median_daily_dollar_vol, forced_close):
                return v3w_fill_cost(notional, tier, calibration,
                                     median_daily_dollar_vol=median_daily_dollar_vol,
                                     forced_close=forced_close)

            for eid, ep in enumerate(eps):
                e = simulate_entry(bars, entry_idx=ep["entry_idx"], tipo=ep["tipo"],
                                   episode_id=eid, median_dollar_vol=mv,
                                   fill_cost=_fill_cost)
                (valle if ep["tipo"] == "valle" else no_valle).append(e)

    verdict = evaluate_verdict(valle, no_valle)
    verdict["fingerprint"] = fp
    verdict["coordenada"] = {"edicion": 2, "candidata": "valle-calidad",
                             "tipo": "sondeo-pre-celda", "verbo": "F"}
    verdict["fecha"] = STUDY_END  # estampar la ventana, no la fecha de corrida (determinismo)

    # Robustez (REPORTE, no gate — pre-registro §Robustez): la diferencia partida
    # en dos mitades por REGIME_SPLIT. Solo informa si la señal es estable o
    # concentrada en un régimen; el verdict de arriba corre sobre la ventana completa.
    split_ms = _to_ms(REGIME_SPLIT)
    def _half(entries, lo, hi):
        return [e for e in entries if lo <= e["entry_ts"] < hi]
    verdict["robustez"] = {
        "split": REGIME_SPLIT,
        "primera_mitad": bootstrap_diff(_half(valle, study_start_ms, split_ms),
                                        _half(no_valle, study_start_ms, split_ms)),
        "segunda_mitad": bootstrap_diff(_half(valle, split_ms, study_end_ms + 1),
                                        _half(no_valle, split_ms, study_end_ms + 1)),
    }

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "verdict.json"), "w", encoding="utf-8") as f:
        json.dump(verdict, f, indent=2, ensure_ascii=False)
    _write_findings(out_dir, verdict)
    return verdict


def _write_findings(out_dir: str, v: dict) -> None:
    """findings.md: veredicto en la línea 1, luego los números del gate."""
    linea1 = {
        "PASS": "PASS — la consolidación aporta sobre el baseline; abrir celda formal E2.",
        "FAIL": "FAIL — estar en el valle no aporta sobre comprar no-valles; ranking muere.",
        "UNDERPOWERED": "UNDERPOWERED — episodios de valle insuficientes; inconcluso.",
    }[v["verdict"]]
    txt = (
        f"# Dictamen — probe valle-calidad\n\n"
        f"**{linea1}**\n\n"
        f"- diff (mean valle − mean no_valle): {v['diff']:.4f} $/entrada\n"
        f"- CI95 (block-bootstrap por episodio, seed 42): "
        f"[{v['ci_low']:.4f}, {v['ci_high']:.4f}]\n"
        f"- N episodios valle: {v['n_episodios_valle']} "
        f"(umbral de poder: {MIN_EPISODES_VALLE})\n"
        f"- N episodios no-valle: {v['n_episodios_no_valle']}\n"
        f"- notional: ${NOTIONAL_USD:.0f} · net-of-v3w · spot · ventana "
        f"{v['fingerprint']['study_window'][0]}→{v['fingerprint']['study_window'][1]}\n\n"
        f"Pre-registro: data/retune/2026-06-11-valle-calidad-probe/PREREGISTRO.md "
        f"(criterios congelados 2026-06-11).\n"
    )
    with open(os.path.join(out_dir, "findings.md"), "w", encoding="utf-8") as f:
        f.write(txt)


if __name__ == "__main__":
    print(main()["verdict"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_valle_probe_run.py -v`
Expected: PASS (gates + candado holdout)

- [ ] **Step 5: Commit**

```bash
git add tools/valle_calidad_probe/run.py tests/test_valle_probe_run.py
git commit -m "feat(probe): run — orquestador + gates PASS/FAIL/UNDERPOWERED + dictamen (cero holdout)"
```

---

### Task 6: Gate de tests + GROW (registro de la candidata)

**Files:**
- Modify: `.mex/programa/INDEX.md` (estado de la candidata valle-calidad)
- Test: el suite del probe completo

- [ ] **Step 1: Suite del probe verde**

Run: `python -m pytest tests/test_valle_probe_constants.py tests/test_valle_probe_episodes.py tests/test_valle_probe_simulate.py tests/test_valle_probe_bootstrap.py tests/test_valle_probe_run.py -q`
Expected: todo verde.

- [ ] **Step 2: Gate rápido global (no romper nada)**

Run: `python -m pytest tests/ -m "not network" -q`
Expected: verde (los nuevos módulos no tocan rutas existentes). Si algo ajeno falla, verificar que sea flake preexistente (no relacionado con `tools/valle_calidad_probe/` ni `screener/`).

- [ ] **Step 3: GROW — registrar la candidata en el INDEX del programa**

En `.mex/programa/INDEX.md`, sección "Candidatas a Edición 2", añadir/actualizar la fila de valle-calidad. Leer el formato exacto de las filas vecinas (sentiment C1, order-flow C2, fundamentales C3) y replicar. Contenido: candidata "valle-calidad (consolidación geométrica)", estado "PROBE CONSTRUIDO — falsificador `tools/valle_calidad_probe/` listo; pre-registro congelado 2026-06-11; PENDIENTE corrida terminal sobre panel regenerado", condición de apertura "PASS del probe ⟹ celda formal E2 que estudia la gradación de calidad".

- [ ] **Step 4: Commit**

```bash
git add .mex/programa/INDEX.md
git commit -m "docs(programa): registrar candidata valle-calidad — probe construido, pendiente corrida terminal"
```

---

## Ejecución del experimento (MANUAL, TERMINAL — la bala única)

**NO la corre un subagente automático.** Es el momento de falsificación: una sola corrida, sin re-correr. Requiere el panel regenerado (Task 0).

```bash
# Pre-condición: data/program_ohlcv.db poblado (Task 0), suite del probe verde.
python -m tools.valle_calidad_probe.run
```

Esto escribe `data/retune/2026-06-11-valle-calidad-probe/{verdict.json, findings.md}`. El veredicto (PASS/FAIL/UNDERPOWERED) es terminal:

- **PASS** → se abre celda formal en E2 (junta de apertura) que estudia la gradación de calidad; solo entonces el ranking puede graduarse a la UI del screener A.
- **FAIL** → la idea del ranking muere barato; la lista de A se queda neutral para siempre (correcto si no hay edge).
- **UNDERPOWERED** → inconcluso; se documenta la N observada, no se fuerza veredicto.

Revisar el dictamen con el operador ANTES de cualquier decisión de apertura. Commitear `verdict.json` + `findings.md` como el fósil del resultado.
