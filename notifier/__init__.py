"""Centralized notifier (#162). Public API: notify, event types."""
from __future__ import annotations

import logging
from typing import Any

from notifier import dedupe, ratelimit
from notifier._storage import record_delivery
from notifier._templates import render
from notifier.channels.base import DeliveryReceipt
from notifier.channels.telegram import TelegramChannel
from notifier.channels.webhook import WebhookChannel
from notifier.events import (
    SignalEvent, HealthEvent, InfraEvent, SystemEvent, PositionExitEvent,
    Event,
)


__all__ = [
    "notify",
    "SignalEvent", "HealthEvent", "InfraEvent", "SystemEvent", "PositionExitEvent",
    "Event", "DeliveryReceipt",
]


log = logging.getLogger("notifier")


_DEFAULT_CHANNELS_BY_EVENT_TYPE: dict[str, list[str]] = {
    "signal":        ["telegram"],
    "health":        ["telegram"],
    "infra":         ["telegram"],
    "system":        ["telegram"],
    "position_exit": ["telegram"],
}

_DEFAULT_DEDUPE_SECONDS_BY_EVENT_TYPE: dict[str, int] = {
    "signal":        0,      # no dedupe — signals are rare and each matters
    "health":        1800,   # 30 min
    "infra":         300,    # 5 min
    "system":        0,
    "position_exit": 0,      # each exit is a discrete event; no dedupe
}


def _resolve_channels(event: Event, cfg: dict) -> list[str]:
    notif_cfg = cfg.get("notifier", {}) or {}
    overrides = (notif_cfg.get("channels_by_event_type") or {})
    return overrides.get(event.event_type,
                          _DEFAULT_CHANNELS_BY_EVENT_TYPE.get(event.event_type, ["telegram"]))


def _resolve_dedupe_window(event: Event, cfg: dict) -> int:
    notif_cfg = cfg.get("notifier", {}) or {}
    dedupe_cfg = notif_cfg.get("dedupe", {}) or {}
    per_type = dedupe_cfg.get("by_event_type", {}) or {}
    if event.event_type in per_type:
        return int(per_type[event.event_type])
    default_min = dedupe_cfg.get("default_window_minutes")
    if default_min is not None:
        return int(default_min) * 60
    return _DEFAULT_DEDUPE_SECONDS_BY_EVENT_TYPE.get(event.event_type, 0)


def notify(event: Event, cfg: dict) -> list[DeliveryReceipt]:
    """Send an event through configured channels with dedupe + ratelimit.

    Returns [] if: notifier disabled, or the event was deduped, or no channels configured.
    Returns list of DeliveryReceipt (one per channel attempted) otherwise.
    """
    notif_cfg = cfg.get("notifier", {}) or {}
    if not notif_cfg.get("enabled", True):
        log.info("notify skipped (notifier disabled): %s %s",
                  event.event_type, event.dedupe_key)
        return []

    window_seconds = _resolve_dedupe_window(event, cfg)
    if not dedupe.should_send(event.event_type, event.dedupe_key,
                                window_seconds=window_seconds,
                                priority=event.priority):
        log.debug("notify deduped: %s %s", event.event_type, event.dedupe_key)
        return []

    test_mode = notif_cfg.get("test_mode", False)
    channels = _resolve_channels(event, cfg)
    receipts: list[DeliveryReceipt] = []
    channels_sent: list[str] = []
    any_error: str | None = None

    for channel_name in channels:
        # Channel factory check happens first — no point renderinga template for
        # a channel we cannot dispatch to.
        if channel_name == "telegram":
            channel = TelegramChannel(cfg)
        elif channel_name == "webhook":
            channel = WebhookChannel(cfg)
        else:
            log.warning("notify: unsupported channel %r (email lands in a future PR)",
                         channel_name)
            receipts.append(DeliveryReceipt(channel=channel_name, status="failed",
                                              error=f"unsupported channel: {channel_name}"))
            continue

        bucket = ratelimit.bucket_for(channel_name)
        if not bucket.acquire():
            receipts.append(DeliveryReceipt(channel=channel_name, status="rate_limited",
                                              error="bucket empty"))
            continue

        # Render through template
        try:
            message = render(event, channel=channel_name)
        except Exception as e:
            receipts.append(DeliveryReceipt(channel=channel_name, status="failed",
                                              error=f"render failed: {e}"))
            any_error = any_error or str(e)
            continue

        if test_mode:
            receipts.append(DeliveryReceipt(channel=channel_name, status="ok",
                                              error="test_mode"))
            channels_sent.append(channel_name)
            continue

        # WebhookChannel takes event_type as an extra arg so it can route to
        # endpoint subscribers. Other channels ignore the kwarg.
        if channel_name == "webhook":
            receipt = channel.send(message, event_type=event.event_type)
        else:
            receipt = channel.send(message)
        receipts.append(receipt)
        if receipt.status == "ok":
            channels_sent.append(channel_name)
        else:
            any_error = any_error or receipt.error

    delivery_status = "ok" if channels_sent else "failed"
    if channels_sent and any_error:
        delivery_status = "partial"

    try:
        record_delivery(
            event_type=event.event_type,
            event_key=event.dedupe_key,
            priority=event.priority,
            payload=event.to_dict(),
            channels_sent=channels_sent or ["none"],
            delivery_status=delivery_status,
            error_log=any_error,
        )
    except Exception:
        log.exception("notifier failed to persist delivery record")

    return receipts
