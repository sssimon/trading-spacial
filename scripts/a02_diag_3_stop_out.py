#!/usr/bin/env python3
"""A.0.2.diag #3 — Stop-out post-mortem with SL multiplier curve.

For each cost-on trade that exited via SL, simulate alternative SL distances
at multipliers {0.5x, 1.0x, 1.5x, 2.0x} of the actual SL. Walks forward
through 1H bars from entry_time, checks first-hit:

  - new SL hit  → stop loss at new wider/tighter level
  - new TP hit  → kept at original TP (sl-mult is the only knob)
  - end-of-data → marked as OPEN, MTM at last close

Reports per (symbol, multiplier):
  - pct_rescued     = trades where outcome flipped from "SL" to "TP" or
                      open-with-positive-MTM
  - avg_intermediate_DD_pct = MAE-from-entry, % terms
  - avg_final_pnl_pct      = aggregate net change in pnl_pct vs original

`atr_distance` is recovered from the original trade as
  abs(entry_price - exit_price) / atr_sl_mult_used  (because exit_price = sl
  for SL-reason exits). TP price is reconstructed from `atr_tp_mult_used` and
  this recovered ATR.

Run:
    python scripts/a02_diag_3_stop_out.py --out /tmp/a02_diag_3.json
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
    CURATED_SYMBOLS, fetch_data, load_config, run_simulation_cached,
)


SL_MULTIPLIERS = (0.5, 1.0, 1.5, 2.0)


def _resimulate(
    df1h: pd.DataFrame,
    entry_time: pd.Timestamp,
    entry_price: float,
    direction: str,
    atr_distance: float,
    sl_mult: float,
    tp_mult: float,
) -> dict:
    """Walk 1H bars forward; first-hit of new SL or TP; track MAE."""
    if direction == "LONG":
        new_sl = entry_price - atr_distance * sl_mult
        new_tp = entry_price + atr_distance * tp_mult
    else:
        new_sl = entry_price + atr_distance * sl_mult
        new_tp = entry_price - atr_distance * tp_mult

    if entry_time.tzinfo is not None:
        entry_time = entry_time.tz_localize(None)
    idx = df1h.index
    if idx.tz is not None:
        idx = idx.tz_localize(None)
        df1h = df1h.copy()
        df1h.index = idx
    try:
        i_start = idx.get_indexer([entry_time], method="ffill")[0]
    except Exception:
        return {"reason": "ENTRY_NOT_FOUND"}
    if i_start < 0 or i_start >= len(df1h) - 1:
        return {"reason": "ENTRY_AT_EOD"}

    mae_pct = 0.0
    for j in range(i_start + 1, len(df1h)):
        bar = df1h.iloc[j]
        high = float(bar["high"])
        low = float(bar["low"])
        if direction == "LONG":
            adverse = (entry_price - low) / entry_price * 100.0
            if adverse > mae_pct:
                mae_pct = adverse
            hit_sl = low <= new_sl
            hit_tp = high >= new_tp
        else:
            adverse = (high - entry_price) / entry_price * 100.0
            if adverse > mae_pct:
                mae_pct = adverse
            hit_sl = high >= new_sl
            hit_tp = low <= new_tp

        if hit_sl and hit_tp:
            # Same-bar collision — be conservative, assume SL first (matches
            # backtest.simulate_strategy convention when bar opens against entry).
            exit_price = new_sl
            reason = "SL"
        elif hit_sl:
            exit_price = new_sl
            reason = "SL"
        elif hit_tp:
            exit_price = new_tp
            reason = "TP"
        else:
            continue
        if direction == "SHORT":
            pnl_pct = (entry_price - exit_price) / entry_price * 100.0
        else:
            pnl_pct = (exit_price - entry_price) / entry_price * 100.0
        return {"reason": reason, "pnl_pct": pnl_pct, "mae_pct": mae_pct}

    # End of data — close at last bar
    last_close = float(df1h.iloc[-1]["close"])
    if direction == "SHORT":
        pnl_pct = (entry_price - last_close) / entry_price * 100.0
    else:
        pnl_pct = (last_close - entry_price) / entry_price * 100.0
    return {"reason": "OPEN", "pnl_pct": pnl_pct, "mae_pct": mae_pct}


def analyze_symbol(symbol: str, cfg: dict, overrides: dict) -> dict:
    df1h, *_ = fetch_data(symbol)
    if df1h.empty:
        return {"symbol": symbol, "error": "no data"}

    trades, _equity = run_simulation_cached(
        symbol, with_costs=True, cfg=cfg, overrides=overrides,
    )
    sl_trades = [t for t in trades if t.get("exit_reason") == "SL"]
    if not sl_trades:
        return {"symbol": symbol, "error": "no SL trades"}

    by_mult: dict[float, list[dict]] = {m: [] for m in SL_MULTIPLIERS}
    n_processed = 0
    for t in sl_trades:
        entry_price = float(t["entry_price"])
        exit_price = float(t["exit_price"])
        mult_used = float(t.get("atr_sl_mult_used") or 0.0)
        tp_mult_used = float(t.get("atr_tp_mult_used") or 0.0)
        if mult_used <= 0 or entry_price <= 0:
            continue
        atr_distance = abs(entry_price - exit_price) / mult_used
        if atr_distance <= 0:
            continue
        n_processed += 1
        et = pd.Timestamp(t["entry_time"])
        for m in SL_MULTIPLIERS:
            r = _resimulate(
                df1h, et, entry_price, t["direction"], atr_distance, m, tp_mult_used,
            )
            by_mult[m].append(r)

    out = {"symbol": symbol, "n_sl_trades": len(sl_trades),
           "n_processed": n_processed, "multipliers": {}}
    for m in SL_MULTIPLIERS:
        results = by_mult[m]
        if not results:
            out["multipliers"][m] = {"n": 0}
            continue
        valid = [r for r in results if "pnl_pct" in r]
        if not valid:
            out["multipliers"][m] = {"n": len(results)}
            continue
        # "Rescued" = no longer hits SL (TP-exit or OPEN with positive pnl)
        rescued = [
            r for r in valid
            if r["reason"] == "TP"
            or (r["reason"] == "OPEN" and r.get("pnl_pct", 0) > 0)
        ]
        out["multipliers"][m] = {
            "n": len(valid),
            "pct_rescued": len(rescued) / len(valid) * 100.0,
            "avg_intermediate_DD_pct": statistics.fmean(
                [r["mae_pct"] for r in valid]
            ),
            "avg_final_pnl_pct": statistics.fmean(
                [r["pnl_pct"] for r in valid]
            ),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/a02_diag_3.json")
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

    print("\n## Stop-out post-mortem (SL multiplier curve)")
    print("| Symbol | n_SL | mult | n_eval | %_rescued | avg_int_DD% | avg_final_pnl% |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for r in results:
        if "error" in r:
            print(f"| {r['symbol']} | — | — | — | — | — | — |")
            continue
        for m in SL_MULTIPLIERS:
            d = r["multipliers"].get(m, {})
            n = d.get("n", 0)
            if n == 0:
                print(f"| {r['symbol']} | {r['n_sl_trades']} | {m:.1f}x | 0 | — | — | — |")
                continue
            print(
                f"| {r['symbol']} | {r['n_sl_trades']} | {m:.1f}x | {n} | "
                f"{d.get('pct_rescued', 0):.1f} | "
                f"{d.get('avg_intermediate_DD_pct', 0):.2f} | "
                f"{d.get('avg_final_pnl_pct', 0):+.3f} |"
            )


if __name__ == "__main__":
    main()
