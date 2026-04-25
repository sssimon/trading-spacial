"""V2 backtest grid optimization for auto-calibrator (#187 #216 B4b.2).

Replaces B4b.1's run_optimization_stub with a real fitness function:
loads closed trades from positions table, replays each across 21 slider
candidates [0..100, step 5] using V2KillSwitchSimulator, picks slider with
max PnL subject to dd_target constraint.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("kill_switch_v2_optimizer")

_DEFAULT_BACKTEST_WINDOW_DAYS = 365
_DEFAULT_DD_TARGET = -0.10
_DEFAULT_CAPITAL_USD = 1000.0
_GRID_STEP = 5


def _load_closed_positions_window(window_days: float, now) -> list[dict[str, Any]]:
    """Load closed positions with exit_ts within the last window_days, ordered by entry_ts."""
    from datetime import timedelta
    import btc_api

    cutoff = (now - timedelta(days=float(window_days))).isoformat()
    conn = btc_api.get_db()
    try:
        rows = conn.execute(
            """SELECT symbol, entry_ts, exit_ts, exit_reason, pnl_usd
               FROM positions
               WHERE status = 'closed'
                 AND exit_ts IS NOT NULL
                 AND exit_ts >= ?
               ORDER BY entry_ts""",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"symbol": r[0], "entry_ts": r[1], "exit_ts": r[2],
         "exit_reason": r[3], "pnl_usd": r[4]}
        for r in rows
    ]


def _override_slider(cfg: dict[str, Any], slider: int) -> dict[str, Any]:
    """Return a deep-copied cfg with kill_switch.v2.aggressiveness=slider.

    Creates the kill_switch.v2 block if missing.
    """
    import copy

    cfg_copy = copy.deepcopy(cfg) if cfg else {}
    ks = cfg_copy.setdefault("kill_switch", {})
    v2 = ks.setdefault("v2", {})
    v2["aggressiveness"] = slider
    return cfg_copy
