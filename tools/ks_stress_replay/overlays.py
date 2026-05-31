"""Common-interface overlays mapping each kill-switch engine to per-trade
decisions for the chronological replay engine.

Interface (duck-typed; all overlays implement it):
    decide(symbol: str, entry_ts: str) -> tuple[bool, float]
        Returns (skip, size_factor) for a hypothetical entry at entry_ts (ISO).
    record_close(symbol: str, exit_ts: str, pnl_usd: float, exit_reason: str) -> None
        Feed the realized (already size-scaled) close back to the engine so its
        internal portfolio-DD / tier state evolves.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone


class NoneOverlay:
    """No kill switch: every trade taken at full size. The unprotected reference."""

    def decide(self, symbol: str, entry_ts: str) -> tuple[bool, float]:
        return (False, 1.0)

    def record_close(
        self, symbol: str, exit_ts: str, pnl_usd: float, exit_reason: str,
    ) -> None:
        return None


# v1 tier -> size factor, matching production (btc_scanner.py:264).
_V1_TIER_FACTOR = {
    "NORMAL": 1.0, "ALERT": 1.0, "REDUCED": 0.5, "PAUSED": 0.0, "PROBATION": 0.5,
}


def _ensure_aware(ts_iso: str) -> datetime:
    dt = datetime.fromisoformat(ts_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class V1Overlay:
    """Wraps the health-based v1 KillSwitchSimulator behind the common interface."""

    def __init__(self, cfg: dict):
        from backtest_kill_switch import KillSwitchSimulator
        self.sim = KillSwitchSimulator(cfg)

    def decide(self, symbol: str, entry_ts: str) -> tuple[bool, float]:
        tier = self.sim.get_tier(symbol)
        factor = _V1_TIER_FACTOR.get(tier, 1.0)
        return (factor == 0.0, factor)

    def record_close(
        self, symbol: str, exit_ts: str, pnl_usd: float, exit_reason: str,
    ) -> None:
        self.sim.on_trade_close(symbol, exit_ts, pnl_usd, _ensure_aware(exit_ts))


class V2Overlay:
    """Wraps V2KillSwitchSimulator at a fixed slider behind the common interface."""

    def __init__(
        self, cfg: dict, slider: float, capital_base: float,
        regime_score: float | None = None,
    ):
        from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator
        cfg2 = copy.deepcopy(cfg) if cfg else {}
        cfg2.setdefault("kill_switch", {}).setdefault("v2", {})
        cfg2["kill_switch"]["v2"]["aggressiveness"] = float(slider)
        self.sim = V2KillSwitchSimulator(
            cfg2, regime_score=regime_score, capital_base=capital_base,
        )

    def decide(self, symbol: str, entry_ts: str) -> tuple[bool, float]:
        return self.sim.should_skip_or_reduce(symbol, entry_ts)

    def record_close(
        self, symbol: str, exit_ts: str, pnl_usd: float, exit_reason: str,
    ) -> None:
        self.sim.on_trade_close(symbol, exit_ts, pnl_usd, exit_reason)
