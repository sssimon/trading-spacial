"""In-memory kill switch simulator for backtests (#186 A6).

Mimics health.py's state machine using the now-pure functions:
    evaluate_state + compute_rolling_metrics_from_trades.

This lets backtest.simulate_strategy() drive a per-symbol tier (NORMAL / ALERT
/ REDUCED / PAUSED) without touching the production SQLite DB, so a historical
run can replay the exact behavior the live system would have taken at each bar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from health import compute_rolling_metrics_from_trades, evaluate_state


@dataclass
class SymbolState:
    """Per-symbol simulator state: current tier + accumulated closed trades."""

    tier: str = "NORMAL"
    closed_trades: list[dict[str, Any]] = field(default_factory=list)


class KillSwitchSimulator:
    """Per-symbol health tier tracking, driven by the pure logic prod uses.

    Usage:
        sim = KillSwitchSimulator(cfg)
        # ... during backtest, read tier before opening a position:
        tier = sim.get_tier("BTCUSDT")
        # ... after a position closes, feed the trade back in:
        new_tier = sim.on_trade_close("BTCUSDT", exit_ts_iso, pnl_usd, now)

    `cfg` is the full config dict (with a top-level `kill_switch` block). The
    simulator passes `cfg["kill_switch"]` into `evaluate_state` — matching how
    health.evaluate_and_record drives the production state machine.
    """

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.states: dict[str, SymbolState] = {}

    def _state_for(self, symbol: str) -> SymbolState:
        if symbol not in self.states:
            self.states[symbol] = SymbolState()
        return self.states[symbol]

    def get_tier(self, symbol: str) -> str:
        """Return the current tier for `symbol` (defaulting to NORMAL)."""
        return self._state_for(symbol).tier

    def on_trade_close(
        self,
        symbol: str,
        exit_ts_iso: str,
        pnl_usd: float,
        now: datetime,
    ) -> str:
        """Record a closed trade, recompute metrics, transition tier if needed.

        Returns the (possibly new) tier after the transition.
        """
        state = self._state_for(symbol)
        state.closed_trades.append({"exit_ts": exit_ts_iso, "pnl_usd": pnl_usd})
        metrics = compute_rolling_metrics_from_trades(state.closed_trades, now=now)
        ks_cfg = (self.cfg or {}).get("kill_switch", {}) or {}
        # evaluate_state signature: (metrics, current_state, manual_override, config)
        new_tier, _reason = evaluate_state(metrics, state.tier, False, ks_cfg)
        state.tier = new_tier
        return new_tier
