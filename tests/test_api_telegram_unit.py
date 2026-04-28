"""Unit tests for api/telegram.py — verify message format and side effects unchanged."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def cfg():
    return {
        "telegram_bot_token": "test-token",
        "telegram_chat_id": "test-chat",
        "webhook_url": "http://test.local/hook",
        "webhook_secret": "test-secret",
        "proxy": "",
    }


@pytest.fixture
def signal_rep():
    return {
        "symbol": "BTCUSDT",
        "estado": "LONG",
        "direction": "LONG",
        "score": 5,
        "score_label": "premium",
        "señal_activa": True,
        "lrc_1h": {"pct": 20.0},
        "rsi_1h": 40.0,
        "macro_4h": {"price_above": True},
        "gatillo_5m": {"vela_5m_alcista": True, "rsi_recuperando": True},
        "price": 50000.0,
        "timestamp": "2026-01-15T10:00:00Z",
        "sizing_1h": {
            "sl_precio": 49000.0,
            "tp_precio": 54000.0,
            "atr_1h": 500.0,
            "qty_btc": 0.002,
            "sl_pct": "2%",
            "tp_pct": "4%",
        },
        "confirmations": {},
    }


def test_build_message_signal_active(signal_rep):
    from api.telegram import build_telegram_message
    msg = build_telegram_message(signal_rep)
    assert "BTCUSDT" in msg
    assert "LONG" in msg
    assert "5/9" in msg
    assert "$50,000.00" in msg
    assert "premium" in msg


def test_build_message_neutral():
    from api.telegram import build_telegram_message
    rep = {
        "symbol": "ETHUSDT", "estado": "NEUTRAL", "score": 1, "score_label": "weak",
        "lrc_1h": {"pct": 50.0}, "macro_4h": {"price_above": False},
        "price": 3000.0, "timestamp": "2026-01-15T10:00:00Z",
        "sizing_1h": {}, "gatillo_5m": {}, "confirmations": {},
    }
    msg = build_telegram_message(rep)
    assert "Scanner Update ETHUSDT" in msg
    assert "$3,000.00" in msg


def test_send_telegram_raw_uses_bot_api(cfg):
    from api.telegram import _send_telegram_raw
    with patch("api.telegram.req_lib.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, ok=True, text="OK")
        _send_telegram_raw("hello", cfg)
        assert mock_post.called
        url = mock_post.call_args[0][0]
        assert "api.telegram.org" in url
        assert "test-token" in url


def test_send_telegram_raw_skips_when_no_creds():
    from api.telegram import _send_telegram_raw
    cfg = {"telegram_bot_token": "", "telegram_chat_id": ""}
    with patch("api.telegram.req_lib.post") as mock_post:
        _send_telegram_raw("hello", cfg)
        assert not mock_post.called


def test_push_webhook_skips_when_no_url(signal_rep):
    from api.telegram import push_webhook
    cfg_no_url = {"webhook_url": "", "telegram_chat_id": "test-chat"}
    with patch("api.telegram.req_lib.post") as mock_post:
        push_webhook(signal_rep, scan_id=1, cfg=cfg_no_url)
        assert not mock_post.called


def test_push_webhook_writes_audit_row(signal_rep, cfg, tmp_path, monkeypatch):
    """push_webhook must insert into webhooks_sent table after the HTTP call."""
    db_path = tmp_path / "test.db"

    import db.connection as dbconn
    monkeypatch.setattr(dbconn, "DB_FILE", str(db_path))

    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(db_path))

    from db.schema import init_db
    init_db()

    from api.telegram import push_webhook
    with patch("api.telegram.req_lib.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, ok=True)
        push_webhook(signal_rep, scan_id=42, cfg=cfg)

    from db.connection import get_db
    con = get_db()
    rows = con.execute("SELECT scan_id, url, status, ok FROM webhooks_sent").fetchall()
    con.close()
    assert len(rows) == 1
    assert rows[0][0] == 42  # scan_id
    assert rows[0][3] == 1   # ok=True
