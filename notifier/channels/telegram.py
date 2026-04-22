"""Telegram channel. Wraps the direct sendMessage API.

Replaces btc_api.push_telegram_direct / _send_telegram_raw while preserving
the same retry behavior (up to 3 attempts with exponential backoff)."""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from notifier.channels.base import Channel, DeliveryReceipt


log = logging.getLogger("notifier.telegram")

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self, cfg: dict[str, Any]):
        self._token = (cfg.get("telegram_bot_token") or "").strip()
        self._chat_id = (cfg.get("telegram_chat_id") or "").strip()

    def send(self, message: str, max_retries: int = 3) -> DeliveryReceipt:
        if not self._token or not self._chat_id:
            return DeliveryReceipt(channel=self.name, status="failed",
                                    error="telegram not configured (missing token or chat_id)")

        url = _TELEGRAM_API.format(token=self._token)
        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        last_error: str | None = None
        for attempt in range(1, max_retries + 1):
            try:
                r = requests.post(url, json=payload, timeout=10)
                if r.ok:
                    return DeliveryReceipt(channel=self.name, status="ok")

                # 4xx except 429 are permanent — fail fast instead of wasting retries.
                if 400 <= r.status_code < 500 and r.status_code != 429:
                    err = f"HTTP {r.status_code}: {r.text[:200]}"
                    log.error("telegram permanent error, not retrying: %s", err)
                    return DeliveryReceipt(channel=self.name, status="failed", error=err)

                last_error = f"HTTP {r.status_code}: {r.text[:200]}"
                log.warning("telegram attempt %d/%d failed: %s", attempt, max_retries, last_error)

                # 429 respects Retry-After header when present.
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
                log.warning("telegram attempt %d/%d exception: %s", attempt, max_retries, last_error)

            if attempt < max_retries:
                # Exponential backoff: 1s, 2s (and 4s+ if max_retries>3).
                time.sleep(2 ** (attempt - 1))

        return DeliveryReceipt(channel=self.name, status="failed", error=last_error)
