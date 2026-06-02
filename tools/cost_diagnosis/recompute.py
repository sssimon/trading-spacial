"""Recompute the v2 model cost for a trade under baseline + correction params.

Reuses backtest_costs.compute_trade_costs unmodified. A correction is
(liq_mult, sf_div): liq_mult scales the liquidity denominator (1440 = per-minute
-> daily basis); sf_div divides the tier size_factor. Pre-registered sweep only.
"""
from __future__ import annotations

from dataclasses import replace

from backtest_costs import tier_for_symbol, load_calibration, compute_trade_costs

_CAL = load_calibration(path="costs_calibration.v2.json")

# (name, liq_mult, sf_div) — pre-registered, NOT fit to the answer.
CORRECTIONS = [
    ("baseline", 1.0, 1.0),
    ("daily_basis", 1440.0, 1.0),
    ("sf_div_37.95", 1.0, 37.95),
    ("sf_div_31.62", 1.0, 31.62),
    ("sf_div_10", 1.0, 10.0),
    ("both_37.95", 1440.0, 37.95),
    ("both_31.62", 1440.0, 31.62),
    ("both_10", 1440.0, 10.0),
]


def model_cost_bps(
    symbol: str, size_usd: float, liq_entry: float, liq_exit: float,
    holding_hours: float, *, liq_mult: float = 1.0, sf_div: float = 1.0,
) -> float:
    """Round-trip total_cost_bps the model would charge under the given correction."""
    tp = _CAL.tiers[tier_for_symbol(symbol)]
    tp = replace(tp, size_factor=tp.size_factor / sf_div)
    d = compute_trade_costs(
        entry_notional_usd=size_usd, exit_notional_usd=size_usd,
        entry_liquidity_usd_per_min=liq_entry * liq_mult,
        exit_liquidity_usd_per_min=liq_exit * liq_mult,
        tier_params=tp, holding_hours=holding_hours, model="v2",
    )
    return float(d["total_cost_bps"])
