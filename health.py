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


# ─────────────────────────────────────────────────────────────────────────────
#  ORCHESTRATION
# ─────────────────────────────────────────────────────────────────────────────

def _get_manual_override(symbol: str) -> bool:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT manual_override FROM symbol_health WHERE symbol=?",
            (symbol,),
        ).fetchone()
    finally:
        conn.close()
    return bool(row[0]) if row else False


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

    current = get_symbol_state(symbol)
    override = _get_manual_override(symbol)
    new_state, reason = evaluate_state(metrics, current, override, ks_cfg)

    if new_state != current:
        apply_transition(symbol, new_state=new_state, reason=reason,
                         metrics=metrics, from_state=current)
        # One-shot notify on transitions into tiered states.
        # PR 2 (#138): ALERT; PR 3 (#138): REDUCED; PR 4 (#138): PAUSED.
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

    If kill_switch.enabled is False, returns {} without touching the DB.

    Fail-fast semantics: if any per-symbol evaluation raises (e.g. a DB lock),
    the exception propagates and later symbols are NOT evaluated. Callers that
    want best-effort behavior (e.g. the daily cron in Task 6) should wrap this
    in try/except and log partial-failure explicitly.
    """
    ks_cfg = (cfg.get("kill_switch") or {})
    if not ks_cfg.get("enabled", True):
        return {}
    from btc_scanner import DEFAULT_SYMBOLS
    return {sym: evaluate_and_record(sym, cfg, now=now) for sym in DEFAULT_SYMBOLS}


def apply_reduce_factor(size: float, symbol: str, cfg: dict[str, Any]) -> float:
    """Return `size` scaled by `reduce_size_factor` if the symbol is in REDUCED state.

    Returns `size` unchanged for NORMAL/ALERT/PAUSED states, or if kill_switch is
    disabled. Callers should use this at position-open time (btc_scanner.scan)
    or at backtest-sim time (backtest.simulate_strategy) to halve risk on
    symbols that have recent losses.

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
    return size


# ─────────────────────────────────────────────────────────────────────────────
#  TRIGGER + DAILY LOOP
# ─────────────────────────────────────────────────────────────────────────────


def trigger_health_evaluation(symbol: str, cfg: dict[str, Any]) -> None:
    """Fire-and-forget health evaluation for a single symbol.
    Swallows exceptions so callers (e.g. db_close_position) never crash."""
    ks_cfg = (cfg.get("kill_switch") or {})
    if not ks_cfg.get("enabled", True):
        return
    try:
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
