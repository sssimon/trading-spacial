# Funding-Carry Shadow-Deploy v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a paper-only daily job that recomputes the funding-carry statistic over a trailing rolling window from live Binance FAPI data, logs it append-only, and fires a pre-registered decay-kill when the live CI falls below the backtest CI-lo (0.0502) — measuring whether the confirmed edge persists out-of-sample, with zero capital and zero holdout access.

**Architecture:** Three new modules in `tools/funding_carry/`. `live_ingest.py` fetches recent settled funding + 1h mark klines + spot via FAPI and appends idempotently to `data/funding.db` (same schema as the historical ingest). `shadow.py` recomputes the decay statistic by REUSING `simulate.carry_for_symbol` over the trailing `W`-week window and `evaluate.gate_a` for the bootstrap CI (identical to the fossil), runs a secondary per-settlement reconciliation, evaluates the decay state, and writes an append-only `.jsonl` ledger plus a derived `.json` state. `power.py` is a one-shot analysis (run during the plan) that sizes `W`/`N` from the expected live SE before they are frozen into `constants.py`.

**Tech Stack:** Python 3.14, stdlib `urllib`/`json`/`sqlite3`, NumPy (bootstrap, reused from `evaluate.gate_a`), pytest. No new third-party deps.

**Spec:** `docs/superpowers/specs/2026-06-03-funding-carry-shadow-deploy-design.md` (REV 3).

**Non-negotiables (verify each task respects them):** No `open_holdout`, no holdout frames, no `simulate_strategy`. No `PositionClosure`, no writes to `positions`. Public FAPI data only. Separate process from `btc_scanner.py`. Append-only `.jsonl`. Cost-model hash stamped on every record.

---

## File Structure

| File | Responsibility |
|---|---|
| `tools/funding_carry/constants.py` (MODIFY) | Add frozen shadow constants: 9-symbol universe, FAPI endpoints, decay thresholds, output dir. |
| `tools/funding_carry/live_ingest.py` (CREATE) | Fetch live funding/mark-klines/spot from FAPI; append idempotently to `funding.db`; detect coverage gaps. Fail-soft per symbol. |
| `tools/funding_carry/shadow.py` (CREATE) | Decay statistic (reuses `carry_for_symbol` + `gate_a`), per-settlement reconciliation (§5 secondary), decay-state machine, `run_once` orchestrator → `.jsonl` + `.json`. |
| `tools/funding_carry/power.py` (CREATE) | One-shot: size `W`/`N` from expected live SE. Output documented; values hand-frozen into constants. |
| `tests/test_funding_carry.py` (MODIFY) | TDD coverage for all of the above. |
| `.mex/context/setup.md` (MODIFY) | Document the daily scheduling entry. |

**Provisional decay params** used while building (frozen for real in Task 9 after `power.py`): `W = 2 weeks`, `N = 4` non-overlapping windows.

---

## Task 1: Frozen shadow constants

**Files:**
- Modify: `tools/funding_carry/constants.py`
- Test: `tests/test_funding_carry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_funding_carry.py — append
def test_shadow_constants_frozen():
    from tools.funding_carry import constants as C
    # The 9-symbol universe is exactly the verdict's symbols_used (LINK/SOL dropped).
    assert C.SHADOW_SYMBOLS == (
        "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
        "UNIUSDT", "XLMUSDT", "RUNEUSDT", "PENDLEUSDT",
    )
    assert "LINKUSDT" not in C.SHADOW_SYMBOLS and "SOLUSDT" not in C.SHADOW_SYMBOLS
    assert C.DECAY_CI_LO == 0.0502           # backtest gate_a ci_lo (in-sample anchor)
    assert C.SHADOW_VERSION == "v0.1"
    assert C.FAPI_MARK_KLINES.startswith("https://fapi.binance.com")
    assert C.FAPI_SPOT.startswith("https://")
    assert C.DECAY_WEEKS_W >= 1 and C.DECAY_KILL_N >= 1
    assert C.FUNDING_FETCH_LIMIT >= 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_funding_carry.py::test_shadow_constants_frozen -v`
Expected: FAIL with `AttributeError: module 'tools.funding_carry.constants' has no attribute 'SHADOW_SYMBOLS'`

- [ ] **Step 3: Add the constants**

Append to `tools/funding_carry/constants.py`:

```python
# --- Shadow-deploy v0.1 (sub-project realizabilidad, spec 2026-06-03) ---
# Frozen universe = the verdict's symbols_used (LINKUSDT/SOLUSDT dropped for coverage).
SHADOW_SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
    "UNIUSDT", "XLMUSDT", "RUNEUSDT", "PENDLEUSDT",
)
FAPI_MARK_KLINES = "https://fapi.binance.com/fapi/v1/markPriceKlines"
FAPI_SPOT = "https://api.binance.com/api/v3/ticker/price"

DECAY_CI_LO = 0.0502           # backtest gate_a ci_lo — in-sample-anchored decay threshold
DECAY_WEEKS_W = 2              # rolling window (PROVISIONAL — frozen by power.py, Task 9/11)
DECAY_KILL_N = 4              # consecutive non-overlapping windows below threshold (PROVISIONAL)
FUNDING_FETCH_LIMIT = 1000     # FAPI fundingRate page size; covers multi-day gaps + back-fill

SHADOW_OUTPUT_DIR = "data/shadow"
SHADOW_VERSION = "v0.1"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_funding_carry.py::test_shadow_constants_frozen -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/constants.py tests/test_funding_carry.py
git commit -m "feat(funding-shadow): frozen shadow-deploy constants (9-symbol universe, decay thresholds)"
```

---

## Task 2: Live funding fetch (FAPI, fail-soft)

**Files:**
- Create: `tools/funding_carry/live_ingest.py`
- Test: `tests/test_funding_carry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_funding_carry.py — append
def test_parse_fapi_funding_rows():
    from tools.funding_carry.live_ingest import parse_fapi_funding
    # FAPI /fapi/v1/fundingRate returns a JSON list of dicts.
    payload = [
        {"symbol": "BTCUSDT", "fundingTime": 1700000000000, "fundingRate": "0.0001"},
        {"symbol": "BTCUSDT", "fundingTime": 1700028800000, "fundingRate": "-0.00005"},
    ]
    rows = parse_fapi_funding(payload)
    assert rows == [(1700000000000, 0.0001), (1700028800000, -0.00005)]


def test_fetch_recent_funding_failsoft(monkeypatch):
    from tools.funding_carry import live_ingest
    def boom(url, **kw):
        raise OSError("network down")
    monkeypatch.setattr(live_ingest, "_get_json", boom)
    # Fail-soft: a down symbol returns [] (logged), never raises.
    assert live_ingest.fetch_recent_funding("BTCUSDT", limit=10) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_funding_carry.py -k "fapi_funding or recent_funding_failsoft" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.funding_carry.live_ingest'`

- [ ] **Step 3: Write minimal implementation**

Create `tools/funding_carry/live_ingest.py`:

```python
"""Live FAPI ingest for the funding-carry shadow (spec 2026-06-03 §4).

Fetches recently-settled funding rates, 1h mark klines, and spot prices from
Binance FAPI and appends them idempotently to data/funding.db (same schema as
the historical bulk ingest). Fail-soft per symbol: a down endpoint logs and
yields empty, never poisons the pool or raises into the daily job. Network +
read/append on funding.db only; never touches holdout or positions."""
from __future__ import annotations
import json
import logging
import sqlite3
import urllib.request
from contextlib import closing
from .constants import FAPI_FUNDING, FAPI_MARK_KLINES, FAPI_SPOT, FUNDING_DB

log = logging.getLogger("funding_carry.live_ingest")


def _get_json(url: str, *, timeout: int = 30):
    """GET a URL and parse JSON. Raises on network/HTTP error (callers fail-soft)."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_fapi_funding(payload: list[dict]) -> list[tuple[int, float]]:
    """Map FAPI /fapi/v1/fundingRate JSON to [(fundingTime_ms, rate)], time-ascending."""
    out = [(int(d["fundingTime"]), float(d["fundingRate"])) for d in payload]
    out.sort(key=lambda x: x[0])
    return out


def fetch_recent_funding(symbol: str, *, limit: int) -> list[tuple[int, float]]:
    """Recent settled funding for `symbol`. Fail-soft: [] on any error (logged)."""
    url = f"{FAPI_FUNDING}?symbol={symbol}&limit={int(limit)}"
    try:
        return parse_fapi_funding(_get_json(url))
    except Exception as e:                       # noqa: BLE001 — fail-soft by contract
        log.warning("fetch_recent_funding(%s) failed: %s", symbol, e)
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_funding_carry.py -k "fapi_funding or recent_funding_failsoft" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/live_ingest.py tests/test_funding_carry.py
git commit -m "feat(funding-shadow): live FAPI funding fetch (fail-soft)"
```

---

## Task 3: Live mark-klines fetch + append to perp_klines

**Files:**
- Modify: `tools/funding_carry/live_ingest.py`
- Test: `tests/test_funding_carry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_funding_carry.py — append
def test_parse_mark_klines():
    from tools.funding_carry.live_ingest import parse_mark_klines
    # FAPI markPriceKlines: [openTime, open, high, low, close, ...]; we keep (openTime, close).
    payload = [
        [1700000000000, "100.0", "101.0", "99.0", "100.5", "0", 0, "0", 0, "0", "0", "0"],
        [1700003600000, "100.5", "102.0", "100.0", "101.2", "0", 0, "0", 0, "0", "0", "0"],
    ]
    assert parse_mark_klines(payload) == [(1700000000000, 100.5), (1700003600000, 101.2)]


def test_append_perp_klines_idempotent(tmp_path):
    from tools.funding_carry import live_ingest
    db = str(tmp_path / "f.db")
    import sqlite3
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE perp_klines(symbol TEXT, open_time INTEGER, close REAL,"
                    " PRIMARY KEY(symbol, open_time))")
    rows = [(1700000000000, 100.5), (1700003600000, 101.2)]
    live_ingest.append_perp_klines(db, "BTCUSDT", rows)
    live_ingest.append_perp_klines(db, "BTCUSDT", rows)   # second call must not double-count
    with sqlite3.connect(db) as con:
        n = con.execute("SELECT COUNT(*) FROM perp_klines WHERE symbol='BTCUSDT'").fetchone()[0]
    assert n == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_funding_carry.py -k "mark_klines or append_perp" -v`
Expected: FAIL with `AttributeError: module 'tools.funding_carry.live_ingest' has no attribute 'parse_mark_klines'`

- [ ] **Step 3: Write minimal implementation**

Append to `tools/funding_carry/live_ingest.py`:

```python
def parse_mark_klines(payload: list[list]) -> list[tuple[int, float]]:
    """Map FAPI markPriceKlines to [(open_time_ms, close)], same fields as the bulk
    ingest keeps (ingest.py:117: open_time index 0, close index 4)."""
    out = [(int(k[0]), float(k[4])) for k in payload]
    out.sort(key=lambda x: x[0])
    return out


def fetch_mark_klines(symbol: str, *, interval: str = "1h", limit: int = 1000
                      ) -> list[tuple[int, float]]:
    """Recent perp mark klines at the SAME grain as the fossil (1h). Fail-soft: []."""
    url = f"{FAPI_MARK_KLINES}?symbol={symbol}&interval={interval}&limit={int(limit)}"
    try:
        return parse_mark_klines(_get_json(url))
    except Exception as e:                       # noqa: BLE001 — fail-soft
        log.warning("fetch_mark_klines(%s) failed: %s", symbol, e)
        return []


def append_perp_klines(db_path: str, symbol: str, rows: list[tuple[int, float]]) -> int:
    """Idempotent append to perp_klines (PK (symbol, open_time)). Returns rows attempted."""
    with closing(sqlite3.connect(db_path)) as con:
        con.executemany("INSERT OR IGNORE INTO perp_klines VALUES(?,?,?)",
                        [(symbol, t, c) for t, c in rows])
        con.commit()
    return len(rows)


def append_funding(db_path: str, symbol: str, rows: list[tuple[int, float]]) -> int:
    """Idempotent append to funding (PK (symbol, funding_time_ms)). Returns rows attempted."""
    with closing(sqlite3.connect(db_path)) as con:
        con.executemany("INSERT OR IGNORE INTO funding VALUES(?,?,?)",
                        [(symbol, t, r) for t, r in rows])
        con.commit()
    return len(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_funding_carry.py -k "mark_klines or append_perp" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/live_ingest.py tests/test_funding_carry.py
git commit -m "feat(funding-shadow): live mark-klines fetch + idempotent perp_klines/funding append"
```

---

## Task 4: Spot fetch + ingest-all-symbols entry point

**Files:**
- Modify: `tools/funding_carry/live_ingest.py`
- Test: `tests/test_funding_carry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_funding_carry.py — append
def test_fetch_spot_failsoft(monkeypatch):
    from tools.funding_carry import live_ingest
    monkeypatch.setattr(live_ingest, "_get_json",
                        lambda url, **kw: {"symbol": "BTCUSDT", "price": "42000.5"})
    assert live_ingest.fetch_spot("BTCUSDT") == 42000.5
    def boom(url, **kw):
        raise OSError("down")
    monkeypatch.setattr(live_ingest, "_get_json", boom)
    import math
    assert math.isnan(live_ingest.fetch_spot("BTCUSDT"))


def test_ingest_live_appends_all(tmp_path, monkeypatch):
    from tools.funding_carry import live_ingest
    db = str(tmp_path / "f.db")
    import sqlite3
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE funding(symbol TEXT, funding_time_ms INTEGER,"
                    " funding_rate REAL, PRIMARY KEY(symbol, funding_time_ms))")
        con.execute("CREATE TABLE perp_klines(symbol TEXT, open_time INTEGER, close REAL,"
                    " PRIMARY KEY(symbol, open_time))")
    monkeypatch.setattr(live_ingest, "fetch_recent_funding",
                        lambda s, limit: [(1700000000000, 0.0001)])
    monkeypatch.setattr(live_ingest, "fetch_mark_klines",
                        lambda s, interval="1h", limit=1000: [(1700000000000, 100.0)])
    summary = live_ingest.ingest_live(["BTCUSDT", "ETHUSDT"], db_path=db, limit=10)
    assert summary["BTCUSDT"]["funding"] == 1 and summary["BTCUSDT"]["klines"] == 1
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM funding").fetchone()[0] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_funding_carry.py -k "fetch_spot or ingest_live_appends" -v`
Expected: FAIL with `AttributeError: ... has no attribute 'fetch_spot'`

- [ ] **Step 3: Write minimal implementation**

Append to `tools/funding_carry/live_ingest.py`:

```python
def fetch_spot(symbol: str) -> float:
    """Current spot price via Binance spot ticker. Fail-soft: NaN on error."""
    try:
        return float(_get_json(f"{FAPI_SPOT}?symbol={symbol}")["price"])
    except Exception as e:                       # noqa: BLE001 — fail-soft
        log.warning("fetch_spot(%s) failed: %s", symbol, e)
        return float("nan")


def ingest_live(symbols: list[str], *, db_path: str = FUNDING_DB,
                limit: int) -> dict:
    """Fetch + append funding and 1h mark klines for each symbol. Fail-soft per
    symbol (a down symbol contributes 0 rows, never raises). Returns per-symbol counts."""
    summary = {}
    for s in symbols:
        funding = fetch_recent_funding(s, limit=limit)
        klines = fetch_mark_klines(s, interval="1h", limit=limit)
        nf = append_funding(db_path, s, funding) if funding else 0
        nk = append_perp_klines(db_path, s, klines) if klines else 0
        summary[s] = {"funding": nf, "klines": nk}
    return summary
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_funding_carry.py -k "fetch_spot or ingest_live_appends" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/live_ingest.py tests/test_funding_carry.py
git commit -m "feat(funding-shadow): spot fetch + ingest_live entry point"
```

---

## Task 5: Decay statistic — REUSE carry_for_symbol over the rolling window (the N1 fix)

**Files:**
- Create: `tools/funding_carry/shadow.py`
- Test: `tests/test_funding_carry.py`

This is the load-bearing task. The decay statistic MUST be the fossil's own computation
(`carry_for_symbol` → `net_return_annual`, span-annualized, entry-mark funding, $/notional)
restricted to the trailing window — NOT a re-derived formula (spec §3, audit N1).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_funding_carry.py — append
def test_decay_statistic_matches_carry_for_symbol(tmp_path):
    """The shadow statistic over a window MUST equal carry_for_symbol on that window —
    identical method, span-annualized (NOT settlement-count annualized). Guards N1."""
    import sqlite3
    from tools.funding_carry import shadow, simulate
    fdb = str(tmp_path / "f.db"); odb = str(tmp_path / "o.db")
    # Build a tiny funding.db + ohlcv.db with a known, GAPPED settlement series.
    with sqlite3.connect(fdb) as con:
        con.execute("CREATE TABLE funding(symbol TEXT, funding_time_ms INTEGER, funding_rate REAL,"
                    " PRIMARY KEY(symbol, funding_time_ms))")
        con.execute("CREATE TABLE perp_klines(symbol TEXT, open_time INTEGER, close REAL,"
                    " PRIMARY KEY(symbol, open_time))")
        H = 3_600_000
        # 4 settlements but with a GAP (skip one 8h slot) so count != span.
        times = [0, 8*H, 16*H, 40*H]
        for t in times:
            con.execute("INSERT INTO funding VALUES('BTCUSDT', ?, 0.0001)", (t,))
            con.execute("INSERT INTO perp_klines VALUES('BTCUSDT', ?, 100.0)", (t,))
    with sqlite3.connect(odb) as con:
        con.execute("CREATE TABLE ohlcv(symbol TEXT, timeframe TEXT, open_time INTEGER,"
                    " close REAL, volume REAL)")
        for t in [0, 8*3_600_000, 16*3_600_000, 40*3_600_000]:
            con.execute("INSERT INTO ohlcv VALUES('BTCUSDT','1h',?,100.0,1000.0)", (t,))
    # Reference: carry_for_symbol directly over the same window.
    funding = simulate.load_funding(fdb, "BTCUSDT", 0, 40*3_600_000)
    ref = simulate.carry_for_symbol(
        symbol="BTCUSDT", funding=funding,
        spot_entry=100.0, spot_exit=100.0, perp_entry=100.0, perp_exit=100.0,
        liq=float("nan"))
    got = shadow.symbol_window_return(
        "BTCUSDT", funding_db=fdb, ohlcv_db=odb, start_ms=0, end_ms=40*3_600_000)
    assert abs(got - ref["net_return_annual"]) < 1e-12


def test_pooled_decay_uses_gate_a(tmp_path, monkeypatch):
    from tools.funding_carry import shadow, evaluate
    monkeypatch.setattr(shadow, "symbol_window_return",
                        lambda s, **kw: {"BTCUSDT": 0.06, "ETHUSDT": 0.07}[s])
    out = shadow.pooled_decay(["BTCUSDT", "ETHUSDT"], funding_db="x", ohlcv_db="y",
                              start_ms=0, end_ms=1)
    ref = evaluate.gate_a([0.06, 0.07])
    assert out["ci_lo"] == ref["ci_lo"] and out["ci_hi"] == ref["ci_hi"]
    assert out["mean"] == ref["mean"] and out["n"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_funding_carry.py -k "decay_statistic_matches or pooled_decay_uses_gate_a" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.funding_carry.shadow'`

- [ ] **Step 3: Write minimal implementation**

Create `tools/funding_carry/shadow.py`:

```python
"""Funding-carry shadow-deploy v0.1 (spec 2026-06-03).

Recomputes the FOSSIL'S OWN statistic (simulate.carry_for_symbol -> net_return_annual,
span-annualized, entry-mark funding, $/notional) over a trailing W-week window, pools
it equal-weight via evaluate.gate_a (identical bootstrap CI), and fires a pre-registered
decay-kill when the live CI-hi falls below the backtest CI-lo (0.0502) for N consecutive
non-overlapping windows. Paper-only: no positions, no orders, no holdout. The statistic
reuses carry_for_symbol verbatim (audit N1) — no new annualization formula."""
from __future__ import annotations
from . import simulate, evaluate


def symbol_window_return(symbol: str, *, funding_db: str, ohlcv_db: str,
                         start_ms: int, end_ms: int) -> float:
    """net_return_annual for `symbol` over [start_ms, end_ms], computed by the fossil's
    carry_for_symbol (span-annualized). Raises ValueError on missing prices (drop upstream)."""
    funding = simulate.load_funding(funding_db, symbol, start_ms, end_ms)
    if len(funding) < 2:
        raise ValueError(f"{symbol}: <2 settlements in window")
    entry_ms, exit_ms = funding[0][0], funding[-1][0]
    rec = simulate.carry_for_symbol(
        symbol=symbol, funding=funding,
        spot_entry=simulate.spot_price_at(ohlcv_db, symbol, entry_ms),
        spot_exit=simulate.spot_price_at(ohlcv_db, symbol, exit_ms),
        perp_entry=simulate.perp_price_at(funding_db, symbol, entry_ms),
        perp_exit=simulate.perp_price_at(funding_db, symbol, exit_ms),
        liq=simulate.spot_liquidity(ohlcv_db, symbol, entry_ms))
    return rec["net_return_annual"]


def pooled_decay(symbols: list[str], *, funding_db: str, ohlcv_db: str,
                 start_ms: int, end_ms: int) -> dict:
    """Equal-weight pooled CI of net_return_annual over the window — identical to gate_a.
    Symbols with <2 settlements / missing prices are dropped loud (not poisoned)."""
    annual, dropped = [], []
    for s in symbols:
        try:
            annual.append(symbol_window_return(
                s, funding_db=funding_db, ohlcv_db=ohlcv_db,
                start_ms=start_ms, end_ms=end_ms))
        except ValueError:
            dropped.append(s)
    out = evaluate.gate_a(annual) if annual else {
        "mean": 0.0, "ci_lo": 0.0, "ci_hi": 0.0, "loo_min_mean": 0.0, "pass_a": False, "n": 0}
    out["dropped"] = dropped
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_funding_carry.py -k "decay_statistic_matches or pooled_decay_uses_gate_a" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/shadow.py tests/test_funding_carry.py
git commit -m "feat(funding-shadow): decay statistic reuses carry_for_symbol over rolling window (N1 fix)"
```

---

## Task 6: Decay-state machine (CI-vs-threshold, non-overlapping window counter)

**Files:**
- Modify: `tools/funding_carry/shadow.py`
- Test: `tests/test_funding_carry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_funding_carry.py — append
def test_decay_state_three_states():
    from tools.funding_carry.shadow import decay_state
    from tools.funding_carry.constants import DECAY_CI_LO, DECAY_KILL_N
    # ALIVE: CI sits at/above the band.
    s = decay_state(ci_lo=0.05, ci_hi=0.08, weeks_below=0)
    assert s["decay_state"] == "ALIVE" and s["weeks_below"] == 0
    # THIN: CI overlaps [0.0502, 0.0633] (ci_hi >= threshold but ci_lo below the headline).
    s = decay_state(ci_lo=0.04, ci_hi=0.06, weeks_below=0)
    assert s["decay_state"] == "THIN" and s["weeks_below"] == 0
    # Below threshold once: counter increments, not yet REFUTED.
    s = decay_state(ci_lo=0.01, ci_hi=DECAY_CI_LO - 0.001, weeks_below=0)
    assert s["weeks_below"] == 1
    assert s["decay_state"] == ("REFUTED" if DECAY_KILL_N <= 1 else "THIN")
    # N consecutive below -> REFUTED.
    s = decay_state(ci_lo=0.01, ci_hi=DECAY_CI_LO - 0.001, weeks_below=DECAY_KILL_N - 1)
    assert s["weeks_below"] == DECAY_KILL_N and s["decay_state"] == "REFUTED"


def test_decay_state_resets_counter_on_recovery():
    from tools.funding_carry.shadow import decay_state
    from tools.funding_carry.constants import DECAY_CI_LO
    s = decay_state(ci_lo=0.06, ci_hi=0.08, weeks_below=3)   # recovered above threshold
    assert s["weeks_below"] == 0 and s["decay_state"] == "ALIVE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_funding_carry.py -k "decay_state" -v`
Expected: FAIL with `ImportError: cannot import name 'decay_state'`

- [ ] **Step 3: Write minimal implementation**

Append to `tools/funding_carry/shadow.py`:

```python
from .constants import DECAY_CI_LO, DECAY_KILL_N

_HEADLINE = 0.0633        # backtest gate_a mean — top of the thin band


def decay_state(*, ci_lo: float, ci_hi: float, weeks_below: int) -> dict:
    """State machine over the live CI vs the in-sample threshold (spec §6).

    REFUTED   : ci_hi < DECAY_CI_LO for DECAY_KILL_N consecutive non-overlapping windows.
    THIN      : CI overlaps [DECAY_CI_LO, headline] — compressing, not dead.
    ALIVE     : CI sits at/above the band.

    `weeks_below` is the prior consecutive count; this call updates it. A window whose
    ci_hi recovers to/above the threshold RESETS the counter (consecutive, not cumulative)."""
    below = ci_hi < DECAY_CI_LO
    new_count = (weeks_below + 1) if below else 0
    if new_count >= DECAY_KILL_N:
        state = "REFUTED"
    elif ci_lo < _HEADLINE and ci_hi >= DECAY_CI_LO:
        state = "THIN"
    elif below:
        state = "THIN"            # below once but not yet N — still compressing, not dead
    else:
        state = "ALIVE"
    return {"decay_state": state, "weeks_below": new_count}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_funding_carry.py -k "decay_state" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/shadow.py tests/test_funding_carry.py
git commit -m "feat(funding-shadow): decay-state machine (CI-vs-threshold, consecutive-window kill)"
```

---

## Task 7: Per-settlement reconciliation (§5 secondary — one-step surprise, NOT decay)

**Files:**
- Modify: `tools/funding_carry/shadow.py`
- Test: `tests/test_funding_carry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_funding_carry.py — append
def test_reconcile_settlement_one_step():
    from tools.funding_carry.shadow import reconcile_settlement
    # expected = naive (prev rate persists); realized = actual settled.
    r = reconcile_settlement(prev_rate=0.0001, settled_rate=0.00008,
                             mark=100.0, units=100.0)
    # expected_net = prev_rate * mark * units ; realized_net = settled_rate * mark * units
    assert abs(r["expected_net"] - 0.0001 * 100.0 * 100.0) < 1e-9
    assert abs(r["realized_net"] - 0.00008 * 100.0 * 100.0) < 1e-9
    assert abs(r["drift"] - (r["realized_net"] - r["expected_net"])) < 1e-12
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_funding_carry.py -k "reconcile_settlement" -v`
Expected: FAIL with `ImportError: cannot import name 'reconcile_settlement'`

- [ ] **Step 3: Write minimal implementation**

Append to `tools/funding_carry/shadow.py`:

```python
def reconcile_settlement(*, prev_rate: float, settled_rate: float,
                         mark: float, units: float) -> dict:
    """Secondary operational sanity check (spec §5). Measures ONE-STEP surprise, NOT decay
    (the naive random-walk baseline persists the prior rate, so it absorbs monotone decay —
    decay is judged in §6 on the pooled CI, not here). Useful only for ingest/mark anomalies."""
    expected_net = prev_rate * mark * units
    realized_net = settled_rate * mark * units
    return {"expected_net": expected_net, "realized_net": realized_net,
            "drift": realized_net - expected_net}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_funding_carry.py -k "reconcile_settlement" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/shadow.py tests/test_funding_carry.py
git commit -m "feat(funding-shadow): per-settlement reconciliation (one-step surprise, declared non-decay)"
```

---

## Task 8: Gap detection (window-incomplete → do not evaluate kill)

**Files:**
- Modify: `tools/funding_carry/shadow.py`
- Test: `tests/test_funding_carry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_funding_carry.py — append
def test_window_complete_detects_gap():
    from tools.funding_carry.shadow import window_complete
    H8 = 8 * 3_600_000
    # Contiguous 8h settlements over the window -> complete.
    ts_ok = [0, H8, 2*H8, 3*H8]
    assert window_complete(ts_ok, start_ms=0, end_ms=3*H8, max_gap_ms=int(1.5*H8)) is True
    # A >1.5x8h hole -> incomplete.
    ts_gap = [0, H8, 5*H8]
    assert window_complete(ts_gap, start_ms=0, end_ms=5*H8, max_gap_ms=int(1.5*H8)) is False
    # Fewer than 2 points -> incomplete.
    assert window_complete([0], start_ms=0, end_ms=H8, max_gap_ms=H8) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_funding_carry.py -k "window_complete" -v`
Expected: FAIL with `ImportError: cannot import name 'window_complete'`

- [ ] **Step 3: Write minimal implementation**

Append to `tools/funding_carry/shadow.py`:

```python
def window_complete(settlement_times_ms: list[int], *, start_ms: int, end_ms: int,
                    max_gap_ms: int) -> bool:
    """True iff the settlement series covers [start_ms, end_ms] with no gap > max_gap_ms.
    A gap marks the window incomplete -> the daily job SKIPS the decay-kill eval (spec §4
    fail-safe: a data hole must not trigger a false REFUTED)."""
    ts = sorted(t for t in settlement_times_ms if start_ms <= t <= end_ms)
    if len(ts) < 2:
        return False
    if ts[0] - start_ms > max_gap_ms or end_ms - ts[-1] > max_gap_ms:
        return False
    return all((b - a) <= max_gap_ms for a, b in zip(ts, ts[1:]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_funding_carry.py -k "window_complete" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/shadow.py tests/test_funding_carry.py
git commit -m "feat(funding-shadow): gap detection (incomplete window skips kill eval)"
```

---

## Task 9: run_once orchestrator → append-only .jsonl + derived .json

**Files:**
- Modify: `tools/funding_carry/shadow.py`
- Test: `tests/test_funding_carry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_funding_carry.py — append
def test_run_once_appends_and_writes_state(tmp_path, monkeypatch):
    import json
    from tools.funding_carry import shadow
    out_dir = tmp_path / "shadow"
    # Stub the moving parts: ingest no-op, a fixed pooled CI, a complete window.
    monkeypatch.setattr(shadow, "_ingest", lambda symbols, db, limit: None)
    monkeypatch.setattr(shadow, "pooled_decay",
                        lambda *a, **k: {"mean": 0.06, "ci_lo": 0.055, "ci_hi": 0.065,
                                         "loo_min_mean": 0.055, "pass_a": True, "n": 9,
                                         "dropped": []})
    monkeypatch.setattr(shadow, "_window_settlement_times", lambda *a, **k: [0, 1])
    monkeypatch.setattr(shadow, "window_complete", lambda *a, **k: True)
    monkeypatch.setattr(shadow, "_cal_hash", lambda: "deadbeef")
    res1 = shadow.run_once(out_dir=str(out_dir), now_ms=10_000_000_000)
    res2 = shadow.run_once(out_dir=str(out_dir), now_ms=10_100_000_000)
    # .jsonl is append-only: second run adds, never truncates.
    lines = (out_dir / "funding_carry_signals.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    state = json.loads((out_dir / "funding_carry_state.json").read_text())
    assert state["decay_state"] == "THIN"          # ci_lo 0.055 < headline 0.0633
    assert state["calibration_identity_hash"] == "deadbeef"
    assert res2["decay_state"] in {"ALIVE", "THIN", "REFUTED"}


def test_run_once_incomplete_window_skips_kill(tmp_path, monkeypatch):
    import json
    from tools.funding_carry import shadow
    out_dir = tmp_path / "shadow"
    monkeypatch.setattr(shadow, "_ingest", lambda symbols, db, limit: None)
    monkeypatch.setattr(shadow, "pooled_decay",
                        lambda *a, **k: {"mean": 0.0, "ci_lo": 0.0, "ci_hi": 0.0,
                                         "loo_min_mean": 0.0, "pass_a": False, "n": 0,
                                         "dropped": []})
    monkeypatch.setattr(shadow, "_window_settlement_times", lambda *a, **k: [0])
    monkeypatch.setattr(shadow, "window_complete", lambda *a, **k: False)
    monkeypatch.setattr(shadow, "_cal_hash", lambda: "x")
    res = shadow.run_once(out_dir=str(out_dir), now_ms=10_000_000_000)
    state = json.loads((out_dir / "funding_carry_state.json").read_text())
    assert state["window_complete"] is False
    assert state["decay_state"] == "INCOMPLETE"     # not evaluated; counter untouched
    assert state["weeks_below"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_funding_carry.py -k "run_once" -v`
Expected: FAIL with `AttributeError: module 'tools.funding_carry.shadow' has no attribute 'run_once'`

- [ ] **Step 3: Write minimal implementation**

Append to `tools/funding_carry/shadow.py`:

```python
import json
import os
from datetime import datetime, timezone
from .constants import (SHADOW_SYMBOLS, SHADOW_OUTPUT_DIR, SHADOW_VERSION,
                        DECAY_WEEKS_W, FUNDING_DB, OHLCV_DB, FUNDING_FETCH_LIMIT)

_WEEK_MS = 7 * 24 * 3_600_000
_MAX_GAP_MS = int(1.5 * 8 * 3_600_000)        # >1.5 funding intervals = a hole


def _ingest(symbols, db, limit):
    from . import live_ingest
    live_ingest.ingest_live(symbols, db_path=db, limit=limit)


def _cal_hash():
    from backtest_costs import calibration_identity_hash, load_calibration
    return calibration_identity_hash(load_calibration())


def _window_settlement_times(funding_db, symbols, start_ms, end_ms):
    """Union of settlement times across symbols in the window (for gap detection)."""
    ts = set()
    for s in symbols:
        ts.update(t for t, _ in simulate.load_funding(funding_db, s, start_ms, end_ms))
    return sorted(ts)


def _read_prev_state(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"weeks_below": 0}


def run_once(*, out_dir: str = SHADOW_OUTPUT_DIR, now_ms: int,
            funding_db: str = FUNDING_DB, ohlcv_db: str = OHLCV_DB,
            symbols: tuple = SHADOW_SYMBOLS) -> dict:
    """One daily shadow cycle: ingest live data, recompute the windowed pooled CI, update
    the decay state, append a per-symbol line to the immutable .jsonl, write derived state.
    Fail-soft: never raises into the scheduler. No positions, no orders, no holdout."""
    os.makedirs(out_dir, exist_ok=True)
    jsonl = os.path.join(out_dir, "funding_carry_signals.jsonl")
    state_path = os.path.join(out_dir, "funding_carry_state.json")
    start_ms, end_ms = now_ms - DECAY_WEEKS_W * _WEEK_MS, now_ms
    cal = _cal_hash()
    ts_utc = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat()

    try:
        _ingest(list(symbols), funding_db, FUNDING_FETCH_LIMIT)
    except Exception:                            # noqa: BLE001 — fail-soft; eval on what we have
        pass

    times = _window_settlement_times(funding_db, list(symbols), start_ms, end_ms)
    complete = window_complete(times, start_ms=start_ms, end_ms=end_ms, max_gap_ms=_MAX_GAP_MS)
    pooled = pooled_decay(list(symbols), funding_db=funding_db, ohlcv_db=ohlcv_db,
                          start_ms=start_ms, end_ms=end_ms)
    prev = _read_prev_state(state_path)

    if complete:
        ds = decay_state(ci_lo=pooled["ci_lo"], ci_hi=pooled["ci_hi"],
                         weeks_below=int(prev.get("weeks_below", 0)))
    else:
        ds = {"decay_state": "INCOMPLETE", "weeks_below": int(prev.get("weeks_below", 0))}

    line = {"settlement_ts_utc": ts_utc, "window": [start_ms, end_ms],
            "pooled_mean": pooled["mean"], "ci_lo": pooled["ci_lo"], "ci_hi": pooled["ci_hi"],
            "n": pooled["n"], "dropped": pooled["dropped"], "window_complete": complete,
            "decay_state": ds["decay_state"], "weeks_below": ds["weeks_below"],
            "calibration_identity_hash": cal, "shadow_version": SHADOW_VERSION}
    with open(jsonl, "a", encoding="utf-8") as f:    # append-only ledger
        f.write(json.dumps(line) + "\n")
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({**line, "decay_weeks_w": DECAY_WEEKS_W}, f, indent=2)
    return line


if __name__ == "__main__":
    import time
    print(run_once(now_ms=int(time.time() * 1000)))
```

Note: `now_ms` is injected (no argless `datetime.now()` in the orchestrator body) so tests are
deterministic; the `__main__` block stamps real time only when run as a script.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_funding_carry.py -k "run_once" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/shadow.py tests/test_funding_carry.py
git commit -m "feat(funding-shadow): run_once orchestrator (append-only jsonl + derived state, gap fail-safe)"
```

---

## Task 10: power.py — size W/N from expected live SE, then freeze constants

**Files:**
- Create: `tools/funding_carry/power.py`
- Test: `tests/test_funding_carry.py`
- Modify: `tools/funding_carry/constants.py` (freeze W/N after running)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_funding_carry.py — append
def test_min_window_weeks_monotone():
    from tools.funding_carry.power import min_window_weeks
    # SE shrinks ~1/sqrt(n); a tighter target band needs a larger window. Monotone & >=1.
    w_loose = min_window_weeks(per_symbol_settlements_per_week=21, n_symbols=9,
                               sigma_annual=0.05, target_half_band=0.0066)
    w_tight = min_window_weeks(per_symbol_settlements_per_week=21, n_symbols=9,
                               sigma_annual=0.05, target_half_band=0.0030)
    assert w_loose >= 1 and w_tight >= w_loose
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_funding_carry.py -k "min_window_weeks" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.funding_carry.power'`

- [ ] **Step 3: Write minimal implementation**

Create `tools/funding_carry/power.py`:

```python
"""One-shot power heuristic to SIZE the decay-kill window W (spec §6, audit N2).

W is sized so the expected LIVE standard error of the pooled annualized return is below
half the thin band (headline 0.0633 - threshold 0.0502 = 0.0131 -> half = 0.00655). The SE
is modeled on the EXPECTED LIVE regime (real per-symbol settlement cadence and symbol count),
NOT the full fossil — the fossil only supplies a sigma prior. N (consecutive non-overlapping
windows) is a separate confirmatory guard chosen for a target false-REFUTED rate. This module
is run ONCE during the plan; its outputs are hand-frozen into constants.py before any live run.

NOT a frequentist gate: the live CI (computed from live data in shadow.pooled_decay) controls
false-REFUTED in-regime. This only prevents picking an absurdly short W."""
from __future__ import annotations
import math


def min_window_weeks(*, per_symbol_settlements_per_week: int, n_symbols: int,
                     sigma_annual: float, target_half_band: float) -> int:
    """Smallest integer W (weeks) such that SE(pooled annualized return) <= target_half_band.

    Pooled equal-weight over n_symbols of a per-symbol mean over (W * settlements/week)
    observations: SE ~ sigma_annual / sqrt(n_symbols * W * settlements_per_week)."""
    per_week = max(1, per_symbol_settlements_per_week)
    denom_per_w = n_symbols * per_week
    # Solve sigma / sqrt(denom_per_w * W) <= band  ->  W >= (sigma/band)^2 / denom_per_w
    w_real = (sigma_annual / target_half_band) ** 2 / denom_per_w
    return max(1, math.ceil(w_real))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_funding_carry.py -k "min_window_weeks" -v`
Expected: PASS

- [ ] **Step 5: Run the heuristic on the real fossil sigma and FREEZE W/N**

Run a short REPL/script to get the sigma prior from the per-symbol annualized returns the
backtest produced, then size W:

```bash
python -c "import json; d=json.load(open('data/retune/2026-06-03-funding-carry-falsification/per_symbol.json')); import statistics as s; xs=[r['net_return_annual'] for r in d]; print('sigma_annual~', round(s.pstdev(xs),4))"
```

Then size W (8h funding => 21 settlements/week; 9 symbols; half-band 0.00655):

```bash
python -c "from tools.funding_carry.power import min_window_weeks; print('W=', min_window_weeks(per_symbol_settlements_per_week=21, n_symbols=9, sigma_annual=<SIGMA_FROM_ABOVE>, target_half_band=0.00655))"
```

Edit `tools/funding_carry/constants.py`: set `DECAY_WEEKS_W` to the computed `W` and
`DECAY_KILL_N` to the confirmatory count (default 4 unless the half-band argues otherwise),
and update the inline comment from `(PROVISIONAL ...)` to `(frozen by power.py 2026-06-..)`.

- [ ] **Step 6: Commit**

```bash
git add tools/funding_carry/power.py tools/funding_carry/constants.py tests/test_funding_carry.py
git commit -m "feat(funding-shadow): power heuristic + frozen W/N (sized from expected live SE)"
```

---

## Task 11: Full suite green + scheduling doc + mex log

**Files:**
- Modify: `.mex/context/setup.md`
- (No new code.)

- [ ] **Step 1: Run the full funding-carry suite**

Run: `python -m pytest tests/test_funding_carry.py -v`
Expected: PASS (all pre-existing tests + the new shadow tests). If any pre-existing test
regressed, STOP and fix before proceeding.

- [ ] **Step 2: Smoke-run the orchestrator against the live FAPI once**

Run: `python -m tools.funding_carry.shadow`
Expected: prints one JSON line; `data/shadow/funding_carry_signals.jsonl` has 1 line and
`data/shadow/funding_carry_state.json` exists with a `decay_state` in {ALIVE, THIN, REFUTED,
INCOMPLETE}. Magnitudes of `pooled_mean` should be in the ballpark of the 0.0633 backtest
(not orders of magnitude off — a gross mismatch means a windowing/units bug, investigate).

- [ ] **Step 3: Document the daily schedule**

Add to `.mex/context/setup.md` (under the automation/Windows section):

```markdown
### Funding-carry shadow (paper-only, daily)

`python -m tools.funding_carry.shadow` — recomputes the funding-carry decay statistic over
the trailing W-week window from live FAPI data and appends to `data/shadow/`. Schedule 1×/day
post-funding-settlement via watchdog/cron. SEPARATE process from `btc_scanner.py`; reads/writes
only `data/funding.db` + `data/shadow/`. Paper-only: no positions, no orders, no holdout.
A `decay_state=REFUTED` means the live carry CI fell below the backtest CI-lo (0.0502) for
N consecutive windows — the edge is arbitraged; do NOT escalate to v0.2/#4.
```

- [ ] **Step 4: Verify the docs build / mex clean**

Run: `mex check`
Expected: no NEW real drift attributable to these files (MISSING_PATH on runtime-created
`data/shadow/*.jsonl` is expected noise per CLAUDE.md — leave it).

- [ ] **Step 5: Record the decision in the event clock**

Run: `mex log "funding-carry shadow v0.1 deployed: daily decay monitor, paper-only, W/N frozen by power.py, decay-kill at CI-hi<0.0502. Spec+plan 2026-06-03. Defers v0.2 paper-exec + #4 capital."`

- [ ] **Step 6: Commit**

```bash
git add .mex/context/setup.md
git commit -m "docs(funding-shadow): daily scheduling + mex log decay-monitor deploy"
```

---

## Self-Review (completed against spec REV 3)

**Spec coverage:**
- §1 universe (9 frozen) → Task 1 (`SHADOW_SYMBOLS`), asserted excludes LINK/SOL.
- §2 scope (measures rate-decay, not execution) → encoded as docstrings + §5 declared non-decay (Task 7); no execution-friction code exists by construction.
- §3 estimando (reuse `carry_for_symbol`, span-annualized, $/notional, equal-weight, gate_a CI) → Task 5 (`symbol_window_return`, `pooled_decay`), guarded by the gapped-window test (N1).
- §4 live ingest (FAPI funding + 1h mark klines + spot, idempotent, gap fail-safe) → Tasks 2-4, 8.
- §5 per-settlement reconciliation (secondary, one-step, non-decay) → Task 7.
- §6 decay-kill (CI-hi vs 0.0502, N non-overlapping, in-sample-anchored, W/N frozen pre-run) → Tasks 6, 9, 10.
- §7 output (.jsonl append-only + derived .json) → Task 9.
- §8 file structure + acotada-pattern-inheritance → Tasks 2-10.
- §9 non-negotiables → asserted no positions/holdout across tasks; cost hash stamped (Task 9).
- §11 forks (W/N from power, spot source, no push) → Task 10 + constants.

**Placeholder scan:** No TBD/TODO; every code step has complete code; commands have expected output.

**Type consistency:** `symbol_window_return` returns float; `pooled_decay` returns the gate_a dict + `dropped`; `decay_state(ci_lo, ci_hi, weeks_below)` keyword-only, returns `{decay_state, weeks_below}`; `run_once` consumes these consistently. `window_complete` signature matches its call in `run_once`. `min_window_weeks` keyword-only matches Task 10 Step 5 usage.

**One open item carried to execution (not a blocker):** Task 10 Step 5 freezes the real `W`/`N` from the fossil sigma; until then the suite runs on provisional `W=2, N=4`. The freeze MUST happen before the first scheduled live run (spec §6).
```
