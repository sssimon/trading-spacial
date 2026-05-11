#!/usr/bin/env python3
"""A.0.2.diag #1 — Per-symbol summary (calibration baseline).

Single-row-per-symbol summary that A.3 will reference for setting validation
thresholds. Aggregates the cost-on backtest's per-trade output to compute:

  - n_trades, win_rate (from net pnl_usd)
  - avg_winner_pct_gross, avg_loser_pct_gross  (from gross_pnl_pct)
  - avg_winner_pct_net, avg_loser_pct_net      (from net pnl_pct)
  - expectancy_gross_bps, expectancy_net_bps   (per-trade mean × 100)
  - cost_bps_mean
  - participation_rate p50/p90/p99             (entry_notional / liq_per_min)

Participation rate is computed from the per-trade entry_notional_usd field
(A.0.2 preserves this) divided by the 30-day rolling 1H-volume proxy at the
entry bar — the same proxy backtest_costs uses internally.

Run:
    python scripts/a02_diag_1_per_symbol.py --out /tmp/a02_diag_1.json
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import numpy as np
import pandas as pd

import sys
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from scripts._a02_diag_lib import (
    CURATED_SYMBOLS, fetch_data, liquidity_per_min_series,
    load_config, run_simulation_cached,
)


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    return float(np.percentile(values, p))


def analyze_symbol(symbol: str, cfg: dict, overrides: dict) -> dict:
    df1h, *_ = fetch_data(symbol)
    if df1h.empty:
        return {"symbol": symbol, "error": "no data"}
    liq = liquidity_per_min_series(df1h)
    if liq.index.tz is not None:
        liq = liq.tz_localize(None)

    trades, _equity = run_simulation_cached(
        symbol, with_costs=True, cfg=cfg, overrides=overrides,
    )
    closed = [t for t in trades if t.get("exit_reason") != "OPEN"]
    if not closed:
        return {"symbol": symbol, "error": "no closed trades"}

    n = len(closed)
    wins_net = [t for t in closed if t["pnl_usd"] > 0]
    losses_net = [t for t in closed if t["pnl_usd"] <= 0]

    gross_pcts = [float(t.get("gross_pnl_pct", 0.0)) for t in closed]
    net_pcts = [float(t["pnl_pct"]) for t in closed]

    # Winners/losers split by NET outcome (consistent with calculate_metrics).
    win_gross = [float(t.get("gross_pnl_pct", 0.0)) for t in wins_net]
    win_net = [float(t["pnl_pct"]) for t in wins_net]
    loss_gross = [float(t.get("gross_pnl_pct", 0.0)) for t in losses_net]
    loss_net = [float(t["pnl_pct"]) for t in losses_net]

    cost_bps_list = [float(t.get("total_cost_bps", 0.0)) for t in closed]

    # Participation rate: entry_notional_usd / liquidity_at_entry_per_min.
    participation = []
    for t in closed:
        ent_n = float(t.get("entry_notional_usd", 0.0) or 0.0)
        if ent_n <= 0:
            continue
        et = pd.Timestamp(t["entry_time"])
        if et.tzinfo is not None:
            et = et.tz_localize(None)
        try:
            i = liq.index.get_indexer([et], method="ffill")[0]
        except Exception:
            continue
        if i < 0:
            continue
        l = float(liq.iloc[i])
        if not np.isfinite(l) or l <= 0:
            continue
        participation.append(ent_n / l)

    return {
        "symbol": symbol,
        "n_trades": n,
        "win_rate": (len(wins_net) / n * 100.0) if n > 0 else 0.0,
        "avg_winner_pct_gross": statistics.fmean(win_gross) if win_gross else 0.0,
        "avg_loser_pct_gross": statistics.fmean(loss_gross) if loss_gross else 0.0,
        "avg_winner_pct_net": statistics.fmean(win_net) if win_net else 0.0,
        "avg_loser_pct_net": statistics.fmean(loss_net) if loss_net else 0.0,
        "expectancy_gross_bps": statistics.fmean(gross_pcts) * 100.0,
        "expectancy_net_bps": statistics.fmean(net_pcts) * 100.0,
        "cost_bps_mean": statistics.fmean(cost_bps_list),
        "participation_rate_p50": _percentile(participation, 50),
        "participation_rate_p90": _percentile(participation, 90),
        "participation_rate_p99": _percentile(participation, 99),
        "n_participation_obs": len(participation),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/a02_diag_1.json")
    ap.add_argument("--symbols", default=",".join(CURATED_SYMBOLS))
    args = ap.parse_args()

    cfg, overrides = load_config()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    results = []
    for sym in symbols:
        print(f"  → {sym}", flush=True)
        results.append(analyze_symbol(sym, cfg, overrides))

    Path(args.out).write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {args.out}")

    print("\n## Per-symbol summary")
    print("| Symbol | n | WR% | win_g% | loss_g% | win_n% | loss_n% | "
          "exp_g_bps | exp_n_bps | cost_bps | part_p50 | part_p90 | part_p99 |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in results:
        if "error" in r:
            print(f"| {r['symbol']} | — | — | — | — | — | — | — | — | — | — | — | — |")
            continue
        print(
            f"| {r['symbol']} | {r['n_trades']} | "
            f"{r['win_rate']:.1f} | "
            f"{r['avg_winner_pct_gross']:+.3f} | {r['avg_loser_pct_gross']:+.3f} | "
            f"{r['avg_winner_pct_net']:+.3f} | {r['avg_loser_pct_net']:+.3f} | "
            f"{r['expectancy_gross_bps']:+.2f} | {r['expectancy_net_bps']:+.2f} | "
            f"{r['cost_bps_mean']:.1f} | "
            f"{r['participation_rate_p50']:.4f} | "
            f"{r['participation_rate_p90']:.4f} | "
            f"{r['participation_rate_p99']:.4f} |"
        )


if __name__ == "__main__":
    main()
