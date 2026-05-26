"""Invariant tests for operators.position_closure.PositionClosure.

Anchors the 10 testable invariants declared in
docs/superpowers/analysis/2026-05-25-446-preconditions-synthesis.md
Section 'PositionClosure operator contract spec'.

Also resolves issue #451: this is the regression test for the trading
invariant ('every mutation derived from one tick of price decision
belongs to one serializable transaction') — done against the operator,
not against ad-hoc helper composition.

Invariant 10 encodes Voronov AMBER F2: the 'best-effort post-commit'
commitment must live in the test suite, not only in prose. A future
contributor who 'fixes' the try/except in __exit__ to raise fails
test_side_effect_failure_does_not_rollback_close.
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
            "INSERT INTO capital (tenant_id, balance, peak_balance, max_drawdown_pct, updated_at) "
            "VALUES (1, 10000.0, 10000.0, 0.0, ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        con.execute(
            "INSERT INTO capital (tenant_id, balance, peak_balance, max_drawdown_pct, updated_at) "
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
    """A legacy NULL-tenant row living under `status='legacy_no_tenant'` is
    structurally non-closeable via the normal open→closed transition.

    Pre-D intent: a `status='open' AND tenant_id IS NULL` row could close;
    PositionClosure would commit the close but skip the capital UPSERT (no
    tenant to credit). D (#471) made `tenant_id NOT NULL` enforced at the
    schema except via the `legacy_no_tenant` / `legacy_unmeasurable` escape
    hatches — so the pre-D scenario is now structurally impossible. Any
    NULL-tenant row in the table MUST be in a legacy status, and the
    closure operator's precheck must reject it as PrecheckRejectedState
    (status != 'open'). The "tampered into 200" silently-skip-capital bug
    is closed at the boundary: the row never gets to a position where
    the closure operator can swallow the missing tenant."""
    from operators.position_closure import PositionClosure

    with transaction() as con:
        con.execute(
            """INSERT INTO positions
               (id, symbol, direction, entry_price, qty, sl_price, tp_price,
                status, entry_ts, tenant_id, atr_entry, be_mult)
               VALUES (99, 'LEGACY', 'long', 100.0, 1.0, 95.0, 110.0,
                       'legacy_no_tenant', ?, NULL, 2.0, 1.5)""",
            (datetime.now(timezone.utc).isoformat(),),
        )

    with PositionClosure(
        pos_id=99, exit_price=110.0, exit_reason="TP_HIT",
        mode="SYSTEM",
    ) as closure:
        outcome = closure.execute()

    # Precheck classifies the row as rejected (not 'open'); no close happens.
    assert outcome.status == "rejected_unexpected_state"
    with transaction() as con:
        pos = con.execute("SELECT status FROM positions WHERE id=99").fetchone()
        cap_null = con.execute("SELECT * FROM capital WHERE tenant_id IS NULL").fetchall()
    # Row unchanged + no capital row written.
    assert pos["status"] == "legacy_no_tenant"
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


# ---- Race: another caller closes between precheck and write-tx BEGIN IMMEDIATE ----

def test_in_tx_race_already_closed_does_not_fire_side_effects(fresh_db_with_two_tenants):
    """If another caller closes the position between this operator's precheck
    and its BEGIN IMMEDIATE, the in-tx re-SELECT detects status != 'open' and
    returns already_closed. Side-effects MUST NOT fire (the other caller already
    fired them). Regresses the bug where _result_row was set in the in-tx
    already_closed branch, causing duplicate side-effect emission."""
    from operators.position_closure import PositionClosure

    closure = PositionClosure(
        pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
        mode="USER", caller_tenant_id=1,
    )
    closure.__enter__()  # precheck reads status=open

    # Another caller closes the position before this closure's execute().
    with transaction() as con:
        con.execute(
            "UPDATE positions SET status = 'closed', exit_price = 109.0, "
            "exit_reason = 'TP_HIT', exit_ts = ? WHERE id = 1",
            (datetime.now(timezone.utc).isoformat(),),
        )

    # execute() opens the write-tx, re-SELECTs, sees status='closed', returns already_closed.
    # Side-effects (notify, health, event log, snapshot) MUST NOT fire.
    with patch("operators.position_closure._write_position_event_log") as ev, \
         patch("operators.position_closure.trigger_health_evaluation") as he, \
         patch("operators.position_closure.notify") as nf, \
         patch("operators.position_closure.update_positions_json") as up:
        outcome = closure.execute()
        closure.__exit__(None, None, None)

    assert outcome.status == "already_closed"
    assert ev.call_count == 0
    assert he.call_count == 0
    assert nf.call_count == 0
    assert up.call_count == 0


# ---- Invariant 13: tenant reassignment between precheck and write-tx (closes #461) ----

def test_tenant_reassignment_between_precheck_and_write_rejects_close(fresh_db_with_two_tenants):
    """If tenant_id changes between precheck and BEGIN IMMEDIATE, the operator
    must collapse to NOT_FOUND. Validates that snapshot re-validation in the
    write-tx covers the IDOR race window that #461 named.

    Setup: position 1 owned by tenant 1. We construct PositionClosure(USER, 1),
    enter it (precheck reads tenant_id=1), then directly UPDATE the row to
    tenant_id=2 before execute() runs. The write-tx's re-SELECT should detect
    the reassignment and return not_found."""
    from operators.position_closure import PositionClosure

    closure = PositionClosure(
        pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
        mode="USER", caller_tenant_id=1,
    )
    # __enter__ runs the precheck.
    closure.__enter__()

    # Simulate tenant reassignment between precheck and write-tx.
    with transaction() as con:
        con.execute("UPDATE positions SET tenant_id = 2 WHERE id = 1")

    # execute() opens the write-tx and re-validates the snapshot. The mismatch
    # must collapse to NOT_FOUND (IDOR-safe).
    outcome = closure.execute()
    closure.__exit__(None, None, None)

    assert outcome.status == "not_found"

    # The position is unchanged; status is still 'open'.
    with transaction() as con:
        pos = con.execute("SELECT status, tenant_id FROM positions WHERE id=1").fetchone()
    assert pos["status"] == "open"
    assert pos["tenant_id"] == 2  # The reassignment is unchanged (we wrote it, operator did not touch it)


# ---- F1 fix: CloseOutcome.position shape uniform across already_closed paths ----

def test_close_outcome_position_shape_uniform_for_already_closed(fresh_db_with_two_tenants):
    """Both already_closed paths (precheck-detected and in-tx-race-detected)
    must return CloseOutcome.position with the SAME set of keys (F1 fix per
    Voronov). The position field is the snapshot shape — no exit_price /
    exit_reason / exit_ts / pnl_usd / pnl_pct."""
    from operators.position_closure import PositionClosure

    # Path A: precheck-detected already_closed
    # Setup: close position 1 first via direct UPDATE
    with transaction() as con:
        con.execute(
            "UPDATE positions SET status='closed', exit_price=109.0, "
            "exit_reason='TP_HIT', exit_ts=? WHERE id=1",
            (datetime.now(timezone.utc).isoformat(),),
        )
    closure_a = PositionClosure(
        pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
        mode="USER", caller_tenant_id=1,
    )
    closure_a.__enter__()
    outcome_a = closure_a.execute()
    closure_a.__exit__(None, None, None)

    # Path B: in-tx-race-detected already_closed
    # Setup: open position 2 still, precheck sees open, then someone else closes
    closure_b = PositionClosure(
        pos_id=2, exit_price=220.0, exit_reason="TP_HIT",
        mode="USER", caller_tenant_id=2,
    )
    closure_b.__enter__()  # precheck reads status=open
    with transaction() as con:
        con.execute(
            "UPDATE positions SET status='closed', exit_price=219.0, "
            "exit_reason='TP_HIT', exit_ts=? WHERE id=2",
            (datetime.now(timezone.utc).isoformat(),),
        )
    outcome_b = closure_b.execute()
    closure_b.__exit__(None, None, None)

    assert outcome_a.status == "already_closed"
    assert outcome_b.status == "already_closed"
    # F1 invariant: both position dicts have the same set of keys.
    assert set(outcome_a.position.keys()) == set(outcome_b.position.keys()), (
        f"position shapes differ: {set(outcome_a.position.keys())} vs {set(outcome_b.position.keys())}"
    )
    # Neither path exposes exit_* fields.
    assert "exit_price" not in outcome_a.position
    assert "exit_price" not in outcome_b.position


# ---- F2 fix: cancelled status returns rejected_unexpected_state, not already_closed ----

def test_cancelled_position_returns_rejected_unexpected_state(fresh_db_with_two_tenants):
    """A position in status='cancelled' (set by DELETE /positions/{id} endpoint)
    must return CloseOutcome.status='rejected_unexpected_state' with the real
    status in position.status, NOT collapsed to 'already_closed' (F2 fix per
    Voronov). Tests both precheck-detected and in-tx-race-detected paths."""
    from operators.position_closure import PositionClosure

    # Path A: precheck-detected cancelled
    with transaction() as con:
        con.execute("UPDATE positions SET status='cancelled' WHERE id=1")

    closure_a = PositionClosure(
        pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
        mode="USER", caller_tenant_id=1,
    )
    closure_a.__enter__()
    outcome_a = closure_a.execute()
    closure_a.__exit__(None, None, None)

    assert outcome_a.status == "rejected_unexpected_state"
    assert outcome_a.position["status"] == "cancelled"

    # Path B: in-tx-race-detected cancelled
    closure_b = PositionClosure(
        pos_id=2, exit_price=220.0, exit_reason="TP_HIT",
        mode="USER", caller_tenant_id=2,
    )
    closure_b.__enter__()  # precheck reads status=open
    with transaction() as con:
        con.execute("UPDATE positions SET status='cancelled' WHERE id=2")
    outcome_b = closure_b.execute()
    closure_b.__exit__(None, None, None)

    assert outcome_b.status == "rejected_unexpected_state"
    assert outcome_b.position["status"] == "cancelled"


# ---- PrecheckRejectedState variant test ----

def test_precheck_rejected_state_distinct_from_already_closed():
    """PrecheckRejectedState is a distinct PrecheckResult variant. Verifies
    pattern matching can distinguish them."""
    from operators.precheck import (
        PositionSnapshot, PrecheckAlreadyClosed, PrecheckRejectedState,
    )

    snap_closed = PositionSnapshot(
        pos_id=1, tenant_id=42, status="closed",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )
    snap_cancelled = PositionSnapshot(
        pos_id=1, tenant_id=42, status="cancelled",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )

    ac = PrecheckAlreadyClosed(snapshot=snap_closed)
    rs = PrecheckRejectedState(snapshot=snap_cancelled)

    assert isinstance(ac, PrecheckAlreadyClosed)
    assert not isinstance(ac, PrecheckRejectedState)
    assert isinstance(rs, PrecheckRejectedState)
    assert not isinstance(rs, PrecheckAlreadyClosed)
    assert rs.snapshot.status == "cancelled"


# ---- Invariant 14: cross-mutation race rejects close when snapshot field drifts ----

def test_cross_mutation_race_entry_price_rejects_close(fresh_db_with_two_tenants):
    """If a snapshot field that is NOT tenant_id/status changes between precheck
    and BEGIN IMMEDIATE (e.g., entry_price is updated by a migration or ad-hoc
    UPDATE), the operator must detect the drift and return not_found.

    Closes #469 + F6: validation lives in the type (OwnershipValidatedSnapshot)
    and the runtime field-by-field comparison in execute()."""
    from operators.position_closure import PositionClosure

    closure = PositionClosure(
        pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
        mode="USER", caller_tenant_id=1,
    )
    closure.__enter__()  # precheck reads entry_price (whatever the fixture set)

    # Simulate an entry_price drift between precheck and write-tx.
    with transaction() as con:
        con.execute("UPDATE positions SET entry_price = 999.99 WHERE id = 1")

    outcome = closure.execute()
    closure.__exit__(None, None, None)

    # The snapshot held the old entry_price; the row now has 999.99.
    # The mismatch must collapse to NOT_FOUND.
    assert outcome.status == "not_found"

    # The row was not closed.
    with transaction() as con:
        pos = con.execute("SELECT status, entry_price FROM positions WHERE id=1").fetchone()
    assert pos["status"] == "open"
    assert pos["entry_price"] == 999.99  # our UPDATE remains


# ---- Invariant 11: single-use enforcement symmetry (#460) ----

def test_reentering_without_execute_still_blocks_second_enter(
    fresh_db_with_two_tenants
):
    """A PositionClosure that is entered (precheck runs) but never executed
    (early exit, or caller forgets to call execute()) must NOT be re-enterable.

    The semantic is 'single-use': the first __enter__ commits the closure
    instance to that one attempt, regardless of whether execute() was
    called inside the block. The previous implementation only set the
    consumed flag inside execute(), so an enter-without-execute left the
    instance reusable — Serrano F-NEW-7 finding from PR #452 review.

    Closes #460 (single-use enforcement asymmetry between __enter__ check
    and execute() set)."""
    from operators.position_closure import PositionClosure

    closure = PositionClosure(
        pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
        mode="USER", caller_tenant_id=1,
    )

    # First enter: succeeds. Deliberately do NOT call execute() inside.
    with closure:
        pass  # caller decided not to execute (degenerate but legal flow)

    # Second enter on the same instance: must raise RuntimeError.
    # Pre-fix: this would silently succeed because _consumed was only
    # set by execute() — the enter check passed and a second precheck ran.
    with pytest.raises(RuntimeError, match=r"single-use"):
        with closure:
            closure.execute()


# ---- Invariant 12: drift check survives float byte-identity loss (#475) ----

def test_close_survives_float_drift_in_entry_price(fresh_db_with_two_tenants):
    """A future migration that touches entry_price via arithmetic may lose
    byte-identity without changing the mathematical value (e.g.,
    `entry_price = qty * (entry_price / qty)` round-trips through float
    arithmetic and lands on a near-equal but bit-different value). The drift
    check must tolerate that via math.isclose so legitimate closes succeed.

    Pre-fix (PR #486 era): exact equality (`==`) rejected ANY bit-level
    difference, collapsing to not_found even for mathematically identical
    values. Post-fix: math.isclose with rel_tol=1e-9 / abs_tol=1e-9 tolerates
    the kind of drift any real-world migration arithmetic could introduce.

    Closes #475 (Serrano F5 [HIGH, STATE/OPS])."""
    from operators.position_closure import PositionClosure

    closure = PositionClosure(
        pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
        mode="USER", caller_tenant_id=1,
    )
    closure.__enter__()  # precheck reads entry_price=100.0 (per fixture)

    # 1e-12 is ~70 ulp at magnitude 100.0 — clearly distinct as a float, well
    # within math.isclose(rel_tol=1e-9, abs_tol=1e-9) tolerance (max diff ~1e-7).
    near_100 = 100.0 + 1e-12
    assert near_100 != 100.0, "test setup: value must be byte-distinct"
    with transaction() as con:
        con.execute("UPDATE positions SET entry_price = ? WHERE id = 1", (near_100,))

    outcome = closure.execute()
    closure.__exit__(None, None, None)

    # Pre-fix: outcome.status == "not_found" (the bug this issue fixes).
    # Post-fix: math.isclose tolerates the 1e-12 drift; close succeeds.
    assert outcome.status == "closed"


def test_close_survives_float_drift_in_qty(fresh_db_with_two_tenants):
    """Same as test_close_survives_float_drift_in_entry_price, but for qty.
    Closes #475 (qty is the other REAL field subject to migration arithmetic)."""
    from operators.position_closure import PositionClosure

    closure = PositionClosure(
        pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
        mode="USER", caller_tenant_id=1,
    )
    closure.__enter__()  # precheck reads qty=1.0 (per fixture)

    near_1 = 1.0 + 1e-12
    assert near_1 != 1.0, "test setup: value must be byte-distinct"
    with transaction() as con:
        con.execute("UPDATE positions SET qty = ? WHERE id = 1", (near_1,))

    outcome = closure.execute()
    closure.__exit__(None, None, None)

    assert outcome.status == "closed"


# ---- Invariant 13: non-tenant drift emits a structured integrity log (#478) ----

def test_non_tenant_drift_emits_integrity_log(fresh_db_with_two_tenants, caplog):
    """When the drift check fires for a NON-tenant_id field (entry_price, qty,
    direction, or symbol), the operator must emit a structured log.error
    naming the field + precheck value + write_tx value. This makes data-
    integrity events distinguishable from IDOR collapses in operator metrics.

    The user-visible CloseOutcome shape remains IDOR-safe (status=not_found,
    no payload) — internal distinguishability is via the log channel only.

    Closes #478 (Serrano F11 [MEDIUM, STATE])."""
    import logging
    from operators.position_closure import PositionClosure

    closure = PositionClosure(
        pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
        mode="USER", caller_tenant_id=1,
    )
    closure.__enter__()  # precheck reads entry_price=100.0

    # Clearly-detectable drift (not float noise — well outside isclose tolerance).
    with transaction() as con:
        con.execute("UPDATE positions SET entry_price = 999.99 WHERE id = 1")

    with caplog.at_level(logging.ERROR, logger="operators.position_closure"):
        outcome = closure.execute()
    closure.__exit__(None, None, None)

    # User-visible: IDOR-safe shape (status=not_found, no payload).
    assert outcome.status == "not_found"
    assert outcome.position is None

    # Internally: integrity event surfaced in the log channel.
    integrity_logs = [r for r in caplog.records if "integrity event" in r.getMessage().lower()]
    assert len(integrity_logs) >= 1, (
        f"expected at least one integrity-event log, got records: {[r.getMessage() for r in caplog.records]}"
    )
    log_msg = integrity_logs[0].getMessage()
    assert "entry_price" in log_msg, f"log must name the drifted field; got: {log_msg!r}"
    assert "100.0" in log_msg, f"log must contain precheck value 100.0; got: {log_msg!r}"
    assert "999.99" in log_msg, f"log must contain write_tx value 999.99; got: {log_msg!r}"


def test_tenant_drift_does_NOT_emit_integrity_log(fresh_db_with_two_tenants, caplog):
    """tenant_id drift remains IDOR-safe AND silent: collapsing to not_found
    is intentional (USER mode must not leak 'position exists, you don't own
    it' vs 'position doesn't exist'), and a log emission would itself leak
    the existence of the row to anyone reading operator logs.

    No integrity log fires on tenant_id-only drift. Non-tenant drift in the
    same drift event WOULD log (covered by sibling test).

    Closes #478 (IDOR safety preserved)."""
    import logging
    from operators.position_closure import PositionClosure

    closure = PositionClosure(
        pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
        mode="USER", caller_tenant_id=1,
    )
    closure.__enter__()  # precheck reads tenant_id=1

    # Re-assign the row to a different tenant (simulates concurrent admin op).
    with transaction() as con:
        con.execute("UPDATE positions SET tenant_id = 2 WHERE id = 1")

    with caplog.at_level(logging.ERROR, logger="operators.position_closure"):
        outcome = closure.execute()
    closure.__exit__(None, None, None)

    # User-visible: IDOR-safe collapse.
    assert outcome.status == "not_found"

    # Internally: NO integrity log — tenant_id is the IDOR-safe case.
    integrity_logs = [r for r in caplog.records if "integrity event" in r.getMessage().lower()]
    assert len(integrity_logs) == 0, (
        f"tenant_id drift must not emit integrity log (IDOR safety); got: "
        f"{[r.getMessage() for r in integrity_logs]}"
    )


def test_combined_tenant_and_non_tenant_drift_logs_only_non_tenant(
    fresh_db_with_two_tenants, caplog
):
    """When BOTH tenant_id AND a non-tenant field (e.g. entry_price) drift
    simultaneously, the operator must:
      1. Emit the integrity log for the non-tenant field, AND
      2. NOT include tenant_id in any log channel (IDOR safety must not be
         defeated just because another field also drifted).

    Guards against a future refactor that:
      - moves the `continue` for tenant_id out of scope, OR
      - reorders the per-field iteration in a way that lets tenant_id
        leak into log records, OR
      - replaces the per-field loop with a generic structured emission
        that includes ALL drift_fields without honoring the tenant_id
        IDOR carve-out.

    Closes #478 — combined-drift safety carve-out for tenant_id."""
    import logging
    from operators.position_closure import PositionClosure

    closure = PositionClosure(
        pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
        mode="USER", caller_tenant_id=1,
    )
    closure.__enter__()  # precheck reads tenant_id=1, entry_price=100.0

    # BOTH fields drift simultaneously in a single UPDATE.
    with transaction() as con:
        con.execute(
            "UPDATE positions SET tenant_id = 2, entry_price = 999.99 WHERE id = 1"
        )

    with caplog.at_level(logging.ERROR, logger="operators.position_closure"):
        outcome = closure.execute()
    closure.__exit__(None, None, None)

    # User-visible: IDOR-safe collapse (same shape as ownership mismatch).
    assert outcome.status == "not_found"

    # Integrity log fires for the non-tenant field.
    integrity_logs = [r for r in caplog.records if "integrity event" in r.getMessage().lower()]
    assert len(integrity_logs) >= 1, (
        f"expected entry_price drift to emit integrity log; got: "
        f"{[r.getMessage() for r in caplog.records]}"
    )

    # CRITICAL: tenant_id must not appear in any integrity log record, even
    # though it also drifted. The IDOR-safe silence is field-scoped, not
    # event-scoped.
    for record in integrity_logs:
        msg = record.getMessage()
        assert "entry_price" in msg, (
            f"expected entry_price log, got: {msg!r}"
        )
        assert "field=tenant_id" not in msg, (
            f"tenant_id must not appear in integrity log even when combined "
            f"with non-tenant drift (IDOR safety is field-scoped); got: {msg!r}"
        )
