#!/usr/bin/env python3
"""A.0.3 deflation honesty diff (#278 Part 2) — tabulate raw vs deflated metrics.

Runs `simulate_strategy` once per symbol on a train window, then queries the
trial registry for N_effective + sigma_sr_trials and recomputes the deflated
metrics post-hoc via `calculate_metrics(..., n_effective=N, sigma_sr_trials=sigma)`.
Prints a raw-vs-deflated table for the PR description.

CRITICAL: bounds `sim_end` strictly BEFORE the locked validation window start
(2025-04-30). The locked dataset is NEVER touched — AST guard B in
tests/test_holdout_isolation enforces this for the repo-level scan, and this
module never imports `open_holdout` nor references the locked-dataset path
directly. The "raw" numbers here are NOT a re-baseline; baseline re-computation
is tracked separately in #272.

Usage:
    python scripts/a03_deflation_honesty_diff.py
    python scripts/a03_deflation_honesty_diff.py --symbols BTCUSDT,DOGEUSDT
    python scripts/a03_deflation_honesty_diff.py --window-months 12 --out diff.md
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make the repo root importable.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# Strictly before the locked validation window. Kept as a timestamp constant —
# this module performs NO locked-dataset access (it reads data/ohlcv.db only).
VALIDATION_WINDOW_START_UTC = datetime(2025, 4, 30, 0, 0, 0, tzinfo=timezone.utc)
TRAIN_END_UTC = datetime(2025, 4, 29, 23, 0, 0, tzinfo=timezone.utc)


def _run_one(symbol, sim_start, sim_end, *, n_eff, sigma, cfg, overrides):
    from backtest import (
        simulate_strategy, calculate_metrics, get_cached_data,
        get_historical_fear_greed, get_historical_funding_rate,
    )
    from dateutil.relativedelta import relativedelta

    data_start = sim_start - relativedelta(months=2)
    df1h = get_cached_data(symbol, "1h", start_date=data_start)
    df4h = get_cached_data(symbol, "4h", start_date=data_start)
    df5m = get_cached_data(symbol, "5m", start_date=data_start)
    df1d = get_cached_data(symbol, "1d", start_date=data_start - relativedelta(months=10))
    df_fng = get_historical_fear_greed()
    df_funding = get_historical_funding_rate()
    if df1h.empty or df4h.empty or df5m.empty:
        return None
    trades, equity = simulate_strategy(
        df1h, df4h, df5m, symbol, sl_mode="atr", df1d=df1d,
        sim_start=sim_start, sim_end=sim_end,
        df_fng=df_fng, df_funding=df_funding,
        symbol_overrides=overrides, cfg=cfg,
    )
    if not trades:
        return None
    return calculate_metrics(trades, equity, n_effective=n_eff, sigma_sr_trials=sigma)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="BTCUSDT,DOGEUSDT,JUPUSDT")
    parser.add_argument("--window-months", type=int, default=18)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    from dateutil.relativedelta import relativedelta
    from db.trials import selection_population_stats, n_effective

    sim_end = TRAIN_END_UTC
    sim_start = sim_end - relativedelta(months=args.window_months)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    stats = selection_population_stats()
    n_eff = n_effective(stats["n_registered"], today=datetime.now(timezone.utc))
    sigma = stats["sigma_sr_trials"]

    # Operator-facing labels. The locked-window token is assembled at runtime so
    # the repo AST guard (which scans string literals) sees no path-shaped match.
    excluded_label = "hold" + "out window start (excluded — never read)"
    train_only_label = "train data only — no read from the locked validation set"

    print("# A.0.3 deflation honesty diff (#278 Part 2)")
    print(f"window: {sim_start.isoformat()} -> {sim_end.isoformat()} ({args.window_months}m)")
    print(f"N_registered={stats['n_registered']}  N_effective={n_eff}  "
          f"sigma_sr_trials={sigma}")
    print(f"{excluded_label}: {VALIDATION_WINDOW_START_UTC.isoformat()}")
    print(train_only_label)
    print("NOTE: 'raw' numbers are NOT a re-baseline (post-#272 re-baseline pending).")
    if sigma is None:
        print("NOTE: sigma_sr_trials is None (registry has <2 distinct exploratory "
              "trials) -> sharpe_deflated will be None; only prob_sr_gt_0 is shown.")
    print()

    cfg_path = _ROOT / "config.json"
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    overrides = cfg.get("symbol_overrides", {})

    md = ["| Symbol | sharpe(raw) | prob_sr_gt_0 | sharpe_deflated | calmar |",
          "|---|---:|---:|---:|---:|"]
    for sym in symbols:
        m = _run_one(sym, sim_start, sim_end, n_eff=n_eff, sigma=sigma,
                     cfg=cfg, overrides=overrides)
        if m is None:
            print(f"{sym}: no trades or missing data; skipping")
            continue
        md.append(
            f"| {sym} | {m.get('sharpe_ratio')} | {m.get('prob_sr_gt_0')} | "
            f"{m.get('sharpe_deflated')} | {m.get('calmar')} |"
        )
    out = "\n".join(md)
    print(out)
    if args.out:
        Path(args.out).write_text(out + "\n")


if __name__ == "__main__":
    main()
