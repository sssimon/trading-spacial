"""Dedupe is a DB-backed sliding window over notifications_sent.
Same (event_type, event_key) within window_seconds returns False (don't send).
Outside window or first occurrence returns True (send)."""
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


def test_first_send_always_allowed(tmp_db):
    from notifier.dedupe import should_send
    assert should_send("health", "health:BTC:PAUSED", window_seconds=60) is True


def test_repeat_within_window_blocked(tmp_db):
    from notifier.dedupe import should_send
    from notifier._storage import record_delivery

    record_delivery("health", "health:BTC:PAUSED", "warning",
                    {"symbol": "BTC"}, ["telegram"], "ok")
    assert should_send("health", "health:BTC:PAUSED", window_seconds=60) is False


def test_zero_window_never_dedupes(tmp_db):
    from notifier.dedupe import should_send
    from notifier._storage import record_delivery

    record_delivery("signal", "signal:BTC", "info",
                    {"symbol": "BTC"}, ["telegram"], "ok")
    assert should_send("signal", "signal:BTC", window_seconds=0) is True


def test_critical_priority_bypasses_dedupe(tmp_db):
    """Critical events always send, regardless of recent history."""
    from notifier.dedupe import should_send
    from notifier._storage import record_delivery

    record_delivery("infra", "infra:scanner", "critical",
                    {"component": "scanner"}, ["telegram"], "ok")
    assert should_send("infra", "infra:scanner", window_seconds=60,
                        priority="critical") is True
    assert should_send("infra", "infra:scanner", window_seconds=60,
                        priority="warning") is False


def test_record_older_than_window_allows_resend(tmp_db):
    """Sliding-window core: an old record outside the window must NOT block new sends."""
    from datetime import datetime, timedelta, timezone
    import btc_api
    from notifier.dedupe import should_send

    # Backdate a row manually — 2 hours old, window is 60 seconds
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    conn = btc_api.get_db()
    try:
        conn.execute(
            """INSERT INTO notifications_sent
               (event_type, event_key, priority, payload_json,
                channels_sent, delivery_status, sent_at, error_log)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("health", "health:BTC:PAUSED", "warning", "{}", "telegram", "ok", past, None),
        )
        conn.commit()
    finally:
        conn.close()

    assert should_send("health", "health:BTC:PAUSED", window_seconds=60) is True
