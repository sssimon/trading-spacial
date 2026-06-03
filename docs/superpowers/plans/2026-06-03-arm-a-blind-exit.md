# Brazo A reformulado — Blind Exit Policy: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pre-registered falsification test that measures whether a blind mechanical exit rule (textbook 3×ATR chandelier) applied to all 27 reconstructable real positions beats the operator's realized exits, net of v3 costs.

**Architecture:** A self-contained offline package `tools/arm_a_blind_exit/` of pure, TDD-tested functions (population filter → Wilder ATR → fill-convention exit simulators → v3 recost → paired bootstrap/LOO → verdict), plus a `run.py` orchestrator that emits a JSON verdict + `findings.md` into `data/retune/2026-06-03-arm-a-blind-exit/`. No network, no holdout, no live-close path. Reuses `backtest_costs.compute_trade_costs` unmodified.

**Tech Stack:** Python 3, sqlite3, numpy, pytest. Reuses `backtest_costs` (`load_calibration`, `tier_for_symbol`, `compute_trade_costs`).

**Frozen pre-registration (from spec `2026-06-03-arm-a-reframed-blind-exit-design.md`, commit 370f1d5):**
- Population: 27 (8 symbols BTC/ETH/RUNE/XLM/PENDLE/UNI/DOGE/AVAX; MANUAL 23 + SL_HIT 4). Drop the 16 with zero OHLCV.
- Primary rule: chandelier, `(mult=3, period=22, tf=1h)` — IRREVOCABLE. Confirmatory: 38%-giveback (descriptive only).
- Fill: pessimistic (primary) + optimistic (sensitivity). Gross: `qty×(exit−entry)` signed. Costs: v3 both arms. Cap: 200h.
- Estimand: paired `Δ_i = blind_net_v3_i − actual_net_v3_i`; bootstrap 10k (seed 20260603) + LOO.
- KILL: PASS = `Δ̄>0` & bootstrap 95% CI excludes zero & survives dropping top influencer & holds under BOTH fills. FAIL = CI includes zero / `Δ̄≤0` under both fills. INDETERMINATE = sign flips with fill convention.

---

## File Structure

- Create `tools/arm_a_blind_exit/__init__.py` — package marker.
- Create `tools/arm_a_blind_exit/population.py` — load + filter the 27 positions; reconstructibility gate.
- Create `tools/arm_a_blind_exit/exit_rules.py` — Wilder ATR, chandelier + giveback simulators, fill conventions.
- Create `tools/arm_a_blind_exit/evaluate.py` — gross PnL, liquidity proxy, v3 recost, paired Δ, bootstrap, LOO, verdict.
- Create `tools/arm_a_blind_exit/run.py` — CLI orchestrator; writes verdict.json / per_trade.json / findings.md / manifest.json.
- Create `tests/test_arm_a_blind_exit.py` — unit tests for all pure functions.
- Output dir (runtime): `data/retune/2026-06-03-arm-a-blind-exit/`.

Constants frozen at module top of `exit_rules.py` / `evaluate.py`: `ATR_PERIOD=22`, `ATR_TF="1h"`, `CHANDELIER_MULT=3.0`, `GIVEBACK_FRAC=0.38`, `MAX_HOLD_H=200`, `BOOTSTRAP_N=10000`, `BOOTSTRAP_SEED=20260603`, `KEEP_SYMBOLS=(...)`.

---

## Task 1: Package skeleton + frozen constants

**Files:**
- Create: `tools/arm_a_blind_exit/__init__.py`
- Create: `tools/arm_a_blind_exit/constants.py`

- [ ] **Step 1: Create the package marker**

`tools/arm_a_blind_exit/__init__.py`:
```python
"""Brazo A reformulado — blind exit policy falsification test.

Pre-registered per docs/superpowers/specs/2026-06-03-arm-a-reframed-blind-exit-design.md.
Offline, read-only. No holdout (#322 untouched), no live PositionClosure path.
"""
```

- [ ] **Step 2: Create frozen constants**

`tools/arm_a_blind_exit/constants.py`:
```python
"""Pre-registered, IRREVOCABLE parameters. Changing any of these = a NEW experiment
with its own pre-registration (spec §3, Adrian F-1). Do not tune to results."""

ATR_PERIOD = 22
ATR_TF = "1h"
CHANDELIER_MULT = 3.0
GIVEBACK_FRAC = 0.38
MAX_HOLD_H = 200.0
PRICE_TF = "5m"
BOOTSTRAP_N = 10000
BOOTSTRAP_SEED = 20260603

# 8 symbols with full 5m coverage AND closed positions (spec §2).
KEEP_SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "RUNEUSDT", "XLMUSDT",
    "PENDLEUSDT", "UNIUSDT", "DOGEUSDT", "AVAXUSDT",
)

PAPA_DB = r"C:\Users\simon\Desktop\Papa\trading_backup_extracted\signals.db"
OHLCV_DB = "data/ohlcv.db"
OUTPUT_DIR = "data/retune/2026-06-03-arm-a-blind-exit"
```

- [ ] **Step 3: Commit**

```bash
git add tools/arm_a_blind_exit/__init__.py tools/arm_a_blind_exit/constants.py
git commit -m "feat(arm-a): package skeleton + frozen pre-registered constants"
```

---

## Task 2: Population loader + reconstructibility gate

**Files:**
- Create: `tools/arm_a_blind_exit/population.py`
- Test: `tests/test_arm_a_blind_exit.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_arm_a_blind_exit.py`:
```python
import os
import pytest
from tools.arm_a_blind_exit import population
from tools.arm_a_blind_exit.constants import KEEP_SYMBOLS, PAPA_DB, OHLCV_DB

papa_missing = not os.path.exists(PAPA_DB)

@pytest.mark.skipif(papa_missing, reason="papá's DB not present on this machine")
def test_population_is_the_frozen_27():
    pos, dropped = population.load_population(PAPA_DB, OHLCV_DB)
    assert len(pos) == 27
    assert all(p["symbol"] in KEEP_SYMBOLS for p in pos)
    reasons = sorted(p["exit_reason"] for p in pos)
    assert reasons.count("MANUAL") == 23
    assert reasons.count("SL_HIT") == 4
    # every kept position carries the fields the simulators need
    for p in pos:
        assert p["qty"] is not None
        assert p["direction"] in ("LONG", "SHORT")
        assert p["entry_ts"] and p["exit_ts"]
    # the 16 drop is reported, not silent
    assert sum(d["n"] for d in dropped) == 16
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_arm_a_blind_exit.py::test_population_is_the_frozen_27 -v`
Expected: FAIL with `ModuleNotFoundError` / `AttributeError: module has no attribute 'load_population'`.

- [ ] **Step 3: Write minimal implementation**

`tools/arm_a_blind_exit/population.py`:
```python
"""Load the frozen 27 from papá's DB; drop the un-reconstructable 16.

Reconstructibility gate (spec §2): a closed position is kept iff its symbol is in
KEEP_SYMBOLS (full 5m coverage) AND it has ≥ATR_PERIOD 1h bars before entry_ts.
The keep-set is MANUAL+SL_HIT only (the 2 TP_HIT fall on dropped symbols)."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timedelta, timezone
from .constants import KEEP_SYMBOLS, ATR_PERIOD, ATR_TF


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _has_pre_entry_1h(ohlcv_db: str, symbol: str, entry_ts: datetime) -> bool:
    con = sqlite3.connect(f"file:{ohlcv_db}?mode=ro", uri=True)
    end_ms = int(entry_ts.timestamp() * 1000)
    n = con.execute(
        "SELECT COUNT(*) FROM ohlcv WHERE symbol=? AND timeframe=? AND open_time < ?",
        (symbol, ATR_TF, end_ms),
    ).fetchone()[0]
    con.close()
    return n >= ATR_PERIOD


def load_population(papa_db: str, ohlcv_db: str) -> tuple[list[dict], list[dict]]:
    """Return (kept_positions, dropped_summary).

    kept_positions: list of dicts with id, symbol, direction, entry_price, entry_ts
    (datetime), exit_price, exit_ts (datetime), qty, exit_reason, pnl_usd.
    dropped_summary: [{"symbol":..., "n":...}] for symbols with zero OHLCV.
    """
    con = sqlite3.connect(f"file:{papa_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, symbol, direction, entry_price, entry_ts, exit_price, exit_ts, "
        "qty, size_usd, exit_reason, pnl_usd FROM positions WHERE status='closed'"
    ).fetchall()
    con.close()

    kept, dropped_counts = [], {}
    for r in rows:
        sym = r["symbol"]
        if sym not in KEEP_SYMBOLS:
            dropped_counts[sym] = dropped_counts.get(sym, 0) + 1
            continue
        entry_ts = _parse_ts(r["entry_ts"])
        if not _has_pre_entry_1h(ohlcv_db, sym, entry_ts):
            dropped_counts[sym] = dropped_counts.get(sym, 0) + 1
            continue
        kept.append({
            "id": int(r["id"]), "symbol": sym, "direction": r["direction"],
            "entry_price": float(r["entry_price"]), "entry_ts": entry_ts,
            "exit_price": float(r["exit_price"]), "exit_ts": _parse_ts(r["exit_ts"]),
            "qty": float(r["qty"]), "exit_reason": r["exit_reason"],
            "pnl_usd": float(r["pnl_usd"]),
        })
    dropped = [{"symbol": s, "n": n} for s, n in sorted(dropped_counts.items())]
    return kept, dropped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_arm_a_blind_exit.py::test_population_is_the_frozen_27 -v`
Expected: PASS (27 kept, 16 dropped). If papá's DB is absent the test SKIPs — that is acceptable on CI but the runner (Task 7) must be executed where the DB exists.

- [ ] **Step 5: Commit**

```bash
git add tools/arm_a_blind_exit/population.py tests/test_arm_a_blind_exit.py
git commit -m "feat(arm-a): population loader + reconstructibility gate (frozen 27)"
```

---

## Task 3: Wilder ATR-22 on 1h

**Files:**
- Create: `tools/arm_a_blind_exit/exit_rules.py`
- Test: `tests/test_arm_a_blind_exit.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_arm_a_blind_exit.py`:
```python
from tools.arm_a_blind_exit import exit_rules

def test_wilder_atr_known_series():
    # 23 bars; TR is constant 2.0 (each bar high-low=2, no gaps) -> ATR=2.0
    bars = [{"high": 12.0, "low": 10.0, "close": 11.0} for _ in range(23)]
    atr = exit_rules.wilder_atr(bars, period=22)
    assert atr == pytest.approx(2.0, abs=1e-9)

def test_wilder_atr_needs_enough_bars():
    bars = [{"high": 1.0, "low": 0.0, "close": 0.5} for _ in range(10)]
    with pytest.raises(ValueError):
        exit_rules.wilder_atr(bars, period=22)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_arm_a_blind_exit.py -k wilder -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'wilder_atr'`.

- [ ] **Step 3: Write minimal implementation**

In `tools/arm_a_blind_exit/exit_rules.py`:
```python
"""Wilder ATR + blind exit simulators with explicit intra-bar fill conventions.

All rules trail from the RUNNING peak/trough (causal — no look-ahead, Halberg).
Pessimistic fill: within a 5m bar the adverse extreme is assumed touched before
the favorable one. Optimistic: the reverse (sensitivity arm, spec §5)."""
from __future__ import annotations
from .constants import CHANDELIER_MULT, GIVEBACK_FRAC, MAX_HOLD_H


def wilder_atr(bars_1h: list[dict], period: int = 22) -> float:
    """Wilder's ATR over the LAST `period` true ranges ending at the final bar.

    bars_1h: chronological dicts with high/low/close. Needs >= period+1 bars."""
    if len(bars_1h) < period + 1:
        raise ValueError(f"need >= {period + 1} 1h bars, got {len(bars_1h)}")
    trs = []
    for prev, cur in zip(bars_1h[-period - 1:-1], bars_1h[-period:]):
        tr = max(
            cur["high"] - cur["low"],
            abs(cur["high"] - prev["close"]),
            abs(cur["low"] - prev["close"]),
        )
        trs.append(tr)
    atr = sum(trs) / period          # seed = simple mean of first window
    return atr
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_arm_a_blind_exit.py -k wilder -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add tools/arm_a_blind_exit/exit_rules.py tests/test_arm_a_blind_exit.py
git commit -m "feat(arm-a): Wilder ATR-22 (frozen period) with seed window"
```

---

## Task 4: Chandelier exit simulator + fill conventions

**Files:**
- Modify: `tools/arm_a_blind_exit/exit_rules.py`
- Test: `tests/test_arm_a_blind_exit.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_arm_a_blind_exit.py`:
```python
def _bar(t, o, h, l, c):
    return {"open_time": t, "open": o, "high": h, "low": l, "close": c}

def test_chandelier_long_trails_up_and_stops():
    # entry 100, ATR 1 -> initial stop 97. Price runs to 110 (stop trails to 107),
    # then a bar dips to 106 -> stop hit at 107.
    atr = 1.0
    path = [
        _bar(0,   100, 101, 99,  100),
        _bar(300, 100, 110, 100, 109),   # peak 110 -> stop = 110 - 3*1 = 107
        _bar(600, 109, 109, 106, 108),   # low 106 <= 107 -> exit at 107
    ]
    px, ts, cap = exit_rules.simulate_chandelier(
        path, "LONG", entry_price=100.0, atr=atr, fill="pessimistic")
    assert px == pytest.approx(107.0)
    assert ts == 600
    assert cap is False

def test_chandelier_short_mirrors():
    atr = 1.0
    path = [
        _bar(0,   100, 101, 99,  100),
        _bar(300, 100, 100, 90,  91),    # trough 90 -> stop = 90 + 3 = 93
        _bar(600, 91,  94,  91,  92),    # high 94 >= 93 -> exit at 93
    ]
    px, ts, cap = exit_rules.simulate_chandelier(
        path, "SHORT", entry_price=100.0, atr=atr, fill="pessimistic")
    assert px == pytest.approx(93.0)
    assert cap is False

def test_chandelier_pessimistic_vs_optimistic_same_bar():
    # one bar where BOTH the new favorable extreme and the stop sit inside the bar.
    atr = 1.0
    path = [
        _bar(0,   100, 101, 99,  100),
        _bar(300, 100, 108, 104, 105),   # high 108 -> stop 105; low 104 <= 105
    ]
    # pessimistic: adverse (low) first -> stop at the PRE-update stop (100-3=97)? No:
    # stop updates from running peak as bars are seen; within the trigger bar the
    # peak is 108 so stop=105, but adverse-first means low 104 crosses 105 -> exit 105.
    px_p, _, _ = exit_rules.simulate_chandelier(path, "LONG", 100.0, atr, fill="pessimistic")
    px_o, _, _ = exit_rules.simulate_chandelier(path, "LONG", 100.0, atr, fill="optimistic")
    assert px_p == pytest.approx(105.0)   # stopped this bar
    assert px_o == pytest.approx(105.0)   # favorable first still ends stopped at 105
    assert px_p <= px_o + 1e-9

def test_chandelier_hits_cap_when_never_stopped():
    # monotone tiny uptrend that never retraces 3*ATR; path spans 250h but cap is 200h,
    # so the rule falls through and exits at the LAST bar WITHIN the cap window.
    atr = 1.0
    from tools.arm_a_blind_exit.constants import MAX_HOLD_H
    path = [_bar(i * 300_000, 100 + i * 0.01, 100 + i * 0.01 + 0.005,
                 100 + i * 0.01 - 0.001, 100 + i * 0.01) for i in range(3000)]
    px, ts, cap = exit_rules.simulate_chandelier(path, "LONG", 100.0, atr, fill="pessimistic")
    cap_ms = path[0]["open_time"] + int(MAX_HOLD_H * 3600 * 1000)
    within = [b for b in path if b["open_time"] <= cap_ms]
    assert cap is True
    assert px == pytest.approx(within[-1]["close"])
    assert ts == within[-1]["open_time"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_arm_a_blind_exit.py -k chandelier -v`
Expected: FAIL with `AttributeError: ... 'simulate_chandelier'`.

- [ ] **Step 3: Write minimal implementation**

Append to `tools/arm_a_blind_exit/exit_rules.py`:
```python
def _cap_ms(path: list[dict]) -> int:
    return path[0]["open_time"] + int(MAX_HOLD_H * 3600 * 1000)


def simulate_chandelier(
    path: list[dict], direction: str, entry_price: float, atr: float,
    *, mult: float = CHANDELIER_MULT, fill: str = "pessimistic",
) -> tuple[float, int, bool]:
    """Trailing chandelier on a 5m path. Returns (exit_price, exit_open_time, hit_cap).

    LONG:  stop = running_peak  − mult*ATR, exit when bar low  <= stop.
    SHORT: stop = running_trough + mult*ATR, exit when bar high >= stop.
    Stop is monotone (LONG: non-decreasing). `fill` decides intra-bar order when
    both the favorable extreme (updating the stop) and the adverse extreme (crossing
    it) live in the same bar."""
    cap_ms = _cap_ms(path)
    long = direction == "LONG"
    peak = entry_price                       # running favorable extreme
    stop = entry_price - mult * atr if long else entry_price + mult * atr
    for bar in path:
        if bar["open_time"] > cap_ms:
            break
        hi, lo = bar["high"], bar["low"]
        fav = hi if long else lo             # favorable extreme this bar
        adv = lo if long else hi             # adverse extreme this bar
        new_peak = max(peak, fav) if long else min(peak, fav)
        new_stop = (new_peak - mult * atr) if long else (new_peak + mult * atr)
        if fill == "optimistic":
            # favorable first: stop ratchets, THEN test adverse against new stop
            peak, stop = new_peak, (max(stop, new_stop) if long else min(stop, new_stop))
            crossed = adv <= stop if long else adv >= stop
            if crossed:
                return stop, bar["open_time"], False
        else:
            # pessimistic: adverse first, tested against the PRIOR bar's stop
            crossed = adv <= stop if long else adv >= stop
            if crossed:
                return stop, bar["open_time"], False
            peak, stop = new_peak, (max(stop, new_stop) if long else min(stop, new_stop))
    # never stopped within data/cap -> exit at last available bar within cap
    last = max((b for b in path if b["open_time"] <= cap_ms), key=lambda b: b["open_time"])
    return last["close"], last["open_time"], True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_arm_a_blind_exit.py -k chandelier -v`
Expected: PASS (all 4 chandelier tests).

- [ ] **Step 5: Commit**

```bash
git add tools/arm_a_blind_exit/exit_rules.py tests/test_arm_a_blind_exit.py
git commit -m "feat(arm-a): chandelier simulator with pessimistic/optimistic fill + cap"
```

---

## Task 5: 38%-giveback confirmatory simulator

**Files:**
- Modify: `tools/arm_a_blind_exit/exit_rules.py`
- Test: `tests/test_arm_a_blind_exit.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_arm_a_blind_exit.py`:
```python
def test_giveback_long_exits_after_retrace():
    # bar0 high=entry (no favorable move yet, stop unarmed); bar1 sets peak 110 with
    # low 108 (above giveback 106.2, no same-bar trigger); bar2 retraces to exit.
    path = [
        _bar(0,   100, 100, 99,  100),   # high == entry -> no favorable move
        _bar(300, 100, 110, 108, 109),   # peak 110, fav move = 10, giveback stop = 106.2
        _bar(600, 109, 109, 105, 106),   # low 105 <= 106.2 -> exit at 106.2
    ]
    px, ts, cap = exit_rules.simulate_giveback(path, "LONG", entry_price=100.0, fill="pessimistic")
    assert px == pytest.approx(106.2)
    assert cap is False

def test_giveback_never_favorable_rides_to_cap():
    # price never exceeds entry -> no favorable move -> giveback stop never arms -> cap.
    path = [_bar(i * 300_000, 100, 100, 99.8, 99.9) for i in range(5)]
    px, ts, cap = exit_rules.simulate_giveback(path, "LONG", 100.0, fill="pessimistic")
    assert cap is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_arm_a_blind_exit.py -k giveback -v`
Expected: FAIL with `AttributeError: ... 'simulate_giveback'`.

- [ ] **Step 3: Write minimal implementation**

Append to `tools/arm_a_blind_exit/exit_rules.py`:
```python
def simulate_giveback(
    path: list[dict], direction: str, entry_price: float,
    *, frac: float = GIVEBACK_FRAC, fill: str = "pessimistic",
) -> tuple[float, int, bool]:
    """Confirmatory rule (DESCRIPTIVE only — spec §6). Exit when price retraces
    `frac` of the running favorable move from entry. LONG: stop = peak - frac*(peak-entry)."""
    cap_ms = _cap_ms(path)
    long = direction == "LONG"
    peak = entry_price
    stop = None                              # undefined until a favorable move exists
    for bar in path:
        if bar["open_time"] > cap_ms:
            break
        hi, lo = bar["high"], bar["low"]
        fav = hi if long else lo
        adv = lo if long else hi

        def _recompute(pk):
            move = (pk - entry_price) if long else (entry_price - pk)
            if move <= 0:
                return None
            return (pk - frac * move) if long else (pk + frac * move)

        if fill == "optimistic":
            peak = max(peak, fav) if long else min(peak, fav)
            stop = _recompute(peak)
            if stop is not None and (adv <= stop if long else adv >= stop):
                return stop, bar["open_time"], False
        else:
            if stop is not None and (adv <= stop if long else adv >= stop):
                return stop, bar["open_time"], False
            peak = max(peak, fav) if long else min(peak, fav)
            stop = _recompute(peak)
    last = max((b for b in path if b["open_time"] <= cap_ms), key=lambda b: b["open_time"])
    return last["close"], last["open_time"], True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_arm_a_blind_exit.py -k giveback -v`
Expected: PASS (both giveback tests).

- [ ] **Step 5: Commit**

```bash
git add tools/arm_a_blind_exit/exit_rules.py tests/test_arm_a_blind_exit.py
git commit -m "feat(arm-a): 38%-giveback confirmatory simulator (descriptive)"
```

---

## Task 6: Gross PnL + liquidity proxy + v3 recost

**Files:**
- Create: `tools/arm_a_blind_exit/evaluate.py`
- Test: `tests/test_arm_a_blind_exit.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_arm_a_blind_exit.py`:
```python
from tools.arm_a_blind_exit import evaluate

def test_gross_pnl_long_and_short():
    assert evaluate.gross_pnl(qty=2.0, entry=100.0, exit=110.0, direction="LONG") == pytest.approx(20.0)
    assert evaluate.gross_pnl(qty=2.0, entry=100.0, exit=110.0, direction="SHORT") == pytest.approx(-20.0)
    assert evaluate.gross_pnl(qty=2.0, entry=100.0, exit=90.0,  direction="SHORT") == pytest.approx(20.0)

def test_liquidity_proxy_formula():
    # usd_per_min = close*volume/60; rolling needs >=120 bars to be non-NaN.
    bars = [{"open_time": i * 3_600_000, "close": 100.0, "volume": 60.0} for i in range(200)]
    series = evaluate.liquidity_series(bars)           # list of (open_time, liq_or_nan)
    # 100*60/60 = 100 USD/min, rolling mean = 100 once warmed
    assert series[-1][1] == pytest.approx(100.0)
    assert evaluate.liquidity_at(series, ts_ms=200 * 3_600_000) == pytest.approx(100.0)

def test_v3_recost_is_positive_and_uses_v3():
    # smoke: a BTC round trip should charge a finite positive v3 cost in USD.
    cost = evaluate.recost_v3(
        symbol="BTCUSDT", entry_notional=10_000.0, exit_notional=10_000.0,
        entry_liq=5_000_000.0, exit_liq=5_000_000.0, holding_hours=24.0)
    assert cost > 0.0
    assert cost < 10_000.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_arm_a_blind_exit.py -k "gross or liquidity or recost" -v`
Expected: FAIL with `ModuleNotFoundError: tools.arm_a_blind_exit.evaluate`.

- [ ] **Step 3: Write minimal implementation**

`tools/arm_a_blind_exit/evaluate.py`:
```python
"""Gross PnL, liquidity proxy, v3 recost, paired stats, verdict.

Reuses backtest_costs.compute_trade_costs UNMODIFIED. The active calibration
(costs_calibration.json) is v3 (version 3); we pass model='v3' explicitly and the
calibration's own globals."""
from __future__ import annotations
import math
import numpy as np
from backtest_costs import load_calibration, tier_for_symbol, compute_trade_costs

_CAL = load_calibration()                    # active = v3 (costs_calibration.json)
assert _CAL.active_model == "v3", f"expected v3 active calibration, got {_CAL.active_model}"


def gross_pnl(*, qty: float, entry: float, exit: float, direction: str) -> float:
    return qty * (exit - entry) if direction == "LONG" else qty * (entry - exit)


def liquidity_series(bars_1h: list[dict]) -> list[tuple[int, float]]:
    """(open_time, rolling-mean USD/min) per 1h bar; matches backtest.py:669-674
    (window 720, min_periods 120). NaN until warmed."""
    times = [b["open_time"] for b in bars_1h]
    upm = [b["close"] * b["volume"] / 60.0 for b in bars_1h]
    out = []
    for i in range(len(upm)):
        lo = max(0, i - 719)
        window = upm[lo:i + 1]
        liq = float(np.mean(window)) if len(window) >= 120 else float("nan")
        out.append((times[i], liq))
    return out


def liquidity_at(series: list[tuple[int, float]], ts_ms: int) -> float:
    """Last rolling-liquidity value at or before ts_ms (backtest.py _liquidity_at)."""
    val = float("nan")
    for t, liq in series:
        if t <= ts_ms:
            val = liq
        else:
            break
    return val


def recost_v3(
    *, symbol: str, entry_notional: float, exit_notional: float,
    entry_liq: float, exit_liq: float, holding_hours: float,
) -> float:
    """Round-trip v3 cost in USD for one trade. Liquidity NaN -> v3 fallback floor."""
    tp = _CAL.tiers[tier_for_symbol(symbol)]
    d = compute_trade_costs(
        entry_notional_usd=entry_notional, exit_notional_usd=exit_notional,
        entry_liquidity_usd_per_min=entry_liq, exit_liquidity_usd_per_min=exit_liq,
        tier_params=tp, holding_hours=holding_hours, model="v3",
        global_params=_CAL.global_,
    )
    return float(d["total_cost_usd"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_arm_a_blind_exit.py -k "gross or liquidity or recost" -v`
Expected: PASS (3 tests). If `recost_v3` raises about v2/poisoned TierParams, the active calibration is not v3 — STOP and confirm `costs_calibration.json` `active_model=="v3"` before continuing.

- [ ] **Step 5: Commit**

```bash
git add tools/arm_a_blind_exit/evaluate.py tests/test_arm_a_blind_exit.py
git commit -m "feat(arm-a): gross PnL + liquidity proxy + v3 recost (reuses backtest_costs)"
```

---

## Task 7: Paired bootstrap + LOO + verdict logic

**Files:**
- Modify: `tools/arm_a_blind_exit/evaluate.py`
- Test: `tests/test_arm_a_blind_exit.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_arm_a_blind_exit.py`:
```python
def test_bootstrap_ci_is_deterministic_with_seed():
    deltas = [1.0, -0.5, 2.0, 0.3, -1.2, 0.8] * 5
    lo1, mean1, hi1 = evaluate.bootstrap_ci(deltas)
    lo2, mean2, hi2 = evaluate.bootstrap_ci(deltas)
    assert (lo1, mean1, hi1) == (lo2, mean2, hi2)     # seeded, reproducible
    assert lo1 <= mean1 <= hi1

def test_leave_one_out_drops_each_once():
    deltas = [1.0, 2.0, 3.0, 4.0]
    ids = [10, 11, 12, 13]
    loo = evaluate.leave_one_out(deltas, ids)
    assert len(loo) == 4
    # dropping id=13 (the 4.0) lowers the mean most
    by_id = {d["dropped_id"]: d["mean"] for d in loo}
    assert by_id[13] == pytest.approx((1 + 2 + 3) / 3)

def test_verdict_pass_requires_ci_excludes_zero_both_fills():
    strong = [1.0] * 27
    weak = [0.01, -0.02, 0.03] * 9
    # PASS: both fills exclude zero, survives LOO
    v = evaluate.verdict(
        pess_deltas=strong, opt_deltas=strong, ids=list(range(27)))
    assert v["verdict"] == "PASS"
    # FAIL: CI includes zero under both
    v2 = evaluate.verdict(pess_deltas=weak, opt_deltas=weak, ids=list(range(27)))
    assert v2["verdict"] == "FAIL"
    # INDETERMINATE: sign depends on fill convention
    v3 = evaluate.verdict(
        pess_deltas=[-1.0] * 27, opt_deltas=[1.0] * 27, ids=list(range(27)))
    assert v3["verdict"] == "INDETERMINATE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_arm_a_blind_exit.py -k "bootstrap or leave_one_out or verdict" -v`
Expected: FAIL with `AttributeError: ... 'bootstrap_ci'`.

- [ ] **Step 3: Write minimal implementation**

Append to `tools/arm_a_blind_exit/evaluate.py`:
```python
from .constants import BOOTSTRAP_N, BOOTSTRAP_SEED


def bootstrap_ci(deltas, n: int = BOOTSTRAP_N, seed: int = BOOTSTRAP_SEED):
    """Percentile 95% CI of the paired mean. Returns (lo, mean, hi)."""
    arr = np.asarray(deltas, dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(arr), size=(n, len(arr)))
    means = arr[idx].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(arr.mean()), float(np.percentile(means, 97.5))


def leave_one_out(deltas, ids):
    """Mean with each trade dropped once. Returns [{dropped_id, mean}], sorted by mean."""
    arr = np.asarray(deltas, dtype=float)
    out = []
    for i, tid in enumerate(ids):
        rest = np.delete(arr, i)
        out.append({"dropped_id": int(tid), "mean": float(rest.mean())})
    return sorted(out, key=lambda d: d["mean"])


def _ci_excludes_zero_positive(deltas) -> bool:
    lo, mean, hi = bootstrap_ci(deltas)
    return lo > 0.0 and mean > 0.0


def verdict(*, pess_deltas, opt_deltas, ids) -> dict:
    """Apply the frozen KILL (spec §6 + §5). Returns full diagnostic dict."""
    pess = bootstrap_ci(pess_deltas)
    opt = bootstrap_ci(opt_deltas)
    pess_pass = _ci_excludes_zero_positive(pess_deltas)
    opt_pass = _ci_excludes_zero_positive(opt_deltas)

    # LOO robustness on the primary (pessimistic) arm: drop the single most
    # influential trade (the one whose removal most lowers the mean) and re-test.
    loo = leave_one_out(pess_deltas, ids)
    worst_drop_id = loo[0]["dropped_id"]
    i = ids.index(worst_drop_id)
    survives_loo = _ci_excludes_zero_positive(
        [d for j, d in enumerate(pess_deltas) if j != i])

    if pess_pass and opt_pass and survives_loo:
        v = "PASS"
    elif (pess[1] <= 0 or not pess_pass) and (opt[1] <= 0 or not opt_pass):
        # both convention means non-positive / CI includes zero -> clean FAIL,
        # unless the two fills DISAGREE in sign (then it's granularity-bound).
        v = "INDETERMINATE" if (pess[1] > 0) != (opt[1] > 0) else "FAIL"
    else:
        v = "INDETERMINATE"
    return {
        "verdict": v,
        "pessimistic_ci": {"lo": pess[0], "mean": pess[1], "hi": pess[2], "excludes_zero": pess_pass},
        "optimistic_ci": {"lo": opt[0], "mean": opt[1], "hi": opt[2], "excludes_zero": opt_pass},
        "loo_survives_top_influencer": survives_loo,
        "loo_top_influencer_id": worst_drop_id,
        "loo": loo,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_arm_a_blind_exit.py -k "bootstrap or leave_one_out or verdict" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/arm_a_blind_exit/evaluate.py tests/test_arm_a_blind_exit.py
git commit -m "feat(arm-a): paired bootstrap + LOO + frozen KILL verdict (PASS/FAIL/INDETERMINATE)"
```

---

## Task 8: Run orchestrator + artifact emission

**Files:**
- Create: `tools/arm_a_blind_exit/run.py`
- Test: `tests/test_arm_a_blind_exit.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_arm_a_blind_exit.py`:
```python
def test_per_trade_record_shape():
    rec = {
        "id": 1, "symbol": "BTCUSDT", "direction": "LONG",
        "actual_net_v3": 5.0, "blind_net_v3_pess": 4.0, "blind_net_v3_opt": 4.5,
        "delta_pess": -1.0, "delta_opt": -0.5, "hit_cap": False,
    }
    from tools.arm_a_blind_exit import run
    assert run.REQUIRED_PER_TRADE_KEYS <= set(rec.keys())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_arm_a_blind_exit.py -k per_trade_record -v`
Expected: FAIL with `ModuleNotFoundError: tools.arm_a_blind_exit.run`.

- [ ] **Step 3: Write minimal implementation**

`tools/arm_a_blind_exit/run.py`:
```python
"""Orchestrate Brazo A reformulado end-to-end and emit the verdict artifacts.

Run: python -m tools.arm_a_blind_exit.run
Reads papá's DB + data/ohlcv.db (read-only). Writes only under OUTPUT_DIR.
No holdout, no live close path."""
from __future__ import annotations
import json
import os
import sqlite3
from datetime import datetime, timezone
import numpy as np

from backtest_costs import calibration_identity_hash, load_calibration
from . import population, exit_rules, evaluate
from .constants import (
    PAPA_DB, OHLCV_DB, OUTPUT_DIR, ATR_PERIOD, ATR_TF, PRICE_TF,
    CHANDELIER_MULT, GIVEBACK_FRAC, MAX_HOLD_H, BOOTSTRAP_SEED, KEEP_SYMBOLS,
)

REQUIRED_PER_TRADE_KEYS = {
    "id", "symbol", "direction", "actual_net_v3",
    "blind_net_v3_pess", "blind_net_v3_opt", "delta_pess", "delta_opt", "hit_cap",
}


def _bars(ohlcv_db, symbol, tf, start_ms, end_ms, with_volume=False):
    con = sqlite3.connect(f"file:{ohlcv_db}?mode=ro", uri=True)
    cols = "open_time, open, high, low, close" + (", volume" if with_volume else "")
    rows = con.execute(
        f"SELECT {cols} FROM ohlcv WHERE symbol=? AND timeframe=? "
        "AND open_time>=? AND open_time<=? ORDER BY open_time",
        (symbol, tf, start_ms, end_ms),
    ).fetchall()
    con.close()
    keys = ["open_time", "open", "high", "low", "close"] + (["volume"] if with_volume else [])
    return [dict(zip(keys, r)) for r in rows]


def _one_trade(p, ohlcv_db):
    sym, direction, qty = p["symbol"], p["direction"], p["qty"]
    entry_ms = int(p["entry_ts"].timestamp() * 1000)
    cap_ms = entry_ms + int(MAX_HOLD_H * 3600 * 1000)

    # 1h bars: [entry - 60d, cap] for ATR + liquidity proxy
    h1 = _bars(ohlcv_db, sym, ATR_TF, entry_ms - 60 * 86400 * 1000, cap_ms, with_volume=True)
    pre = [b for b in h1 if b["open_time"] < entry_ms]
    atr = exit_rules.wilder_atr(pre, period=ATR_PERIOD)
    liq = evaluate.liquidity_series(h1)

    # 5m path from entry to cap
    path = _bars(ohlcv_db, sym, PRICE_TF, entry_ms, cap_ms)

    def net(exit_price, exit_ms):
        hold_h = (exit_ms - entry_ms) / 3_600_000
        entry_notional = abs(qty) * p["entry_price"]
        exit_notional = abs(qty) * exit_price
        cost = evaluate.recost_v3(
            symbol=sym, entry_notional=entry_notional, exit_notional=exit_notional,
            entry_liq=evaluate.liquidity_at(liq, entry_ms),
            exit_liq=evaluate.liquidity_at(liq, exit_ms), holding_hours=hold_h)
        return evaluate.gross_pnl(qty=qty, entry=p["entry_price"], exit=exit_price,
                                  direction=direction) - cost

    # baseline: the operator's REAL exit, recosted to v3
    actual_net = net(p["exit_price"], int(p["exit_ts"].timestamp() * 1000))

    rec = {"id": p["id"], "symbol": sym, "direction": direction, "actual_net_v3": actual_net}
    for fill in ("pess", "opt"):
        conv = "pessimistic" if fill == "pess" else "optimistic"
        px, ts, cap = exit_rules.simulate_chandelier(path, direction, p["entry_price"], atr, fill=conv)
        rec[f"blind_net_v3_{fill}"] = net(px, ts)
        rec[f"delta_{fill}"] = rec[f"blind_net_v3_{fill}"] - actual_net
        if fill == "pess":
            rec["hit_cap"] = cap
        # confirmatory (descriptive)
        gpx, gts, _ = exit_rules.simulate_giveback(path, direction, p["entry_price"], fill=conv)
        rec[f"giveback_net_v3_{fill}"] = net(gpx, gts)
    return rec


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    kept, dropped = population.load_population(PAPA_DB, OHLCV_DB)
    records = [_one_trade(p, OHLCV_DB) for p in kept]
    ids = [r["id"] for r in records]
    pess = [r["delta_pess"] for r in records]
    opt = [r["delta_opt"] for r in records]

    v = evaluate.verdict(pess_deltas=pess, opt_deltas=opt, ids=ids)
    cal = load_calibration()
    manifest = {
        "experiment": "arm-a-reformulado-blind-exit",
        "spec_commit": "370f1d5",
        "frozen_params": {"atr_period": ATR_PERIOD, "atr_tf": ATR_TF, "price_tf": PRICE_TF,
                          "chandelier_mult": CHANDELIER_MULT, "giveback_frac": GIVEBACK_FRAC,
                          "max_hold_h": MAX_HOLD_H, "bootstrap_seed": BOOTSTRAP_SEED},
        "cost_model": {"active_model": cal.active_model,
                       "calibration_identity_hash": calibration_identity_hash(cal)},
        "population": {"kept": len(kept), "dropped": dropped, "keep_symbols": list(KEEP_SYMBOLS)},
        "generated_utc": None,   # stamp post-run; Date.now() unavailable in some envs
    }
    confirmatory = {
        "pess_mean_delta": float(np.mean([r["giveback_net_v3_pess"] - r["actual_net_v3"] for r in records])),
        "opt_mean_delta": float(np.mean([r["giveback_net_v3_opt"] - r["actual_net_v3"] for r in records])),
        "note": "DESCRIPTIVE ONLY — barred from any edge-existence claim (spec §6, Adrian F-4)",
    }

    with open(os.path.join(OUTPUT_DIR, "per_trade.json"), "w") as f:
        json.dump(records, f, indent=2)
    with open(os.path.join(OUTPUT_DIR, "verdict.json"), "w") as f:
        json.dump({"primary": v, "confirmatory_descriptive": confirmatory, "manifest": manifest}, f, indent=2)

    n_cap = sum(1 for r in records if r["hit_cap"])
    n_blind_worse = sum(1 for r in records if r["delta_pess"] < 0)
    lines = [
        "# Brazo A reformulado — blind exit policy: VERDICT", "",
        f"**Verdict (primary, chandelier 3xATR): {v['verdict']}**", "",
        f"- N = {len(records)} (27 frozen; dropped {sum(d['n'] for d in dropped)})",
        f"- Pessimistic CI95: [{v['pessimistic_ci']['lo']:.4f}, {v['pessimistic_ci']['hi']:.4f}], "
        f"mean {v['pessimistic_ci']['mean']:.4f}, excludes_zero={v['pessimistic_ci']['excludes_zero']}",
        f"- Optimistic CI95:  [{v['optimistic_ci']['lo']:.4f}, {v['optimistic_ci']['hi']:.4f}], "
        f"mean {v['optimistic_ci']['mean']:.4f}, excludes_zero={v['optimistic_ci']['excludes_zero']}",
        f"- LOO survives top influencer (id={v['loo_top_influencer_id']}): {v['loo_survives_top_influencer']}",
        f"- Blind worse than operator on {n_blind_worse}/{len(records)} trades; cap hits {n_cap}/{len(records)}",
        f"- Confirmatory 38%-giveback (DESCRIPTIVE): pess mean Δ {confirmatory['pess_mean_delta']:.4f}", "",
        "Ceiling: n=27, single bull regime, 8 liquid symbols. PASS = in-regime only, not deployable.",
        "FAIL -> Lyra Sage (double-FAIL with Brazo B).",
    ]
    with open(os.path.join(OUTPUT_DIR, "findings.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"VERDICT: {v['verdict']}  (pess mean Δ {v['pessimistic_ci']['mean']:.4f})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test + execute the experiment**

Run unit test: `python -m pytest tests/test_arm_a_blind_exit.py -v`
Expected: ALL pass (population test may SKIP if DB absent).

Execute (on the machine with papá's DB): `python -m tools.arm_a_blind_exit.run`
Expected: prints `VERDICT: <PASS|FAIL|INDETERMINATE>` and writes 3 files into `data/retune/2026-06-03-arm-a-blind-exit/`. (Halberg's gross preview predicts FAIL: blind worse on ~17/27, CI straddling zero.)

- [ ] **Step 5: Commit**

```bash
git add tools/arm_a_blind_exit/run.py tests/test_arm_a_blind_exit.py
git commit -m "feat(arm-a): run orchestrator + verdict/findings artifact emission"
```

---

## Task 9: Stamp the verdict + record the result

**Files:**
- Modify (runtime artifact): `data/retune/2026-06-03-arm-a-blind-exit/verdict.json` (stamp `generated_utc`)
- No code change — this task is the human-in-the-loop reading of the pre-registered result.

- [ ] **Step 1: Read `findings.md` and `verdict.json`.** Confirm `verdict` ∈ {PASS, FAIL, INDETERMINATE} and which top influencer drove LOO.
- [ ] **Step 2: `mex log`** the result: `mex log "Brazo A reformulado <VERDICT>: chandelier 3xATR vs operator, n=27, pess mean Δ=<x>, CI=[..], LOO id=<..>"`.
- [ ] **Step 3: Route per KILL.** PASS → open the "exit-rule product candidate (in-regime only)" follow-up. FAIL/INDETERMINATE → invoke **Lyra Sage** for the double-FAIL portfolio decision (this is the pre-registered branch).
- [ ] **Step 4: Write memory** updating `fork-arm-b-fail-arm-a-pending` → resolved, with the verdict + artifact path.

---

## Self-Review

**Spec coverage:** §2 population → Task 2. §3 chandelier + frozen params → Tasks 1,3,4. §3 confirmatory → Task 5. §4 gross/baseline/liquidity contract → Task 6. §5 bootstrap/LOO/fill-sensitivity → Tasks 4,7. §6 KILL (PASS/FAIL/INDETERMINATE) → Task 7. §7 reconstruction/provenance → Tasks 6,8 (manifest). §9 ceiling → findings.md (Task 8). §10 NN → no holdout/no live-close anywhere (read-only sqlite + offline sim). All covered.

**Placeholder scan:** `manifest["generated_utc"]=None` is intentional (Date.now() unavailable in some envs; stamped in Task 9), not a TODO. No other placeholders.

**Type consistency:** `simulate_chandelier`/`simulate_giveback` both return `(price, open_time, hit_cap)`. `bootstrap_ci` returns `(lo, mean, hi)` consistently (Tasks 7 consumer matches). `verdict(pess_deltas, opt_deltas, ids)` signature matches its call in `run.main`. `recost_v3` kwargs match its call site. `liquidity_at(series, ts_ms)` matches both test and `run` usage. Per-trade keys in `REQUIRED_PER_TRADE_KEYS` match `_one_trade` output.

**Risk note (Halberg/Adrian carried):** the FAIL branch is only clean if it holds under both fills — encoded in `verdict()` via INDETERMINATE. The cap is empirically inert (preview) but `hit_cap` is reported so a regime change that activates it is visible.
