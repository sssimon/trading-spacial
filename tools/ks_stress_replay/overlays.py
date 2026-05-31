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


class NoneOverlay:
    """No kill switch: every trade taken at full size. The unprotected reference."""

    def decide(self, symbol: str, entry_ts: str) -> tuple[bool, float]:
        return (False, 1.0)

    def record_close(
        self, symbol: str, exit_ts: str, pnl_usd: float, exit_reason: str,
    ) -> None:
        return None
