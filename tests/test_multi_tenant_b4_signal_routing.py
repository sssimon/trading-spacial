"""Tests for B.4 per-user signal subscriptions + routing (#257).

Pre-reg: docs/superpowers/plans/2026-05-16-multi-tenant-b4-signal-routing-pre-reg.md

Locked test list (§3):
- test_two_users_different_filters
- test_inactive_user_skipped
- test_user_without_prefs_uses_defaults
- test_per_user_telegram_chat_routing
- test_dedupe_per_user_independent
- test_record_delivery_has_tenant_id
- test_legacy_notify_call_unchanged
- test_dispatcher_handles_no_users
- test_symbol_filter_empty_list_means_none
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Fixture: tmp DB with users + user_preferences + notifier state reset
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """DB with two users + prefs rows, plus notifier dedupe state cleared."""
    import btc_api
    db_path = str(tmp_path / "test_b4.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)

    from db.auth_schema import init_auth_db
    from db.schema import init_db
    from db.connection import get_db
    init_db()
    init_auth_db()
    # Note: notifier.dedupe is DB-backed (queries notifications_sent table),
    # so a fresh tmp_path DB per test gives us clean dedupe state automatically.

    con = get_db()
    # Two active users + one inactive
    con.execute(
        "INSERT INTO users (id, email, password_hash, role, is_active, "
        "created_at, password_changed_at) VALUES "
        "(1, 'a@example.com', 'h', 'admin', 1, "
        "'2026-05-16T00:00:00+00:00', '2026-05-16T00:00:00+00:00')"
    )
    con.execute(
        "INSERT INTO users (id, email, password_hash, role, is_active, "
        "created_at, password_changed_at) VALUES "
        "(2, 'b@example.com', 'h', 'viewer', 1, "
        "'2026-05-16T00:00:00+00:00', '2026-05-16T00:00:00+00:00')"
    )
    con.commit()
    con.close()
    yield db_path


def _make_signal(symbol: str, score: int):
    from notifier import SignalEvent
    return SignalEvent(symbol=symbol, score=score, direction="LONG",
                       entry=100.0, sl=95.0, tp=110.0)


def _base_cfg():
    """Minimal cfg that lets test_mode short-circuit channel send()."""
    return {
        "notifier": {"enabled": True, "test_mode": True},
        "telegram_chat_id": "GLOBAL_CHAT",
        "telegram_bot_token": "GLOBAL_TOKEN",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDispatchPerUser:
    def test_two_users_different_filters(self, seeded_db):
        from db.user_preferences import db_upsert_user_preferences
        from notifier.dispatch_per_user import dispatch_signal_to_users

        db_upsert_user_preferences(1, symbol_filter=["BTCUSDT"], min_score=5)
        db_upsert_user_preferences(2, symbol_filter=None, min_score=2)

        # BTC sc=6 → both
        out = dispatch_signal_to_users(_make_signal("BTCUSDT", 6), _base_cfg())
        assert set(out.keys()) == {1, 2}

        # BTC sc=3 → only user 2 (min_score gate)
        out = dispatch_signal_to_users(_make_signal("BTCUSDT", 3), _base_cfg())
        assert set(out.keys()) == {2}

        # ETH sc=6 → only user 2 (symbol_filter gate)
        out = dispatch_signal_to_users(_make_signal("ETHUSDT", 6), _base_cfg())
        assert set(out.keys()) == {2}

    def test_inactive_user_skipped(self, seeded_db):
        from db.connection import get_db
        from notifier.dispatch_per_user import dispatch_signal_to_users

        # Deactivate user 1
        con = get_db()
        con.execute("UPDATE users SET is_active = 0 WHERE id = 1")
        con.commit()
        con.close()

        out = dispatch_signal_to_users(_make_signal("BTCUSDT", 9), _base_cfg())
        assert 1 not in out
        # User 2 still receives (no prefs row → uses defaults: min_score=4 allows score=9)
        assert 2 in out

    def test_user_without_prefs_uses_defaults(self, seeded_db):
        """No user_preferences row → all symbols, min_score=4, global cfg channels."""
        from notifier.dispatch_per_user import dispatch_signal_to_users

        # Both users have no prefs row at this point
        # Score 4 passes default min_score=4
        out = dispatch_signal_to_users(_make_signal("RUNEUSDT", 4), _base_cfg())
        assert set(out.keys()) == {1, 2}

        # Score 3 fails default min_score=4
        out = dispatch_signal_to_users(_make_signal("RUNEUSDT", 3), _base_cfg())
        assert out == {}

    def test_per_user_telegram_chat_routing(self, seeded_db, monkeypatch):
        """User's notify_channels.telegram_chat_id overrides global."""
        from db.user_preferences import db_upsert_user_preferences
        from notifier.dispatch_per_user import dispatch_signal_to_users

        db_upsert_user_preferences(
            1, notify_channels={"telegram_chat_id": "USER_A_CHAT"},
        )
        db_upsert_user_preferences(2, notify_channels=None)

        # Patch TelegramChannel.__init__ to capture chat_id per-call
        seen_chats: list[str] = []
        from notifier.channels import telegram as _tg

        real_init = _tg.TelegramChannel.__init__

        def spy_init(self, cfg):
            seen_chats.append((cfg.get("telegram_chat_id") or "").strip())
            real_init(self, cfg)

        monkeypatch.setattr(_tg.TelegramChannel, "__init__", spy_init)

        dispatch_signal_to_users(_make_signal("BTCUSDT", 9), _base_cfg())
        # User 1 → USER_A_CHAT; user 2 → GLOBAL_CHAT (no override)
        assert "USER_A_CHAT" in seen_chats
        assert "GLOBAL_CHAT" in seen_chats

    def test_dedupe_per_user_independent(self, seeded_db):
        """A's send doesn't suppress B's send for the same signal."""
        from notifier.dispatch_per_user import dispatch_signal_to_users

        out = dispatch_signal_to_users(_make_signal("BTCUSDT", 9), _base_cfg())
        # Both receive on the first fire even though dedupe.should_send is per-key
        assert set(out.keys()) == {1, 2}
        # Each user's receipt list non-empty (test_mode → "ok")
        assert len(out[1]) >= 1
        assert len(out[2]) >= 1

    def test_record_delivery_has_tenant_id(self, seeded_db):
        from notifier.dispatch_per_user import dispatch_signal_to_users
        from db.connection import get_db

        dispatch_signal_to_users(_make_signal("BTCUSDT", 9), _base_cfg())

        con = get_db()
        try:
            rows = con.execute(
                "SELECT tenant_id, event_type FROM notifications_sent "
                "ORDER BY tenant_id"
            ).fetchall()
        finally:
            con.close()
        tenant_ids = sorted(r["tenant_id"] for r in rows)
        assert tenant_ids == [1, 2]
        assert all(r["event_type"] == "signal" for r in rows)

    def test_legacy_notify_call_unchanged(self, seeded_db):
        """notify(HealthEvent…) without tenant_id → NULL tenant_id row, no key prefix."""
        from notifier import notify, HealthEvent
        from db.connection import get_db

        notify(
            HealthEvent(symbol="BTCUSDT", from_state="NORMAL",
                        to_state="ALERT", reason="test"),
            cfg=_base_cfg(),
        )
        con = get_db()
        try:
            row = con.execute(
                "SELECT tenant_id, event_key FROM notifications_sent "
                "WHERE event_type = 'health'"
            ).fetchone()
        finally:
            con.close()
        assert row is not None
        assert row["tenant_id"] is None  # NULL = broadcast
        assert "tenant:" not in row["event_key"]  # bare key, no prefix

    def test_dispatcher_handles_no_users(self, tmp_path, monkeypatch):
        """Fresh DB with no users → returns {} without errors."""
        import btc_api
        db_path = str(tmp_path / "empty.db")
        monkeypatch.setattr(btc_api, "DB_FILE", db_path)
        from db.schema import init_db
        from db.auth_schema import init_auth_db
        init_db()
        init_auth_db()

        from notifier.dispatch_per_user import dispatch_signal_to_users
        out = dispatch_signal_to_users(_make_signal("BTCUSDT", 9), _base_cfg())
        assert out == {}

    def test_symbol_filter_empty_list_means_none(self, seeded_db):
        """symbol_filter=[] (explicit empty whitelist) → user skipped."""
        from db.user_preferences import db_upsert_user_preferences
        from notifier.dispatch_per_user import dispatch_signal_to_users

        db_upsert_user_preferences(1, symbol_filter=[], min_score=0)
        # User 2 has no prefs → defaults

        out = dispatch_signal_to_users(_make_signal("BTCUSDT", 9), _base_cfg())
        assert 1 not in out  # explicit empty whitelist excludes user 1
        assert 2 in out
