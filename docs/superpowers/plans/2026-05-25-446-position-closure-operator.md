# PositionClosure Operator + Helper Layer Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve issues #446, #447, #448, #449, #450 (with explicit trade-off), and #451. Introduce `PositionClosure` business operator as the single legal entry point for closing a position. Demote `db/*` helpers (and related modules) to a pure SQL layer where `con: Connection` is mandatory. Delete `_tx_or_use`.

**Architecture:** Three-layer separation per Voronov's ontology: (1) pure SQL operators in `db/`, `auth/`, `notifier/`, etc. — receive `con`, no side-effects, no `_tx_or_use`; (2) business operators in new `operators/` package — own `transaction()`, orchestrate side-effects, declare atomicity; (3) callers (endpoints, scanner, scripts) — instantiate operators OR wrap helpers in explicit `with transaction()` blocks. The first inhabitant of layer 2 is `PositionClosure`. Other operators (`PositionOpening`, etc.) emerge only when caller composition demands them.

**Access primitives in `db/transaction.py`:** `transaction()` for read-write unit-of-work (BEGIN IMMEDIATE / COMMIT / ROLLBACK / close), `read_only_connection()` for pre-validation reads outside any transaction (no BEGIN; close on exit). Operators may use both; pure SQL helpers receive `con` and use neither.

**Tech Stack:** Python 3, `sqlite3` stdlib, `contextlib`, `pytest`.

---

## Context (read this before starting)

This plan executes the dirección Voronov articulated in `docs/superpowers/analysis/2026-05-25-446-tx-or-use-analysis-and-direction.md` and the contract specified in `docs/superpowers/analysis/2026-05-25-446-preconditions-synthesis.md`. Both files committed at `de70052` and `261e5ef` on this branch. **Read both before executing any task.**

Locked decisions:
- **Option C strict:** `db/` is the pure SQL layer; business transitions live in `operators/`.
- **PositionClosure only** in this PR. `PositionOpening` deferred (precondition 2 verdict: no symptoms present).
- **`apply_pnl_to_capital` runs IN-TX** (resolves #450 in favor of atomicity; trade-off: capital failure aborts close).
- **All 34 Cat. 1 helpers** become `con: Connection` mandatory in this PR. Caller-side wrapping in `with transaction()` where needed.
- **5 Cat. 2 hidden operators** (`save_scan`, `init_db`, `log_auth_event`, `dispatch_signal_to_users`, `db_close_position` standalone path) — fix `_tx_or_use` removal but defer operator-extraction to separate tickets. They use `transaction()` directly after migration.
- **Issues #446/#447/#448/#449 dissolve by construction.** #450 closed with documented trade-off. #451 closed via test fix in Task 14.

---

## File Structure

### New files

- `operators/__init__.py` — empty marker for the new business-operator package.
- `operators/position_closure.py` — `PositionClosure` context manager + `CloseOutcome` dataclass.
- `tests/operators/__init__.py` — empty.
- `tests/operators/test_position_closure.py` — 10 invariant tests (anchors #451; invariant 10 encodes the "best-effort post-commit" commitment).

### Modified files (close-flow specific)

- `db/positions.py`:
  - Add `db_get_position_by_id(con, pos_id) -> Optional[dict]`.
  - Add `db_close_position_sql(con, pos_id, exit_price, exit_reason, exit_ts, pnl_usd, pnl_pct) -> dict`.
  - Delete old `db_close_position` (dual-contract function with health-trigger branch).
  - Migrate other close-flow callers as needed.
- `db/capital.py`:
  - `apply_pnl_to_capital(con: Connection, tenant_id, pnl_usd)` — `con` mandatory.
  - `db_get_capital(con: Connection, tenant_id)` — `con` mandatory.
  - `db_upsert_capital(con: Connection, tenant_id, **kwargs)` — `con` mandatory.
  - `db_list_active_tenant_ids(con: Connection)` — `con` mandatory.
- `api/positions.py`:
  - `close_position` endpoint: use `PositionClosure(mode="USER")`.
  - `check_position_stops` scanner: use `PositionClosure(mode="SYSTEM")` in close loop after trailing-SL tx.
  - Delete `_apply_close_to_capital` shim (logic absorbed into operator).
  - Delete `post_tx_actions` machinery (operator handles ordering).

### Modified files (mechanical Cat. 1 sweep)

For each, change `_tx_or_use(con)` → direct `con.execute(...)` and signature `con: Optional[Connection] = None` → `con: Connection`. Then audit callsites and add `with transaction() as con:` wrappers where callers don't already pass `con`:

- `db/positions.py` — `db_create_position`, `db_last_exit_ts`, `db_get_positions`, `db_update_position`.
- `db/signals.py` — `get_scans`, `get_latest_scan_per_symbol`, `get_latest_signal`, `get_latest_scan`, `get_signals_summary`. (`save_scan` stays as Cat. 2 — use `transaction()` directly inside it, not `_tx_or_use`.)
- `db/schema.py` — `backfill_tenant`, `_migrate_multi_tenant_b1`, `_migrate_agent_audit`, `_migrate_agent_history`. (`init_db` stays as Cat. 2 hidden operator — use `transaction()` directly inside it.)
- `db/auth_schema.py` — `init_auth_db`, `has_any_user`, `init_system_state`, `is_setup_completed`, `mark_setup_completed`.
- `db/user_preferences.py` — `db_get_user_preferences`, `db_upsert_user_preferences`.
- `auth/tokens.py` — `create_refresh_token`, `lookup_refresh`, `revoke_refresh`, `revoke_family`, `revoke_all_for_user`.
- `auth/audit.py` — `log_auth_event`. (Cat. 2 hidden operator — use `transaction()` directly inside it.)
- `notifier/_storage.py` — `record_delivery`, `list_unread`, `mark_read`, `mark_all_read`.
- `notifier/dedupe.py` — `should_send`.
- `notifier/dispatch_per_user.py` — `_list_active_users`. (`dispatch_signal_to_users` stays as Cat. 2 — use `transaction()` directly inside it.)
- `health.py` — `_get_symbol_health_row`, `record_portfolio_transition`, `recent_portfolio_transitions`.
- `strategy/kill_switch_v2_shadow.py` — `_load_closed_trades`, `_load_open_positions`.
- `db/transaction.py` — **delete `_tx_or_use`** in the final task.

### Modified files (tests)

- `tests/api/test_check_position_stops_atomicity.py` — fix fixture to actually trigger trailing-SL (#451).
- `tests/db/test_transaction.py` — no changes needed (the wrapper itself is unchanged).
- Other test files that called helpers without passing `con` — wrap in `with transaction()` blocks.

### Modified files (docs)

- `CLAUDE.md` — update "Database access" section to describe operators layer + pure SQL helpers.

---

## Tasks

### Task 1: Verify branch and baseline

**Files:** none modified.

- [ ] **Step 1: Confirm branch and HEAD**

Run: `git rev-parse --abbrev-ref HEAD && git rev-parse HEAD`
Expected: `feat/fix-tx-or-use-dual-contract-446` and `261e5ef` (the preconditions commit).

- [ ] **Step 2: Confirm clean working tree**

Run: `git status --short`
Expected: empty.

- [ ] **Step 3: Baseline test count**

Run: `pytest --collect-only -q 2>&1 | tail -3`
Expected: around 2502 tests collected. Note the exact number.

- [ ] **Step 4: Smoke check that all 9 wrapper contract tests still pass**

Run: `pytest tests/db/test_transaction.py -v 2>&1 | tail -15`
Expected: 9/9 passing.

---

### Task 2: TDD — write the 9 failing invariant tests for PositionClosure

**Files:**
- Create: `tests/operators/__init__.py`
- Create: `tests/operators/test_position_closure.py`

- [ ] **Step 1: Create the empty package marker**

Create `tests/operators/__init__.py` as an empty file (matches project convention from `tests/db/__init__.py` and `tests/api/__init__.py`).

- [ ] **Step 2: Write the test file with all 9 invariants**

```python
"""Invariant tests for operators.position_closure.PositionClosure.

Anchors the 9 testable invariants declared in
docs/superpowers/analysis/2026-05-25-446-preconditions-synthesis.md
Section 'PositionClosure operator contract spec'.

Also resolves issue #451: this is the regression test for the trading
invariant ('every mutation derived from one tick of price decision
belongs to one serializable transaction') — done against the operator,
not against ad-hoc helper composition.
"""
import sqlite3
import threading
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from db.transaction import transaction
from db.schema import init_db


@pytest.fixture
def fresh_db_with_two_tenants(tmp_path, monkeypatch):
    """Initialize a fresh DB with two tenants, each with an open position."""
    db_path = tmp_path / "test.db"
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(db_path))
    init_db()

    with transaction() as con:
        # Two tenants with capital seeded.
        con.execute(
            "INSERT INTO capital (tenant_id, balance, peak_balance, max_drawdown_pct, updated_ts) "
            "VALUES (1, 10000.0, 10000.0, 0.0, ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        con.execute(
            "INSERT INTO capital (tenant_id, balance, peak_balance, max_drawdown_pct, updated_ts) "
            "VALUES (2, 5000.0, 5000.0, 0.0, ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        # Two open positions: one per tenant.
        con.execute(
            """INSERT INTO positions
               (id, symbol, direction, entry_price, qty, sl_price, tp_price,
                status, entry_ts, tenant_id, atr_entry, be_mult)
               VALUES (1, 'BTCUSDT', 'long', 100.0, 1.0, 95.0, 110.0,
                       'open', ?, 1, 2.0, 1.5)""",
            (datetime.now(timezone.utc).isoformat(),),
        )
        con.execute(
            """INSERT INTO positions
               (id, symbol, direction, entry_price, qty, sl_price, tp_price,
                status, entry_ts, tenant_id, atr_entry, be_mult)
               VALUES (2, 'ETHUSDT', 'long', 200.0, 0.5, 195.0, 220.0,
                       'open', ?, 2, 3.0, 1.5)""",
            (datetime.now(timezone.utc).isoformat(),),
        )
    return str(db_path)


# ---- Invariant 1: atomicity of close + capital ----

def test_atomicity_close_and_capital_succeed_together(fresh_db_with_two_tenants):
    """Successful close commits both UPDATE positions and UPDATE capital."""
    from operators.position_closure import PositionClosure

    with PositionClosure(
        pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
        mode="USER", caller_tenant_id=1,
    ) as closure:
        outcome = closure.execute()

    assert outcome.status == "closed"
    with transaction() as con:
        pos = con.execute("SELECT status FROM positions WHERE id=1").fetchone()
        cap = con.execute("SELECT balance FROM capital WHERE tenant_id=1").fetchone()
    assert pos["status"] == "closed"
    assert cap["balance"] != 10000.0  # P&L applied


def test_atomicity_capital_failure_aborts_close(fresh_db_with_two_tenants):
    """If apply_pnl_to_capital raises, the position close is rolled back."""
    from operators.position_closure import PositionClosure
    from db import capital as capital_module

    original = capital_module.apply_pnl_to_capital

    def boom(con, tenant_id, pnl_usd):
        raise sqlite3.OperationalError("simulated capital failure")

    with patch.object(capital_module, "apply_pnl_to_capital", boom):
        with pytest.raises(sqlite3.OperationalError, match="simulated capital failure"):
            with PositionClosure(
                pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
                mode="USER", caller_tenant_id=1,
            ) as closure:
                closure.execute()

    with transaction() as con:
        pos = con.execute("SELECT status FROM positions WHERE id=1").fetchone()
        cap = con.execute("SELECT balance FROM capital WHERE tenant_id=1").fetchone()
    assert pos["status"] == "open"
    assert cap["balance"] == 10000.0


# ---- Invariant 2: ownership-before-lock ----

def test_ownership_check_skips_begin_immediate(fresh_db_with_two_tenants):
    """USER-mode ownership mismatch must NOT open a write transaction."""
    from operators.position_closure import PositionClosure
    from db import transaction as tx_module

    begin_count = {"n": 0}
    original_transaction = tx_module.transaction

    from contextlib import contextmanager

    @contextmanager
    def counting_transaction():
        begin_count["n"] += 1
        with original_transaction() as con:
            yield con

    with patch.object(tx_module, "transaction", counting_transaction):
        with PositionClosure(
            pos_id=2, exit_price=220.0, exit_reason="MANUAL",
            mode="USER", caller_tenant_id=1,
        ) as closure:
            outcome = closure.execute()

    assert outcome.status == "not_found"
    assert begin_count["n"] == 0


# ---- Invariant 3: IDOR-equivalence in USER mode ----

def test_idor_equivalent_not_found_for_missing_and_other_tenant(fresh_db_with_two_tenants):
    """USER mode: missing row and other-tenant row both return identical NOT_FOUND."""
    from operators.position_closure import PositionClosure

    with PositionClosure(
        pos_id=9999, exit_price=100.0, exit_reason="MANUAL",
        mode="USER", caller_tenant_id=1,
    ) as closure:
        missing = closure.execute()

    with PositionClosure(
        pos_id=2, exit_price=220.0, exit_reason="MANUAL",
        mode="USER", caller_tenant_id=1,
    ) as closure:
        other_tenant = closure.execute()

    assert missing.status == "not_found"
    assert other_tenant.status == "not_found"
    assert missing.position is None
    assert other_tenant.position is None


# ---- Invariant 4: SYSTEM mode no-IDOR-leak ----

def test_system_mode_closes_correct_tenants_capital(fresh_db_with_two_tenants):
    """SYSTEM mode applies P&L to the position's owning tenant, not cross-leaked."""
    from operators.position_closure import PositionClosure

    with PositionClosure(
        pos_id=2, exit_price=220.0, exit_reason="TP_HIT",
        mode="SYSTEM",
    ) as closure:
        outcome = closure.execute()

    assert outcome.status == "closed"
    with transaction() as con:
        cap1 = con.execute("SELECT balance FROM capital WHERE tenant_id=1").fetchone()
        cap2 = con.execute("SELECT balance FROM capital WHERE tenant_id=2").fetchone()
    assert cap1["balance"] == 10000.0
    assert cap2["balance"] != 5000.0


# ---- Invariant 5: no post-commit side-effect on exception ----

def test_no_post_commit_side_effects_on_exception(fresh_db_with_two_tenants):
    """If execute() raises, none of the 4 post-commit side-effects fire."""
    from operators.position_closure import PositionClosure
    from db import capital as capital_module

    with patch.object(capital_module, "apply_pnl_to_capital",
                      side_effect=sqlite3.OperationalError("boom")):
        with patch("operators.position_closure._write_position_event_log") as ev, \
             patch("operators.position_closure.trigger_health_evaluation") as he, \
             patch("operators.position_closure.notify") as nf, \
             patch("operators.position_closure.update_positions_json") as up:
            with pytest.raises(sqlite3.OperationalError):
                with PositionClosure(
                    pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
                    mode="USER", caller_tenant_id=1,
                ) as closure:
                    closure.execute()

    assert ev.call_count == 0
    assert he.call_count == 0
    assert nf.call_count == 0
    assert up.call_count == 0


# ---- Invariant 6: single side-effect firing on success ----

def test_each_side_effect_fires_exactly_once_on_success(fresh_db_with_two_tenants):
    """Each of the 4 post-commit side-effects is invoked exactly once."""
    from operators.position_closure import PositionClosure

    with patch("operators.position_closure._write_position_event_log") as ev, \
         patch("operators.position_closure.trigger_health_evaluation") as he, \
         patch("operators.position_closure.notify") as nf, \
         patch("operators.position_closure.update_positions_json") as up:
        with PositionClosure(
            pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
            mode="USER", caller_tenant_id=1,
        ) as closure:
            closure.execute()

    assert ev.call_count == 1
    assert he.call_count == 1
    assert nf.call_count == 1
    assert up.call_count == 1


# ---- Invariant 7: idempotent re-close ----

def test_idempotent_reclose_returns_already_closed(fresh_db_with_two_tenants):
    """Closing an already-closed position returns ALREADY_CLOSED, no re-fire."""
    from operators.position_closure import PositionClosure

    with PositionClosure(
        pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
        mode="USER", caller_tenant_id=1,
    ) as closure:
        closure.execute()

    with transaction() as con:
        cap_after_first = con.execute(
            "SELECT balance FROM capital WHERE tenant_id=1"
        ).fetchone()["balance"]

    with patch("operators.position_closure.notify") as nf:
        with PositionClosure(
            pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
            mode="USER", caller_tenant_id=1,
        ) as closure:
            second_outcome = closure.execute()

    assert second_outcome.status == "already_closed"
    assert nf.call_count == 0
    with transaction() as con:
        cap_after_second = con.execute(
            "SELECT balance FROM capital WHERE tenant_id=1"
        ).fetchone()["balance"]
    assert cap_after_first == cap_after_second


# ---- Invariant 8: no writer-lock held across I/O ----

def test_no_writer_lock_held_across_post_commit_side_effects(fresh_db_with_two_tenants):
    """Post-commit side-effects fire strictly after transaction() exits."""
    from operators.position_closure import PositionClosure
    from db import transaction as tx_module

    tx_exit_time = {"t": None}
    side_effect_times = {"ev": None, "he": None, "nf": None, "up": None}

    original_transaction = tx_module.transaction
    from contextlib import contextmanager

    @contextmanager
    def timed_transaction():
        with original_transaction() as con:
            yield con
        import time
        tx_exit_time["t"] = time.perf_counter()

    def record_time(name):
        def _fn(*args, **kwargs):
            import time
            side_effect_times[name] = time.perf_counter()
        return _fn

    with patch.object(tx_module, "transaction", timed_transaction), \
         patch("operators.position_closure._write_position_event_log", record_time("ev")), \
         patch("operators.position_closure.trigger_health_evaluation", record_time("he")), \
         patch("operators.position_closure.notify", record_time("nf")), \
         patch("operators.position_closure.update_positions_json", record_time("up")):
        with PositionClosure(
            pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
            mode="USER", caller_tenant_id=1,
        ) as closure:
            closure.execute()

    assert tx_exit_time["t"] is not None
    for name, t in side_effect_times.items():
        assert t is not None, f"side-effect {name} was not called"
        assert t >= tx_exit_time["t"], f"side-effect {name} fired during tx"


# ---- Invariant 9: no-tenant capital skip ----

def test_legacy_null_tenant_position_close_skips_capital(fresh_db_with_two_tenants):
    """Closing a tenant_id=NULL legacy row commits but skips capital UPSERT."""
    from operators.position_closure import PositionClosure

    with transaction() as con:
        con.execute(
            """INSERT INTO positions
               (id, symbol, direction, entry_price, qty, sl_price, tp_price,
                status, entry_ts, tenant_id, atr_entry, be_mult)
               VALUES (99, 'LEGACY', 'long', 100.0, 1.0, 95.0, 110.0,
                       'open', ?, NULL, 2.0, 1.5)""",
            (datetime.now(timezone.utc).isoformat(),),
        )

    with PositionClosure(
        pos_id=99, exit_price=110.0, exit_reason="TP_HIT",
        mode="SYSTEM",
    ) as closure:
        outcome = closure.execute()

    assert outcome.status == "closed"
    with transaction() as con:
        pos = con.execute("SELECT status FROM positions WHERE id=99").fetchone()
        # No capital row should exist for tenant=NULL.
        cap_null = con.execute("SELECT * FROM capital WHERE tenant_id IS NULL").fetchall()
    assert pos["status"] == "closed"
    assert cap_null == []


# ---- Invariant 10: post-commit side-effect failure does NOT rollback close ----

def test_side_effect_failure_does_not_rollback_close(fresh_db_with_two_tenants):
    """Best-effort post-commit contract: if any of the 4 __exit__ side-effects
    raises, the close + capital remain durably committed. Encodes the
    Voronov AMBER F2 finding — the 'best-effort' commitment must live in
    the test suite, not only in prose. A future contributor who 'fixes' the
    try/except in __exit__ to raise will fail this test."""
    from operators.position_closure import PositionClosure

    # Force each of the 4 post-commit side-effects to raise. None of them
    # should rollback the close (impossible — already committed) and none
    # should propagate to the caller.
    with patch("operators.position_closure._write_position_event_log",
               side_effect=IOError("event log down")), \
         patch("operators.position_closure.trigger_health_evaluation",
               side_effect=RuntimeError("health module crashed")), \
         patch("operators.position_closure.notify",
               side_effect=ConnectionError("telegram unreachable")), \
         patch("operators.position_closure.update_positions_json",
               side_effect=OSError("disk full")):
        with PositionClosure(
            pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
            mode="USER", caller_tenant_id=1,
        ) as closure:
            outcome = closure.execute()

    # Caller observes a clean close. All four side-effect failures were
    # swallowed and logged as WARN by __exit__.
    assert outcome.status == "closed"
    with transaction() as con:
        pos = con.execute("SELECT status FROM positions WHERE id=1").fetchone()
        cap = con.execute("SELECT balance FROM capital WHERE tenant_id=1").fetchone()
    assert pos["status"] == "closed"
    assert cap["balance"] != 10000.0  # P&L was applied in the in-tx step
```

- [ ] **Step 3: Verify all 10 tests fail with ModuleNotFoundError**

Run: `pytest tests/operators/test_position_closure.py -v 2>&1 | tail -20`
Expected: `ModuleNotFoundError: No module named 'operators'` (collection error).

- [ ] **Step 4: Commit**

```bash
git add tests/operators/
git commit -m "test(operators): add 10 invariant tests for PositionClosure (anchors #451)

Invariant 10 encodes Voronov AMBER F2: post-commit side-effect failure
does NOT rollback the close. Locks the 'best-effort log-and-continue'
contract in the test suite so a future 'fix' that turns the try/except
into raise fails loudly."

---

### Task 3: Create the operators package + CloseOutcome dataclass + read_only_connection helper

**Files:**
- Create: `operators/__init__.py`
- Create: `operators/position_closure.py` (skeleton only — full implementation in Task 4)
- Modify: `db/transaction.py` (add `read_only_connection()` context manager — names the 4th access pattern per Voronov AMBER F6)

- [ ] **Step 1: Create empty package marker**

Create `operators/__init__.py` as an empty file.

- [ ] **Step 2: Add `read_only_connection()` to `db/transaction.py`**

Append to `db/transaction.py` (after `transaction()`):

```python
@contextmanager
def read_only_connection() -> Iterator[sqlite3.Connection]:
    """Open a configured connection for read-only work outside any transaction.

    Use when an operator needs pre-validation reads (ownership check,
    existence check) that must NOT hold a writer lock. The connection
    closes on exit; no BEGIN/COMMIT is issued.

    Caller contract:
    - MAY use con.execute for SELECT.
    - MUST NOT issue INSERT/UPDATE/DELETE — if SQLite's autocommit triggers,
      the write happens without the operator's atomicity guarantee.
    - MUST NOT escape the connection past the `with` block.
    """
    con = _open_configured_connection()
    try:
        yield con
    finally:
        con.close()
```

- [ ] **Step 3: Create `operators/position_closure.py` with `CloseOutcome` dataclass and class skeleton**

```python
"""PositionClosure — business operator for closing a position.

Implements the contract specified in
docs/superpowers/analysis/2026-05-25-446-preconditions-synthesis.md
Section 'PositionClosure operator contract spec'.

Single legal entry point for closing a position. The only caller of
transaction() in the close-flow. db/* helpers are pure SQL and receive
con from this operator.

NOTE on `mode` parameter (Voronov AMBER F1): `mode` is a Literal flag
that bifurcates ownership-check behavior (USER enforces, SYSTEM skips).
This works for the two modes present today. If a third mode ever emerges
(BATCH, RECONCILIATION, etc.), the flag-based dispatcher becomes a
homograph of the _tx_or_use pattern this PR closed. Resolve at that
point by splitting into subclasses (`UserPositionClosure`,
`SystemPositionClosure`) sharing an abstract base where the ownership
contract lives in the type, not in a runtime branch.
"""
from __future__ import annotations
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import sqlite3
from typing import Literal, Optional

from db.transaction import transaction
from db.connection import _open_configured_connection

log = logging.getLogger("operators.position_closure")

_VALID_EXIT_REASONS = frozenset({"MANUAL", "SL_HIT", "TP_HIT", "TIME_LIMIT_HIT"})


@dataclass(frozen=True)
class CloseOutcome:
    status: Literal["closed", "not_found", "already_closed"]
    position: Optional[dict]
    pnl_usd: Optional[float]
    pnl_pct: Optional[float]


class PositionClosure:
    """See module docstring."""

    def __init__(
        self,
        pos_id: int,
        exit_price: float,
        exit_reason: str,
        *,
        mode: Literal["USER", "SYSTEM"],
        caller_tenant_id: Optional[int] = None,
        cfg: Optional[dict] = None,
        now: Optional[datetime] = None,
    ) -> None:
        if mode not in ("USER", "SYSTEM"):
            raise ValueError(f"mode must be 'USER' or 'SYSTEM', got {mode!r}")
        if mode == "USER":
            if caller_tenant_id is None or caller_tenant_id <= 0:
                raise ValueError("USER mode requires caller_tenant_id > 0")
        if mode == "SYSTEM" and caller_tenant_id is not None:
            raise ValueError("SYSTEM mode forbids caller_tenant_id (got %r)" % caller_tenant_id)
        if exit_price <= 0:
            raise ValueError(f"exit_price must be > 0, got {exit_price}")
        if exit_reason not in _VALID_EXIT_REASONS:
            raise ValueError(
                f"exit_reason must be one of {sorted(_VALID_EXIT_REASONS)}, got {exit_reason!r}"
            )

        self._pos_id = pos_id
        self._exit_price = exit_price
        self._exit_reason = exit_reason
        self._mode = mode
        self._caller_tenant_id = caller_tenant_id
        self._cfg = cfg
        self._now = now or datetime.now(timezone.utc)

        self._state: Literal["INIT", "NOT_FOUND", "ALREADY_CLOSED", "OK_TO_PROCEED"] = "INIT"
        self._pre_row: Optional[dict] = None
        self._result_row: Optional[dict] = None
        self._consumed = False

    def __enter__(self) -> "PositionClosure":
        raise NotImplementedError  # Filled in Task 4

    def execute(self) -> CloseOutcome:
        raise NotImplementedError  # Filled in Task 4

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        raise NotImplementedError  # Filled in Task 4
```

- [ ] **Step 4: Verify imports work**

Run: `python -c "from operators.position_closure import PositionClosure, CloseOutcome; from db.transaction import read_only_connection, transaction; print('ok')"`
Expected: `ok`.

Run: `pytest tests/operators/test_position_closure.py -v 2>&1 | tail -10`
Expected: tests collect, fail with `NotImplementedError` instead of `ModuleNotFoundError`.

- [ ] **Step 5: Commit**

```bash
git add operators/ db/transaction.py
git commit -m "feat(operators): skeleton for PositionClosure + CloseOutcome dataclass + read_only_connection helper

read_only_connection() names the 4th DB access pattern (read outside
any tx, no BEGIN, close on exit). PositionClosure.__enter__ will use
it for pre-validation reads (Voronov AMBER F6)."
```

---

### Task 4: Implement PositionClosure lifecycle + add db_get_position_by_id + db_close_position_sql

**Files:**
- Modify: `operators/position_closure.py`
- Modify: `db/positions.py`

This task does three things together because they form one logical unit (the operator + the two pure helpers it needs that don't exist yet).

- [ ] **Step 1: Add `db_get_position_by_id` to `db/positions.py`**

Open `db/positions.py`. After the existing `db_get_positions` function (or wherever helpers are grouped), add:

```python
def db_get_position_by_id(con: sqlite3.Connection, pos_id: int) -> Optional[dict]:
    """Read a single position by id. Pure SQL — no tenant filter.

    Caller is responsible for tenant ownership check (per Task 8.5 design
    where helpers are pure SQL operators; ownership lives in the business
    operator).
    """
    row = con.execute("SELECT * FROM positions WHERE id = ?", (pos_id,)).fetchone()
    return dict(row) if row else None
```

If `sqlite3` and `Optional` are not yet imported at module top, ensure they are:

```python
import sqlite3
from typing import Optional
```

- [ ] **Step 2: Add `db_close_position_sql` to `db/positions.py`**

In `db/positions.py`, add (typically after `db_close_position` so the diff stays local):

```python
def db_close_position_sql(
    con: sqlite3.Connection,
    pos_id: int,
    exit_price: float,
    exit_reason: str,
    exit_ts: str,
    pnl_usd: float,
    pnl_pct: float,
) -> dict:
    """Pure SQL: UPDATE the position to closed. Returns the updated row.

    No health trigger, no notify, no logging beyond ERROR. Caller (operator)
    owns lifecycle, transaction, and side-effects.
    """
    con.execute(
        """UPDATE positions
           SET status = 'closed',
               exit_price = ?,
               exit_ts = ?,
               exit_reason = ?,
               pnl_usd = ?,
               pnl_pct = ?
           WHERE id = ?""",
        (exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct, pos_id),
    )
    row = con.execute("SELECT * FROM positions WHERE id = ?", (pos_id,)).fetchone()
    return dict(row)
```

- [ ] **Step 3: Implement `__enter__`, `execute()`, and `__exit__` in `operators/position_closure.py`**

Replace the three `NotImplementedError` stubs with real implementations. Update imports at the top of the file (drop `_open_configured_connection` direct import; add `read_only_connection` instead):

```python
from db.transaction import transaction, read_only_connection
from db.positions import db_get_position_by_id, db_close_position_sql, _calc_pnl
from db.capital import apply_pnl_to_capital
from api.positions import _write_position_event_log, update_positions_json
from health import trigger_health_evaluation
from notifier import notify
from api.config import load_config
```

Remove the `from contextlib import closing` and `from db.connection import _open_configured_connection` lines that were in the skeleton (Task 3) — they're no longer needed.

Then the three methods:

```python
    def __enter__(self) -> "PositionClosure":
        if self._consumed:
            raise RuntimeError("PositionClosure is single-use; construct a new one")
        # Pre-validation read outside any write transaction (no lock contention).
        with read_only_connection() as con:
            self._pre_row = db_get_position_by_id(con, self._pos_id)
        if self._pre_row is None:
            self._state = "NOT_FOUND"
            return self
        if self._mode == "USER":
            if self._pre_row.get("tenant_id") != self._caller_tenant_id:
                self._state = "NOT_FOUND"  # IDOR-safe collapse
                return self
        if self._pre_row.get("status") != "open":
            self._state = "ALREADY_CLOSED"
            return self
        self._state = "OK_TO_PROCEED"
        return self

    def execute(self) -> CloseOutcome:
        if self._consumed:
            raise RuntimeError("PositionClosure already executed; single-use")
        self._consumed = True

        if self._state == "NOT_FOUND":
            return CloseOutcome(status="not_found", position=None, pnl_usd=None, pnl_pct=None)
        if self._state == "ALREADY_CLOSED":
            return CloseOutcome(
                status="already_closed",
                position=self._pre_row,
                pnl_usd=None,
                pnl_pct=None,
            )
        # OK_TO_PROCEED
        with transaction() as con:
            # Re-select inside the write tx to cover the race window.
            row = db_get_position_by_id(con, self._pos_id)
            if row is None:
                return CloseOutcome(status="not_found", position=None, pnl_usd=None, pnl_pct=None)
            if row.get("status") != "open":
                self._result_row = row
                return CloseOutcome(
                    status="already_closed", position=row, pnl_usd=None, pnl_pct=None,
                )

            pnl_usd, pnl_pct = _calc_pnl(
                row["direction"], row["entry_price"], self._exit_price, row["qty"],
            )
            exit_ts = self._now.isoformat()
            closed_row = db_close_position_sql(
                con, self._pos_id, self._exit_price, self._exit_reason,
                exit_ts, pnl_usd, pnl_pct,
            )
            tenant_id = closed_row.get("tenant_id")
            if tenant_id is not None and pnl_usd is not None:
                apply_pnl_to_capital(con, tenant_id, pnl_usd)
            elif tenant_id is None:
                log.warning(
                    "PositionClosure: skipping capital roll-in for legacy tenant_id=NULL pos_id=%s",
                    self._pos_id,
                )
            self._result_row = closed_row
            self._result_pnl = (pnl_usd, pnl_pct)
        # Transaction committed here.
        return CloseOutcome(
            status="closed",
            position=self._result_row,
            pnl_usd=self._result_pnl[0],
            pnl_pct=self._result_pnl[1],
        )

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None:
            log.error(
                "PositionClosure failed: pos_id=%s mode=%s caller_tenant_id=%s "
                "exit_reason=%s exit_price=%s exception=%s",
                self._pos_id, self._mode, self._caller_tenant_id,
                self._exit_reason, self._exit_price, exc_type.__name__,
            )
            return False  # propagate

        if self._result_row is None:
            # NOT_FOUND or ALREADY_CLOSED path — no side-effects to fire.
            return False

        cfg = self._cfg or load_config()
        # 1) event log
        try:
            _write_position_event_log(
                self._result_row, self._exit_reason, self._exit_price,
            )
        except Exception as e:  # pragma: no cover - best-effort
            log.warning("PositionClosure: event log failed: %s", e)
        # 2) health trigger
        try:
            trigger_health_evaluation(self._result_row["symbol"], cfg)
        except Exception as e:  # pragma: no cover
            log.warning("PositionClosure: health trigger failed: %s", e)
        # 3) notify
        try:
            from notifier import PositionExitEvent  # local import: notifier import is slow
            notify(
                PositionExitEvent(
                    position=self._result_row,
                    exit_reason=self._exit_reason,
                    exit_price=self._exit_price,
                    pnl_usd=self._result_pnl[0],
                ),
                cfg=cfg,
            )
        except Exception as e:  # pragma: no cover
            log.warning("PositionClosure: notify failed: %s", e)
        # 4) positions snapshot
        try:
            update_positions_json()
        except Exception as e:  # pragma: no cover
            log.warning("PositionClosure: positions snapshot failed: %s", e)
        return False
```

**Note:** the local import `from notifier import PositionExitEvent` may need adjustment depending on the actual notifier API. If `notify()` accepts a dict instead of an event object, adapt. Check `notifier/__init__.py` for the public API before finalizing this code.

- [ ] **Step 4: Run the 9 invariant tests**

Run: `pytest tests/operators/test_position_closure.py -v 2>&1 | tail -30`
Expected: all 9 tests pass. If any fail, investigate before committing.

If `notify()` signature doesn't match the assumed API, you'll see test failures. Adjust the operator's `__exit__` to match the real signature; do NOT change the notifier itself in this task.

- [ ] **Step 5: Smoke check that existing tests didn't regress**

Run: `pytest tests/api/ tests/db/ -q --tb=no 2>&1 | tail -10`
Expected: no new failures.

- [ ] **Step 6: Commit**

```bash
git add db/positions.py operators/position_closure.py
git commit -m "feat(operators): implement PositionClosure lifecycle + pure SQL helpers (db_get_position_by_id, db_close_position_sql)

The 9 invariant tests now pass. db_close_position dual-contract function
still exists; it's removed in Task 6 after callers migrate."
```

---

### Task 5: Make apply_pnl_to_capital + db_get_capital + db_upsert_capital take con mandatory

**Files:**
- Modify: `db/capital.py`
- Modify: any callers that currently call these without `con`.

These three helpers are called by `PositionClosure` (Task 4) and by other callers in the codebase. We're making the contract explicit: helpers don't open transactions.

- [ ] **Step 1: Grep all callers of these three functions**

```bash
grep -rn "apply_pnl_to_capital\|db_get_capital\|db_upsert_capital" --include="*.py" .
```

Note every callsite. Classify each: (a) already passes `con`, (b) doesn't pass `con` and relies on helper opening tx.

- [ ] **Step 2: Change signatures in `db/capital.py`**

For each of `apply_pnl_to_capital`, `db_get_capital`, `db_upsert_capital`, `db_list_active_tenant_ids`:

Before:
```python
def db_get_capital(tenant_id: int, con: Optional[sqlite3.Connection] = None) -> Optional[dict]:
    with _tx_or_use(con) as con:
        ...
```

After:
```python
def db_get_capital(con: sqlite3.Connection, tenant_id: int) -> Optional[dict]:
    row = con.execute("SELECT * FROM capital WHERE tenant_id = ?", (tenant_id,)).fetchone()
    return dict(row) if row else None
```

Notes:
- `con` becomes positional first arg.
- `_tx_or_use(con)` block removed; SQL executes directly on `con`.
- Type hint: `sqlite3.Connection` (no Optional).
- `apply_pnl_to_capital` previously called itself via auto-recursion with `inner_con` — flatten: with mandatory `con`, the function just reads + writes inline (no `_tx_or_use(None)` recursive call).

- [ ] **Step 3: Update every caller identified in Step 1**

For each callsite (a) that already passes `con`: change call to positional, e.g., `db_get_capital(con, tenant_id)`. For each (b) that doesn't: wrap in `with transaction() as con:` block, then call `db_get_capital(con, tenant_id)`. Update keyword arg style to positional throughout.

- [ ] **Step 4: Run capital and positions tests**

Run: `pytest tests/ -k "capital or positions" --tb=short 2>&1 | tail -30`
Expected: pass.

Run the 9 invariant tests too:
Run: `pytest tests/operators/test_position_closure.py -v 2>&1 | tail -15`
Expected: still 9/9 pass.

- [ ] **Step 5: Commit**

```bash
git add db/capital.py api/ db/ scripts/ tests/ scanner/ strategy/ health.py
git commit -m "refactor(capital): apply_pnl_to_capital + db_get/upsert_capital require con mandatory

Helpers are pure Cat. 1 SQL operators. _tx_or_use is removed from these
helpers (still present elsewhere; full removal in Task 13)."
```

---

### Task 6: Migrate api/positions.py callers to PositionClosure

**Files:**
- Modify: `api/positions.py`

Two callers migrate: the user-facing endpoint and the scanner.

- [ ] **Step 1: Migrate `close_position` endpoint (USER mode)**

Find the existing `close_position` endpoint function in `api/positions.py` (around lines 390-413). Replace its body with:

```python
@router.post("/positions/{pos_id}/close")
async def close_position(
    pos_id: int,
    request: Request,
    tenant_id: int = Depends(get_current_tenant_id),
):
    """Close a position. USER-mode PositionClosure handles all the
    choreography (atomicity, ownership, post-commit side-effects)."""
    from operators.position_closure import PositionClosure

    body = await request.json() if request.headers.get("content-length") else {}
    exit_price = body.get("exit_price")
    exit_reason = body.get("exit_reason", "MANUAL")

    if exit_price is None:
        raise HTTPException(422, "exit_price required")

    with PositionClosure(
        pos_id=pos_id,
        exit_price=float(exit_price),
        exit_reason=exit_reason,
        mode="USER",
        caller_tenant_id=tenant_id,
    ) as closure:
        outcome = closure.execute()

    if outcome.status == "not_found":
        raise HTTPException(404, "position not found")
    if outcome.status == "already_closed":
        return {"ok": True, "position": outcome.position, "already_closed": True}
    return {"ok": True, "position": outcome.position}
```

Adjust to match the real endpoint signature (path params, body schema, response model) in your codebase. The key change is: the body is one `with PositionClosure(...)` block; the four old calls (`db_close_position`, `_write_position_event_log`, `_apply_close_to_capital`, `update_positions_json`) all disappear from here.

- [ ] **Step 2: Migrate `check_position_stops` scanner (SYSTEM mode)**

Find `check_position_stops` in `api/positions.py` (around lines 135-335). The current shape has:
- Outer `with transaction() as con:` for SELECT open positions + trailing-SL UPDATEs + inline `db_close_position(con=con)` + `_apply_close_to_capital(closed, con=con)`.
- Post-tx `post_tx_actions` accumulator that fires event log + health + notify in a loop outside the tx.

New shape (preserve all existing logic — this is structural):

```python
def check_position_stops(symbol, price, now=None, *, symbol_price_overrides=None):
    """Per-tick decision: read open positions for this symbol, apply trailing-SL,
    and close positions that hit SL/TP/TIME_LIMIT. Uses PositionClosure for the
    close-flow so atomicity and side-effects are handled correctly.
    """
    # ... existing kwarg validation, batch-mode dispatch (symbol_price_overrides
    # path), config loading — all unchanged ...

    cfg = load_config()
    now = now or datetime.now(timezone.utc)

    # Phase 1: read open positions + apply trailing-SL writes in one tx.
    # (Trailing-SL is per-tick mutation; not part of the close-flow.)
    pos_list_to_close = []
    with transaction() as con:
        rows = con.execute(
            "SELECT * FROM positions WHERE symbol = ? AND status = 'open'",
            (symbol,),
        ).fetchall()
        pos_list = [dict(r) for r in rows]
        for pos in pos_list:
            # Existing trailing-SL ratchet logic — unchanged
            new_sl = _compute_trailing_sl(pos, price)  # or whatever the existing helper is
            if new_sl is not None and new_sl > pos["sl_price"]:
                con.execute(
                    "UPDATE positions SET sl_price = ? WHERE id = ?",
                    (new_sl, pos["id"]),
                )
                pos["sl_price"] = new_sl

            # Decision: should this position close?
            reason, exit_price = _decide_exit(pos, price, now, cfg)  # or equivalent
            if reason:
                pos_list_to_close.append((pos["id"], exit_price, reason))
    # Trailing-SL writes are now durable.

    # Phase 2: close each marked position via PositionClosure in SYSTEM mode.
    # Each closure is its own atomic close + capital tx + post-commit side-effects.
    from operators.position_closure import PositionClosure
    for pos_id, exit_price, reason in pos_list_to_close:
        try:
            with PositionClosure(
                pos_id=pos_id,
                exit_price=exit_price,
                exit_reason=reason,
                mode="SYSTEM",
                cfg=cfg,
                now=now,
            ) as closure:
                closure.execute()
        except Exception:
            log.exception("PositionClosure failed for pos_id=%s", pos_id)
            continue
```

Delete:
- `_apply_close_to_capital` shim function (lines ~52-80). The operator now owns capital roll-in.
- `post_tx_actions` accumulator and the post-tx loop that drains it.
- Any local imports of `trigger_health_evaluation`, `notify`, etc. that existed only for the post-tx loop.

If your codebase has helpers named differently than `_compute_trailing_sl` or `_decide_exit`, use the real names. Do not invent — preserve the existing decision logic.

- [ ] **Step 3: Run the regression test for the trading invariant**

Run: `pytest tests/api/test_check_position_stops_atomicity.py -v 2>&1 | tail -20`
Expected: PASS.

Run: `pytest tests/api/ tests/operators/ --tb=short 2>&1 | tail -30`
Expected: no regressions.

- [ ] **Step 4: Commit**

```bash
git add api/positions.py
git commit -m "feat(positions): migrate close_position endpoint + check_position_stops to PositionClosure operator

Resolves #446 for the close-flow specifically. Both callers now use the
operator; _apply_close_to_capital shim deleted; post_tx_actions loop
deleted. The trailing-SL writes still live in their own per-tick tx
(intentional separation: trailing is price-tick, close is business
transition)."
```

---

### Task 7: Delete the old db_close_position function

**Files:**
- Modify: `db/positions.py`
- Modify: any test files that called the old function.

- [ ] **Step 1: Grep for remaining callers**

```bash
grep -rn "db_close_position\b" --include="*.py" .
```

Should show no callers other than `db_close_position_sql` (added in Task 4) and the old `db_close_position` definition. If any production caller remains, it was missed — migrate it to `PositionClosure` before deleting.

If tests directly call the old `db_close_position`, decide per test: (a) does the test verify the close-flow semantics? → migrate to use `PositionClosure`. (b) Does it verify the pure SQL UPDATE? → migrate to use `db_close_position_sql`. (c) Was it testing the `_caller_owned_con` branch? → delete as patology (per Voronov's "tests that resist migration are testing the patology").

- [ ] **Step 2: Delete the old `db_close_position` function from `db/positions.py`**

Remove the entire `def db_close_position(...)` block including the `_caller_owned_con` logic, the `_tx_or_use(con)` wrapper, the standalone-mode health trigger import, and the docstring.

- [ ] **Step 3: Verify production grep is clean**

```bash
grep -rn "db_close_position\b" --include="*.py" . | grep -v "db_close_position_sql"
```
Expected: empty.

```bash
grep -rn "_caller_owned_con" --include="*.py" .
```
Expected: empty.

- [ ] **Step 4: Run full test suite, note pass/fail counts**

Run: `pytest tests/ --tb=no -q 2>&1 | tail -5`
Note the numbers. Compare to baseline (2502 collected). Expect some test fixes needed in Task 8 if callsites changed signature.

- [ ] **Step 5: Commit**

```bash
git add db/positions.py tests/
git commit -m "refactor(positions): delete db_close_position dual-contract function

All callers migrated to PositionClosure (production) or
db_close_position_sql (pure SQL tests). The _caller_owned_con pattern
and the standalone-mode health trigger are gone from db/."
```

---

### Task 8: Mechanical sweep — db/ helpers (Cat. 1) to con mandatory

**Files:**
- Modify: `db/positions.py` (remaining helpers), `db/signals.py`, `db/schema.py`, `db/auth_schema.py`, `db/user_preferences.py`.
- Modify: all callers of those helpers that don't already pass `con`.

This is the biggest mechanical task. Apply the same pattern uniformly.

- [ ] **Step 1: For each helper in scope, change the signature**

For each of:

`db/positions.py`: `db_create_position`, `db_last_exit_ts`, `db_get_positions`, `db_update_position`
`db/signals.py`: `get_scans`, `get_latest_scan_per_symbol`, `get_latest_signal`, `get_latest_scan`, `get_signals_summary`
`db/schema.py`: `backfill_tenant`, `_migrate_multi_tenant_b1`, `_migrate_agent_audit`, `_migrate_agent_history`
`db/auth_schema.py`: `init_auth_db`, `has_any_user`, `init_system_state`, `is_setup_completed`, `mark_setup_completed`
`db/user_preferences.py`: `db_get_user_preferences`, `db_upsert_user_preferences`

Pattern:

Before:
```python
def db_X(arg1, arg2, con: Optional[sqlite3.Connection] = None) -> ReturnType:
    with _tx_or_use(con) as con:
        # SQL body
```

After:
```python
def db_X(con: sqlite3.Connection, arg1, arg2) -> ReturnType:
    # SQL body (one less indent)
```

- [ ] **Step 2: For each helper, audit and fix callsites**

For each helper above, run:
```bash
grep -rn "helper_name(" --include="*.py" .
```

For each callsite:
- If caller already passes `con` as kwarg: change to positional first arg.
- If caller doesn't pass `con`: wrap the call in `with transaction() as con:` and pass `con` positionally.

Caller categories typically seen:
- Endpoints in `api/*.py` → wrap in `with transaction()` block.
- Scripts in `scripts/*.py` → wrap.
- Tests in `tests/*.py` → wrap.
- Other helpers (e.g., `init_db` calling `_migrate_*`) → already inside a `transaction()`, just pass `con`.

- [ ] **Step 3: Smoke test after each module migration**

After finishing each module (`db/positions.py`, then `db/signals.py`, etc.), run:
```bash
pytest tests/ -k "<module_keyword>" --tb=short 2>&1 | tail -20
```

E.g. after `db/signals.py`: `pytest tests/ -k "signal or scan" --tb=short 2>&1 | tail -20`.

Fix any failures before moving to the next module.

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ --tb=no -q 2>&1 | tail -5`
Expected: pass count restored to baseline (2502) or better.

- [ ] **Step 5: Commit**

```bash
git add db/ api/ scripts/ scanner/ strategy/ tests/ health.py auth/ notifier/
git commit -m "refactor(db): Cat. 1 helpers in db/ require con mandatory

db_create_position, db_get_positions, db_last_exit_ts, db_update_position,
get_scans, get_latest_*, get_signals_summary, backfill_tenant, _migrate_*,
init_auth_db, has_any_user, init_system_state, is_setup_completed,
mark_setup_completed, db_get_user_preferences, db_upsert_user_preferences
— all now take con: sqlite3.Connection (no Optional).

Callers wrap in 'with transaction() as con:' where they did not before."
```

---

### Task 9: Mechanical sweep — auth/, notifier/, health/, strategy/ helpers

**Files:**
- Modify: `auth/tokens.py`, `auth/audit.py`, `notifier/_storage.py`, `notifier/dedupe.py`, `notifier/dispatch_per_user.py`, `health.py`, `strategy/kill_switch_v2_shadow.py`.
- Modify: all callers of those helpers that don't already pass `con`.

Same pattern as Task 8.

- [ ] **Step 1: Apply the signature pattern to each helper**

For each of:

`auth/tokens.py`: `create_refresh_token`, `lookup_refresh`, `revoke_refresh`, `revoke_family`, `revoke_all_for_user`
`notifier/_storage.py`: `record_delivery`, `list_unread`, `mark_read`, `mark_all_read`
`notifier/dedupe.py`: `should_send`
`notifier/dispatch_per_user.py`: `_list_active_users`
`health.py`: `_get_symbol_health_row`, `record_portfolio_transition`, `recent_portfolio_transitions`
`strategy/kill_switch_v2_shadow.py`: `_load_closed_trades`, `_load_open_positions`

Apply same Before/After pattern as Task 8.

- [ ] **Step 2: For each, audit and fix callsites**

Same as Task 8 Step 2.

- [ ] **Step 3: Smoke test each module**

After `auth/`: `pytest tests/ -k "auth" --tb=short 2>&1 | tail -20`
After `notifier/`: `pytest tests/ -k "notifier or dedupe or dispatch" --tb=short 2>&1 | tail -20`
After `health.py` + `strategy/`: `pytest tests/ -k "health or kill_switch" --tb=short 2>&1 | tail -20`

- [ ] **Step 4: Full suite**

Run: `pytest tests/ --tb=no -q 2>&1 | tail -5`
Expected: pass count restored.

- [ ] **Step 5: Commit**

```bash
git add auth/ notifier/ health.py strategy/ api/ scripts/ tests/
git commit -m "refactor: Cat. 1 helpers in auth/, notifier/, health/, strategy/ require con mandatory

create_refresh_token, lookup_refresh, revoke_*, record_delivery, list_unread,
mark_read, mark_all_read, should_send, _list_active_users,
_get_symbol_health_row, record_portfolio_transition, recent_portfolio_transitions,
_load_closed_trades, _load_open_positions — all now take con: sqlite3.Connection.

Callers wrap in 'with transaction() as con:' where they did not before."
```

---

### Task 10: Migrate Cat. 2 hidden operators from _tx_or_use to direct transaction()

**Files:**
- Modify: `db/signals.py` (`save_scan`), `db/schema.py` (`init_db`), `auth/audit.py` (`log_auth_event`), `notifier/dispatch_per_user.py` (`dispatch_signal_to_users`).

These 4 functions stay structurally where they are (operator-extraction is a separate ticket per the preconditions synthesis), but they cannot continue using `_tx_or_use` after it's deleted in Task 13. Migrate them to `transaction()` directly.

- [ ] **Step 1: For each Cat. 2 hidden operator, replace `_tx_or_use(...)` with `transaction()`**

Pattern: inside the function body, where `_tx_or_use(con)` appears (typically with `con=None` since these are top-level orchestrators), replace with `transaction()`:

Before:
```python
def save_scan(scan_data):
    with _tx_or_use(None) as con:
        con.execute("INSERT INTO scans ...", ...)
        scan_id = con.lastrowid
    # Second tx for outcomes
    with _tx_or_use(None) as con:
        ...
```

After:
```python
def save_scan(scan_data):
    with transaction() as con:
        con.execute("INSERT INTO scans ...", ...)
        scan_id = con.lastrowid
    # Second tx for outcomes
    with transaction() as con:
        ...
```

- [ ] **Step 2: Verify each Cat. 2 function still works**

Run targeted tests:
```bash
pytest tests/ -k "save_scan or init_db or log_auth_event or dispatch_signal" --tb=short 2>&1 | tail -20
```
Expected: pass.

- [ ] **Step 3: Grep that the 4 functions no longer reference `_tx_or_use`**

```bash
grep -n "_tx_or_use" db/signals.py db/schema.py auth/audit.py notifier/dispatch_per_user.py
```
Expected: empty.

- [ ] **Step 4: Commit**

```bash
git add db/signals.py db/schema.py auth/audit.py notifier/dispatch_per_user.py
git commit -m "refactor: Cat. 2 hidden operators use transaction() directly, not _tx_or_use

save_scan, init_db, log_auth_event, dispatch_signal_to_users — these
remain hidden business operators (operator-extraction deferred to
separate tickets) but no longer depend on _tx_or_use. Prepares for
_tx_or_use deletion."
```

---

### Task 11: Verify _tx_or_use has no remaining callers and delete it

**Files:**
- Modify: `db/transaction.py`

- [ ] **Step 1: Grep for any remaining `_tx_or_use` callers**

```bash
grep -rn "_tx_or_use" --include="*.py" .
```
Expected: only the definition in `db/transaction.py` and possibly references in docstrings/comments.

If any caller remains in production code, return to Task 8/9/10 and migrate it. Do not delete `_tx_or_use` until grep is clean.

- [ ] **Step 2: Delete `_tx_or_use` from `db/transaction.py`**

Open `db/transaction.py`. Delete:
- The `_tx_or_use` function definition (lines ~60-83).
- Any reference to `_tx_or_use` in module docstring or other comments.

`transaction()` itself remains untouched.

- [ ] **Step 3: Confirm `db/transaction.py` still imports cleanly**

```bash
python -c "from db.transaction import transaction; print('ok')"
```
Expected: `ok`.

- [ ] **Step 4: Run the 9 wrapper contract tests for `transaction()`**

```bash
pytest tests/db/test_transaction.py -v 2>&1 | tail -15
```
Expected: 9/9 pass (no regression).

- [ ] **Step 5: Run the 9 PositionClosure invariant tests**

```bash
pytest tests/operators/test_position_closure.py -v 2>&1 | tail -15
```
Expected: 9/9 pass.

- [ ] **Step 6: Run full suite**

```bash
pytest tests/ --tb=no -q 2>&1 | tail -5
```
Expected: pass count at or near baseline. If any new failures, fix before commit.

- [ ] **Step 7: Commit**

```bash
git add db/transaction.py
git commit -m "refactor(db): delete _tx_or_use — issues #446, #447, #448 closed by construction

All helpers now require con explicitly. PositionClosure is the only
caller of transaction() in the close-flow. _tx_or_use was the fossil
trace of an unnamed layer (Voronov, 2026-05-25); the layer is now
named (operators/) and the fossil is gone."
```

---

### Task 12: Fix the trailing-ratchet atomicity test fixture (#451)

**Files:**
- Modify: `tests/api/test_check_position_stops_atomicity.py`

The existing test was flagged by Serrano (F-13): the fixture's math means the trailing-SL logic doesn't actually fire under the test scenario. The assertion accepts 4 valid outcomes when only 2 are valid. Fix.

- [ ] **Step 1: Read the current test and its fixture**

Run: `cat tests/api/test_check_position_stops_atomicity.py`

Identify:
- The position fixture parameters (entry_price, sl_price, atr_entry, be_mult).
- The price used in the test (108.0 currently).
- The assertion (`row["sl_price"] in {105.0, 100.0, 104.0, 108.0}`).

- [ ] **Step 2: Adjust fixture so the scanner actually competes**

Per Serrano's F-13 analysis: set `entry_price=98, sl_price=97` so `be_threshold = 98 + 3 = 101` and `108 >= 101 AND sl_price(97) < entry(98)` both True → trailing fires.

Update the fixture seed:

```python
with transaction() as con:
    con.execute(
        """INSERT INTO positions
           (id, symbol, direction, entry_price, qty, sl_price, tp_price,
            status, entry_ts, tenant_id, atr_entry, be_mult)
           VALUES (1, 'BTCUSDT', 'long', 98.0, 1.0, 97.0, 110.0,
                   'open', ?, 1, 2.0, 1.5)""",
        (0,),
    )
```

- [ ] **Step 3: Tighten the assertion**

Replace the loose set with exactly two valid outcomes (operator wins → 105.0; scanner wins → the computed trailing-SL value, which under these inputs is `98 + round(2.0 * 1.5, 2) = 101.0`).

```python
assert row["sl_price"] in {105.0, 101.0}, row["sl_price"]
```

Update the comment to reflect that exactly two winners are valid.

- [ ] **Step 4: Run the regression test**

Run: `pytest tests/api/test_check_position_stops_atomicity.py -v 2>&1 | tail -20`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/api/test_check_position_stops_atomicity.py
git commit -m "test(positions): fix trailing-ratchet atomicity fixture to actually trigger SL move (#451)

Previous fixture had be_threshold=103 vs entry=100 with price=108; the
trailing logic was a no-op for the scanner, making the assertion vacuous.
New fixture (entry=98, sl=97) makes both branches of the race
observable: scanner writes 101.0 (computed trailing) or operator writes
105.0 (manual). Atomicity invariant is now actually tested."
```

---

### Task 13: Update CLAUDE.md "Database access" section

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Read the current section**

The "Database access" section was added in the previous PR. It currently describes `transaction()` as the entry point for callers. Replace with the new three-layer model.

- [ ] **Step 2: Rewrite the section**

Replace the existing "Database access" subsection with:

```markdown
## Database access

Three-layer separation:

### 1. Pure SQL helpers (`db/*.py`, `auth/*.py`, etc.)

Receive `con: sqlite3.Connection` as a mandatory first argument. They run SQL and return data. No `transaction()` calls. No side-effects (no HTTP, no file I/O, no logging beyond DEBUG). Examples: `db_close_position_sql`, `db_get_capital`, `apply_pnl_to_capital`, `db_create_position`.

**Documented exceptions** (Cat. 2 hidden business operators living in helper directories — operator-extraction deferred to separate tickets per the rationale in `docs/superpowers/analysis/2026-05-25-446-preconditions-synthesis.md`):

- `db/schema.py::init_db` — bootstrap orchestrator; opens its own `transaction()` and calls migration helpers.
- `db/signals.py::save_scan` — dual-transaction pattern (scan write + outcomes write); calls `transaction()` directly twice.
- `auth/audit.py::log_auth_event` — fallback to stderr if DB write fails; calls `transaction()` directly.
- `notifier/dispatch_per_user.py::dispatch_signal_to_users` — fan-out orchestrator; calls `transaction()` directly and fires `notify()` side-effect.

These four are recognized exceptions today. When their operator-extraction lands, they migrate to `operators/` and this list shrinks.

### 2. Business operators (`operators/*.py`)

Own `transaction()` for one named business transition. Orchestrate side-effects. Declare atomicity. The only legal entry point for the transitions they represent. Currently: `PositionClosure` (closing a position with atomic capital roll-in + post-commit health/notify/event-log/snapshot).

Pattern:
\`\`\`python
from operators.position_closure import PositionClosure

with PositionClosure(
    pos_id=42, exit_price=110.0, exit_reason="TP_HIT",
    mode="USER", caller_tenant_id=tenant_id,
) as closure:
    outcome = closure.execute()
\`\`\`

### 3. Direct `with transaction()` for ad-hoc unit-of-work

When the caller needs a transactional scope around one or more pure SQL helpers but the operation isn't a named business transition, wrap the helpers in `with transaction() as con:` directly:

\`\`\`python
from db.transaction import transaction
from db.signals import get_latest_signal

with transaction() as con:
    sig = get_latest_signal(con, "BTCUSDT")
\`\`\`

### 4. Read-only pre-validation outside any transaction (`read_only_connection()`)

When an operator needs to read state BEFORE deciding whether to open a write transaction (e.g., ownership check that should not acquire a writer lock), use `read_only_connection()` from `db.transaction`:

\`\`\`python
from db.transaction import read_only_connection

with read_only_connection() as con:
    row = db_get_position_by_id(con, pos_id)
# no transaction was opened; no lock held.
\`\`\`

Only used by operators today (`PositionClosure.__enter__`). Pure SQL helpers never call this — they receive `con` from their caller.

New business operators emerge from evidence (caller composes >1 helper + side-effect with conditional behavior), not preemptively. See `docs/superpowers/analysis/2026-05-25-446-tx-or-use-analysis-and-direction.md` for the rationale (Voronov, 2026-05-25).
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: rewrite Database access section for three-layer model (helpers / operators / direct tx)"
```

---

### Task 14: Final verification

**Files:** none modified (unless smoke fixes needed).

- [ ] **Step 1: No `_tx_or_use` anywhere**

```bash
grep -rnE "_tx_or_use|_caller_owned_con" --include="*.py" --include="*.md" --exclude-dir=docs/superpowers/plans --exclude-dir=docs/superpowers/analysis --exclude-dir=.claude .
```
Expected: empty.

- [ ] **Step 2: Full test suite**

```bash
pytest tests/ -q --tb=no 2>&1 | tail -5
```
Expected: 0 failed. Same skip count as baseline (~22, all network). Pass count at or near baseline (~2502).

- [ ] **Step 3: Smoke imports for all migrated modules**

```bash
python -c "
import importlib
modules = [
    'operators.position_closure',
    'db.transaction', 'db.connection', 'db.positions', 'db.capital',
    'db.signals', 'db.schema', 'db.auth_schema', 'db.user_preferences',
    'auth.tokens', 'auth.audit',
    'notifier._storage', 'notifier.dedupe', 'notifier.dispatch_per_user',
    'health', 'strategy.kill_switch_v2_shadow',
    'api.positions',
]
for m in modules:
    importlib.import_module(m)
print('all', len(modules), 'modules import cleanly')
"
```
Expected: `all 17 modules import cleanly`.

- [ ] **Step 4: Confirm PositionClosure tests pass**

```bash
pytest tests/operators/test_position_closure.py -v 2>&1 | tail -15
```
Expected: 9/9 pass.

- [ ] **Step 5: Confirm wrapper contract tests still pass**

```bash
pytest tests/db/test_transaction.py -v 2>&1 | tail -15
```
Expected: 9/9 pass.

- [ ] **Step 6: Confirm trailing-ratchet test passes with the corrected fixture**

```bash
pytest tests/api/test_check_position_stops_atomicity.py -v 2>&1 | tail -10
```
Expected: PASS.

---

### Task 15: Push, close issues, open PR

**Files:** none modified.

**REQUIRES USER CONFIRMATION** — pushes to dad's repo + opens externally-visible PR.

- [ ] **Step 1: Push branch**

```bash
git push -u origin feat/fix-tx-or-use-dual-contract-446
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --repo sssimon/trading-spacial \
  --title "feat: introduce PositionClosure operator + demote db/ to pure SQL layer (closes #446, #447, #448, #449)" \
  --body "$(cat <<'EOF'
## Summary

Implements the architectural direction articulated by Voronov on 2026-05-25 (see analysis docs in `docs/superpowers/analysis/`). `db/` becomes a pure SQL layer where helpers receive `con: sqlite3.Connection` mandatory. Business transitions live in the new `operators/` package; the first inhabitant is `PositionClosure`.

`_tx_or_use` is deleted. All 34 Cat. 1 helpers migrate to `con` mandatory. The 5 Cat. 2 hidden business operators (save_scan, init_db, log_auth_event, dispatch_signal_to_users, ex-db_close_position) use `transaction()` directly; operator-extraction for the remaining 4 is deferred to separate tickets.

## Resolves

- **#446** (`_tx_or_use` dual-contract) — closed by construction; the dispatcher and its symbol both gone.
- **#447** (named operator) — `PositionClosure` is the operator; pattern for future operators established.
- **#448** (`con` validation) — disuelto; helpers no longer receive `con` from external callers without passing through the operator layer or an explicit `with transaction()` block.
- **#449** (health trigger non-enforceable) — health trigger lives in `PositionClosure.__exit__`; called exactly once per successful close; tested by invariant #6.
- **#451** (test laxness) — fixture corrected to actually trigger trailing-SL; assertion tightened to exactly two valid outcomes.

## Resolves with explicit trade-off

- **#450** (`apply_pnl_to_capital` best-effort silencioso) — capital roll-in now runs IN-TX inside the operator's transaction. Trade-off accepted: capital UPSERT failure aborts the close (position stays open). This is strictly safer than the inverse (close commits, capital silently desynced). Documented in `docs/superpowers/analysis/2026-05-25-446-preconditions-synthesis.md` Section 'Post-commit side-effects'.

## Deferred to separate tickets

- Operator-extraction for the 4 remaining Cat. 2 hidden operators: `save_scan`, `init_db`, `log_auth_event`, `dispatch_signal_to_users`. They function correctly with `transaction()` directly; operator-naming is a separate exercise per Voronov's evidential rule.
- `PositionOpening` operator: precondition 2 confirmed no current symptoms (no contract interrogation, no conditional side-effects, minimal composition). Create when evidence emerges.
- Outbox infrastructure for true compensable post-commit delivery: v1 is best-effort log-and-continue. Separate epic.
- **Retry policy for capital lock contention** (Voronov AMBER F4): under SQLite `BEGIN IMMEDIATE`, `apply_pnl_to_capital` failures will most commonly be `sqlite3.OperationalError: database is locked` rather than logic errors. Current operator surfaces these as exception → close aborted → user clicks Close again → likely same lock. No retry, no backoff, no metric for "how often does capital-lock abort closes in prod". Acceptable for v1; needs explicit retry policy + observability before high-concurrency multi-tenant scenarios.
- **`mode="USER"|"SYSTEM"` flag → subclass migration** (Voronov AMBER F1): the Literal flag works for 2 modes today. A third mode (BATCH, RECONCILIATION) reintroduces the dual-contract shape this PR closed. Resolve by splitting into `UserPositionClosure` / `SystemPositionClosure` sharing an abstract base when that third mode emerges. Documented in operator's module docstring.

## Test plan

- [x] 9 PositionClosure invariant tests (`tests/operators/test_position_closure.py`) — atomicity, ownership-before-lock, IDOR-equivalence (USER), no-IDOR-leak (SYSTEM), no-side-effects-on-exception, single-firing, idempotent re-close, no-lock-during-I/O, no-tenant capital skip.
- [x] 9 `transaction()` wrapper contract tests still pass (no regression).
- [x] `check_position_stops` atomicity regression test passes with corrected fixture.
- [x] Full test suite: same baseline as before (no regressions).
- [ ] Manual smoke in prod after merge: close a position via API, verify health + notify + event log + snapshot all fire. Run scanner cycle, verify position closes correctly on SL/TP hit.

## Migration scope

| Layer | Files | Notes |
|---|---|---|
| New | 4 | `operators/__init__.py`, `operators/position_closure.py`, `tests/operators/__init__.py`, `tests/operators/test_position_closure.py` |
| Production helpers migrated | 13 | db/*, auth/*, notifier/*, health.py, strategy/* |
| Production callers updated | several | wrapped in `with transaction()` where they relied on helper-opened tx |
| Total commits | 15 | one per logical phase |

## Architecture notes

The two analysis documents are the source of truth for the design:
- `docs/superpowers/analysis/2026-05-25-446-tx-or-use-analysis-and-direction.md` — Serrano's clinical analysis (13 findings, 5 options) + Voronov's ontological reframe + direction.
- `docs/superpowers/analysis/2026-05-25-446-preconditions-synthesis.md` — audit results, opening-flow decision, multi-tenancy invariants, full `PositionClosure` contract spec.

Read those before reviewing implementation details.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Close issues with cross-link to PR**

```bash
NEW_PR=$(gh pr view --json number --jq .number)
for issue in 446 447 448 449; do
  gh issue comment $issue --repo sssimon/trading-spacial --body "Closed structurally by #$NEW_PR (PositionClosure operator + db/ as pure SQL layer)."
  gh issue close $issue --repo sssimon/trading-spacial
done

gh issue comment 450 --repo sssimon/trading-spacial --body "Closed by #$NEW_PR with explicit trade-off: apply_pnl_to_capital now runs IN-TX inside PositionClosure; capital UPSERT failure aborts the close (position stays open). This is the deliberate design — see PR description and \`docs/superpowers/analysis/2026-05-25-446-preconditions-synthesis.md\` Section 'Post-commit side-effects' for the trade-off reasoning."
gh issue close 450 --repo sssimon/trading-spacial

gh issue comment 451 --repo sssimon/trading-spacial --body "Closed by #$NEW_PR: fixture corrected (entry=98, sl=97) so trailing-SL actually fires; assertion tightened to exactly two valid outcomes (105.0 operator-wins or 101.0 scanner-wins)."
gh issue close 451 --repo sssimon/trading-spacial
```

- [ ] **Step 4: Done**

The plan is fully executed when this step completes. Manual smoke in prod is the only remaining item, which is the user's call.

---

## Self-Review

**Spec coverage:**
- All 5 BLOCKERS + 5 HIGHs from Serrano: tracked via tasks (PositionClosure, helper migration, deletion, fixture fix, docs).
- Voronov's direction C strict: implemented in Tasks 3-4 (operator + pure helpers in db/), Tasks 8-9 (sweep), Task 11 (deletion).
- All 10 invariants from preconditions synthesis + Voronov AMBER F2: Task 2 (write tests) + Task 4 (implement to pass). Invariant 10 specifically encodes the "best-effort post-commit" commitment so a future contributor cannot silently change it.
- Multi-tenancy invariants (precondition 3): tested by invariants 2/3/4/9 + implemented in operator's `__enter__` ownership check.
- `apply_pnl_to_capital` IN-TX trade-off: Task 5 (signature change) + Task 4 (operator calls it inside tx) + tested by invariant 1. Retry policy hole (Voronov F4) named in deferred list.
- `_tx_or_use` deletion: Task 11.
- `read_only_connection()` named primitive (Voronov F6): added to `db/transaction.py` in Task 3 step 2; used by operator's `__enter__` in Task 4; documented in CLAUDE.md Task 13 §4.
- 4 Cat. 2 hidden operators acknowledged in CLAUDE.md (Voronov F3): Task 13 step 2 enumerates them as documented exceptions, eliminating the doc-vs-code dual-contract Voronov flagged.
- Operator `mode` flag → subclass migration (Voronov F1): documented in operator module docstring (Task 3 step 3) and deferred list (Task 15 PR body).
- Preconditions docs referenced in plan header and self-review.

**Placeholder scan:** None of the disallowed patterns present. All code blocks complete. Migration patterns explicit (Before/After examples in Tasks 8/9).

**Type consistency:**
- `PositionClosure(...)`: constructor signature consistent across tasks.
- `CloseOutcome`: dataclass fields (`status`, `position`, `pnl_usd`, `pnl_pct`) consistent.
- `db_close_position_sql(con, pos_id, exit_price, exit_reason, exit_ts, pnl_usd, pnl_pct)`: same signature used in Task 4 implementation and operator.
- `apply_pnl_to_capital(con: sqlite3.Connection, tenant_id, pnl_usd)`: same in Task 5 and operator.
- `db_get_position_by_id(con: sqlite3.Connection, pos_id: int) -> Optional[dict]`: same in Task 4 and operator.

**Caveats:**
- The exact shape of `notify()`, `notifier.PositionExitEvent`, `_compute_trailing_sl`, and `_decide_exit` in the existing codebase may differ from the names assumed in Task 4 and Task 6. Implementer must read existing code and adapt — these are NOT placeholders; the implementer is told explicitly to "use the real names" in both tasks.
- The `close_position` endpoint's exact signature (Body schema, async/sync, response model) varies; Task 6 says explicitly "adjust to match the real endpoint signature".
- Number of helpers per module in Tasks 8-9 reflects the audit (Cat. 1 count = 34); if the audit missed any, implementer must add them to the sweep before Task 11's grep check.
