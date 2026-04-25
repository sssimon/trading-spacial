"""V2 in-memory kill switch simulator for backtest replay (#187 #216 B4b.2).

Mirrors B1 velocity + B2 portfolio DD + B3 regime + B4a baselines logic in
pure-memory form. Used by run_optimization_v2 to evaluate fitness across
slider candidates without DB I/O.

Construction applies B3 regime adjustment to cfg.aggressiveness once; the
simulator subsequently uses the adjusted slider for all threshold
interpolations.
"""
from __future__ import annotations

from typing import Any


class V2KillSwitchSimulator:
    """In-memory v2 kill switch state for backtest replay."""

    def __init__(
        self,
        cfg: dict[str, Any],
        regime_score: float | None = None,
        capital_base: float = 1000.0,
    ):
        from strategy.kill_switch_v2 import apply_regime_adjustment

        self.cfg_eff = apply_regime_adjustment(cfg, regime_score)
        self.regime_score = regime_score
        self.capital_base = float(capital_base)

        # Per-symbol: baseline cache + velocity state
        self._baselines: dict[str, dict] = {}
        self._velocity_state: dict[str, dict] = {}
        # Cumulative closed trades, used for portfolio DD + velocity SL window
        self._all_trades: list[dict] = []
        # Per-symbol closed trades for baseline/rolling
        self._symbol_trades: dict[str, list[dict]] = {}

    def _current_portfolio_dd(self) -> float:
        """Compute portfolio DD from cumulative trade PnL on capital_base.

        Returns negative value if in drawdown; 0.0 otherwise.
        """
        if not self._all_trades:
            return 0.0
        equity = self.capital_base
        peak = self.capital_base
        for trade in self._all_trades:
            equity += float(trade.get("pnl_usd") or 0)
            peak = max(peak, equity)
        if peak <= 0:
            return 0.0
        return (equity - peak) / peak

    def _is_velocity_active(self, symbol: str, now) -> bool:
        """Check if velocity cooldown is still active for symbol at `now`."""
        from datetime import datetime, timezone

        state = self._velocity_state.get(symbol, {})
        cooldown = state.get("velocity_cooldown_until")
        if not cooldown:
            return False
        try:
            parsed = datetime.fromisoformat(cooldown)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed > now
        except (TypeError, ValueError):
            return False

    def _count_concurrent_failures(self, now) -> int:
        """Count symbols with active velocity cooldowns at `now`.

        Used as proxy for B2 portfolio's concurrent_failures input.
        """
        return sum(
            1 for sym in self._velocity_state
            if self._is_velocity_active(sym, now)
        )
