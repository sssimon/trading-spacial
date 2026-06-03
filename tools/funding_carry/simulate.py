"""Delta-neutral funding-carry P&L per symbol: funding accrual + basis + v3 recost.

Position: long spot N, short perp N (delta ~ 0). Carry = funding the short collects
(when rate>0) + basis convergence, minus v3 cost on 4 fills. Directional price move
cancels between legs by construction."""
from __future__ import annotations
import sqlite3
from contextlib import closing
from backtest_costs import load_calibration, tier_for_symbol, compute_trade_costs
from .constants import OHLCV_DB, FUNDING_DB, NOTIONAL

_CAL = load_calibration()
assert _CAL.active_model == "v3", f"expected v3 calibration, got {_CAL.active_model}"


def funding_pnl(funding: list[tuple[int, float]], *, units: float, mark_price: float) -> float:
    """Sum of funding the short leg collects. funding: [(time_ms, rate)]. Positive
    rate -> short receives. mark_price is the perp notional basis per unit."""
    return sum(rate * mark_price * units for _, rate in funding)


def basis_pnl(*, spot_entry: float, perp_entry: float,
              spot_exit: float, perp_exit: float, units: float) -> float:
    """Delta-neutral price P&L = -units*(basis_exit - basis_entry); basis = perp - spot."""
    basis_entry = perp_entry - spot_entry
    basis_exit = perp_exit - spot_exit
    return -units * (basis_exit - basis_entry)


def recost_four_legs(*, symbol: str, units: float, spot_price: float,
                     perp_price: float, liq: float, holding_hours: float) -> float:
    """v3 cost (USD) of opening+closing BOTH legs = 2 round trips (4 fills).

    Each leg is one round trip; compute_trade_costs returns a round-trip cost, so two
    calls (spot leg, perp leg) cover all four fills."""
    tp = _CAL.tiers[tier_for_symbol(symbol)]

    def _rt(notional):
        d = compute_trade_costs(
            entry_notional_usd=notional, exit_notional_usd=notional,
            entry_liquidity_usd_per_min=liq, exit_liquidity_usd_per_min=liq,
            tier_params=tp, holding_hours=holding_hours, model="v3",
            global_params=_CAL.global_)
        return float(d["total_cost_usd"])

    return _rt(units * spot_price) + _rt(units * perp_price)


def carry_for_symbol(*, symbol: str, funding: list[tuple[int, float]],
                     spot_entry: float, spot_exit: float, perp_entry: float,
                     perp_exit: float, liq: float, notional: float = NOTIONAL) -> dict:
    """Full delta-neutral carry record for one symbol over its window."""
    units = notional / spot_entry
    mark = perp_entry                       # mark basis per unit for funding notional
    f_pnl = funding_pnl(funding, units=units, mark_price=mark)
    b_pnl = basis_pnl(spot_entry=spot_entry, perp_entry=perp_entry,
                      spot_exit=spot_exit, perp_exit=perp_exit, units=units)
    window_ms = (funding[-1][0] - funding[0][0]) if len(funding) >= 2 else 0
    window_hours = window_ms / 3_600_000
    cost = recost_four_legs(symbol=symbol, units=units, spot_price=spot_entry,
                            perp_price=perp_entry, liq=liq, holding_hours=window_hours)
    net = f_pnl + b_pnl - cost
    years = (window_hours / 24.0 / 365.0) or 1e-9
    return {"symbol": symbol, "funding_pnl": f_pnl, "basis_pnl": b_pnl, "cost_v3": cost,
            "net": net, "net_return": net / notional,
            "net_return_annual": (net / notional) / years,
            "n_funding": len(funding), "window_hours": window_hours}
