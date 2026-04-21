"""End-to-end notify() flow: dedupe → ratelimit → render → send → record."""
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def tmp_db_and_reset(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    # Reset ratelimit singletons between tests
    from notifier import ratelimit
    ratelimit.reset_all_for_tests()
    yield db_path


@pytest.fixture
def ok_telegram():
    fake = MagicMock()
    fake.ok = True
    fake.status_code = 200
    fake.json.return_value = {"ok": True}
    return fake


def _cfg():
    return {
        "notifier": {"enabled": True, "test_mode": False,
                      "dedupe": {"default_window_minutes": 30}},
        "telegram_bot_token": "t", "telegram_chat_id": "1",
    }


def test_notify_signal_sends_to_telegram_and_records(tmp_db_and_reset, ok_telegram):
    from notifier import notify, SignalEvent
    ev = SignalEvent(symbol="BTCUSDT", score=6, direction="LONG",
                      entry=50_000, sl=49_000, tp=55_000)

    with patch("notifier.channels.telegram.requests.post", return_value=ok_telegram) as mock_post:
        receipts = notify(ev, cfg=_cfg())

    assert len(receipts) == 1
    assert receipts[0].status == "ok"
    assert mock_post.call_count == 1

    from notifier._storage import list_unread
    rows = list_unread(limit=5)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "signal"


def test_notify_blocks_duplicate_within_dedupe_window(tmp_db_and_reset, ok_telegram):
    from notifier import notify, HealthEvent
    ev = HealthEvent(symbol="JUP", from_state="REDUCED", to_state="PAUSED",
                     reason="3mo_consec_neg")

    with patch("notifier.channels.telegram.requests.post", return_value=ok_telegram) as mock_post:
        r1 = notify(ev, cfg=_cfg())
        r2 = notify(ev, cfg=_cfg())

    assert len(r1) == 1 and r1[0].status == "ok"
    assert r2 == []  # deduped
    assert mock_post.call_count == 1


def test_notify_test_mode_skips_http(tmp_db_and_reset):
    from notifier import notify, SignalEvent
    cfg = _cfg()
    cfg["notifier"]["test_mode"] = True

    ev = SignalEvent(symbol="BTCUSDT", score=6, direction="LONG",
                     entry=50_000, sl=49_000, tp=55_000)
    with patch("notifier.channels.telegram.requests.post") as mock_post:
        receipts = notify(ev, cfg=cfg)

    assert mock_post.call_count == 0
    assert len(receipts) == 1
    assert receipts[0].status == "ok"  # treated as "simulated ok"


def test_notify_disabled_config_returns_empty(tmp_db_and_reset):
    from notifier import notify, SignalEvent
    cfg = _cfg()
    cfg["notifier"]["enabled"] = False

    ev = SignalEvent(symbol="X", score=1, direction="LONG",
                     entry=1, sl=1, tp=1)
    with patch("notifier.channels.telegram.requests.post") as mock_post:
        receipts = notify(ev, cfg=cfg)

    assert receipts == []
    assert mock_post.call_count == 0


def test_notify_rate_limit_queues_overflow(tmp_db_and_reset, ok_telegram):
    """21st call in a burst hits the rate limiter (default capacity=20)."""
    from notifier import notify, SignalEvent, ratelimit

    cfg = _cfg()

    with patch("notifier.channels.telegram.requests.post", return_value=ok_telegram):
        receipts_batch = []
        for i in range(25):
            ev = SignalEvent(symbol=f"SYM{i}", score=1, direction="LONG",
                              entry=1, sl=1, tp=1)
            receipts_batch.append(notify(ev, cfg=cfg))

    sent_count = sum(1 for r in receipts_batch if r and r[0].status == "ok")
    limited_count = sum(1 for r in receipts_batch if r and r[0].status == "rate_limited")
    # At most 20 go through; the rest are rate_limited
    assert sent_count <= 20
    assert sent_count + limited_count == 25
