"""Tests for B.5 API JWT enforcement (#258).

Pre-reg: docs/superpowers/plans/2026-05-15-multi-tenant-b5-api-enforcement-pre-reg.md

Two test layers:
- Helper: get_current_tenant_id extracts user.id from JWT-injected User object
- DB layer: db/positions.py functions filter/enforce ownership when tenant_id provided
- IDOR scenarios: ownership enforced; pre-backfill (tenant_id=NULL) invisible
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Setup: tmp DB + minimal User object
# ---------------------------------------------------------------------------


@pytest.fixture
def initialized_db(tmp_path, monkeypatch):
    """Fresh schema'd DB per test — uses real btc_api like existing test pattern."""
    import btc_api
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    from db.schema import init_db
    init_db()
    yield db_path


def _make_user(user_id: int, role: str = "admin"):
    """Build a minimal User dataclass instance for dependency tests."""
    from auth.models import User
    return User(
        id=user_id,
        email=f"user{user_id}@example.com",
        role=role,
        is_active=True,
        created_at="2026-05-15T00:00:00+00:00",
        password_changed_at="2026-05-15T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# get_current_tenant_id helper
# ---------------------------------------------------------------------------


class TestGetCurrentTenantId:
    def test_returns_user_id(self):
        from auth.dependencies import get_current_tenant_id
        user = _make_user(42)
        assert get_current_tenant_id(user) == 42

    def test_different_users_return_distinct_ids(self):
        from auth.dependencies import get_current_tenant_id
        assert get_current_tenant_id(_make_user(1)) == 1
        assert get_current_tenant_id(_make_user(99)) == 99


# ---------------------------------------------------------------------------
# db_create_position — tenant_id persisted when provided
# ---------------------------------------------------------------------------


class TestDbCreatePosition:
    def test_with_tenant_id_persists(self, initialized_db):
        from db.positions import db_create_position
        pos = db_create_position(
            {"symbol": "BTCUSDT", "entry_price": 80000, "size_usd": 500},
            tenant_id=42,
        )
        assert pos["tenant_id"] == 42

    def test_without_tenant_id_is_null(self, initialized_db):
        """Legacy callers (no tenant_id) insert NULL — preserves pre-multi-tenant behavior."""
        from db.positions import db_create_position
        pos = db_create_position(
            {"symbol": "BTCUSDT", "entry_price": 80000, "size_usd": 500},
        )
        assert pos["tenant_id"] is None


# ---------------------------------------------------------------------------
# db_get_positions — filter by tenant when provided
# ---------------------------------------------------------------------------


class TestDbGetPositions:
    def test_filters_by_tenant_id(self, initialized_db):
        from db.positions import db_create_position, db_get_positions
        db_create_position({"symbol": "BTC", "entry_price": 80000}, tenant_id=1)
        db_create_position({"symbol": "ETH", "entry_price": 2300}, tenant_id=2)
        db_create_position({"symbol": "RUNE", "entry_price": 0.5}, tenant_id=1)

        user1 = db_get_positions(tenant_id=1)
        user2 = db_get_positions(tenant_id=2)

        assert len(user1) == 2
        assert {p["symbol"] for p in user1} == {"BTC", "RUNE"}
        assert len(user2) == 1
        assert user2[0]["symbol"] == "ETH"

    def test_legacy_no_tenant_returns_all(self, initialized_db):
        """When tenant_id=None (legacy/internal), returns all rows including NULL."""
        from db.positions import db_create_position, db_get_positions
        db_create_position({"symbol": "BTC", "entry_price": 80000}, tenant_id=1)
        db_create_position({"symbol": "ETH", "entry_price": 2300})  # NULL

        all_positions = db_get_positions()
        assert len(all_positions) == 2

    def test_status_and_tenant_combined(self, initialized_db):
        from db.positions import db_create_position, db_close_position, db_get_positions
        db_create_position({"symbol": "BTC", "entry_price": 80000, "size_usd": 500}, tenant_id=1)
        db_create_position({"symbol": "ETH", "entry_price": 2300, "size_usd": 500}, tenant_id=1)
        # Close one for user 1
        all_pos = db_get_positions(tenant_id=1)
        first_id = all_pos[0]["id"]
        db_close_position(first_id, 81000, "MANUAL", tenant_id=1)

        open_for_1 = db_get_positions(status="open", tenant_id=1)
        closed_for_1 = db_get_positions(status="closed", tenant_id=1)
        assert len(open_for_1) == 1
        assert len(closed_for_1) == 1

    def test_pre_backfill_data_invisible(self, initialized_db):
        """Existing positions with tenant_id=NULL must NOT show up when filtering by tenant."""
        from db.positions import db_create_position, db_get_positions
        db_create_position({"symbol": "BTC", "entry_price": 80000})  # NULL tenant
        db_create_position({"symbol": "ETH", "entry_price": 2300}, tenant_id=1)

        user1 = db_get_positions(tenant_id=1)
        assert len(user1) == 1
        assert user1[0]["symbol"] == "ETH"


# ---------------------------------------------------------------------------
# db_close_position — ownership enforced
# ---------------------------------------------------------------------------


class TestDbClosePosition:
    def test_owner_can_close(self, initialized_db):
        from db.positions import db_create_position, db_close_position
        pos = db_create_position(
            {"symbol": "BTC", "entry_price": 80000, "size_usd": 500},
            tenant_id=1,
        )
        closed = db_close_position(pos["id"], 81000, "MANUAL", tenant_id=1)
        assert closed is not None
        assert closed["status"] == "closed"

    def test_non_owner_returns_none(self, initialized_db):
        """IDOR protection: user 2 tries to close user 1's position → None."""
        from db.positions import db_create_position, db_close_position
        pos = db_create_position(
            {"symbol": "BTC", "entry_price": 80000, "size_usd": 500},
            tenant_id=1,
        )
        closed = db_close_position(pos["id"], 81000, "MANUAL", tenant_id=2)
        assert closed is None

        # Verify position still open
        from db.positions import db_get_positions
        all_pos = db_get_positions(tenant_id=1)
        assert all_pos[0]["status"] == "open"

    def test_legacy_no_tenant_works(self, initialized_db):
        """tenant_id=None preserves legacy: any position closeable."""
        from db.positions import db_create_position, db_close_position
        pos = db_create_position(
            {"symbol": "BTC", "entry_price": 80000, "size_usd": 500},
            tenant_id=1,
        )
        closed = db_close_position(pos["id"], 81000, "MANUAL")  # no tenant_id
        assert closed is not None


# ---------------------------------------------------------------------------
# db_update_position — ownership enforced
# ---------------------------------------------------------------------------


class TestDbUpdatePosition:
    def test_owner_can_update(self, initialized_db):
        from db.positions import db_create_position, db_update_position
        pos = db_create_position(
            {"symbol": "BTC", "entry_price": 80000},
            tenant_id=1,
        )
        updated = db_update_position(pos["id"], {"sl_price": 79000}, tenant_id=1)
        assert updated is not None
        assert updated["sl_price"] == 79000

    def test_non_owner_returns_none(self, initialized_db):
        """IDOR: user 2 can't update user 1's position."""
        from db.positions import db_create_position, db_update_position
        pos = db_create_position(
            {"symbol": "BTC", "entry_price": 80000},
            tenant_id=1,
        )
        updated = db_update_position(pos["id"], {"sl_price": 79000}, tenant_id=2)
        assert updated is None

        # Verify SL not changed
        from db.positions import db_get_positions
        all_pos = db_get_positions(tenant_id=1)
        assert all_pos[0]["sl_price"] is None


# ---------------------------------------------------------------------------
# db_last_exit_ts — filter by tenant when provided
# ---------------------------------------------------------------------------


class TestDbLastExitTs:
    def test_filter_by_tenant(self, initialized_db):
        from db.positions import db_create_position, db_close_position, db_last_exit_ts
        # User 1 closes a BTC position
        pos1 = db_create_position(
            {"symbol": "BTC", "entry_price": 80000, "size_usd": 500}, tenant_id=1,
        )
        db_close_position(pos1["id"], 81000, "MANUAL", tenant_id=1)
        # User 2 also has a BTC trade, but never closed
        db_create_position(
            {"symbol": "BTC", "entry_price": 79000, "size_usd": 500}, tenant_id=2,
        )

        # User 1 sees their own exit
        last_user1 = db_last_exit_ts("BTC", tenant_id=1)
        assert last_user1 is not None
        # User 2 has no exit
        last_user2 = db_last_exit_ts("BTC", tenant_id=2)
        assert last_user2 is None
        # Legacy (no tenant) sees user 1's exit (most recent)
        last_legacy = db_last_exit_ts("BTC")
        assert last_legacy is not None


# ---------------------------------------------------------------------------
# Tampering scenarios (semantic — verify the Depends contract)
# ---------------------------------------------------------------------------


class TestTamperingResistance:
    def test_helper_does_not_read_request_attrs(self):
        """get_current_tenant_id ONLY reads user.id, not any request attrs.

        Inspect the function signature: parameter is `user: User`, not
        `request: Request`. FastAPI Depends chains through get_current_user
        which reads from request.state.user (set by AuthMiddleware from JWT).
        No code path reads tenant_id from query/header/body.
        """
        import inspect
        from auth.dependencies import get_current_tenant_id

        sig = inspect.signature(get_current_tenant_id)
        params = list(sig.parameters.keys())
        assert params == ["user"], (
            f"get_current_tenant_id must only depend on user (JWT-derived), "
            f"got params={params}"
        )

    def test_body_tenant_id_is_dropped_on_create(self, initialized_db):
        """db_create_position uses the explicit tenant_id param, not body['tenant_id'].

        Even if a malicious body contains {'tenant_id': 999}, only the explicit
        tenant_id arg (from JWT) is honored.
        """
        from db.positions import db_create_position
        # Body claims tenant_id=999, but we pass tenant_id=1
        malicious_body = {
            "symbol": "BTC", "entry_price": 80000, "size_usd": 500,
            "tenant_id": 999,  # attacker tries to set this
        }
        pos = db_create_position(malicious_body, tenant_id=1)
        # tenant_id reflects the explicit arg (1), NOT the body value (999)
        assert pos["tenant_id"] == 1


# ---------------------------------------------------------------------------
# Backfill restores visibility post-migration
# ---------------------------------------------------------------------------


class TestBackfillRestoresVisibility:
    def test_backfill_makes_existing_data_visible(self, initialized_db):
        """Pre-backfill NULL data invisible to tenant filter; backfill flips that."""
        from db.positions import db_create_position, db_get_positions
        from db.schema import backfill_tenant

        # Pre-migration: legacy create (no tenant_id)
        db_create_position({"symbol": "BTC", "entry_price": 80000})

        # User 1 queries, sees nothing
        assert db_get_positions(tenant_id=1) == []

        # Run backfill assigning everything to user 1
        affected = backfill_tenant(user_id=1)
        assert affected["positions"] == 1

        # Now user 1 sees the row
        results = db_get_positions(tenant_id=1)
        assert len(results) == 1
        assert results[0]["symbol"] == "BTC"
