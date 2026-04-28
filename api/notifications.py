"""Notifications API — thin router wrapper.

Extracted from btc_api.py in PR6 of the api+db refactor (2026-04-27).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from api.deps import verify_api_key
from db.connection import get_db

log = logging.getLogger("api.notifications")

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", dependencies=[Depends(verify_api_key)])
def get_notifications(
    unread: bool = True,
    limit: int = Query(50, ge=1, le=200,
                        description="Max rows returned (capped to prevent unbounded scans)"),
):
    """List notifications recorded by the notifier.

    By default returns only unread entries; pass ?unread=false to include
    read ones too. Sorted most-recent-first.
    """
    from notifier._storage import list_unread
    if not unread:
        # Full list (both read + unread) — use a direct query since list_unread
        # filters on read_at IS NULL.
        con = get_db()
        try:
            rows = con.execute(
                """SELECT id, event_type, event_key, priority, payload_json,
                          channels_sent, delivery_status, sent_at, read_at, error_log
                   FROM notifications_sent
                   ORDER BY sent_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        finally:
            con.close()
        cols = ("id", "event_type", "event_key", "priority", "payload_json",
                "channels_sent", "delivery_status", "sent_at", "read_at", "error_log")
        return {"notifications": [dict(zip(cols, r)) for r in rows]}
    return {"notifications": list_unread(limit=limit)}


@router.post("/{notif_id}/read", dependencies=[Depends(verify_api_key)])
def post_notification_read(notif_id: int):
    """Mark a single notification as read."""
    from notifier._storage import mark_read
    mark_read(notif_id)
    return {"ok": True, "id": notif_id}


@router.post("/read-all", dependencies=[Depends(verify_api_key)])
def post_notifications_read_all():
    """Mark all currently-unread notifications as read. Returns how many were updated."""
    from notifier._storage import mark_all_read
    n = mark_all_read()
    return {"ok": True, "marked": n}
