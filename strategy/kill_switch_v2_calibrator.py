"""Kill switch v2 auto-calibrator daemon (#187 #214 B4b.1).

Daily-tick thread that evaluates triggers and persists stub recommendations
to kill_switch_recommendations. The "intelligence" (v2-aware backtest + grid
optimization) lands in B4b.2 (#216) — for now the fitness fn is a stub that
always returns no_feasible.

Only two triggers are wired: manual (via POST /kill_switch/recalibrate) and
safety_net (30 days since last recalibration). The other three triggers
(regime_change, portfolio_dd_degradation, event_cascade) come with B4b.3
(#217) along with rate limiting and Telegram notifications.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("kill_switch_v2_calibrator")

_DEFAULT_SAFETY_NET_DAYS = 30


def should_run_safety_net(
    last_recalibration_ts: str | None,
    now,
    safety_net_days: int,
) -> bool:
    """Return True if safety_net should fire (>= safety_net_days since last).

    last_recalibration_ts None → True (never recalibrated).
    Malformed string → True (treat as never).
    Future timestamp → True (clock skew guard).
    Exactly safety_net_days ago → False (strict `>`).
    """
    from datetime import datetime, timedelta, timezone

    if not last_recalibration_ts:
        return True
    try:
        parsed = datetime.fromisoformat(last_recalibration_ts)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    if parsed > now:
        return True
    return (now - parsed) > timedelta(days=float(safety_net_days))


def _classify_regime_band(score: float) -> str:
    """Helper: which band does this score fall into?

    >= 60 → BULL, < 40 → BEAR, else NEUTRAL.
    """
    if score >= 60:
        return "BULL"
    if score < 40:
        return "BEAR"
    return "NEUTRAL"


def should_run_regime_change(
    last_calibration_regime_score: float | None,
    current_regime_score: float | None,
) -> bool:
    """Fire if regime band changed since last calibration.

    Returns False if either score is None (no baseline OR no current data).
    Bands: <40 BEAR, 40-60 NEUTRAL, >=60 BULL.
    """
    if last_calibration_regime_score is None or current_regime_score is None:
        return False
    return (
        _classify_regime_band(last_calibration_regime_score)
        != _classify_regime_band(current_regime_score)
    )


def should_run_portfolio_dd_degradation(
    current_dd: float,
    last_applied_projected_dd: float | None,
    multiplier: float = 1.5,
) -> bool:
    """Fire if current DD is degrading vs the projected baseline.

    Both args are negative (drawdowns). "Degradation" = more negative.
    Threshold = multiplier * last_applied_projected_dd. Strict `<`.

    Returns False if no applied recommendation exists yet, OR if the baseline
    DD is 0/positive (no meaningful historical drawdown to amplify).
    """
    if last_applied_projected_dd is None or last_applied_projected_dd >= 0:
        return False
    threshold = multiplier * last_applied_projected_dd
    return current_dd < threshold


def should_run_event_cascade(
    symbols_in_alert_count: int,
    threshold: int = 3,
) -> bool:
    """Fire if number of distinct symbols in ALERT-or-worse >= threshold.

    Boundary: count == threshold returns True (>= semantics).
    """
    return symbols_in_alert_count >= threshold


def is_rate_limit_ok(
    last_run_ts: str | None,
    now,
    max_per_day_count: int,
    today_count: int,
    min_cooldown_hours: float,
    trigger_kind: str,
) -> bool:
    """Whether a recalibration may run.

    Bypass for trigger_kind in {"manual", "safety_net"}: always True.
    Otherwise:
      - last_run_ts None → True (no prior run)
      - elapsed < cooldown_hours → False
      - today_count >= max_per_day_count → False
      - else → True
    """
    from datetime import datetime, timedelta, timezone

    if trigger_kind in ("manual", "safety_net"):
        return True

    if not last_run_ts:
        return True

    try:
        parsed = datetime.fromisoformat(last_run_ts)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        # Malformed ts → treat as no prior run (conservative for rate limit)
        return True

    elapsed = now - parsed
    if elapsed < timedelta(hours=float(min_cooldown_hours)):
        return False

    if today_count >= int(max_per_day_count):
        return False

    return True


def build_no_feasible_report(reason: str, now) -> dict[str, Any]:
    """Construct the report payload for stub no_feasible runs.

    The `stub: True` flag is a sentinel for the dashboard / future B4b.2
    code: stubs can be filtered out of the "real" recommendation list.
    """
    return {
        "status": "no_feasible",
        "reason": reason,
        "ts": now.isoformat(),
        "stub": True,
    }


def run_optimization_stub(cfg: dict[str, Any]) -> dict[str, Any]:
    """Stub fitness for B4b.1. Always returns no_feasible.

    Will be replaced by run_optimization_v2 in B4b.2 (#216) which performs
    grid optimization over slider 0..100 with a v2-aware backtest.

    Args:
        cfg: ignored (signature preserved for forward compatibility).

    Returns:
        {
            "status": "no_feasible",
            "slider_value": None,
            "projected_pnl": None,
            "projected_dd": None,
            "report": {<no_feasible_report>},
        }
    """
    from datetime import datetime, timezone
    now = datetime.now(tz=timezone.utc)
    return {
        "status": "no_feasible",
        "slider_value": None,
        "projected_pnl": None,
        "projected_dd": None,
        "report": build_no_feasible_report(
            reason="v2 backtest pending B4b.2", now=now,
        ),
    }


def _persist_recommendation(
    triggered_by: list[str],
    result: dict[str, Any],
    now,
) -> int:
    """Insert a recommendation row. Returns the new row id.

    Validates that result has the required keys (status, report); raises
    KeyError on missing keys instead of silently coercing report to {}.
    Same pattern as B4a's _upsert_baseline guard — masks an upstream bug
    producing malformed result dicts otherwise.
    """
    import json
    import btc_api

    missing = [k for k in ("status", "report") if k not in result]
    if missing:
        raise KeyError(
            f"_persist_recommendation: result dict missing required keys: {missing}"
        )

    conn = btc_api.get_db()
    try:
        cursor = conn.execute(
            """INSERT INTO kill_switch_recommendations
                 (ts, triggered_by, slider_value, projected_pnl, projected_dd,
                  status, applied_ts, applied_by, report_json)
               VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)""",
            (
                now.isoformat(),
                json.dumps(triggered_by),
                result.get("slider_value"),
                result.get("projected_pnl"),
                result.get("projected_dd"),
                result["status"],
                json.dumps(result["report"]),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def _load_last_recalibration_ts() -> str | None:
    """Return the latest ts from kill_switch_recommendations, or None."""
    import btc_api
    conn = btc_api.get_db()
    try:
        row = conn.execute(
            "SELECT MAX(ts) FROM kill_switch_recommendations",
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row and row[0] else None


def _count_recalibrations_today(now) -> int:
    """Count rows in kill_switch_recommendations persisted today (UTC)."""
    import btc_api

    today_prefix = now.strftime("%Y-%m-%d")
    conn = btc_api.get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM kill_switch_recommendations WHERE ts LIKE ?",
            (today_prefix + "%",),
        ).fetchone()
    finally:
        conn.close()
    return int(row[0]) if row else 0


def _load_last_applied_recommendation() -> dict[str, Any] | None:
    """Return the most recent applied recommendation row as a dict, or None."""
    import btc_api

    conn = btc_api.get_db()
    try:
        row = conn.execute(
            """SELECT id, ts, slider_value, projected_pnl, projected_dd,
                      status, applied_ts, applied_by, report_json
               FROM kill_switch_recommendations
               WHERE status = 'applied'
               ORDER BY applied_ts DESC, id DESC
               LIMIT 1""",
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "id": row[0], "ts": row[1], "slider_value": row[2],
        "projected_pnl": row[3], "projected_dd": row[4],
        "status": row[5], "applied_ts": row[6], "applied_by": row[7],
        "report_json": row[8],
    }


def _load_last_calibration_regime_score() -> float | None:
    """Read regime_score from the latest recommendation's report_json.

    Returns None if no rows exist, report_json is malformed, or regime_score
    field is missing.
    """
    import json
    import btc_api

    conn = btc_api.get_db()
    try:
        row = conn.execute(
            """SELECT report_json FROM kill_switch_recommendations
               ORDER BY ts DESC, id DESC LIMIT 1""",
        ).fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        return None
    try:
        report = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    score = report.get("regime_score")
    if score is None:
        return None
    try:
        return float(score)
    except (TypeError, ValueError):
        return None


def _count_symbols_with_recent_alerts(window_hours: float) -> int:
    """Count distinct symbols whose v2_shadow decisions in the last window_hours
    have per_symbol_tier='ALERT' OR portfolio_tier IN ('REDUCED','FROZEN').
    """
    from datetime import datetime, timedelta, timezone
    import btc_api

    now = datetime.now(tz=timezone.utc)
    cutoff = (now - timedelta(hours=float(window_hours))).isoformat()
    conn = btc_api.get_db()
    try:
        row = conn.execute(
            """SELECT COUNT(DISTINCT symbol)
               FROM kill_switch_decisions
               WHERE engine = 'v2_shadow'
                 AND ts >= ?
                 AND (per_symbol_tier = 'ALERT'
                      OR portfolio_tier IN ('REDUCED', 'FROZEN'))""",
            (cutoff,),
        ).fetchone()
    finally:
        conn.close()
    return int(row[0]) if row else 0


def _mark_prior_pending_as_superseded(new_id: int) -> None:
    """Mark all pending recommendations except `new_id` as superseded."""
    import btc_api

    conn = btc_api.get_db()
    try:
        conn.execute(
            """UPDATE kill_switch_recommendations
               SET status = 'superseded'
               WHERE status = 'pending' AND id != ?""",
            (int(new_id),),
        )
        conn.commit()
    finally:
        conn.close()


def _send_telegram_recommendation(
    rec_id: int,
    result: dict[str, Any],
    triggered_by: list[str],
    cfg: dict[str, Any],
) -> None:
    """Send a Telegram notification for a pending recommendation.

    Wraps notifier.notify(SystemEvent(...), cfg). Fails open: any exception
    is logged and swallowed so notifier failure doesn't break the daemon
    or endpoint.
    """
    import notifier
    from notifier.events import SystemEvent

    try:
        slider = result.get("slider_value")
        pnl = result.get("projected_pnl")
        dd = result.get("projected_dd")
        slider_str = f"{slider}%" if slider is not None else "N/A"
        pnl_str = f"+${pnl:.0f}" if isinstance(pnl, (int, float)) else "N/A"
        dd_str = f"{dd:.2%}" if isinstance(dd, (int, float)) else "N/A"

        message = (
            f"Kill switch v2: nueva recomendación id={rec_id}. "
            f"Slider {slider_str}, {pnl_str} proyectado, DD {dd_str}. "
            f"Triggered by {triggered_by}. Ver dashboard."
        )

        notifier.notify(
            SystemEvent(kind="kill_switch_v2_recommendation", message=message),
            cfg=cfg,
        )
    except Exception as e:
        log.warning(
            "Telegram notification failed for rec_id=%s: %s",
            rec_id, e, exc_info=True,
        )


def kill_switch_calibrator_loop(cfg_fn, stop_event=None) -> None:
    """Daily auto-calibrator loop.

    Pattern mirrors health_monitor_loop. Wakes once per day, evaluates the
    safety_net trigger, persists a stub recommendation if fired. Manual
    triggers come through the FastAPI endpoint, not via this loop.

    Fail-open: any exception inside an iteration is logged with exc_info;
    the loop continues. stop_event.set() exits the loop cleanly.
    """
    import threading
    from datetime import datetime, timedelta, timezone

    if stop_event is None:
        stop_event = threading.Event()

    while not stop_event.is_set():
        try:
            cfg = cfg_fn()
            v2_cfg = (cfg.get("kill_switch", {}) or {}).get("v2", {}) or {}
            auto_cal_cfg = v2_cfg.get("auto_calibrator", {}) or {}
            safety_net_days = int(
                auto_cal_cfg.get("safety_net_days", _DEFAULT_SAFETY_NET_DAYS)
            )

            now = datetime.now(tz=timezone.utc)
            last_ts = _load_last_recalibration_ts()

            if should_run_safety_net(last_ts, now, safety_net_days):
                # B4b.2: real fitness via grid optimization (was stub in B4b.1)
                from strategy.kill_switch_v2_optimizer import run_optimization_v2
                try:
                    result = run_optimization_v2(cfg)
                except Exception as opt_err:
                    log.warning(
                        "run_optimization_v2 failed; falling back to stub: %s",
                        opt_err, exc_info=True,
                    )
                    result = run_optimization_stub(cfg)
                rec_id = _persist_recommendation(
                    triggered_by=["safety_net"], result=result, now=now,
                )
                # Stub notification — real Telegram lands in B4b.3 (#217)
                log.warning(
                    "Kill switch v2: recomendación id=%d (status=%s, "
                    "triggered_by=safety_net). Telegram pendiente B4b.3.",
                    rec_id, result["status"],
                )
        except Exception as e:
            log.warning(
                "kill_switch_calibrator_loop iteration failed: %s",
                e, exc_info=True,
            )

        # Sleep until next midnight UTC (or until stop_event is set)
        now = datetime.now(tz=timezone.utc)
        next_midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        seconds = max(60.0, (next_midnight - now).total_seconds())
        if stop_event.wait(seconds):
            break

    log.info("kill_switch_calibrator_loop exiting cleanly")
