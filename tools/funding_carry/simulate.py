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
