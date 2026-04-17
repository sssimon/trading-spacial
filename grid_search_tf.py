#!/usr/bin/env python3
"""
Grid Search — Trend-Following Parameter Optimization Per-Symbol

Tests combinations of EMA_FAST, EMA_SLOW, EMA_FILTER, ATR_TRAIL, RSI_ENTRY
for the trend-following strategy on each symbol.

Forces strategy="trend_following" to isolate TF performance from mean-reversion.

Usage:
    python grid_search_tf.py                          # All target symbols
    python grid_search_tf.py --symbol SOLUSDT         # Single symbol
    python grid_search_tf.py --symbol SOLUSDT --quick # Reduced grid (faster)
"""

import os
import sys
import json
import time
import argparse
import logging
import itertools
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import numpy as np

from backtest import (
    get_cached_data, simulate_strategy, calculate_metrics,
    get_historical_fear_greed, get_historical_funding_rate,
    INITIAL_CAPITAL, DATA_DIR,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger("grid_search_tf")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Symbols to optimize — prioritized by proximity to breakeven
TARGET_SYMBOLS = [
    "XRPUSDT",    # -$1,143 (30.1% WR) — closest to breakeven
    "SOLUSDT",    # -$3,042 (26.9% WR) — poster child for trending
    "BTCUSDT",    # -$3,377 (27.5% WR) — reference
    "AVAXUSDT",   # -$3,676 (27.6% WR)
    "NEARUSDT",   # -$3,793 (25.4% WR)
    "ETHUSDT",    # -$4,235 (28.5% WR)
    "BNBUSDT",    # -$6,210 (24.9% WR) — trending token
    "APTUSDT",    # -$4,704 (24.4% WR)
    "DOTUSDT",    # -$7,286 (22.6% WR)
    "OPUSDT",     # -$5,881 (22.6% WR)
    "ATOMUSDT",   # -$5,129 (24.9% WR)
    "LINKUSDT",   # -$7,734 (23.0% WR)
]

# Also run on the 3 profitable symbols to see if TF improves them
PROFITABLE_SYMBOLS = ["DOGEUSDT", "XLMUSDT", "ADAUSDT"]

# Full parameter grid
FULL_GRID = {
    "tf_ema_fast":       [5, 8, 9, 12],
    "tf_ema_slow":       [15, 20, 21, 26],
    "tf_ema_filter":     [40, 50, 55, 100],
    "tf_atr_trail":      [1.5, 2.0, 2.5, 3.0],
    "tf_rsi_entry_long": [50, 55, 60],
}
# 4 * 4 * 4 * 4 * 3 = 768 combos

# Quick grid (for testing)
QUICK_GRID = {
    "tf_ema_fast":       [8, 12],
    "tf_ema_slow":       [20, 26],
    "tf_ema_filter":     [50, 100],
    "tf_atr_trail":      [2.0, 3.0],
    "tf_rsi_entry_long": [50, 55],
}
# 2 * 2 * 2 * 2 * 2 = 32 combos


def grid_search_symbol(symbol: str, grid: dict,
                       sim_start: datetime, sim_end: datetime,
                       use_5m_trigger: bool = False) -> list[dict]:
    """Run grid search for one symbol. Returns sorted results."""

    log.info(f"Loading data for {symbol}...")
    df1h = get_cached_data(symbol, "1h", datetime(2021, 1, 1, tzinfo=timezone.utc))
    df4h = get_cached_data(symbol, "4h", datetime(2021, 1, 1, tzinfo=timezone.utc))
    df5m = get_cached_data(symbol, "5m", datetime(2021, 1, 1, tzinfo=timezone.utc))
    df1d = get_cached_data(symbol, "1d", datetime(2021, 1, 1, tzinfo=timezone.utc))

    df_fng = get_historical_fear_greed()
    df_funding = get_historical_funding_rate()

    log.info(f"Data: 1H={len(df1h)}, 4H={len(df4h)}, 5M={len(df5m)}, 1D={len(df1d)}")

    # Generate all parameter combinations
    keys = list(grid.keys())
    values = list(grid.values())
    combos = list(itertools.product(*values))
    total = len(combos)

    log.info(f"Testing {total} parameter combinations for {symbol}...")
    results = []
    start_time = time.time()

    for idx, combo in enumerate(combos):
        params = dict(zip(keys, combo))

        # Skip invalid combos (fast >= slow)
        if params["tf_ema_fast"] >= params["tf_ema_slow"]:
            continue

        # Build config that forces trend_following
        config = {
            "symbol_overrides": {
                symbol: {
                    "strategy": "trend_following",
                    "use_5m_trigger": use_5m_trigger,
                    **params,
                    "tf_rsi_entry_short": 100 - params["tf_rsi_entry_long"],
                }
            }
        }

        try:
            trades, equity = simulate_strategy(
                df1h=df1h, df4h=df4h, df5m=df5m,
                symbol=symbol,
                df1d=df1d,
                sim_start=sim_start, sim_end=sim_end,
                df_fng=df_fng, df_funding=df_funding,
                backtest_config=config,
            )

            if not trades:
                continue

            metrics = calculate_metrics(trades, equity)
            if "error" in metrics:
                continue

            result = {
                "symbol": symbol,
                **params,
                "use_5m_trigger": use_5m_trigger,
                "trades": metrics["total_trades"],
                "win_rate": metrics["win_rate"],
                "net_pnl": metrics["net_pnl"],
                "profit_factor": metrics["profit_factor"],
                "max_drawdown": metrics["max_drawdown_pct"],
                "sharpe": metrics["sharpe_ratio"],
                "final_equity": metrics["final_equity"],
                "trades_per_month": metrics["trades_per_month"],
            }
            results.append(result)

        except Exception as e:
            log.warning(f"  Error with {params}: {e}")
            continue

        # Progress every 50 combos
        if (idx + 1) % 50 == 0:
            elapsed = time.time() - start_time
            rate = (idx + 1) / elapsed
            remaining = (total - idx - 1) / rate
            log.info(f"  {idx+1}/{total} ({elapsed:.0f}s elapsed, ~{remaining:.0f}s remaining)")

    elapsed = time.time() - start_time
    log.info(f"Completed {len(results)} valid combos for {symbol} in {elapsed:.0f}s")

    # Sort by net_pnl descending
    results.sort(key=lambda x: x["net_pnl"], reverse=True)
    return results


def print_top_results(results: list[dict], n: int = 10):
    """Print top N results."""
    if not results:
        print("  No valid results")
        return

    print(f"\n  {'EMA_F':>5} {'EMA_S':>5} {'EMA_FL':>6} {'ATR_T':>5} {'RSI':>3} "
          f"{'Trades':>6} {'WR%':>5} {'P&L':>10} {'PF':>5} {'DD%':>5} {'Sharpe':>6}")
    print(f"  {'-'*5} {'-'*5} {'-'*6} {'-'*5} {'-'*3} "
          f"{'-'*6} {'-'*5} {'-'*10} {'-'*5} {'-'*5} {'-'*6}")

    for r in results[:n]:
        pnl_str = f"${r['net_pnl']:+,.0f}"
        print(f"  {r['tf_ema_fast']:>5} {r['tf_ema_slow']:>5} {r['tf_ema_filter']:>6} "
              f"{r['tf_atr_trail']:>5.1f} {r['tf_rsi_entry_long']:>3} "
              f"{r['trades']:>6} {r['win_rate']:>5.1f} {pnl_str:>10} "
              f"{r['profit_factor']:>5.2f} {r['max_drawdown']:>5.1f} {r['sharpe']:>6.2f}")


def main():
    parser = argparse.ArgumentParser(description="Grid Search TF Parameters")
    parser.add_argument("--symbol", type=str, help="Single symbol to optimize")
    parser.add_argument("--quick", action="store_true", help="Use reduced grid (32 combos)")
    parser.add_argument("--start", type=str, default="2023-01-01", help="Sim start date")
    parser.add_argument("--end", type=str, default="2026-01-01", help="Sim end date")
    parser.add_argument("--top", type=int, default=10, help="Show top N results")
    parser.add_argument("--all", action="store_true", help="Include profitable symbols too")
    args = parser.parse_args()

    grid = QUICK_GRID if args.quick else FULL_GRID
    sim_start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    sim_end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        symbols = TARGET_SYMBOLS[:]
        if args.all:
            symbols.extend(PROFITABLE_SYMBOLS)

    total_combos = len(list(itertools.product(*grid.values())))
    log.info(f"Grid: {total_combos} combos per symbol, {len(symbols)} symbols")

    all_results = {}
    summary = []

    for sym in symbols:
        print(f"\n{'='*60}")
        print(f"  {sym}")
        print(f"{'='*60}")

        results = grid_search_symbol(sym, grid, sim_start, sim_end, use_5m_trigger=False)
        all_results[sym] = results
        print_top_results(results, args.top)

        if results:
            best = results[0]
            summary.append(best)
            if best["net_pnl"] > 0:
                print(f"\n  >>> PROFITABLE: ${best['net_pnl']:+,.0f} with "
                      f"EMA({best['tf_ema_fast']}/{best['tf_ema_slow']}/{best['tf_ema_filter']}), "
                      f"ATR_TRAIL={best['tf_atr_trail']}, RSI={best['tf_rsi_entry_long']}")
            else:
                print(f"\n  >>> Best: ${best['net_pnl']:+,.0f} (still negative)")

    # Final summary
    print(f"\n{'='*60}")
    print(f"  GRID SEARCH SUMMARY")
    print(f"{'='*60}")

    profitable = [s for s in summary if s["net_pnl"] > 0]
    negative = [s for s in summary if s["net_pnl"] <= 0]

    if profitable:
        print(f"\n  PROFITABLE ({len(profitable)} symbols):")
        for r in sorted(profitable, key=lambda x: x["net_pnl"], reverse=True):
            print(f"    {r['symbol']:>10}: ${r['net_pnl']:+,.0f} "
                  f"(WR {r['win_rate']}%, PF {r['profit_factor']:.2f}, "
                  f"EMA {r['tf_ema_fast']}/{r['tf_ema_slow']}/{r['tf_ema_filter']}, "
                  f"ATR {r['tf_atr_trail']}, RSI {r['tf_rsi_entry_long']})")

    if negative:
        print(f"\n  NOT PROFITABLE ({len(negative)} symbols):")
        for r in sorted(negative, key=lambda x: x["net_pnl"], reverse=True):
            print(f"    {r['symbol']:>10}: ${r['net_pnl']:+,.0f} (best combo)")

    # Save results to CSV
    output_dir = os.path.join(SCRIPT_DIR, "data", "backtest")
    os.makedirs(output_dir, exist_ok=True)

    for sym, results in all_results.items():
        if results:
            df = pd.DataFrame(results)
            csv_path = os.path.join(output_dir, f"{sym}_tf_grid_search.csv")
            df.to_csv(csv_path, index=False)
            log.info(f"Results saved: {csv_path}")

    # Save best params as JSON for easy config integration
    best_params = {}
    for r in summary:
        if r["net_pnl"] > 0:
            best_params[r["symbol"]] = {
                "strategy": "auto",
                "tf_ema_fast": r["tf_ema_fast"],
                "tf_ema_slow": r["tf_ema_slow"],
                "tf_ema_filter": r["tf_ema_filter"],
                "tf_atr_trail": r["tf_atr_trail"],
                "tf_rsi_entry_long": r["tf_rsi_entry_long"],
                "tf_rsi_entry_short": 100 - r["tf_rsi_entry_long"],
                "use_5m_trigger": False,
                "backtest_pnl": r["net_pnl"],
                "backtest_wr": r["win_rate"],
                "backtest_pf": r["profit_factor"],
            }

    if best_params:
        json_path = os.path.join(output_dir, "tf_optimized_params.json")
        with open(json_path, "w") as f:
            json.dump(best_params, f, indent=2)
        log.info(f"Optimized params saved: {json_path}")
        print(f"\n  Config-ready params saved to: {json_path}")

    print(f"\n  Total profitable: {len(profitable)}/{len(summary)} symbols")


if __name__ == "__main__":
    main()
