"""Generic webhook channel — POST JSON to one or more configured URLs.

Compatible with n8n, Discord webhooks, Slack incoming webhooks, and any
generic JSON-receiver. Multi-endpoint support per event type via config:

    "notifier": {
        "channels": {
            "webhook": {
                "enabled": true,
                "endpoints": [
                    {"url": "http://localhost:5678/webhook/trading",
                     "types": ["signal", "health"]},
                    {"url": "https://discord.com/api/webhooks/...",
                     "types": ["position_exit"]}
                ]
            }
        }
    }

Each endpoint opts into the event types it wants. An endpoint with no `types`
entry receives all events. Transport: `requests.post` with 4xx fail-fast,
429 Retry-After honored, exponential backoff on 5xx (1s, 2s, 4s).
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from api.security.url_validation import validate_outbound_url
from notifier.channels.base import Channel, DeliveryReceipt


log = logging.getLogger("notifier.webhook")


class WebhookChannel(Channel):
    name = "webhook"

    def __init__(self, cfg: dict[str, Any]):
        """cfg is the full app config; we read notifier.channels.webhook.endpoints."""
        notif_cfg = (cfg.get("notifier") or {})
        ch_cfg = ((notif_cfg.get("channels") or {}).get("webhook") or {})
        self._enabled = bool(ch_cfg.get("enabled", False))
        self._endpoints: list[dict[str, Any]] = list(ch_cfg.get("endpoints") or [])
        # SSRF opt-in (#127): operators running trusted internal webhooks can
        # set cfg.security.webhook_allow_private_ips=True. Link-local / IMDS
        # is always blocked regardless of this flag.
        self._allow_private = bool(
            (cfg.get("security") or {}).get("webhook_allow_private_ips", False)
        )

    def _filter_endpoints_for(self, event_type: str) -> list[dict[str, Any]]:
        matched: list[dict[str, Any]] = []
        for ep in self._endpoints:
            types = ep.get("types")
            if not types or event_type in types:
                matched.append(ep)
        return matched

    def send(self, message: str, event_type: str = "", max_retries: int = 3) -> DeliveryReceipt:
        """`message` is the rendered JSON string from the <event>.webhook.j2 template."""
        if not self._enabled:
            return DeliveryReceipt(channel=self.name, status="failed",
                                    error="webhook channel disabled in config")
        endpoints = self._filter_endpoints_for(event_type)
        if not endpoints:
            return DeliveryReceipt(channel=self.name, status="failed",
                                    error=f"no webhook endpoint subscribes to event_type={event_type!r}")

        # If any endpoint succeeds, the overall result is ok. Aggregate errors otherwise.
        any_ok = False
        errors: list[str] = []
        for ep in endpoints:
            url = ep.get("url", "").strip()
            if not url:
                errors.append("endpoint has empty url")
                continue
            # SSRF guard #127: any operator-configurable webhook URL is a potential
            # SSRF sink. Validate before each send (defense in depth + TOCTOU).
            try:
                url = validate_outbound_url(url, allow_private=self._allow_private)
            except ValueError as e:
                err_msg = f"{url}: rechazado por SSRF guard — {e}"
                log.warning("Endpoint bloqueado: %s", err_msg)
                errors.append(err_msg)
                continue
            ok, err = self._post_with_retry(url, message, max_retries=max_retries)
            if ok:
                any_ok = True
            else:
                errors.append(f"{url}: {err}")
        if any_ok:
            return DeliveryReceipt(
                channel=self.name, status="ok",
                error=(None if not errors else " | ".join(errors)),
            )
        return DeliveryReceipt(channel=self.name, status="failed",
                                error=" | ".join(errors) or "all endpoints failed")

    def _post_with_retry(self, url: str, body: str, max_retries: int) -> tuple[bool, str | None]:
        last_error: str | None = None
        for attempt in range(1, max_retries + 1):
            try:
                r = requests.post(url, data=body,
                                   headers={"Content-Type": "application/json"},
                                   timeout=10)
                if r.ok:
                    return True, None
                if 400 <= r.status_code < 500 and r.status_code != 429:
                    err = f"HTTP {r.status_code}: {r.text[:200]}"
                    log.error("webhook permanent error, not retrying: %s", err)
                    return False, err
                last_error = f"HTTP {r.status_code}: {r.text[:200]}"
                log.warning("webhook attempt %d/%d failed: %s",
                             attempt, max_retries, last_error)
                if r.status_code == 429 and attempt < max_retries:
                    try:
                        retry_after = int(r.headers.get("Retry-After", "0"))
                    except (TypeError, ValueError):
                        retry_after = 0
                    if retry_after > 0:
                        time.sleep(retry_after)
                        continue
            except requests.RequestException as e:
                last_error = f"{type(e).__name__}: {e}"
                log.warning("webhook attempt %d/%d exception: %s",
                             attempt, max_retries, last_error)
            if attempt < max_retries:
                time.sleep(2 ** (attempt - 1))  # 1s, 2s, 4s
        return False, last_error
