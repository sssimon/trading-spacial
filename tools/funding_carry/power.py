"""One-shot power heuristic to SIZE the decay-kill window W (spec §6, audit N2).

W is sized so the expected LIVE standard error of the pooled annualized return is below
half the thin band (headline 0.0633 - threshold 0.0502 = 0.0131 -> half = 0.00655). The SE
is modeled on the EXPECTED LIVE regime (real per-symbol settlement cadence and symbol count),
NOT the full fossil — the fossil only supplies a sigma prior. N (consecutive non-overlapping
windows) is a separate confirmatory guard chosen for a target false-REFUTED rate. This module
is run ONCE during the plan; its outputs are hand-frozen into constants.py before any live run.

NOT a frequentist gate: the live CI (computed from live data in shadow.pooled_decay) controls
false-REFUTED in-regime. This only prevents picking an absurdly short W."""
from __future__ import annotations
import math


def min_window_weeks(*, per_symbol_settlements_per_week: int, n_symbols: int,
                     sigma_annual: float, target_half_band: float) -> int:
    """Smallest integer W (weeks) such that SE(pooled annualized return) <= target_half_band.

    Pooled equal-weight over n_symbols of a per-symbol mean over (W * settlements/week)
    observations: SE ~ sigma_annual / sqrt(n_symbols * W * settlements_per_week)."""
    per_week = max(1, per_symbol_settlements_per_week)
    denom_per_w = n_symbols * per_week
    # Solve sigma / sqrt(denom_per_w * W) <= band  ->  W >= (sigma/band)^2 / denom_per_w
    w_real = (sigma_annual / target_half_band) ** 2 / denom_per_w
    return max(1, math.ceil(w_real))
