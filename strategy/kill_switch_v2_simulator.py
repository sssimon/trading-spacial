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

    def should_skip_or_reduce(
        self, symbol: str, entry_ts: str,
    ) -> tuple[bool, float]:
        """Return (skip, size_factor) for a hypothetical trade entry at entry_ts.

        Multiplicative composition:
          - portfolio_factor: NORMAL=1.0, WARNED=1.0, REDUCED=0.5, FROZEN=0.0
          - per_symbol_factor: NORMAL=1.0, ALERT=0.5
          - velocity_factor: 1.0 if no cooldown, 0.0 if cooldown active
          → product. 0.0 → skip=True.
        """
        from datetime import datetime, timezone
        from strategy.kill_switch_v2 import (
            evaluate_per_symbol_tier, evaluate_portfolio_tier,
            get_baseline_sigma_multiplier,
        )
        from health import compute_rolling_metrics_from_trades

        try:
            now = datetime.fromisoformat(entry_ts)
        except (TypeError, ValueError):
            # Conservative: malformed entry_ts → treat as skip
            return (True, 0.0)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        # B1 velocity check (kills size factor to 0 unconditionally)
        velocity_active = self._is_velocity_active(symbol, now)
        velocity_factor = 0.0 if velocity_active else 1.0

        # B4a per-symbol tier
        baseline = self._baselines.get(
            symbol, {"wr": 0.0, "sigma": 0.0, "count": 0},
        )
        rolling = compute_rolling_metrics_from_trades(
            self._symbol_trades.get(symbol, []), now=now,
        )
        rolling_wr = rolling.get("win_rate_20_trades")
        sigma_mult = get_baseline_sigma_multiplier(self.cfg_eff)
        v2_cfg = (self.cfg_eff.get("kill_switch", {}) or {}).get("v2", {}) or {}
        min_trades = int(v2_cfg.get("baseline_min_trades", 100))

        per_symbol_tier = evaluate_per_symbol_tier(
            rolling_wr_20=rolling_wr, baseline=baseline,
            sigma_multiplier=sigma_mult, trades_count=baseline["count"],
            min_trades=min_trades,
        )
        per_symbol_factor = {"NORMAL": 1.0, "ALERT": 0.5}.get(per_symbol_tier, 1.0)

        # B2 portfolio tier
        portfolio_dd = self._current_portfolio_dd()
        concurrent_failures = self._count_concurrent_failures(now)
        portfolio = evaluate_portfolio_tier(
            portfolio_dd=portfolio_dd,
            concurrent_failures=concurrent_failures,
            cfg=self.cfg_eff,
        )
        portfolio_factor = {
            "NORMAL": 1.0, "WARNED": 1.0, "REDUCED": 0.5, "FROZEN": 0.0,
        }.get(portfolio["tier"], 1.0)

        size_factor = portfolio_factor * per_symbol_factor * velocity_factor
        return (size_factor == 0.0, size_factor)
