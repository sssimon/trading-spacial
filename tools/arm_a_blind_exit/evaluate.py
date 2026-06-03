"""Gross PnL, liquidity proxy, v3 recost, paired stats, verdict.

Reuses backtest_costs.compute_trade_costs UNMODIFIED. The active calibration
(costs_calibration.json) is v3 (version 3); we pass model='v3' explicitly and the
calibration's own globals."""
from __future__ import annotations
import math
import numpy as np
from backtest_costs import load_calibration, tier_for_symbol, compute_trade_costs

_CAL = load_calibration()                    # active = v3 (costs_calibration.json)
assert _CAL.active_model == "v3", f"expected v3 active calibration, got {_CAL.active_model}"


def gross_pnl(*, qty: float, entry: float, exit: float, direction: str) -> float:
    return qty * (exit - entry) if direction == "LONG" else qty * (entry - exit)


def liquidity_series(bars_1h: list[dict]) -> list[tuple[int, float]]:
    """(open_time, rolling-mean USD/min) per 1h bar; matches backtest.py:669-674
    (window 720, min_periods 120). NaN until warmed."""
    times = [b["open_time"] for b in bars_1h]
    upm = [b["close"] * b["volume"] / 60.0 for b in bars_1h]
    out = []
    for i in range(len(upm)):
        lo = max(0, i - 719)
        window = upm[lo:i + 1]
        liq = float(np.mean(window)) if len(window) >= 120 else float("nan")
        out.append((times[i], liq))
    return out


def liquidity_at(series: list[tuple[int, float]], ts_ms: int) -> float:
    """Last rolling-liquidity value at or before ts_ms (backtest.py _liquidity_at)."""
    val = float("nan")
    for t, liq in series:
        if t <= ts_ms:
            val = liq
        else:
            break
    return val


def recost_v3(
    *, symbol: str, entry_notional: float, exit_notional: float,
    entry_liq: float, exit_liq: float, holding_hours: float,
) -> float:
    """Round-trip v3 cost in USD for one trade. Liquidity NaN -> v3 fallback floor."""
    tp = _CAL.tiers[tier_for_symbol(symbol)]
    d = compute_trade_costs(
        entry_notional_usd=entry_notional, exit_notional_usd=exit_notional,
        entry_liquidity_usd_per_min=entry_liq, exit_liquidity_usd_per_min=exit_liq,
        tier_params=tp, holding_hours=holding_hours, model="v3",
        global_params=_CAL.global_,
    )
    return float(d["total_cost_usd"])
