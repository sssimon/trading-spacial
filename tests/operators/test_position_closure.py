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
