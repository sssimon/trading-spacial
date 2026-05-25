"""User preferences DB layer — per-user filter + notification config.

Introduced in B.5 follow-up B. Schema from B.1 #254.
Single row per tenant_id (enforced by UNIQUE INDEX idx_user_prefs_tenant).

JSON columns:
- symbol_filter_json: list[str] of symbol whitelist (or None for all)
- notify_channels_json: dict (e.g., {"telegram_chat_id": "...", "email": "..."})

Pre-reg: docs/superpowers/plans/2026-05-16-multi-tenant-b5-capital-prefs-pre-reg.md
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("db.user_preferences")


def _decode_row(row) -> dict:
    """Convert raw row to dict with JSON-parsed list/dict fields."""
    d = dict(row)
    if d.get("symbol_filter_json"):
        try:
            d["symbol_filter"] = json.loads(d["symbol_filter_json"])
        except (json.JSONDecodeError, TypeError):
            d["symbol_filter"] = None
    else:
        d["symbol_filter"] = None
    if d.get("notify_channels_json"):
        try:
            d["notify_channels"] = json.loads(d["notify_channels_json"])
        except (json.JSONDecodeError, TypeError):
            d["notify_channels"] = None
    else:
        d["notify_channels"] = None
    return d


def db_get_user_preferences(
    con: sqlite3.Connection,
    tenant_id: int,
) -> Optional[dict]:
    """Return preferences row for tenant, or None if not yet set.

    Task 8 (#446): `con` is now mandatory positional. Callers must pass an
    open `sqlite3.Connection` from a surrounding `transaction()` block.
    """
    row = con.execute(
        "SELECT * FROM user_preferences WHERE tenant_id = ?", (tenant_id,),
    ).fetchone()
    if row is None:
        return None
    return _decode_row(row)


def db_upsert_user_preferences(
    con: sqlite3.Connection,
    tenant_id: int,
    *,
    symbol_filter: Optional[list[str]] = None,
    min_score: Optional[int] = None,
    notify_channels: Optional[dict[str, Any]] = None,
) -> dict:
    """Insert or replace user_preferences row for tenant.

    None values:
    - On insert: column gets DB default (min_score=4 per schema; others NULL)
    - On update: preserve existing value (no overwrite)

    Returns resulting row with JSON fields parsed.

    Task 8 (#446): `con` is now mandatory positional first arg.
    """
    now = datetime.now(timezone.utc).isoformat()
    existing = con.execute(
        "SELECT * FROM user_preferences WHERE tenant_id = ?", (tenant_id,),
    ).fetchone()

    sf_json = json.dumps(symbol_filter) if symbol_filter is not None else None
    nc_json = json.dumps(notify_channels) if notify_channels is not None else None

    if existing is None:
        con.execute(
            """INSERT INTO user_preferences
               (tenant_id, symbol_filter_json, min_score, notify_channels_json,
                updated_at)
               VALUES (?, ?, COALESCE(?, 4), ?, ?)""",
            (tenant_id, sf_json, min_score, nc_json, now),
        )
    else:
        # Preserve fields when None passed
        effective_sf = sf_json if sf_json is not None else existing["symbol_filter_json"]
        effective_ms = min_score if min_score is not None else existing["min_score"]
        effective_nc = nc_json if nc_json is not None else existing["notify_channels_json"]
        con.execute(
            """UPDATE user_preferences
               SET symbol_filter_json = ?, min_score = ?,
                   notify_channels_json = ?, updated_at = ?
               WHERE tenant_id = ?""",
            (effective_sf, effective_ms, effective_nc, now, tenant_id),
        )
    row = con.execute(
        "SELECT * FROM user_preferences WHERE tenant_id = ?", (tenant_id,),
    ).fetchone()
    return _decode_row(row)
