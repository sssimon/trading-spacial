"""End-to-end: notify(event) with webhook channel configured → POST JSON to URL."""
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
    from notifier import ratelimit
    ratelimit.reset_all_for_tests()
    yield db_path


def _cfg_with_webhook(url="http://n8n.local/hook", types=None, telegram_enabled=False):
    cfg = {
        "notifier": {
            "enabled": True,
            "channels_by_event_type": {
                "signal": (["telegram", "webhook"] if telegram_enabled else ["webhook"]),
                "position_exit": ["webhook"],
                "health": ["webhook"],
            },
            "channels": {
                "webhook": {
                    "enabled": True,
                    "endpoints": [{"url": url, "types": types}] if types else [{"url": url}],
                },
            },
        },
    }
    if telegram_enabled:
        cfg["telegram_bot_token"] = "t"
        cfg["telegram_chat_id"] = "1"
    return cfg


def test_notify_signal_via_webhook_posts_json(tmp_db_and_reset):
    from notifier import notify, SignalEvent

    ok = MagicMock()
    ok.ok = True
    ok.status_code = 200

    with patch("notifier.channels.webhook.requests.post", return_value=ok) as mock_post:
        receipts = notify(
            SignalEvent(symbol="BTCUSDT", score=6, direction="LONG",
                         entry=50_000, sl=49_000, tp=55_000),
            cfg=_cfg_with_webhook(types=["signal"]),
        )

    assert len(receipts) == 1
    assert receipts[0].status == "ok"
    assert mock_post.call_count == 1
    body = mock_post.call_args.kwargs["data"]
    # The body is a rendered JSON template — check it contains the expected fields.
    assert '"type": "signal"' in body
    assert '"symbol": "BTCUSDT"' in body


def test_notify_position_exit_via_webhook(tmp_db_and_reset):
    from notifier import notify, PositionExitEvent

    ok = MagicMock()
    ok.ok = True

    with patch("notifier.channels.webhook.requests.post", return_value=ok) as mock_post:
        receipts = notify(
            PositionExitEvent(symbol="BTC", direction="LONG", exit_reason="TP",
                               entry_price=50_000, exit_price=55_000,
                               pnl_usd=100, pnl_pct=10),
            cfg=_cfg_with_webhook(types=["position_exit"]),
        )

    assert receipts[0].status == "ok"
    body = mock_post.call_args.kwargs["data"]
    assert '"type": "position_exit"' in body
    assert '"exit_reason": "TP"' in body


def _ok_response():
    m = MagicMock()
    m.ok = True
    m.status_code = 200
    return m


def test_notify_routes_to_both_telegram_and_webhook(tmp_db_and_reset):
    """Both telegram and webhook channels route via requests.post — use a single
    patch that dispatches by URL (they share the same module-level requests.post
    reference so two patches would collide)."""
    from notifier import notify, SignalEvent

    calls = {"telegram": 0, "webhook": 0}

    def _dispatcher(url, *a, **kw):
        if "api.telegram.org" in url:
            calls["telegram"] += 1
        else:
            calls["webhook"] += 1
        return _ok_response()

    with patch("requests.post", side_effect=_dispatcher):
        receipts = notify(
            SignalEvent(symbol="BTC", score=5, direction="LONG",
                         entry=1, sl=1, tp=1),
            cfg=_cfg_with_webhook(telegram_enabled=True),
        )

    assert calls["telegram"] == 1
    assert calls["webhook"] == 1
    statuses = {r.channel: r.status for r in receipts}
    assert statuses["telegram"] == "ok"
    assert statuses["webhook"] == "ok"


def test_notify_webhook_failure_does_not_block_telegram(tmp_db_and_reset):
    """Partial delivery: webhook 500s but telegram succeeds → both tier receipts recorded."""
    from notifier import notify, SignalEvent

    def _dispatcher(url, *a, **kw):
        if "api.telegram.org" in url:
            return _ok_response()
        fail = MagicMock()
        fail.ok = False
        fail.status_code = 500
        fail.text = "server err"
        return fail

    with patch("requests.post", side_effect=_dispatcher), \
         patch("notifier.channels.webhook.time.sleep"):
        receipts = notify(
            SignalEvent(symbol="BTC", score=5, direction="LONG",
                         entry=1, sl=1, tp=1),
            cfg=_cfg_with_webhook(telegram_enabled=True),
        )

    statuses = {r.channel: r.status for r in receipts}
    assert statuses["telegram"] == "ok"
    assert statuses["webhook"] == "failed"
