"""Wilder ATR + blind exit simulators with explicit intra-bar fill conventions.

All rules trail from the RUNNING peak/trough (causal — no look-ahead, Halberg).
Pessimistic fill: within a 5m bar the adverse extreme is assumed touched before
the favorable one. Optimistic: the reverse (sensitivity arm, spec §5)."""
from __future__ import annotations
from .constants import CHANDELIER_MULT, GIVEBACK_FRAC, MAX_HOLD_H


def wilder_atr(bars_1h: list[dict], period: int = 22) -> float:
    """Wilder's ATR over the LAST `period` true ranges ending at the final bar.

    bars_1h: chronological dicts with high/low/close. Needs >= period+1 bars."""
    if len(bars_1h) < period + 1:
        raise ValueError(f"need >= {period + 1} 1h bars, got {len(bars_1h)}")
    trs = []
    for prev, cur in zip(bars_1h[-period - 1:-1], bars_1h[-period:]):
        tr = max(
            cur["high"] - cur["low"],
            abs(cur["high"] - prev["close"]),
            abs(cur["low"] - prev["close"]),
        )
        trs.append(tr)
    atr = sum(trs) / period          # seed = simple mean of first window
    return atr
