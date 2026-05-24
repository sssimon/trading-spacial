"""B.4 #257 — per-user signal fan-out.

Replaces a single global `notify(SignalEvent, cfg)` call with one call per
active user, each filtered through their `user_preferences` row and routed
to their `notify_channels`.

Scope (pre-reg §2.1): only SignalEvent flows through here. Other event types
remain broadcast through the existing `notify()` API.

Pre-reg: docs/superpowers/plans/2026-05-16-multi-tenant-b4-signal-routing-pre-reg.md
"""
from __future__ import annotations

import logging
from typing import Any

from db.transaction import transaction
from db.user_preferences import db_get_user_preferences
from notifier import notify
from notifier.channels.base import DeliveryReceipt
from notifier.events import SignalEvent

log = logging.getLogger("notifier.dispatch_per_user")


# Pre-reg §2.3: sane defaults for a user with no preferences row yet.
_DEFAULT_MIN_SCORE = 4
_DEFAULT_SYMBOL_FILTER: list[str] | None = None  # None = all symbols allowed


def _list_active_users() -> list[dict]:
    """Return active users (id + email) for fan-out.

    Pre-bootstrap case: if the `users` table doesn't exist yet (auth schema
    not initialized — e.g. legacy scanner-only test fixtures), return []
    so callers fall back to the legacy broadcast path.
    """
    import sqlite3
    try:
        with transaction() as con:
            rows = con.execute(
                "SELECT id, email FROM users WHERE is_active = 1 ORDER BY id"
            ).fetchall()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            return []
        raise
    return [dict(r) for r in rows]


def _user_passes_filter(
    event: SignalEvent,
    symbol_filter: list[str] | None,
    min_score: int,
) -> bool:
    """Pre-reg §2.3 filter rules."""
    # symbol_filter=None → all symbols allowed
    # symbol_filter=[] → explicit empty whitelist (user opted into nothing)
    if symbol_filter is not None and event.symbol not in symbol_filter:
        return False
    if event.score < min_score:
        return False
    return True


def dispatch_signal_to_users(
    event: SignalEvent,
    base_cfg: dict[str, Any],
) -> dict[int, list[DeliveryReceipt]]:
    """Fan a SignalEvent out to each active user, honoring their preferences.

    Returns a dict mapping `user_id` → list of `DeliveryReceipt` for that user.
    Users who are filtered out (symbol/min_score) are absent from the dict.
    Users who pass the filter but have no channels configured still appear
    (with empty receipts) — useful for observability.
    """
    users = _list_active_users()
    if not users:
        log.debug("dispatch_signal_to_users: no active users — broadcast skipped")
        return {}

    out: dict[int, list[DeliveryReceipt]] = {}

    for user in users:
        prefs = db_get_user_preferences(user["id"])
        if prefs is None:
            symbol_filter = _DEFAULT_SYMBOL_FILTER
            min_score = _DEFAULT_MIN_SCORE
            notify_channels: dict[str, Any] = {}
        else:
            symbol_filter = prefs.get("symbol_filter")
            # The schema stores min_score as INT NOT NULL DEFAULT 4 — but be
            # defensive in case a future migration relaxes that.
            min_score = int(prefs.get("min_score") or _DEFAULT_MIN_SCORE)
            notify_channels = prefs.get("notify_channels") or {}

        if not _user_passes_filter(event, symbol_filter, min_score):
            log.debug(
                "dispatch: user_id=%s skipped — symbol=%s score=%s filter=%s min=%s",
                user["id"], event.symbol, event.score, symbol_filter, min_score,
            )
            continue

        # Pre-reg §2.4: build patched cfg from base + user channel overlay
        user_cfg = {**base_cfg, **notify_channels}

        receipts = notify(event, user_cfg, tenant_id=user["id"])
        out[user["id"]] = receipts
        log.info(
            "dispatch: user_id=%s symbol=%s score=%s receipts=%d",
            user["id"], event.symbol, event.score, len(receipts),
        )
    return out
