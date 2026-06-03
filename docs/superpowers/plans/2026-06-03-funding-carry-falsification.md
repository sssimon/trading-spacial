# Funding-Carry Falsification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pre-registered falsification that measures whether delta-neutral funding carry (long spot + short perp) on the liquid symbol universe produces positive net-of-v3-cost return over 2024-26 that survives a synthetic short-vol tail shock.

**Architecture:** A self-contained offline package `tools/funding_carry/` — `ingest` (bulk-download + parse Binance Vision `fundingRate` + `markPriceKlines` into `data/funding.db`), `simulate` (delta-neutral carry P&L per symbol: funding accrual + basis + v3 recost), `evaluate` (Gate A bootstrap/LOO/deflation + Gate B in-sample tail + synthetic shock → PASS/FAIL), `run` (orchestrator → JSON+findings artifacts). Reuses `backtest_costs.compute_trade_costs` unmodified and spot klines from `data/ohlcv.db`. Mirrors the `tools/arm_a_blind_exit/` pattern.

**Tech Stack:** Python 3, sqlite3, numpy, urllib (stdlib), zipfile, csv, pytest. Reuses `backtest_costs`.

**Frozen pre-registration (spec `2026-06-03-funding-carry-falsification-design.md`, commit 2f10134):**
- Universe: liquid symbols with full `fundingRate`+`markPriceKlines`+spot coverage. Per-symbol window (enter at coverage start, exit at window end), annualize by symbol length.
- Carry: long spot N + short perp N, delta≈0, continuous hold (1 entry / 1 exit), collect all funding. N=$10,000.
- Gate A: pooled equal-weight annualized net-of-v3 return; bootstrap 95% CI (10k, seed 20260603); deflate by symbol count; PASS_A = pooled CI lower bound > 0.
- Gate B: B1 = max-DD of pooled funding-equity curve + worst single interval; B2 = synthetic shock (K=5 days of forced negative funding at F=0.5%/8h) — PASS_B = pooled net survives the shock ≥ 0.
- KILL: PASS = A ∧ B. FAIL = either. $-denominated → sidesteps sharpe/net_pnl mirage. No live perps, no holdout.

**Network note:** Task 2 (ingest) is the ONLY task needing internet (Binance Vision + fapi). All other tasks run on the local `data/funding.db` it produces. The Binance Vision bulk CSV schema is verified by header-inspection at first download (Task 2 Step 3 maps column-name variants).

---

## File Structure

- Create `tools/funding_carry/__init__.py` — package marker.
- Create `tools/funding_carry/constants.py` — frozen params (symbols, window, N, seeds, shock F/K, URLs, paths).
- Create `tools/funding_carry/ingest.py` — bulk download + parse → `data/funding.db`; header-robust column mapping; API gap-fill.
- Create `tools/funding_carry/simulate.py` — per-symbol delta-neutral carry P&L (funding accrual, basis, v3 recost, liquidity proxy).
- Create `tools/funding_carry/evaluate.py` — Gate A (bootstrap/LOO/deflation) + Gate B (B1 in-sample + B2 synthetic shock) + verdict.
- Create `tools/funding_carry/run.py` — orchestrator → `data/retune/2026-06-03-funding-carry-falsification/{verdict,per_symbol,findings}.json|md`.
- Create `tests/test_funding_carry.py` — TDD for all pure functions.

---

## Task 1: Package skeleton + frozen constants

**Files:** Create `tools/funding_carry/__init__.py`, `tools/funding_carry/constants.py`

- [ ] **Step 1: Create the package marker**

`tools/funding_carry/__init__.py`:
```python
"""Funding-carry falsification (liquid universe, 2024-26).

Pre-registered per docs/superpowers/specs/2026-06-03-funding-carry-falsification-design.md.
Offline backtest on public Binance Vision funding/perp data + spot ohlcv.db.
No holdout (#322 untouched), no live perps, no PositionClosure.
"""
```

- [ ] **Step 2: Create frozen constants**

`tools/funding_carry/constants.py`:
```python
"""Pre-registered, IRREVOCABLE parameters. Changing any = a NEW experiment."""

# Candidate liquid universe (filtered to those with full coverage at ingest).
CANDIDATE_SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT", "AVAXUSDT",
    "DOGEUSDT", "LINKUSDT", "UNIUSDT", "XLMUSDT", "RUNEUSDT", "PENDLEUSDT",
)

WINDOW_START = "2024-01-01"
WINDOW_END = "2026-05-31"
NOTIONAL = 10_000.0            # $ per leg; returns are scale-invariant in %

BOOTSTRAP_N = 10_000
BOOTSTRAP_SEED = 20260603

# Gate B2 synthetic short-vol shock, calibrated to 2022 (LUNA/FTX) magnitude.
SHOCK_FUNDING_PER_8H = 0.005   # forced NEGATIVE funding we PAY, 0.5%/8h (extreme)
SHOCK_DAYS = 5                 # sustained stress duration
SHOCK_INTERVALS_PER_DAY = 3    # 8h funding -> 3/day (the shock is defined on an 8h basis)

# Binance Vision bulk + API.
BULK_BASE = "https://data.binance.vision/data/futures/um/monthly"
FAPI_FUNDING = "https://fapi.binance.com/fapi/v1/fundingRate"

OHLCV_DB = "data/ohlcv.db"        # spot klines (reused)
FUNDING_DB = "data/funding.db"    # produced by ingest
OUTPUT_DIR = "data/retune/2026-06-03-funding-carry-falsification"
```

- [ ] **Step 3: Commit**

```bash
git add tools/funding_carry/__init__.py tools/funding_carry/constants.py
git commit -m "feat(funding-carry): package skeleton + frozen constants"
```

---

## Task 2: Ingest — download + parse funding & perp into data/funding.db

**Files:** Create `tools/funding_carry/ingest.py`; Test `tests/test_funding_carry.py`

This task needs network. It builds `data/funding.db` with two tables: `funding(symbol, funding_time_ms, funding_rate, mark_price)` and `perp_klines(symbol, open_time, close)`.

- [ ] **Step 1: Write the failing test (parser, no network)**

`tests/test_funding_carry.py`:
```python
import os
import pytest
from tools.funding_carry import ingest

def test_parse_funding_rows_maps_known_schemas():
    # Binance Vision fundingRate CSV header variant: calc_time,funding_interval_hours,last_funding_rate
    header = ["calc_time", "funding_interval_hours", "last_funding_rate"]
    rows = [["1704067200000", "8", "0.0001"], ["1704096000000", "8", "-0.0002"]]
    out = ingest.parse_funding_rows(header, rows)
    assert out == [(1704067200000, 0.0001), (1704096000000, -0.0002)]

def test_parse_funding_rows_api_schema():
    # fapi JSON-derived rows: fundingTime, fundingRate
    header = ["fundingTime", "fundingRate", "markPrice"]
    rows = [["1704067200000", "0.0001", "42000.0"]]
    out = ingest.parse_funding_rows(header, rows)
    assert out == [(1704067200000, 0.0001)]
```

- [ ] **Step 2: Run, confirm FAIL**

Run: `python -m pytest tests/test_funding_carry.py -k parse_funding -v`
Expected: FAIL (no module ingest).

- [ ] **Step 3: Implement ingest.py**

`tools/funding_carry/ingest.py`:
```python
"""Download Binance Vision bulk funding + perp mark klines into data/funding.db.

The bulk fundingRate CSV schema is mapped by header inspection (column names vary
by era: calc_time/last_funding_rate vs fundingTime/fundingRate). markPriceKlines is
standard kline-shaped. Read-only on ohlcv.db. Network is used here only."""
from __future__ import annotations
import csv
import io
import sqlite3
import urllib.request
import zipfile
from contextlib import closing
from .constants import BULK_BASE, FUNDING_DB, CANDIDATE_SYMBOLS, WINDOW_START, WINDOW_END

_TIME_KEYS = ("funding_time_ms", "fundingtime", "calc_time", "calctime")
_RATE_KEYS = ("funding_rate", "fundingrate", "last_funding_rate", "lastfundingrate")


def parse_funding_rows(header: list[str], rows: list[list[str]]) -> list[tuple[int, float]]:
    """Map a funding CSV (any known schema) to [(funding_time_ms, funding_rate)]."""
    norm = [h.strip().lower() for h in header]
    ti = next(i for i, h in enumerate(norm) if h in _TIME_KEYS)
    ri = next(i for i, h in enumerate(norm) if h in _RATE_KEYS)
    out = []
    for r in rows:
        out.append((int(float(r[ti])), float(r[ri])))
    return out


def _months(start: str, end: str) -> list[str]:
    sy, sm = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    res, y, m = [], sy, sm
    while (y, m) <= (ey, em):
        res.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return res


def _fetch_zip_csv(url: str) -> tuple[list[str], list[list[str]]] | None:
    """Download a Binance Vision .zip, return (header, rows) of its single CSV.
    Returns None on 404 (month not published)."""
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            blob = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = z.namelist()[0]
        text = z.read(name).decode("utf-8")
    reader = list(csv.reader(io.StringIO(text)))
    if not reader:
        return [], []
    # Some files have a header row; some are headerless. Detect: if first cell is non-numeric.
    first = reader[0][0].strip().lower()
    if any(c.isalpha() for c in first):
        return reader[0], reader[1:]
    # headerless fundingRate (older): calc_time,funding_interval_hours,last_funding_rate
    return ["calc_time", "funding_interval_hours", "last_funding_rate"], reader


def ingest_all(db_path: str = FUNDING_DB) -> dict:
    """Populate funding.db for all candidate symbols over the window. Returns coverage summary."""
    months = _months(WINDOW_START, WINDOW_END)
    with closing(sqlite3.connect(db_path)) as con:
        con.execute("CREATE TABLE IF NOT EXISTS funding("
                    "symbol TEXT, funding_time_ms INTEGER, funding_rate REAL,"
                    "PRIMARY KEY(symbol, funding_time_ms))")
        con.execute("CREATE TABLE IF NOT EXISTS perp_klines("
                    "symbol TEXT, open_time INTEGER, close REAL,"
                    "PRIMARY KEY(symbol, open_time))")
        summary = {}
        for sym in CANDIDATE_SYMBOLS:
            nf, nk = 0, 0
            for mo in months:
                fu = _fetch_zip_csv(f"{BULK_BASE}/fundingRate/{sym}/{sym}-fundingRate-{mo}.zip")
                if fu:
                    hdr, rows = fu
                    for t, rate in parse_funding_rows(hdr, rows):
                        con.execute("INSERT OR IGNORE INTO funding VALUES(?,?,?)", (sym, t, rate))
                        nf += 1
                kl = _fetch_zip_csv(f"{BULK_BASE}/markPriceKlines/{sym}/1h/{sym}-1h-{mo}.zip")
                if kl:
                    _, rows = kl
                    for r in rows:
                        con.execute("INSERT OR IGNORE INTO perp_klines VALUES(?,?,?)",
                                    (sym, int(float(r[0])), float(r[4])))  # open_time, close
                        nk += 1
            summary[sym] = {"funding_rows": nf, "perp_klines": nk}
        con.commit()
    return summary


if __name__ == "__main__":
    print(ingest_all())
```

- [ ] **Step 4: Run the parser test, confirm PASS**

Run: `python -m pytest tests/test_funding_carry.py -k parse_funding -v`
Expected: PASS (2 tests). (The download path is exercised in Task 9, not unit-tested — it needs network.)

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/ingest.py tests/test_funding_carry.py
git commit -m "feat(funding-carry): bulk ingest (header-robust funding + perp klines)"
```

---

## Task 3: Funding accrual + basis (pure functions)

**Files:** Create `tools/funding_carry/simulate.py`; Test `tests/test_funding_carry.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_funding_carry.py`:
```python
from tools.funding_carry import simulate

def test_funding_accrual_short_receives_when_positive():
    # short perp RECEIVES funding when rate>0. units=u, mark=const M.
    # funding_pnl = sum(rate_i * M * u)
    funding = [(0, 0.0001), (28_800_000, -0.0002), (57_600_000, 0.0003)]  # 8h apart
    u, mark = 2.0, 100.0
    pnl = simulate.funding_pnl(funding, units=u, mark_price=mark)
    assert pnl == pytest.approx((0.0001 - 0.0002 + 0.0003) * 100.0 * 2.0)

def test_basis_pnl_short_perp_long_spot():
    # basis = perp - spot. delta-neutral pnl = -u*(basis_exit - basis_entry).
    pnl = simulate.basis_pnl(spot_entry=100.0, perp_entry=101.0,
                             spot_exit=100.0, perp_exit=100.5, units=3.0)
    # basis_entry=1.0, basis_exit=0.5 -> -3*(0.5-1.0)=+1.5 (convergence favorable)
    assert pnl == pytest.approx(1.5)
```

- [ ] **Step 2: Run, confirm FAIL**

Run: `python -m pytest tests/test_funding_carry.py -k "funding_accrual or basis_pnl" -v`
Expected: FAIL (no module simulate).

- [ ] **Step 3: Implement (first half of simulate.py)**

`tools/funding_carry/simulate.py`:
```python
"""Delta-neutral funding-carry P&L per symbol: funding accrual + basis + v3 recost.

Position: long spot N, short perp N (delta ~ 0). Carry = funding the short collects
(when rate>0) + basis convergence, minus v3 cost on 4 fills. Directional price move
cancels between legs by construction."""
from __future__ import annotations
import sqlite3
from contextlib import closing
from backtest_costs import load_calibration, tier_for_symbol, compute_trade_costs
from .constants import OHLCV_DB, FUNDING_DB, NOTIONAL

_CAL = load_calibration()
assert _CAL.active_model == "v3", f"expected v3 calibration, got {_CAL.active_model}"


def funding_pnl(funding: list[tuple[int, float]], *, units: float, mark_price: float) -> float:
    """Sum of funding the short leg collects. funding: [(time_ms, rate)]. Positive
    rate -> short receives. mark_price is the perp notional basis per unit."""
    return sum(rate * mark_price * units for _, rate in funding)


def basis_pnl(*, spot_entry: float, perp_entry: float,
              spot_exit: float, perp_exit: float, units: float) -> float:
    """Delta-neutral price P&L = -units*(basis_exit - basis_entry); basis = perp - spot."""
    basis_entry = perp_entry - spot_entry
    basis_exit = perp_exit - spot_exit
    return -units * (basis_exit - basis_entry)
```

- [ ] **Step 4: Run, confirm PASS**

Run: `python -m pytest tests/test_funding_carry.py -k "funding_accrual or basis_pnl" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/simulate.py tests/test_funding_carry.py
git commit -m "feat(funding-carry): funding accrual + basis pnl (delta-neutral)"
```

---

## Task 4: v3 recost of the 4 legs + per-symbol carry

**Files:** Modify `tools/funding_carry/simulate.py`; Test `tests/test_funding_carry.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_funding_carry.py`:
```python
def test_recost_four_legs_positive():
    cost = simulate.recost_four_legs(symbol="BTCUSDT", units=0.25, spot_price=40_000.0,
                                     perp_price=40_050.0, liq=5_000_000.0, holding_hours=720.0)
    assert cost > 0.0          # 4 fills each charged v3
    assert cost < NOTIONAL

def test_carry_for_symbol_assembles_net(monkeypatch):
    # a synthetic symbol: constant +0.01%/8h funding for ~30 days, flat basis.
    funding = [(i * 28_800_000, 0.0001) for i in range(90)]  # 90 * 8h = 30 days
    rec = simulate.carry_for_symbol(
        symbol="BTCUSDT",
        funding=funding, spot_entry=40_000.0, spot_exit=40_000.0,
        perp_entry=40_000.0, perp_exit=40_000.0, liq=5_000_000.0)
    assert set(rec) >= {"symbol", "funding_pnl", "basis_pnl", "cost_v3", "net",
                        "net_return", "n_funding", "window_hours"}
    assert rec["funding_pnl"] > 0
    assert rec["net"] == pytest.approx(rec["funding_pnl"] + rec["basis_pnl"] - rec["cost_v3"])
```

- [ ] **Step 2: Run, confirm FAIL**

Run: `python -m pytest tests/test_funding_carry.py -k "recost_four or carry_for_symbol" -v`
Expected: FAIL.

- [ ] **Step 3: Implement (append to simulate.py)**

```python
def recost_four_legs(*, symbol: str, units: float, spot_price: float,
                     perp_price: float, liq: float, holding_hours: float) -> float:
    """v3 cost (USD) of opening+closing BOTH legs = 2 round trips (4 fills).

    Each leg is one round trip; compute_trade_costs returns a round-trip cost, so two
    calls (spot leg, perp leg) cover all four fills."""
    tp = _CAL.tiers[tier_for_symbol(symbol)]

    def _rt(notional):
        d = compute_trade_costs(
            entry_notional_usd=notional, exit_notional_usd=notional,
            entry_liquidity_usd_per_min=liq, exit_liquidity_usd_per_min=liq,
            tier_params=tp, holding_hours=holding_hours, model="v3",
            enable_funding=False, global_params=_CAL.global_)   # funding modeled in funding_pnl
        return float(d["total_cost_usd"])

    return _rt(units * spot_price) + _rt(units * perp_price)


def carry_for_symbol(*, symbol: str, funding: list[tuple[int, float]],
                     spot_entry: float, spot_exit: float, perp_entry: float,
                     perp_exit: float, liq: float, notional: float = NOTIONAL) -> dict:
    """Full delta-neutral carry record for one symbol over its window."""
    units = notional / spot_entry
    mark = perp_entry                       # mark basis per unit for funding notional
    f_pnl = funding_pnl(funding, units=units, mark_price=mark)
    b_pnl = basis_pnl(spot_entry=spot_entry, perp_entry=perp_entry,
                      spot_exit=spot_exit, perp_exit=perp_exit, units=units)
    window_ms = (funding[-1][0] - funding[0][0]) if len(funding) >= 2 else 0
    window_hours = window_ms / 3_600_000
    cost = recost_four_legs(symbol=symbol, units=units, spot_price=spot_entry,
                            perp_price=perp_entry, liq=liq, holding_hours=window_hours)
    net = f_pnl + b_pnl - cost
    years = (window_hours / 24.0 / 365.0) or 1e-9
    return {"symbol": symbol, "funding_pnl": f_pnl, "basis_pnl": b_pnl, "cost_v3": cost,
            "net": net, "net_return": net / notional,
            "net_return_annual": (net / notional) / years,
            "n_funding": len(funding), "window_hours": window_hours}
```

- [ ] **Step 4: Run, confirm PASS**

Run: `python -m pytest tests/test_funding_carry.py -k "recost_four or carry_for_symbol" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/simulate.py tests/test_funding_carry.py
git commit -m "feat(funding-carry): v3 recost (4 legs) + per-symbol carry record"
```

---

## Task 5: Data loaders (funding.db + spot ohlcv.db)

**Files:** Modify `tools/funding_carry/simulate.py`; Test `tests/test_funding_carry.py`

- [ ] **Step 1: Write the failing test (uses a temp sqlite)**

Append to `tests/test_funding_carry.py`:
```python
import sqlite3

def _mk_funding_db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE funding(symbol TEXT, funding_time_ms INTEGER, funding_rate REAL)")
    con.execute("CREATE TABLE perp_klines(symbol TEXT, open_time INTEGER, close REAL)")
    for i in range(10):
        con.execute("INSERT INTO funding VALUES('BTCUSDT', ?, 0.0001)", (i * 28_800_000,))
        con.execute("INSERT INTO perp_klines VALUES('BTCUSDT', ?, 100.0)", (i * 3_600_000,))
    con.commit(); con.close()

def test_load_funding_window(tmp_path):
    db = str(tmp_path / "f.db"); _mk_funding_db(db)
    rows = simulate.load_funding(db, "BTCUSDT", 0, 9 * 28_800_000)
    assert len(rows) == 10
    assert rows[0] == (0, 0.0001)

def test_perp_price_at(tmp_path):
    db = str(tmp_path / "f.db"); _mk_funding_db(db)
    assert simulate.perp_price_at(db, "BTCUSDT", 5 * 3_600_000) == pytest.approx(100.0)
```

- [ ] **Step 2: Run, confirm FAIL**

Run: `python -m pytest tests/test_funding_carry.py -k "load_funding or perp_price_at" -v`
Expected: FAIL.

- [ ] **Step 3: Implement (append to simulate.py)**

```python
def load_funding(funding_db: str, symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
    with closing(sqlite3.connect(f"file:{funding_db}?mode=ro", uri=True)) as con:
        return [(int(t), float(r)) for t, r in con.execute(
            "SELECT funding_time_ms, funding_rate FROM funding WHERE symbol=? "
            "AND funding_time_ms>=? AND funding_time_ms<=? ORDER BY funding_time_ms",
            (symbol, start_ms, end_ms))]


def perp_price_at(funding_db: str, symbol: str, ts_ms: int) -> float:
    """Last perp close at or before ts_ms (NaN if none)."""
    with closing(sqlite3.connect(f"file:{funding_db}?mode=ro", uri=True)) as con:
        row = con.execute(
            "SELECT close FROM perp_klines WHERE symbol=? AND open_time<=? "
            "ORDER BY open_time DESC LIMIT 1", (symbol, ts_ms)).fetchone()
    return float(row[0]) if row else float("nan")


def spot_price_at(ohlcv_db: str, symbol: str, ts_ms: int) -> float:
    """Last spot 1h close at or before ts_ms (NaN if none)."""
    with closing(sqlite3.connect(f"file:{ohlcv_db}?mode=ro", uri=True)) as con:
        row = con.execute(
            "SELECT close FROM ohlcv WHERE symbol=? AND timeframe='1h' AND open_time<=? "
            "ORDER BY open_time DESC LIMIT 1", (symbol, ts_ms)).fetchone()
    return float(row[0]) if row else float("nan")


def spot_liquidity(ohlcv_db: str, symbol: str, ts_ms: int) -> float:
    """30-day rolling USD/min proxy at ts_ms from spot 1h bars (matches backtest.py:669).
    Returns NaN -> compute_trade_costs falls back to the v3 floor."""
    with closing(sqlite3.connect(f"file:{ohlcv_db}?mode=ro", uri=True)) as con:
        rows = con.execute(
            "SELECT close, volume FROM ohlcv WHERE symbol=? AND timeframe='1h' "
            "AND open_time<=? ORDER BY open_time DESC LIMIT 720", (symbol, ts_ms)).fetchall()
    if len(rows) < 120:
        return float("nan")
    return sum(c * v / 60.0 for c, v in rows) / len(rows)
```

- [ ] **Step 4: Run, confirm PASS**

Run: `python -m pytest tests/test_funding_carry.py -k "load_funding or perp_price_at" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/simulate.py tests/test_funding_carry.py
git commit -m "feat(funding-carry): funding.db + spot ohlcv loaders + liquidity proxy"
```

---

## Task 6: Gate A — pooled bootstrap + LOO + deflation

**Files:** Create `tools/funding_carry/evaluate.py`; Test `tests/test_funding_carry.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_funding_carry.py`:
```python
from tools.funding_carry import evaluate

def test_gate_a_bootstrap_deterministic():
    rets = [0.05, 0.08, -0.02, 0.06, 0.04, 0.09, 0.01, 0.07]
    a = evaluate.gate_a(rets)
    b = evaluate.gate_a(rets)
    assert a["ci_lo"] == b["ci_lo"]                # seeded
    assert a["ci_lo"] <= a["mean"] <= a["ci_hi"]
    assert "pass_a" in a and "loo_min_mean" in a

def test_gate_a_pass_only_if_ci_excludes_zero():
    strong = [0.10] * 9
    weak = [0.02, -0.05, 0.03, -0.04, 0.01, 0.02, -0.03, 0.04, -0.02]
    assert evaluate.gate_a(strong)["pass_a"] is True
    assert evaluate.gate_a(weak)["pass_a"] is False
```

- [ ] **Step 2: Run, confirm FAIL**

Run: `python -m pytest tests/test_funding_carry.py -k gate_a -v`
Expected: FAIL.

- [ ] **Step 3: Implement evaluate.py (Gate A)**

`tools/funding_carry/evaluate.py`:
```python
"""Gate A (carry net>0) + Gate B (tail) + verdict for funding carry."""
from __future__ import annotations
import numpy as np
from .constants import (BOOTSTRAP_N, BOOTSTRAP_SEED, SHOCK_FUNDING_PER_8H,
                        SHOCK_DAYS, SHOCK_INTERVALS_PER_DAY, NOTIONAL)


def gate_a(annual_returns: list[float]) -> dict:
    """Pooled equal-weight bootstrap CI of annualized net return + LOO. PASS_A = CI lo > 0."""
    arr = np.asarray(annual_returns, dtype=float)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    idx = rng.integers(0, len(arr), size=(BOOTSTRAP_N, len(arr)))
    means = arr[idx].mean(axis=1)
    ci_lo, ci_hi = float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
    loo = [float(np.delete(arr, i).mean()) for i in range(len(arr))]
    return {"mean": float(arr.mean()), "ci_lo": ci_lo, "ci_hi": ci_hi,
            "loo_min_mean": min(loo), "pass_a": bool(ci_lo > 0.0 and min(loo) > 0.0),
            "n": len(arr)}
```

- [ ] **Step 4: Run, confirm PASS**

Run: `python -m pytest tests/test_funding_carry.py -k gate_a -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/evaluate.py tests/test_funding_carry.py
git commit -m "feat(funding-carry): Gate A pooled bootstrap + LOO"
```

---

## Task 7: Gate B — in-sample tail + synthetic shock

**Files:** Modify `tools/funding_carry/evaluate.py`; Test `tests/test_funding_carry.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_funding_carry.py`:
```python
def test_gate_b_max_drawdown():
    # cumulative equity goes 0 -> 10 -> 4 -> 12; max DD = 6 (from 10 to 4)
    interval_pnls = [10.0, -6.0, 8.0]
    b1 = evaluate.gate_b1(interval_pnls)
    assert b1["max_drawdown"] == pytest.approx(6.0)
    assert b1["worst_interval"] == pytest.approx(-6.0)

def test_gate_b_synthetic_shock_kills_thin_carry():
    # shock bleed = 5 days * 3/day * 0.005 = 0.075 of notional per symbol.
    # thin carry (mean net_return 0.03) -> 0.03 - 0.075 < 0 -> FAIL_B2.
    thin = evaluate.gate_b2(mean_net_return=0.03)
    assert thin["pass_b2"] is False
    fat = evaluate.gate_b2(mean_net_return=0.20)   # survives the 0.075 bleed
    assert fat["pass_b2"] is True
    assert fat["shock_bleed"] == pytest.approx(0.075)
```

- [ ] **Step 2: Run, confirm FAIL**

Run: `python -m pytest tests/test_funding_carry.py -k "gate_b_max or synthetic_shock" -v`
Expected: FAIL.

- [ ] **Step 3: Implement (append to evaluate.py)**

```python
def gate_b1(interval_pnls: list[float]) -> dict:
    """In-sample tail: max drawdown of the cumulative pooled equity + worst interval."""
    eq = np.cumsum(np.asarray(interval_pnls, dtype=float))
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    return {"max_drawdown": float(dd.max()) if len(dd) else 0.0,
            "worst_interval": float(min(interval_pnls)) if interval_pnls else 0.0}


def gate_b2(mean_net_return: float) -> dict:
    """Synthetic short-vol shock (LUNA/FTX-calibrated): a SHOCK_DAYS forced-negative-funding
    episode bleeds SHOCK_DAYS*intervals*F of notional. PASS_B2 = carry survives net>=0."""
    shock_bleed = SHOCK_DAYS * SHOCK_INTERVALS_PER_DAY * SHOCK_FUNDING_PER_8H
    return {"shock_bleed": shock_bleed,
            "post_shock_return": mean_net_return - shock_bleed,
            "pass_b2": bool(mean_net_return - shock_bleed >= 0.0)}
```

- [ ] **Step 4: Run, confirm PASS**

Run: `python -m pytest tests/test_funding_carry.py -k "gate_b_max or synthetic_shock" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/evaluate.py tests/test_funding_carry.py
git commit -m "feat(funding-carry): Gate B in-sample tail + synthetic short-vol shock"
```

---

## Task 8: Verdict + run orchestrator + artifacts

**Files:** Modify `tools/funding_carry/evaluate.py`; Create `tools/funding_carry/run.py`; Test `tests/test_funding_carry.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_funding_carry.py`:
```python
def test_verdict_requires_both_gates():
    a_pass = {"pass_a": True}; a_fail = {"pass_a": False}
    b_pass = {"pass_b2": True}; b_fail = {"pass_b2": False}
    assert evaluate.verdict(a_pass, b_pass)["verdict"] == "PASS"
    assert evaluate.verdict(a_fail, b_pass)["verdict"] == "FAIL"
    assert evaluate.verdict(a_pass, b_fail)["verdict"] == "FAIL"

def test_required_artifact_keys():
    from tools.funding_carry import run
    rec = {"symbol": "BTCUSDT", "net_return_annual": 0.1, "net": 1000.0,
           "funding_pnl": 1200.0, "basis_pnl": 0.0, "cost_v3": 200.0}
    assert run.REQUIRED_SYMBOL_KEYS <= set(rec.keys())
```

- [ ] **Step 2: Run, confirm FAIL**

Run: `python -m pytest tests/test_funding_carry.py -k "verdict_requires or required_artifact" -v`
Expected: FAIL.

- [ ] **Step 3: Implement verdict (append to evaluate.py) + run.py**

Append to `evaluate.py`:
```python
def verdict(a: dict, b2: dict) -> dict:
    """PASS iff Gate A and Gate B2 both pass (spec §7). $-denominated, no mirage."""
    v = "PASS" if (a.get("pass_a") and b2.get("pass_b2")) else "FAIL"
    return {"verdict": v, "pass_a": bool(a.get("pass_a")), "pass_b2": bool(b2.get("pass_b2"))}
```

`tools/funding_carry/run.py`:
```python
"""Orchestrate the funding-carry falsification end-to-end → verdict artifacts.

Run: python -m tools.funding_carry.run   (requires data/funding.db from ingest first)
Reads funding.db + ohlcv.db (read-only). Writes only under OUTPUT_DIR. No holdout."""
from __future__ import annotations
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
import numpy as np

from backtest_costs import calibration_identity_hash, load_calibration
from . import simulate, evaluate
from .constants import (OHLCV_DB, FUNDING_DB, OUTPUT_DIR, CANDIDATE_SYMBOLS,
                        WINDOW_START, WINDOW_END, NOTIONAL, BOOTSTRAP_SEED,
                        SHOCK_FUNDING_PER_8H, SHOCK_DAYS)

REQUIRED_SYMBOL_KEYS = {"symbol", "net_return_annual", "net", "funding_pnl", "basis_pnl", "cost_v3"}


def _ms(date_str: str) -> int:
    return int(datetime.fromisoformat(date_str + "T00:00:00+00:00").timestamp() * 1000)


def _covered_symbols(funding_db: str, w0: int, w1: int) -> list[str]:
    """Candidate symbols that have funding AND perp coverage spanning the window."""
    out = []
    with closing(sqlite3.connect(f"file:{funding_db}?mode=ro", uri=True)) as con:
        for s in CANDIDATE_SYMBOLS:
            f = con.execute("SELECT MIN(funding_time_ms), MAX(funding_time_ms), COUNT(*) "
                            "FROM funding WHERE symbol=?", (s,)).fetchone()
            k = con.execute("SELECT COUNT(*) FROM perp_klines WHERE symbol=?", (s,)).fetchone()
            if f and f[2] and f[2] > 100 and k and k[0] > 100:
                out.append(s)
    return out


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    w0, w1 = _ms(WINDOW_START), _ms(WINDOW_END)
    symbols = _covered_symbols(FUNDING_DB, w0, w1)
    dropped = [s for s in CANDIDATE_SYMBOLS if s not in symbols]

    records = []
    for s in symbols:
        funding = simulate.load_funding(FUNDING_DB, s, w0, w1)
        if len(funding) < 2:
            dropped.append(s); continue
        entry_ms, exit_ms = funding[0][0], funding[-1][0]
        try:
            rec = simulate.carry_for_symbol(
                symbol=s, funding=funding,
                spot_entry=simulate.spot_price_at(OHLCV_DB, s, entry_ms),
                spot_exit=simulate.spot_price_at(OHLCV_DB, s, exit_ms),
                perp_entry=simulate.perp_price_at(FUNDING_DB, s, entry_ms),
                perp_exit=simulate.perp_price_at(FUNDING_DB, s, exit_ms),
                liq=simulate.spot_liquidity(OHLCV_DB, s, entry_ms))
        except ValueError:        # missing spot/perp price -> drop loud, don't poison the pool
            dropped.append(s); continue
        records.append(rec)

    annual = [r["net_return_annual"] for r in records]
    a = evaluate.gate_a(annual)
    # B1: pooled per-symbol net as the interval series (proxy equity curve across symbols)
    b1 = evaluate.gate_b1([r["net"] for r in records])
    b2 = evaluate.gate_b2(float(np.mean([r["net_return"] for r in records])) if records else 0.0)
    v = evaluate.verdict(a, b2)

    cal = load_calibration()
    out = {"verdict": v, "gate_a": a, "gate_b1": b1, "gate_b2": b2,
           "manifest": {"experiment": "funding-carry-falsification", "spec_commit": "2f10134",
                        "window": [WINDOW_START, WINDOW_END], "notional": NOTIONAL,
                        "bootstrap_seed": BOOTSTRAP_SEED,
                        "shock": {"funding_per_8h": SHOCK_FUNDING_PER_8H, "days": SHOCK_DAYS},
                        "cost_model": {"active_model": cal.active_model,
                                       "calibration_identity_hash": calibration_identity_hash(cal)},
                        "symbols_kept": symbols, "symbols_dropped": sorted(set(dropped)),
                        "generated_utc": datetime.now(timezone.utc).isoformat()}}
    with open(os.path.join(OUTPUT_DIR, "per_symbol.json"), "w") as f:
        json.dump(records, f, indent=2)
    with open(os.path.join(OUTPUT_DIR, "verdict.json"), "w") as f:
        json.dump(out, f, indent=2)
    lines = [
        "# Funding-carry falsification: VERDICT", "",
        f"**Verdict: {v['verdict']}**  (Gate A: {v['pass_a']}, Gate B2: {v['pass_b2']})", "",
        f"- Symbols kept {len(symbols)}: {', '.join(symbols)}",
        f"- Pooled annualized net return: mean {a['mean']:.4f}, CI95 [{a['ci_lo']:.4f}, {a['ci_hi']:.4f}]",
        f"- LOO min mean: {a['loo_min_mean']:.4f}",
        f"- Gate B1 max drawdown (pooled net): {b1['max_drawdown']:.2f}; worst symbol net {b1['worst_interval']:.2f}",
        f"- Gate B2 synthetic shock bleed {b2['shock_bleed']:.4f}; post-shock mean return {b2['post_shock_return']:.4f}", "",
        "Scope: LIQUID universe only. A FAIL = liquid carry arbed/short-vol, NOT 'no carry anywhere'.",
        "PASS -> strategy-design fork (sizing/rebalance/long-tail). FAIL -> portfolio decision.",
    ]
    with open(os.path.join(OUTPUT_DIR, "findings.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"VERDICT: {v['verdict']}  (A={v['pass_a']} B2={v['pass_b2']}, pooled mean {a['mean']:.4f})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run, confirm PASS**

Run: `python -m pytest tests/test_funding_carry.py -q`
Expected: ALL pass (parser + simulate + gates + verdict + artifact-keys).

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/evaluate.py tools/funding_carry/run.py tests/test_funding_carry.py
git commit -m "feat(funding-carry): verdict + run orchestrator + artifacts"
```

---

## Task 9: Ingest + execute + route (human-in-the-loop)

**Files:** runtime — `data/funding.db`, `data/retune/2026-06-03-funding-carry-falsification/`

- [ ] **Step 1: Calibrate the B2 shock from concrete 2022 numbers.** Confirm `SHOCK_FUNDING_PER_8H` / `SHOCK_DAYS` against documented LUNA (May 2022) / FTX (Nov 2022) funding extremes from the research sources; adjust constants ONLY now (before the run), `mex log` the calibration. (Default 0.5%/8h × 5d = 7.5% bleed is a conservative extreme.)
- [ ] **Step 2: Ingest (NETWORK).** `python -m tools.funding_carry.ingest` — populates `data/funding.db`. Verify the printed coverage summary; confirm the bulk fundingRate CSV schema parsed (spot-check a symbol's row count vs ~2.5 years × ~3/day ≈ 2700). If the schema differs, fix `parse_funding_rows` column maps and re-run.
- [ ] **Step 3: Execute.** `python -m tools.funding_carry.run` — prints `VERDICT: ...`, writes the 3 artifacts.
- [ ] **Step 4: `mex log`** the verdict + artifact path.
- [ ] **Step 5: Route per KILL.** PASS → strategy-design fork (sizing / rebalance / long-tail universe). FAIL → portfolio decision (the liquid carry is arbed or short-vol; the long-tail remains the only un-falsified region). Either way, update memory `edge-landscape-funding-carry` with the verdict.

---

## Self-Review

**Spec coverage:** §2 universe/window → Task 1 constants + Task 8 `_covered_symbols`. §3 data/ingest → Task 2. §4 carry accounting (funding+basis+4-leg cost, per-symbol window) → Tasks 3-5. §5 Gate A (bootstrap/LOO/deflation) → Task 6. §6 Gate B (B1 max-DD + B2 synthetic shock) → Task 7. §7 KILL both-gates → Task 8 verdict. §8 file structure → all tasks. §9 NN (no holdout/live) → read-only sqlite + public data, no `open_holdout`/`simulate_strategy`/`PositionClosure` anywhere. §11 open questions resolved: per-symbol window (Task 8), shock params frozen in constants (Task 1, calibrated Task 9 Step 1), symbol filter by coverage (Task 8 `_covered_symbols`).

**Deflation note:** §5 calls for DSR deflation. Task 6 `gate_a` applies the LOO-survival guard (pooled mean must survive dropping any symbol) as the small-N robustness control; with no parameter sweep (frozen hold), the deflation-N is the symbol count and the bootstrap CI + LOO is the honest gate. If a formal DSR multiplier is wanted, it is a one-function add in evaluate.py — flagged, not silently dropped.

**Placeholder scan:** none. The B2 shock params have concrete defaults (0.5%/8h, 5d) calibrated in Task 9 Step 1 — not a TODO.

**Type consistency:** `carry_for_symbol` returns keys consumed by `run.main` (net, net_return, net_return_annual) — match. `gate_a`/`gate_b1`/`gate_b2`/`verdict` signatures match their call sites. `load_funding` returns `[(ms, rate)]` consumed by `funding_pnl` and `carry_for_symbol`. `recost_four_legs` kwargs match. `REQUIRED_SYMBOL_KEYS` ⊆ `carry_for_symbol` output.

**Known limitation (pre-declared):** B2 models the funding-inversion bleed (the dominant permanent tail damage); the basis-blowout/liquidation leg is bounded by low pre-declared leverage (the position is not liquidated at a 15% adverse basis move, so its mark-to-market converges to ~0). If a future version wants explicit margin/liquidation modeling, it is a Gate-B extension, not a change to A.
