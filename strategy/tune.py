"""Tune-result classification for the auto-tune pipeline (extracted from btc_scanner.py per #225).

Used by scripts/apply_tune_to_config.py to decide whether a (symbol, direction)
tuning result yields a dedicated triplet, fallback to per-symbol, or disabled.
"""
from __future__ import annotations

import numpy as np


def _classify_tune_result(count: int, profit_factor: float | None) -> str:
    """Classify a (symbol, direction) tuning result into one of three tiers.

    Returns one of: "dedicated", "fallback", "disabled".

    Rules:
        N ≥ 30 AND PF ≥ 1.3   → "dedicated"
        N ≥ 30 AND 1.0 ≤ PF < 1.3 → "fallback"
        N < 30 OR PF < 1.0    → "disabled"
        PF = inf (no losses)  → "dedicated" if N ≥ 30
        PF is None or NaN     → "disabled" (insufficient info)
    """
    if count == 0 or profit_factor is None:
        return "disabled"
    try:
        pf = float(profit_factor)
    except (TypeError, ValueError):
        return "disabled"
    if np.isnan(pf):
        return "disabled"
    if count < 30:
        return "disabled"
    if pf < 1.0:
        return "disabled"
    if pf < 1.3:
        return "fallback"
    return "dedicated"  # pf ≥ 1.3 (including inf)
