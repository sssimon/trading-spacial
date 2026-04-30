#!/usr/bin/env python3
"""A.0.2.diag #4 — Régime breakdown 10×3 (symbol × régime).

Tags each trade by the régime active at its entry_time, where régime is
defined by a 30-day rolling BTC daily return:

  Bear:     btc_30d_return < -5%
  Sideways: -5% ≤ btc_30d_return ≤ +15%
  Bull:     btc_30d_return > +15%

Boundaries chosen per spec #281 §Análisis 4. The simplified régime tag is
NOT detect_regime() from production — chosen for reproducibility independent
of the regime detector's evolving inputs.

Output: 10×3 table with (n_trades, win_rate%, expectancy_net_bps) per cell.

Run:
    python scripts/a02_diag_4_regime_breakdown.py --out /tmp/a02_diag_4.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import sys
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from scripts._a02_diag_lib import (
    CURATED_SYMBOLS, fetch_data, load_config, run_simulation_cached,
)


REGIMES = ("Bear", "Sideways", "Bull")


def _btc_30d_return_series():
    """BTC daily close → 30-day rolling return (decimal). Indexed by date."""
    from backtest import get_cached_data
    from datetime import datetime, timezone
    df1d = get_cached_data(
        "BTCUSDT", "1d",
        start_date=datetime(2022, 1, 1, tzinfo=timezone.utc),
    )
    if df1d.empty:
        raise SystemExit("BTCUSDT 1d data missing — cannot tag régimes")
    close = df1d["close"]
    if close.index.tz is not None:
        close = close.tz_localize(None)
    return close.pct_change(periods=30)  # 30 daily bars ≈ 30 calendar days


def regime_at(ts: pd.Timestamp, btc_30d: pd.Series) -> str:
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    try:
        i = btc_30d.index.get_indexer([ts], method="ffill")[0]
    except Exception:
        return "Sideways"
    if i < 0:
        return "Sideways"
    r = float(btc_30d.iloc[i])
    if not np.isfinite(r):
        return "Sideways"
    if r < -0.05:
        return "Bear"
    if r > 0.15:
        return "Bull"
    return "Sideways"


def analyze_symbol(symbol: str, btc_30d, cfg: dict, overrides: dict) -> dict:
    trades, _ = run_simulation_cached(symbol, with_costs=True, cfg=cfg, overrides=overrides)
    closed = [t for t in trades if t.get("exit_reason") != "OPEN"]
    if not closed:
        return {"symbol": symbol, "error": "no closed trades"}

    by_regime = {r: {"n": 0, "wins": 0, "sum_net_pct": 0.0} for r in REGIMES}
    for t in closed:
        rg = regime_at(pd.Timestamp(t["entry_time"]), btc_30d)
        by_regime[rg]["n"] += 1
        if t["pnl_usd"] > 0:
            by_regime[rg]["wins"] += 1
        by_regime[rg]["sum_net_pct"] += float(t["pnl_pct"])

    out = {"symbol": symbol, "regimes": {}}
    for r, agg in by_regime.items():
        n = agg["n"]
        out["regimes"][r] = {
            "n_trades": n,
            "win_rate_pct": (agg["wins"] / n * 100.0) if n > 0 else 0.0,
            "expectancy_net_bps": (agg["sum_net_pct"] / n * 100.0) if n > 0 else 0.0,
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/a02_diag_4.json")
    ap.add_argument("--symbols", default=",".join(CURATED_SYMBOLS))
    args = ap.parse_args()

    cfg, overrides = load_config()
    btc_30d = _btc_30d_return_series()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    results = []
    for sym in symbols:
        print(f"  → {sym}", flush=True)
        results.append(analyze_symbol(sym, btc_30d, cfg, overrides))

    Path(args.out).write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {args.out}")

    print("\n## Régime breakdown (Bear<-5% | Sideways -5%..15% | Bull>+15%)")
    print("| Symbol | Bear n | Bear WR% | Bear exp_bps | Side n | Side WR% | "
          "Side exp_bps | Bull n | Bull WR% | Bull exp_bps |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in results:
        if "error" in r:
            print(f"| {r['symbol']} | — | — | — | — | — | — | — | — | — |")
            continue
        rg = r["regimes"]
        cells = []
        for k in REGIMES:
            d = rg[k]
            cells.append(f"{d['n_trades']}")
            cells.append(f"{d['win_rate_pct']:.1f}")
            cells.append(f"{d['expectancy_net_bps']:+.1f}")
        print(f"| {r['symbol']} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
