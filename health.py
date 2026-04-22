"""Per-symbol health monitor (#138) — observer-only in PR 1.

Pure functions for computing rolling metrics + deciding state transitions,
plus thin persistence wrappers. Does NOT change trading behavior here; that
lands in PRs 2-4.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def _month_key(dt: datetime) -> str:
    """YYYY-MM string from a datetime (used as pnl_by_month key)."""
    return dt.strftime("%Y-%m")


def _previous_full_month_keys(now: datetime, n: int) -> list[str]:
    """Return the last n full calendar months BEFORE the month containing `now`,
    ordered from most recent to oldest. Example: now=2026-06-15, n=3 → ['2026-05', '2026-04', '2026-03']."""
    keys: list[str] = []
    first_of_now = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    current = first_of_now
    for _ in range(n):
        if current.month == 1:
            current = current.replace(year=current.year - 1, month=12)
        else:
            current = current.replace(month=current.month - 1)
        keys.append(_month_key(current))
    return keys


def _months_negative_consecutive(pnl_by_month: dict[str, float], now: datetime) -> int:
    """Count trailing consecutive FULL calendar months (starting from the month
    before `now`'s month) with sum(pnl) < 0. Stops at the first non-negative month."""
    streak = 0
    for key in _previous_full_month_keys(now, 12):
        pnl = pnl_by_month.get(key, 0.0)
        if pnl < 0:
            streak += 1
        else:
            break
    return streak


def compute_rolling_metrics(symbol: str, conn, now: datetime | None = None) -> dict[str, Any]:
    """Compute health metrics for `symbol` from the positions table.

    Only closed positions (`status='closed'`) are counted. `now` defaults to
    `datetime.now(timezone.utc)` but is injectable for tests.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    cutoff_30d = (now - timedelta(days=30)).isoformat()

    total = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE symbol=? AND status='closed'",
        (symbol,),
    ).fetchone()[0]

    last20 = conn.execute(
        """SELECT pnl_usd FROM positions
           WHERE symbol=? AND status='closed' AND exit_ts IS NOT NULL
           ORDER BY exit_ts DESC
           LIMIT 20""",
        (symbol,),
    ).fetchall()
    if last20:
        # Explicit NULL check — avoids `(pnl or 0) > 0` silently treating
        # breakeven (pnl=0.0) and NULL as losers via Python truthiness.
        winners = sum(1 for (pnl,) in last20 if pnl is not None and pnl > 0)
        win_rate_20_trades = winners / len(last20)
    else:
        win_rate_20_trades = 0.0

    pnl_30d_row = conn.execute(
        """SELECT COALESCE(SUM(pnl_usd), 0) FROM positions
           WHERE symbol=? AND status='closed' AND exit_ts >= ?""",
        (symbol, cutoff_30d),
    ).fetchone()
    pnl_30d = float(pnl_30d_row[0]) if pnl_30d_row else 0.0

    by_month_rows = conn.execute(
        """SELECT substr(exit_ts, 1, 7) AS ym, SUM(pnl_usd) AS pnl
           FROM positions
           WHERE symbol=? AND status='closed' AND exit_ts IS NOT NULL
           GROUP BY ym""",
        (symbol,),
    ).fetchall()
    pnl_by_month = {row[0]: float(row[1] or 0.0) for row in by_month_rows}

    return {
        "trades_count_total": int(total),
        "win_rate_20_trades": float(win_rate_20_trades),
        "pnl_30d": pnl_30d,
        "pnl_by_month": pnl_by_month,
        "months_negative_consecutive": _months_negative_consecutive(pnl_by_month, now),
    }
