"""Audit trail for auth events.

log_auth_event() is failure-tolerant: it INSERTs in its own connection and
swallows any exception, falling back to stderr. The audit trail is
important, but not more important than login working. We never block the
auth flow on a DB write failure.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Optional

from db.connection import get_db

log = logging.getLogger("auth.audit")

VALID_EVENT_TYPES = frozenset({
    "login_success",
    "login_failed",
    "logout",
    "refresh",
    "password_change",
    "role_change",
    "refresh_reuse_detected",  # token theft — also revokes family
})


def log_auth_event(
    *,
    event_type: str,
    success: bool,
    user_id: Optional[int] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Insert one row into auth_events. Never raises.

    Sensitive fields (passwords, tokens — even hashed) MUST NOT appear in
    `metadata`. Callers are responsible. Common metadata keys: 'reason',
    'family_id', 'rotation_count', 'old_role'/'new_role'.
    """
    if event_type not in VALID_EVENT_TYPES:
        # Defensive: unknown event types still get logged but flagged.
        log.warning("log_auth_event: unknown event_type=%r", event_type)

    metadata_json = json.dumps(metadata) if metadata else None
    ts = datetime.now(timezone.utc).isoformat()

    try:
        con = get_db()
        try:
            con.execute(
                """
                INSERT INTO auth_events
                    (user_id, event_type, ip, user_agent, ts, success, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    event_type,
                    ip,
                    user_agent,
                    ts,
                    1 if success else 0,
                    metadata_json,
                ),
            )
            con.commit()
        finally:
            con.close()
    except Exception as exc:
        # Audit failure must NOT break the calling flow. Log to stderr with
        # enough detail to reconstruct the event later from server logs.
        # NOTE: do NOT log password/token data even on the failure path —
        # callers contracts forbid passing them in metadata.
        sys.stderr.write(
            f"[auth.audit] failed to persist event_type={event_type!r} "
            f"user_id={user_id!r} success={success}: {type(exc).__name__}: {exc}\n"
        )
        sys.stderr.flush()
