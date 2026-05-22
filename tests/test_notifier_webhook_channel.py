"""WebhookChannel tests — POST JSON to configured endpoints.

NOTE on test URLs: these tests mock requests.post, so the URL is never
actually dialed. URLs use 8.8.8.8 (public, globally routable) so the
SSRF guard added in #127 (notifier/channels/webhook.py) lets them past
the validation step and into the mocked HTTP call.
"""
from unittest.mock import patch, MagicMock

import pytest


def _cfg(endpoints=None, enabled=True):
    return {
        "notifier": {
            "channels": {
                "webhook": {
                    "enabled": enabled,
                    "endpoints": endpoints or [],
                },
            },
        },
    }


def test_webhook_disabled_by_default():
    """cfg without webhook.enabled → channel returns failed without HTTP."""
    from notifier.channels.webhook import WebhookChannel
    channel = WebhookChannel(_cfg(enabled=False))
    receipt = channel.send('{"foo":"bar"}', event_type="signal")
    assert receipt.status == "failed"
    assert "disabled" in receipt.error.lower()


def test_webhook_no_matching_endpoint():
    """An endpoint with types=['signal'] does NOT receive health events."""
    from notifier.channels.webhook import WebhookChannel
    cfg = _cfg([{"url": "http://8.8.8.8/x", "types": ["signal"]}])
    channel = WebhookChannel(cfg)
    receipt = channel.send('{"ok": true}', event_type="health")
    assert receipt.status == "failed"
    assert "no webhook endpoint subscribes" in receipt.error


def test_webhook_success_posts_json():
    """Single endpoint, event type matches → POST is made once with correct body."""
    from notifier.channels.webhook import WebhookChannel
    cfg = _cfg([{"url": "http://8.8.8.8/n8n-hook", "types": ["signal"]}])
    channel = WebhookChannel(cfg)

    ok = MagicMock()
    ok.ok = True
    ok.status_code = 200

    with patch("notifier.channels.webhook.requests.post", return_value=ok) as mock_post:
        receipt = channel.send('{"symbol":"BTC"}', event_type="signal")

    assert receipt.status == "ok"
    assert mock_post.call_count == 1
    call = mock_post.call_args
    assert call.args[0] == "http://8.8.8.8/n8n-hook"
    assert call.kwargs["data"] == '{"symbol":"BTC"}'
    assert call.kwargs["headers"]["Content-Type"] == "application/json"


def test_webhook_endpoint_without_types_receives_all():
    """An endpoint without 'types' filter accepts every event_type."""
    from notifier.channels.webhook import WebhookChannel
    cfg = _cfg([{"url": "http://8.8.8.8/catchall"}])  # no 'types' key
    channel = WebhookChannel(cfg)

    ok = MagicMock()
    ok.ok = True
    with patch("notifier.channels.webhook.requests.post", return_value=ok) as mock_post:
        channel.send('{"x":1}', event_type="infra")
        channel.send('{"x":2}', event_type="signal")
    assert mock_post.call_count == 2


def test_webhook_4xx_fail_fast():
    """401/400/403 etc. are permanent — no retry."""
    from notifier.channels.webhook import WebhookChannel
    cfg = _cfg([{"url": "http://8.8.8.8/bad-server"}])
    channel = WebhookChannel(cfg)

    fail = MagicMock()
    fail.ok = False
    fail.status_code = 401
    fail.text = "Unauthorized"

    with patch("notifier.channels.webhook.requests.post", return_value=fail) as mock_post:
        with patch("notifier.channels.webhook.time.sleep") as mock_sleep:
            receipt = channel.send('{}', event_type="signal")
    assert receipt.status == "failed"
    assert "401" in receipt.error
    assert mock_post.call_count == 1
    assert mock_sleep.call_count == 0


def test_webhook_5xx_retries_with_backoff():
    from notifier.channels.webhook import WebhookChannel
    cfg = _cfg([{"url": "http://8.8.8.8/flaky"}])
    channel = WebhookChannel(cfg)

    fail = MagicMock()
    fail.ok = False
    fail.status_code = 500
    fail.text = "server err"
    ok = MagicMock()
    ok.ok = True
    ok.status_code = 200

    with patch("notifier.channels.webhook.requests.post",
                side_effect=[fail, fail, ok]):
        with patch("notifier.channels.webhook.time.sleep") as mock_sleep:
            receipt = channel.send('{}', event_type="signal")
    assert receipt.status == "ok"
    # Backoff after attempts 1 and 2 (not after the successful attempt 3)
    assert mock_sleep.call_count == 2
    assert mock_sleep.call_args_list[0].args[0] == 1
    assert mock_sleep.call_args_list[1].args[0] == 2


def test_webhook_multiple_endpoints_partial_success():
    """If one endpoint succeeds and another fails, overall status is ok + error recorded."""
    from notifier.channels.webhook import WebhookChannel
    cfg = _cfg([
        {"url": "http://8.8.8.8/good"},
        {"url": "http://8.8.8.8/bad"},
    ])
    channel = WebhookChannel(cfg)

    ok = MagicMock(); ok.ok = True; ok.status_code = 200
    bad = MagicMock(); bad.ok = False; bad.status_code = 400; bad.text = "bad req"

    with patch("notifier.channels.webhook.requests.post",
                side_effect=[ok, bad]):
        receipt = channel.send('{}', event_type="signal")
    assert receipt.status == "ok"
    assert "http://8.8.8.8/bad" in receipt.error
