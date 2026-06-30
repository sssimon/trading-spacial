# Calibración del gate de exposición por régimen — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir el harness de la PRUEBA DE FALSACIÓN que decide, con datos point-in-time, si el gate de exposición por régimen debe encenderse (y con qué umbrales), produciendo `results.json` + `findings.md` con veredicto PASA / NO PASA / INVERTIDO.

**Architecture:** Un estudio reproducible en `data/retune/2026-06-23-calibracion-gate-regimen/calib_study.py`, espejo estructural de `data/retune/2026-06-18-setup-edge-multiregimen/edge_study.py`. Lee el panel anti-survivorship `data/program_ohlcv.db` (1h→diario) + un CSV BTC.D congelado. **Reusa el `regime.alt_season.compose_regime` real** (fidelidad con producción) para el voto de 3 componentes; reimplementa los features vectorizados (testeados contra las definiciones de `screener/valley_filter.measure_setup`). Evalúa un criterio de aceptación pre-comprometido.

**Tech Stack:** Python 3, stdlib + numpy/pandas/scipy. sqlite3 (lee `program_ohlcv.db`). Sin red en la corrida de decisión (el BTC.D es un dato congelado, paso previo).

**Spec:** `docs/superpowers/specs/es/2026-06-23-calibracion-gate-regimen-design.md` (commit `15d31c0`).

## Global Constraints

(Aplican a TODAS las tareas — valores verbatim del spec / no-negociables.)

- **#2/#3 (holdout):** usar SOLO `data/program_ohlcv.db` + el CSV BTC.D congelado. NUNCA `data/holdout/`, NUNCA `open_holdout`, NUNCA `simulate_strategy`. **Período de señales termina 2025-04-29** (la barra antes de la ventana del holdout `2025-04-30 → 2026-04-30`, `.mex/context/decisions.md:39`).
- **#4:** no aplica — el estudio no toca sizing/`RISK_PER_TRADE`.
- **Fidelidad de régimen:** el voto de 3 componentes DEBE reusar `regime.alt_season.compose_regime` + `effective_thresholds` (no reimplementar el voto). Los features ex-ante (pos/rsi/sma/consol/vol) deben coincidir con las definiciones de `screener/valley_filter.measure_setup` (verificado por test).
- **Reproducible:** la corrida de decisión no toca red. El estudio vive en `data/retune/2026-06-23-calibracion-gate-regimen/`; sus tests se co-ubican ahí y se corren manualmente (NO entran al gate de CI — el estudio no es código de producción y CI no depende de `program_ohlcv.db`).
- **Criterio de aceptación PRE-COMPROMETIDO (no mover el poste):** margen **+2 pp**, **p<0.01**. Definir ANTES de mirar resultados.
- **Veredicto honesto:** el estudio tiene permiso de concluir NO PASA o INVERTIDO. No "tunear hasta que pase".

## File Structure

| Archivo | Responsabilidad | Task |
|---|---|---|
| `data/retune/2026-06-23-calibracion-gate-regimen/METODOLOGIA.md` | Congela esta metodología (qué se mide, criterio, caveats) | 1 |
| `data/retune/2026-06-23-calibracion-gate-regimen/btc_dominance.csv` | Serie BTC.D diaria congelada (date,dominance) | 1 |
| `data/retune/2026-06-23-calibracion-gate-regimen/calib_study.py` | El harness. Funciones puras + main. | 1-6 |
| `data/retune/2026-06-23-calibracion-gate-regimen/test_calib_study.py` | Tests co-ubicados (corren manualmente) | 1-6 |
| `data/retune/2026-06-23-calibracion-gate-regimen/results.json` | Salida (generada al correr) | 6 |
| `data/retune/2026-06-23-calibracion-gate-regimen/findings.md` | Veredicto (generado al correr) | 6 |

**Orden:** 1 (scaffold + BTC.D loader) → 2 (panel resample) → 3 (features) → 4 (régimen 3-comp) → 5 (selección + stats + criterio) → 6 (main + salidas + grid).

**Nota de testing:** los tests co-ubicados se corren con `python -m pytest data/retune/2026-06-23-calibracion-gate-regimen/test_calib_study.py -v` desde la raíz del repo. Usan datos SINTÉTICOS (sqlite in-memory, CSV/frames construidos) — no requieren `program_ohlcv.db` ni red.

---

## Task 1: Scaffold + cargador de BTC.D congelado

**Files:**
- Create: `data/retune/2026-06-23-calibracion-gate-regimen/METODOLOGIA.md`
- Create: `data/retune/2026-06-23-calibracion-gate-regimen/btc_dominance.csv` (dato congelado, ver Step 1)
- Create: `data/retune/2026-06-23-calibracion-gate-regimen/calib_study.py` (con `load_btc_dominance`)
- Test: `data/retune/2026-06-23-calibracion-gate-regimen/test_calib_study.py`

**Interfaces:**
- Produces: `load_btc_dominance(csv_path: str) -> pandas.Series` — index=fecha UTC normalizada (Timestamp tz-aware), valor=dominancia como **fracción 0-1**. Normaliza si el CSV viene en 0-100 (divide /100 cuando el máximo > 1.5).

- [ ] **Step 1 (prerequisito manual): obtener y congelar el CSV BTC.D**

Obtener una serie DIARIA de dominancia BTC que cubra `2021-01-01 → 2025-04-29` de una fuente documentable (TradingView ticker `BTC.D` export, o Investing.com "Bitcoin Dominance" históricos, o un dataset público). Guardar como `data/retune/2026-06-23-calibracion-gate-regimen/btc_dominance.csv` con exactamente dos columnas, header `date,dominance`:
```csv
date,dominance
2021-01-01,69.7
2021-01-02,68.9
...
```
`date` en ISO `YYYY-MM-DD`. `dominance` en porcentaje 0-100 O fracción 0-1 (el loader normaliza). Documentar fuente + fecha de descarga en `METODOLOGIA.md` (§Procedencia). Si no se puede cubrir todo el rango, el loader debe fallar ruidosamente (Step 4 cubre el gap-check).

- [ ] **Step 2: Write the failing test**

```python
# data/retune/2026-06-23-calibracion-gate-regimen/test_calib_study.py
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import pandas as pd
import calib_study as cs

def test_load_btc_dominance_normaliza_porcentaje(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("date,dominance\n2021-01-01,70.0\n2021-01-02,68.5\n")
    s = cs.load_btc_dominance(str(p))
    assert abs(s.loc[pd.Timestamp("2021-01-01", tz="UTC")] - 0.70) < 1e-9
    assert s.max() <= 1.0  # normalizado a fracción

def test_load_btc_dominance_ya_fraccion(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("date,dominance\n2021-01-01,0.70\n2021-01-02,0.685\n")
    s = cs.load_btc_dominance(str(p))
    assert abs(s.loc[pd.Timestamp("2021-01-01", tz="UTC")] - 0.70) < 1e-9
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest data/retune/2026-06-23-calibracion-gate-regimen/test_calib_study.py -v`
Expected: FAIL con `ModuleNotFoundError` o `AttributeError: module 'calib_study' has no attribute 'load_btc_dominance'`.

- [ ] **Step 4: Implement**

Crear `calib_study.py` con el header del módulo + `load_btc_dominance`:

```python
"""
Calibración del gate de exposición por régimen — prueba de falsación.
Implementación CONGELADA según METODOLOGIA.md (2026-06-23). No cambia la metodología.

Lee data/program_ohlcv.db (anti-survivorship) + btc_dominance.csv (congelado).
NO toca data/holdout/, NO llama open_holdout/simulate_strategy. Período de señales
termina 2025-04-29 (barra antes del holdout 2025-04-30→2026-04-30).
Reusa regime.alt_season.compose_regime para el voto de 3 componentes (fidelidad).
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

HERE = Path(__file__).resolve().parent
# raíz del repo: .../data/retune/<dir>/ → subir 3
REPO_ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

SIGNAL_START = pd.Timestamp("2021-01-01", tz="UTC")
SIGNAL_END = pd.Timestamp("2025-04-29", tz="UTC")   # barra antes del holdout
HOLDOUT_START = pd.Timestamp("2025-04-30", tz="UTC")


def load_btc_dominance(csv_path: str) -> pd.Series:
    """CSV (date,dominance) → Series index=fecha UTC, valor=fracción 0-1.
    Normaliza desde 0-100 si el máximo sugiere porcentaje (>1.5)."""
    df = pd.read_csv(csv_path)
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.normalize()
    s = pd.Series(df["dominance"].astype(float).values, index=df["date"])
    s = s[~s.index.duplicated(keep="last")].sort_index()
    if s.max() > 1.5:
        s = s / 100.0
    s.name = "btc_dominance"
    return s
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest data/retune/2026-06-23-calibracion-gate-regimen/test_calib_study.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Escribir METODOLOGIA.md**

Copiar la §Metodología + §Criterio de aceptación + §Caveats del spec (`docs/superpowers/specs/es/2026-06-23-calibracion-gate-regimen-design.md`) a `METODOLOGIA.md`, y añadir §Procedencia del BTC.D (fuente exacta + fecha de descarga + rango cubierto). Es el congelamiento de la metodología.

- [ ] **Step 7: Commit**

```bash
git add data/retune/2026-06-23-calibracion-gate-regimen/
git commit -m "feat(calib): scaffold del estudio + cargador de BTC.D congelado"
```

---

## Task 2: Cargador del panel (1h → diario)

**Files:**
- Modify: `data/retune/2026-06-23-calibracion-gate-regimen/calib_study.py`
- Test: `data/retune/2026-06-23-calibracion-gate-regimen/test_calib_study.py`

**Interfaces:**
- Produces: `load_spot_daily(db_path: str, symbols: list[str] | None = None) -> dict[str, pandas.DataFrame]` — por símbolo, DataFrame diario index=fecha UTC con columnas `open, high, low, close, volume, quote_vol`. Resample 1h→diario: open=primer open del día UTC, high=max, low=min, close=último close, volume=suma; `quote_vol = volume * close` (aprox). Días sin barras se omiten (no se rellenan).

- [ ] **Step 1: Write the failing test**

```python
def _mk_db(tmp_path):
    import sqlite3
    p = tmp_path / "panel.db"
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE spot_klines(symbol TEXT, open_time INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY(symbol, open_time)) WITHOUT ROWID")
    # 2 días de barras horarias para FOOUSDT: día1 (24 barras), día2 (2 barras)
    H = 3600_000
    d1 = int(pd.Timestamp("2021-03-01", tz="UTC").timestamp() * 1000)
    rows = []
    for h in range(24):
        rows.append(("FOOUSDT", d1 + h*H, 10.0+h, 20.0+h, 5.0+h, 12.0+h, 100.0))
    d2 = int(pd.Timestamp("2021-03-02", tz="UTC").timestamp() * 1000)
    rows.append(("FOOUSDT", d2, 50.0, 60.0, 40.0, 55.0, 7.0))
    rows.append(("FOOUSDT", d2 + H, 55.0, 65.0, 45.0, 58.0, 3.0))
    con.executemany("INSERT INTO spot_klines VALUES (?,?,?,?,?,?,?)", rows)
    con.commit(); con.close()
    return str(p)

def test_load_spot_daily_resample(tmp_path):
    dbp = _mk_db(tmp_path)
    out = cs.load_spot_daily(dbp)
    df = out["FOOUSDT"]
    d1 = pd.Timestamp("2021-03-01", tz="UTC")
    assert df.loc[d1, "open"] == 10.0          # primer open del día
    assert df.loc[d1, "high"] == 20.0 + 23      # max high (h=23)
    assert df.loc[d1, "low"] == 5.0             # min low (h=0)
    assert df.loc[d1, "close"] == 12.0 + 23     # último close
    assert df.loc[d1, "volume"] == 100.0 * 24   # suma
    assert abs(df.loc[d1, "quote_vol"] - (100.0*24)*(12.0+23)) < 1e-6
    d2 = pd.Timestamp("2021-03-02", tz="UTC")
    assert df.loc[d2, "close"] == 58.0          # último close del día parcial
```

- [ ] **Step 2: Run to verify it fails** — `AttributeError: ... 'load_spot_daily'`.

- [ ] **Step 3: Implement**

```python
import sqlite3

_DEFAULT_DB = str(REPO_ROOT / "data" / "program_ohlcv.db")
_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}

def load_spot_daily(db_path: str, symbols=None) -> dict:
    con = sqlite3.connect(db_path)
    try:
        if symbols is None:
            symbols = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM spot_klines")]
        out = {}
        for sym in symbols:
            rows = con.execute(
                "SELECT open_time, open, high, low, close, volume FROM spot_klines "
                "WHERE symbol=? ORDER BY open_time", (sym,)).fetchall()
            if not rows:
                continue
            df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume"])
            df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            df = df.set_index("ts")
            daily = df.resample("1D").agg(_AGG).dropna(subset=["close"])
            daily["quote_vol"] = daily["volume"] * daily["close"]
            daily.index = daily.index.normalize()
            out[sym] = daily
        return out
    finally:
        con.close()
```

- [ ] **Step 4: Run to verify it passes** — PASS.

- [ ] **Step 5: Commit**

```bash
git add data/retune/2026-06-23-calibracion-gate-regimen/calib_study.py data/retune/2026-06-23-calibracion-gate-regimen/test_calib_study.py
git commit -m "feat(calib): cargador del panel program_ohlcv.db con resample 1h→diario"
```

---

## Task 3: Features ex-ante + forward

**Files:**
- Modify: `data/retune/2026-06-23-calibracion-gate-regimen/calib_study.py`
- Test: `data/retune/2026-06-23-calibracion-gate-regimen/test_calib_study.py`

**Interfaces:**
- Consumes: el DataFrame diario de `load_spot_daily`.
- Produces: `compute_features(df) -> df` — añade `pos_in_30d_range, rsi14, sma20, sma50, pct_vs_sma20, pct_vs_sma50, consol_30d, vol_ratio, above_sma50, ret_30d, alive, max_fwd_7d, max_fwd_14d, max_fwd_30d, rule_return, win15`. Definiciones idénticas a `screener/valley_filter.measure_setup` + `edge_study.py`.

**Decisión (item abierto resuelto):** las constantes del gate de vida y forward se copian verbatim de `edge_study.py`: `MIN_MEDIAN_QUOTE_VOL_30D=500_000.0`, `POS_THRESHOLD=0.25`, `TP=0.20`, `SL=-0.12`, `RULE_HOLD_DAYS=14`, `WIN15_THRESHOLD=0.15`.

- [ ] **Step 1: Write the failing test**

```python
def test_pos_in_30d_range_y_forward():
    import numpy as np
    # serie monótona creciente 40 días → pos≈1 (cierra en el techo del rango)
    idx = pd.date_range("2021-01-01", periods=40, freq="1D", tz="UTC")
    close = pd.Series(np.arange(1.0, 41.0), index=idx)
    df = pd.DataFrame({"open": close, "high": close*1.01, "low": close*0.99,
                       "close": close, "volume": 1e6})
    df = cs.compute_features(df)
    # en una serie creciente, el último cierre está cerca del máximo del rango 30d
    assert df["pos_in_30d_range"].iloc[35] > 0.9
    # max_fwd_7d en t = (max high de t+1..t+7 - open_{t+1}) / open_{t+1} > 0 en serie creciente
    assert df["max_fwd_7d"].iloc[10] > 0

def test_rule_return_sl_primero():
    import numpy as np
    idx = pd.date_range("2021-01-01", periods=20, freq="1D", tz="UTC")
    # entrada en t+1 = open=100; al día siguiente toca SL (low=80 < 88) y TP (high=130>120) → SL primero
    o = [100.0]*20; h=[101.0]*20; l=[99.0]*20; c=[100.0]*20
    o[1]=100.0; h[2]=130.0; l[2]=80.0
    df = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume":[1e6]*20}, index=idx)
    df = cs.compute_features(df)
    assert abs(df["rule_return"].iloc[1] - (-0.12)) < 1e-9   # SL primero (conservador)
```

- [ ] **Step 2: Run to verify it fails** — `AttributeError: ... 'compute_features'`.

- [ ] **Step 3: Implement**

`wilder_rsi`, `compute_features` (las columnas pos/rsi/sma/consol/vol_ratio/forward) y `_compute_rule_return` se **copian VERBATIM** de `data/retune/2026-06-18-setup-edge-multiregimen/edge_study.py` (líneas 203-326 para `wilder_rsi`/`compute_features`/`_compute_rule_return`), con UNA diferencia: el gate de vida usa `quote_vol` ya presente en el DataFrame diario (de `load_spot_daily`) en vez de la columna `quote_vol` original de Binance — el nombre de columna es el mismo (`quote_vol`), así que el código de `compute_features` funciona sin cambios. Añadir `ret_30d`:

```python
def compute_features(df):
    # ... (cuerpo VERBATIM de edge_study.py compute_features, líneas 217-283) ...
    # añadir ret_30d para outperf del régimen:
    close = df["close"]
    df["ret_30d"] = (close - close.shift(30)) / close.shift(30)
    return df
```

Las constantes (`MIN_MEDIAN_QUOTE_VOL_30D`, `POS_THRESHOLD`, `TP`, `SL`, `RULE_HOLD_DAYS`, `WIN15_THRESHOLD`) también verbatim de edge_study.py (líneas 60-72).

**Verificación de consistencia (item abierto):** las fórmulas de `pos_in_30d_range`, `rsi14` (Wilder), `consol_30d`, `vol_ratio` en edge_study.py YA están alineadas con `screener/valley_filter.measure_setup` (mismo origen, spec SP2). El test de Step 1 fija los valores; además, añadir un comentario citando `measure_setup` como la definición canónica.

- [ ] **Step 4: Run to verify it passes** — PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add data/retune/2026-06-23-calibracion-gate-regimen/
git commit -m "feat(calib): features ex-ante + forward (verbatim de edge_study, + ret_30d)"
```

---

## Task 4: Régimen de 3 componentes (reusa compose_regime)

**Files:**
- Modify: `data/retune/2026-06-23-calibracion-gate-regimen/calib_study.py`
- Test: `data/retune/2026-06-23-calibracion-gate-regimen/test_calib_study.py`

**Interfaces:**
- Consumes: el panel largo (concat de los DataFrames con features) + la Series BTC.D.
- Produces: `regime_by_date(panel: pandas.DataFrame, btc_dom: pandas.Series, thresholds: dict | None) -> pandas.Series` — index=fecha, valor=estado ∈ {`alts`,`mixto`,`btc`}, computado **reusando `regime.alt_season.compose_regime`** por fecha.

**Decisión (item abierto resuelto):** REUSAR `compose_regime` directamente (no espejo). Firma confirmada: `compose_regime(alt_contribs, btc_ret_30d, btc_dominance, coverage_ratio, thresholds=None)` donde `alt_contribs = [{"above_sma50": bool, "ret_30d": float}, ...]`. Por fecha: construir `alt_contribs` de las alts vivas (no BTC), `btc_ret_30d` de BTCUSDT, `btc_dominance` del CSV, `coverage_ratio = n_evaluadas/n_universo`.

- [ ] **Step 1: Write the failing test**

```python
def test_regime_by_date_reusa_compose():
    import numpy as np
    # construir un panel de 1 fecha: 5 alts todas sobre SMA50 con ret_30d alto, BTC ret bajo,
    # dominancia baja → debe votar 'alts'
    d = pd.Timestamp("2022-01-01", tz="UTC")
    rows = []
    for i in range(5):
        rows.append({"date": d, "symbol": f"A{i}USDT", "above_sma50": True,
                     "ret_30d": 0.30, "alive": True, "close": 1.0, "sma50": 0.5})
    rows.append({"date": d, "symbol": "BTCUSDT", "above_sma50": True,
                 "ret_30d": 0.02, "alive": True, "close": 1.0, "sma50": 0.5})
    panel = pd.DataFrame(rows)
    btc_dom = pd.Series([0.40], index=[d])   # dominancia baja → lean alts
    reg = cs.regime_by_date(panel, btc_dom, thresholds=None)
    assert reg.loc[d] == "alts"
```

- [ ] **Step 2: Run to verify it fails** — `AttributeError: ... 'regime_by_date'`.

- [ ] **Step 3: Implement**

```python
def regime_by_date(panel, btc_dom, thresholds=None):
    from regime.alt_season import compose_regime
    BTC = "BTCUSDT"
    out = {}
    universe_by_date = panel.groupby("date")
    for date, g in universe_by_date:
        alive = g[g["alive"]]
        n_universe = len(g)
        n_eval = len(alive)
        coverage_ratio = (n_eval / n_universe) if n_universe else 0.0
        alts = alive[alive["symbol"] != BTC]
        alt_contribs = [
            {"above_sma50": bool(r.above_sma50), "ret_30d": float(r.ret_30d)}
            for r in alts.itertuples() if pd.notna(r.ret_30d)
        ]
        btc_row = alive[alive["symbol"] == BTC]
        btc_ret_30d = float(btc_row["ret_30d"].iloc[0]) if len(btc_row) and pd.notna(btc_row["ret_30d"].iloc[0]) else None
        dom = btc_dom.get(date, None)
        dom = float(dom) if dom is not None and pd.notna(dom) else None
        res = compose_regime(alt_contribs, btc_ret_30d, dom, coverage_ratio, thresholds=thresholds)
        out[date] = res["estado"]
    return pd.Series(out, name="regime")
```

- [ ] **Step 4: Run to verify it passes** — PASS.

- [ ] **Step 5: Commit**

```bash
git add data/retune/2026-06-23-calibracion-gate-regimen/
git commit -m "feat(calib): régimen de 3 componentes por fecha (reusa compose_regime)"
```

---

## Task 5: Selección + cell stats + Mann-Whitney + criterio de aceptación

**Files:**
- Modify: `data/retune/2026-06-23-calibracion-gate-regimen/calib_study.py`
- Test: `data/retune/2026-06-23-calibracion-gate-regimen/test_calib_study.py`

**Interfaces:**
- Produces:
  - `select_rule_minimal(panel) -> mask` (`alive AND pos_in_30d_range ≤ 0.25`).
  - `cell_stats(rows) -> dict` y `mann_whitney(s, b) -> dict` (verbatim de edge_study.py).
  - `evaluate_acceptance(by_estado: dict, b2_stats: dict) -> dict` con `{"verdict": "PASA"|"NO_PASA"|"INVERTIDO", "delta_pp": float, "p_value": float, "rule_return_inverts": bool, "razon": str}`.

**Criterio pre-comprometido (verbatim del spec):** `MARGEN_PP = 2.0`, `P_MAX = 0.01`.
- PASA si: `delta = median(max_fwd_14d|alts) − median(|btc) ≥ +2pp` Y `median(max_fwd_14d|btc) < B2` Y MW one-sided (`alts>btc`) `p<0.01` Y la dirección NO se invierte en `rule_return`.
- INVERTIDO si: la separación significativa va al revés (`median(|btc) − median(|alts) ≥ +2pp` con `p<0.01` en `btc>alts`).
- NO_PASA: cualquier otro caso.

- [ ] **Step 1: Write the failing test**

```python
def _stats(median, n=500):
    return {"n": n, "median_max_fwd_14d": median, "mean_max_fwd_14d": median,
            "median_rule_return": median, "win15": 0.4}

def test_acceptance_pasa():
    by = {
        "alts": {"max_fwd_14d": [0.15]*500, "rule_return": [0.10]*500, "stats": _stats(0.15)},
        "btc":  {"max_fwd_14d": [0.10]*500, "rule_return": [0.05]*500, "stats": _stats(0.10)},
    }
    b2 = _stats(0.13)  # btc(0.10) < b2(0.13) ✓
    r = cs.evaluate_acceptance(by, b2)
    assert r["verdict"] == "PASA"   # delta=+5pp, btc<b2, alts>btc significativo, rr no invierte

def test_acceptance_invertido():
    by = {
        "alts": {"max_fwd_14d": [0.08]*500, "rule_return": [0.03]*500, "stats": _stats(0.08)},
        "btc":  {"max_fwd_14d": [0.14]*500, "rule_return": [0.09]*500, "stats": _stats(0.14)},
    }
    b2 = _stats(0.11)
    r = cs.evaluate_acceptance(by, b2)
    assert r["verdict"] == "INVERTIDO"

def test_acceptance_no_pasa_margen_chico():
    by = {
        "alts": {"max_fwd_14d": [0.111]*500, "rule_return": [0.05]*500, "stats": _stats(0.111)},
        "btc":  {"max_fwd_14d": [0.110]*500, "rule_return": [0.05]*500, "stats": _stats(0.110)},
    }
    b2 = _stats(0.10)
    r = cs.evaluate_acceptance(by, b2)
    assert r["verdict"] == "NO_PASA"   # delta=+0.1pp < 2pp
```

- [ ] **Step 2: Run to verify it fails** — `AttributeError: ... 'evaluate_acceptance'`.

- [ ] **Step 3: Implement**

`cell_stats` y `mann_whitney` se copian VERBATIM de `edge_study.py` (líneas 433-460). `select_rule_minimal` verbatim (líneas 373-374). Añadir:

```python
MARGEN_PP = 2.0
P_MAX = 0.01

def evaluate_acceptance(by_estado: dict, b2_stats: dict) -> dict:
    alts = by_estado.get("alts", {})
    btc = by_estado.get("btc", {})
    if not alts.get("max_fwd_14d") or not btc.get("max_fwd_14d"):
        return {"verdict": "NO_PASA", "razon": "sin datos en alts o btc", "delta_pp": None,
                "p_value": None, "rule_return_inverts": None}
    med_alts = float(np.median(alts["max_fwd_14d"]))
    med_btc = float(np.median(btc["max_fwd_14d"]))
    delta_pp = (med_alts - med_btc) * 100.0
    mw_fwd = mann_whitney(alts["max_fwd_14d"], btc["max_fwd_14d"])        # alts > btc
    mw_inv = mann_whitney(btc["max_fwd_14d"], alts["max_fwd_14d"])        # btc > alts
    p_fwd = mw_fwd.get("p_value")
    p_inv = mw_inv.get("p_value")
    # rule_return: ¿la dirección se mantiene? (alts >= btc en realizado)
    rr_alts = float(np.median(alts["rule_return"])) if alts.get("rule_return") else None
    rr_btc = float(np.median(btc["rule_return"])) if btc.get("rule_return") else None
    rr_inverts = (rr_alts is not None and rr_btc is not None and rr_alts < rr_btc)
    btc_below_b2 = med_btc < (b2_stats.get("median_max_fwd_14d") or float("inf"))

    if (delta_pp >= MARGEN_PP and btc_below_b2 and p_fwd is not None and p_fwd < P_MAX
            and not rr_inverts):
        verdict, razon = "PASA", "separación direccional + significativa + btc<B2 + rr no invierte"
    elif (delta_pp <= -MARGEN_PP and p_inv is not None and p_inv < P_MAX):
        verdict, razon = "INVERTIDO", "btc le gana a alts con significancia — el gate está al revés"
    else:
        verdict, razon = "NO_PASA", "no se cumple el criterio pre-comprometido"
    return {"verdict": verdict, "delta_pp": delta_pp, "p_value": p_fwd,
            "p_value_invertido": p_inv, "rule_return_inverts": rr_inverts,
            "btc_below_b2": btc_below_b2, "razon": razon}
```

- [ ] **Step 4: Run to verify it passes** — PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add data/retune/2026-06-23-calibracion-gate-regimen/
git commit -m "feat(calib): selección + stats + criterio de aceptación pre-comprometido"
```

---

## Task 6: Main + salidas + grid exploratorio

**Files:**
- Modify: `data/retune/2026-06-23-calibracion-gate-regimen/calib_study.py`
- Test: `data/retune/2026-06-23-calibracion-gate-regimen/test_calib_study.py`

**Interfaces:**
- Produces: `run_study(db_path, btc_dom_csv) -> dict` (orquesta todo, devuelve el dict de results) y `main()` (escribe `results.json` + `findings.md`). `grid_search(panel, btc_dom) -> list[dict]` (exploratorio).

- [ ] **Step 1: Write the failing test (smoke end-to-end sintético)**

```python
def test_run_study_smoke(tmp_path, monkeypatch):
    # panel sintético mínimo en sqlite + CSV BTC.D → run_study produce un dict con la forma esperada
    dbp = _mk_panel_multi(tmp_path)   # helper: BTCUSDT + varias alts, >120 barras diarias, 2 regímenes
    csvp = tmp_path / "dom.csv"
    # dominancia que cae (alts) y sube (btc) en distintos tramos
    dates = pd.date_range("2021-01-01", periods=200, freq="1D")
    dom = ["date,dominance"] + [f"{d.date()},{45 if i<100 else 60}" for i, d in enumerate(dates)]
    csvp.write_text("\n".join(dom))
    res = cs.run_study(str(dbp), str(csvp))
    assert "verdict" in res and res["verdict"] in ("PASA", "NO_PASA", "INVERTIDO")
    assert "by_estado" in res and "production_thresholds" in res
    assert "grid_exploratory" in res
```

(El helper `_mk_panel_multi` construye un sqlite con BTCUSDT + ~10 alts, ≥150 barras diarias-equivalentes en 1h, para que haya forward 14d y régimen evaluable. Construirlo de forma determinista.)

- [ ] **Step 2: Run to verify it fails** — `AttributeError: ... 'run_study'`.

- [ ] **Step 3: Implement `run_study` + `grid_search` + `main`**

```python
def _build_panel(symbol_dfs):
    frames = []
    for sym, df in symbol_dfs.items():
        sub = df.copy(); sub["symbol"] = sym
        frames.append(sub.reset_index().rename(columns={"index": "date", "ts": "date"}))
    panel = pd.concat(frames, ignore_index=True)
    if "date" not in panel.columns:
        panel = panel.rename(columns={panel.columns[0]: "date"})
    panel["date"] = pd.to_datetime(panel["date"], utc=True).dt.normalize()
    return panel[(panel["date"] >= SIGNAL_START) & (panel["date"] <= SIGNAL_END)]

def _bucketed(panel, regime_series, mask):
    rows = panel[mask].copy()
    rows = rows.merge(regime_series.rename("regime"), left_on="date", right_index=True, how="left")
    rows = rows.dropna(subset=["max_fwd_14d"])
    by = {}
    for estado, g in rows.groupby("regime"):
        by[estado] = {"max_fwd_14d": g["max_fwd_14d"].dropna().tolist(),
                      "rule_return": g["rule_return"].dropna().tolist(),
                      "stats": cell_stats(g)}
    return by, rows

def run_study(db_path, btc_dom_csv):
    btc_dom = load_btc_dominance(btc_dom_csv)
    symbol_dfs = {s: compute_features(df) for s, df in load_spot_daily(db_path).items()}
    panel = _build_panel(symbol_dfs)
    regime_prod = regime_by_date(panel, btc_dom, thresholds=None)   # umbrales de producción
    m_min = select_rule_minimal(panel)
    by_estado, _ = _bucketed(panel, regime_prod, m_min)
    b2_stats = cell_stats(panel[panel["alive"]].dropna(subset=["max_fwd_14d"]))
    acceptance = evaluate_acceptance(by_estado, b2_stats)
    grid = grid_search(panel, btc_dom)
    return {
        "by_estado": {k: v["stats"] for k, v in by_estado.items()},   # stats por estado a umbrales de producción
        "b2": b2_stats,
        "verdict": acceptance["verdict"],
        "acceptance": acceptance,
        "grid_exploratory": grid,
        "signal_period": [str(SIGNAL_START.date()), str(SIGNAL_END.date())],
        "caveats": [
            "Survivorship: panel retiene delistadas pero su cobertura no es total (187 símbolos del ingest 2026-06-05).",
            "quote_vol derivado = volume × close (≈ quote-vol de Binance).",
            "BTC.D de fuente externa congelada (ver METODOLOGIA §Procedencia).",
            "Retorno en USDT incluye beta de BTC.",
            "Grid-search es EXPLORATORIO (overfitting); la decisión es a umbrales de producción.",
        ],
    }

def grid_search(panel, btc_dom):
    """Exploratorio: varía los umbrales en grilla gruesa, reporta el mejor delta. Cota superior."""
    from regime.alt_season import effective_thresholds
    best = []
    for breadth_alt in (0.55, 0.60, 0.65, 0.70):
        for outperf_alt in (0.0, 0.05, 0.10):
            ov = {"BREADTH_ALT": breadth_alt, "OUTPERF_ALT": outperf_alt}
            reg = regime_by_date(panel, btc_dom, thresholds=effective_thresholds(ov))
            by, _ = _bucketed(panel, reg, select_rule_minimal(panel))
            a = by.get("alts", {}).get("stats", {}).get("median_max_fwd_14d")
            b = by.get("btc", {}).get("stats", {}).get("median_max_fwd_14d")
            if a is not None and b is not None:
                best.append({"overrides": ov, "delta_pp": (a - b) * 100.0,
                             "n_alts": by["alts"]["stats"]["n"], "n_btc": by["btc"]["stats"]["n"]})
    best.sort(key=lambda x: x["delta_pp"], reverse=True)
    return best[:10]
```

Añadir `main()` que llama `run_study` con `_DEFAULT_DB` + el CSV congelado y escribe `results.json` + `findings.md` (veredicto honesto con la tabla por estado + el grid marcado como exploratorio + los caveats). `findings.md` se genera con prosa según `acceptance["verdict"]` (PASA/NO_PASA/INVERTIDO).

- [ ] **Step 4: Run to verify it passes** — PASS (smoke).

- [ ] **Step 5: Correr el estudio real + revisar el veredicto**

```bash
python data/retune/2026-06-23-calibracion-gate-regimen/calib_study.py
```
Leer `findings.md`. El veredicto (PASA / NO_PASA / INVERTIDO) es el output que decide el encendido. **NO encender nada en este plan** — solo producir + leer la evidencia.

- [ ] **Step 6: Commit**

```bash
git add data/retune/2026-06-23-calibracion-gate-regimen/
git commit -m "feat(calib): orquestación + salidas (results.json/findings.md) + grid exploratorio"
```

---

## Verificación final (tras todas las tareas)

- [ ] `python -m pytest data/retune/2026-06-23-calibracion-gate-regimen/test_calib_study.py -v` — todos verdes, pristine.
- [ ] El estudio corrió sobre `program_ohlcv.db` real y produjo `results.json` + `findings.md` con un veredicto.
- [ ] **Disciplina holdout:** confirmar que el período de señales termina ≤ 2025-04-29 (revisar `signal_period` en results.json) y que NO se referenció `data/holdout/`, `open_holdout`, ni `simulate_strategy` en `calib_study.py`.
- [ ] El veredicto se reporta a Samuel para la decisión de encendido (que es OTRO trabajo, fuera de este plan).

## Items diferidos (NO en este plan)

- **Encender el gate** (config.json + `umbral_overrides` + `enabled=true`) — solo si el veredicto es PASA; gobernado por las "Precondiciones de activación" del spec del gate.
- **Validación temporal (enfoque B)** — solo si el grid exploratorio sugiere una región mejor que producción y se quiere refinar antes de encender.
- **Re-correr el estudio de la firma musikito** sobre el panel anti-survivorship (deuda separada).
