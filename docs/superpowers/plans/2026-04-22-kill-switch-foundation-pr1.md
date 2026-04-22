# Kill switch Foundation (#138 PR 1) implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the observer-only infrastructure for per-symbol health monitoring — rolling metrics, state machine (NORMAL/ALERT/REDUCED/PAUSED), persistence, API endpoints, and a daily cron + on-position-close evaluation loop — without changing any trading behavior. Downstream PRs 2-4 will add the tier actions (alert message, size reduction, skip signals).

**Architecture:** New `health.py` module with pure functions for metrics + state evaluation, thin persistence wrappers over two new `signals.db` tables (`symbol_health`, `symbol_health_events`). Evaluation runs from a thread inside `btc_api.py` that fires on a daily cron at 00:00 UTC and after every position close. API exposes read endpoints and a manual-reactivate POST. No consumer reads `get_symbol_state()` yet — that's PRs 2-4.

**Tech Stack:** Python 3.12, SQLite (existing `signals.db`), FastAPI (existing), pytest, freezegun (already in deps via pytest ecosystem — verify in Task 1). Uses `notifier.notify(HealthEvent(...))` from PR #164 for state-transition Telegram messages.

---

## File structure

```
health.py                                (new module, ~250 LOC)

tests/
├── test_health_rolling_metrics.py       (compute_rolling_metrics)
├── test_health_state_machine.py         (evaluate_state transitions)
├── test_health_persistence.py           (apply_transition, get_symbol_state)
├── test_health_integration.py           (end-to-end: positions → evaluate → events)
└── test_health_endpoints.py             (GET /health/symbols, GET /health/events, POST /reactivate)

btc_api.py                               (modified)
  - init_db()              — +symbol_health + symbol_health_events tables
  - scanner_loop area      — spawn health_monitor_loop thread alongside
  - NEW endpoints          — GET /health/symbols, GET /health/events, POST /health/reactivate/{symbol}
  - db_close_position()    — trigger health evaluation after status=closed

config-defaults              — add kill_switch block (in CFG_DEFAULTS)
```

**health.py responsibilities (by function):**

| Function | Kind | Purpose |
|---|---|---|
| `compute_rolling_metrics(symbol, conn, now)` | pure | Read positions table, return dict of metrics |
| `_months_negative_consecutive(pnl_by_month, now)` | pure | Count trailing full calendar months with pnl<0 |
| `evaluate_state(metrics, current_state, manual_override, config)` | pure | Returns (new_state, reason) |
| `apply_transition(symbol, new_state, reason, metrics, conn)` | persist | Writes symbol_health + symbol_health_events |
| `get_symbol_state(symbol)` | read | Fast lookup — returns "NORMAL" if no row |
| `evaluate_and_record(symbol, cfg, conn, now)` | orchestrator | The composite used by the monitor loop |
| `evaluate_all_symbols(cfg, now)` | orchestrator | Calls evaluate_and_record for every curated symbol |
| `health_monitor_loop(...)` | thread | Daily cron @ 00:00 UTC; separate fn for on-position-close trigger |

**Task 10 will wire the on-position-close trigger into `db_close_position` in `btc_api.py`.**

---

## Task 1: Dependency check + schema migration + config defaults

**Files:**
- Modify: `btc_api.py` (init_db + CFG_DEFAULTS)
- Create: `tests/test_health_persistence.py` (initial file with just schema test)

- [ ] **Step 1: Confirm freezegun is importable**

Run: `python -c "import freezegun; print(freezegun.__version__)"`
Expected: version string. If ImportError, add `freezegun>=1.4` to `requirements.txt` and `pip install freezegun`. (freezegun is widely available; btc_scanner tests use it already — verify with `grep -l freezegun tests/`.)

- [ ] **Step 2: Write failing schema test**

Create `tests/test_health_persistence.py`:
```python
"""Schema + persistence tests for health module."""
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


def test_schema_has_symbol_health_tables(tmp_db):
    """init_db() must create the two health tables."""
    import btc_api
    conn = btc_api.get_db()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('symbol_health', 'symbol_health_events')"
        ).fetchall()
    finally:
        conn.close()
    names = {r[0] for r in rows}
    assert "symbol_health" in names
    assert "symbol_health_events" in names


def test_symbol_health_columns(tmp_db):
    """symbol_health must have the specified columns."""
    import btc_api
    conn = btc_api.get_db()
    try:
        cols = conn.execute("PRAGMA table_info(symbol_health)").fetchall()
    finally:
        conn.close()
    col_names = {c[1] for c in cols}
    for required in ("symbol", "state", "state_since",
                      "last_evaluated_at", "last_metrics_json", "manual_override"):
        assert required in col_names, f"missing column: {required}"


def test_symbol_health_events_columns(tmp_db):
    import btc_api
    conn = btc_api.get_db()
    try:
        cols = conn.execute("PRAGMA table_info(symbol_health_events)").fetchall()
    finally:
        conn.close()
    col_names = {c[1] for c in cols}
    for required in ("id", "symbol", "from_state", "to_state",
                      "trigger_reason", "metrics_json", "ts"):
        assert required in col_names, f"missing column: {required}"
```

- [ ] **Step 3: Run test to verify fail**

Run: `python -m pytest tests/test_health_persistence.py -v`
Expected: 3 tests FAIL (tables don't exist).

- [ ] **Step 4: Add tables to btc_api.init_db()**

Open `btc_api.py`, locate `init_db()` (around line 780). After the existing `notifications_sent` table + index (added in PR #164), add before the closing of the function:

```python
    con.execute("""
        CREATE TABLE IF NOT EXISTS symbol_health (
            symbol              TEXT PRIMARY KEY,
            state               TEXT NOT NULL DEFAULT 'NORMAL',
            state_since         TEXT NOT NULL,
            last_evaluated_at   TEXT NOT NULL,
            last_metrics_json   TEXT,
            manual_override     INTEGER NOT NULL DEFAULT 0
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS symbol_health_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT NOT NULL,
            from_state      TEXT NOT NULL,
            to_state        TEXT NOT NULL,
            trigger_reason  TEXT NOT NULL,
            metrics_json    TEXT NOT NULL,
            ts              TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_health_events_symbol
            ON symbol_health_events(symbol, ts DESC)
    """)
```

- [ ] **Step 5: Add kill_switch block to CFG_DEFAULTS**

Locate `CFG_DEFAULTS` in `btc_api.py` (around line 160). Add the `kill_switch` block among other defaults:

```python
        "kill_switch": {
            "enabled": True,
            "min_trades_for_eval": 20,
            "alert_win_rate_threshold": 0.15,
            "reduce_pnl_window_days": 30,
            "reduce_size_factor": 0.5,
            "pause_months_consecutive": 3,
            "auto_recovery_enabled": True,
        },
```

- [ ] **Step 6: Run tests to verify pass**

Run: `python -m pytest tests/test_health_persistence.py -v`
Expected: 3 PASS.

- [ ] **Step 7: Commit**

```bash
git add btc_api.py tests/test_health_persistence.py
git commit -m "feat(health): add symbol_health + symbol_health_events tables + config (#138)"
```

---

## Task 2: compute_rolling_metrics (pure function)

**Files:**
- Create: `health.py`
- Create: `tests/test_health_rolling_metrics.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_health_rolling_metrics.py`:
```python
"""Rolling metrics computed from positions table (closed positions only)."""
from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    yield db_path


def _insert_closed_position(conn, symbol, pnl_usd, exit_ts):
    """Insert a closed position directly. exit_ts is an ISO datetime string."""
    conn.execute(
        """INSERT INTO positions
           (symbol, direction, status, entry_price, entry_ts,
            exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct)
           VALUES (?, 'LONG', 'closed', 100.0, ?, 101.0, ?, 'TP', ?, ?)""",
        (symbol, exit_ts, exit_ts, pnl_usd, pnl_usd / 100.0),
    )
    conn.commit()


NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def test_empty_db_returns_zero_trades(tmp_db):
    from health import compute_rolling_metrics
    import btc_api
    conn = btc_api.get_db()
    try:
        metrics = compute_rolling_metrics("BTCUSDT", conn, now=NOW)
    finally:
        conn.close()
    assert metrics["trades_count_total"] == 0
    assert metrics["win_rate_20_trades"] == 0.0
    assert metrics["pnl_30d"] == 0.0
    assert metrics["months_negative_consecutive"] == 0


def test_open_positions_are_excluded(tmp_db):
    """Only closed positions count."""
    from health import compute_rolling_metrics
    import btc_api
    conn = btc_api.get_db()
    try:
        conn.execute(
            """INSERT INTO positions
               (symbol, direction, status, entry_price, entry_ts, pnl_usd, pnl_pct)
               VALUES ('BTCUSDT', 'LONG', 'open', 100.0, ?, NULL, NULL)""",
            (NOW.isoformat(),),
        )
        conn.commit()
        metrics = compute_rolling_metrics("BTCUSDT", conn, now=NOW)
    finally:
        conn.close()
    assert metrics["trades_count_total"] == 0


def test_win_rate_last_20_trades(tmp_db):
    """win_rate_20_trades = (winners in last 20 closed) / 20."""
    from health import compute_rolling_metrics
    import btc_api
    conn = btc_api.get_db()
    try:
        # 25 closed positions: first 5 wins, then 15 losses, then 5 wins → last 20 = 15 L + 5 W
        for i in range(25):
            pnl = 100.0 if (i < 5 or i >= 20) else -50.0
            ts = (NOW - timedelta(days=25 - i)).isoformat()
            _insert_closed_position(conn, "BTCUSDT", pnl, ts)
        metrics = compute_rolling_metrics("BTCUSDT", conn, now=NOW)
    finally:
        conn.close()
    assert metrics["trades_count_total"] == 25
    # Last 20 sorted desc by exit_ts: the 20 most recent → i=5..24 → 15 losses (i=5..19) + 5 wins (i=20..24) = 5/20 = 0.25
    assert metrics["win_rate_20_trades"] == 0.25


def test_win_rate_falls_back_to_available_when_under_20(tmp_db):
    """If only 10 trades exist, win_rate uses those 10 (caller handles cold-start via trades_count_total)."""
    from health import compute_rolling_metrics
    import btc_api
    conn = btc_api.get_db()
    try:
        for i in range(10):
            pnl = 100.0 if i < 3 else -50.0
            ts = (NOW - timedelta(days=10 - i)).isoformat()
            _insert_closed_position(conn, "BTCUSDT", pnl, ts)
        metrics = compute_rolling_metrics("BTCUSDT", conn, now=NOW)
    finally:
        conn.close()
    assert metrics["trades_count_total"] == 10
    assert metrics["win_rate_20_trades"] == 0.3  # 3/10


def test_pnl_30d_window(tmp_db):
    """Only positions with exit_ts >= now - 30d contribute to pnl_30d."""
    from health import compute_rolling_metrics
    import btc_api
    conn = btc_api.get_db()
    try:
        # 1 trade 40 days ago (excluded), 3 trades in the last 30 days
        _insert_closed_position(conn, "BTC", 999.0, (NOW - timedelta(days=40)).isoformat())
        _insert_closed_position(conn, "BTC", 100.0, (NOW - timedelta(days=10)).isoformat())
        _insert_closed_position(conn, "BTC", -50.0, (NOW - timedelta(days=5)).isoformat())
        _insert_closed_position(conn, "BTC", 30.0, (NOW - timedelta(days=1)).isoformat())
        metrics = compute_rolling_metrics("BTC", conn, now=NOW)
    finally:
        conn.close()
    assert metrics["pnl_30d"] == 80.0  # 100 - 50 + 30


def test_months_negative_consecutive_3_months(tmp_db):
    """3 full calendar months all with sum(pnl)<0 → months_negative_consecutive=3."""
    from health import compute_rolling_metrics
    import btc_api
    conn = btc_api.get_db()
    try:
        # NOW = 2026-06-15; previous 3 full calendar months are 2026-05, 2026-04, 2026-03.
        # Seed each with a loss.
        _insert_closed_position(conn, "BTC", -100.0, "2026-05-10T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", -50.0,  "2026-04-15T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", -75.0,  "2026-03-20T12:00:00+00:00")
        # Positive in 2026-02 — breaks the streak after 3
        _insert_closed_position(conn, "BTC", +200.0, "2026-02-05T12:00:00+00:00")
        metrics = compute_rolling_metrics("BTC", conn, now=NOW)
    finally:
        conn.close()
    assert metrics["months_negative_consecutive"] == 3


def test_months_negative_consecutive_broken_by_positive(tmp_db):
    """A positive month in the trailing window caps the streak."""
    from health import compute_rolling_metrics
    import btc_api
    conn = btc_api.get_db()
    try:
        # 2026-05 positive → streak is 0 from this moment
        _insert_closed_position(conn, "BTC", +500.0, "2026-05-10T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", -50.0,  "2026-04-15T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", -75.0,  "2026-03-20T12:00:00+00:00")
        metrics = compute_rolling_metrics("BTC", conn, now=NOW)
    finally:
        conn.close()
    assert metrics["months_negative_consecutive"] == 0


def test_months_negative_consecutive_partial_streak(tmp_db):
    """Most recent 2 months negative, 3rd back positive → streak=2."""
    from health import compute_rolling_metrics
    import btc_api
    conn = btc_api.get_db()
    try:
        _insert_closed_position(conn, "BTC", -100.0, "2026-05-10T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", -50.0,  "2026-04-15T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", +200.0, "2026-03-20T12:00:00+00:00")
        metrics = compute_rolling_metrics("BTC", conn, now=NOW)
    finally:
        conn.close()
    assert metrics["months_negative_consecutive"] == 2


def test_current_month_in_progress_is_excluded_from_streak(tmp_db):
    """The month containing NOW does NOT count toward consecutive_negative
    (it's partial). Only FULL prior calendar months do."""
    from health import compute_rolling_metrics
    import btc_api
    conn = btc_api.get_db()
    try:
        # 2026-06 is partial (NOW = 2026-06-15). Put a loss here — should not matter.
        _insert_closed_position(conn, "BTC", -999.0, "2026-06-10T12:00:00+00:00")
        # 2026-05, 04, 03 negative → streak=3
        _insert_closed_position(conn, "BTC", -100.0, "2026-05-10T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", -50.0,  "2026-04-15T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", -75.0,  "2026-03-20T12:00:00+00:00")
        metrics = compute_rolling_metrics("BTC", conn, now=NOW)
    finally:
        conn.close()
    assert metrics["months_negative_consecutive"] == 3  # not 4
```

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/test_health_rolling_metrics.py -v`
Expected: 9 FAIL (module `health` doesn't exist).

- [ ] **Step 3: Create `health.py` with compute_rolling_metrics**

Create `health.py`:
```python
"""Per-symbol health monitor (#138) — observer-only in PR 1.

Pure functions for computing rolling metrics + deciding state transitions,
plus thin persistence wrappers. Does NOT change trading behavior here; that
lands in PRs 2-4.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any


def _month_key(dt: datetime) -> str:
    """YYYY-MM string from a datetime (used as pnl_by_month key)."""
    return dt.strftime("%Y-%m")


def _previous_full_month_keys(now: datetime, n: int) -> list[str]:
    """Return the last n full calendar months BEFORE the month containing `now`,
    ordered from most recent to oldest. Example: now=2026-06-15, n=3 → ['2026-05', '2026-04', '2026-03']."""
    keys: list[str] = []
    # Step to the first day of `now`'s month, then step back one month at a time.
    first_of_now = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    current = first_of_now
    for _ in range(n):
        # Step back one month
        if current.month == 1:
            current = current.replace(year=current.year - 1, month=12)
        else:
            current = current.replace(month=current.month - 1)
        keys.append(_month_key(current))
    return keys


def _months_negative_consecutive(pnl_by_month: dict[str, float], now: datetime) -> int:
    """Count trailing consecutive FULL calendar months (starting from the month
    before `now`'s month) with sum(pnl) < 0. Stops at the first non-negative month."""
    streak = 0
    # Check up to 12 months backward — enough for any realistic threshold
    for key in _previous_full_month_keys(now, 12):
        pnl = pnl_by_month.get(key, 0.0)
        if pnl < 0:
            streak += 1
        else:
            break
    return streak


def compute_rolling_metrics(symbol: str, conn, now: datetime | None = None) -> dict[str, Any]:
    """Compute health metrics for `symbol` from the positions table.

    Only closed positions (`status='closed'`) are counted. `now` defaults to
    `datetime.now(timezone.utc)` but is injectable for tests.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    cutoff_30d = (now - timedelta(days=30)).isoformat()

    total = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE symbol=? AND status='closed'",
        (symbol,),
    ).fetchone()[0]

    last20 = conn.execute(
        """SELECT pnl_usd FROM positions
           WHERE symbol=? AND status='closed'
           ORDER BY exit_ts DESC
           LIMIT 20""",
        (symbol,),
    ).fetchall()
    if last20:
        winners = sum(1 for (pnl,) in last20 if (pnl or 0) > 0)
        win_rate_20_trades = winners / len(last20)
    else:
        win_rate_20_trades = 0.0

    pnl_30d_row = conn.execute(
        """SELECT COALESCE(SUM(pnl_usd), 0) FROM positions
           WHERE symbol=? AND status='closed' AND exit_ts >= ?""",
        (symbol, cutoff_30d),
    ).fetchone()
    pnl_30d = float(pnl_30d_row[0]) if pnl_30d_row else 0.0

    by_month_rows = conn.execute(
        """SELECT substr(exit_ts, 1, 7) AS ym, SUM(pnl_usd) AS pnl
           FROM positions
           WHERE symbol=? AND status='closed' AND exit_ts IS NOT NULL
           GROUP BY ym""",
        (symbol,),
    ).fetchall()
    pnl_by_month = {row[0]: float(row[1] or 0.0) for row in by_month_rows}

    return {
        "trades_count_total": int(total),
        "win_rate_20_trades": float(win_rate_20_trades),
        "pnl_30d": pnl_30d,
        "pnl_by_month": pnl_by_month,
        "months_negative_consecutive": _months_negative_consecutive(pnl_by_month, now),
    }
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_health_rolling_metrics.py -v`
Expected: 9 PASS.

- [ ] **Step 5: Commit**

```bash
git add health.py tests/test_health_rolling_metrics.py
git commit -m "feat(health): compute_rolling_metrics pure function (#138)"
```

---

## Task 3: evaluate_state (state machine, pure)

**Files:**
- Modify: `health.py`
- Create: `tests/test_health_state_machine.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_health_state_machine.py`:
```python
"""State machine: NORMAL → ALERT → REDUCED → PAUSED + manual override.

evaluate_state is pure: given metrics + current state + manual_override flag +
config, returns (new_state, reason)."""


CFG = {
    "min_trades_for_eval": 20,
    "alert_win_rate_threshold": 0.15,
    "reduce_pnl_window_days": 30,  # resolved elsewhere — evaluate_state just uses pnl_30d
    "reduce_size_factor": 0.5,     # unused by evaluate_state
    "pause_months_consecutive": 3,
    "auto_recovery_enabled": True,
}


def _metrics(total=50, wr=0.5, pnl_30d=500.0, months_neg=0):
    return {
        "trades_count_total": total,
        "win_rate_20_trades": wr,
        "pnl_30d": pnl_30d,
        "pnl_by_month": {},
        "months_negative_consecutive": months_neg,
    }


def test_healthy_symbol_stays_normal():
    from health import evaluate_state
    new, reason = evaluate_state(_metrics(), "NORMAL", False, CFG)
    assert new == "NORMAL"
    assert reason == "healthy"


def test_low_win_rate_transitions_to_alert():
    from health import evaluate_state
    new, reason = evaluate_state(_metrics(wr=0.10), "NORMAL", False, CFG)
    assert new == "ALERT"
    assert reason == "wr_below_threshold"


def test_negative_pnl_30d_transitions_to_reduced():
    from health import evaluate_state
    new, reason = evaluate_state(_metrics(wr=0.5, pnl_30d=-100.0), "ALERT", False, CFG)
    assert new == "REDUCED"
    assert reason == "pnl_neg_30d"


def test_three_months_negative_transitions_to_paused():
    from health import evaluate_state
    new, reason = evaluate_state(_metrics(months_neg=3), "REDUCED", False, CFG)
    assert new == "PAUSED"
    assert reason == "3mo_consec_neg"


def test_rule_order_paused_beats_reduced_beats_alert():
    """When multiple rules fire, the most severe wins."""
    from health import evaluate_state
    new, reason = evaluate_state(
        _metrics(wr=0.05, pnl_30d=-500, months_neg=3), "NORMAL", False, CFG,
    )
    assert new == "PAUSED"


def test_cold_start_holds_state_unchanged():
    """If trades_count_total < min_trades, state is locked to its current value."""
    from health import evaluate_state
    new, reason = evaluate_state(
        _metrics(total=10, wr=0.0, pnl_30d=-1000, months_neg=3), "NORMAL", False, CFG,
    )
    assert new == "NORMAL"
    assert reason == "insufficient_data"


def test_auto_recovery_from_alert_to_normal():
    """Once metrics are healthy again, ALERT → NORMAL automatically."""
    from health import evaluate_state
    new, reason = evaluate_state(_metrics(wr=0.5), "ALERT", False, CFG)
    assert new == "NORMAL"
    assert reason == "auto_recovery"


def test_auto_recovery_disabled_by_config():
    """If auto_recovery_enabled=False, non-healthy states hold until manual intervention."""
    from health import evaluate_state
    cfg = dict(CFG, auto_recovery_enabled=False)
    new, reason = evaluate_state(_metrics(wr=0.5), "ALERT", False, cfg)
    assert new == "ALERT"
    assert reason == "auto_recovery_disabled"


def test_manual_override_respected_on_normal_with_good_metrics():
    """A reactivated (manual_override=1) symbol with healthy metrics stays NORMAL
    (auto-recovery path but also fine; override is informational here)."""
    from health import evaluate_state
    new, reason = evaluate_state(_metrics(wr=0.5), "NORMAL", True, CFG)
    assert new == "NORMAL"


def test_manual_override_expires_if_a_severe_rule_fires():
    """Manual override survives minor dips but NOT a fresh PAUSED-triggering condition."""
    from health import evaluate_state
    new, reason = evaluate_state(
        _metrics(months_neg=3), "NORMAL", True, CFG,
    )
    assert new == "PAUSED"
    assert reason == "3mo_consec_neg"
```

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/test_health_state_machine.py -v`
Expected: 10 FAIL (`evaluate_state` doesn't exist).

- [ ] **Step 3: Implement evaluate_state**

Append to `health.py`:
```python
# ─────────────────────────────────────────────────────────────────────────────
#  STATE MACHINE (pure)
# ─────────────────────────────────────────────────────────────────────────────

VALID_STATES = ("NORMAL", "ALERT", "REDUCED", "PAUSED")


def evaluate_state(
    metrics: dict[str, Any],
    current_state: str,
    manual_override: bool,
    config: dict[str, Any],
) -> tuple[str, str]:
    """Return (new_state, reason) given metrics + current state + manual override.

    Rule precedence (most severe wins):
      1. insufficient_data → hold current state
      2. months_negative_consecutive >= pause_months_consecutive → PAUSED
      3. pnl_30d < 0 → REDUCED
      4. win_rate_20_trades < alert_win_rate_threshold → ALERT
      5. else → NORMAL (auto-recovery; if auto_recovery_enabled=False and
         current != NORMAL, hold current state with reason='auto_recovery_disabled')

    manual_override is informational: a PAUSED→NORMAL reactivation sets override=True,
    but a SUBSEQUENT severe rule (rule 2) still transitions to PAUSED.
    """
    min_trades = int(config.get("min_trades_for_eval", 20))
    if metrics.get("trades_count_total", 0) < min_trades:
        return current_state, "insufficient_data"

    # Most severe first
    pause_threshold = int(config.get("pause_months_consecutive", 3))
    if metrics.get("months_negative_consecutive", 0) >= pause_threshold:
        return "PAUSED", "3mo_consec_neg"

    if metrics.get("pnl_30d", 0.0) < 0:
        return "REDUCED", "pnl_neg_30d"

    wr_threshold = float(config.get("alert_win_rate_threshold", 0.15))
    if metrics.get("win_rate_20_trades", 0.0) < wr_threshold:
        return "ALERT", "wr_below_threshold"

    # Healthy path
    if current_state == "NORMAL":
        return "NORMAL", "healthy"

    if config.get("auto_recovery_enabled", True):
        return "NORMAL", "auto_recovery"
    return current_state, "auto_recovery_disabled"
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_health_state_machine.py -v`
Expected: 10 PASS.

- [ ] **Step 5: Commit**

```bash
git add health.py tests/test_health_state_machine.py
git commit -m "feat(health): evaluate_state pure function (#138)"
```

---

## Task 4: apply_transition + get_symbol_state (persistence)

**Files:**
- Modify: `health.py`
- Modify: `tests/test_health_persistence.py`

- [ ] **Step 1: Extend failing tests**

Append to `tests/test_health_persistence.py`:
```python
from datetime import datetime, timezone


def test_apply_transition_writes_row_and_event(tmp_db):
    from health import apply_transition
    import btc_api
    metrics = {
        "trades_count_total": 50, "win_rate_20_trades": 0.5,
        "pnl_30d": 100.0, "pnl_by_month": {},
        "months_negative_consecutive": 0,
    }
    apply_transition("BTCUSDT", new_state="ALERT", reason="wr_below_threshold",
                     metrics=metrics, from_state="NORMAL")
    conn = btc_api.get_db()
    try:
        row = conn.execute(
            "SELECT state, state_since, manual_override FROM symbol_health WHERE symbol=?",
            ("BTCUSDT",),
        ).fetchone()
        event = conn.execute(
            """SELECT from_state, to_state, trigger_reason FROM symbol_health_events
               WHERE symbol=? ORDER BY ts DESC LIMIT 1""",
            ("BTCUSDT",),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "ALERT"
    assert row[2] == 0  # manual_override default 0
    assert event == ("NORMAL", "ALERT", "wr_below_threshold")


def test_get_symbol_state_returns_normal_for_unknown(tmp_db):
    """A symbol with no row is treated as NORMAL by default."""
    from health import get_symbol_state
    assert get_symbol_state("UNSEEN") == "NORMAL"


def test_get_symbol_state_returns_persisted(tmp_db):
    from health import apply_transition, get_symbol_state
    metrics = {"trades_count_total": 50, "win_rate_20_trades": 0.5,
                "pnl_30d": 0.0, "pnl_by_month": {},
                "months_negative_consecutive": 0}
    apply_transition("JUPUSDT", new_state="PAUSED", reason="3mo_consec_neg",
                      metrics=metrics, from_state="REDUCED")
    assert get_symbol_state("JUPUSDT") == "PAUSED"


def test_apply_transition_same_state_is_idempotent(tmp_db):
    """If new_state == current, update last_evaluated_at but do NOT insert event row."""
    from health import apply_transition, _record_evaluation
    import btc_api
    metrics = {"trades_count_total": 50, "win_rate_20_trades": 0.5,
                "pnl_30d": 0.0, "pnl_by_month": {},
                "months_negative_consecutive": 0}
    apply_transition("BTC", new_state="ALERT", reason="wr_below_threshold",
                      metrics=metrics, from_state="NORMAL")
    _record_evaluation("BTC", metrics, new_state="ALERT")  # idempotent no-op transition
    conn = btc_api.get_db()
    try:
        event_count = conn.execute(
            "SELECT COUNT(*) FROM symbol_health_events WHERE symbol='BTC'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert event_count == 1  # initial transition, not a second event


def test_reactivate_sets_manual_override(tmp_db):
    """reactivate_symbol flips manual_override to 1 and emits event."""
    from health import apply_transition, reactivate_symbol
    metrics = {"trades_count_total": 50, "win_rate_20_trades": 0.5,
                "pnl_30d": 0.0, "pnl_by_month": {},
                "months_negative_consecutive": 0}
    apply_transition("DOGE", new_state="PAUSED", reason="3mo_consec_neg",
                      metrics=metrics, from_state="REDUCED")
    reactivate_symbol("DOGE", reason="backtest_validated")
    import btc_api
    conn = btc_api.get_db()
    try:
        row = conn.execute(
            "SELECT state, manual_override FROM symbol_health WHERE symbol='DOGE'"
        ).fetchone()
        last_event = conn.execute(
            "SELECT trigger_reason FROM symbol_health_events WHERE symbol='DOGE' "
            "ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("NORMAL", 1)
    assert last_event[0] == "manual_override"
```

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/test_health_persistence.py -v`
Expected: 3 existing PASS + 5 new FAIL.

- [ ] **Step 3: Implement the persistence helpers in health.py**

Append to `health.py`:
```python
# ─────────────────────────────────────────────────────────────────────────────
#  PERSISTENCE
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn():
    import btc_api
    return btc_api.get_db()


def get_symbol_state(symbol: str) -> str:
    """Return the current state of a symbol, or 'NORMAL' if it has no row."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT state FROM symbol_health WHERE symbol=?",
            (symbol,),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else "NORMAL"


def _record_evaluation(symbol: str, metrics: dict[str, Any], new_state: str) -> None:
    """Update last_evaluated_at + last_metrics_json without changing state.
    Creates the row if it doesn't exist. No event is emitted."""
    conn = _conn()
    now = _now_iso()
    try:
        conn.execute(
            """INSERT INTO symbol_health (symbol, state, state_since, last_evaluated_at, last_metrics_json)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(symbol) DO UPDATE SET
                 last_evaluated_at = excluded.last_evaluated_at,
                 last_metrics_json = excluded.last_metrics_json""",
            (symbol, new_state, now, now, json.dumps(metrics, default=str)),
        )
        conn.commit()
    finally:
        conn.close()


def apply_transition(
    symbol: str,
    new_state: str,
    reason: str,
    metrics: dict[str, Any],
    from_state: str,
    manual_override: int | None = None,
) -> None:
    """Write the new state to symbol_health AND append a row to symbol_health_events.

    If new_state == from_state this is a bug — callers should prefer `_record_evaluation`
    for same-state updates. We still handle it gracefully by skipping the event insert.
    """
    if new_state not in VALID_STATES:
        raise ValueError(f"invalid state: {new_state!r}")
    now = _now_iso()
    metrics_json = json.dumps(metrics, default=str)

    conn = _conn()
    try:
        # Update symbol_health (upsert).
        extra_sets = ""
        extra_params: tuple = ()
        if manual_override is not None:
            extra_sets = ", manual_override = excluded.manual_override"
        conn.execute(
            f"""INSERT INTO symbol_health
                (symbol, state, state_since, last_evaluated_at, last_metrics_json, manual_override)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                  state = excluded.state,
                  state_since = excluded.state_since,
                  last_evaluated_at = excluded.last_evaluated_at,
                  last_metrics_json = excluded.last_metrics_json
                  {extra_sets}""",
            (symbol, new_state, now, now, metrics_json,
             int(manual_override) if manual_override is not None else 0),
        )

        # Emit event only if state actually changed.
        if from_state != new_state:
            conn.execute(
                """INSERT INTO symbol_health_events
                   (symbol, from_state, to_state, trigger_reason, metrics_json, ts)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (symbol, from_state, new_state, reason, metrics_json, now),
            )
        conn.commit()
    finally:
        conn.close()


def reactivate_symbol(symbol: str, reason: str = "manual") -> None:
    """Manually reset a symbol to NORMAL with manual_override=1. Used by the
    reactivate endpoint and CLI."""
    current = get_symbol_state(symbol)
    metrics = {"reactivation_reason": reason}
    apply_transition(
        symbol, new_state="NORMAL", reason="manual_override",
        metrics=metrics, from_state=current, manual_override=1,
    )
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_health_persistence.py -v`
Expected: 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add health.py tests/test_health_persistence.py
git commit -m "feat(health): apply_transition + get_symbol_state + reactivate_symbol (#138)"
```

---

## Task 5: evaluate_and_record + evaluate_all_symbols orchestrators

**Files:**
- Modify: `health.py`
- Create: `tests/test_health_integration.py`

- [ ] **Step 1: Write failing integration tests**

Create `tests/test_health_integration.py`:
```python
"""End-to-end: insert positions → run evaluate_and_record → verify state + events."""
from datetime import datetime, timedelta, timezone

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


def _insert_closed(conn, symbol, pnl, exit_ts):
    conn.execute(
        """INSERT INTO positions
           (symbol, direction, status, entry_price, entry_ts,
            exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct)
           VALUES (?, 'LONG', 'closed', 100.0, ?, 101.0, ?, 'TP', ?, ?)""",
        (symbol, exit_ts, exit_ts, pnl, pnl / 100.0),
    )
    conn.commit()


CFG = {"kill_switch": {
    "enabled": True,
    "min_trades_for_eval": 20,
    "alert_win_rate_threshold": 0.15,
    "reduce_pnl_window_days": 30,
    "reduce_size_factor": 0.5,
    "pause_months_consecutive": 3,
    "auto_recovery_enabled": True,
}}
NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def test_evaluate_and_record_healthy_leaves_normal_no_event(tmp_db):
    from health import evaluate_and_record
    import btc_api
    conn = btc_api.get_db()
    try:
        # 25 winning trades → healthy
        for i in range(25):
            _insert_closed(conn, "BTC", 100.0, (NOW - timedelta(days=25 - i)).isoformat())
        evaluate_and_record("BTC", CFG, now=NOW)
        state = conn.execute(
            "SELECT state FROM symbol_health WHERE symbol='BTC'"
        ).fetchone()
        events = conn.execute(
            "SELECT COUNT(*) FROM symbol_health_events WHERE symbol='BTC'"
        ).fetchone()
    finally:
        conn.close()
    assert state[0] == "NORMAL"
    assert events[0] == 0  # no state transition


def test_evaluate_and_record_transitions_emit_event(tmp_db):
    from health import evaluate_and_record
    import btc_api
    conn = btc_api.get_db()
    try:
        # 25 losing trades in last 3 months → PAUSED
        _insert_closed(conn, "DOGE", -100.0, "2026-05-10T12:00:00+00:00")
        _insert_closed(conn, "DOGE", -100.0, "2026-04-15T12:00:00+00:00")
        _insert_closed(conn, "DOGE", -100.0, "2026-03-20T12:00:00+00:00")
        for i in range(22):
            _insert_closed(conn, "DOGE", -10.0, (NOW - timedelta(days=40 + i)).isoformat())
        evaluate_and_record("DOGE", CFG, now=NOW)
        state_row = conn.execute(
            "SELECT state FROM symbol_health WHERE symbol='DOGE'"
        ).fetchone()
        events = conn.execute(
            "SELECT to_state, trigger_reason FROM symbol_health_events WHERE symbol='DOGE'"
        ).fetchall()
    finally:
        conn.close()
    assert state_row[0] == "PAUSED"
    assert len(events) == 1
    assert events[0] == ("PAUSED", "3mo_consec_neg")


def test_evaluate_all_symbols_iterates_default_list(tmp_db, monkeypatch):
    from health import evaluate_all_symbols
    import btc_api
    # Patch DEFAULT_SYMBOLS to a small deterministic set
    monkeypatch.setattr("btc_scanner.DEFAULT_SYMBOLS", ["ALPHA", "BETA"])
    conn = btc_api.get_db()
    try:
        # Seed ALPHA healthy; leave BETA without trades
        for i in range(25):
            _insert_closed(conn, "ALPHA", 100.0, (NOW - timedelta(days=25 - i)).isoformat())
        evaluate_all_symbols(CFG, now=NOW)
        rows = conn.execute(
            "SELECT symbol, state FROM symbol_health"
        ).fetchall()
    finally:
        conn.close()
    rows_dict = dict(rows)
    assert rows_dict.get("ALPHA") == "NORMAL"
    # BETA has 0 trades → insufficient_data → state stays at default NORMAL
    assert rows_dict.get("BETA") == "NORMAL"


def test_kill_switch_disabled_in_config_skips_evaluation(tmp_db, monkeypatch):
    from health import evaluate_all_symbols
    import btc_api
    monkeypatch.setattr("btc_scanner.DEFAULT_SYMBOLS", ["X"])
    cfg = {"kill_switch": {"enabled": False}}
    conn = btc_api.get_db()
    try:
        for i in range(25):
            _insert_closed(conn, "X", -100.0, (NOW - timedelta(days=25 - i)).isoformat())
        evaluate_all_symbols(cfg, now=NOW)
        rows = conn.execute("SELECT COUNT(*) FROM symbol_health").fetchone()
    finally:
        conn.close()
    assert rows[0] == 0  # no row created
```

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/test_health_integration.py -v`
Expected: 4 FAIL (orchestrators don't exist).

- [ ] **Step 3: Implement orchestrators**

Append to `health.py`:
```python
# ─────────────────────────────────────────────────────────────────────────────
#  ORCHESTRATION
# ─────────────────────────────────────────────────────────────────────────────

def _get_manual_override(symbol: str) -> bool:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT manual_override FROM symbol_health WHERE symbol=?",
            (symbol,),
        ).fetchone()
    finally:
        conn.close()
    return bool(row[0]) if row else False


def evaluate_and_record(symbol: str, cfg: dict[str, Any], now: datetime | None = None) -> str:
    """Compute metrics + evaluate state + persist. Returns the resulting state."""
    ks_cfg = (cfg.get("kill_switch") or {})
    if not ks_cfg.get("enabled", True):
        return "NORMAL"

    if now is None:
        now = datetime.now(timezone.utc)

    conn = _conn()
    try:
        metrics = compute_rolling_metrics(symbol, conn, now=now)
    finally:
        conn.close()

    current = get_symbol_state(symbol)
    override = _get_manual_override(symbol)
    new_state, reason = evaluate_state(metrics, current, override, ks_cfg)

    if new_state != current:
        apply_transition(symbol, new_state=new_state, reason=reason,
                         metrics=metrics, from_state=current)
    else:
        _record_evaluation(symbol, metrics, new_state)
    return new_state


def evaluate_all_symbols(cfg: dict[str, Any], now: datetime | None = None) -> dict[str, str]:
    """Evaluate every symbol in btc_scanner.DEFAULT_SYMBOLS. Returns {symbol: state}.

    If kill_switch.enabled is False, returns {} without touching the DB.
    """
    ks_cfg = (cfg.get("kill_switch") or {})
    if not ks_cfg.get("enabled", True):
        return {}
    from btc_scanner import DEFAULT_SYMBOLS
    return {sym: evaluate_and_record(sym, cfg, now=now) for sym in DEFAULT_SYMBOLS}
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_health_integration.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add health.py tests/test_health_integration.py
git commit -m "feat(health): evaluate_and_record + evaluate_all_symbols orchestrators (#138)"
```

---

## Task 6: Daily cron + on-position-close trigger (health_monitor_loop)

**Files:**
- Modify: `health.py` (add health_monitor_loop + trigger_health_evaluation)
- Modify: `btc_api.py` (spawn thread in main startup; call trigger on close)
- Create: `tests/test_health_trigger.py` (focused test — the full loop is not unit-tested)

- [ ] **Step 1: Write failing tests**

Create `tests/test_health_trigger.py`:
```python
"""Position-close trigger: closing a position must invoke evaluate_and_record
for that symbol."""
from unittest.mock import patch, MagicMock

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


def test_trigger_health_evaluation_calls_evaluate_and_record(tmp_db):
    """The trigger wraps evaluate_and_record in error-suppression so DB errors
    don't crash the position-close path."""
    from health import trigger_health_evaluation
    with patch("health.evaluate_and_record") as mock_eval:
        trigger_health_evaluation("BTCUSDT", {"kill_switch": {"enabled": True}})
    mock_eval.assert_called_once_with("BTCUSDT", {"kill_switch": {"enabled": True}})


def test_trigger_swallows_exceptions(tmp_db, caplog):
    """If evaluate_and_record raises, the trigger logs and returns None."""
    import logging
    from health import trigger_health_evaluation
    with patch("health.evaluate_and_record", side_effect=RuntimeError("boom")):
        with caplog.at_level(logging.ERROR, logger="health"):
            result = trigger_health_evaluation("BTC", {"kill_switch": {"enabled": True}})
    assert result is None
    assert any("boom" in r.message for r in caplog.records)


def test_trigger_respects_disabled_kill_switch(tmp_db):
    """If kill_switch.enabled=False, trigger is a no-op (does not call evaluate)."""
    from health import trigger_health_evaluation
    with patch("health.evaluate_and_record") as mock_eval:
        trigger_health_evaluation("BTC", {"kill_switch": {"enabled": False}})
    assert mock_eval.call_count == 0
```

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/test_health_trigger.py -v`
Expected: 3 FAIL.

- [ ] **Step 3: Implement trigger and loop**

Append to `health.py`:
```python
# ─────────────────────────────────────────────────────────────────────────────
#  TRIGGER + DAILY LOOP
# ─────────────────────────────────────────────────────────────────────────────

import logging as _logging
import threading as _threading
import time as _time

log = _logging.getLogger("health")


def trigger_health_evaluation(symbol: str, cfg: dict[str, Any]) -> None:
    """Fire-and-forget health evaluation for a single symbol.
    Swallows exceptions so callers (e.g. db_close_position) never crash."""
    ks_cfg = (cfg.get("kill_switch") or {})
    if not ks_cfg.get("enabled", True):
        return
    try:
        evaluate_and_record(symbol, cfg)
    except Exception as e:  # noqa: BLE001
        log.error("health trigger failed for %s: %s", symbol, e, exc_info=True)


def _seconds_until_next_midnight_utc(now: datetime) -> float:
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    return (tomorrow - now).total_seconds()


def health_monitor_loop(cfg_fn, stop_event=None) -> None:
    """Daily cron @ 00:00 UTC: run evaluate_all_symbols with fresh cfg.

    `cfg_fn` is a callable returning the current config dict (re-read each
    day in case user edits config.json). `stop_event` is an optional
    threading.Event for graceful shutdown; if None, loops until killed.
    """
    if stop_event is None:
        stop_event = _threading.Event()
    while not stop_event.is_set():
        sleep_s = _seconds_until_next_midnight_utc(datetime.now(timezone.utc))
        # Wake slightly before midnight in case of drift, but the loop body rechecks time
        if stop_event.wait(timeout=sleep_s):
            return
        try:
            cfg = cfg_fn()
            evaluate_all_symbols(cfg)
            log.info("health_monitor_loop: daily sweep complete")
        except Exception as e:  # noqa: BLE001
            log.error("health_monitor_loop sweep failed: %s", e, exc_info=True)
```

- [ ] **Step 4: Wire the trigger into db_close_position**

In `btc_api.py`, find `db_close_position` (around line 510-520). After the successful UPDATE, just before `return`, add:

```python
    # Kill switch #138: trigger health evaluation for this symbol.
    try:
        from health import trigger_health_evaluation
        trigger_health_evaluation(pos["symbol"], CFG)
    except Exception as e:
        log.warning("health trigger skipped for position close: %s", e)
```

Adjust variable names to match the actual function (it may be `pos["symbol"]` or similar — read the function body and use the real variable holding the symbol).

- [ ] **Step 5: Spawn the daily thread in the API startup**

In `btc_api.py`, locate where `scanner_loop` is started (look for `threading.Thread(target=scanner_loop...)`). After that thread starts, add:

```python
    # Kill switch daily sweep (#138)
    from health import health_monitor_loop
    health_thread = threading.Thread(
        target=health_monitor_loop,
        args=(lambda: CFG,),  # fresh CFG lookup each day
        daemon=True,
        name="health-monitor",
    )
    health_thread.start()
    log.info("Health monitor thread started (daily @ 00:00 UTC)")
```

- [ ] **Step 6: Run trigger tests**

Run: `python -m pytest tests/test_health_trigger.py -v`
Expected: 3 PASS.

- [ ] **Step 7: Run full health test set**

Run: `python -m pytest tests/test_health_*.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add health.py btc_api.py tests/test_health_trigger.py
git commit -m "feat(health): trigger + health_monitor_loop (daily cron + on-close) (#138)"
```

---

## Task 7: API endpoints

**Files:**
- Modify: `btc_api.py` (add three endpoints)
- Create: `tests/test_health_endpoints.py`

- [ ] **Step 1: Write failing endpoint tests**

Create `tests/test_health_endpoints.py`:
```python
"""GET /health/symbols, GET /health/events, POST /health/reactivate/{symbol}."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    return TestClient(btc_api.app)


def test_get_health_symbols_empty(client):
    resp = client.get("/health/symbols")
    assert resp.status_code == 200
    assert resp.json() == {"symbols": []}


def test_get_health_symbols_returns_rows(client):
    from health import apply_transition
    apply_transition(
        "BTC", new_state="ALERT", reason="wr_below_threshold",
        metrics={"trades_count_total": 50, "win_rate_20_trades": 0.1,
                  "pnl_30d": 0.0, "pnl_by_month": {},
                  "months_negative_consecutive": 0},
        from_state="NORMAL",
    )
    resp = client.get("/health/symbols")
    assert resp.status_code == 200
    payload = resp.json()
    assert len(payload["symbols"]) == 1
    assert payload["symbols"][0]["symbol"] == "BTC"
    assert payload["symbols"][0]["state"] == "ALERT"


def test_get_health_events_returns_history(client):
    from health import apply_transition
    metrics = {"trades_count_total": 50, "win_rate_20_trades": 0.5,
                "pnl_30d": 0.0, "pnl_by_month": {},
                "months_negative_consecutive": 0}
    apply_transition("BTC", "ALERT", "wr_below_threshold", metrics, "NORMAL")
    apply_transition("BTC", "REDUCED", "pnl_neg_30d", metrics, "ALERT")
    resp = client.get("/health/events?symbol=BTC")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert len(events) == 2
    # Most recent first
    assert events[0]["to_state"] == "REDUCED"
    assert events[1]["to_state"] == "ALERT"


def test_post_health_reactivate_sets_manual_override(client):
    from health import apply_transition
    metrics = {"trades_count_total": 50, "win_rate_20_trades": 0.5,
                "pnl_30d": 0.0, "pnl_by_month": {},
                "months_negative_consecutive": 0}
    apply_transition("JUP", "PAUSED", "3mo_consec_neg", metrics, "REDUCED")

    resp = client.post("/health/reactivate/JUP", json={"reason": "backtest_ok"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["state"] == "NORMAL"

    # GET again and verify
    resp = client.get("/health/symbols")
    rows = {r["symbol"]: r for r in resp.json()["symbols"]}
    assert rows["JUP"]["state"] == "NORMAL"
    assert rows["JUP"]["manual_override"] == 1
```

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/test_health_endpoints.py -v`
Expected: 4 FAIL (endpoints don't exist).

- [ ] **Step 3: Add endpoints to btc_api.py**

Find a convenient location near other endpoints in `btc_api.py` (e.g., after `/positions` endpoints). Add:

```python
# ── Kill switch / health endpoints (#138) ─────────────────────────────
from pydantic import BaseModel


class ReactivateRequest(BaseModel):
    reason: str = "manual"


@app.get("/health/symbols")
def get_health_symbols():
    """List current health state per symbol."""
    con = get_db()
    try:
        rows = con.execute(
            """SELECT symbol, state, state_since, last_evaluated_at,
                      last_metrics_json, manual_override
               FROM symbol_health
               ORDER BY symbol"""
        ).fetchall()
    finally:
        con.close()
    cols = ("symbol", "state", "state_since", "last_evaluated_at",
            "last_metrics_json", "manual_override")
    return {"symbols": [dict(zip(cols, r)) for r in rows]}


@app.get("/health/events")
def get_health_events(symbol: Optional[str] = None, limit: int = 50):
    """Transition history. Optionally filter by symbol."""
    con = get_db()
    try:
        if symbol:
            rows = con.execute(
                """SELECT id, symbol, from_state, to_state, trigger_reason,
                          metrics_json, ts
                   FROM symbol_health_events WHERE symbol=?
                   ORDER BY ts DESC LIMIT ?""",
                (symbol, limit),
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT id, symbol, from_state, to_state, trigger_reason,
                          metrics_json, ts
                   FROM symbol_health_events ORDER BY ts DESC LIMIT ?""",
                (limit,),
            ).fetchall()
    finally:
        con.close()
    cols = ("id", "symbol", "from_state", "to_state", "trigger_reason",
            "metrics_json", "ts")
    return {"events": [dict(zip(cols, r)) for r in rows]}


@app.post("/health/reactivate/{symbol}")
def post_health_reactivate(symbol: str, body: ReactivateRequest):
    """Manually reset a symbol to NORMAL with manual_override=1."""
    from health import reactivate_symbol, get_symbol_state
    reactivate_symbol(symbol.upper(), reason=body.reason)
    return {"ok": True, "symbol": symbol.upper(), "state": get_symbol_state(symbol.upper())}
```

- [ ] **Step 4: Run endpoint tests**

Run: `python -m pytest tests/test_health_endpoints.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add btc_api.py tests/test_health_endpoints.py
git commit -m "feat(health): GET /health/symbols, GET /health/events, POST /health/reactivate (#138)"
```

---

## Task 8: Full regression + push + PR

**Files:** none changed; verification only.

- [ ] **Step 1: Full test suite**

Run: `python -m pytest tests/ -q -m "not network"`
Expected: all PASS. Prior baseline was 504 (after PR #164); this PR adds ~35 tests (9 + 10 + 8 + 4 + 3 + 4 = 38), so target is **~542 passed, 0 failed**. If different, investigate before pushing.

- [ ] **Step 2: Push branch**

```bash
git push -u origin feat/kill-switch-foundation
```

- [ ] **Step 3: Open PR**

```bash
gh pr create --base main --head feat/kill-switch-foundation \
  --title "feat(health): kill switch Foundation — observer-only (#138 PR 1)" \
  --body "$(cat <<'BODY'
## Summary
First of 4 PRs for #138 (see [spec](docs/superpowers/specs/es/2026-04-21-kill-switch-design.md)). Ships the health-monitor infrastructure as an **observer only** — state machine, rolling metrics, persistence, API endpoints, daily cron + on-position-close trigger — without changing any trading behavior. Downstream PRs 2-4 will add the tier actions.

## What ships
- New tables `symbol_health` + `symbol_health_events` (via `btc_api.init_db()`)
- New module `health.py`:
  - `compute_rolling_metrics` (pure)
  - `evaluate_state` (pure state machine)
  - `apply_transition` / `get_symbol_state` / `reactivate_symbol` (persistence)
  - `evaluate_and_record` / `evaluate_all_symbols` (orchestrators)
  - `trigger_health_evaluation` + `health_monitor_loop` (daily cron + on-close)
- New endpoints: `GET /health/symbols`, `GET /health/events`, `POST /health/reactivate/{symbol}`
- Config: `kill_switch` block added to `CFG_DEFAULTS` in `btc_api.py`
- `db_close_position` now triggers `trigger_health_evaluation` after a successful close (fire-and-forget, errors logged)

## Unblocks
- PRs 2 (Alert), 3 (Reduce), 4 (Pause) can now wire their hooks into `scan()` via `get_symbol_state()`.

## Does NOT change
- Trading behavior: PR 1 is observer-only. No signals are skipped, no sizing is adjusted.
- No Telegram notifications yet (wired in PR 2 via `notifier.notify(HealthEvent)`).

## Test plan
- [x] ~38 new tests across 6 files (rolling metrics, state machine, persistence, integration, trigger, endpoints)
- [x] Full suite: ~542 passed, 0 failed
- [x] Schema migration verified (symbol_health + symbol_health_events exist after init_db)

Closes partial: #138 (PR 1 of 4).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
)"
```

- [ ] **Step 4: Watch CI**

```bash
sleep 12 && gh pr checks --watch --interval 15
```
Expected: backend-tests + frontend-typecheck PASS.

---

## Self-review

**Spec coverage (against `docs/superpowers/specs/es/2026-04-21-kill-switch-design.md` §9 PR 1):**
- §5 Arquitectura — `health.py` module + thread in `btc_api.py` + endpoints ✓ (Tasks 2-7)
- §6 Schema DB — `symbol_health` + `symbol_health_events` + index ✓ (Task 1)
- §7 State machine — all 5 transition rules, cold start, auto-recovery, manual_override ✓ (Task 3, 4)
- §8 Config block — kill_switch defaults ✓ (Task 1)
- §9 PR 1 list items:
  - Schema migration ✓ (Task 1)
  - `compute_rolling_metrics` ✓ (Task 2)
  - `evaluate_state` ✓ (Task 3)
  - `apply_transition` ✓ (Task 4)
  - `get_symbol_state` ✓ (Task 4)
  - `health_monitor_loop` daily cron ✓ (Task 6)
  - Triggered on position close ✓ (Task 6)
  - 3 API endpoints ✓ (Task 7)
  - Tests for state machine, rolling metrics, cold start, idempotence, migration ✓ (Tasks 1-7)
  - Trading behavior unchanged ✓ (no modifications to `btc_scanner.scan`, `simulate_strategy`, or Telegram emission paths aside from the notifier refactor already in PR #164)
- §11 Riesgos — `try/except` around trigger swallows failures so position close doesn't crash ✓ (Task 6, step 3)

**Placeholder scan:** no "TBD", no "similar to Task N", no "add error handling as needed". Every code step shows concrete code. Task 6 step 4 has one hedge ("adjust variable names to match") — acceptable because the implementer must read the actual function body.

**Type consistency:**
- `metrics` dict shape defined once in Task 2 (`trades_count_total`, `win_rate_20_trades`, `pnl_30d`, `pnl_by_month`, `months_negative_consecutive`) — used consistently in Tasks 3, 4, 5, 7.
- `apply_transition(symbol, new_state, reason, metrics, from_state, manual_override=None)` — Task 4 defines; Task 5 calls with 5 positional args (from_state=current) + no manual_override; `reactivate_symbol` in Task 4 calls with manual_override=1.
- `evaluate_state(metrics, current_state, manual_override, config) -> (new_state, reason)` — Task 3 signature used in Task 5 orchestrator.
- `trigger_health_evaluation(symbol, cfg)` — Task 6 defines; Task 6 step 4 calls from btc_api.
- `health_monitor_loop(cfg_fn, stop_event=None)` — Task 6 defines; step 5 spawns thread with `args=(lambda: CFG,)`.

All consistent.

**Scope:** observer-only PR 1 of #138 series. Does not include Alert/Reduce/Pause tier actions (PRs 2-4) or weekly report (separate feature, explicitly out of scope).
