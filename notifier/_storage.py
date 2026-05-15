"""Thin wrapper around signals.db for notification records.

Uses btc_api.get_db() so tests monkeypatching DB_FILE work transparently.

## Multi-tenancy (B.5 follow-up #258 — 2026-05-15)

All functions accept optional `tenant_id: int | None = None`:
- `None` (default) — legacy behavior; system broadcasts inserted with tenant_id=NULL
- `int` — strict filter (rows match exact tenant_id, NULL rows invisible)

Caveat: scanner-emitted notifications (system-level) call record_delivery
without tenant context → inserted as NULL → invisible to authenticated
per-user listings. B.4 (signal subscriptions + notification routing) is
the proper home for fan-out logic to per-user notifications.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn():
    import btc_api
    return btc_api.get_db()


def record_delivery(
    event_type: str,
    event_key: str,
    priority: str,
    payload: dict[str, Any],
    channels_sent: list[str],
    delivery_status: str,
    error_log: str | None = None,
    tenant_id: Optional[int] = None,
) -> int:
    conn = _conn()
    try:
        cur = conn.execute(
            """INSERT INTO notifications_sent
               (event_type, event_key, priority, payload_json,
                channels_sent, delivery_status, sent_at, error_log, tenant_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_type, event_key, priority,
                json.dumps(payload, default=str),
                ",".join(channels_sent), delivery_status,
                _now_iso(), error_log, tenant_id,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_unread(
    limit: int = 50,
    tenant_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        if tenant_id is None:
            rows = conn.execute(
                """SELECT id, event_type, event_key, priority, payload_json,
                          channels_sent, delivery_status, sent_at, read_at, error_log
                   FROM notifications_sent
                   WHERE read_at IS NULL
                   ORDER BY sent_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, event_type, event_key, priority, payload_json,
                          channels_sent, delivery_status, sent_at, read_at, error_log
                   FROM notifications_sent
                   WHERE read_at IS NULL AND tenant_id = ?
                   ORDER BY sent_at DESC
                   LIMIT ?""",
                (tenant_id, limit),
            ).fetchall()
    finally:
        conn.close()
    cols = ["id", "event_type", "event_key", "priority", "payload_json",
            "channels_sent", "delivery_status", "sent_at", "read_at", "error_log"]
    return [dict(zip(cols, r)) for r in rows]


def mark_read(
    notification_id: int,
    tenant_id: Optional[int] = None,
) -> bool:
    """Mark notification as read. Ownership-enforced when tenant_id provided.

    Returns True if a row was updated, False if not (e.g., IDOR: trying to
    mark another user's notification).
    """
    conn = _conn()
    try:
        if tenant_id is None:
            cur = conn.execute(
                "UPDATE notifications_sent SET read_at = ? WHERE id = ?",
                (_now_iso(), notification_id),
            )
        else:
            cur = conn.execute(
                "UPDATE notifications_sent SET read_at = ? "
                "WHERE id = ? AND tenant_id = ?",
                (_now_iso(), notification_id, tenant_id),
            )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def mark_all_read(tenant_id: Optional[int] = None) -> int:
    """Mark all unread as read. Scope-limited by tenant_id when provided."""
    conn = _conn()
    try:
        if tenant_id is None:
            cur = conn.execute(
                "UPDATE notifications_sent SET read_at = ? WHERE read_at IS NULL",
                (_now_iso(),),
            )
        else:
            cur = conn.execute(
                "UPDATE notifications_sent SET read_at = ? "
                "WHERE read_at IS NULL AND tenant_id = ?",
                (_now_iso(), tenant_id),
            )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
