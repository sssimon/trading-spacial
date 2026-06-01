# Cost-Model Diagnosis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only diagnostic that falsifies the backtest's v2 slippage model against the 27 live trades, sweeps candidate corrections, and emits a pre-registered branch verdict (RE-ANCHOR vs REBUILD).

**Architecture:** Five small offline units under `tools/cost_diagnosis/` plus one prod-data dump step. `live_trades.py` loads a one-time `mode=ro` dump of closed positions; `liquidity.py` rebuilds the backtest's per-minute liquidity proxy from 1H bars; `recompute.py` invokes the unmodified `backtest_costs` engine under baseline + correction parameters; `reconcile.py` applies the pre-registered §3 thresholds; `run.py` wires it together and writes `findings.md` + `per_trade.json`.

**Tech Stack:** Python, pandas, pytest. Reuses `backtest_costs.compute_trade_costs` (no modification) and `backtest.get_cached_data`.

**Spec:** `docs/superpowers/specs/es/2026-06-01-cost-model-diagnosis-design.md`

---

## Prerequisite: dump the live trades (one-time, read-only)

Before running the driver (Task 6), dump the 27 closed positions joined to their
originating scan price. This is the ONLY prod touch — strictly `mode=ro`.

```bash
mkdir -p data/retune/2026-06-01-cost-model-diagnosis
ssh -i ~/.ssh/atrium_aws ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com \
  "sqlite3 'file:/var/www/trading/signals.db?mode=ro' -json \
  \"SELECT p.id, p.symbol, p.direction, p.size_usd, p.qty, p.entry_price, p.entry_ts, \
   p.exit_price, p.exit_ts, p.pnl_usd, p.pnl_pct, p.scan_id, s.price AS scan_price, \
   s.ts AS scan_ts FROM positions p LEFT JOIN scans s ON p.scan_id = s.id \
   WHERE p.status='closed';\"" \
  > data/retune/2026-06-01-cost-model-diagnosis/live_trades.json
```

`sqlite3 -json` emits a JSON array. The unit tests use small fixtures, not this
file, so Tasks 1-5 do not need prod access. Only Task 6's full run does.

---

## Task 1: Package skeleton + live-trade loader

**Files:**
- Create: `tools/cost_diagnosis/__init__.py`
- Create: `tools/cost_diagnosis/live_trades.py`
- Test: `tests/test_cost_diagnosis_live_trades.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cost_diagnosis_live_trades.py
import json
import pytest
from tools.cost_diagnosis.live_trades import load_live_trades, LiveTrade


def _write(tmp_path, rows):
    p = tmp_path / "live.json"
    p.write_text(json.dumps(rows), encoding="utf-8")
    return str(p)


def test_loads_and_parses_rows(tmp_path):
    rows = [{
        "id": 1, "symbol": "AVAXUSDT", "direction": "SHORT", "size_usd": 644.0,
        "qty": 30.0, "entry_price": 21.5, "entry_ts": "2026-05-21T21:21:40+00:00",
        "exit_price": 21.0, "exit_ts": "2026-05-22T03:00:00+00:00", "pnl_usd": 12.3,
        "pnl_pct": 0.9, "scan_id": 99, "scan_price": 21.6, "scan_ts": "2026-05-21T21:00:00+00:00",
    }]
    trades = load_live_trades(_write(tmp_path, rows))
    assert len(trades) == 1
    t = trades[0]
    assert isinstance(t, LiveTrade)
    assert t.symbol == "AVAXUSDT" and t.size_usd == 644.0 and t.scan_price == 21.6


def test_missing_required_field_raises(tmp_path):
    rows = [{"id": 2, "symbol": "BTCUSDT", "direction": "SHORT"}]  # missing prices etc.
    with pytest.raises(ValueError, match="missing"):
        load_live_trades(_write(tmp_path, rows))


def test_null_scan_price_is_allowed(tmp_path):
    rows = [{
        "id": 3, "symbol": "BTCUSDT", "direction": "SHORT", "size_usd": 644.0,
        "qty": 0.01, "entry_price": 60000.0, "entry_ts": "2026-05-21T00:00:00+00:00",
        "exit_price": 60100.0, "exit_ts": "2026-05-21T02:00:00+00:00", "pnl_usd": -1.2,
        "pnl_pct": -0.1, "scan_id": None, "scan_price": None, "scan_ts": None,
    }]
    trades = load_live_trades(_write(tmp_path, rows))
    assert trades[0].scan_price is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cost_diagnosis_live_trades.py -v`
Expected: FAIL with `ModuleNotFoundError: tools.cost_diagnosis.live_trades`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/cost_diagnosis/__init__.py
```

```python
# tools/cost_diagnosis/live_trades.py
"""Read-only loader for the one-time mode=ro dump of closed live positions.

The dump is produced by the prerequisite ssh+sqlite3 command (see plan). This
module only loads + validates that JSON, keeping the rest of the diagnostic
offline and testable. No prod access here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

_REQUIRED = (
    "symbol", "direction", "size_usd", "entry_price", "entry_ts",
    "exit_price", "exit_ts", "pnl_usd",
)


@dataclass(frozen=True)
class LiveTrade:
    symbol: str
    direction: str
    size_usd: float
    entry_price: float
    entry_ts: str
    exit_price: float
    exit_ts: str
    pnl_usd: float
    scan_price: float | None
    scan_ts: str | None


def load_live_trades(path: str) -> list[LiveTrade]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    out: list[LiveTrade] = []
    for r in raw:
        for k in _REQUIRED:
            if r.get(k) is None:
                raise ValueError(f"live trade {r.get('id')} missing required field {k!r}")
        sp = r.get("scan_price")
        out.append(LiveTrade(
            symbol=str(r["symbol"]), direction=str(r["direction"]),
            size_usd=float(r["size_usd"]), entry_price=float(r["entry_price"]),
            entry_ts=str(r["entry_ts"]), exit_price=float(r["exit_price"]),
            exit_ts=str(r["exit_ts"]), pnl_usd=float(r["pnl_usd"]),
            scan_price=(float(sp) if sp is not None else None),
            scan_ts=(str(r["scan_ts"]) if r.get("scan_ts") is not None else None),
        ))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cost_diagnosis_live_trades.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/cost_diagnosis/__init__.py tools/cost_diagnosis/live_trades.py tests/test_cost_diagnosis_live_trades.py
git commit -m "feat(cost-diag): live-trade dump loader + validation"
```

---

## Task 2: Liquidity proxy (same as backtest)

**Files:**
- Create: `tools/cost_diagnosis/liquidity.py`
- Test: `tests/test_cost_diagnosis_liquidity.py`

Rebuilds the backtest's proxy exactly: `(close × volume) / 60` then a 720-bar
(30-day) rolling mean with `min_periods=120`. Takes a DataFrame so it is testable
without network.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cost_diagnosis_liquidity.py
import math
import numpy as np
import pandas as pd
from tools.cost_diagnosis.liquidity import liquidity_series, liquidity_at


def _df(n, close=100.0, volume=600.0, start="2026-04-01"):
    idx = pd.date_range(start, periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"close": [close] * n, "volume": [volume] * n}, index=idx)


def test_series_matches_backtest_formula():
    # close*volume/60 = 100*600/60 = 1000 per bar; rolling mean settles at 1000.
    s = liquidity_series(_df(200))
    assert math.isclose(float(s.iloc[-1]), 1000.0, rel_tol=1e-9)


def test_min_periods_gives_nan_early():
    s = liquidity_series(_df(200))
    # bar 0..118 (< min_periods=120) are NaN
    assert np.isnan(float(s.iloc[50]))


def test_liquidity_at_picks_last_bar_at_or_before_ts():
    df = _df(200)
    s = liquidity_series(df)
    ts = df.index[150]
    assert math.isclose(liquidity_at(s, ts), 1000.0, rel_tol=1e-9)


def test_liquidity_at_before_series_is_nan():
    s = liquidity_series(_df(200, start="2026-04-01"))
    assert math.isnan(liquidity_at(s, "2026-01-01T00:00:00+00:00"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cost_diagnosis_liquidity.py -v`
Expected: FAIL with `ModuleNotFoundError: tools.cost_diagnosis.liquidity`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/cost_diagnosis/liquidity.py
"""Per-minute USD liquidity proxy, identical to backtest.py:1018.

    usd_per_min = (close * volume) / 60
    liquidity   = usd_per_min.rolling(720, min_periods=120).mean()

Kept pure (takes a DataFrame) so it tests without market-data access.
"""
from __future__ import annotations

import pandas as pd


def liquidity_series(df1h: pd.DataFrame) -> pd.Series:
    usd_per_min = (df1h["close"] * df1h["volume"]) / 60.0
    return usd_per_min.rolling(720, min_periods=120).mean()


def liquidity_at(series: pd.Series, ts) -> float:
    """Last liquidity value at or before ts. NaN if none / empty."""
    if series is None or len(series) == 0:
        return float("nan")
    ts = pd.Timestamp(ts)
    mask = series.index <= ts
    if not mask.any():
        return float("nan")
    return float(series[mask].iloc[-1])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cost_diagnosis_liquidity.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/cost_diagnosis/liquidity.py tests/test_cost_diagnosis_liquidity.py
git commit -m "feat(cost-diag): liquidity proxy mirroring backtest"
```

---

## Task 3: Model-cost recompute under corrections

**Files:**
- Create: `tools/cost_diagnosis/recompute.py`
- Test: `tests/test_cost_diagnosis_recompute.py`

Invokes the UNMODIFIED `backtest_costs.compute_trade_costs`. A "correction" is a
`(liq_mult, sf_div)` pair: `liq_mult=1440` models a daily participation basis;
`sf_div` divides the tier `size_factor`. Baseline is `(1.0, 1.0)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cost_diagnosis_recompute.py
import math
from tools.cost_diagnosis.recompute import model_cost_bps, CORRECTIONS


def test_baseline_major_matches_hand_calc():
    # BTC tier=major: base_bps=2, size_factor=885.44, half_spread=1.5, fee_per_side=10.
    # size=644, liq=2_000_000/min => participation=644/2e6=3.22e-4, sqrt=0.017944.
    # slip/fill = 2 + 885.44*0.017944 = 17.886; round-trip slip = 35.77.
    # spread round-trip = 3.0; fee round-trip = 20.0; funding (hold<8h) = 0.
    # total ~ 58.77 bps.
    bps = model_cost_bps("BTCUSDT", 644.0, 2_000_000.0, 2_000_000.0, 5.0)
    assert math.isclose(bps, 58.77, abs_tol=0.5)


def test_daily_basis_reduces_slippage():
    base = model_cost_bps("AVAXUSDT", 644.0, 50_000.0, 50_000.0, 3.0)
    daily = model_cost_bps("AVAXUSDT", 644.0, 50_000.0, 50_000.0, 3.0, liq_mult=1440.0)
    assert daily < base


def test_size_factor_divisor_reduces_slippage():
    base = model_cost_bps("RUNEUSDT", 644.0, 30_000.0, 30_000.0, 3.0)
    div = model_cost_bps("RUNEUSDT", 644.0, 30_000.0, 30_000.0, 3.0, sf_div=37.95)
    assert div < base


def test_corrections_table_shape():
    names = [c[0] for c in CORRECTIONS]
    assert names[0] == "baseline"
    assert "daily_basis" in names and "sf_div_37.95" in names and "both_37.95" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cost_diagnosis_recompute.py -v`
Expected: FAIL with `ModuleNotFoundError: tools.cost_diagnosis.recompute`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/cost_diagnosis/recompute.py
"""Recompute the v2 model cost for a trade under baseline + correction params.

Reuses backtest_costs.compute_trade_costs unmodified. A correction is
(liq_mult, sf_div): liq_mult scales the liquidity denominator (1440 = per-minute
-> daily basis); sf_div divides the tier size_factor. Pre-registered sweep only.
"""
from __future__ import annotations

from dataclasses import replace

from backtest_costs import tier_for_symbol, load_calibration, compute_trade_costs

_CAL = load_calibration()

# (name, liq_mult, sf_div) — pre-registered, NOT fit to the answer.
CORRECTIONS = [
    ("baseline", 1.0, 1.0),
    ("daily_basis", 1440.0, 1.0),
    ("sf_div_37.95", 1.0, 37.95),
    ("sf_div_31.62", 1.0, 31.62),
    ("sf_div_10", 1.0, 10.0),
    ("both_37.95", 1440.0, 37.95),
    ("both_31.62", 1440.0, 31.62),
    ("both_10", 1440.0, 10.0),
]


def model_cost_bps(
    symbol: str, size_usd: float, liq_entry: float, liq_exit: float,
    holding_hours: float, *, liq_mult: float = 1.0, sf_div: float = 1.0,
) -> float:
    """Round-trip total_cost_bps the model would charge under the given correction."""
    tp = _CAL.tiers[tier_for_symbol(symbol)]
    tp = replace(tp, size_factor=tp.size_factor / sf_div)
    d = compute_trade_costs(
        entry_notional_usd=size_usd, exit_notional_usd=size_usd,
        entry_liquidity_usd_per_min=liq_entry * liq_mult,
        exit_liquidity_usd_per_min=liq_exit * liq_mult,
        tier_params=tp, holding_hours=holding_hours, model="v2",
    )
    return float(d["total_cost_bps"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cost_diagnosis_recompute.py -v`
Expected: PASS (4 tests). If `test_baseline_major_matches_hand_calc` is off,
read the printed value and confirm against `costs_calibration.json` major tier
before adjusting the `abs_tol` — do NOT change the engine.

- [ ] **Step 5: Commit**

```bash
git add tools/cost_diagnosis/recompute.py tests/test_cost_diagnosis_recompute.py
git commit -m "feat(cost-diag): model-cost recompute under correction sweep"
```

---

## Task 4: Reconcile + branch verdict (pre-registered thresholds)

**Files:**
- Create: `tools/cost_diagnosis/reconcile.py`
- Test: `tests/test_cost_diagnosis_reconcile.py`

Applies spec §3: a correction reconciles iff (1) no winning trade has
`cost_pct > observed_move_pct`, AND (2) per-tier median round-trip cost is within
the band (major/mid 30 bps, small 50 bps). Branch RE-ANCHOR if any non-baseline
correction reconciles, else REBUILD.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cost_diagnosis_reconcile.py
from tools.cost_diagnosis.reconcile import reconcile

CORR = [("baseline", 1.0, 1.0), ("daily_basis", 1440.0, 1.0)]


def _trade(tier, pnl, move_pct, baseline_bps, daily_bps):
    return {
        "tier": tier, "pnl_usd": pnl, "observed_move_pct": move_pct,
        "costs": {"baseline": baseline_bps, "daily_basis": daily_bps},
    }


def test_re_anchor_when_a_correction_reconciles():
    # baseline over-charges winners (90bps=0.9% > 0.5% move); daily_basis (8bps) fixes it.
    per_trade = [_trade("major", 10.0, 0.5, 90.0, 8.0) for _ in range(3)]
    branch, winning, results = reconcile(per_trade, CORR)
    assert branch == "RE-ANCHOR"
    assert "daily_basis" in winning
    assert results["baseline"]["winners_exceeded"] == 3
    assert results["daily_basis"]["reconciles"] is True


def test_rebuild_when_none_reconcile():
    # even daily_basis still exceeds the winning move (move 0.05% < 0.08% cost).
    per_trade = [_trade("major", 5.0, 0.05, 90.0, 8.0) for _ in range(3)]
    branch, winning, results = reconcile(per_trade, CORR)
    assert branch == "REBUILD"
    assert winning == []


def test_no_winner_exceeded_but_band_broken_does_not_reconcile():
    # cost (40bps) never exceeds the big move (5%) on winners, but 40 > 30 band (major).
    per_trade = [_trade("major", 10.0, 5.0, 40.0, 40.0) for _ in range(3)]
    branch, winning, results = reconcile(per_trade, CORR)
    assert results["daily_basis"]["reconciles"] is False
    assert branch == "REBUILD"


def test_small_tier_uses_50bps_band():
    # 45bps round-trip: within 50 (small) but would break 30 (major). Move large so cond1 ok.
    per_trade = [_trade("small", 10.0, 5.0, 45.0, 45.0) for _ in range(3)]
    _, winning, results = reconcile(per_trade, CORR)
    assert results["daily_basis"]["reconciles"] is True
    assert "daily_basis" in winning
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cost_diagnosis_reconcile.py -v`
Expected: FAIL with `ModuleNotFoundError: tools.cost_diagnosis.reconcile`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/cost_diagnosis/reconcile.py
"""Pre-registered reconcile thresholds + branch verdict (spec §3).

A correction reconciles iff:
  1. no winning trade (pnl_usd > 0) has cost_pct > observed_move_pct, AND
  2. per-tier median round-trip cost <= band (major/mid 30 bps, small 50 bps).
Branch RE-ANCHOR if any non-baseline correction reconciles, else REBUILD.
Thresholds are fixed here and do not move after seeing results.
"""
from __future__ import annotations

from statistics import median

TIER_BAND_BPS = {"major": 30.0, "mid": 30.0, "small": 50.0}


def reconcile(per_trade: list[dict], corrections: list) -> tuple[str, list[str], dict]:
    results: dict = {}
    for name, *_ in corrections:
        winners_exceeded = [
            t for t in per_trade
            if t["pnl_usd"] > 0 and (t["costs"][name] / 100.0) > t["observed_move_pct"]
        ]
        tier_medians: dict = {}
        ok_band = True
        for tier, band in TIER_BAND_BPS.items():
            vals = [t["costs"][name] for t in per_trade if t["tier"] == tier]
            if vals:
                m = median(vals)
                tier_medians[tier] = m
                if m > band:
                    ok_band = False
        reconciles = (len(winners_exceeded) == 0) and ok_band
        results[name] = {
            "winners_exceeded": len(winners_exceeded),
            "tier_medians": tier_medians,
            "reconciles": reconciles,
        }
    winning = [
        name for name, *_ in corrections
        if name != "baseline" and results[name]["reconciles"]
    ]
    branch = "RE-ANCHOR" if winning else "REBUILD"
    return branch, winning, results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cost_diagnosis_reconcile.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/cost_diagnosis/reconcile.py tests/test_cost_diagnosis_reconcile.py
git commit -m "feat(cost-diag): pre-registered reconcile thresholds + branch"
```

---

## Task 5: Per-trade assembly (pure)

**Files:**
- Create: `tools/cost_diagnosis/assemble.py`
- Test: `tests/test_cost_diagnosis_assemble.py`

Pure function: given live trades + a `{symbol: liquidity_series}` map, produce the
per-trade rows (tier, observed_move_pct, holding_hours, liq@entry/exit, costs under
every correction, scan-vs-fill cross-check). Kept separate from IO so it tests
without network.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cost_diagnosis_assemble.py
import math
import pandas as pd
from tools.cost_diagnosis.live_trades import LiveTrade
from tools.cost_diagnosis.assemble import assemble_per_trade


def _liq(symbol_value):
    idx = pd.date_range("2026-05-01", periods=300, freq="1h", tz="UTC")
    # constant series so liquidity_at is deterministic
    return pd.Series([symbol_value] * 300, index=idx)


def test_assembles_expected_fields():
    t = LiveTrade(
        symbol="AVAXUSDT", direction="SHORT", size_usd=644.0,
        entry_price=20.0, entry_ts="2026-05-10T00:00:00+00:00",
        exit_price=19.0, exit_ts="2026-05-10T05:00:00+00:00", pnl_usd=12.0,
        scan_price=20.1, scan_ts="2026-05-09T23:00:00+00:00",
    )
    liq_map = {"AVAXUSDT": _liq(50_000.0)}
    rows = assemble_per_trade([t], liq_map)
    r = rows[0]
    assert r["tier"] == "mid"
    # observed move = |19-20|/20 * 100 = 5.0%
    assert math.isclose(r["observed_move_pct"], 5.0, rel_tol=1e-9)
    assert math.isclose(r["holding_hours"], 5.0, rel_tol=1e-9)
    # scan-vs-fill slippage = |20.0-20.1|/20.1 * 100
    assert math.isclose(r["scan_fill_slip_pct"], abs(20.0 - 20.1) / 20.1 * 100, rel_tol=1e-9)
    assert "baseline" in r["costs"] and "daily_basis" in r["costs"]
    assert r["costs"]["daily_basis"] < r["costs"]["baseline"]


def test_nan_liquidity_marks_trade_unobservable():
    t = LiveTrade(
        symbol="BTCUSDT", direction="SHORT", size_usd=644.0,
        entry_price=60000.0, entry_ts="2020-01-01T00:00:00+00:00",  # before series
        exit_price=59000.0, exit_ts="2020-01-01T02:00:00+00:00", pnl_usd=9.0,
        scan_price=None, scan_ts=None,
    )
    rows = assemble_per_trade([t], {"BTCUSDT": _liq(1_000_000.0)})
    assert rows[0]["liquidity_unobservable"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cost_diagnosis_assemble.py -v`
Expected: FAIL with `ModuleNotFoundError: tools.cost_diagnosis.assemble`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/cost_diagnosis/assemble.py
"""Pure per-trade assembly: live trades + liquidity series -> diagnostic rows.

No IO. The driver (run.py) provides the liquidity series map. Trades whose
liquidity is NaN at entry/exit are flagged `liquidity_unobservable` and excluded
from the reconcile aggregate (same spirit as the model's 100bps fallback).
"""
from __future__ import annotations

import math

import pandas as pd

from backtest_costs import tier_for_symbol
from tools.cost_diagnosis.liquidity import liquidity_at
from tools.cost_diagnosis.recompute import model_cost_bps, CORRECTIONS
from tools.cost_diagnosis.live_trades import LiveTrade


def assemble_per_trade(trades: list[LiveTrade], liq_map: dict) -> list[dict]:
    rows: list[dict] = []
    for t in trades:
        series = liq_map.get(t.symbol)
        liq_entry = liquidity_at(series, t.entry_ts) if series is not None else float("nan")
        liq_exit = liquidity_at(series, t.exit_ts) if series is not None else float("nan")
        unobservable = not (math.isfinite(liq_entry) and math.isfinite(liq_exit))

        observed_move_pct = abs(t.exit_price - t.entry_price) / t.entry_price * 100.0
        holding_hours = (
            pd.Timestamp(t.exit_ts) - pd.Timestamp(t.entry_ts)
        ).total_seconds() / 3600.0

        costs: dict = {}
        if not unobservable:
            for name, liq_mult, sf_div in CORRECTIONS:
                costs[name] = model_cost_bps(
                    t.symbol, t.size_usd, liq_entry, liq_exit, holding_hours,
                    liq_mult=liq_mult, sf_div=sf_div,
                )

        scan_fill_slip_pct = (
            abs(t.entry_price - t.scan_price) / t.scan_price * 100.0
            if t.scan_price else None
        )

        rows.append({
            "symbol": t.symbol, "direction": t.direction, "size_usd": t.size_usd,
            "tier": tier_for_symbol(t.symbol), "pnl_usd": t.pnl_usd,
            "entry_ts": t.entry_ts, "exit_ts": t.exit_ts,
            "observed_move_pct": observed_move_pct, "holding_hours": holding_hours,
            "liq_entry": liq_entry, "liq_exit": liq_exit,
            "liquidity_unobservable": unobservable,
            "scan_fill_slip_pct": scan_fill_slip_pct,
            "costs": costs,
        })
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cost_diagnosis_assemble.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/cost_diagnosis/assemble.py tests/test_cost_diagnosis_assemble.py
git commit -m "feat(cost-diag): pure per-trade assembly with scan-fill cross-check"
```

---

## Task 6: Driver + report writers

**Files:**
- Create: `tools/cost_diagnosis/run.py`
- Test: `tests/test_cost_diagnosis_run.py`

Loads the dumped live trades, loads 1H bars per unique symbol via
`backtest.get_cached_data`, builds liquidity series, assembles rows, reconciles,
and writes `findings.md` + `per_trade.json`. The report writers are pure and
tested; the data-loading `main()` is exercised by the manual full run.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cost_diagnosis_run.py
import json
from tools.cost_diagnosis.run import write_reports, CORRECTIONS_FOR_REPORT


def test_write_reports_emits_both_files_and_branch(tmp_path):
    per_trade = [{
        "symbol": "AVAXUSDT", "direction": "SHORT", "size_usd": 644.0, "tier": "mid",
        "pnl_usd": 10.0, "entry_ts": "2026-05-10T00:00:00+00:00",
        "exit_ts": "2026-05-10T05:00:00+00:00", "observed_move_pct": 0.5,
        "holding_hours": 5.0, "liq_entry": 50000.0, "liq_exit": 50000.0,
        "liquidity_unobservable": False, "scan_fill_slip_pct": 0.4,
        "costs": {"baseline": 90.0, "daily_basis": 8.0},
    }]
    branch, winning = write_reports(per_trade, str(tmp_path),
                                    corrections=[("baseline", 1.0, 1.0), ("daily_basis", 1440.0, 1.0)])
    assert branch == "RE-ANCHOR" and "daily_basis" in winning
    findings = (tmp_path / "findings.md").read_text(encoding="utf-8")
    assert "RE-ANCHOR" in findings and "over-charge" in findings.lower()
    rows = json.loads((tmp_path / "per_trade.json").read_text(encoding="utf-8"))
    assert rows[0]["symbol"] == "AVAXUSDT"


def test_unobservable_trades_excluded_from_reconcile(tmp_path):
    per_trade = [
        {"symbol": "BTCUSDT", "tier": "major", "pnl_usd": 5.0, "observed_move_pct": 0.5,
         "liquidity_unobservable": True, "scan_fill_slip_pct": None, "costs": {},
         "direction": "SHORT", "size_usd": 644.0, "entry_ts": "x", "exit_ts": "y",
         "holding_hours": 2.0, "liq_entry": float("nan"), "liq_exit": float("nan")},
        {"symbol": "AVAXUSDT", "tier": "mid", "pnl_usd": 10.0, "observed_move_pct": 0.5,
         "liquidity_unobservable": False, "scan_fill_slip_pct": 0.4,
         "costs": {"baseline": 90.0, "daily_basis": 8.0},
         "direction": "SHORT", "size_usd": 644.0, "entry_ts": "a", "exit_ts": "b",
         "holding_hours": 5.0, "liq_entry": 50000.0, "liq_exit": 50000.0},
    ]
    branch, winning = write_reports(per_trade, str(tmp_path),
                                    corrections=[("baseline", 1.0, 1.0), ("daily_basis", 1440.0, 1.0)])
    # only the observable AVAX trade drives the verdict
    assert branch == "RE-ANCHOR"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cost_diagnosis_run.py -v`
Expected: FAIL with `ImportError` / `ModuleNotFoundError` for `tools.cost_diagnosis.run`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/cost_diagnosis/run.py
"""Driver: dumped live trades -> diagnosis -> findings.md + per_trade.json.

Read-only. The only prod touch is the prerequisite ssh+sqlite dump (see plan);
this driver consumes that JSON offline.
"""
from __future__ import annotations

import json
import os
from statistics import median

from tools.cost_diagnosis.live_trades import load_live_trades
from tools.cost_diagnosis.liquidity import liquidity_series
from tools.cost_diagnosis.recompute import CORRECTIONS
from tools.cost_diagnosis.assemble import assemble_per_trade
from tools.cost_diagnosis.reconcile import reconcile

OUT_DIR = os.path.join("data", "retune", "2026-06-01-cost-model-diagnosis")
CORRECTIONS_FOR_REPORT = CORRECTIONS


def _over_charge_summary(per_trade: list[dict]) -> dict:
    """Baseline falsification headline: winners whose model cost exceeds the move."""
    obs = [t for t in per_trade if not t["liquidity_unobservable"]]
    winners = [t for t in obs if t["pnl_usd"] > 0]
    exceeded = [
        t for t in winners
        if (t["costs"]["baseline"] / 100.0) > t["observed_move_pct"]
    ]
    ratios = [
        (t["costs"]["baseline"] / 100.0) / t["observed_move_pct"]
        for t in obs if t["observed_move_pct"] > 0
    ]
    return {
        "winners": len(winners), "winners_exceeded": len(exceeded),
        "median_over_charge_ratio": (median(ratios) if ratios else float("nan")),
    }


def write_reports(per_trade: list[dict], out_dir: str, corrections=CORRECTIONS):
    os.makedirs(out_dir, exist_ok=True)
    observable = [t for t in per_trade if not t["liquidity_unobservable"]]
    branch, winning, results = reconcile(observable, corrections)
    summary = _over_charge_summary(per_trade)

    lines = [
        "# Cost-model diagnosis — findings",
        "",
        f"**Branch verdict: {branch}**",
        f"Winning correction(s): {', '.join(winning) if winning else '(none)'}",
        "",
        "## Baseline falsification (over-charge headline)",
        f"- observable trades: {len(observable)} / {len(per_trade)}",
        f"- winners: {summary['winners']}; winners whose model cost exceeds the "
        f"entire price move: {summary['winners_exceeded']}",
        f"- median over-charge ratio (model cost / observed move): "
        f"{summary['median_over_charge_ratio']:.2f}",
        "",
        "## Per-correction reconcile",
        "| correction | winners exceeded | tier medians (bps) | reconciles |",
        "|---|---|---|---|",
    ]
    for name, *_ in corrections:
        r = results[name]
        tm = ", ".join(f"{k}:{v:.1f}" for k, v in sorted(r["tier_medians"].items()))
        lines.append(f"| {name} | {r['winners_exceeded']} | {tm} | {r['reconciles']} |")
    lines += [
        "",
        "## Cross-check C — scan-price vs fill slippage (entry, conflated w/ operator delay)",
    ]
    sc = [t["scan_fill_slip_pct"] for t in per_trade if t.get("scan_fill_slip_pct") is not None]
    if sc:
        lines.append(f"- median scan→fill slip: {median(sc):.3f}% over {len(sc)} trades")
    else:
        lines.append("- no scan prices available")
    lines += [
        "",
        "## Next",
        "- RE-ANCHOR -> spec the winning correction; confirm with cross-check B "
        "(re-run pre-holdout under it).",
        "- REBUILD -> spec real-execution data collection + re-derivation (the v3).",
        "",
        "Read-only diagnostic. Thresholds pre-registered in the design spec §3.",
    ]
    with open(os.path.join(out_dir, "findings.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(out_dir, "per_trade.json"), "w", encoding="utf-8") as f:
        json.dump(per_trade, f, indent=2, default=str)
    return branch, winning


def main():
    from backtest import get_cached_data

    trades = load_live_trades(os.path.join(OUT_DIR, "live_trades.json"))
    liq_map: dict = {}
    for sym in sorted({t.symbol for t in trades}):
        df1h = get_cached_data(sym, "1h")
        liq_map[sym] = liquidity_series(df1h) if df1h is not None and len(df1h) else None
    per_trade = assemble_per_trade(trades, liq_map)
    branch, winning = write_reports(per_trade, OUT_DIR)
    print(f"branch={branch} winning={winning}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cost_diagnosis_run.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/cost_diagnosis/run.py tests/test_cost_diagnosis_run.py
git commit -m "feat(cost-diag): driver + report writers"
```

---

## Task 7: Run the diagnosis (manual, needs prod dump + market data)

**Files:**
- Produces: `data/retune/2026-06-01-cost-model-diagnosis/{findings.md, per_trade.json}`

- [ ] **Step 1: Dump live trades** — run the prerequisite ssh command (top of plan).
  Network to Binance providers can be flaky; if `get_cached_data` fails on a
  recent gap-fill, re-run — caches warm progressively (documented in the base-edge
  session).

- [ ] **Step 2: Run the driver**

Run: `python -m tools.cost_diagnosis.run`
Expected: prints `branch=RE-ANCHOR|REBUILD winning=[...]` and writes both files.

- [ ] **Step 3: Read `findings.md`** — confirm the over-charge headline and the
  branch verdict. This verdict selects the NEXT spec (re-anchor vs rebuild). Do
  NOT change `costs_calibration.json` in this task.

- [ ] **Step 4: Commit the artifacts**

```bash
git add data/retune/2026-06-01-cost-model-diagnosis/
git commit -m "diag(cost-model): live-vs-model reconcile verdict + per-trade data"
```

- [ ] **Step 5: Run the full test suite for the package**

Run: `python -m pytest tests/test_cost_diagnosis_*.py -v`
Expected: all green.

---

## Done criteria

- All `tests/test_cost_diagnosis_*.py` green.
- `findings.md` emits a clear branch verdict (RE-ANCHOR / REBUILD) with the
  over-charge headline and per-correction reconcile table.
- No production code touched; no change to `costs_calibration.json` or
  `backtest_costs.py`; live access was strictly `mode=ro`.
- The branch verdict tells us which recalibration spec to write next (#14 continues).
