"""TelegramChannel refactors push_telegram_direct behind a Channel ABC.
Uses requests mocking to avoid real HTTP."""
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def telegram_cfg():
    return {"telegram_bot_token": "test-token", "telegram_chat_id": "12345"}


def test_telegram_send_success(telegram_cfg):
    from notifier.channels.telegram import TelegramChannel
    channel = TelegramChannel(telegram_cfg)

    fake_response = MagicMock()
    fake_response.ok = True
    fake_response.status_code = 200
    fake_response.json.return_value = {"ok": True, "result": {"message_id": 42}}

    with patch("notifier.channels.telegram.requests.post", return_value=fake_response) as mock_post:
        receipt = channel.send("hello")

    assert receipt.status == "ok"
    assert mock_post.call_count == 1
    args, kwargs = mock_post.call_args
    assert "test-token" in args[0]
    assert kwargs["json"]["chat_id"] == "12345"
    assert kwargs["json"]["text"] == "hello"


def test_telegram_send_retries_on_transient_failure(telegram_cfg):
    from notifier.channels.telegram import TelegramChannel
    channel = TelegramChannel(telegram_cfg)

    fail_resp = MagicMock()
    fail_resp.ok = False
    fail_resp.status_code = 500
    fail_resp.text = "server error"
    ok_resp = MagicMock()
    ok_resp.ok = True
    ok_resp.status_code = 200
    ok_resp.json.return_value = {"ok": True}

    with patch("notifier.channels.telegram.requests.post",
                side_effect=[fail_resp, fail_resp, ok_resp]) as mock_post:
        with patch("notifier.channels.telegram.time.sleep"):
            receipt = channel.send("hello")

    assert receipt.status == "ok"
    assert mock_post.call_count == 3


def test_telegram_send_gives_up_after_max_retries(telegram_cfg):
    from notifier.channels.telegram import TelegramChannel
    channel = TelegramChannel(telegram_cfg)

    fail_resp = MagicMock()
    fail_resp.ok = False
    fail_resp.status_code = 500
    fail_resp.text = "server error"

    with patch("notifier.channels.telegram.requests.post", return_value=fail_resp):
        with patch("notifier.channels.telegram.time.sleep"):
            receipt = channel.send("hello", max_retries=2)

    assert receipt.status == "failed"
    assert "server error" in (receipt.error or "")


def test_telegram_send_noop_when_not_configured():
    from notifier.channels.telegram import TelegramChannel
    # No token/chat_id — channel reports failed without attempting HTTP
    channel = TelegramChannel({})
    receipt = channel.send("hello")
    assert receipt.status == "failed"
    assert "not configured" in receipt.error.lower()
