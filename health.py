"""Per-symbol health monitor (#138) — observer-only in PR 1.

Pure functions for computing rolling metrics + deciding state transitions,
plus thin persistence wrappers. Does NOT change trading behavior here; that
lands in PRs 2-4.
"""
from __future__ import annotations

import json
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


# ─────────────────────────────────────────────────────────────────────────────
#  STATE MACHINE (pure)
# ─────────────────────────────────────────────────────────────────────────────

VALID_STATES = ("NORMAL", "ALERT", "REDUCED", "PAUSED")


def evaluate_state(
    metrics: dict[str, Any],
    current_state: str,
    manual_override: bool,
    config: dict[str, Any],
) -> tuple[str, str]:
    """Return (new_state, reason) given metrics + current state + manual override.

    Rule precedence (most severe wins):
      1. insufficient_data → hold current state
      2. months_negative_consecutive >= pause_months_consecutive → PAUSED
      3. pnl_30d < 0 → REDUCED
      4. win_rate_20_trades < alert_win_rate_threshold → ALERT
      5. else → NORMAL (auto-recovery; if auto_recovery_enabled=False and
         current != NORMAL, hold current state with reason='auto_recovery_disabled')

    manual_override is informational: a PAUSED→NORMAL reactivation sets override=True,
    but a SUBSEQUENT severe rule (rule 2) still transitions to PAUSED.
    """
    if current_state not in VALID_STATES:
        raise ValueError(f"evaluate_state: unknown current_state={current_state!r}")

    min_trades = int(config.get("min_trades_for_eval", 20))
    if metrics.get("trades_count_total", 0) < min_trades:
        return current_state, "insufficient_data"

    pause_threshold = int(config.get("pause_months_consecutive", 3))
    if metrics.get("months_negative_consecutive", 0) >= pause_threshold:
        return "PAUSED", "3mo_consec_neg"

    if metrics.get("pnl_30d", 0.0) < 0:
        return "REDUCED", "pnl_neg_30d"

    wr_threshold = float(config.get("alert_win_rate_threshold", 0.15))
    if metrics.get("win_rate_20_trades", 0.0) < wr_threshold:
        return "ALERT", "wr_below_threshold"

    # Healthy path
    if current_state == "NORMAL":
        return "NORMAL", "healthy"

    if config.get("auto_recovery_enabled", True):
        return "NORMAL", "auto_recovery"
    return current_state, "auto_recovery_disabled"


# ─────────────────────────────────────────────────────────────────────────────
#  PERSISTENCE
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn():
    import btc_api
    return btc_api.get_db()


def get_symbol_state(symbol: str) -> str:
    """Return the current state of a symbol, or 'NORMAL' if it has no row."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT state FROM symbol_health WHERE symbol=?",
            (symbol,),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else "NORMAL"


def _record_evaluation(symbol: str, metrics: dict[str, Any], new_state: str) -> None:
    """Update last_evaluated_at + last_metrics_json without changing state.
    Creates the row if it doesn't exist. No event is emitted."""
    conn = _conn()
    now = _now_iso()
    try:
        conn.execute(
            """INSERT INTO symbol_health (symbol, state, state_since, last_evaluated_at, last_metrics_json)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(symbol) DO UPDATE SET
                 last_evaluated_at = excluded.last_evaluated_at,
                 last_metrics_json = excluded.last_metrics_json""",
            (symbol, new_state, now, now, json.dumps(metrics, default=str)),
        )
        conn.commit()
    finally:
        conn.close()


def apply_transition(
    symbol: str,
    new_state: str,
    reason: str,
    metrics: dict[str, Any],
    from_state: str,
    manual_override: int | None = None,
) -> None:
    """Write the new state to symbol_health AND append a row to symbol_health_events.

    If new_state == from_state this is a bug — callers should prefer `_record_evaluation`
    for same-state updates. We still handle it gracefully by skipping the event insert.
    """
    if new_state not in VALID_STATES:
        raise ValueError(f"invalid state: {new_state!r}")
    now = _now_iso()
    metrics_json = json.dumps(metrics, default=str)

    conn = _conn()
    try:
        extra_sets = ""
        if manual_override is not None:
            extra_sets = ", manual_override = excluded.manual_override"
        # state_since must only advance when the state actually changes. A stale
        # from_state passed by a caller (or a concurrent write) that happens to match
        # the stored state would otherwise silently reset "time in state".
        conn.execute(
            f"""INSERT INTO symbol_health
                (symbol, state, state_since, last_evaluated_at, last_metrics_json, manual_override)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                  state = excluded.state,
                  state_since = CASE
                    WHEN symbol_health.state != excluded.state THEN excluded.state_since
                    ELSE symbol_health.state_since
                  END,
                  last_evaluated_at = excluded.last_evaluated_at,
                  last_metrics_json = excluded.last_metrics_json
                  {extra_sets}""",
            (symbol, new_state, now, now, metrics_json,
             int(manual_override) if manual_override is not None else 0),
        )

        if from_state != new_state:
            conn.execute(
                """INSERT INTO symbol_health_events
                   (symbol, from_state, to_state, trigger_reason, metrics_json, ts)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (symbol, from_state, new_state, reason, metrics_json, now),
            )
        conn.commit()
    finally:
        conn.close()


def reactivate_symbol(symbol: str, reason: str = "manual") -> None:
    """Manually reset a symbol to NORMAL with manual_override=1."""
    current = get_symbol_state(symbol)
    metrics = {"reactivation_reason": reason}
    apply_transition(
        symbol, new_state="NORMAL", reason="manual_override",
        metrics=metrics, from_state=current, manual_override=1,
    )
