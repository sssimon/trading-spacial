# Kill Switch v2 — Phase 1: Observability Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the kill switch v2 observability foundation — decision log infrastructure, 2 API endpoints, and an MVP frontend dashboard — so the operator can see what the kill switch is doing in real time. Delivers operational value for v1 immediately and unlocks v2 shadow mode in future phases.

**Architecture:** New top-level module `observability.py` with `record_decision` + `query_decisions` + `get_current_state` + `compute_portfolio_aggregate` (all pure where possible, DB-backed through `btc_api.get_db()`). New table `kill_switch_decisions` (append-only with indexes on `ts` and `(symbol, ts)`). Two read-only endpoints (`GET /kill_switch/decisions`, `GET /kill_switch/current_state`), both `verify_api_key`-gated. Scanner wired to call `record_decision` after health lookup with `engine="v1"`. MVP React component `KillSwitchDashboard.tsx` polling every 30s.

**Tech Stack:** Python 3.12, SQLite, FastAPI, React 18 + TypeScript, Vitest, pytest.

---

## File structure

```
observability.py                                         (new)
tests/test_observability.py                              (new)
btc_api.py                                               (modified: init_db + 2 endpoints)
btc_scanner.py                                           (modified: log v1 decision in scan())
tests/test_scanner.py                                    (modified: assert record_decision call)
tests/test_api.py                                        (modified: add tests for new endpoints)
frontend/src/types.ts                                    (modified: add 4 new types)
frontend/src/api.ts                                      (modified: add 2 fetch fns + type imports)
frontend/src/components/KillSwitchDashboard.tsx          (new)
frontend/src/components/KillSwitchDashboard.test.tsx     (new)
frontend/src/App.tsx                                     (modified: add Kill Switch tab)
frontend/src/App.css                                     (modified: styles for dashboard)
```

**Module responsibilities:**

- `observability.py` — pure storage + read helpers. No business logic about *what tier a symbol is in* (that's v1 health for now, v2 engines later).
- `btc_scanner.scan()` — after v1 health check, calls `observability.record_decision(engine="v1", ...)`. Fail-open try/except so observability errors don't break trading.
- `btc_api.py` — adds table to `init_db()` + 2 endpoints. Reuses existing `verify_api_key` + `Query` imports.
- `KillSwitchDashboard.tsx` — read-only view; MVP shows portfolio card + per-symbol grid. Polls every 30s. Future phases add slider + recommendation panel.

---

## Task 1: `observability` module + DB table + record/query helpers

**Files:**
- Create: `observability.py`
- Create: `tests/test_observability.py`
- Modify: `btc_api.py` (add table to `init_db()`)

- [ ] **Step 1: Write failing tests**

Create `tests/test_observability.py`:

```python
"""Tests for the kill switch decision log (Phase 1 of #187)."""
import json

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    yield db_path


def test_record_decision_inserts_row(tmp_db):
    from observability import record_decision, query_decisions
    record_decision(
        symbol="BTCUSDT",
        engine="v1",
        per_symbol_tier="NORMAL",
        portfolio_tier="NORMAL",
        size_factor=1.0,
        skip=False,
        reasons={"wr_rolling_20": 0.35},
        scan_id=None,
        slider_value=None,
        velocity_active=False,
    )
    rows = query_decisions()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["engine"] == "v1"
    assert rows[0]["per_symbol_tier"] == "NORMAL"
    assert rows[0]["size_factor"] == 1.0
    assert rows[0]["skip"] is False
    assert json.loads(rows[0]["reasons_json"]) == {"wr_rolling_20": 0.35}


def test_query_filters_by_symbol(tmp_db):
    from observability import record_decision, query_decisions
    record_decision(symbol="BTCUSDT", engine="v1", per_symbol_tier="NORMAL",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    record_decision(symbol="ETHUSDT", engine="v1", per_symbol_tier="ALERT",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    rows = query_decisions(symbol="ETHUSDT")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "ETHUSDT"


def test_query_filters_by_engine(tmp_db):
    from observability import record_decision, query_decisions
    record_decision(symbol="BTCUSDT", engine="v1", per_symbol_tier="NORMAL",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    record_decision(symbol="BTCUSDT", engine="v2_shadow", per_symbol_tier="NORMAL",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    rows = query_decisions(engine="v1")
    assert len(rows) == 1
    assert rows[0]["engine"] == "v1"


def test_query_ordered_by_ts_desc(tmp_db):
    from observability import record_decision, query_decisions
    record_decision(symbol="A", engine="v1", per_symbol_tier="NORMAL",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    record_decision(symbol="B", engine="v1", per_symbol_tier="NORMAL",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    rows = query_decisions()
    assert rows[0]["symbol"] == "B"
    assert rows[1]["symbol"] == "A"


def test_query_respects_limit(tmp_db):
    from observability import record_decision, query_decisions
    for i in range(5):
        record_decision(symbol=f"SYM{i}", engine="v1", per_symbol_tier="NORMAL",
                        portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                        reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    rows = query_decisions(limit=3)
    assert len(rows) == 3
```

- [ ] **Step 2: Run tests — confirm failure**

Run: `python -m pytest tests/test_observability.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'observability'`.

- [ ] **Step 3: Add `kill_switch_decisions` table to `btc_api.init_db()`**

Find `init_db()` in `btc_api.py` (around line 860-870 after the `tune_results` table creation). Add the new table + indexes before `con.commit()`:

```python
    con.execute("""
        CREATE TABLE IF NOT EXISTS kill_switch_decisions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              TEXT NOT NULL,
            scan_id         INTEGER,
            symbol          TEXT NOT NULL,
            engine          TEXT NOT NULL,
            per_symbol_tier TEXT NOT NULL,
            portfolio_tier  TEXT NOT NULL,
            velocity_active INTEGER DEFAULT 0,
            size_factor     REAL NOT NULL,
            skip            INTEGER NOT NULL,
            reasons_json    TEXT,
            slider_value    REAL
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_ks_decisions_ts
            ON kill_switch_decisions(ts)
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_ks_decisions_symbol_ts
            ON kill_switch_decisions(symbol, ts)
    """)
```

- [ ] **Step 4: Create `observability.py`**

Create `observability.py`:

```python
"""Observability layer for the kill switch (Phase 1 of #187).

Tracks every decision taken by v1/v2 engines in the kill_switch_decisions
table. Used by the frontend dashboard, shadow-mode validation (future
phases), and audit trails.

Append-only. Queries read by symbol/engine/time window.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    import btc_api
    return btc_api.get_db()


def record_decision(
    symbol: str,
    engine: str,                  # "v1" | "v2_shadow" | "v2_live"
    per_symbol_tier: str,          # "NORMAL" | "ALERT" | "REDUCED" | "PAUSED" | "PROBATION"
    portfolio_tier: str,           # "NORMAL" | "WARNED" | "REDUCED" | "FROZEN"
    size_factor: float,
    skip: bool,
    reasons: dict[str, Any],
    scan_id: int | None = None,
    slider_value: float | None = None,
    velocity_active: bool = False,
) -> int:
    """Insert a decision row. Returns the row id."""
    conn = _conn()
    cur = conn.execute(
        """INSERT INTO kill_switch_decisions
           (ts, scan_id, symbol, engine, per_symbol_tier, portfolio_tier,
            velocity_active, size_factor, skip, reasons_json, slider_value)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _now_iso(), scan_id, symbol, engine, per_symbol_tier, portfolio_tier,
            int(velocity_active), size_factor, int(skip),
            json.dumps(reasons, default=str), slider_value,
        ),
    )
    conn.commit()
    return cur.lastrowid


def query_decisions(
    symbol: str | None = None,
    engine: str | None = None,
    since: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Query decisions, newest first. Optional filters by symbol, engine, time."""
    conn = _conn()
    where: list[str] = []
    params: list[Any] = []
    if symbol:
        where.append("symbol = ?")
        params.append(symbol)
    if engine:
        where.append("engine = ?")
        params.append(engine)
    if since:
        where.append("ts >= ?")
        params.append(since)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    cols = ["id", "ts", "scan_id", "symbol", "engine", "per_symbol_tier",
            "portfolio_tier", "velocity_active", "size_factor", "skip",
            "reasons_json", "slider_value"]
    rows = conn.execute(
        f"""SELECT {', '.join(cols)} FROM kill_switch_decisions
           {where_sql}
           ORDER BY ts DESC
           LIMIT ?""",
        (*params, limit),
    ).fetchall()

    result = []
    for r in rows:
        d = dict(zip(cols, r))
        d["skip"] = bool(d["skip"])
        d["velocity_active"] = bool(d["velocity_active"])
        result.append(d)
    return result
```

- [ ] **Step 5: Run tests — confirm pass**

Run: `python -m pytest tests/test_observability.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add observability.py tests/test_observability.py btc_api.py
git commit -m "feat(observability): decision log table + record_decision/query_decisions (#187 phase 1)"
```

---

## Task 2: Portfolio-level aggregate state (basic)

**Files:**
- Modify: `observability.py` (add `compute_portfolio_aggregate`)
- Modify: `tests/test_observability.py`

Phase 1 scope is deliberately minimal — concurrent-failure-count only. Real aggregate DD computation lands with B2 (portfolio circuit breaker) in epic #187. This task ships a pure function the dashboard can read today; B2 will swap its internals later.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_observability.py`:

```python
def test_compute_portfolio_aggregate_all_normal():
    from observability import compute_portfolio_aggregate
    per_symbol_tiers = {"BTCUSDT": "NORMAL", "ETHUSDT": "NORMAL", "ADAUSDT": "NORMAL"}
    result = compute_portfolio_aggregate(per_symbol_tiers, concurrent_alert_threshold=3)
    assert result["tier"] == "NORMAL"
    assert result["concurrent_failures"] == 0


def test_compute_portfolio_aggregate_warned_at_threshold():
    from observability import compute_portfolio_aggregate
    per_symbol_tiers = {
        "BTCUSDT": "ALERT", "ETHUSDT": "REDUCED",
        "ADAUSDT": "PAUSED", "XLMUSDT": "NORMAL",
    }
    result = compute_portfolio_aggregate(per_symbol_tiers, concurrent_alert_threshold=3)
    assert result["tier"] == "WARNED"
    assert result["concurrent_failures"] == 3


def test_compute_portfolio_aggregate_below_threshold():
    from observability import compute_portfolio_aggregate
    per_symbol_tiers = {"BTCUSDT": "ALERT", "ETHUSDT": "NORMAL", "ADAUSDT": "NORMAL"}
    result = compute_portfolio_aggregate(per_symbol_tiers, concurrent_alert_threshold=3)
    assert result["tier"] == "NORMAL"
    assert result["concurrent_failures"] == 1


def test_compute_portfolio_aggregate_empty_input():
    from observability import compute_portfolio_aggregate
    result = compute_portfolio_aggregate({}, concurrent_alert_threshold=3)
    assert result["tier"] == "NORMAL"
    assert result["concurrent_failures"] == 0
```

- [ ] **Step 2: Run tests — confirm failure**

Run: `python -m pytest tests/test_observability.py -v -k "portfolio_aggregate"`
Expected: FAIL with `AttributeError: module 'observability' has no attribute 'compute_portfolio_aggregate'`.

- [ ] **Step 3: Implement `compute_portfolio_aggregate`**

Append to `observability.py`:

```python
PORTFOLIO_FAILURE_TIERS = {"ALERT", "REDUCED", "PAUSED", "PROBATION"}


def compute_portfolio_aggregate(
    per_symbol_tiers: dict[str, str],
    concurrent_alert_threshold: int = 3,
) -> dict[str, Any]:
    """Compute the portfolio-level aggregate state from per-symbol tiers.

    Phase 1 scope: concurrent-failure-count only. Real aggregate DD
    computation (REDUCED/FROZEN thresholds) lands with B2 (portfolio
    circuit breaker) in epic #187.

    Returns {"tier": "NORMAL" | "WARNED", "concurrent_failures": int}.
    """
    failures = sum(
        1 for t in per_symbol_tiers.values() if t in PORTFOLIO_FAILURE_TIERS
    )
    tier = "WARNED" if failures >= concurrent_alert_threshold else "NORMAL"
    return {"tier": tier, "concurrent_failures": failures}
```

- [ ] **Step 4: Run tests — confirm pass**

Run: `python -m pytest tests/test_observability.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add observability.py tests/test_observability.py
git commit -m "feat(observability): compute_portfolio_aggregate for phase 1 (#187)"
```

---

## Task 3: Wire v1 scan decisions to the decision log

**Files:**
- Modify: `btc_scanner.py` (inside `scan()`, after health-state lookup)
- Modify: `tests/test_scanner.py` (add test class)

- [ ] **Step 1: Write failing test**

Append to `tests/test_scanner.py` (add at end of file, new test class):

```python
class TestScanWritesToDecisionLog:
    def test_scan_records_v1_decision(self, tmp_path, monkeypatch):
        """scan() writes a row to kill_switch_decisions with engine='v1'."""
        import btc_api, btc_scanner, observability
        db_path = str(tmp_path / "signals.db")
        monkeypatch.setattr(btc_api, "DB_FILE", db_path)
        if hasattr(btc_api, "_db_conn"):
            delattr(btc_api, "_db_conn")
        btc_api.init_db()

        # Simplest possible scan path: mock health to return NORMAL, mock
        # market data fetches to return empty DataFrames — scan() likely
        # errors out computing indicators but the log entry should still
        # be recorded (fail-open after the health step).
        import pandas as pd
        monkeypatch.setattr(btc_scanner.md, "get_klines", lambda *a, **k: pd.DataFrame())
        monkeypatch.setattr(btc_scanner, "get_fear_greed_index_latest", lambda: 50)
        monkeypatch.setattr(btc_scanner, "get_binance_funding_rate", lambda s: 0.0)

        try:
            btc_scanner.scan("BTCUSDT")
        except Exception:
            # Scan may throw on empty dataframes — that's fine for this test.
            # We're only asserting the side effect of the decision log write
            # that happens BEFORE the indicator-heavy path.
            pass

        rows = observability.query_decisions(symbol="BTCUSDT")
        assert len(rows) >= 1
        assert rows[0]["engine"] == "v1"
        assert rows[0]["per_symbol_tier"] in (
            "NORMAL", "ALERT", "REDUCED", "PAUSED", "PROBATION",
        )
```

Note on test design: the implementer may need to adjust the mocks based on the exact shape of `scan()` at head of branch. The `try/except` around `scan()` intentionally swallows indicator errors — the assertion is about the log write, which the plan places early in the function (after health-state lookup, before price fetches). If implementer finds a cleaner test path (e.g. by calling a helper that only does the decision-logging portion), they should do so.

- [ ] **Step 2: Run test — confirm failure**

Run: `python -m pytest tests/test_scanner.py::TestScanWritesToDecisionLog -v`
Expected: FAIL — no row recorded.

- [ ] **Step 3: Add decision logging to `btc_scanner.scan()`**

Find the block in `scan()` where `_health_state` is computed (around line 1027-1035 per the current code, where `get_symbol_state(symbol)` is called). **Immediately after** that lookup (while `_health_state` is defined and BEFORE any early returns), add the logging:

```python
    # Observability (#187 phase 1): log the v1 decision so the dashboard
    # can visualize + so future shadow mode can compare v2 vs v1 side-by-side.
    try:
        import observability
        _v1_size_factor = {
            "NORMAL": 1.0, "ALERT": 1.0, "REDUCED": 0.5,
            "PAUSED": 0.0, "PROBATION": 0.5,
        }.get(_health_state, 1.0)
        _v1_skip = (_health_state == "PAUSED")
        observability.record_decision(
            symbol=symbol,
            engine="v1",
            per_symbol_tier=_health_state,
            portfolio_tier="NORMAL",          # phase 1: hardcoded; B2 computes real aggregate
            size_factor=_v1_size_factor,
            skip=_v1_skip,
            reasons={"health_state": _health_state},
            scan_id=None,
            slider_value=None,
            velocity_active=False,
        )
    except Exception as _obs_err:
        log.warning("observability.record_decision failed for %s: %s", symbol, _obs_err)
```

Fail-open: if observability crashes, log warning but don't break scanner. Pattern matches the existing kill switch try/except (`push_telegram_direct: health lookup failed` nearby).

- [ ] **Step 4: Run test — confirm pass**

Run: `python -m pytest tests/test_scanner.py::TestScanWritesToDecisionLog -v`
Expected: PASS.

- [ ] **Step 5: Run full scanner suite**

Run: `python -m pytest tests/test_scanner.py -v`
Expected: all scanner tests still pass.

- [ ] **Step 6: Commit**

```bash
git add btc_scanner.py tests/test_scanner.py
git commit -m "feat(scanner): log v1 decisions to kill_switch_decisions (#187 phase 1)"
```

---

## Task 4: `GET /kill_switch/decisions` endpoint

**Files:**
- Modify: `btc_api.py` (add endpoint)
- Modify: `tests/test_api.py` (add test class)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_api.py` (new test class):

```python
class TestKillSwitchDecisionsEndpoint:
    def test_returns_empty_when_no_decisions(self, client, tmp_db):
        """GET /kill_switch/decisions returns [] when no decisions recorded."""
        r = client.get("/kill_switch/decisions")
        assert r.status_code == 200
        assert r.json()["decisions"] == []

    def test_returns_recorded_decisions(self, client, tmp_db):
        """GET /kill_switch/decisions returns what was recorded."""
        import observability
        observability.record_decision(
            symbol="BTCUSDT", engine="v1", per_symbol_tier="NORMAL",
            portfolio_tier="NORMAL", size_factor=1.0, skip=False,
            reasons={"x": 1}, scan_id=None, slider_value=None,
            velocity_active=False,
        )
        r = client.get("/kill_switch/decisions")
        assert r.status_code == 200
        body = r.json()
        assert len(body["decisions"]) == 1
        assert body["decisions"][0]["symbol"] == "BTCUSDT"
        assert body["decisions"][0]["engine"] == "v1"

    def test_filters_by_symbol(self, client, tmp_db):
        import observability
        observability.record_decision(symbol="BTCUSDT", engine="v1",
                                      per_symbol_tier="NORMAL", portfolio_tier="NORMAL",
                                      size_factor=1.0, skip=False, reasons={},
                                      scan_id=None, slider_value=None, velocity_active=False)
        observability.record_decision(symbol="ETHUSDT", engine="v1",
                                      per_symbol_tier="ALERT", portfolio_tier="NORMAL",
                                      size_factor=1.0, skip=False, reasons={},
                                      scan_id=None, slider_value=None, velocity_active=False)
        r = client.get("/kill_switch/decisions?symbol=ETHUSDT")
        assert r.status_code == 200
        assert len(r.json()["decisions"]) == 1
        assert r.json()["decisions"][0]["symbol"] == "ETHUSDT"

    def test_respects_limit_query(self, client, tmp_db):
        import observability
        for i in range(5):
            observability.record_decision(
                symbol=f"S{i}", engine="v1",
                per_symbol_tier="NORMAL", portfolio_tier="NORMAL",
                size_factor=1.0, skip=False, reasons={},
                scan_id=None, slider_value=None, velocity_active=False,
            )
        r = client.get("/kill_switch/decisions?limit=2")
        assert r.status_code == 200
        assert len(r.json()["decisions"]) == 2

    def test_rejects_limit_over_max(self, client, tmp_db):
        r = client.get("/kill_switch/decisions?limit=500")
        assert r.status_code == 422  # pydantic Query validation
```

(Uses existing `client` and `tmp_db` fixtures from `test_api.py`. Confirm those fixtures exist — they are used by the notifications endpoint tests added in PR #170.)

- [ ] **Step 2: Run tests — confirm failure**

Run: `python -m pytest tests/test_api.py::TestKillSwitchDecisionsEndpoint -v`
Expected: 404 (endpoint not yet defined).

- [ ] **Step 3: Add endpoint to `btc_api.py`**

Find the notifications endpoints added in PR #170 (around line 2050-2100; search for `/notifications/read-all` or `list_unread`). Add immediately after them:

```python
# ─────────────────────────────────────────────────────────────────────────────
#  KILL SWITCH OBSERVABILITY ENDPOINTS (#187 phase 1)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/kill_switch/decisions", dependencies=[Depends(verify_api_key)])
def get_kill_switch_decisions(
    symbol: Optional[str] = None,
    engine: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
):
    """Kill switch decision log (#187 phase 1). Filter by symbol/engine/since ts."""
    import observability
    rows = observability.query_decisions(
        symbol=symbol, engine=engine, since=since, limit=limit,
    )
    return {"decisions": rows}
```

Confirm `Query`, `Optional`, `Depends`, `verify_api_key` are already imported at top of `btc_api.py` — they are (used by the notifications endpoints).

- [ ] **Step 4: Run tests — confirm pass**

Run: `python -m pytest tests/test_api.py::TestKillSwitchDecisionsEndpoint -v`
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add btc_api.py tests/test_api.py
git commit -m "feat(api): GET /kill_switch/decisions endpoint (#187 phase 1)"
```

---

## Task 5: `get_current_state` helper + `GET /kill_switch/current_state` endpoint

**Files:**
- Modify: `observability.py` (add `get_current_state`)
- Modify: `btc_api.py` (add endpoint)
- Modify: `tests/test_observability.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing test for `get_current_state`**

Append to `tests/test_observability.py`:

```python
def test_get_current_state_returns_latest_per_symbol(tmp_db):
    from observability import record_decision, get_current_state
    # Record two decisions for the same symbol; newer should win
    record_decision(symbol="BTCUSDT", engine="v1", per_symbol_tier="NORMAL",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    record_decision(symbol="BTCUSDT", engine="v1", per_symbol_tier="ALERT",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    record_decision(symbol="ETHUSDT", engine="v1", per_symbol_tier="REDUCED",
                    portfolio_tier="NORMAL", size_factor=0.5, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)

    state = get_current_state()
    assert state["symbols"]["BTCUSDT"]["per_symbol_tier"] == "ALERT"
    assert state["symbols"]["ETHUSDT"]["per_symbol_tier"] == "REDUCED"
    assert state["portfolio"]["tier"] == "NORMAL"
    # ALERT + REDUCED = 2 concurrent failures (< default threshold of 3)
    assert state["portfolio"]["concurrent_failures"] == 2


def test_get_current_state_empty_db(tmp_db):
    from observability import get_current_state
    state = get_current_state()
    assert state["symbols"] == {}
    assert state["portfolio"]["tier"] == "NORMAL"
    assert state["portfolio"]["concurrent_failures"] == 0


def test_get_current_state_filters_by_engine(tmp_db):
    from observability import record_decision, get_current_state
    record_decision(symbol="BTCUSDT", engine="v1", per_symbol_tier="NORMAL",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    record_decision(symbol="BTCUSDT", engine="v2_shadow", per_symbol_tier="ALERT",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)

    state_v1 = get_current_state(engine="v1")
    state_v2 = get_current_state(engine="v2_shadow")
    assert state_v1["symbols"]["BTCUSDT"]["per_symbol_tier"] == "NORMAL"
    assert state_v2["symbols"]["BTCUSDT"]["per_symbol_tier"] == "ALERT"
```

- [ ] **Step 2: Run tests — confirm failure**

Run: `python -m pytest tests/test_observability.py -v -k "current_state"`
Expected: FAIL with `AttributeError: module 'observability' has no attribute 'get_current_state'`.

- [ ] **Step 3: Implement `get_current_state`**

Append to `observability.py`:

```python
def get_current_state(
    engine: str = "v1",
    concurrent_alert_threshold: int = 3,
) -> dict[str, Any]:
    """Return current per-symbol state + portfolio aggregate.

    Takes the latest decision per symbol from the log (filtered to the
    given engine) and computes portfolio aggregate.
    """
    conn = _conn()
    rows = conn.execute(
        """SELECT d.symbol, d.per_symbol_tier, d.portfolio_tier, d.size_factor,
                  d.skip, d.velocity_active, d.ts, d.reasons_json
           FROM kill_switch_decisions d
           INNER JOIN (
               SELECT symbol, MAX(ts) AS max_ts
               FROM kill_switch_decisions
               WHERE engine = ?
               GROUP BY symbol
           ) latest
             ON d.symbol = latest.symbol AND d.ts = latest.max_ts
           WHERE d.engine = ?""",
        (engine, engine),
    ).fetchall()

    cols = ["symbol", "per_symbol_tier", "portfolio_tier", "size_factor",
            "skip", "velocity_active", "ts", "reasons_json"]
    symbols: dict[str, dict[str, Any]] = {}
    per_symbol_tiers: dict[str, str] = {}
    for r in rows:
        d = dict(zip(cols, r))
        d["skip"] = bool(d["skip"])
        d["velocity_active"] = bool(d["velocity_active"])
        symbols[d["symbol"]] = d
        per_symbol_tiers[d["symbol"]] = d["per_symbol_tier"]

    portfolio = compute_portfolio_aggregate(
        per_symbol_tiers,
        concurrent_alert_threshold=concurrent_alert_threshold,
    )
    return {"symbols": symbols, "portfolio": portfolio}
```

- [ ] **Step 4: Run tests — confirm pass**

Run: `python -m pytest tests/test_observability.py -v -k "current_state"`
Expected: 3 tests PASS.

- [ ] **Step 5: Write failing test for the endpoint**

Append to `tests/test_api.py`:

```python
class TestKillSwitchCurrentStateEndpoint:
    def test_returns_empty_state(self, client, tmp_db):
        r = client.get("/kill_switch/current_state")
        assert r.status_code == 200
        body = r.json()
        assert body["symbols"] == {}
        assert body["portfolio"]["tier"] == "NORMAL"
        assert body["portfolio"]["concurrent_failures"] == 0

    def test_returns_latest_per_symbol(self, client, tmp_db):
        import observability
        observability.record_decision(
            symbol="BTCUSDT", engine="v1",
            per_symbol_tier="ALERT", portfolio_tier="NORMAL",
            size_factor=1.0, skip=False, reasons={},
            scan_id=None, slider_value=None, velocity_active=False,
        )
        r = client.get("/kill_switch/current_state")
        assert r.status_code == 200
        body = r.json()
        assert body["symbols"]["BTCUSDT"]["per_symbol_tier"] == "ALERT"
```

- [ ] **Step 6: Add endpoint to `btc_api.py`**

Immediately after the `/kill_switch/decisions` endpoint (added in Task 4):

```python
@app.get("/kill_switch/current_state", dependencies=[Depends(verify_api_key)])
def get_kill_switch_current_state(engine: str = "v1"):
    """Current tier state per symbol + portfolio aggregate (#187 phase 1)."""
    import observability
    return observability.get_current_state(engine=engine)
```

- [ ] **Step 7: Run endpoint tests**

Run: `python -m pytest tests/test_api.py::TestKillSwitchCurrentStateEndpoint -v`
Expected: 2 tests PASS.

- [ ] **Step 8: Commit**

```bash
git add observability.py btc_api.py tests/test_observability.py tests/test_api.py
git commit -m "feat(api): GET /kill_switch/current_state + get_current_state helper (#187 phase 1)"
```

---

## Task 6: Frontend API client + types

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: Add types**

Append to `frontend/src/types.ts`:

```typescript
// ─── Kill switch observability (#187 phase 1) ────────────────────────

export type KillSwitchEngine = 'v1' | 'v2_shadow' | 'v2_live';
export type KillSwitchPerSymbolTier =
  | 'NORMAL' | 'ALERT' | 'REDUCED' | 'PAUSED' | 'PROBATION';
export type KillSwitchPortfolioTier =
  | 'NORMAL' | 'WARNED' | 'REDUCED' | 'FROZEN';

export interface KillSwitchDecision {
  id: number;
  ts: string;
  scan_id: number | null;
  symbol: string;
  engine: KillSwitchEngine;
  per_symbol_tier: KillSwitchPerSymbolTier;
  portfolio_tier: KillSwitchPortfolioTier;
  velocity_active: boolean;
  size_factor: number;
  skip: boolean;
  reasons_json: string;
  slider_value: number | null;
}

export interface KillSwitchDecisionsResponse {
  decisions: KillSwitchDecision[];
}

export interface KillSwitchSymbolState {
  symbol: string;
  per_symbol_tier: KillSwitchPerSymbolTier;
  portfolio_tier: KillSwitchPortfolioTier;
  size_factor: number;
  skip: boolean;
  velocity_active: boolean;
  ts: string;
  reasons_json: string;
}

export interface KillSwitchPortfolioState {
  tier: KillSwitchPortfolioTier;
  concurrent_failures: number;
}

export interface KillSwitchCurrentStateResponse {
  symbols: { [symbol: string]: KillSwitchSymbolState };
  portfolio: KillSwitchPortfolioState;
}
```

- [ ] **Step 2: Add fetch functions to `frontend/src/api.ts`**

First, extend the imports at the top of `api.ts`:

```typescript
import type {
  // ... existing imports ...
  KillSwitchDecisionsResponse,
  KillSwitchCurrentStateResponse,
  KillSwitchEngine,
} from './types';
```

Append at the end of `api.ts`:

```typescript
// ---- Kill switch observability (#187 phase 1) ---------------------------

export async function getKillSwitchDecisions(
  opts: {
    symbol?: string;
    engine?: KillSwitchEngine;
    since?: string;
    limit?: number;
  } = {},
): Promise<KillSwitchDecisionsResponse> {
  const params = new URLSearchParams();
  if (opts.symbol) params.set('symbol', opts.symbol);
  if (opts.engine) params.set('engine', opts.engine);
  if (opts.since) params.set('since', opts.since);
  if (opts.limit !== undefined) params.set('limit', String(opts.limit));
  const qs = params.toString();
  return request<KillSwitchDecisionsResponse>(
    `/kill_switch/decisions${qs ? `?${qs}` : ''}`,
  );
}

export async function getKillSwitchCurrentState(
  engine: KillSwitchEngine = 'v1',
): Promise<KillSwitchCurrentStateResponse> {
  return request<KillSwitchCurrentStateResponse>(
    `/kill_switch/current_state?engine=${engine}`,
  );
}
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: clean (no errors).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts
git commit -m "feat(frontend): kill switch API client + types (#187 phase 1)"
```

---

## Task 7: `KillSwitchDashboard` component MVP

**Files:**
- Create: `frontend/src/components/KillSwitchDashboard.tsx`
- Create: `frontend/src/components/KillSwitchDashboard.test.tsx`
- Modify: `frontend/src/App.css`

- [ ] **Step 1: Write failing tests**

Create `frontend/src/components/KillSwitchDashboard.test.tsx`:

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import KillSwitchDashboard from './KillSwitchDashboard';

vi.mock('../api', () => ({
  getKillSwitchCurrentState: vi.fn(),
}));

import { getKillSwitchCurrentState } from '../api';

describe('KillSwitchDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows portfolio tier NORMAL when no symbols reported', async () => {
    (getKillSwitchCurrentState as ReturnType<typeof vi.fn>).mockResolvedValue({
      symbols: {},
      portfolio: { tier: 'NORMAL', concurrent_failures: 0 },
    });
    render(<KillSwitchDashboard />);
    await waitFor(() => {
      expect(getKillSwitchCurrentState).toHaveBeenCalled();
    });
    expect(screen.getByText(/Portfolio/i)).toBeInTheDocument();
    expect(screen.getByText('NORMAL')).toBeInTheDocument();
  });

  it('renders per-symbol tier cards for each symbol', async () => {
    (getKillSwitchCurrentState as ReturnType<typeof vi.fn>).mockResolvedValue({
      symbols: {
        BTCUSDT: {
          symbol: 'BTCUSDT', per_symbol_tier: 'NORMAL', portfolio_tier: 'NORMAL',
          size_factor: 1.0, skip: false, velocity_active: false,
          ts: '2026-04-23T12:00:00Z', reasons_json: '{}',
        },
        ETHUSDT: {
          symbol: 'ETHUSDT', per_symbol_tier: 'ALERT', portfolio_tier: 'NORMAL',
          size_factor: 1.0, skip: false, velocity_active: false,
          ts: '2026-04-23T12:00:00Z', reasons_json: '{}',
        },
      },
      portfolio: { tier: 'NORMAL', concurrent_failures: 1 },
    });
    render(<KillSwitchDashboard />);
    await waitFor(() => {
      expect(screen.getByText('BTCUSDT')).toBeInTheDocument();
      expect(screen.getByText('ETHUSDT')).toBeInTheDocument();
    });
  });

  it('shows portfolio WARNED when threshold reached', async () => {
    (getKillSwitchCurrentState as ReturnType<typeof vi.fn>).mockResolvedValue({
      symbols: {},
      portfolio: { tier: 'WARNED', concurrent_failures: 3 },
    });
    render(<KillSwitchDashboard />);
    await waitFor(() => {
      expect(screen.getByText('WARNED')).toBeInTheDocument();
    });
  });

  it('survives API failure without crashing', async () => {
    (getKillSwitchCurrentState as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('network'),
    );
    const { container } = render(<KillSwitchDashboard />);
    await waitFor(() => {
      expect(getKillSwitchCurrentState).toHaveBeenCalled();
    });
    // component still mounted
    expect(container.querySelector('.ks-dashboard')).not.toBeNull();
  });
});
```

- [ ] **Step 2: Run tests — confirm failure**

Run: `cd frontend && npm test -- --run KillSwitchDashboard`
Expected: FAIL — component file doesn't exist.

- [ ] **Step 3: Implement component**

Create `frontend/src/components/KillSwitchDashboard.tsx`:

```typescript
// ============================================================
// KillSwitchDashboard.tsx — Phase 1 MVP of kill switch v2 (#187)
// Shows per-symbol tier grid + portfolio aggregate state.
// Polls /kill_switch/current_state every 30s.
// ============================================================

import React, { useEffect, useState } from 'react';
import { getKillSwitchCurrentState } from '../api';
import type {
  KillSwitchCurrentStateResponse,
  KillSwitchPerSymbolTier,
  KillSwitchPortfolioTier,
} from '../types';

const POLL_INTERVAL_MS = 30_000;

const TIER_COLORS_PER_SYMBOL: Record<KillSwitchPerSymbolTier, string> = {
  NORMAL: '#22c55e',
  ALERT: '#f59e0b',
  REDUCED: '#fb923c',
  PAUSED: '#ef4444',
  PROBATION: '#a78bfa',
};

const TIER_COLORS_PORTFOLIO: Record<KillSwitchPortfolioTier, string> = {
  NORMAL: '#22c55e',
  WARNED: '#f59e0b',
  REDUCED: '#fb923c',
  FROZEN: '#ef4444',
};

const KillSwitchDashboard: React.FC = () => {
  const [state, setState] = useState<KillSwitchCurrentStateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const fetchState = async () => {
      try {
        const resp = await getKillSwitchCurrentState('v1');
        if (!alive) return;
        setState(resp);
        setError(null);
      } catch (err) {
        if (!alive) return;
        setError(err instanceof Error ? err.message : 'Error');
      }
    };
    fetchState();
    const id = setInterval(fetchState, POLL_INTERVAL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const symbols = state ? Object.values(state.symbols) : [];
  const portfolio = state?.portfolio ?? { tier: 'NORMAL' as const, concurrent_failures: 0 };

  return (
    <div className="ks-dashboard">
      {error && (
        <div className="ks-error">Error cargando kill switch: {error}</div>
      )}

      <div className="ks-portfolio-card">
        <div className="ks-portfolio-label">Portfolio</div>
        <div
          className="ks-portfolio-tier"
          style={{ color: TIER_COLORS_PORTFOLIO[portfolio.tier] }}
        >
          {portfolio.tier}
        </div>
        <div className="ks-portfolio-meta">
          {portfolio.concurrent_failures} símbolo(s) en ALERT/REDUCED/PAUSED
        </div>
      </div>

      <div className="ks-symbol-grid">
        {symbols.map((s) => (
          <div key={s.symbol} className="ks-symbol-card">
            <div className="ks-symbol-name">{s.symbol}</div>
            <div
              className="ks-symbol-tier"
              style={{ color: TIER_COLORS_PER_SYMBOL[s.per_symbol_tier] }}
            >
              {s.per_symbol_tier}
            </div>
            <div className="ks-symbol-meta">
              size × {s.size_factor.toFixed(2)} · {s.skip ? 'skip' : 'operating'}
            </div>
            <div className="ks-symbol-ts">
              {new Date(s.ts).toLocaleString('es-ES')}
            </div>
          </div>
        ))}
        {symbols.length === 0 && (
          <div className="ks-empty">Sin datos aún — esperando scans.</div>
        )}
      </div>
    </div>
  );
};

export default KillSwitchDashboard;
```

- [ ] **Step 4: Add CSS**

Append to `frontend/src/App.css`:

```css
/* KillSwitchDashboard (#187 phase 1) */
.ks-dashboard {
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.ks-portfolio-card {
  background: var(--bg-secondary, #1c1d25);
  border: 1px solid var(--border, #2b2d38);
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.ks-portfolio-label {
  font-size: 0.8rem;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-secondary, #a8aab7);
}

.ks-portfolio-tier {
  font-size: 1.4rem;
  font-weight: 600;
}

.ks-portfolio-meta {
  font-size: 0.85rem;
  color: var(--text-secondary, #a8aab7);
}

.ks-symbol-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
}

.ks-symbol-card {
  background: var(--bg-secondary, #1c1d25);
  border: 1px solid var(--border, #2b2d38);
  border-radius: 6px;
  padding: 12px;
}

.ks-symbol-name {
  font-weight: 600;
  margin-bottom: 4px;
}

.ks-symbol-tier {
  font-size: 1.1rem;
  font-weight: 600;
  margin-bottom: 4px;
}

.ks-symbol-meta {
  font-size: 0.8rem;
  color: var(--text-secondary, #a8aab7);
}

.ks-symbol-ts {
  font-size: 0.7rem;
  color: var(--text-secondary, #a8aab7);
  margin-top: 4px;
}

.ks-empty {
  grid-column: 1 / -1;
  text-align: center;
  padding: 32px;
  color: var(--text-secondary, #a8aab7);
}

.ks-error {
  padding: 12px;
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.3);
  border-radius: 4px;
  color: #ef4444;
}
```

- [ ] **Step 5: Run tests — confirm pass**

Run: `cd frontend && npm test`
Expected: 4 KillSwitchDashboard tests PASS, all existing 17 tests still pass.

- [ ] **Step 6: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/KillSwitchDashboard.tsx \
        frontend/src/components/KillSwitchDashboard.test.tsx \
        frontend/src/App.css
git commit -m "feat(frontend): KillSwitchDashboard MVP component (#187 phase 1)"
```

---

## Task 8: Integrate dashboard into `App.tsx`

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Extend `MainTab` type and add import**

In `App.tsx`, find the type definition:
```typescript
type MainTab = 'mercado' | 'posiciones';
```

Change to:
```typescript
type MainTab = 'mercado' | 'posiciones' | 'kill-switch';
```

Add to existing imports:
```typescript
import KillSwitchDashboard from './components/KillSwitchDashboard';
```

- [ ] **Step 2: Add tab button in the main tab bar**

Find the `<div className="main-tab-bar">` block. After the "Posiciones" button, add:

```tsx
<button
  className={`main-tab${mainTab === 'kill-switch' ? ' main-tab--active' : ''}`}
  onClick={() => setMainTab('kill-switch')}
>
  Kill Switch
</button>
```

- [ ] **Step 3: Add the dashboard section**

After the `{mainTab === 'posiciones' && (...)}` block, add:

```tsx
{/* ── Kill Switch tab ─────────────────────────────────── */}
{mainTab === 'kill-switch' && (
  <ErrorBoundary fallbackLabel="Error en dashboard de kill switch">
    <KillSwitchDashboard />
  </ErrorBoundary>
)}
```

- [ ] **Step 4: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: clean.

- [ ] **Step 5: Run frontend tests**

Run: `cd frontend && npm test`
Expected: all tests pass (17 baseline + 4 new from Task 7 = 21 total).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(frontend): add Kill Switch tab to main app (#187 phase 1)"
```

---

## Task 9: Full regression, push, and open PR

- [ ] **Step 1: Full Python test suite**

Run: `python -m pytest tests/ -q -m "not network"`
Expected: all pass. Count: ≥ 607 baseline + ~13 new (5 Task 1 + 4 Task 2 + 1 Task 3 + 5 Task 4 + 3 Task 5 + 2 Task 5 endpoint = 20) ≈ 627 tests.

- [ ] **Step 2: Full frontend test suite**

Run: `cd frontend && npm test && cd ..`
Expected: all tests pass (17 baseline + 4 new = 21 tests).

- [ ] **Step 3: Frontend build check**

Run: `cd frontend && npm run build && cd ..`
Expected: clean build (no TypeScript errors, no build errors).

- [ ] **Step 4: Push branch**

```bash
git push -u origin feat/kill-switch-observability-foundation
```

- [ ] **Step 5: Open PR**

```bash
gh pr create --base main --head feat/kill-switch-observability-foundation \
  --title "feat(observability): kill switch decision log + MVP dashboard (#187 phase 1)" \
  --body "$(cat <<'BODY'
## Summary

Phase 1 of the kill switch v2 (epic #187) — the observability foundation. Delivers decision log infrastructure, 2 read-only API endpoints, and an MVP frontend dashboard. Ships operational value for v1 immediately (Simon sees tier per symbol + portfolio summary in real time) and unlocks v2 shadow mode in future phases (the `engine` column accepts `v1` | `v2_shadow` | `v2_live`).

## Ships

- `observability.py` module — `record_decision`, `query_decisions`, `get_current_state`, `compute_portfolio_aggregate`.
- `kill_switch_decisions` table in `signals.db` (append-only, indexed on `ts` and `(symbol, ts)`).
- `btc_scanner.scan()` now logs every v1 decision with fail-open try/except.
- `GET /kill_switch/decisions?symbol=&engine=&since=&limit=` — filter and paginate the log. Auth-gated.
- `GET /kill_switch/current_state?engine=v1` — latest per-symbol + portfolio aggregate.
- Frontend `KillSwitchDashboard.tsx` — polling every 30s, portfolio card + per-symbol grid with tier-colored labels.
- New `Kill Switch` tab in `App.tsx`.

## Intentionally NOT shipped (per design, deferred to later phases)

- Real portfolio aggregate DD computation — lands with B2 (portfolio circuit breaker, issue #196). Phase 1 shows concurrent-failure-count only.
- v2 decision engine (shadow or live) — later Phase 2 features.
- Slider / advanced config panel — arrives with auto-calibrator phase.
- `GET /kill_switch/recommendations` — lands with auto-calibrator (recommendations table doesn't exist in phase 1).

## Test plan

- [x] Python: 607 baseline + ~20 new (observability + scanner + api endpoint tests) = ~627 passing.
- [x] Frontend: 17 baseline + 4 new KillSwitchDashboard tests = 21 passing.
- [x] `tsc --noEmit` clean.
- [x] Frontend build clean.
- [ ] Manual smoke: load `/kill_switch` tab on dev server, trigger a manual scan via `/scan?symbol=BTCUSDT`, confirm row appears in the table and in the dashboard within 30s.

## Addresses

Part of #200 (B6 dashboard — MVP; richer dashboard comes when v2 engines exist to visualize).

## References

- Epic #187 — kill switch v2
- Design spec: \`docs/superpowers/specs/es/2026-04-23-kill-switch-v2-design.md\` §6.1
- Plan: \`docs/superpowers/plans/2026-04-23-kill-switch-v2-phase1-observability.md\`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
)"
```

- [ ] **Step 6: Watch CI**

Run: `sleep 12 && gh pr checks --watch --interval 15`
Expected: `backend-tests` PASS + `frontend-typecheck` PASS.

- [ ] **Step 7: Report to user**

Report PR URL + CI verdict. Do NOT merge automatically — wait for user explicit approval.

---

## Self-review

**Spec coverage (§6.1 of design spec):**

- ✅ Decision log table with exact schema — Task 1 creates the table with all 12 columns named in the spec plus 2 indexes (also named in the spec).
- ✅ Endpoints: `GET /kill_switch/decisions` (Task 4), `GET /kill_switch/current_state` (Task 5). `GET /kill_switch/recommendations` intentionally NOT in phase 1 because the recommendations table doesn't exist until the auto-calibrator daemon ships (future phase). The PR body is explicit about this deferral.
- ✅ Frontend `KillSwitchDashboard.tsx` MVP — Task 7. Phase-1 scope: portfolio card + per-symbol grid. Polling every 30s matches §6.1 bullet 3.
- ✅ Wire v1 decisions to log — Task 3 adds the log call inside `btc_scanner.scan()` after health-state lookup, fail-open pattern matches rest of kill-switch-v1 wiring.
- ✅ Dashboard visualizes v1 decisions without v2 existing — explicit in design spec "En esta fase el dashboard muestra v1 decisions" and implemented that way in Task 7 (hard-coded engine='v1' in the fetch).

**Placeholder scan:** searched for `TBD|TODO|FIXME|similar to|fill in|placeholder` — none present. Every step has either actual code or a concrete command with expected output.

**Type consistency:**
- `record_decision(symbol, engine, per_symbol_tier, portfolio_tier, size_factor, skip, reasons, scan_id, slider_value, velocity_active)` — same signature used in Tasks 1, 2, 3, 4, 5, 7. All keyword-only calls.
- `query_decisions(symbol, engine, since, limit)` — same in Tasks 1, 4.
- `get_current_state(engine, concurrent_alert_threshold)` — Task 5 defines, called from Task 5 endpoint and Task 6 frontend (JSON shape matches TS types).
- `compute_portfolio_aggregate(per_symbol_tiers, concurrent_alert_threshold)` — Task 2 defines, called from `get_current_state` in Task 5.
- TypeScript types (`KillSwitchPerSymbolTier`, `KillSwitchPortfolioTier`) use string literal unions that match the Python-emitted values exactly.

**Scope:** Phase 1 only. No bleeding into B1-B6 features. Portfolio aggregate is explicitly limited to concurrent-failure-count with a code comment pointing to B2 for the real implementation.

**No broken references:** no task references types, functions, or modules not defined in an earlier task.

Plan is ready for execution.
