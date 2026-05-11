#!/usr/bin/env python3
"""A.0.2.diag #5 — Expectancy decomposition (killed-by-costs vs structural).

Per symbol, decomposes per-trade expectancy into gross and net components and
classifies the symbol into one of three categories:

  - "killed_by_costs": gross_expectancy > 0 AND net_expectancy < 0
                       → A.4 + #279 + sqrt v2 can rescue these
  - "structural":      gross_expectancy ≤ 0
                       → no tuning recovers these; remove or redesign
  - "survivor":        net_expectancy > 0
                       → currently viable (expecting 0 or very few)

Uses the cost-on backtest path. gross_pnl_pct is preserved by A.0.2 as a
per-trade field (pre-cost). expectancy is reported in bps for direct
comparison with cost_bps_mean.

Run:
    python scripts/a02_diag_5_expectancy_decomp.py --out /tmp/a02_diag_5.json
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import sys
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from scripts._a02_diag_lib import CURATED_SYMBOLS, load_config, run_simulation_cached


def classify(gross_bps: float, net_bps: float) -> str:
    if net_bps > 0:
        return "survivor"
    if gross_bps > 0 and net_bps < 0:
        return "killed_by_costs"
    return "structural"


def analyze_symbol(symbol: str, cfg: dict, overrides: dict) -> dict:
    trades, _equity = run_simulation_cached(
        symbol, with_costs=True, cfg=cfg, overrides=overrides,
    )
    closed = [t for t in trades if t.get("exit_reason") != "OPEN"]
    if not closed:
        return {"symbol": symbol, "error": "no closed trades"}

    gross_pcts = [float(t.get("gross_pnl_pct", 0.0)) for t in closed]
    net_pcts = [float(t["pnl_pct"]) for t in closed]
    cost_bps_list = [float(t.get("total_cost_bps", 0.0)) for t in closed]

    n = len(closed)
    gross_mean_pct = statistics.fmean(gross_pcts)
    net_mean_pct = statistics.fmean(net_pcts)
    cost_mean_bps = statistics.fmean(cost_bps_list)

    gross_bps = gross_mean_pct * 100.0  # 1% = 100 bps
    net_bps = net_mean_pct * 100.0

    return {
        "symbol": symbol,
        "n_trades": n,
        "expectancy_gross_pct": gross_mean_pct,
        "expectancy_net_pct": net_mean_pct,
        "expectancy_gross_bps": gross_bps,
        "expectancy_net_bps": net_bps,
        "cost_mean_bps": cost_mean_bps,
        "category": classify(gross_bps, net_bps),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/a02_diag_5.json")
    ap.add_argument("--symbols", default=",".join(CURATED_SYMBOLS))
    args = ap.parse_args()

    cfg, overrides = load_config()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    results = []
    for sym in symbols:
        print(f"  → {sym}", flush=True)
        r = analyze_symbol(sym, cfg, overrides)
        results.append(r)

    Path(args.out).write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {args.out}")

    # Summary
    print("\n## Expectancy decomposition")
    print("| Symbol | n | gross_bps | cost_bps | net_bps | category |")
    print("|---|---:|---:|---:|---:|---|")
    cat_counts = {"survivor": 0, "killed_by_costs": 0, "structural": 0}
    for r in results:
        if "error" in r:
            print(f"| {r['symbol']} | — | — | — | — | (no trades) |")
            continue
        cat_counts[r["category"]] += 1
        print(
            f"| {r['symbol']} | {r['n_trades']} | "
            f"{r['expectancy_gross_bps']:+.2f} | "
            f"{r['cost_mean_bps']:.2f} | "
            f"{r['expectancy_net_bps']:+.2f} | {r['category']} |"
        )
    print("\nCategory counts:")
    for k, v in cat_counts.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
