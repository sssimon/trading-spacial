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
