"""DB-backed sliding-window deduplication for notifier.notify().

Query shape:
  SELECT 1 FROM notifications_sent
  WHERE event_type=? AND event_key=?
        AND sent_at >= (now - window_seconds)
  LIMIT 1

IMPORTANT: close the sqlite connection we opened via btc_api.get_db(),
since that helper opens a fresh connection per call and does not manage
lifecycle itself (see Task 3 review feedback).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def should_send(
    event_type: str,
    event_key: str,
    window_seconds: int,
    priority: str = "info",
) -> bool:
    """Return True if this event should be sent (no recent duplicate found).

    Critical-priority events always pass. Window of 0 disables dedupe.
    """
    if priority == "critical":
        return True
    if window_seconds <= 0:
        return True

    import btc_api
    conn = btc_api.get_db()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
        row = conn.execute(
            """SELECT 1 FROM notifications_sent
               WHERE event_type = ? AND event_key = ? AND sent_at >= ?
               LIMIT 1""",
            (event_type, event_key, cutoff.isoformat()),
        ).fetchone()
    finally:
        conn.close()
    return row is None
