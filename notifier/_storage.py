"""Thin wrapper around signals.db for notification records.

Uses btc_api.get_db() so tests monkeypatching DB_FILE work transparently.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


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
) -> int:
    conn = _conn()
    try:
        cur = conn.execute(
            """INSERT INTO notifications_sent
               (event_type, event_key, priority, payload_json,
                channels_sent, delivery_status, sent_at, error_log)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_type, event_key, priority,
                json.dumps(payload, default=str),
                ",".join(channels_sent), delivery_status,
                _now_iso(), error_log,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_unread(limit: int = 50) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        rows = conn.execute(
            """SELECT id, event_type, event_key, priority, payload_json,
                      channels_sent, delivery_status, sent_at, read_at, error_log
               FROM notifications_sent
               WHERE read_at IS NULL
               ORDER BY sent_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    cols = ["id", "event_type", "event_key", "priority", "payload_json",
            "channels_sent", "delivery_status", "sent_at", "read_at", "error_log"]
    return [dict(zip(cols, r)) for r in rows]


def mark_read(notification_id: int) -> None:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE notifications_sent SET read_at = ? WHERE id = ?",
            (_now_iso(), notification_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_all_read() -> int:
    conn = _conn()
    try:
        cur = conn.execute(
            "UPDATE notifications_sent SET read_at = ? WHERE read_at IS NULL",
            (_now_iso(),),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
