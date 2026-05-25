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
        from db.positions import db_create_position, db_get_positions
        from operators.position_closure import PositionClosure
        db_create_position({"symbol": "BTC", "entry_price": 80000, "size_usd": 500}, tenant_id=1)
        db_create_position({"symbol": "ETH", "entry_price": 2300, "size_usd": 500}, tenant_id=1)
        # Close one for user 1
        all_pos = db_get_positions(tenant_id=1)
        first_id = all_pos[0]["id"]
        with PositionClosure(first_id, 81000, "MANUAL", mode="USER", caller_tenant_id=1) as c:
            c.execute()

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
# PositionClosure — ownership enforced (migrated from db_close_position, Task 7)
# ---------------------------------------------------------------------------


class TestDbClosePosition:
    def test_owner_can_close(self, initialized_db):
        from db.positions import db_create_position
        from operators.position_closure import PositionClosure
        pos = db_create_position(
            {"symbol": "BTC", "entry_price": 80000, "size_usd": 500},
            tenant_id=1,
        )
        with PositionClosure(pos["id"], 81000, "MANUAL", mode="USER", caller_tenant_id=1) as c:
            outcome = c.execute()
        assert outcome.status == "closed"
        assert outcome.position is not None
        assert outcome.position["status"] == "closed"

    def test_non_owner_returns_none(self, initialized_db):
        """IDOR protection: user 2 tries to close user 1's position → not_found."""
        from db.positions import db_create_position
        from operators.position_closure import PositionClosure
        pos = db_create_position(
            {"symbol": "BTC", "entry_price": 80000, "size_usd": 500},
            tenant_id=1,
        )
        with PositionClosure(pos["id"], 81000, "MANUAL", mode="USER", caller_tenant_id=2) as c:
            outcome = c.execute()
        assert outcome.status == "not_found"
        assert outcome.position is None

        # Verify position still open
        from db.positions import db_get_positions
        all_pos = db_get_positions(tenant_id=1)
        assert all_pos[0]["status"] == "open"

    def test_legacy_no_tenant_works(self, initialized_db):
        """SYSTEM mode (no tenant_id) can close any position."""
        from db.positions import db_create_position
        from operators.position_closure import PositionClosure
        pos = db_create_position(
            {"symbol": "BTC", "entry_price": 80000, "size_usd": 500},
            tenant_id=1,
        )
        with PositionClosure(pos["id"], 81000, "MANUAL", mode="SYSTEM") as c:
            outcome = c.execute()
        assert outcome.status == "closed"
        assert outcome.position is not None


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
        from db.positions import db_create_position, db_last_exit_ts
        from operators.position_closure import PositionClosure
        # User 1 closes a BTC position
        pos1 = db_create_position(
            {"symbol": "BTC", "entry_price": 80000, "size_usd": 500}, tenant_id=1,
        )
        with PositionClosure(pos1["id"], 81000, "MANUAL", mode="USER", caller_tenant_id=1) as c:
            c.execute()
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


# ---------------------------------------------------------------------------
# B.5 follow-up: notifier._storage tenant enforcement
# ---------------------------------------------------------------------------


class TestNotifierStorageTenantEnforcement:
    def test_record_delivery_persists_tenant_id(self, initialized_db):
        from notifier._storage import record_delivery
        import sqlite3
        nid = record_delivery(
            event_type="signal", event_key="signal:BTC", priority="info",
            payload={"symbol": "BTC"}, channels_sent=["telegram"],
            delivery_status="ok", tenant_id=42,
        )
        con = sqlite3.connect(initialized_db)
        row = con.execute(
            "SELECT tenant_id FROM notifications_sent WHERE id=?", (nid,)
        ).fetchone()
        con.close()
        assert row[0] == 42

    def test_list_unread_filters_by_tenant(self, initialized_db):
        from notifier._storage import list_unread, record_delivery
        record_delivery(
            event_type="signal", event_key="signal:BTC", priority="info",
            payload={}, channels_sent=["telegram"], delivery_status="ok",
            tenant_id=1,
        )
        record_delivery(
            event_type="signal", event_key="signal:ETH", priority="info",
            payload={}, channels_sent=["telegram"], delivery_status="ok",
            tenant_id=2,
        )
        user1_notifs = list_unread(tenant_id=1)
        user2_notifs = list_unread(tenant_id=2)
        assert len(user1_notifs) == 1
        assert user1_notifs[0]["event_key"] == "signal:BTC"
        assert len(user2_notifs) == 1
        assert user2_notifs[0]["event_key"] == "signal:ETH"

    def test_mark_read_idor_returns_false(self, initialized_db):
        """User 2 cannot mark user 1's notification as read."""
        from notifier._storage import mark_read, record_delivery, list_unread
        nid = record_delivery(
            event_type="signal", event_key="signal:BTC", priority="info",
            payload={}, channels_sent=["telegram"], delivery_status="ok",
            tenant_id=1,
        )
        # User 2 tries to mark user 1's notification
        ok = mark_read(nid, tenant_id=2)
        assert ok is False
        # Verify notif is still unread for user 1
        user1_unread = list_unread(tenant_id=1)
        assert len(user1_unread) == 1

    def test_mark_read_owner_succeeds(self, initialized_db):
        from notifier._storage import mark_read, record_delivery
        nid = record_delivery(
            event_type="signal", event_key="signal:BTC", priority="info",
            payload={}, channels_sent=["telegram"], delivery_status="ok",
            tenant_id=1,
        )
        ok = mark_read(nid, tenant_id=1)
        assert ok is True

    def test_mark_all_read_scope_limited_to_tenant(self, initialized_db):
        """mark_all_read affects only current tenant's notifications."""
        from notifier._storage import mark_all_read, record_delivery, list_unread
        # 2 unread for user 1
        for i in range(2):
            record_delivery(
                event_type="signal", event_key=f"signal:U1_{i}", priority="info",
                payload={}, channels_sent=["telegram"], delivery_status="ok",
                tenant_id=1,
            )
        # 1 unread for user 2
        record_delivery(
            event_type="signal", event_key="signal:U2", priority="info",
            payload={}, channels_sent=["telegram"], delivery_status="ok",
            tenant_id=2,
        )

        marked_for_user1 = mark_all_read(tenant_id=1)
        assert marked_for_user1 == 2

        # User 2's notification still unread
        user2_unread = list_unread(tenant_id=2)
        assert len(user2_unread) == 1
        assert user2_unread[0]["event_key"] == "signal:U2"

    def test_legacy_null_tenant_invisible_to_filter(self, initialized_db):
        """Notifications without tenant_id (system broadcasts) invisible to per-user filter."""
        from notifier._storage import list_unread, record_delivery
        record_delivery(
            event_type="signal", event_key="system:broadcast", priority="info",
            payload={}, channels_sent=["telegram"], delivery_status="ok",
            # No tenant_id → NULL
        )
        # User 1 sees nothing (strict filter)
        user1_unread = list_unread(tenant_id=1)
        assert user1_unread == []
        # Legacy unfiltered listing sees it
        all_unread = list_unread()
        assert len(all_unread) == 1
