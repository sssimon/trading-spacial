# Funding-Carry Execution-Realism v0.2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the v0.2 execution-realism one-shot experiment (spec REV 2.1): U1 computes `T_FLOOR_REAL` from live walked orderbooks and renders a same-epoch PASS/THIN/FAIL/INVALID/ABORT verdict against v0.1's live rate; U2 emits a descriptive basis-σ table with NO verdict.

**Architecture:** Two new modules in `tools/funding_carry/` (`execution_cost.py` = U1, `leg_lag.py` = U2) plus v0.2-only fetchers in `live_ingest.py` (new functions only — v0.1 functions untouched). One-shot, settlement-adjacent (enforced hard-refuse), fail-LOUD (`FetchFailed` → ABORT; `InsufficientDepth` → flag/INVALID), atomic artifact writes to `data/retune/2026-06-04-funding-carry-exec-realism/`. Never writes `data/shadow/` (v0.1's namespace), never reads `funding.db`; `ohlcv.db` only via read-only `_liq_ro` with explicit busy_timeout.

**Tech Stack:** Python stdlib only (urllib, sqlite3, statistics, json) — same as v0.1. pytest with monkeypatch for network mocks.

**Spec:** `docs/superpowers/specs/2026-06-03-funding-carry-execution-realism-v0.2-design.md` (REV 2.1). Read §0 (co-location invariant), §3 (U1), §4 (U2), §7 (No-Negotiables) before starting. Where this plan and the spec disagree, the spec wins.

**Pre-registration discipline:** all numeric parameters in Task 1 are FROZEN once committed. Changing any after the first real run = a new experiment.

---

## File Structure

- `tools/funding_carry/constants.py` — append v0.2 frozen block (endpoints, fees, limits, output dir).
- `tools/funding_carry/live_ingest.py` — append: `FetchFailed`, `_get_json_retry`, `parse_depth`, `fetch_perp_depth`, `fetch_spot_depth`, `fetch_klines_1m_paginated`. **Do not modify any existing function** (v0.1 hot path).
- `tools/funding_carry/execution_cost.py` — NEW (U1): `walk_book`, `roundtrip_real_cost`, `t_floor_real`, `settlement_check`, `read_v01_state`, `verdict`, `_liq_ro`, `cost_v3_today`, `run`.
- `tools/funding_carry/leg_lag.py` — NEW (U2): `basis_sigma_1m`, `scale_to_window`, `run`.
- `tests/test_funding_carry.py` — append v0.2 tests (existing file, existing conventions: plain functions, `pytest.approx`, `tmp_path`).

---

### Task 1: Frozen v0.2 constants

**Files:**
- Modify: `tools/funding_carry/constants.py` (append at end)
- Test: `tests/test_funding_carry.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_funding_carry.py`:

```python
# ---------------------------------------------------------------------------
# Execution-realism v0.2 (spec 2026-06-03 REV 2.1)
# ---------------------------------------------------------------------------

def test_v02_constants_frozen():
    from tools.funding_carry import constants as C
    # Endpoints named truthfully (SPOT_* is spot, FAPI_* is futures) — Halberg RC-1.
    assert C.FAPI_PERP_DEPTH.startswith("https://fapi.binance.com/")
    assert C.SPOT_DEPTH.startswith("https://api.binance.com/")
    assert C.SPOT_KLINES_1M.startswith("https://api.binance.com/")
    # Frozen numerics (pre-registered; changing any = new experiment).
    assert C.DEPTH_LIMIT_PERP == 1000 and C.DEPTH_LIMIT_SPOT == 5000
    assert C.PERP_TAKER_FEE == 0.0005 and C.SPOT_TAKER_FEE == 0.001
    assert C.LEG_LAG_DAYS == 30 and C.LEG_LAG_T_SWEEP == (1, 10, 60, 300)
    assert C.SETTLEMENT_WINDOW_MIN == 15
    assert C.MAX_INSUFFICIENT_SYMBOLS == 2
    assert C.STATE_MAX_AGE_HOURS == 26
    assert C.HOLDING_HOURS_DIAG == 17520          # H_REF_YEARS * 8760
    assert C.KLINE_PAGE_LIMIT == 1500 and C.KLINE_MIN_COVERAGE == 0.98
    assert C.EXEC_REALISM_VERSION == "v0.2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_funding_carry.py::test_v02_constants_frozen -v`
Expected: FAIL with `AttributeError: ... has no attribute 'FAPI_PERP_DEPTH'`

- [ ] **Step 3: Write minimal implementation**

Append to `tools/funding_carry/constants.py`:

```python
# --- Execution-realism v0.2 (spec 2026-06-03 REV 2.1) — FROZEN 2026-06-04 ---
# Naming note (Halberg RC-1): legacy FAPI_SPOT above actually points at the SPOT
# ticker (mislabel). New constants use truthful prefixes: SPOT_* = api.binance.com,
# FAPI_* = fapi.binance.com. Renaming the legacy one = separate cleanup PR, not here.
FAPI_PERP_DEPTH = "https://fapi.binance.com/fapi/v1/depth"
SPOT_DEPTH = "https://api.binance.com/api/v3/depth"
SPOT_KLINES_1M = "https://api.binance.com/api/v3/klines"
DEPTH_LIMIT_PERP = 1000
DEPTH_LIMIT_SPOT = 5000
# Taker fees frozen NUMERICALLY (Binance public VIP0, 2026-06). NOT read from the
# v3 calibration: v3's fee is multiplied by stress_mult and does not co-locate
# with a walked-book cost (spec §5 / Adrian F6).
PERP_TAKER_FEE = 0.0005        # 5 bps per fill, USDT-M futures
SPOT_TAKER_FEE = 0.001         # 10 bps per fill, spot
LEG_LAG_DAYS = 30
LEG_LAG_T_SWEEP = (1, 10, 60, 300)   # seconds; DESCRIPTIVE sweep — no verdict, no T_REF
SETTLEMENT_WINDOW_MIN = 15     # run must start <= this many minutes AFTER a settlement
MAX_INSUFFICIENT_SYMBOLS = 2   # k > this -> verdict INVALID (k=3 invalidates, k=2 not)
STATE_MAX_AGE_HOURS = 26       # v0.1 state staleness bound (daily cadence + margin)
HOLDING_HOURS_DIAG = int(H_REF_YEARS * 8760)   # 17520h; cost_v3_hoy diagnostic holding
KLINE_PAGE_LIMIT = 1500        # Binance klines per-request cap (pagination required)
KLINE_MIN_COVERAGE = 0.98      # hard-fail below this fraction of expected 1m bars
EXEC_REALISM_OUTPUT_DIR = "data/retune/2026-06-04-funding-carry-exec-realism"
EXEC_REALISM_VERSION = "v0.2"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_funding_carry.py::test_v02_constants_frozen -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/constants.py tests/test_funding_carry.py
git commit -m "feat(exec-realism): frozen v0.2 constants (spec REV 2.1 §5)"
```

---

### Task 2: `FetchFailed` + `_get_json_retry` (bounded retry, Retry-After, weight log)

**Files:**
- Modify: `tools/funding_carry/live_ingest.py` (append; do NOT touch `_get_json` or any v0.1 function)
- Test: `tests/test_funding_carry.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_funding_carry.py`:

```python
def test_get_json_retry_succeeds_after_transient_error():
    from tools.funding_carry import live_ingest
    calls = {"n": 0}
    def fake_open(url, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionResetError("transient")
        return [{"ok": True}]
    out = live_ingest._get_json_retry("http://x", _open=fake_open, _sleep=lambda s: None)
    assert out == [{"ok": True}] and calls["n"] == 3

def test_get_json_retry_raises_fetch_failed_after_exhaustion():
    from tools.funding_carry import live_ingest
    def fake_open(url, timeout):
        raise ConnectionResetError("down")
    with pytest.raises(live_ingest.FetchFailed):
        live_ingest._get_json_retry("http://x", _open=fake_open, _sleep=lambda s: None)

def test_get_json_retry_honors_retry_after_on_429():
    import urllib.error
    from email.message import Message
    from tools.funding_carry import live_ingest
    sleeps = []
    calls = {"n": 0}
    hdrs = Message(); hdrs["Retry-After"] = "7"
    def fake_open(url, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError("http://x", 429, "rate", hdrs, None)
        return {"ok": 1}
    out = live_ingest._get_json_retry("http://x", _open=fake_open, _sleep=sleeps.append)
    assert out == {"ok": 1}
    assert sleeps == [7.0]      # Retry-After respected, not generic backoff
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_funding_carry.py -k get_json_retry -v`
Expected: 3 FAIL with `AttributeError: ... no attribute '_get_json_retry'`

- [ ] **Step 3: Write minimal implementation**

Append to `tools/funding_carry/live_ingest.py` (also add `import time` and `import urllib.error` to the imports block at the top — these are new imports, not modifications of existing functions):

```python
# ---------------------------------------------------------------------------
# Execution-realism v0.2 fetchers (spec 2026-06-03 REV 2.1 §5).
# v0.2 policy is fail-LOUD: FetchFailed propagates and the caller ABORTs the whole
# run — the verdict sample must NEVER be a function of network weather (Halberg).
# v0.1 functions above keep their bare _get_json + fail-soft contract, untouched.
# ---------------------------------------------------------------------------

class FetchFailed(Exception):
    """Network/HTTP failure after bounded retries. v0.2 callers ABORT, never shrink the pool."""


def _default_open(url: str, timeout: int):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        used = resp.headers.get("X-MBX-USED-WEIGHT-1M")
        if used:
            log.info("binance used-weight-1m=%s (%s)", used, url.split("?")[0])
        return json.loads(resp.read().decode("utf-8"))


def _get_json_retry(url: str, *, timeout: int = 30, retries: int = 3,
                    backoff_s: float = 2.0, _open=_default_open, _sleep=time.sleep):
    """GET+parse with bounded retry. Honors Retry-After on HTTP 429/418 (rate-limit),
    generic linear backoff otherwise. Raises FetchFailed after exhausting retries."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return _open(url, timeout)
        except urllib.error.HTTPError as e:
            last = e
            ra = e.headers.get("Retry-After") if e.code in (429, 418) and e.headers else None
            _sleep(float(ra) if ra else backoff_s * (attempt + 1))
        except Exception as e:                   # noqa: BLE001 — converted to FetchFailed below
            last = e
            _sleep(backoff_s * (attempt + 1))
    raise FetchFailed(f"{url}: {last!r}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_funding_carry.py -k get_json_retry -v`
Expected: 3 PASS

- [ ] **Step 5: Run the FULL existing suite (v0.1 untouched check)**

Run: `python -m pytest tests/test_funding_carry.py -v`
Expected: all PASS (no v0.1 regression)

- [ ] **Step 6: Commit**

```bash
git add tools/funding_carry/live_ingest.py tests/test_funding_carry.py
git commit -m "feat(exec-realism): FetchFailed + _get_json_retry with Retry-After (v0.2 fail-loud fetch layer)"
```

---

### Task 3: Depth fetchers (`parse_depth`, `fetch_perp_depth`, `fetch_spot_depth`)

**Files:**
- Modify: `tools/funding_carry/live_ingest.py` (append)
- Test: `tests/test_funding_carry.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_depth_maps_binance_payload():
    from tools.funding_carry import live_ingest
    payload = {"lastUpdateId": 1, "bids": [["99.5", "2.0"], ["99.0", "5.0"]],
               "asks": [["100.5", "1.0"], ["101.0", "4.0"]]}
    book = live_ingest.parse_depth(payload)
    assert book["bids"] == [(99.5, 2.0), (99.0, 5.0)]    # price-descending as Binance sends
    assert book["asks"] == [(100.5, 1.0), (101.0, 4.0)]  # price-ascending as Binance sends

def test_fetch_depth_propagates_fetch_failed(monkeypatch):
    from tools.funding_carry import live_ingest
    def boom(url, **kw):
        raise live_ingest.FetchFailed("down")
    monkeypatch.setattr(live_ingest, "_get_json_retry", boom)
    with pytest.raises(live_ingest.FetchFailed):
        live_ingest.fetch_perp_depth("BTCUSDT")
    with pytest.raises(live_ingest.FetchFailed):
        live_ingest.fetch_spot_depth("BTCUSDT")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_funding_carry.py -k "parse_depth or fetch_depth" -v`
Expected: FAIL (`parse_depth` not defined)

- [ ] **Step 3: Write minimal implementation**

Append to `tools/funding_carry/live_ingest.py` (add `FAPI_PERP_DEPTH, SPOT_DEPTH, DEPTH_LIMIT_PERP, DEPTH_LIMIT_SPOT` to the existing `from .constants import (...)` line):

```python
def parse_depth(payload: dict) -> dict:
    """Map a Binance depth payload to {'bids': [(price, qty)...], 'asks': [...]}.
    Order preserved as sent (bids best-first descending, asks best-first ascending)."""
    return {"bids": [(float(p), float(q)) for p, q in payload["bids"]],
            "asks": [(float(p), float(q)) for p, q in payload["asks"]]}


def fetch_perp_depth(symbol: str, *, limit: int = DEPTH_LIMIT_PERP) -> dict:
    """USDT-M perp orderbook snapshot. Raises FetchFailed (v0.2 ABORT policy)."""
    return parse_depth(_get_json_retry(
        f"{FAPI_PERP_DEPTH}?symbol={symbol}&limit={int(limit)}"))


def fetch_spot_depth(symbol: str, *, limit: int = DEPTH_LIMIT_SPOT) -> dict:
    """Spot orderbook snapshot. Raises FetchFailed (v0.2 ABORT policy)."""
    return parse_depth(_get_json_retry(
        f"{SPOT_DEPTH}?symbol={symbol}&limit={int(limit)}"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_funding_carry.py -k "parse_depth or fetch_depth" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/live_ingest.py tests/test_funding_carry.py
git commit -m "feat(exec-realism): perp/spot depth fetchers (fail-loud)"
```

---

### Task 4: `fetch_klines_1m_paginated` (pagination + short-series hard-fail)

**Files:**
- Modify: `tools/funding_carry/live_ingest.py` (append)
- Test: `tests/test_funding_carry.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def _fake_kline_pages(start_ms, n_bars, page_limit):
    """Build a fake paginated klines server: returns a callable like _get_json_retry."""
    all_rows = [[start_ms + i * 60_000, "0", "0", "0", str(100.0 + i), "0"]
                for i in range(n_bars)]
    def fake(url, **kw):
        import urllib.parse as up
        q = dict(up.parse_qsl(up.urlsplit(url).query))
        s, e, lim = int(q["startTime"]), int(q["endTime"]), int(q["limit"])
        page = [r for r in all_rows if s <= r[0] < e][:lim]
        return page
    return fake

def test_fetch_klines_1m_paginated_walks_pages(monkeypatch):
    from tools.funding_carry import live_ingest
    start = 0
    n = 3000                                   # > 2 pages at limit 1500
    end_ms = n * 60_000
    monkeypatch.setattr(live_ingest, "_get_json_retry",
                        _fake_kline_pages(start, n, 1500))
    out = live_ingest.fetch_klines_1m_paginated(
        "BTCUSDT", base_url="http://fake", days=end_ms / 86_400_000, end_ms=end_ms)
    assert len(out) == 3000
    assert out[0] == (0, 100.0) and out[-1] == ((n - 1) * 60_000, 100.0 + n - 1)
    assert out == sorted(out)                  # ascending, no duplicates

def test_fetch_klines_1m_paginated_hard_fails_on_short_series(monkeypatch):
    from tools.funding_carry import live_ingest
    # Server only has ~25h of data but caller asks for 30 days -> MUST raise,
    # never silently return a short series labeled 30d (Halberg BP-1).
    monkeypatch.setattr(live_ingest, "_get_json_retry",
                        _fake_kline_pages(0, 1500, 1500))
    with pytest.raises(live_ingest.FetchFailed):
        live_ingest.fetch_klines_1m_paginated(
            "BTCUSDT", base_url="http://fake", days=30, end_ms=30 * 86_400_000)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_funding_carry.py -k klines_1m_paginated -v`
Expected: FAIL (`fetch_klines_1m_paginated` not defined)

- [ ] **Step 3: Write minimal implementation**

Append to `tools/funding_carry/live_ingest.py` (add `KLINE_PAGE_LIMIT, KLINE_MIN_COVERAGE` to the constants import):

```python
def fetch_klines_1m_paginated(symbol: str, *, base_url: str, days: float, end_ms: int,
                              page_limit: int = KLINE_PAGE_LIMIT,
                              min_coverage: float = KLINE_MIN_COVERAGE
                              ) -> list[tuple[int, float]]:
    """1m closes over `days` ending at end_ms, paginated (Binance caps at 1500/request).
    Returns [(open_time_ms, close)] ascending. Raises FetchFailed if total bars
    < min_coverage x expected — NEVER silently truncates (spec §4, Halberg BP-1).
    <=2% missing bars (maintenance, thin perps) is tolerated as benign gaps."""
    start_ms = int(end_ms - days * 86_400_000)
    out: list[tuple[int, float]] = []
    cursor = start_ms
    while cursor < end_ms:
        url = (f"{base_url}?symbol={symbol}&interval=1m"
               f"&startTime={cursor}&endTime={int(end_ms)}&limit={int(page_limit)}")
        page = _get_json_retry(url)
        if not page:
            break
        rows = sorted((int(k[0]), float(k[4])) for k in page)
        out.extend(rows)
        cursor = rows[-1][0] + 60_000
    expected = days * 1440
    if len(out) < min_coverage * expected:
        raise FetchFailed(
            f"{symbol}: short 1m series {len(out)} < {min_coverage}x{expected:.0f} expected")
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_funding_carry.py -k klines_1m_paginated -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/live_ingest.py tests/test_funding_carry.py
git commit -m "feat(exec-realism): paginated 1m klines with short-series hard-fail"
```

---

### Task 5: `walk_book` (exact arithmetic, both sides, insufficient depth)

**Files:**
- Create: `tools/funding_carry/execution_cost.py`
- Test: `tests/test_funding_carry.py` (append)

- [ ] **Step 1: Write the failing tests**

The arithmetic is pinned by the spec §3.2: `mid = (best_bid + best_ask)/2` of THIS book; `qty_target = notional_usd / mid` (fixed-USD target converted at mid); `slippage_cost = |VWAP_fill − mid| × qty_target ≥ 0`.

Worked example: book bids `[(98.0, 1000)]`, asks `[(100.0, 50), (101.0, 200)]` → mid `= 99.0`; `qty_target = 10000/99 = 101.0101…`; buy walks asks: `50 @ 100` + `51.0101 @ 101` → fill cost `= 5000 + 5152.0202 = 10152.0202`; `mid × qty_target = 10000` exactly, so `slippage = 152.0202`.

```python
def _book(bids, asks):
    return {"bids": bids, "asks": asks}

def test_walk_book_buy_multi_level_exact():
    from tools.funding_carry import execution_cost as ec
    book = _book(bids=[(98.0, 1000.0)], asks=[(100.0, 50.0), (101.0, 200.0)])
    r = ec.walk_book(book, 10_000.0, "buy")
    assert r["mid"] == pytest.approx(99.0)
    assert r["qty_target"] == pytest.approx(10_000.0 / 99.0)
    # fill = 50@100 + 51.0101@101 = 10152.0202; mid*qty = 10000 exactly
    assert r["slippage_cost"] == pytest.approx(152.0202, abs=1e-3)
    assert r["vwap"] == pytest.approx(10_152.0202 / (10_000.0 / 99.0), abs=1e-3)

def test_walk_book_sell_single_level_exact():
    from tools.funding_carry import execution_cost as ec
    book = _book(bids=[(98.0, 1000.0)], asks=[(100.0, 1000.0)])
    r = ec.walk_book(book, 10_000.0, "sell")
    # mid=99, qty=101.0101, sell fills at 98 -> slippage = (99-98)*101.0101
    assert r["slippage_cost"] == pytest.approx(10_000.0 / 99.0, abs=1e-6)
    assert r["slippage_cost"] >= 0.0

def test_walk_book_insufficient_depth_raises():
    from tools.funding_carry import execution_cost as ec
    book = _book(bids=[(98.0, 1000.0)], asks=[(100.0, 50.0)])   # 50 qty < ~101 needed
    with pytest.raises(ec.InsufficientDepth):
        ec.walk_book(book, 10_000.0, "buy")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_funding_carry.py -k walk_book -v`
Expected: FAIL (`No module named ...execution_cost`)

- [ ] **Step 3: Write minimal implementation**

Create `tools/funding_carry/execution_cost.py`:

```python
"""Funding-carry execution-realism v0.2 — Unidad 1 (spec 2026-06-03 REV 2.1 §3).

ONE-SHOT, settlement-adjacent (enforced), paper-only. Computes T_FLOOR_REAL — the
cost floor measured against the LIVE orderbook, identical construction to
power.cost_floor — and renders PASS/THIN/FAIL against the LIVE pooled rate read
from v0.1's state: same epoch, same type, same denominator (Axiom-0 co-location
invariant, spec §0). The fossil replay is dead as a verdict; cost_v3_today
(same-epoch, via the same recost_four_legs that produced the fossil's cost_v3)
survives as a per-symbol upper-bound diagnostic.

Fail-LOUD: FetchFailed -> ABORT (the verdict sample is never a function of network
weather); InsufficientDepth -> per-symbol flag, INVALID above MAX_INSUFFICIENT_SYMBOLS.
Never writes data/shadow/ (v0.1's namespace); never reads funding.db; ohlcv.db only
via _liq_ro (read-only + busy_timeout). No positions, no orders, no holdout."""
from __future__ import annotations
import json
import os
import sqlite3
import statistics
from contextlib import closing
from datetime import datetime, timezone
from . import simulate
from .constants import (NOTIONAL, H_REF_YEARS, MARGIN, T_FLOOR, SHADOW_SYMBOLS,
                        SHADOW_OUTPUT_DIR, OHLCV_DB, PERP_TAKER_FEE, SPOT_TAKER_FEE,
                        SETTLEMENT_WINDOW_MIN, MAX_INSUFFICIENT_SYMBOLS,
                        STATE_MAX_AGE_HOURS, HOLDING_HOURS_DIAG,
                        EXEC_REALISM_OUTPUT_DIR, EXEC_REALISM_VERSION)

_SETTLEMENT_MS = 8 * 3_600_000          # funding settles 00:00/08:00/16:00 UTC


class InsufficientDepth(Exception):
    """The real book cannot fill NOTIONAL within the fetched levels — a FINDING
    against the edge (opposite meaning to FetchFailed = network weather)."""


class AbortRun(Exception):
    """Hard-refuse: precondition violated (off-window, stale/invalid v0.1 state,
    calibration drift, fetch failure). No verdict is computed."""


def walk_book(book: dict, notional_usd: float, side: str) -> dict:
    """Walk asks (buy) or bids (sell) to fill a fixed-USD target converted at mid.

    mid = (best_bid + best_ask) / 2 of THIS book; qty_target = notional_usd / mid;
    slippage_cost = |VWAP_fill - mid| * qty_target  (>= 0 both sides by construction).
    Raises InsufficientDepth if the levels cannot fill qty_target (spec §3.2)."""
    best_bid, best_ask = book["bids"][0][0], book["asks"][0][0]
    mid = (best_bid + best_ask) / 2.0
    qty_target = notional_usd / mid
    levels = book["asks"] if side == "buy" else book["bids"]
    filled = 0.0
    fill_cost = 0.0
    for price, qty in levels:
        take = min(qty, qty_target - filled)
        filled += take
        fill_cost += take * price
        if filled >= qty_target - 1e-12:
            break
    if filled < qty_target - 1e-12:
        raise InsufficientDepth(
            f"filled {filled:.8f} < target {qty_target:.8f} ({side})")
    vwap = fill_cost / qty_target
    return {"mid": mid, "qty_target": qty_target, "vwap": vwap,
            "slippage_cost": abs(vwap - mid) * qty_target}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_funding_carry.py -k walk_book -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/execution_cost.py tests/test_funding_carry.py
git commit -m "feat(exec-realism): walk_book with pinned mid/qty/slippage arithmetic"
```

---

### Task 6: `roundtrip_real_cost` (4 legs, all-in)

**Files:**
- Modify: `tools/funding_carry/execution_cost.py` (append)
- Test: `tests/test_funding_carry.py` (append)

- [ ] **Step 1: Write the failing test**

Symmetric books → exact expected values. Spot: bids `(98,1000)` / asks `(100,1000)` → mid 99, each leg slippage `= 1 × 10000/99 = 101.0101`. Perp: bids `(199,1000)` / asks `(201,1000)` → mid 200, qty 50, each leg slippage `= 1 × 50 = 50`. Fees `= 2×10000×0.001 + 2×10000×0.0005 = 30`.

```python
def test_roundtrip_real_cost_four_legs_all_in():
    from tools.funding_carry import execution_cost as ec
    spot = _book(bids=[(98.0, 1000.0)], asks=[(100.0, 1000.0)])
    perp = _book(bids=[(199.0, 1000.0)], asks=[(201.0, 1000.0)])
    r = ec.roundtrip_real_cost(perp, spot)
    slip_expected = 2 * (10_000.0 / 99.0) + 2 * 50.0      # 2 spot legs + 2 perp legs
    assert r["slippage_total"] == pytest.approx(slip_expected, abs=1e-6)
    assert r["fees_total"] == pytest.approx(30.0)          # 2x10bps + 2x5bps on 10k
    assert r["cost_real"] == pytest.approx(slip_expected + 30.0, abs=1e-6)
    assert set(r["legs"]) == {"spot_buy", "perp_sell", "spot_sell", "perp_buy"}

def test_roundtrip_real_cost_propagates_insufficient_depth():
    from tools.funding_carry import execution_cost as ec
    spot = _book(bids=[(98.0, 1000.0)], asks=[(100.0, 1.0)])   # too thin to buy
    perp = _book(bids=[(199.0, 1000.0)], asks=[(201.0, 1000.0)])
    with pytest.raises(ec.InsufficientDepth):
        ec.roundtrip_real_cost(perp, spot)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_funding_carry.py -k roundtrip_real_cost -v`
Expected: FAIL (`roundtrip_real_cost` not defined)

- [ ] **Step 3: Write minimal implementation**

Append to `tools/funding_carry/execution_cost.py`:

```python
def roundtrip_real_cost(perp_book: dict, spot_book: dict, *, notional: float = NOTIONAL,
                        spot_fee: float = SPOT_TAKER_FEE,
                        perp_fee: float = PERP_TAKER_FEE) -> dict:
    """All-in 4-leg roundtrip cost on the SAME snapshot (approximation §6.1):
    open = spot-buy + perp-sell; close = spot-sell + perp-buy.
    Per leg: slippage + taker_fee * notional.
    Leg/denominator convention pinned to the fossil (spec §3.3 / Adrian REV2-F5):
    recost_four_legs = 4 fills with per-leg notional; cost_floor divides the 4-leg
    total by NOTIONAL=10000 per-leg, NOT 2x. This function returns the 4-leg USD total."""
    legs = {
        "spot_buy":  walk_book(spot_book, notional, "buy"),
        "perp_sell": walk_book(perp_book, notional, "sell"),
        "spot_sell": walk_book(spot_book, notional, "sell"),
        "perp_buy":  walk_book(perp_book, notional, "buy"),
    }
    slip = sum(leg["slippage_cost"] for leg in legs.values())
    fees = 2 * notional * spot_fee + 2 * notional * perp_fee
    return {"cost_real": slip + fees, "slippage_total": slip,
            "fees_total": fees, "legs": legs}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_funding_carry.py -k roundtrip_real_cost -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/execution_cost.py tests/test_funding_carry.py
git commit -m "feat(exec-realism): roundtrip_real_cost — 4 legs all-in, fossil leg convention"
```

---

### Task 7: `t_floor_real` + keystone type-identity test

**Files:**
- Modify: `tools/funding_carry/execution_cost.py` (append)
- Test: `tests/test_funding_carry.py` (append)

- [ ] **Step 1: Write the failing tests**

The keystone (spec §3.4): feeding the fossil's own `cost_v3` values into `t_floor_real` must reproduce `power.cost_floor`'s output AND the frozen `T_FLOOR` constant exactly. This proves the type identity the whole verdict rests on.

```python
def test_t_floor_real_median_construction():
    from tools.funding_carry import execution_cost as ec
    # median([10, 20, 30])/10000/2.0 + 0.0 = 20/20000 = 0.001
    assert ec.t_floor_real([10.0, 20.0, 30.0]) == pytest.approx(0.001)

def test_t_floor_real_keystone_reproduces_frozen_t_floor():
    """KEYSTONE (spec §3.4 / Adrian REV2-F5): cost_real := fossil cost_v3 values
    => t_floor_real == power.cost_floor == frozen T_FLOOR. Fixed numeric expectation."""
    import json as _json
    from tools.funding_carry import execution_cost as ec
    from tools.funding_carry.power import cost_floor
    from tools.funding_carry.constants import (OUTPUT_DIR, T_FLOOR, NOTIONAL,
                                               H_REF_YEARS, MARGIN)
    path = os.path.join(OUTPUT_DIR, "per_symbol.json")
    if not os.path.exists(path):
        pytest.skip("fossil per_symbol.json not present in this checkout")
    with open(path, encoding="utf-8") as fh:
        records = _json.load(fh)
    costs = [rec["cost_v3"] for rec in records]
    tfr = ec.t_floor_real(costs)
    assert tfr == pytest.approx(cost_floor(path, notional=NOTIONAL,
                                           h_ref_years=H_REF_YEARS, margin=MARGIN))
    assert tfr == pytest.approx(T_FLOOR)        # 0.0038575872804181457
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_funding_carry.py -k t_floor_real -v`
Expected: FAIL (`t_floor_real` not defined)

- [ ] **Step 3: Write minimal implementation**

Append to `tools/funding_carry/execution_cost.py`:

```python
def t_floor_real(costs_usd: list[float], *, notional: float = NOTIONAL,
                 h_ref_years: float = H_REF_YEARS, margin: float = MARGIN) -> float:
    """Annualized REAL cost floor: median(cost/notional)/h_ref_years + margin.
    Construction IDENTICAL to power.cost_floor (median — PENDLE's cost is ~40x
    others; per-leg NOTIONAL denominator; H_REF amortization) with the live
    walked-book cost in place of the fossil's cost_v3. Keystone: feeding the
    fossil's cost_v3 values must reproduce the frozen T_FLOOR exactly."""
    per_sym = [c / notional for c in costs_usd]
    return statistics.median(per_sym) / h_ref_years + margin
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_funding_carry.py -k t_floor_real -v`
Expected: 2 PASS (or 1 PASS + 1 SKIP if the fossil artifact is absent — on this repo it must PASS; investigate if skipped)

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/execution_cost.py tests/test_funding_carry.py
git commit -m "feat(exec-realism): t_floor_real + keystone type-identity vs power.cost_floor"
```

---

### Task 8: Preconditions — `settlement_check`, `read_v01_state`, `verdict`

**Files:**
- Modify: `tools/funding_carry/execution_cost.py` (append)
- Test: `tests/test_funding_carry.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
_SETTLE = 8 * 3_600_000

def _v01_state(tmp_path, **over):
    """Fabricate a valid v0.1 state.json; override fields via kwargs."""
    base = {
        "run_ts_utc": "2026-06-04T08:05:00+00:00",
        "decay_state": "THIN",
        "R_pooled": 0.036, "R_ci_lo": 0.0094, "R_ci_hi": 0.0606,
        "calibration_identity_hash": "CALHASH",
    }
    base.update(over)
    for k, v in list(base.items()):
        if v is None:
            del base[k]
    p = tmp_path / "funding_carry_state.json"
    p.write_text(json.dumps(base), encoding="utf-8")
    return str(p)

def test_settlement_check_inside_window_returns_settlement():
    from tools.funding_carry import execution_cost as ec
    now = 1_780_531_200_000 + 10 * 60_000          # settlement + 10min
    assert ec.settlement_check(now) == 1_780_531_200_000

def test_settlement_check_off_window_aborts():
    from tools.funding_carry import execution_cost as ec
    now = 1_780_531_200_000 + 20 * 60_000          # settlement + 20min > 15
    with pytest.raises(ec.AbortRun):
        ec.settlement_check(now)

def test_read_v01_state_happy_path(tmp_path):
    from tools.funding_carry import execution_cost as ec
    path = _v01_state(tmp_path)
    now_ms = 1_780_905_900_000                      # 2026-06-04T08:05 UTC + small delta
    st = ec.read_v01_state(path, now_ms=now_ms)
    assert st["R_ci_lo"] == 0.0094

def test_read_v01_state_aborts_on_stale(tmp_path):
    from tools.funding_carry import execution_cost as ec
    path = _v01_state(tmp_path, run_ts_utc="2026-06-01T08:05:00+00:00")
    with pytest.raises(ec.AbortRun):
        ec.read_v01_state(path, now_ms=1_780_905_900_000)   # ~3 days later > 26h

def test_read_v01_state_aborts_on_bad_decay_state(tmp_path):
    from tools.funding_carry import execution_cost as ec
    for bad in ("ERROR", "INCOMPLETE", "REFUTED"):
        path = _v01_state(tmp_path, decay_state=bad)
        with pytest.raises(ec.AbortRun):
            ec.read_v01_state(path, now_ms=1_780_905_900_000)

def test_read_v01_state_aborts_clean_on_missing_keys(tmp_path):
    # v0.1's ERROR branch omits R_ci_lo/R_ci_hi/calibration_identity_hash:
    # the guard must hard-refuse cleanly, NOT crash with KeyError (Adrian REV2-F8).
    from tools.funding_carry import execution_cost as ec
    path = _v01_state(tmp_path, R_ci_lo=None)
    with pytest.raises(ec.AbortRun):
        ec.read_v01_state(path, now_ms=1_780_905_900_000)

def test_verdict_semantics_isomorphic_to_rev5():
    from tools.funding_carry import execution_cost as ec
    st = {"R_ci_lo": 0.0094, "R_ci_hi": 0.0606}
    assert ec.verdict(0.002, st) == "PASS"      # ci_lo >= floor
    assert ec.verdict(0.030, st) == "THIN"      # ci_lo < floor <= ci_hi
    assert ec.verdict(0.080, st) == "FAIL"      # ci_hi < floor
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_funding_carry.py -k "settlement_check or read_v01_state or verdict_semantics" -v`
Expected: FAIL (functions not defined)

- [ ] **Step 3: Write minimal implementation**

Append to `tools/funding_carry/execution_cost.py`:

```python
def settlement_check(now_ms: int, *, window_min: int = SETTLEMENT_WINDOW_MIN) -> int:
    """Return the funding settlement (ms, 8h-grid: 00/08/16 UTC) this run is adjacent
    to. Hard-refuse if now is more than window_min minutes after the last settlement —
    instrument-time co-location is ENFORCED, not trusted (spec §3 / Adrian REV2-F7)."""
    last = (int(now_ms) // _SETTLEMENT_MS) * _SETTLEMENT_MS
    delta_min = (now_ms - last) / 60_000.0
    if delta_min > window_min:
        raise AbortRun(f"off-window: {delta_min:.1f}min after settlement "
                       f"> SETTLEMENT_WINDOW_MIN={window_min}")
    return last


_REQUIRED_STATE_KEYS = ("run_ts_utc", "decay_state", "R_pooled", "R_ci_lo",
                        "R_ci_hi", "calibration_identity_hash")


def read_v01_state(path: str, *, now_ms: int,
                   max_age_hours: float = STATE_MAX_AGE_HOURS) -> dict:
    """Validated read of v0.1's state.json — the verdict's LEFT operand (spec §3
    preconditions / Adrian REV2-F4/F8). ABORT (clean, never KeyError) on:
    missing file; missing keys (v0.1's ERROR branch omits them); staleness
    > max_age_hours; decay_state not in {ALIVE, THIN} (ERROR/INCOMPLETE = no
    operand today; REFUTED = v0.1 already killed the edge, v0.2 is moot)."""
    if not os.path.exists(path):
        raise AbortRun(f"v0.1 state missing: {path}")
    with open(path, encoding="utf-8") as fh:
        st = json.load(fh)
    missing = [k for k in _REQUIRED_STATE_KEYS if k not in st]
    if missing:
        raise AbortRun(f"v0.1 state missing keys {missing} "
                       f"(decay_state={st.get('decay_state')!r})")
    run_ms = int(datetime.fromisoformat(st["run_ts_utc"]).timestamp() * 1000)
    age_h = (now_ms - run_ms) / 3_600_000.0
    if age_h > max_age_hours:
        raise AbortRun(f"v0.1 state stale: {age_h:.1f}h > {max_age_hours}h")
    if st["decay_state"] not in ("ALIVE", "THIN"):
        raise AbortRun(f"v0.1 decay_state={st['decay_state']!r} — no valid left operand")
    return st


def verdict(t_floor_real_val: float, state: dict) -> str:
    """PASS/THIN/FAIL — same semantics as v0.1 REV 5 but against the REAL floor.
    A snapshot: does NOT touch v0.1's kill counter (spec §8)."""
    if state["R_ci_lo"] >= t_floor_real_val:
        return "PASS"
    if state["R_ci_hi"] < t_floor_real_val:
        return "FAIL"
    return "THIN"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_funding_carry.py -k "settlement_check or read_v01_state or verdict_semantics" -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/execution_cost.py tests/test_funding_carry.py
git commit -m "feat(exec-realism): enforced preconditions — settlement window, v0.1 state guards, REV5-isomorphic verdict"
```

---

### Task 9: `_liq_ro` + `cost_v3_today` (same-epoch diagnostic)

**Files:**
- Modify: `tools/funding_carry/execution_cost.py` (append)
- Test: `tests/test_funding_carry.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def _mk_ohlcv_db(path, n_bars=720):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE ohlcv(symbol TEXT, timeframe TEXT, open_time INTEGER,"
                " close REAL, volume REAL)")
    for i in range(n_bars):
        con.execute("INSERT INTO ohlcv VALUES('BTCUSDT','1h',?,100.0,60.0)",
                    (i * 3_600_000,))
    con.commit(); con.close()

def test_liq_ro_matches_spot_liquidity_query(tmp_path):
    from tools.funding_carry import execution_cost as ec
    db = tmp_path / "ohlcv.db"
    _mk_ohlcv_db(db)
    liq = ec._liq_ro(str(db), "BTCUSDT", 720 * 3_600_000)
    # 720 bars of close=100, vol=60 -> sum(100*60/60)/720 = 100.0
    assert liq == pytest.approx(100.0)
    # And identical to v0.1's spot_liquidity on the same data (same query semantics).
    assert liq == pytest.approx(simulate.spot_liquidity(str(db), "BTCUSDT", 720 * 3_600_000))

def test_liq_ro_nan_under_120_bars(tmp_path):
    import math
    from tools.funding_carry import execution_cost as ec
    db = tmp_path / "thin.db"
    _mk_ohlcv_db(db, n_bars=10)
    assert math.isnan(ec._liq_ro(str(db), "BTCUSDT", 720 * 3_600_000))

def test_cost_v3_today_uses_recost_four_legs(monkeypatch):
    from tools.funding_carry import execution_cost as ec
    seen = {}
    def fake_recost(**kw):
        seen.update(kw); return 42.0
    monkeypatch.setattr(ec.simulate, "recost_four_legs", fake_recost)
    out = ec.cost_v3_today("BTCUSDT", spot_mid=40_000.0, perp_mid=40_100.0, liq=5e6)
    assert out == 42.0
    assert seen["units"] == pytest.approx(10_000.0 / 40_000.0)   # NOTIONAL / spot_mid
    assert seen["holding_hours"] == 17520                         # HOLDING_HOURS_DIAG frozen
    assert seen["spot_price"] == 40_000.0 and seen["perp_price"] == 40_100.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_funding_carry.py -k "liq_ro or cost_v3_today" -v`
Expected: FAIL (functions not defined)

- [ ] **Step 3: Write minimal implementation**

Append to `tools/funding_carry/execution_cost.py`:

```python
def _liq_ro(ohlcv_db: str, symbol: str, ts_ms: int, *,
            busy_timeout_ms: int = 5000) -> float:
    """spot_liquidity's exact query with an explicit busy_timeout on an own read-only
    connection — v0.2 must not modify v0.1 functions and must not hit SQLITE_BUSY
    against scanner writes (spec §3 / Adrian REV2-F10). Same semantics: 30-day
    rolling USD/min proxy from spot 1h bars; NaN under 120 bars."""
    with closing(sqlite3.connect(f"file:{ohlcv_db}?mode=ro", uri=True)) as con:
        con.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        rows = con.execute(
            "SELECT close, volume FROM ohlcv WHERE symbol=? AND timeframe='1h' "
            "AND open_time<=? ORDER BY open_time DESC LIMIT 720",
            (symbol, ts_ms)).fetchall()
    if len(rows) < 120:
        return float("nan")
    return sum(c * v / 60.0 for c, v in rows) / len(rows)


def cost_v3_today(symbol: str, *, spot_mid: float, perp_mid: float, liq: float,
                  holding_hours: float = HOLDING_HOURS_DIAG) -> float:
    """Same-epoch v3 cost via the SAME function that produced the fossil's cost_v3
    (recost_four_legs — one invocation, not a hand reconstruction of
    compute_trade_costs; Adrian REV2-F9), with live mids/liq and the frozen
    diagnostic holding. Same 4-leg basis as roundtrip_real_cost by construction."""
    units = NOTIONAL / spot_mid
    return simulate.recost_four_legs(symbol=symbol, units=units, spot_price=spot_mid,
                                     perp_price=perp_mid, liq=liq,
                                     holding_hours=holding_hours)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_funding_carry.py -k "liq_ro or cost_v3_today" -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/execution_cost.py tests/test_funding_carry.py
git commit -m "feat(exec-realism): _liq_ro (busy_timeout, v0.1 untouched) + cost_v3_today via recost_four_legs"
```

---

### Task 10: U1 `run()` orchestration (ABORT/INVALID paths, atomic artifact)

**Files:**
- Modify: `tools/funding_carry/execution_cost.py` (append)
- Test: `tests/test_funding_carry.py` (append)

- [ ] **Step 1: Write the failing tests**

`1_780_531_200_000` is exactly on the 8h settlement grid (it is a week-block boundary from v0.1's grid, and weeks are multiples of 8h). All tests run "settlement + 5min".

```python
_NOW = 1_780_531_200_000 + 5 * 60_000     # settlement + 5min, inside window

def _exec_env(tmp_path, monkeypatch, *, state_over=None, books=None):
    """Wire a full fake environment for execution_cost.run()."""
    from tools.funding_carry import execution_cost as ec
    from tools.funding_carry import live_ingest
    state_path = _v01_state(tmp_path, run_ts_utc="2026-06-04T05:00:00+00:00",
                            **(state_over or {}))
    out_dir = str(tmp_path / "artifact")
    good_spot = _book(bids=[(98.0, 1000.0)], asks=[(100.0, 1000.0)])
    good_perp = _book(bids=[(199.0, 1000.0)], asks=[(201.0, 1000.0)])
    books = books if books is not None else {}
    monkeypatch.setattr(live_ingest, "fetch_spot_depth",
                        lambda s, **kw: books.get(("spot", s), good_spot))
    monkeypatch.setattr(live_ingest, "fetch_perp_depth",
                        lambda s, **kw: books.get(("perp", s), good_perp))
    monkeypatch.setattr(ec, "_cal_hash", lambda: "CALHASH")
    monkeypatch.setattr(ec, "_liq_ro", lambda db, s, ts, **kw: 5_000_000.0)
    return ec, state_path, out_dir

def test_run_happy_path_writes_verdict_and_artifact(tmp_path, monkeypatch):
    ec, state_path, out_dir = _exec_env(tmp_path, monkeypatch)
    res = ec.run(now_ms=_NOW, out_dir=out_dir, state_path=state_path,
                 ohlcv_db="unused.db")
    assert res["verdict"] in ("PASS", "THIN", "FAIL")     # computed, not ABORT/INVALID
    assert res["t_floor_real"] > 0.0
    assert res["floor_ratio_vs_v3"] == pytest.approx(res["t_floor_real"] / res["t_floor_v3"])
    assert res["n_ok"] == 9 and res["insufficient"] == []
    # Artifact files exist and are valid JSON / markdown.
    assert os.path.exists(os.path.join(out_dir, "per_symbol.json"))
    assert os.path.exists(os.path.join(out_dir, "findings.md"))
    assert os.path.isdir(os.path.join(out_dir, "depth_snapshots"))
    with open(os.path.join(out_dir, "per_symbol.json"), encoding="utf-8") as fh:
        per = json.load(fh)
    assert set(per) == set(ec.SHADOW_SYMBOLS)
    for rec in per.values():
        assert rec["status"] == "OK"
        assert "cost_real" in rec and "cost_v3_hoy" in rec and "ratio" in rec

def test_run_aborts_off_window(tmp_path, monkeypatch):
    ec, state_path, out_dir = _exec_env(tmp_path, monkeypatch)
    res = ec.run(now_ms=1_780_531_200_000 + 60 * 60_000,   # settlement + 1h
                 out_dir=out_dir, state_path=state_path, ohlcv_db="unused.db")
    assert res["verdict"] == "ABORT" and "off-window" in res["reason"]

def test_run_aborts_on_calibration_drift(tmp_path, monkeypatch):
    ec, state_path, out_dir = _exec_env(
        tmp_path, monkeypatch, state_over={"calibration_identity_hash": "OTHER"})
    res = ec.run(now_ms=_NOW, out_dir=out_dir, state_path=state_path,
                 ohlcv_db="unused.db")
    assert res["verdict"] == "ABORT" and "calibration" in res["reason"]

def test_run_aborts_on_fetch_failed_never_shrinks_pool(tmp_path, monkeypatch):
    from tools.funding_carry import live_ingest
    ec, state_path, out_dir = _exec_env(tmp_path, monkeypatch)
    def boom(s, **kw):
        raise live_ingest.FetchFailed("rate limited")
    monkeypatch.setattr(live_ingest, "fetch_perp_depth", boom)
    res = ec.run(now_ms=_NOW, out_dir=out_dir, state_path=state_path,
                 ohlcv_db="unused.db")
    assert res["verdict"] == "ABORT" and "FETCH_FAILED" in res["reason"]

def test_run_invalid_above_max_insufficient(tmp_path, monkeypatch):
    from tools.funding_carry.constants import SHADOW_SYMBOLS
    thin = _book(bids=[(98.0, 1000.0)], asks=[(100.0, 0.001)])   # cannot fill buy
    books = {("spot", s): thin for s in SHADOW_SYMBOLS[:3]}      # k=3 > MAX=2
    ec, state_path, out_dir = _exec_env(tmp_path, monkeypatch, books=books)
    res = ec.run(now_ms=_NOW, out_dir=out_dir, state_path=state_path,
                 ohlcv_db="unused.db")
    assert res["verdict"] == "INVALID"
    assert sorted(res["insufficient"]) == sorted(SHADOW_SYMBOLS[:3])

def test_run_flags_but_proceeds_at_max_insufficient(tmp_path, monkeypatch):
    from tools.funding_carry.constants import SHADOW_SYMBOLS
    thin = _book(bids=[(98.0, 1000.0)], asks=[(100.0, 0.001)])
    books = {("spot", s): thin for s in SHADOW_SYMBOLS[:2]}      # k=2 == MAX -> proceed
    ec, state_path, out_dir = _exec_env(tmp_path, monkeypatch, books=books)
    res = ec.run(now_ms=_NOW, out_dir=out_dir, state_path=state_path,
                 ohlcv_db="unused.db")
    assert res["verdict"] in ("PASS", "THIN", "FAIL")
    assert len(res["insufficient"]) == 2 and res["n_ok"] == 7

def test_run_never_writes_data_shadow(tmp_path, monkeypatch):
    # NN spec §7: data/shadow/ is v0.1's namespace. run() must not create/modify it.
    ec, state_path, out_dir = _exec_env(tmp_path, monkeypatch)
    shadow_dir = tmp_path / "data" / "shadow"
    monkeypatch.chdir(tmp_path)
    ec.run(now_ms=_NOW, out_dir=out_dir, state_path=state_path, ohlcv_db="unused.db")
    assert not shadow_dir.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_funding_carry.py -k "test_run_" -v`
Expected: FAIL (`run` not defined on execution_cost)

Note: v0.1 has `run_once` tests — the `-k "test_run_"` filter may catch them; that is fine, they must keep passing.

- [ ] **Step 3: Write minimal implementation**

Append to `tools/funding_carry/execution_cost.py`:

```python
def _cal_hash() -> str:
    from backtest_costs import calibration_identity_hash, load_calibration
    return calibration_identity_hash(load_calibration())


def _atomic_write(path: str, text: str) -> None:
    """temp + os.replace — a crash mid-write never leaves a torn artifact (Halberg CF-1)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _findings_md(res: dict) -> str:
    lines = [
        "# Funding-carry execution-realism v0.2 — U1 findings",
        "",
        f"- run_ts_utc: {res['run_ts_utc']}",
        f"- settlement_ts_ms: {res.get('settlement_ts_ms')}",
        f"- **VERDICT: {res['verdict']}**" + (f" — {res['reason']}" if res.get("reason") else ""),
        f"- T_FLOOR_REAL: {res.get('t_floor_real')}",
        f"- T_FLOOR (v3, frozen): {res.get('t_floor_v3')}",
        f"- floor_ratio real/v3: {res.get('floor_ratio_vs_v3')}",
        f"- live rate (v0.1 state): R_pooled={res.get('R_pooled')} "
        f"CI=[{res.get('R_ci_lo')}, {res.get('R_ci_hi')}] decay_state={res.get('v01_decay_state')}",
        f"- n_ok={res.get('n_ok')} insufficient={res.get('insufficient')}",
        f"- calibration_identity_hash: {res.get('calibration_identity_hash')}",
        f"- version: {res.get('version')}",
        "",
        "A PASS here is a same-epoch snapshot (rate vivo vs piso real). It is NOT by",
        "itself a go for #4 — deployability has no joint estimator yet (spec §8).",
    ]
    return "\n".join(lines) + "\n"


def run(*, now_ms: int, out_dir: str = EXEC_REALISM_OUTPUT_DIR,
        state_path: str = os.path.join(SHADOW_OUTPUT_DIR, "funding_carry_state.json"),
        ohlcv_db: str = OHLCV_DB, symbols: tuple = SHADOW_SYMBOLS) -> dict:
    """One-shot U1 (spec §3). Order: enforce settlement window -> validate v0.1 state ->
    hard-refuse on calibration drift -> fetch+walk all 9 books (FetchFailed -> ABORT;
    InsufficientDepth -> flag) -> T_FLOOR_REAL -> verdict -> atomic artifact.
    Returns the result dict (also written to out_dir). Never touches data/shadow/."""
    from . import live_ingest
    run_ts = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat()
    res: dict = {"run_ts_utc": run_ts, "version": EXEC_REALISM_VERSION,
                 "t_floor_v3": T_FLOOR}
    os.makedirs(out_dir, exist_ok=True)
    snap_dir = os.path.join(out_dir, "depth_snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    try:
        settlement = settlement_check(now_ms)
        res["settlement_ts_ms"] = settlement
        state = read_v01_state(state_path, now_ms=now_ms)
        res.update({"R_pooled": state["R_pooled"], "R_ci_lo": state["R_ci_lo"],
                    "R_ci_hi": state["R_ci_hi"], "v01_decay_state": state["decay_state"],
                    "v01_run_ts_utc": state["run_ts_utc"]})
        cal = _cal_hash()
        res["calibration_identity_hash"] = cal
        if cal != state["calibration_identity_hash"]:
            raise AbortRun(f"calibration drift: live={cal[:12]} "
                           f"!= v0.1 state={state['calibration_identity_hash'][:12]}")

        per_symbol: dict = {}
        insufficient: list[str] = []
        try:
            for s in symbols:
                perp_book = live_ingest.fetch_perp_depth(s)
                spot_book = live_ingest.fetch_spot_depth(s)
                _atomic_write(os.path.join(snap_dir, f"{s}.json"), json.dumps(
                    {"fetched_now_ms": now_ms, "perp": perp_book, "spot": spot_book}))
                try:
                    rt = roundtrip_real_cost(perp_book, spot_book)
                except InsufficientDepth as e:
                    insufficient.append(s)
                    per_symbol[s] = {"status": "INSUFFICIENT_DEPTH", "detail": str(e)}
                    continue
                spot_mid = rt["legs"]["spot_buy"]["mid"]
                perp_mid = rt["legs"]["perp_buy"]["mid"]
                liq = _liq_ro(ohlcv_db, s, now_ms)
                v3 = cost_v3_today(s, spot_mid=spot_mid, perp_mid=perp_mid, liq=liq)
                per_symbol[s] = {
                    "status": "OK", "cost_real": rt["cost_real"],
                    "slippage_total": rt["slippage_total"], "fees_total": rt["fees_total"],
                    "cost_v3_hoy": v3, "ratio": rt["cost_real"] / v3 if v3 else None,
                    "violation_v3_upper_bound": bool(v3) and rt["cost_real"] > v3,
                    "spot_mid": spot_mid, "perp_mid": perp_mid, "liq": liq,
                }
        except live_ingest.FetchFailed as e:
            raise AbortRun(f"FETCH_FAILED: {e}") from e

        res["insufficient"] = insufficient
        res["n_ok"] = len(symbols) - len(insufficient)
        if len(insufficient) > MAX_INSUFFICIENT_SYMBOLS:
            res["verdict"] = "INVALID"
            res["reason"] = (f"k={len(insufficient)} INSUFFICIENT_DEPTH "
                             f"> MAX_INSUFFICIENT_SYMBOLS={MAX_INSUFFICIENT_SYMBOLS}")
            res["t_floor_real"] = None
            res["floor_ratio_vs_v3"] = None
        else:
            costs = [per_symbol[s]["cost_real"] for s in symbols
                     if per_symbol[s]["status"] == "OK"]
            tfr = t_floor_real(costs)
            res["t_floor_real"] = tfr
            res["floor_ratio_vs_v3"] = tfr / T_FLOOR
            res["verdict"] = verdict(tfr, state)
        _atomic_write(os.path.join(out_dir, "per_symbol.json"),
                      json.dumps(per_symbol, indent=2))
    except AbortRun as e:
        res["verdict"] = "ABORT"
        res["reason"] = str(e)
    _atomic_write(os.path.join(out_dir, "findings.md"), _findings_md(res))
    _atomic_write(os.path.join(out_dir, "u1_result.json"), json.dumps(res, indent=2))
    return res


if __name__ == "__main__":
    import time
    print(json.dumps(run(now_ms=int(time.time() * 1000)), indent=2))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_funding_carry.py -k "test_run_" -v`
Expected: all new `test_run_*` PASS, all pre-existing v0.1 `test_run_once*` still PASS

Note: `test_run_happy_path` exercises the real `simulate.recost_four_legs` (loads the v3 calibration from the repo) — this works in CI exactly like the existing `test_recost_four_legs_positive` does.

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/execution_cost.py tests/test_funding_carry.py
git commit -m "feat(exec-realism): U1 run() — ABORT/INVALID paths, atomic artifact, shadow-namespace untouched"
```

---

### Task 11: U2 pure functions — `basis_sigma_1m`, `scale_to_window`

**Files:**
- Create: `tools/funding_carry/leg_lag.py`
- Test: `tests/test_funding_carry.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_basis_sigma_1m_deterministic():
    from tools.funding_carry import leg_lag
    # spot constant 100; perp alternates 100.0 / 100.1 -> basis 0, 0.001, 0, 0.001
    # deltas = +0.001, -0.001, +0.001 -> stdev = sqrt( sum((d-mean)^2)/(n-1) )
    spot = [(i * 60_000, 100.0) for i in range(4)]
    perp = [(0, 100.0), (60_000, 100.1), (120_000, 100.0), (180_000, 100.1)]
    s = leg_lag.basis_sigma_1m(spot, perp)
    assert s == pytest.approx(statistics.stdev([0.001, -0.001, 0.001]))

def test_basis_sigma_1m_aligns_on_common_timestamps():
    from tools.funding_carry import leg_lag
    spot = [(0, 100.0), (60_000, 100.0), (120_000, 100.0)]
    perp = [(0, 100.0), (120_000, 100.2)]          # missing the middle bar
    s = leg_lag.basis_sigma_1m(spot, perp)
    # only ts {0, 120000} align -> basis [0, 0.002] -> one delta -> stdev of 1 value
    # is undefined: function must return 0.0 for < 2 deltas, not crash.
    assert s == 0.0

def test_scale_to_window_sqrt_t():
    import math
    from tools.funding_carry import leg_lag
    assert leg_lag.scale_to_window(0.004, 60) == pytest.approx(0.004)
    assert leg_lag.scale_to_window(0.004, 15) == pytest.approx(0.002)
    assert leg_lag.scale_to_window(0.004, 300) == pytest.approx(0.004 * math.sqrt(5))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_funding_carry.py -k "basis_sigma or scale_to_window" -v`
Expected: FAIL (`No module named ...leg_lag`)

- [ ] **Step 3: Write minimal implementation**

Create `tools/funding_carry/leg_lag.py`:

```python
"""Funding-carry execution-realism v0.2 — Unidad 2 (spec 2026-06-03 REV 2.1 §4).

DESCRIPTIVE ONLY — NO VERDICT, by design: sigma_T x NOTIONAL is a 2nd-moment
quantity, and a risk bound is only comparable against a risk budget (2nd moment),
which does not exist yet. Renaming a drag to a bound does not co-locate the types
(Axiom-0 / Richter). This module measures and tabulates; interpretation waits.

One-shot, paper-only. Approximations declared in spec §6: sqrt(T) sub-minute
extrapolation (§6.2), T is an ASSUMED window (§6.3), mark-basis != executable
basis (§6.4). Fail-LOUD: a short klines series raises FetchFailed (never sigma
over 25h labeled 30d)."""
from __future__ import annotations
import json
import math
import os
import statistics
from datetime import datetime, timezone
from .constants import (SHADOW_SYMBOLS, NOTIONAL, LEG_LAG_DAYS, LEG_LAG_T_SWEEP,
                        SPOT_KLINES_1M, FAPI_MARK_KLINES,
                        EXEC_REALISM_OUTPUT_DIR, EXEC_REALISM_VERSION)


def basis_sigma_1m(spot_closes: list[tuple[int, float]],
                   perp_closes: list[tuple[int, float]]) -> float:
    """std of per-minute changes of the relative basis (perp-spot)/spot, computed
    over timestamps present in BOTH series (inner join). Returns 0.0 when fewer
    than 2 deltas exist (degenerate, not an error — the table will show it)."""
    spot = dict(spot_closes)
    perp = dict(perp_closes)
    ts = sorted(set(spot) & set(perp))
    basis = [(perp[t] - spot[t]) / spot[t] for t in ts]
    deltas = [b2 - b1 for b1, b2 in zip(basis, basis[1:])]
    if len(deltas) < 2:
        return 0.0
    return statistics.stdev(deltas)


def scale_to_window(sigma_1m: float, t_seconds: float) -> float:
    """Random-walk scaling: sigma_T = sigma_1m * sqrt(T/60). Declared approximation
    (spec §6.2) — a description, not a measurement."""
    return sigma_1m * math.sqrt(t_seconds / 60.0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_funding_carry.py -k "basis_sigma or scale_to_window" -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/leg_lag.py tests/test_funding_carry.py
git commit -m "feat(exec-realism): U2 pure functions — basis sigma + sqrt(T) scaling (descriptive, no verdict)"
```

---

### Task 12: U2 `run()` — descriptive table artifact

**Files:**
- Modify: `tools/funding_carry/leg_lag.py` (append)
- Test: `tests/test_funding_carry.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_leg_lag_run_emits_table_no_verdict(tmp_path, monkeypatch):
    from tools.funding_carry import leg_lag, live_ingest
    # 1m series with constant tiny basis wiggle, full coverage.
    n = int(0.99 * 30 * 1440)
    spot = [(i * 60_000, 100.0) for i in range(n)]
    perp = [(i * 60_000, 100.0 + 0.05 * (i % 2)) for i in range(n)]
    def fake_fetch(symbol, *, base_url, days, end_ms, **kw):
        return spot if "api.binance.com" in base_url else perp
    monkeypatch.setattr(live_ingest, "fetch_klines_1m_paginated", fake_fetch)
    out_dir = str(tmp_path / "artifact")
    res = leg_lag.run(now_ms=n * 60_000, out_dir=out_dir)
    assert set(res["per_symbol"]) == set(leg_lag.SHADOW_SYMBOLS)
    row = res["per_symbol"]["BTCUSDT"]
    assert row["sigma_1m"] > 0.0
    assert set(row["per_event_usd"]) == {1, 10, 60, 300}      # the full sweep
    for t in (1, 10, 60, 300):
        assert row["hold_continuo_usd"][t] == pytest.approx(
            row["per_event_usd"][t] * (2 ** 0.5))             # sqrt(2) aggregate labeled apart
    # NO verdict anywhere — descriptive by design (spec §4).
    assert "verdict" not in res
    assert os.path.exists(os.path.join(out_dir, "leg_lag.json"))
    assert os.path.exists(os.path.join(out_dir, "leg_lag.md"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_funding_carry.py -k leg_lag_run -v`
Expected: FAIL (`run` not defined on leg_lag)

- [ ] **Step 3: Write minimal implementation**

Append to `tools/funding_carry/leg_lag.py`:

```python
def _atomic_write(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _table_md(res: dict) -> str:
    head = ("# U2 — basis sigma table (DESCRIPTIVE, no verdict)\n\n"
            f"run_ts_utc: {res['run_ts_utc']} · days={LEG_LAG_DAYS} · "
            f"notional/leg={NOTIONAL} · version={res['version']}\n\n"
            "A 2nd-moment scale. NOT comparable against costs or carry (1st moment);\n"
            "interpretation waits for a declared risk budget (spec §4).\n\n"
            "| symbol | sigma_1m | " +
            " | ".join(f"per-event USD T={t}s" for t in LEG_LAG_T_SWEEP) + " | " +
            " | ".join(f"hold-cont USD T={t}s" for t in LEG_LAG_T_SWEEP) + " |\n" +
            "|" + "---|" * (2 + 2 * len(LEG_LAG_T_SWEEP)) + "\n")
    rows = []
    for s, r in res["per_symbol"].items():
        cells = [s, f"{r['sigma_1m']:.3e}"]
        cells += [f"{r['per_event_usd'][t]:.2f}" for t in LEG_LAG_T_SWEEP]
        cells += [f"{r['hold_continuo_usd'][t]:.2f}" for t in LEG_LAG_T_SWEEP]
        rows.append("| " + " | ".join(cells) + " |")
    return head + "\n".join(rows) + "\n"


def run(*, now_ms: int, out_dir: str = EXEC_REALISM_OUTPUT_DIR,
        symbols: tuple = SHADOW_SYMBOLS, days: float = LEG_LAG_DAYS) -> dict:
    """One-shot U2: paginated 1m klines (hard-fail on short series), per-symbol
    sigma table over the full T sweep. Two labeled columns per T: per-event and
    sqrt(2) hold-continuo aggregate (Adrian REV2-F12). FetchFailed propagates —
    manual one-shot, crash loud and re-run."""
    from . import live_ingest
    run_ts = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat()
    per: dict = {}
    for s in symbols:
        spot = live_ingest.fetch_klines_1m_paginated(
            s, base_url=SPOT_KLINES_1M, days=days, end_ms=now_ms)
        perp = live_ingest.fetch_klines_1m_paginated(
            s, base_url=FAPI_MARK_KLINES, days=days, end_ms=now_ms)
        s1 = basis_sigma_1m(spot, perp)
        per_event = {t: scale_to_window(s1, t) * NOTIONAL for t in LEG_LAG_T_SWEEP}
        per[s] = {"sigma_1m": s1,
                  "per_event_usd": per_event,
                  "hold_continuo_usd": {t: v * math.sqrt(2.0)
                                        for t, v in per_event.items()}}
    res = {"run_ts_utc": run_ts, "version": EXEC_REALISM_VERSION,
           "days": days, "per_symbol": per}
    os.makedirs(out_dir, exist_ok=True)
    _atomic_write(os.path.join(out_dir, "leg_lag.json"), json.dumps(res, indent=2))
    _atomic_write(os.path.join(out_dir, "leg_lag.md"), _table_md(res))
    return res


if __name__ == "__main__":
    import time
    print(json.dumps(run(now_ms=int(time.time() * 1000)), indent=2))
```

Note for the test: `res["per_symbol"]["BTCUSDT"]["per_event_usd"]` keys are ints in-process; after a JSON round-trip they become strings — the test reads the in-process dict, so int keys are correct.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_funding_carry.py -k leg_lag_run -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/leg_lag.py tests/test_funding_carry.py
git commit -m "feat(exec-realism): U2 run() — descriptive sigma table artifact, no verdict"
```

---

### Task 13: Full suite + docs + real one-shot run + PR

**Files:**
- Modify: `.mex/context/setup.md` (append v0.2 section)
- Artifact: `data/retune/2026-06-04-funding-carry-exec-realism/` (committed after the real run)

- [ ] **Step 1: Run the FULL test suite**

Run: `python -m pytest tests/ -v`
Expected: all PASS (v0.1 + v0.2 + the rest of the repo). Any failure = stop and fix before proceeding.

- [ ] **Step 2: Document the one-shot in setup.md**

Append to `.mex/context/setup.md` under the funding-carry shadow section:

```markdown
### Execution-realism v0.2 (one-shot, settlement-adjacent)

Both are MANUAL one-shots (no scheduler). U1 MUST be launched within 15 minutes
AFTER a funding settlement (00:00/08:00/16:00 UTC) — it hard-refuses otherwise:

```bash
python -m tools.funding_carry.execution_cost   # U1: T_FLOOR_REAL vs live rate
python -m tools.funding_carry.leg_lag          # U2: descriptive sigma table
```

Preconditions for U1: v0.1's state.json fresh (<26h), decay_state in {ALIVE, THIN},
calibration hash matching. Output: data/retune/2026-06-04-funding-carry-exec-realism/.
ABORT/INVALID verdicts are recorded in the artifact, not raised. A v0.2 PASS is a
same-epoch snapshot — NOT by itself a go for #4 (spec §8: no joint estimator yet).
```

- [ ] **Step 3: Real one-shot run (settlement-adjacent!)**

Timing: the next settlement after implementation lands (10:00 UTC ≈ Madrid 12:00 in summer → settlements at Madrid 02:00 / 10:00 / 18:00 CEST). Launch within 15 minutes AFTER one:

Run: `python -m tools.funding_carry.execution_cost`
Expected: JSON result with `verdict` in {PASS, THIN, FAIL} (or a recorded ABORT with reason — fix the precondition and re-run at the next settlement).

Then (any time, it has no settlement constraint):

Run: `python -m tools.funding_carry.leg_lag`
Expected: JSON with per-symbol sigma table. Takes minutes (≈520 paginated requests); transient errors are retried; a hard FetchFailed = re-run.

- [ ] **Step 4: Commit the artifact**

```bash
git add data/retune/2026-06-04-funding-carry-exec-realism/
git commit -m "results(exec-realism): one-shot U1 verdict + U2 sigma table (settlement-adjacent run)"
```

- [ ] **Step 5: Commit docs + open the PR**

```bash
git add .mex/context/setup.md
git commit -m "docs(exec-realism): one-shot run instructions in setup.md"
git push -u origin feat/funding-carry-exec-realism
gh pr create --title "Funding-carry execution-realism v0.2 — same-epoch T_FLOOR_REAL verdict (one-shot)" --body "(paste both verdicts + lineage #557 -> #560 -> this; confirm NN: no holdout, no capital, no PositionClosure, data/shadow untouched)"
```

PR body must include: U1 verdict line (T_FLOOR_REAL, floor ratio vs v3, live rate CI), U2 table summary, the §8 caveat (PASS ≠ go for #4), and `mex log` the milestone.

---

## Self-Review

**Spec coverage:** §0 invariant → Tasks 8/10 (same-epoch verdict, enforced window); §3 U1 method 1-4 → Tasks 3/5/6/7; §3 preconditions → Task 8; §3 verdict + diagnostic + pooling rules → Tasks 8/9/10; §4 U2 → Tasks 4/11/12; §5 files/constants → Tasks 1-12 match the listed structure (`fetch_klines_1m_paginated` naming per §5); §6 approximations → declared in module docstrings and findings; §7 NNs → Task 10 tests (no data/shadow write, abort-not-shrink, hash hard-refuse), `_liq_ro` busy_timeout, no funding.db read anywhere; §8 caveat → findings.md text + setup.md; §9 resolved forks → frozen in Task 1. Gap check: none found.

**Placeholder scan:** clean — every code step has complete code; the only "paste" is the PR body verdicts, which cannot exist before the real run.

**Type consistency:** `walk_book` returns `{mid, qty_target, vwap, slippage_cost}` consumed by `roundtrip_real_cost` (Task 6) and `run` (Task 10, reads `legs["spot_buy"]["mid"]`); `t_floor_real(costs_usd)` takes a plain list of USD costs in Tasks 7 and 10; `read_v01_state` returns the raw state dict, `verdict(tfr, state)` reads `R_ci_lo`/`R_ci_hi` — consistent across Tasks 8/10; `FetchFailed` lives in `live_ingest`, imported indirectly in Task 10 via `live_ingest.FetchFailed`. Constants names match Task 1 across all tasks.
