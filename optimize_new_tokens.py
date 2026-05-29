#!/usr/bin/env python3
"""
Per-Symbol ATR Optimization for New Token Candidates.

Same grid search approach used for the original 7 winners.
Tests 105 combinations of atr_sl_mult, atr_tp_mult, atr_be_mult.

Usage:
    python optimize_new_tokens.py
    python optimize_new_tokens.py --symbol PENDLEUSDT
"""

import os
import sys
import json
import time
import argparse
import logging
import itertools
from datetime import datetime, timezone

import pandas as pd
import numpy as np

from backtest import (
    get_cached_data, simulate_strategy, calculate_metrics,
    get_historical_fear_greed, get_historical_funding_rate,
    INITIAL_CAPITAL,
)
from db.trials import claim_trial, finalize_trial

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger("optimize")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

CANDIDATES = ["PENDLEUSDT", "JUPUSDT", "RUNEUSDT", "SUIUSDT", "INJUSDT"]

GRID = {
    "atr_sl_mult": [0.5, 0.7, 1.0, 1.2, 1.5, 2.0, 2.5],
    "atr_tp_mult": [2.0, 3.0, 4.0, 5.0, 6.0],
    "atr_be_mult": [1.5, 2.0, 2.5],
}
# 7 * 5 * 3 = 105 combos


def optimize_symbol(symbol, sim_start, sim_end):
    log.info(f"Loading data for {symbol}...")
    df1h = get_cached_data(symbol, "1h", datetime(2021, 1, 1, tzinfo=timezone.utc))
    df4h = get_cached_data(symbol, "4h", datetime(2021, 1, 1, tzinfo=timezone.utc))
    df5m = get_cached_data(symbol, "5m", datetime(2021, 1, 1, tzinfo=timezone.utc))
    df1d = get_cached_data(symbol, "1d", datetime(2021, 1, 1, tzinfo=timezone.utc))
    df_fng = get_historical_fear_greed()
    df_funding = get_historical_funding_rate()

    log.info(f"Data: 1H={len(df1h)}, 4H={len(df4h)}, 5M={len(df5m)}")

    keys = list(GRID.keys())
    values = list(GRID.values())
    combos = list(itertools.product(*values))
    log.info(f"Testing {len(combos)} combinations for {symbol}...")

    results = []
    start_time = time.time()
    window_label = f"{sim_start}..{sim_end}"

    for idx, combo in enumerate(combos):
        params = dict(zip(keys, combo))

        trial_id = claim_trial(
            source="optimize_new_tokens",
            symbol=symbol,
            combo=params,
            window_label=window_label,
        )
        try:
            trades, equity = simulate_strategy(
                df1h=df1h, df4h=df4h, df5m=df5m,
                symbol=symbol, sl_mode="atr",
                atr_sl_mult=params["atr_sl_mult"],
                atr_tp_mult=params["atr_tp_mult"],
                atr_be_mult=params["atr_be_mult"],
                df1d=df1d,
                sim_start=sim_start, sim_end=sim_end,
                df_fng=df_fng, df_funding=df_funding,
            )

            if not trades:
                finalize_trial(trial_id, status="failed", error="no trades")
                continue

            metrics = calculate_metrics(trades, equity)
            if "error" in metrics:
                finalize_trial(trial_id, status="failed", error=str(metrics["error"]))
                continue

            finalize_trial(trial_id, status="ok", metrics=metrics)

            results.append({
                "symbol": symbol,
                **params,
                "trades": metrics["total_trades"],
                "win_rate": metrics["win_rate"],
                "net_pnl": metrics["net_pnl"],
                "profit_factor": metrics["profit_factor"],
                "max_drawdown": metrics["max_drawdown_pct"],
                "sharpe": metrics["sharpe_ratio"],
                "final_equity": metrics["final_equity"],
            })
        except Exception as e:
            finalize_trial(trial_id, status="failed", error=str(e))
            log.warning(f"  Error: {e}")
            continue

        if (idx + 1) % 20 == 0:
            elapsed = time.time() - start_time
            rate = (idx + 1) / elapsed
            remaining = (len(combos) - idx - 1) / rate
            log.info(f"  {idx+1}/{len(combos)} ({elapsed:.0f}s elapsed, ~{remaining:.0f}s remaining)")

    elapsed = time.time() - start_time
    log.info(f"Completed {len(results)} combos for {symbol} in {elapsed:.0f}s")

    results.sort(key=lambda x: x["net_pnl"], reverse=True)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", type=str)
    parser.add_argument("--start", default="2023-06-01")
    parser.add_argument("--end", default="2026-01-01")
    args = parser.parse_args()

    sim_start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    sim_end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    symbols = [args.symbol.upper()] if args.symbol else CANDIDATES

    all_best = {}

    for sym in symbols:
        print(f"\n{'='*60}")
        print(f"  {sym}")
        print(f"{'='*60}")

        results = optimize_symbol(sym, sim_start, sim_end)

        if not results:
            print("  No valid results")
            continue

        # Show top 5
        print(f"\n  {'SL':>5} {'TP':>5} {'BE':>5} | {'Trades':>6} {'WR%':>5} {'P&L':>10} {'PF':>5} {'DD%':>6}")
        print(f"  {'-'*5} {'-'*5} {'-'*5} | {'-'*6} {'-'*5} {'-'*10} {'-'*5} {'-'*6}")
        for r in results[:5]:
            pnl = f"${r['net_pnl']:+,.0f}"
            print(f"  {r['atr_sl_mult']:>5.1f} {r['atr_tp_mult']:>5.1f} {r['atr_be_mult']:>5.1f} | "
                  f"{r['trades']:>6} {r['win_rate']:>5.1f} {pnl:>10} {r['profit_factor']:>5.2f} {r['max_drawdown']:>6.1f}")

        best = results[0]
        all_best[sym] = best

        if best["net_pnl"] > 0:
            print(f"\n  >>> PROFITABLE: ${best['net_pnl']:+,.0f} with SL={best['atr_sl_mult']}x, TP={best['atr_tp_mult']}x, BE={best['atr_be_mult']}x")
        else:
            print(f"\n  >>> Best: ${best['net_pnl']:+,.0f} (still negative)")

        # Save CSV
        df = pd.DataFrame(results)
        csv_path = os.path.join(SCRIPT_DIR, "data", "backtest", f"{sym}_mr_optimization.csv")
        df.to_csv(csv_path, index=False)

    # Summary
    print(f"\n{'='*60}")
    print(f"  OPTIMIZATION SUMMARY")
    print(f"{'='*60}")

    profitable = {k: v for k, v in all_best.items() if v["net_pnl"] > 0}
    negative = {k: v for k, v in all_best.items() if v["net_pnl"] <= 0}

    if profitable:
        print(f"\n  PROFITABLE ({len(profitable)}):")
        for sym, r in sorted(profitable.items(), key=lambda x: x[1]["net_pnl"], reverse=True):
            print(f"    {sym:>12}: ${r['net_pnl']:+,.0f} (WR {r['win_rate']}%, PF {r['profit_factor']:.2f}) "
                  f"SL={r['atr_sl_mult']}x TP={r['atr_tp_mult']}x BE={r['atr_be_mult']}x")

    if negative:
        print(f"\n  NOT PROFITABLE ({len(negative)}):")
        for sym, r in sorted(negative.items(), key=lambda x: x[1]["net_pnl"], reverse=True):
            print(f"    {sym:>12}: ${r['net_pnl']:+,.0f} (best combo)")

    # Save optimized params
    if profitable:
        params_path = os.path.join(SCRIPT_DIR, "data", "backtest", "new_tokens_optimized.json")
        config_params = {}
        for sym, r in profitable.items():
            config_params[sym] = {
                "atr_sl_mult": r["atr_sl_mult"],
                "atr_tp_mult": r["atr_tp_mult"],
                "atr_be_mult": r["atr_be_mult"],
                "backtest_pnl": r["net_pnl"],
                "backtest_wr": r["win_rate"],
                "backtest_pf": r["profit_factor"],
            }
        with open(params_path, "w") as f:
            json.dump(config_params, f, indent=2)
        print(f"\n  Optimized params saved: {params_path}")


if __name__ == "__main__":
    main()
