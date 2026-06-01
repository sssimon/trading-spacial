"""Pure per-trade assembly: live trades + liquidity series -> diagnostic rows.

No IO. The driver (run.py) provides the liquidity series map. Trades whose
liquidity is NaN at entry/exit are flagged `liquidity_unobservable` and excluded
from the reconcile aggregate (same spirit as the model's 100bps fallback).
"""
from __future__ import annotations

import math

import pandas as pd

from backtest_costs import tier_for_symbol
from tools.cost_diagnosis.liquidity import liquidity_at
from tools.cost_diagnosis.recompute import model_cost_bps, CORRECTIONS
from tools.cost_diagnosis.live_trades import LiveTrade


def assemble_per_trade(trades: list[LiveTrade], liq_map: dict) -> list[dict]:
    rows: list[dict] = []
    for t in trades:
        series = liq_map.get(t.symbol)
        liq_entry = liquidity_at(series, t.entry_ts) if series is not None else float("nan")
        liq_exit = liquidity_at(series, t.exit_ts) if series is not None else float("nan")
        unobservable = not (math.isfinite(liq_entry) and math.isfinite(liq_exit))

        observed_move_pct = abs(t.exit_price - t.entry_price) / t.entry_price * 100.0
        holding_hours = (
            pd.Timestamp(t.exit_ts) - pd.Timestamp(t.entry_ts)
        ).total_seconds() / 3600.0

        costs: dict = {}
        if not unobservable:
            for name, liq_mult, sf_div in CORRECTIONS:
                costs[name] = model_cost_bps(
                    t.symbol, t.size_usd, liq_entry, liq_exit, holding_hours,
                    liq_mult=liq_mult, sf_div=sf_div,
                )

        scan_fill_slip_pct = (
            abs(t.entry_price - t.scan_price) / t.scan_price * 100.0
            if t.scan_price else None
        )

        rows.append({
            "symbol": t.symbol, "direction": t.direction, "size_usd": t.size_usd,
            "tier": tier_for_symbol(t.symbol), "pnl_usd": t.pnl_usd,
            "entry_ts": t.entry_ts, "exit_ts": t.exit_ts,
            "observed_move_pct": observed_move_pct, "holding_hours": holding_hours,
            "liq_entry": liq_entry, "liq_exit": liq_exit,
            "liquidity_unobservable": unobservable,
            "scan_fill_slip_pct": scan_fill_slip_pct,
            "costs": costs,
        })
    return rows
