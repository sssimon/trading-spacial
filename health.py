"""Per-symbol health monitor (#138) — observer-only in PR 1.

Pure functions for computing rolling metrics + deciding state transitions,
plus thin persistence wrappers. Does NOT change trading behavior here; that
lands in PRs 2-4.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any


log = logging.getLogger("health")

# Lazy re-export so tests can patch health.notify without reaching into notifier.
# Using a try/except because notifier is a sibling package (not a stdlib) and
# we want health.py to remain importable even if notifier fails to import.
try:
    from notifier import notify, HealthEvent  # noqa: F401
except ImportError:
    notify = None  # type: ignore
    HealthEvent = None  # type: ignore


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


def compute_rolling_metrics_from_trades(
    closed_trades: list[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Pure version: compute rolling metrics from a list of closed trades.

    Each trade dict needs keys: `exit_ts` (ISO string), `pnl_usd` (float).
    Extra keys are ignored.

    Returns a dict with:
      - trades_count_total (int)
      - win_rate_20_trades (float | None) — None when no trades with exit_ts exist
      - win_rate_10_trades (float | None) — None when no trades with exit_ts exist
      - pnl_30d (float)
      - pnl_by_month (dict "YYYY-MM" -> float)
      - months_negative_consecutive (int)

    Note: the DB-backed wrapper `compute_rolling_metrics` coerces None → 0.0
    on win_rate_20_trades and win_rate_10_trades to preserve its historical contract.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    trades_count_total = len(closed_trades)

    # Sort by exit_ts ascending for predictable slicing.
    # Trades without exit_ts sort to the front (empty string) and get excluded
    # from the last-20 window by the same filter the DB query applies.
    sorted_trades = sorted(
        closed_trades, key=lambda t: t.get("exit_ts") or ""
    )

    # Last 20 trades win rate — mirrors the DB query which restricts to
    # rows where exit_ts IS NOT NULL.
    trades_with_exit = [t for t in sorted_trades if t.get("exit_ts")]
    last_20 = trades_with_exit[-20:]
    if len(last_20) > 0:
        # Explicit NULL/None check — avoids `(pnl or 0) > 0` silently treating
        # breakeven (pnl=0.0) and NULL as losers via Python truthiness.
        wins = sum(
            1 for t in last_20
            if t.get("pnl_usd") is not None and t["pnl_usd"] > 0
        )
        win_rate_20_trades: float | None = wins / len(last_20)
    else:
        win_rate_20_trades = None

    # Last 10 trades win rate (B5: PROBATION regression check uses this)
    last_10 = trades_with_exit[-10:]
    if len(last_10) > 0:
        wins_10 = sum(
            1 for t in last_10
            if t.get("pnl_usd") is not None and t["pnl_usd"] > 0
        )
        win_rate_10_trades: float | None = wins_10 / len(last_10)
    else:
        win_rate_10_trades = None

    # Last 30 days PnL
    cutoff_30d = now - timedelta(days=30)
    pnl_30d = 0.0
    for t in sorted_trades:
        exit_ts_str = t.get("exit_ts")
        if not exit_ts_str:
            continue
        try:
            ts = datetime.fromisoformat(exit_ts_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if ts >= cutoff_30d:
            pnl_30d += float(t.get("pnl_usd") or 0)

    # Monthly PnL aggregation
    pnl_by_month: dict[str, float] = {}
    for t in sorted_trades:
        exit_ts_str = t.get("exit_ts")
        if not exit_ts_str:
            continue
        try:
            ts = datetime.fromisoformat(exit_ts_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        key = _month_key(ts)
        pnl_by_month[key] = pnl_by_month.get(key, 0.0) + float(t.get("pnl_usd") or 0)

    months_negative_consecutive = _months_negative_consecutive(pnl_by_month, now)

    return {
        "trades_count_total": trades_count_total,
        "win_rate_20_trades": win_rate_20_trades,
        "win_rate_10_trades": win_rate_10_trades,
        "pnl_30d": pnl_30d,
        "pnl_by_month": pnl_by_month,
        "months_negative_consecutive": months_negative_consecutive,
    }


def compute_rolling_metrics(symbol: str, conn, now: datetime | None = None) -> dict[str, Any]:
    """DB-backed wrapper around `compute_rolling_metrics_from_trades`.

    Reads closed trades for `symbol` from the `positions` table and delegates
    to the pure function. Behavior for callers is unchanged from the pre-#186
    implementation:
      - `trades_count_total` counts ALL closed rows (including those with NULL
        exit_ts), matching the original `SELECT COUNT(*)`.
      - `win_rate_20_trades` is coerced to 0.0 when there are no trades
        (the pure function returns None in that case).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Original contract: trades_count_total includes rows with NULL exit_ts.
    total_all_closed = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE symbol=? AND status='closed'",
        (symbol,),
    ).fetchone()[0]

    # The pure function expects trades with exit_ts (it ignores None/empty).
    cursor = conn.execute(
        """SELECT exit_ts, pnl_usd FROM positions
           WHERE symbol = ? AND status = 'closed'
             AND exit_ts IS NOT NULL""",
        (symbol,),
    )
    closed_trades = [
        {"exit_ts": row[0], "pnl_usd": row[1]}
        for row in cursor.fetchall()
    ]
    metrics = compute_rolling_metrics_from_trades(closed_trades, now=now)

    # Overwrite with the all-closed count to preserve the legacy contract.
    metrics["trades_count_total"] = int(total_all_closed)
    # Preserve legacy contract: empty-case win_rate is 0.0, always a float.
    if metrics["win_rate_20_trades"] is None:
        metrics["win_rate_20_trades"] = 0.0
    else:
        metrics["win_rate_20_trades"] = float(metrics["win_rate_20_trades"])
    if metrics["win_rate_10_trades"] is None:
        metrics["win_rate_10_trades"] = 0.0
    else:
        metrics["win_rate_10_trades"] = float(metrics["win_rate_10_trades"])
    return metrics


# ─────────────────────────────────────────────────────────────────────────────
#  B5 PROBATION TIER (pure)
# ─────────────────────────────────────────────────────────────────────────────


def compute_probation_trades_remaining(
    days_paused: int,
    trades_base: int = 10,
    per_pause_day: float = 0.2,
) -> int:
    """Initial probation_trades_remaining when reactivating from PAUSED.

    Formula: round(trades_base + per_pause_day * days_paused).
    Example: 15 days paused → 10 + 0.2*15 = 13.

    days_paused <= 0 returns `trades_base` unchanged (clock skew defensive).
    """
    if days_paused <= 0:
        return int(trades_base)
    return int(round(trades_base + per_pause_day * days_paused))


def compute_next_conditions(
    state: str,
    metrics: dict[str, Any],
    manual_override: bool,
    cfg: dict[str, Any],
    days_in_paused: int = 0,
) -> str:
    """B6: Spanish text describing the gap to next tier change.

    Pure function. Uses cfg thresholds + current metrics to phrase what
    the operator should expect ("para salir de ALERT: WR>0.20 sobre 8 trades").

    Args:
        state: current per-symbol tier ("NORMAL"|"ALERT"|"REDUCED"|"PAUSED"|"PROBATION").
        metrics: rolling metrics dict (output of compute_rolling_metrics).
        manual_override: whether the symbol has manual_override=1.
        cfg: kill_switch sub-config (already unwrapped).
        days_in_paused: how many days the symbol has been in PAUSED (used for
            auto-recovery countdown).

    Returns: Spanish text, never None.
    """
    if state == "NORMAL":
        return "Saludable — sin alertas activas."

    if state == "ALERT":
        threshold = float(cfg.get("alert_win_rate_threshold", 0.15))
        wr20 = float(metrics.get("win_rate_20_trades", 0.0) or 0.0)
        # wins needed = ceil(threshold * 20 - current_wins). current_wins = wr20*20.
        import math
        current_wins = int(round(wr20 * 20))
        wins_needed = max(0, int(math.ceil(threshold * 20 - current_wins)))
        return (
            f"Para salir: WR>{threshold:.2f} sobre próximos 20 trades. "
            f"Actual: WR={wr20:.2f} ({current_wins}/20 wins), faltan {wins_needed} wins."
        )

    if state == "REDUCED":
        pnl_30d = float(metrics.get("pnl_30d", 0.0) or 0.0)
        gap = max(0.0, -pnl_30d)
        return (
            f"Para salir: pnl_30d ≥ 0. Actual: ${pnl_30d:.2f}, "
            f"faltan ${gap:.2f}."
        )

    if state == "PAUSED":
        if manual_override:
            return "Reactivación manual disponible vía POST /health/reactivate/{symbol}."
        v2_cfg = (cfg.get("v2") or {})
        prob_cfg = (v2_cfg.get("probation") or {})
        threshold_days = int(prob_cfg.get("paused_to_probation_days", 14))
        days_remaining = max(0, threshold_days - int(days_in_paused))
        return (
            f"Auto-recovery: en {days_remaining} días + portfolio NORMAL → PROBATION. "
            f"Días en PAUSED: {days_in_paused}/{threshold_days}."
        )

    if state == "PROBATION":
        trades_remaining = metrics.get("probation_trades_remaining")
        wr10 = float(metrics.get("win_rate_10_trades", 0.0) or 0.0)
        v2_cfg = (cfg.get("v2") or {})
        prob_cfg = (v2_cfg.get("probation") or {})
        regression_wr = float(prob_cfg.get("regression_wr_threshold", 0.10))
        if trades_remaining is None:
            return f"En PROBATION (sin contador). WR_10={wr10:.2f}."
        return (
            f"En PROBATION: {int(trades_remaining)} trades restantes (al llegar a 0 → NORMAL). "
            f"Riesgo regresión: WR_10={wr10:.2f}, threshold={regression_wr:.2f}."
        )

    return f"Estado desconocido: {state}"


# ─────────────────────────────────────────────────────────────────────────────
#  STATE MACHINE (pure)
# ─────────────────────────────────────────────────────────────────────────────

VALID_STATES = ("NORMAL", "ALERT", "REDUCED", "PAUSED", "PROBATION")


def evaluate_state(
    metrics: dict[str, Any],
    current_state: str,
    manual_override: bool,
    config: dict[str, Any],
) -> tuple[str, str]:
    """Return (new_state, reason) given metrics + current state + manual override.

    Rule precedence (most severe wins):
      1. insufficient_data → hold current state
      1b. PROBATION branch (only when current_state == PROBATION):
            - WR_10 < regression_wr_threshold AND trades >= regression_window → PAUSED
            - probation_trades_remaining (NULL/0) → NORMAL (probation_complete)
            - else → hold PROBATION (probation_in_progress)
          Severe regression is the ONLY downward path from PROBATION; the months-
          negative cascade below is intentionally bypassed (a freshly reactivated
          symbol still carries its past PAUSED months).
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

    # B5 PROBATION branch — only fires when current_state is already PROBATION.
    # Read v2.probation sub-config (defaults match spec).
    if current_state == "PROBATION":
        v2_cfg = (config.get("v2") or {})
        prob_cfg = (v2_cfg.get("probation") or {})
        regression_wr = float(prob_cfg.get("regression_wr_threshold", 0.10))
        regression_window = int(prob_cfg.get("regression_window_trades", 10))

        wr_10 = metrics.get("win_rate_10_trades", 0.0) or 0.0
        if (
            metrics.get("trades_count_total", 0) >= regression_window
            and wr_10 < regression_wr
        ):
            return "PAUSED", "regression_severe"

        trades_remaining = metrics.get("probation_trades_remaining")
        if trades_remaining is None or int(trades_remaining) <= 0:
            return "NORMAL", "probation_complete"

        return "PROBATION", "probation_in_progress"

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

def _decrement_probation_counter(symbol: str) -> None:
    """Atomically decrement probation_trades_remaining if the symbol is in PROBATION.

    Floored at 0. No-op if the symbol is not in PROBATION (uses a state-guarded
    UPDATE so we don't accidentally write to non-PROBATION rows).
    """
    conn = _conn()
    try:
        conn.execute(
            """UPDATE symbol_health
               SET probation_trades_remaining = MAX(
                     COALESCE(probation_trades_remaining, 0) - 1, 0
               )
               WHERE symbol = ? AND state = 'PROBATION'""",
            (symbol,),
        )
        conn.commit()
    finally:
        conn.close()


def _is_portfolio_normal(cfg: dict[str, Any]) -> bool:
    """Return True if portfolio aggregate tier is NORMAL.

    Reuses kill_switch_v2 helpers. Defensive: any failure → False (block
    auto-recovery in unclear state).
    """
    try:
        from strategy.kill_switch_v2 import evaluate_portfolio_tier
        from strategy.kill_switch_v2_calibrator import _compute_current_portfolio_dd
        portfolio_dd = _compute_current_portfolio_dd(cfg)
        # Concurrent failures count: use existing health rows.
        conn = _conn()
        try:
            n_failures = conn.execute(
                """SELECT COUNT(*) FROM symbol_health
                   WHERE state IN ('ALERT', 'REDUCED', 'PAUSED', 'PROBATION')"""
            ).fetchone()[0]
        finally:
            conn.close()
        portfolio = evaluate_portfolio_tier(portfolio_dd, int(n_failures), cfg)
        return portfolio.get("tier") == "NORMAL"
    except Exception as e:  # noqa: BLE001
        log.warning("_is_portfolio_normal failed: %s — treating as not-normal", e)
        return False


def _maybe_auto_reactivate(
    symbol: str,
    threshold_days: int,
    cfg: dict[str, Any],
) -> None:
    """B5 daily-cron hook: PAUSED ≥ threshold days + portfolio NORMAL → PROBATION.

    No-op when:
      - symbol is not currently PAUSED
      - paused-duration < threshold_days
      - portfolio aggregate tier ≠ NORMAL
    """
    row = _get_symbol_health_row(symbol)
    if row is None or row["state"] != "PAUSED":
        return

    state_since_iso = row["state_since"]
    if not state_since_iso:
        return
    try:
        state_since_dt = datetime.fromisoformat(state_since_iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return
    days_paused = (datetime.now(timezone.utc) - state_since_dt).days
    if days_paused < int(threshold_days):
        return

    if not _is_portfolio_normal(cfg):
        log.info(
            "_maybe_auto_reactivate(%s): portfolio gate blocks (not NORMAL); skipping",
            symbol,
        )
        return

    log.info(
        "_maybe_auto_reactivate(%s): %d days in PAUSED, portfolio NORMAL → PROBATION",
        symbol, days_paused,
    )
    reactivate_symbol(symbol, reason="auto_recovery", cfg=cfg)


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


def sparkline_for_symbol(symbol: str, conn, n: int = 20) -> list[str | None]:
    """B6: Last n trade outcomes for `symbol`, oldest→newest, padded with None.

    'W' if pnl_usd > 0, 'L' otherwise. Breakeven (pnl=0) counts as L.
    Returns a list of length exactly n.
    """
    cursor = conn.execute(
        """SELECT pnl_usd FROM positions
           WHERE symbol = ? AND status = 'closed' AND exit_ts IS NOT NULL
           ORDER BY exit_ts DESC LIMIT ?""",
        (symbol, n),
    )
    raw = [row[0] for row in cursor.fetchall()]
    raw.reverse()  # newest-first → oldest-first

    outcomes: list[str | None] = []
    for pnl in raw:
        if pnl is not None and pnl > 0:
            outcomes.append('W')
        else:
            outcomes.append('L')

    # Pad with leading None until length == n
    while len(outcomes) < n:
        outcomes.insert(0, None)
    return outcomes


def summarize_recent_alerts(
    conn=None,
    window_hours: int = 24,
) -> dict[str, Any]:
    """B6: Aggregated 24h alert summary for the dashboard alerts strip.

    Reads `symbol_health_events` for `symbol_failures` and `auto_reactivation`,
    `kill_switch_decisions` for `velocity_burst`. Returns {"items": [...]}
    where each item has kind/text/severity/ts.
    """
    if conn is None:
        conn = _conn()
        owns_conn = True
    else:
        owns_conn = False

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    items: list[dict[str, Any]] = []

    try:
        # symbol_failures: distinct symbols entering ALERT/REDUCED/PAUSED
        rows = conn.execute(
            """SELECT DISTINCT symbol, MAX(ts) AS latest
               FROM symbol_health_events
               WHERE ts >= ? AND to_state IN ('ALERT', 'REDUCED', 'PAUSED')
               GROUP BY symbol""",
            (cutoff,),
        ).fetchall()
        if rows:
            n = len(rows)
            latest_ts = max(r[1] for r in rows)
            severity = "warning" if n >= 3 else "info"
            items.append({
                "kind": "symbol_failures",
                "text": f"{n} símbolo(s) entraron en ALERT/REDUCED/PAUSED en últimas {window_hours}h",
                "severity": severity,
                "ts": latest_ts,
            })

        # auto_reactivation: PROBATION transitions with reason starting "reactivated_auto"
        rows = conn.execute(
            """SELECT COUNT(*) AS n, MAX(ts) AS latest
               FROM symbol_health_events
               WHERE ts >= ? AND to_state = 'PROBATION'
                 AND trigger_reason LIKE 'reactivated_auto%'""",
            (cutoff,),
        ).fetchone()
        if rows and rows[0] > 0:
            items.append({
                "kind": "auto_reactivation",
                "text": f"{rows[0]} símbolo(s) auto-reactivados a PROBATION en últimas {window_hours}h",
                "severity": "info",
                "ts": rows[1],
            })

        # velocity_burst: count of decisions with velocity_active=1
        rows = conn.execute(
            """SELECT COUNT(*) AS n, MAX(ts) AS latest
               FROM kill_switch_decisions
               WHERE ts >= ? AND velocity_active = 1""",
            (cutoff,),
        ).fetchone()
        if rows and rows[0] > 0:
            items.append({
                "kind": "velocity_burst",
                "text": f"Velocity trigger fired {rows[0]} veces en últimas {window_hours}h",
                "severity": "info",
                "ts": rows[1],
            })

        # Sort newest-first
        items.sort(key=lambda x: x["ts"], reverse=True)
    finally:
        if owns_conn:
            conn.close()

    return {"items": items}


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

        # B5: clear probation columns when transitioning OUT of PROBATION.
        # When new_state == PROBATION, reactivate_symbol writes the columns
        # explicitly via a separate path (see Task 5).
        probation_reset = ""
        if new_state != "PROBATION":
            probation_reset = (
                ", probation_trades_remaining = NULL"
                ", probation_started_at = NULL"
                ", paused_days_at_entry = NULL"
            )

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
                  {extra_sets}
                  {probation_reset}""",
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


def _get_symbol_health_row(symbol: str) -> dict[str, Any] | None:
    """Return the symbol_health row for `symbol`, or None if absent.

    Selected columns: state, state_since, manual_override,
    probation_trades_remaining, probation_started_at, paused_days_at_entry.
    """
    conn = _conn()
    try:
        row = conn.execute(
            """SELECT state, state_since, manual_override,
                      probation_trades_remaining, probation_started_at,
                      paused_days_at_entry
               FROM symbol_health WHERE symbol=?""",
            (symbol,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "state": row[0],
        "state_since": row[1],
        "manual_override": int(row[2] or 0),
        "probation_trades_remaining": row[3],
        "probation_started_at": row[4],
        "paused_days_at_entry": row[5],
    }


def reactivate_symbol(
    symbol: str,
    reason: str = "manual",
    cfg: dict[str, Any] | None = None,
) -> None:
    """Transition a PAUSED symbol → PROBATION (B5 #199).

    `reason='manual'` sets manual_override=1; any other reason (e.g.
    'auto_recovery') sets it to 0. Reads probation params from
    cfg['kill_switch']['v2']['probation'] when provided; otherwise uses
    spec defaults (trades_base=10, per_pause_day=0.2).

    No-ops with a warning when the symbol is not currently PAUSED.
    """
    row = _get_symbol_health_row(symbol)
    current = row["state"] if row else "NORMAL"

    if current != "PAUSED":
        log.warning(
            "reactivate_symbol(%s): symbol is not in PAUSED (current=%s); skipping",
            symbol, current,
        )
        return

    # Compute days_paused from the PAUSED row's state_since.
    state_since_iso = row["state_since"] if row else None
    days_paused = 0
    if state_since_iso:
        try:
            state_since_dt = datetime.fromisoformat(state_since_iso.replace("Z", "+00:00"))
            days_paused = max(0, int((datetime.now(timezone.utc) - state_since_dt).days))
        except (ValueError, AttributeError):
            log.warning(
                "reactivate_symbol(%s): invalid state_since=%r; treating days_paused=0",
                symbol, state_since_iso,
            )

    # Pull probation params (with defaults).
    prob_cfg = (((cfg or {}).get("kill_switch") or {}).get("v2") or {}).get("probation") or {}
    trades_base = int(prob_cfg.get("trades_base", 10))
    per_pause_day = float(prob_cfg.get("trades_per_pause_day", 0.2))
    trades_remaining = compute_probation_trades_remaining(
        days_paused, trades_base=trades_base, per_pause_day=per_pause_day,
    )

    metrics = {
        "reactivation_reason": reason,
        "days_paused": days_paused,
        "probation_trades_remaining": trades_remaining,
    }
    manual_override = 1 if reason == "manual" else 0
    metrics_json = json.dumps(metrics, default=str)
    now_iso = _now_iso()

    # B5 C2 fix: single atomic transaction (state + probation columns + event row)
    # to close the race window where a concurrent trigger_health_evaluation could
    # read NULL counter and silently revert state to NORMAL between two writes.
    conn = _conn()
    try:
        conn.execute(
            """INSERT INTO symbol_health
               (symbol, state, state_since, last_evaluated_at, last_metrics_json,
                manual_override, probation_trades_remaining,
                probation_started_at, paused_days_at_entry)
               VALUES (?, 'PROBATION', ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol) DO UPDATE SET
                 state = 'PROBATION',
                 state_since = CASE
                   WHEN symbol_health.state != 'PROBATION' THEN excluded.state_since
                   ELSE symbol_health.state_since
                 END,
                 last_evaluated_at = excluded.last_evaluated_at,
                 last_metrics_json = excluded.last_metrics_json,
                 manual_override = excluded.manual_override,
                 probation_trades_remaining = excluded.probation_trades_remaining,
                 probation_started_at = excluded.probation_started_at,
                 paused_days_at_entry = excluded.paused_days_at_entry""",
            (symbol, now_iso, now_iso, metrics_json, manual_override,
             trades_remaining, now_iso, days_paused),
        )
        conn.execute(
            """INSERT INTO symbol_health_events
               (symbol, from_state, to_state, trigger_reason, metrics_json, ts)
               VALUES (?, ?, 'PROBATION', ?, ?, ?)""",
            (symbol, current, f"reactivated_{reason}", metrics_json, now_iso),
        )
        conn.commit()
    finally:
        conn.close()

    # B5 C1 fix: notify on PROBATION entry (auto + manual reactivations).
    # Previously dead code in evaluate_and_record (which never returns
    # PROBATION from a non-PROBATION current state).
    if notify is not None and HealthEvent is not None:
        try:
            notify(
                HealthEvent(symbol=symbol, from_state=current,
                            to_state="PROBATION", reason=f"reactivated_{reason}",
                            metrics=metrics),
                cfg=cfg or {},
            )
        except Exception as e:  # noqa: BLE001
            log.warning("health: PROBATION notify failed for %s: %s", symbol, e)


# ─────────────────────────────────────────────────────────────────────────────
#  ORCHESTRATION
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_and_record(symbol: str, cfg: dict[str, Any], now: datetime | None = None) -> str:
    """Compute metrics + evaluate state + persist. Returns the resulting state."""
    ks_cfg = (cfg.get("kill_switch") or {})
    if not ks_cfg.get("enabled", True):
        return "NORMAL"

    if now is None:
        now = datetime.now(timezone.utc)

    conn = _conn()
    try:
        metrics = compute_rolling_metrics(symbol, conn, now=now)
    finally:
        conn.close()

    # B5: inject current probation_trades_remaining into metrics so evaluate_state
    # can apply the PROBATION branch. Read from the symbol_health row.
    row = _get_symbol_health_row(symbol)
    current = row["state"] if row else "NORMAL"
    override = bool(row["manual_override"]) if row else False
    if row is not None:
        metrics["probation_trades_remaining"] = row["probation_trades_remaining"]

    new_state, reason = evaluate_state(metrics, current, override, ks_cfg)

    if new_state != current:
        apply_transition(symbol, new_state=new_state, reason=reason,
                         metrics=metrics, from_state=current)
        # One-shot notify on transitions into tiered states.
        # PROBATION is intentionally NOT in this set: evaluate_state never
        # returns PROBATION from a non-PROBATION current state, so the only
        # entry path is reactivate_symbol (which fires its own notify).
        notify_on_states = {"ALERT", "REDUCED", "PAUSED"}
        if new_state in notify_on_states and notify is not None and HealthEvent is not None:
            try:
                notify(
                    HealthEvent(symbol=symbol, from_state=current,
                                to_state=new_state, reason=reason, metrics=metrics),
                    cfg=cfg,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("health: %s notify failed for %s: %s", new_state, symbol, e)
    else:
        _record_evaluation(symbol, metrics, new_state)
    return new_state


def evaluate_all_symbols(cfg: dict[str, Any], now: datetime | None = None) -> dict[str, str]:
    """Evaluate every symbol in btc_scanner.DEFAULT_SYMBOLS. Returns {symbol: state}.

    B5: before evaluating each symbol's metrics, runs auto-reactivation check
    so a PAUSED symbol that crossed the cooldown threshold transitions to
    PROBATION first, then gets evaluated as PROBATION on the same sweep.
    """
    ks_cfg = (cfg.get("kill_switch") or {})
    if not ks_cfg.get("enabled", True):
        return {}
    from btc_scanner import DEFAULT_SYMBOLS

    v2_cfg = (ks_cfg.get("v2") or {})
    prob_cfg = (v2_cfg.get("probation") or {})
    threshold_days = int(prob_cfg.get("paused_to_probation_days", 14))

    out: dict[str, str] = {}
    for sym in DEFAULT_SYMBOLS:
        try:
            _maybe_auto_reactivate(sym, threshold_days, cfg)
        except Exception as e:  # noqa: BLE001
            log.warning("auto_reactivate(%s) failed: %s", sym, e, exc_info=True)
        out[sym] = evaluate_and_record(sym, cfg, now=now)
    return out


def apply_reduce_factor(size: float, symbol: str, cfg: dict[str, Any]) -> float:
    """Return `size` scaled by the kill-switch tier factor.

    Scaling rules:
      - REDUCED → size * `kill_switch.reduce_size_factor` (default 0.5)
      - PROBATION → size * `kill_switch.v2.probation.size_factor`
        (default 0.5; falls back to `reduce_size_factor` if absent)
      - NORMAL/ALERT/PAUSED → size unchanged
      - kill_switch.enabled=False → size unchanged

    Callers should use this at position-open time (btc_scanner.scan) or at
    backtest-sim time (backtest.simulate_strategy) to halve risk on symbols
    that are in a degraded tier.

    Safe on any failure: swallows exceptions (returns original size). The
    kill-switch must never block a trade by raising in this hot path.
    """
    ks_cfg = (cfg.get("kill_switch") or {})
    if not ks_cfg.get("enabled", True):
        return size
    try:
        state = get_symbol_state(symbol)
    except Exception as e:  # noqa: BLE001
        log.warning("apply_reduce_factor: state lookup failed for %s: %s", symbol, e)
        return size
    if state == "REDUCED":
        factor = float(ks_cfg.get("reduce_size_factor", 0.5))
        return size * factor
    if state == "PROBATION":
        # Prefer v2.probation.size_factor; fall back to v1's reduce_size_factor.
        v2_cfg = (ks_cfg.get("v2") or {})
        prob_cfg = (v2_cfg.get("probation") or {})
        factor = float(prob_cfg.get(
            "size_factor",
            ks_cfg.get("reduce_size_factor", 0.5),
        ))
        return size * factor
    return size


# ─────────────────────────────────────────────────────────────────────────────
#  TRIGGER + DAILY LOOP
# ─────────────────────────────────────────────────────────────────────────────


def trigger_health_evaluation(symbol: str, cfg: dict[str, Any]) -> None:
    """Fire-and-forget health evaluation for a single symbol.
    Swallows exceptions so callers (e.g. db_close_position) never crash.

    B5: decrements probation_trades_remaining before evaluation when symbol
    is in PROBATION (so the regression/completion check sees the post-trade
    counter value).
    """
    ks_cfg = (cfg.get("kill_switch") or {})
    if not ks_cfg.get("enabled", True):
        return
    try:
        _decrement_probation_counter(symbol)
        evaluate_and_record(symbol, cfg)
    except Exception as e:  # noqa: BLE001
        log.error("health trigger failed for %s: %s", symbol, e, exc_info=True)


def _seconds_until_next_midnight_utc(now: datetime) -> float:
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    return (tomorrow - now).total_seconds()


def health_monitor_loop(cfg_fn, stop_event=None) -> None:
    """Daily cron @ 00:00 UTC: run evaluate_all_symbols with fresh cfg.

    `cfg_fn` is a callable returning the current config dict (re-read each
    day in case user edits config.json). `stop_event` is an optional
    threading.Event for graceful shutdown; if None, loops until killed.
    """
    if stop_event is None:
        stop_event = threading.Event()
    while not stop_event.is_set():
        sleep_s = _seconds_until_next_midnight_utc(datetime.now(timezone.utc))
        if stop_event.wait(timeout=sleep_s):
            return
        try:
            cfg = cfg_fn()
            evaluate_all_symbols(cfg)
            log.info("health_monitor_loop: daily sweep complete")
        except Exception as e:  # noqa: BLE001
            log.error("health_monitor_loop sweep failed: %s", e, exc_info=True)
