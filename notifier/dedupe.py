"""DB-backed sliding-window deduplication for notifier.notify().

Query shape:
  SELECT 1 FROM notifications_sent
  WHERE event_type=? AND event_key=?
        AND sent_at >= (now - window_seconds)
  LIMIT 1

Connection lifecycle is owned by db.transaction.transaction() (Task 8 of
the transaction unit-of-work refactor). We never touch commit/close.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone


def should_send(
    con: sqlite3.Connection,
    event_type: str,
    event_key: str,
    window_seconds: int,
    priority: str = "info",
) -> bool:
    """Return True if this event should be sent (no recent duplicate found).

    Critical-priority events always pass. Window of 0 or negative disables dedupe.

    The string comparison `sent_at >= ?` works correctly only because both
    writers (notifier._storage._now_iso) and this reader build the timestamp
    via `datetime.now(timezone.utc).isoformat()`, which yields a consistent
    `"...+00:00"` suffix. Future writers to notifications_sent.sent_at MUST
    use the same convention or the window comparison will silently misfire.
    """
    if priority == "critical":
        return True
    if window_seconds <= 0:
        return True

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    row = con.execute(
        """SELECT 1 FROM notifications_sent
           WHERE event_type = ? AND event_key = ? AND sent_at >= ?
           LIMIT 1""",
        (event_type, event_key, cutoff.isoformat()),
    ).fetchone()
    return row is None
