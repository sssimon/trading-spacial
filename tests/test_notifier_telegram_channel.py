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
        with patch("notifier.channels.telegram.time.sleep") as mock_sleep:
            receipt = channel.send("hello")

    assert receipt.status == "ok"
    assert mock_post.call_count == 3
    # Backoff: sleep(1) after attempt 1, sleep(2) after attempt 2, no sleep after success
    assert mock_sleep.call_count == 2
    assert mock_sleep.call_args_list[0].args[0] == 1
    assert mock_sleep.call_args_list[1].args[0] == 2


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


def test_telegram_send_does_not_retry_on_4xx(telegram_cfg):
    """4xx (bad token, bad payload) are permanent errors — no point retrying."""
    from notifier.channels.telegram import TelegramChannel
    channel = TelegramChannel(telegram_cfg)

    fail_resp = MagicMock()
    fail_resp.ok = False
    fail_resp.status_code = 401
    fail_resp.text = "Unauthorized"

    with patch("notifier.channels.telegram.requests.post", return_value=fail_resp) as mock_post:
        with patch("notifier.channels.telegram.time.sleep") as mock_sleep:
            receipt = channel.send("hello")

    assert receipt.status == "failed"
    assert "401" in receipt.error
    assert mock_post.call_count == 1  # only 1 attempt, no retries
    assert mock_sleep.call_count == 0


def test_telegram_send_429_respects_retry_after(telegram_cfg):
    """429 Too Many Requests should honor the Retry-After header."""
    from notifier.channels.telegram import TelegramChannel
    channel = TelegramChannel(telegram_cfg)

    rate_limited = MagicMock()
    rate_limited.ok = False
    rate_limited.status_code = 429
    rate_limited.text = "Too Many Requests"
    rate_limited.headers = {"Retry-After": "5"}
    ok_resp = MagicMock()
    ok_resp.ok = True
    ok_resp.status_code = 200
    ok_resp.json.return_value = {"ok": True}

    with patch("notifier.channels.telegram.requests.post",
                side_effect=[rate_limited, ok_resp]):
        with patch("notifier.channels.telegram.time.sleep") as mock_sleep:
            receipt = channel.send("hello")

    assert receipt.status == "ok"
    # Sleep was called once with Retry-After value (5), not the default exponential
    assert mock_sleep.call_count == 1
    assert mock_sleep.call_args_list[0].args[0] == 5
