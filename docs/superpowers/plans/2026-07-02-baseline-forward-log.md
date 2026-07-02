# Motor forward-log del baseline cured-random — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un baseline vivo de la cura random-curada (ensemble de N semillas) corriendo en prod como yardstick descriptivo, con freshness owner (#8) y `GET /baseline`.

**Architecture:** Un ensemble de N portafolios paper independientes avanza un día a la vez (escalera viva acumulada + `kill_switch_v2` con pico rodante 180d + picks random reproducibles). Un lifespan thread en `scanner/runtime.py` (patrón `_managed_threads`/`stop_event`) es el dueño de frescura: cada tick avanza el ensemble y persiste el estado con su `generated_at`. `GET /baseline` (router en `api/baseline.py`) lee el estado persistido y lo envuelve en `freshness.LiveSnapshot` — descriptivo, sin surfacear picks.

**Tech Stack:** Python, FastAPI (APIRouter), pandas/numpy no requeridos en runtime (aritmética pura), `strategy.kill_switch_v2`, `freshness.LiveSnapshot`, pytest.

## Global Constraints

- **#8 (freshness owner + LiveSnapshot):** el estado vivo cruza el borde de proceso → dueño nombrado en prod (lifespan thread en `scanner/runtime.py`, registrado en `_managed_threads`, NUNCA un CLI manual) + emitir frescura vía `freshness.LiveSnapshot`. Nada de empty mudo.
- **#4 (RISK_PER_TRADE=0.01 fijo):** el baseline es PAPER; sizing equal-weight `cap/M`. NO toca ni referencia `RISK_PER_TRADE` de producción; sin scalers.
- **#3 (holdout bloqueado):** el motor vive forward (hoy → adelante). NUNCA `open_holdout`/`simulate_strategy`/`data/holdout/`.
- **Anti-veredicto:** el read describe un yardstick ("el baseline va en X"), nunca ordena. Los picks individuales del día NO se surfacean.
- **Escalera CONGELADA (verbatim):** `TPS=[0.15,0.30,0.50,0.90]`, `FRACS=[0.25,0.25,0.20,0.15]`, `DISASTER=-0.50`, `HORIZON=30`.
- **Constantes del baseline:** `N_SEEDS=30`, `M=20`, `PEAK_WIN=180`, `BASELINE_AGGRESSIVENESS=100`.
- Entra a CI. Gate rápido: `python -m pytest tests/ -m "not network" -n auto -q`.

---

### Task 1: Escalera congelada (aritmética pura)

**Files:**
- Create: `scanner/baseline/__init__.py` (vacío)
- Create: `scanner/baseline/ladder.py`
- Test: `tests/test_baseline_ladder.py`

**Interfaces:**
- Produces: `ladder_return(entry: float, hi_max: float, lo_min: float, close_last: float) -> float | None` — retorno realizado de la escalera dado el máximo alto, mínimo bajo y cierre final de la ventana. `TPS, FRACS, DISASTER, HORIZON` constantes de módulo.

- [ ] **Step 1: Write the failing test**

`tests/test_baseline_ladder.py`:
```python
from scanner.baseline.ladder import ladder_return, TPS, FRACS, DISASTER, HORIZON


def test_constants_frozen():
    assert TPS == [0.15, 0.30, 0.50, 0.90]
    assert FRACS == [0.25, 0.25, 0.20, 0.15]
    assert DISASTER == -0.50
    assert HORIZON == 30


def test_all_targets_hit_plus_runner():
    # hi_max alcanza el +90% => vende las 4 fracciones; runner (0.15 restante) al close +100%
    r = ladder_return(entry=100.0, hi_max=200.0, lo_min=95.0, close_last=200.0)
    realized = 0.25*0.15 + 0.25*0.30 + 0.20*0.50 + 0.15*0.90
    runner = (1.0 - 0.85) * (200.0 - 100.0) / 100.0
    assert abs(r - (realized + runner)) < 1e-9


def test_disaster_floor_when_no_target():
    # nunca toca +15% y el low perfora -50% => piso -0.50
    r = ladder_return(entry=100.0, hi_max=110.0, lo_min=40.0, close_last=45.0)
    assert r == DISASTER


def test_no_target_no_disaster_is_runner_close():
    # no toca ningún target ni el piso => runner completo al close
    r = ladder_return(entry=100.0, hi_max=110.0, lo_min=90.0, close_last=105.0)
    assert abs(r - 0.05) < 1e-9


def test_guards_return_none():
    assert ladder_return(0.0, 1.0, 1.0, 1.0) is None
    assert ladder_return(100.0, 100.0, 100.0, None) is None


def test_live_accumulation_equals_oneshot():
    # contrato escalera VIVA ↔ un-tiro: acumular hi_max/lo_min día a día sobre la
    # ventana da EXACTAMENTE lo mismo que ladder_return sobre los extremos completos
    highs = [100 + i for i in range(HORIZON)]      # sube a 129
    lows = [90 - (i % 5) for i in range(HORIZON)]
    close_last, entry = 125.0, 100.0
    oneshot = ladder_return(entry, max(highs), min(lows), close_last)
    hi_max, lo_min = highs[0], lows[0]
    for i in range(1, HORIZON):                    # acumulación como PaperPortfolio
        hi_max = max(hi_max, highs[i]); lo_min = min(lo_min, lows[i])
    assert ladder_return(entry, hi_max, lo_min, close_last) == oneshot
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_baseline_ladder.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'scanner.baseline'`.

- [ ] **Step 3: Write minimal implementation**

`scanner/baseline/__init__.py`: archivo vacío.

`scanner/baseline/ladder.py`:
```python
"""Escalera de salida CONGELADA (verbatim del estudio curar_azar / confirm_study).
Aritmética pura: vende fracciones en targets ascendentes si el máximo alto los tocó;
piso -50% si ningún target y el mínimo bajo lo perforó; runner al cierre final.
Producción posee su copia (no importa de data/retune/)."""
from __future__ import annotations

TPS = [0.15, 0.30, 0.50, 0.90]
FRACS = [0.25, 0.25, 0.20, 0.15]
DISASTER = -0.50
HORIZON = 30


def ladder_return(entry: float, hi_max: float, lo_min: float,
                  close_last: float | None) -> float | None:
    """Retorno realizado de la escalera. `hi_max`/`lo_min` = extremos de la ventana
    [entry+1 .. entry+HORIZON]; `close_last` = cierre del último día. None si inválido."""
    if entry is None or entry <= 0 or close_last is None:
        return None
    realized = 0.0
    sold = 0.0
    for tp, fr in zip(TPS, FRACS):
        if hi_max >= entry * (1 + tp):
            realized += fr * tp
            sold += fr
        else:
            break
    if sold == 0.0 and lo_min <= entry * (1 + DISASTER):
        return DISASTER
    runner_frac = 1.0 - sold
    runner_ret = (close_last - entry) / entry
    return realized + runner_frac * runner_ret
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_baseline_ladder.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add scanner/baseline/__init__.py scanner/baseline/ladder.py tests/test_baseline_ladder.py
git commit -m "feat(baseline): escalera congelada (aritmetica pura)"
```

---

### Task 2: PaperPortfolio — un portafolio paper avanza un día

**Files:**
- Create: `scanner/baseline/ensemble.py`
- Test: `tests/test_baseline_portfolio.py`

**Interfaces:**
- Consumes: `ladder_return, TPS, FRACS, DISASTER, HORIZON` (Task 1); `strategy.kill_switch_v2.evaluate_portfolio_tier(portfolio_dd: float, concurrent_failures: int, cfg: dict) -> dict` (retorna `{"tier": str, ...}`, tier ∈ NORMAL/WARNED/REDUCED/FROZEN).
- Produces:
  - Constantes: `N_SEEDS=30, M=20, PEAK_WIN=180, BASELINE_AGGRESSIVENESS=100`, `_KS_CFG`, `SF`.
  - `seed_pick(universe: list[str], date: str, seed: int, k: int) -> list[str]` — k símbolos reproducibles.
  - `class PaperPortfolio` con `cap: float`, `eq: list[float]`, `open_pos: list[dict]`, y `advance_day(date: str, bars: dict[str, dict], universe: list[str], seed: int) -> None`. `bars[symbol]` = `{"open","high","low","close"}`.

- [ ] **Step 1: Write the failing test**

`tests/test_baseline_portfolio.py`:
```python
from scanner.baseline.ensemble import PaperPortfolio, seed_pick, M
from scanner.baseline.ladder import HORIZON


def _bars(universe, price):
    return {s: {"open": price, "high": price, "low": price, "close": price} for s in universe}


def test_seed_pick_reproducible_and_seeded():
    uni = [f"S{i}" for i in range(50)]
    assert seed_pick(uni, "2026-07-02", 3, M) == seed_pick(uni, "2026-07-02", 3, M)
    # semillas distintas -> selección distinta (con universo grande)
    assert seed_pick(uni, "2026-07-02", 3, M) != seed_pick(uni, "2026-07-02", 7, M)


def test_flat_market_returns_zero_pnl():
    # precio plano 30+ días => cada posición realiza runner 0% => cap vuelve a 1.0
    uni = [f"S{i}" for i in range(50)]
    p = PaperPortfolio()
    for d in range(HORIZON + 2):
        p.advance_day(f"2026-07-{d+1:02d}", _bars(uni, 100.0), uni, seed=1)
    assert abs(p.cap - 1.0) < 1e-6
    assert p.open_pos == [] or all(pp["bars_left"] > 0 for pp in p.open_pos)


def test_frozen_tier_blocks_new_entries():
    # forzar drawdown fuerte -> el kill-switch (agresivo) congela -> no abre nuevas
    uni = [f"S{i}" for i in range(50)]
    p = PaperPortfolio()
    p.cap = 1.0
    p.eq = [1.0, 1.0, 1.0]
    # inyectar una caída del 20% respecto al pico rodante
    p.cap = 0.80
    p.advance_day("2026-08-01", _bars(uni, 100.0), uni, seed=1)
    # con cap 0.80 vs pico ~1.0 => dd -20% <= frozen (~-10.5%) => FROZEN => sin nuevas
    assert len(p.open_pos) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_baseline_portfolio.py -v`
Expected: FAIL con `ImportError` (PaperPortfolio no existe).

- [ ] **Step 3: Write minimal implementation**

`scanner/baseline/ensemble.py`:
```python
"""Ensemble de portafolios paper de la cura random-curada. Avanza un día a la vez
(escalera viva acumulada + kill_switch_v2 pico-rodante-180d + picks random reproducibles).
PAPER: sizing equal-weight cap/M, NO toca RISK_PER_TRADE (#4). Puro: sin red, sin DB."""
from __future__ import annotations

import hashlib
import statistics

from scanner.baseline.ladder import ladder_return, HORIZON
# NB: `strategy.kill_switch_v2` se importa LAZY dentro de _tier() para respetar las
# reglas de frontera scanner/ (mismo motivo que los lazy imports en scanner/runtime.py).

N_SEEDS = 30
M = 20
PEAK_WIN = 180
BASELINE_AGGRESSIVENESS = 100
# cfg mínimo para kill_switch_v2: usa los rangos DD por defecto + aggressiveness fija
_KS_CFG = {"kill_switch": {"v2": {"aggressiveness": BASELINE_AGGRESSIVENESS}}}
SF = {"NORMAL": 1.0, "WARNED": 1.0, "REDUCED": 0.5, "FROZEN": 0.0}


def seed_pick(universe: list[str], date: str, seed: int, k: int) -> list[str]:
    """k símbolos reproducibles por (date, seed): rotación determinista del universo."""
    if not universe:
        return []
    uni = sorted(universe)
    off = int(hashlib.sha256(f"{date}|{seed}".encode()).hexdigest(), 16) % len(uni)
    rot = uni[off:] + uni[:off]
    return rot[:k]


class PaperPortfolio:
    """Un portafolio paper (una semilla). Estado serializable vía to_dict/from_dict."""

    def __init__(self) -> None:
        self.cap: float = 1.0
        self.eq: list[float] = []
        # cada posición: {"symbol","entry","notional","hi_max","lo_min","bars_left"}
        self.open_pos: list[dict] = []

    def _tier(self) -> str:
        from strategy.kill_switch_v2 import evaluate_portfolio_tier  # lazy: frontera scanner/
        window = self.eq[-(PEAK_WIN - 1):] + [self.cap]
        peak = max(window) if window else self.cap
        dd = -(1.0 - self.cap / peak) if peak > 0 else 0.0  # negativo en drawdown
        return evaluate_portfolio_tier(dd, 0, _KS_CFG)["tier"]

    def advance_day(self, date: str, bars: dict[str, dict],
                    universe: list[str], seed: int) -> None:
        # 1) marcar posiciones abiertas con la barra de hoy + realizar las que maduran
        still = []
        for pp in self.open_pos:
            bar = bars.get(pp["symbol"])
            if bar is not None:
                pp["hi_max"] = max(pp["hi_max"], bar["high"])
                pp["lo_min"] = min(pp["lo_min"], bar["low"])
                pp["bars_left"] -= 1
                if pp["bars_left"] <= 0:
                    r = ladder_return(pp["entry"], pp["hi_max"], pp["lo_min"], bar["close"])
                    self.cap += pp["notional"] * (r if r is not None else 0.0)
                    continue
            still.append(pp)
        self.open_pos = still
        # 2) tier del kill-switch (pico rodante)
        sf = SF[self._tier()]
        # 3) abrir picks random si hay slots libres y no está FROZEN
        free = M - len(self.open_pos)
        if free > 0 and sf > 0.0:
            held = {pp["symbol"] for pp in self.open_pos}
            alive = [s for s in universe if s in bars and s not in held]
            for sym in seed_pick(alive, date, seed, free):
                bar = bars[sym]
                self.open_pos.append({
                    "symbol": sym, "entry": bar["open"],
                    "notional": (self.cap / M) * sf,
                    "hi_max": bar["high"], "lo_min": bar["low"], "bars_left": HORIZON,
                })
        self.eq.append(self.cap)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_baseline_portfolio.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add scanner/baseline/ensemble.py tests/test_baseline_portfolio.py
git commit -m "feat(baseline): PaperPortfolio avanza un dia (escalera viva + kill-switch + picks)"
```

---

### Task 3: BaselineEnsemble — N semillas + snapshot distribucional

**Files:**
- Modify: `scanner/baseline/ensemble.py`
- Test: `tests/test_baseline_ensemble.py`

**Interfaces:**
- Consumes: `PaperPortfolio, seed_pick, N_SEEDS, M` (Task 2).
- Produces: `class BaselineEnsemble` con:
  - `__init__(self, n_seeds: int = N_SEEDS)`
  - `last_date: str | None`
  - `advance_day(self, date: str, bars: dict[str, dict], universe: list[str]) -> None` — idempotente por fecha.
  - `snapshot(self) -> dict` — `{"mediana","banda_p10","banda_p90","n_seeds","tier_mediana","last_date"}`. SIN picks.

- [ ] **Step 1: Write the failing test**

`tests/test_baseline_ensemble.py`:
```python
from scanner.baseline.ensemble import BaselineEnsemble


def _bars(universe, price):
    return {s: {"open": price, "high": price, "low": price, "close": price} for s in universe}


def test_snapshot_shape_no_picks():
    uni = [f"S{i}" for i in range(60)]
    e = BaselineEnsemble(n_seeds=10)
    e.advance_day("2026-07-02", _bars(uni, 100.0), uni)
    snap = e.snapshot()
    assert set(snap) == {"mediana", "banda_p10", "banda_p90", "n_seeds", "tier_mediana", "last_date"}
    assert snap["n_seeds"] == 10
    assert snap["last_date"] == "2026-07-02"
    # anti-veredicto: el snapshot NO expone picks/símbolos
    import json
    assert "symbol" not in json.dumps(snap).lower()


def test_advance_day_idempotent_by_date():
    uni = [f"S{i}" for i in range(60)]
    e = BaselineEnsemble(n_seeds=5)
    e.advance_day("2026-07-02", _bars(uni, 100.0), uni)
    snap1 = e.snapshot()
    e.advance_day("2026-07-02", _bars(uni, 100.0), uni)  # misma fecha => no-op
    assert e.snapshot() == snap1


def test_band_ordering():
    uni = [f"S{i}" for i in range(60)]
    e = BaselineEnsemble(n_seeds=20)
    for d in range(3):
        e.advance_day(f"2026-07-{d+2:02d}", _bars(uni, 100.0), uni)
    snap = e.snapshot()
    assert snap["banda_p10"] <= snap["mediana"] <= snap["banda_p90"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_baseline_ensemble.py -v`
Expected: FAIL con `ImportError: cannot import name 'BaselineEnsemble'`.

- [ ] **Step 3: Write minimal implementation**

Añadir al final de `scanner/baseline/ensemble.py`:
```python
def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


class BaselineEnsemble:
    """N portafolios paper independientes; emite la DISTRIBUCIÓN, no un camino."""

    def __init__(self, n_seeds: int = N_SEEDS) -> None:
        self.seeds = list(range(n_seeds))
        self.portfolios = [PaperPortfolio() for _ in self.seeds]
        self.last_date: str | None = None

    def advance_day(self, date: str, bars: dict[str, dict], universe: list[str]) -> None:
        if self.last_date is not None and date <= self.last_date:
            return  # idempotente / monotónico por fecha
        for seed, p in zip(self.seeds, self.portfolios):
            p.advance_day(date, bars, universe, seed)
        self.last_date = date

    def snapshot(self) -> dict:
        caps = sorted(p.cap for p in self.portfolios)
        tiers = sorted(p._tier() for p in self.portfolios)
        return {
            "mediana": statistics.median(caps) if caps else 1.0,
            "banda_p10": _percentile(caps, 0.10),
            "banda_p90": _percentile(caps, 0.90),
            "n_seeds": len(self.portfolios),
            "tier_mediana": tiers[len(tiers) // 2] if tiers else "NORMAL",
            "last_date": self.last_date,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_baseline_ensemble.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add scanner/baseline/ensemble.py tests/test_baseline_ensemble.py
git commit -m "feat(baseline): BaselineEnsemble N semillas + snapshot distribucional (sin picks)"
```

---

### Task 4: Persistencia — sobrevivir reinicios

**Files:**
- Create: `scanner/baseline/store.py`
- Modify: `scanner/baseline/ensemble.py` (añadir `to_dict`/`from_dict` a `PaperPortfolio` y `BaselineEnsemble`)
- Test: `tests/test_baseline_store.py`

**Interfaces:**
- Consumes: `BaselineEnsemble` (Task 3).
- Produces:
  - `PaperPortfolio.to_dict() -> dict` / `PaperPortfolio.from_dict(d) -> PaperPortfolio`
  - `BaselineEnsemble.to_dict() -> dict` / `BaselineEnsemble.from_dict(d) -> BaselineEnsemble`
  - `scanner/baseline/store.py`: `persist(ensemble, generated_at: str, path: str = _DEFAULT_PATH) -> None` (escritura atómica tmp+rename); `load(path: str = _DEFAULT_PATH) -> tuple[BaselineEnsemble | None, str | None]` (retorna `(ensemble, generated_at)`; `(None, None)` si no existe).

- [ ] **Step 1: Write the failing test**

`tests/test_baseline_store.py`:
```python
import os
from scanner.baseline.ensemble import BaselineEnsemble
from scanner.baseline import store


def _bars(universe, price):
    return {s: {"open": price, "high": price, "low": price, "close": price} for s in universe}


def test_persist_load_roundtrip(tmp_path):
    uni = [f"S{i}" for i in range(60)]
    e = BaselineEnsemble(n_seeds=8)
    e.advance_day("2026-07-02", _bars(uni, 100.0), uni)
    p = str(tmp_path / "state.json")
    store.persist(e, "2026-07-02T00:00:00Z", path=p)
    e2, gen = store.load(path=p)
    assert gen == "2026-07-02T00:00:00Z"
    assert e2.last_date == "2026-07-02"
    assert e2.snapshot() == e.snapshot()


def test_load_missing_returns_none(tmp_path):
    assert store.load(path=str(tmp_path / "nope.json")) == (None, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_baseline_store.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'scanner.baseline.store'`.

- [ ] **Step 3: Write minimal implementation**

Añadir a `scanner/baseline/ensemble.py` (métodos de serialización):
```python
# --- serialización (añadir dentro de PaperPortfolio) ---
    def to_dict(self) -> dict:
        return {"cap": self.cap, "eq": self.eq, "open_pos": self.open_pos}

    @classmethod
    def from_dict(cls, d: dict) -> "PaperPortfolio":
        p = cls()
        p.cap = d["cap"]
        p.eq = list(d["eq"])
        p.open_pos = [dict(x) for x in d["open_pos"]]
        return p
```
```python
# --- serialización (añadir dentro de BaselineEnsemble) ---
    def to_dict(self) -> dict:
        return {"seeds": self.seeds, "last_date": self.last_date,
                "portfolios": [p.to_dict() for p in self.portfolios]}

    @classmethod
    def from_dict(cls, d: dict) -> "BaselineEnsemble":
        e = cls(n_seeds=len(d["seeds"]))
        e.seeds = list(d["seeds"])
        e.last_date = d["last_date"]
        e.portfolios = [PaperPortfolio.from_dict(x) for x in d["portfolios"]]
        return e
```

`scanner/baseline/store.py`:
```python
"""Persistencia del ensemble para sobrevivir reinicios (replay determinista).
Escritura atómica (tmp + rename). El generated_at persistido alimenta la frescura."""
from __future__ import annotations

import json
import os

from scanner.baseline.ensemble import BaselineEnsemble

_DEFAULT_PATH = os.path.join("data", "baseline_state.json")


def persist(ensemble: BaselineEnsemble, generated_at: str, path: str = _DEFAULT_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = {"generated_at": generated_at, "ensemble": ensemble.to_dict()}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp, path)  # atómico


def load(path: str = _DEFAULT_PATH) -> tuple[BaselineEnsemble | None, str | None]:
    if not os.path.exists(path):
        return None, None
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    return BaselineEnsemble.from_dict(payload["ensemble"]), payload.get("generated_at")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_baseline_store.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add scanner/baseline/ensemble.py scanner/baseline/store.py tests/test_baseline_store.py
git commit -m "feat(baseline): persistencia atomica del ensemble (reinicio determinista)"
```

---

### Task 5: GET /baseline — LiveSnapshot descriptivo, sin picks

**Files:**
- Create: `api/baseline.py`
- Modify: `btc_api.py` (import `baseline_router` + `app.include_router(baseline_router)`)
- Test: `tests/test_baseline_endpoint.py`

**Interfaces:**
- Consumes: `scanner.baseline.store.load` (Task 4); `freshness.LiveSnapshot(payload: dict, generated_at: str | None, umbral_seg: float)` con `.to_response() -> dict` que inyecta `frescura`.
- Produces: `baseline_router: APIRouter` con `GET /baseline`. Respuesta = `to_response()` del snapshot + `{"nota": <copy anti-veredicto>}`. `_UMBRAL_SEG = 26*3600`.

- [ ] **Step 1: Write the failing test**

`tests/test_baseline_endpoint.py`:
```python
from unittest.mock import patch
import api.baseline as baseline_mod
from scanner.baseline.ensemble import BaselineEnsemble


def _ensemble():
    uni = [f"S{i}" for i in range(60)]
    e = BaselineEnsemble(n_seeds=6)
    bars = {s: {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0} for s in uni}
    e.advance_day("2026-07-02", bars, uni)
    return e


def test_baseline_fresco_carries_frescura_no_picks():
    with patch.object(baseline_mod, "load",
                      return_value=(_ensemble(), "2026-07-02T00:00:00Z")):
        out = baseline_mod.get_baseline()
    assert "frescura" in out
    assert out["frescura"]["estado"] in ("fresco", "rancio")  # depende de la hora del run
    assert out["n_seeds"] == 6
    assert "nota" in out                      # copy anti-veredicto presente
    assert "symbol" not in str(out).lower()   # picks NO surfaceados


def test_baseline_muerto_when_no_state():
    with patch.object(baseline_mod, "load", return_value=(None, None)):
        out = baseline_mod.get_baseline()
    assert out["frescura"]["estado"] == "muerto"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_baseline_endpoint.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'api.baseline'`.

- [ ] **Step 3: Write minimal implementation**

`api/baseline.py`:
```python
"""GET /baseline — yardstick descriptivo del baseline cured-random (anti-veredicto).
Lee el estado persistido por el freshness owner y lo envuelve en LiveSnapshot. NO
surfacea picks individuales (se leerían como señal); solo el agregado distribucional."""
from __future__ import annotations

from fastapi import APIRouter

from freshness import LiveSnapshot
from scanner.baseline.store import load

baseline_router = APIRouter()
_UMBRAL_SEG = 26 * 3600  # ~26h: un tick diario sano queda fresco; owner muerto -> muerto

_NOTA = ("Yardstick descriptivo del azar curado (entrada random + escalera + freno de "
         "drawdown). Mide contra qué compararte; no es una señal ni te dice qué comprar.")


def get_baseline() -> dict:
    ensemble, generated_at = load()
    payload = ensemble.snapshot() if ensemble is not None else {
        "mediana": None, "banda_p10": None, "banda_p90": None,
        "n_seeds": 0, "tier_mediana": None, "last_date": None,
    }
    payload["nota"] = _NOTA
    return LiveSnapshot(payload=payload, generated_at=generated_at,
                        umbral_seg=_UMBRAL_SEG).to_response()


@baseline_router.get("/baseline", summary="Baseline cured-random (yardstick descriptivo)")
def baseline_endpoint() -> dict:
    return get_baseline()
```

En `btc_api.py`, junto a los otros imports de routers (cerca de la línea 100-109) añadir:
```python
from api.baseline import baseline_router
```
Y junto a los `app.include_router(...)` (después de la línea 322 `app.include_router(plan_router)`):
```python
app.include_router(baseline_router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_baseline_endpoint.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add api/baseline.py btc_api.py tests/test_baseline_endpoint.py
git commit -m "feat(baseline): GET /baseline LiveSnapshot descriptivo, sin picks (anti-veredicto)"
```

---

### Task 6: Freshness owner — lifespan thread (#8)

**Files:**
- Modify: `scanner/runtime.py` (añadir `baseline_loop` + registrarlo en `start_scanner_thread`)
- Test: `tests/test_baseline_owner.py`

**Interfaces:**
- Consumes: `BaselineEnsemble` (Task 3), `store.persist`/`store.load` (Task 4), `_fetch_daily_bars` (existente en `api/levels.py`).
- Produces: `baseline_loop(stop_event: threading.Event | None = None) -> None` en `scanner/runtime.py`; un `baseline_thread` (name `"baseline_loop"`) añadido a `_managed_threads` dentro de `start_scanner_thread()`.

- [ ] **Step 1: Write the failing test**

`tests/test_baseline_owner.py`:
```python
import threading
import time
import scanner.runtime as rt


def test_baseline_loop_ticks_and_persists(tmp_path, monkeypatch):
    # universo pequeño + barras fake => sin red
    uni = [f"S{i}" for i in range(40)]
    bars = {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}
    monkeypatch.setattr(rt, "_baseline_universe", lambda: uni)
    monkeypatch.setattr(rt, "_baseline_bar", lambda sym: dict(bars))
    monkeypatch.setattr(rt, "_baseline_today", lambda: "2026-07-02")
    path = str(tmp_path / "state.json")
    monkeypatch.setattr(rt, "_BASELINE_PATH", path)

    ev = threading.Event()
    t = threading.Thread(target=rt.baseline_loop, kwargs={"stop_event": ev}, daemon=True)
    t.start()
    time.sleep(0.5)   # deja correr un ciclo
    ev.set()
    t.join(timeout=3)

    from scanner.baseline.store import load
    ensemble, gen = load(path=path)
    assert ensemble is not None and ensemble.last_date == "2026-07-02"
    assert gen is not None  # generated_at presente => la frescura será 'fresco'


def test_baseline_thread_registered_in_managed():
    # start_scanner_thread debe registrar el baseline_thread para el teardown (#8)
    import inspect
    src = inspect.getsource(rt.start_scanner_thread)
    assert "baseline_loop" in src
    assert "baseline_thread" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_baseline_owner.py -v`
Expected: FAIL con `AttributeError: module 'scanner.runtime' has no attribute 'baseline_loop'`.

- [ ] **Step 3: Write minimal implementation**

En `scanner/runtime.py`, añadir cerca de los otros loops (tras `sync_loop`, ~línea 492) los helpers + el loop. Los helpers son puntos de inyección para el test (evitan red):
```python
# ── Baseline forward-log (yardstick cured-random, #8 freshness owner) ──
from scanner.baseline.ensemble import BaselineEnsemble  # noqa: E402
from scanner.baseline import store as _baseline_store  # noqa: E402

_BASELINE_PATH = _baseline_store._DEFAULT_PATH
_BASELINE_INTERVAL_SEC = 3600  # despierta c/hora; avanza solo si hay día nuevo (idempotente)


def _baseline_universe() -> list[str]:
    """Universo alt vivo trackeado (símbolos activos, sin BTC)."""
    return [s for s in get_active_symbols() if s != "BTCUSDT"]


def _baseline_bar(symbol: str) -> dict | None:
    """Última barra diaria del símbolo, o None si no disponible."""
    from api.levels import _fetch_daily_bars, BinanceUnavailable  # noqa: PLC0415
    try:
        bars = _fetch_daily_bars(symbol)
    except BinanceUnavailable:
        return None
    return bars[-1] if bars else None


def _baseline_today() -> str:
    from datetime import datetime, timezone  # noqa: PLC0415
    return datetime.now(timezone.utc).date().isoformat()


def baseline_loop(stop_event: threading.Event | None = None) -> None:
    """Freshness owner del baseline. Cada hora: si hay día nuevo, avanza el ensemble
    con las barras diarias del universo y persiste con generated_at (#8)."""
    if stop_event is None:
        stop_event = threading.Event()
    while not stop_event.is_set():
        try:
            ensemble, _ = _baseline_store.load(path=_BASELINE_PATH)
            if ensemble is None:
                ensemble = BaselineEnsemble()
            today = _baseline_today()
            if ensemble.last_date is None or today > ensemble.last_date:
                universe = _baseline_universe()
                bars = {s: b for s in universe if (b := _baseline_bar(s)) is not None}
                ensemble.advance_day(today, bars, list(bars.keys()))
                from datetime import datetime, timezone  # noqa: PLC0415
                gen = datetime.now(timezone.utc).isoformat()
                _baseline_store.persist(ensemble, gen, path=_BASELINE_PATH)
                log.info("baseline_loop: avanzado a %s (%d símbolos)", today, len(bars))
        except Exception:  # noqa: BLE001
            log.exception("baseline_loop cycle error (continúa)")
        if stop_event.wait(_BASELINE_INTERVAL_SEC):
            break
    log.info("baseline_loop exiting cleanly")
```

En `start_scanner_thread()`, tras el bloque de `sync_thread` (después de la línea ~576 `_managed_threads.append(sync_thread)`) y antes de `return t`:
```python
    baseline_thread = threading.Thread(
        target=baseline_loop, name="baseline_loop",
        kwargs={"stop_event": _thread_stop_event}, daemon=True,
    )
    baseline_thread.start()
    _managed_threads.append(baseline_thread)
    log.info("Baseline forward-log thread started (freshness owner, hourly)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_baseline_owner.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the fast gate + commit**

Run: `python -m pytest tests/ -m "not network" -n auto -q`
Expected: PASS (toda la suite rápida, incluidos los 6 tests de baseline).

```bash
git add scanner/runtime.py tests/test_baseline_owner.py
git commit -m "feat(baseline): freshness owner lifespan thread (#8) + registro en managed threads"
```

---

## Diferido (post-MVP, NO en este plan)

Comparación operador-vs-baseline: captura/normalización de las decisiones reales del operador (su input lo registra el lifecycle de posiciones vía `PositionClosure`) + scorecard. Se construye cuando el papá haya operado N semanas y el baseline tenga historia. Registrar con `mex log` al cerrar el MVP.
