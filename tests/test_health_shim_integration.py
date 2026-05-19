"""push_telegram_direct (the notifier shim) must stamp the symbol's current
health_state into the SignalEvent so ALERT symbols get the warning prefix."""
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    # Reset ratelimit between tests
    from notifier import ratelimit
    ratelimit.reset_all_for_tests()
    yield db_path


def _cfg():
    return {
        "notifier": {"enabled": True, "test_mode": False},
        "telegram_bot_token": "t", "telegram_chat_id": "1",
    }


def test_shim_sends_signal_with_health_state_from_db(tmp_db):
    """If get_symbol_state(sym) == 'ALERT', the SignalEvent carries health_state='ALERT'."""
    from health import apply_transition
    import btc_api

    apply_transition(
        "BTC", new_state="ALERT", reason="wr_below_threshold",
        metrics={"trades_count_total": 50, "win_rate_20_trades": 0.1,
                 "pnl_30d": 0.0, "pnl_by_month": {}, "months_negative_consecutive": 0},
        from_state="NORMAL",
    )

    rep = {"symbol": "BTC", "score": 6, "direction": "LONG",
           "price": 50_000.0, "sizing_1h": {"sl_precio": 49_000.0, "tp_precio": 55_000.0}}

    fake_resp = MagicMock()
    fake_resp.ok = True
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"ok": True}

    with patch("notifier.channels.telegram.requests.post", return_value=fake_resp) as mock_post:
        result = btc_api.push_telegram_direct(rep, _cfg())

    assert result is True
    assert mock_post.call_count == 1
    sent_text = mock_post.call_args.kwargs["json"]["text"]
    assert sent_text.startswith("⚠️ *ALERT*\n"), f"no prefix on shim-routed signal: {sent_text!r}"
    assert "BTC" in sent_text


def test_shim_unknown_symbol_defaults_to_normal(tmp_db):
    """If no row exists in symbol_health, health_state defaults to NORMAL → no prefix."""
    import btc_api

    rep = {"symbol": "UNSEEN", "score": 3, "direction": "LONG",
           "price": 1.0, "sizing_1h": {"sl_precio": 0.9, "tp_precio": 1.2}}

    fake_resp = MagicMock()
    fake_resp.ok = True

    with patch("notifier.channels.telegram.requests.post", return_value=fake_resp) as mock_post:
        btc_api.push_telegram_direct(rep, _cfg())

    assert mock_post.call_count == 1, "no signal sent — later assertion would be misleading"
    sent_text = mock_post.call_args.kwargs["json"]["text"]
    assert not sent_text.startswith("⚠️"), f"unexpected prefix: {sent_text!r}"


def test_shim_propagates_lrc_pct_from_rep_to_signal_event(tmp_db):
    """#385: rep["lrc_1h"]["pct"] must land in the persisted notification
    payload so the bell can render the percentage instead of '?'.
    """
    import btc_api, json
    from notifier import _storage as storage

    rep = {
        "symbol": "PENDLE", "score": 5, "direction": "SHORT",
        "price": 4.20,
        "sizing_1h": {"sl_precio": 4.31, "tp_precio": 4.05},
        "lrc_1h": {"pct": 87.3, "upper": 4.40, "lower": 3.90, "mid": 4.15},
    }
    fake_resp = MagicMock()
    fake_resp.ok = True

    with patch("notifier.channels.telegram.requests.post", return_value=fake_resp):
        btc_api.push_telegram_direct(rep, _cfg())

    # The notifier persists the SignalEvent payload to notifications_sent.
    rows = storage.list_unread(tenant_id=None)
    signal_rows = [r for r in rows if r["event_type"] == "signal"]
    assert signal_rows, "no signal notification persisted"
    payload = json.loads(signal_rows[0]["payload_json"])
    assert payload.get("lrc_pct") == pytest.approx(87.3), (
        f"lrc_pct missing from persisted payload — bell will still render '?'. "
        f"Got payload keys: {list(payload.keys())}"
    )


def test_shim_handles_missing_lrc_gracefully(tmp_db):
    """When the scanner report omits lrc_1h (corrupted run, partial fixture),
    the persisted payload carries lrc_pct=None and the bell will render '?'
    — never fabricates a value."""
    import btc_api, json
    from notifier import _storage as storage

    rep = {
        "symbol": "JUP", "score": 4, "direction": "LONG",
        "price": 1.20,
        "sizing_1h": {"sl_precio": 1.10, "tp_precio": 1.35},
        # NO lrc_1h key on purpose
    }
    fake_resp = MagicMock()
    fake_resp.ok = True

    with patch("notifier.channels.telegram.requests.post", return_value=fake_resp):
        btc_api.push_telegram_direct(rep, _cfg())

    rows = storage.list_unread(tenant_id=None)
    signal_rows = [r for r in rows if r["event_type"] == "signal"]
    assert signal_rows, "no signal notification persisted"
    payload = json.loads(signal_rows[0]["payload_json"])
    assert payload.get("lrc_pct") is None
