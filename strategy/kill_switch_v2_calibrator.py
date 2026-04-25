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
