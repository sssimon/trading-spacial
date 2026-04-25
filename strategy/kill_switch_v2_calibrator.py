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
