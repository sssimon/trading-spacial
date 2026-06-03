"""Delta-neutral funding-carry P&L per symbol: funding accrual + basis + v3 recost.

Position: long spot N, short perp N (delta ~ 0). Carry = funding the short collects
(when rate>0) + basis convergence, minus v3 cost on 4 fills. Directional price move
cancels between legs by construction."""
from __future__ import annotations
import math
import sqlite3
from contextlib import closing
from backtest_costs import load_calibration, tier_for_symbol, compute_trade_costs
from .constants import NOTIONAL

_CAL = load_calibration()
assert _CAL.active_model == "v3", f"expected v3 calibration, got {_CAL.active_model}"


def funding_pnl(funding: list[tuple[int, float]], *, units: float, mark_price: float) -> float:
    """Sum of funding the short leg collects. funding: [(time_ms, rate)]. Positive
    rate -> short receives.

    APPROXIMATION (pre-registered, spec §4): a constant `mark_price` (= perp entry) is
    used for every settlement. Real funding settles at the per-interval mark; over a long
    window with large price drift this understates income for appreciating assets (e.g.
    BTC 40k->100k) — i.e. CONSERVATIVE (biases toward FAIL, so a PASS is robust). Using the
    per-interval mark is the fast-follow if the verdict is marginal."""
    return sum(rate * mark_price * units for _, rate in funding)


def funding_increments(funding: list[tuple[int, float]], *, units: float,
                       mark_price: float) -> list[tuple[int, float]]:
    """Per-settlement funding P&L stream [(time_ms, increment)] — feeds the pooled,
    TIME-ORDERED in-sample equity curve for Gate B1 (a real drawdown, not symbol-order)."""
    return [(t, rate * mark_price * units) for t, rate in funding]


def basis_pnl(*, spot_entry: float, perp_entry: float,
              spot_exit: float, perp_exit: float, units: float) -> float:
    """Delta-neutral price P&L = -units*(basis_exit - basis_entry); basis = perp - spot."""
    basis_entry = perp_entry - spot_entry
    basis_exit = perp_exit - spot_exit
    return -units * (basis_exit - basis_entry)


def recost_four_legs(*, symbol: str, units: float, spot_price: float,
                     perp_price: float, liq: float, holding_hours: float) -> float:
    """v3 TRANSACTION cost (USD) of opening+closing BOTH legs = 2 round trips (4 fills).

    Each leg is one round trip; compute_trade_costs returns a round-trip cost, so two
    calls (spot leg, perp leg) cover all four fills. enable_funding=False on BOTH legs:
    the carry's funding is modeled EXPLICITLY (with real rates) in funding_pnl, so the
    cost model must contribute only spread+fee+slippage — not a generic funding charge
    (which would double-count funding and wrongly apply it to the spot leg)."""
    tp = _CAL.tiers[tier_for_symbol(symbol)]

    def _rt(notional):
        d = compute_trade_costs(
            entry_notional_usd=notional, exit_notional_usd=notional,
            entry_liquidity_usd_per_min=liq, exit_liquidity_usd_per_min=liq,
            tier_params=tp, holding_hours=holding_hours, model="v3",
            enable_funding=False, global_params=_CAL.global_)
        return float(d["total_cost_usd"])

    return _rt(units * spot_price) + _rt(units * perp_price)


def carry_for_symbol(*, symbol: str, funding: list[tuple[int, float]],
                     spot_entry: float, spot_exit: float, perp_entry: float,
                     perp_exit: float, liq: float, notional: float = NOTIONAL) -> dict:
    """Full delta-neutral carry record for one symbol over its window."""
    if not funding:
        raise ValueError(f"{symbol}: empty funding series")
    if any(math.isnan(p) for p in (spot_entry, spot_exit, perp_entry, perp_exit)):
        raise ValueError(f"{symbol}: missing spot/perp price at entry/exit (NaN); "
                         "drop the symbol upstream rather than poison the pool")
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


def load_funding(funding_db: str, symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
    with closing(sqlite3.connect(f"file:{funding_db}?mode=ro", uri=True)) as con:
        return [(int(t), float(r)) for t, r in con.execute(
            "SELECT funding_time_ms, funding_rate FROM funding WHERE symbol=? "
            "AND funding_time_ms>=? AND funding_time_ms<=? ORDER BY funding_time_ms",
            (symbol, start_ms, end_ms))]


def perp_price_at(funding_db: str, symbol: str, ts_ms: int) -> float:
    """Last perp close at or before ts_ms (NaN if none)."""
    with closing(sqlite3.connect(f"file:{funding_db}?mode=ro", uri=True)) as con:
        row = con.execute(
            "SELECT close FROM perp_klines WHERE symbol=? AND open_time<=? "
            "ORDER BY open_time DESC LIMIT 1", (symbol, ts_ms)).fetchone()
    return float(row[0]) if row else float("nan")


def spot_price_at(ohlcv_db: str, symbol: str, ts_ms: int) -> float:
    """Last spot 1h close at or before ts_ms (NaN if none)."""
    with closing(sqlite3.connect(f"file:{ohlcv_db}?mode=ro", uri=True)) as con:
        row = con.execute(
            "SELECT close FROM ohlcv WHERE symbol=? AND timeframe='1h' AND open_time<=? "
            "ORDER BY open_time DESC LIMIT 1", (symbol, ts_ms)).fetchone()
    return float(row[0]) if row else float("nan")


def spot_liquidity(ohlcv_db: str, symbol: str, ts_ms: int) -> float:
    """30-day rolling USD/min proxy at ts_ms from spot 1h bars (matches backtest.py:669).
    Returns NaN -> compute_trade_costs falls back to the v3 floor."""
    with closing(sqlite3.connect(f"file:{ohlcv_db}?mode=ro", uri=True)) as con:
        rows = con.execute(
            "SELECT close, volume FROM ohlcv WHERE symbol=? AND timeframe='1h' "
            "AND open_time<=? ORDER BY open_time DESC LIMIT 720", (symbol, ts_ms)).fetchall()
    if len(rows) < 120:
        return float("nan")
    return sum(c * v / 60.0 for c, v in rows) / len(rows)


def perp_mark_series(funding_db: str, symbol: str, times_ms: list[int]) -> list[float]:
    """Perp mark close at or before each funding settlement time (NaN if none)."""
    return [perp_price_at(funding_db, symbol, t) for t in times_ms]


def funding_pnl_per_interval(funding: list[tuple[int, float]], *, marks: list[float],
                             units: float) -> float:
    """Funding the short collects, marked PER SETTLEMENT: sum(rate_i * mark_i * units).
    More accurate than the constant-entry-mark approximation (spec §2)."""
    assert len(marks) == len(funding), \
        f"marks/funding length mismatch: {len(marks)} vs {len(funding)}"
    return sum(rate * mark * units for (_, rate), mark in zip(funding, marks))
