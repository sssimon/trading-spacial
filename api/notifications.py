"""Notifications API — thin router wrapper.

Extracted from btc_api.py in PR6 of the api+db refactor (2026-04-27).

## Multi-tenancy (B.5 follow-up #258 — 2026-05-15)

All endpoints use Depends(get_current_tenant_id) — tenant scoped from JWT,
never from request. Strict filter: NULL tenant_id rows invisible per
established B.5 pattern (PR #363).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import verify_api_key
from auth.dependencies import get_current_tenant_id
from db.connection import get_db

log = logging.getLogger("api.notifications")

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", dependencies=[Depends(verify_api_key)])
def get_notifications(
    unread: bool = True,
    limit: int = Query(50, ge=1, le=200,
                        description="Max rows returned (capped to prevent unbounded scans)"),
    tenant_id: int = Depends(get_current_tenant_id),
):
    """List notifications recorded by the notifier.

    By default returns only unread entries; pass ?unread=false to include
    read ones too. Sorted most-recent-first. B.5 #258: scoped to tenant_id
    from JWT — pre-backfill (NULL tenant_id) notifications invisible.
    """
    from notifier._storage import list_unread
    if not unread:
        # Full list (both read + unread) — direct query (list_unread filters on read_at).
        con = get_db()
        try:
            rows = con.execute(
                """SELECT id, event_type, event_key, priority, payload_json,
                          channels_sent, delivery_status, sent_at, read_at, error_log
                   FROM notifications_sent
                   WHERE tenant_id = ?
                   ORDER BY sent_at DESC
                   LIMIT ?""",
                (tenant_id, limit),
            ).fetchall()
        finally:
            con.close()
        cols = ("id", "event_type", "event_key", "priority", "payload_json",
                "channels_sent", "delivery_status", "sent_at", "read_at", "error_log")
        return {"notifications": [dict(zip(cols, r)) for r in rows]}
    return {"notifications": list_unread(limit=limit, tenant_id=tenant_id)}


@router.post("/{notif_id}/read", dependencies=[Depends(verify_api_key)])
def post_notification_read(
    notif_id: int,
    tenant_id: int = Depends(get_current_tenant_id),
):
    """Mark a single notification as read. B.5 #258: ownership-enforced.

    Returns 404 if notification does not belong to current tenant (IDOR
    protection — same response as 'not found').
    """
    from notifier._storage import mark_read
    updated = mark_read(notif_id, tenant_id=tenant_id)
    if not updated:
        raise HTTPException(
            status_code=404, detail=f"Notification #{notif_id} not found",
        )
    return {"ok": True, "id": notif_id}


@router.post("/read-all", dependencies=[Depends(verify_api_key)])
def post_notifications_read_all(
    tenant_id: int = Depends(get_current_tenant_id),
):
    """Mark all currently-unread notifications for current tenant as read.

    B.5 #258: scope-limited to tenant_id — affects only current user's queue.
    """
    from notifier._storage import mark_all_read
    n = mark_all_read(tenant_id=tenant_id)
    return {"ok": True, "marked": n}
