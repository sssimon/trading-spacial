"""Tests for B.2 capital tracker logic (#255).

Pre-reg: docs/superpowers/plans/2026-05-16-multi-tenant-b2-capital-tracker-pre-reg.md

Locked test list (§4):
- test_manual_close_increments_balance
- test_manual_close_decrements_balance
- test_peak_is_monotonic_after_loss_streak
- test_auto_close_via_check_position_stops
- test_two_users_independent
- test_cancel_does_not_touch_capital
- test_legacy_tenant_null_skipped (DELETED post-#446 Task 6 — the
  `_apply_close_to_capital` shim it tested was removed; the
  "tenant_id=NULL → skip capital" semantic is covered at the operator
  level by tests/operators/test_position_closure.py invariant 9
  `test_legacy_null_tenant_position_close_skips_capital`)
- test_first_close_auto_inits_capital
- test_drawdown_undefined_when_peak_zero
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


@pytest.fixture
def initialized_db(tmp_path, monkeypatch):
    """Fresh DB per test — uses real btc_api like the B.5 enforcement tests."""
    import btc_api
    db_path = str(tmp_path / "test_b2.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    from db.schema import init_db
    init_db()
    yield db_path


# ---------------------------------------------------------------------------
# Pure-helper tests: apply_pnl_to_capital semantics in isolation
# ---------------------------------------------------------------------------


class TestApplyPnlToCapital:
    def test_first_close_auto_inits_capital(self, initialized_db):
        """Pre-reg §2.3: first close with no prior row auto-inits from INITIAL_CAPITAL_DEFAULT."""
        from db.capital import apply_pnl_to_capital, db_get_capital, INITIAL_CAPITAL_DEFAULT
        from db.transaction import transaction
        with transaction() as con:
            row = apply_pnl_to_capital(con, 42, 250.0)
        assert row["balance"] == INITIAL_CAPITAL_DEFAULT + 250.0
        assert row["peak_balance"] == INITIAL_CAPITAL_DEFAULT + 250.0
        # Persistence check
        with transaction() as con:
            persisted = db_get_capital(con, 42)
        assert persisted["balance"] == INITIAL_CAPITAL_DEFAULT + 250.0

    def test_peak_is_monotonic_after_loss_streak(self, initialized_db):
        """Pre-reg §2.3: peak never decreases across +300, -200, -50 sequence."""
        from db.capital import apply_pnl_to_capital, INITIAL_CAPITAL_DEFAULT
        from db.transaction import transaction
        with transaction() as con:
            apply_pnl_to_capital(con, 1, 300.0)   # bal 10300, peak 10300
        with transaction() as con:
            apply_pnl_to_capital(con, 1, -200.0)  # bal 10100, peak 10300
        with transaction() as con:
            row = apply_pnl_to_capital(con, 1, -50.0)  # bal 10050, peak 10300
        assert row["balance"] == INITIAL_CAPITAL_DEFAULT + 50.0
        assert row["peak_balance"] == INITIAL_CAPITAL_DEFAULT + 300.0
        # dd_pct = (10300 - 10050) / 10300 * 100
        expected_dd = (10300 - 10050) / 10300 * 100
        assert row["max_drawdown_pct"] == pytest.approx(expected_dd, abs=1e-6)

    def test_drawdown_undefined_when_peak_zero(self, initialized_db):
        """Pre-reg §2.3: peak <= 0 leaves max_drawdown_pct as None (no div-by-zero)."""
        from db.capital import apply_pnl_to_capital, db_upsert_capital
        from db.transaction import transaction
        # Synthetic seed: peak forced to 0
        with transaction() as con:
            db_upsert_capital(con, 7, balance=0.0, peak_balance=0.0, max_drawdown_pct=None)
        with transaction() as con:
            row = apply_pnl_to_capital(con, 7, -10.0)  # balance -10, peak max(0, -10)=0
        assert row["peak_balance"] == 0.0
        assert row["max_drawdown_pct"] is None  # peak not > 0 -> None

    def test_two_users_independent(self, initialized_db):
        """Pre-reg §4: closing for user A does NOT touch user B."""
        from db.capital import apply_pnl_to_capital, db_get_capital, INITIAL_CAPITAL_DEFAULT
        from db.transaction import transaction
        with transaction() as con:
            apply_pnl_to_capital(con, 1, 100.0)
        with transaction() as con:
            apply_pnl_to_capital(con, 2, -50.0)
        with transaction() as con:
            a = db_get_capital(con, 1)
            b = db_get_capital(con, 2)
        assert a["balance"] == INITIAL_CAPITAL_DEFAULT + 100.0
        assert b["balance"] == INITIAL_CAPITAL_DEFAULT - 50.0
        # Round-trip: another close for A must not touch B
        with transaction() as con:
            apply_pnl_to_capital(con, 1, 25.0)
        with transaction() as con:
            assert db_get_capital(con, 1)["balance"] == INITIAL_CAPITAL_DEFAULT + 125.0
            assert db_get_capital(con, 2)["balance"] == INITIAL_CAPITAL_DEFAULT - 50.0


# ---------------------------------------------------------------------------
# Integration tests: hook fires from api/positions.py close paths
# ---------------------------------------------------------------------------


class TestCloseHookIntegration:
    def test_manual_close_increments_balance(self, initialized_db):
        """API close path: close LONG +250 → balance +250."""
        from api.positions import close_position
        from db.capital import db_get_capital, INITIAL_CAPITAL_DEFAULT
        from db.positions import db_create_position

        pos = db_create_position(
            {"symbol": "BTCUSDT", "entry_price": 100.0, "qty": 10.0,
             "direction": "LONG"},
            tenant_id=1,
        )
        close_position(
            pos_id=pos["id"],
            body={"exit_price": 125.0, "exit_reason": "MANUAL"},
            tenant_id=1,
        )
        from db.transaction import transaction
        with transaction() as con:
            row = db_get_capital(con, 1)
        assert row["balance"] == pytest.approx(INITIAL_CAPITAL_DEFAULT + 250.0, abs=0.01)

    def test_manual_close_decrements_balance(self, initialized_db):
        """API close path: close LONG -100 → balance -100, peak unchanged, dd > 0."""
        from api.positions import close_position
        from db.capital import db_get_capital, INITIAL_CAPITAL_DEFAULT
        from db.positions import db_create_position

        pos = db_create_position(
            {"symbol": "BTCUSDT", "entry_price": 100.0, "qty": 10.0,
             "direction": "LONG"},
            tenant_id=1,
        )
        close_position(
            pos_id=pos["id"],
            body={"exit_price": 90.0, "exit_reason": "MANUAL"},
            tenant_id=1,
        )
        from db.transaction import transaction
        with transaction() as con:
            row = db_get_capital(con, 1)
        assert row["balance"] == pytest.approx(INITIAL_CAPITAL_DEFAULT - 100.0, abs=0.01)
        assert row["peak_balance"] == INITIAL_CAPITAL_DEFAULT  # auto-init peak preserved
        assert row["max_drawdown_pct"] > 0  # current drawdown from peak

    def test_auto_close_via_check_position_stops(self, initialized_db, monkeypatch):
        """Auto-exit path: SL_HIT in check_position_stops also updates capital."""
        from api.positions import check_position_stops
        from db.capital import db_get_capital, INITIAL_CAPITAL_DEFAULT
        from db.positions import db_create_position

        # Stub config so check_position_stops doesn't touch the filesystem
        import api.positions as _pos
        monkeypatch.setattr(_pos, "load_config", lambda: {"symbol_overrides": {}})
        monkeypatch.setattr(_pos, "update_positions_json", lambda: None)

        db_create_position(
            {"symbol": "BTCUSDT", "entry_price": 100.0, "qty": 10.0,
             "direction": "LONG", "sl_price": 95.0, "tp_price": 120.0},
            tenant_id=3,
        )
        # Price tags SL → SL_HIT triggers db_close_position + capital hook
        check_position_stops("BTCUSDT", 94.0, now=datetime.now(timezone.utc))
        from db.transaction import transaction
        with transaction() as con:
            row = db_get_capital(con, 3)
        # SL_HIT exit_price = sl_price (95.0) → pnl = (95-100)*10 = -50
        assert row["balance"] == pytest.approx(INITIAL_CAPITAL_DEFAULT - 50.0, abs=0.01)

    # test_legacy_tenant_null_skipped — DELETED post-#446 Task 6.
    # The `api.positions._apply_close_to_capital` shim it imported was
    # removed when `close_position` migrated to PositionClosure. The
    # "tenant_id=NULL → skip capital" semantic is now covered at the
    # operator level by
    # tests/operators/test_position_closure.py::test_legacy_null_tenant_position_close_skips_capital
    # (invariant 9). Retaining this test here would duplicate the
    # operator invariant and require re-implementing the shim purely to
    # back the test.

    def test_cancel_does_not_touch_capital(self, initialized_db):
        """DELETE /positions/{id} (cancel) leaves capital untouched."""
        from api.positions import delete_position
        from db.capital import db_get_capital
        from db.positions import db_create_position

        db_create_position(
            {"symbol": "BTCUSDT", "entry_price": 100.0, "qty": 10.0,
             "direction": "LONG"},
            tenant_id=9,
        )
        # Pull pos_id (we just created it)
        from db.positions import db_get_positions
        pos = db_get_positions(tenant_id=9)[0]
        # Stub update_positions_json
        import api.positions as _pos
        _orig = _pos.update_positions_json
        _pos.update_positions_json = lambda: None
        try:
            delete_position(pos_id=pos["id"], tenant_id=9)
        finally:
            _pos.update_positions_json = _orig
        from db.transaction import transaction
        with transaction() as con:
            assert db_get_capital(con, 9) is None  # never created
