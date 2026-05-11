#!/usr/bin/env python3
"""A.0.2.diag #6 — Holding period distribution (winners vs losers).

Per symbol, separates closed trades by net outcome (winner vs loser based on
net pnl_usd) and reports holding-period (duration_hours) distribution stats:
mean, median, p10, p90.

Decision tier (per spec #281): if winners hold materially longer than losers
(e.g., medians differ by factor 3+), the exit logic may be cutting positions
before the thesis develops — implication is that A.4 should consider widening
exits or extending holding rules.

Run:
    python scripts/a02_diag_6_holding_period.py --out /tmp/a02_diag_6.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

import sys
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from scripts._a02_diag_lib import CURATED_SYMBOLS, load_config, run_simulation_cached


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    arr = np.asarray(values, dtype=float)
    return {
        "n": len(arr),
        "mean_h": float(arr.mean()),
        "median_h": float(np.median(arr)),
        "p10_h": float(np.percentile(arr, 10)),
        "p90_h": float(np.percentile(arr, 90)),
    }


def analyze_symbol(symbol: str, cfg: dict, overrides: dict) -> dict:
    trades, _ = run_simulation_cached(symbol, with_costs=True, cfg=cfg, overrides=overrides)
    closed = [t for t in trades if t.get("exit_reason") != "OPEN"]
    if not closed:
        return {"symbol": symbol, "error": "no closed trades"}

    win_h = [float(t["duration_hours"]) for t in closed if t["pnl_usd"] > 0]
    loss_h = [float(t["duration_hours"]) for t in closed if t["pnl_usd"] <= 0]

    return {
        "symbol": symbol,
        "winners": _stats(win_h),
        "losers": _stats(loss_h),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/a02_diag_6.json")
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

    print("\n## Holding period distribution (hours)")
    print("| Symbol | Win n | Win med | Win p10 | Win p90 | "
          "Loss n | Loss med | Loss p10 | Loss p90 | ratio (med) |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in results:
        if "error" in r:
            print(f"| {r['symbol']} | — | — | — | — | — | — | — | — | — |")
            continue
        w, l = r["winners"], r["losers"]
        if w.get("n", 0) == 0 or l.get("n", 0) == 0:
            ratio = "—"
        else:
            ratio = f"{w['median_h'] / l['median_h']:.2f}" if l['median_h'] > 0 else "—"
        wparts = (
            (str(w.get("n", 0)),) +
            ((f"{w['median_h']:.1f}", f"{w['p10_h']:.1f}", f"{w['p90_h']:.1f}")
             if w.get("n", 0) > 0 else ("—", "—", "—"))
        )
        lparts = (
            (str(l.get("n", 0)),) +
            ((f"{l['median_h']:.1f}", f"{l['p10_h']:.1f}", f"{l['p90_h']:.1f}")
             if l.get("n", 0) > 0 else ("—", "—", "—"))
        )
        print(
            f"| {r['symbol']} | " + " | ".join(wparts) + " | " +
            " | ".join(lparts) + f" | {ratio} |"
        )


if __name__ == "__main__":
    main()
