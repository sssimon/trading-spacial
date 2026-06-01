"""Read-only edge DIAGNOSIS: WHY does the base strategy lose on pre-holdout?

Follow-up to measure_base_edge.py (which established the SIGN: catastrophic loss,
0/7 winners). This breaks the loss down to locate the cause:

  - signal problem  -> gross P&L (before costs) is already negative => bad
                       direction/timing; costs are not the issue.
  - cost/sizing     -> gross positive but net negative => fees/slippage/funding
                       or sizing is eating a real edge.
  - win/loss shape  -> 2-19% win rates only survive if winners >> losers (R).
                       If avg|win| ~ avg|loss|, low win rate is fatal by itself.
  - exit mix        -> mostly SL => stops too tight / entries mistimed.
  - direction split -> long-only into 2022 bear vs symmetric.

Dumps the full stream to JSON so further cuts are instant (no re-simulation).
Read-only on OHLCV; holdout cutoff enforced inside generate_base_stream (#3).
"""
from __future__ import annotations

import json
import os
from collections import Counter

from tools.ks_stress_replay.base_stream import generate_base_stream

OUT_DIR = os.path.join("data", "retune", "2026-06-01-base-edge-diag")


def _num(tr: dict, key: str) -> float:
    try:
        return float(tr.get(key, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _per_symbol(sym: str, trades: list[dict]) -> dict:
    n = len(trades)
    wins = [t for t in trades if _num(t, "pnl_usd") > 0]
    losses = [t for t in trades if _num(t, "pnl_usd") <= 0]
    gross = sum(_num(t, "gross_pnl_usd") for t in trades)
    net = sum(_num(t, "pnl_usd") for t in trades)
    cost = sum(_num(t, "total_cost_usd") for t in trades)
    avg_win_pct = (sum(_num(t, "pnl_pct") for t in wins) / len(wins)) if wins else 0.0
    avg_loss_pct = (sum(_num(t, "pnl_pct") for t in losses) / len(losses)) if losses else 0.0
    expectancy_pct = (sum(_num(t, "pnl_pct") for t in trades) / n) if n else 0.0
    rr = abs(avg_win_pct / avg_loss_pct) if avg_loss_pct else 0.0
    dirs = Counter(str(t.get("direction", "?")) for t in trades)
    exits = Counter(str(t.get("exit_reason", "?")) for t in trades)
    # net pnl split by direction
    dir_pnl: dict = {}
    for t in trades:
        d = str(t.get("direction", "?"))
        dir_pnl[d] = dir_pnl.get(d, 0.0) + _num(t, "pnl_usd")
    return {
        "symbol": sym, "n": n,
        "win_rate_pct": (len(wins) / n * 100.0) if n else 0.0,
        "gross_usd": gross, "net_usd": net, "cost_usd": cost,
        "avg_win_pct": avg_win_pct, "avg_loss_pct": avg_loss_pct,
        "rr": rr, "expectancy_pct": expectancy_pct,
        "dirs": dict(dirs), "exits": dict(exits), "dir_pnl": dir_pnl,
    }


def diagnose() -> dict:
    stream = generate_base_stream()
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "base_stream.json"), "w", encoding="utf-8") as f:
        json.dump(stream, f, default=str)
    rows = [_per_symbol(s, t) for s, t in stream.items() if t]
    return {"rows": rows, "stream": stream}


def _print(rep: dict) -> None:
    rows = rep["rows"]

    print("\n=== SIGNAL vs COST (gross before costs / net after / cost drag) ===")
    hdr = f"{'symbol':<9} {'n':>4} {'gross$':>10} {'net$':>10} {'cost$':>9} {'gross<0?':>9}"
    print(hdr); print("-" * len(hdr))
    for r in rows:
        print(f"{r['symbol']:<9} {r['n']:>4d} {r['gross_usd']:>10.1f} "
              f"{r['net_usd']:>10.1f} {r['cost_usd']:>9.1f} "
              f"{str(r['gross_usd'] < 0):>9}")

    print("\n=== WIN/LOSS SHAPE (does R compensate the low win rate?) ===")
    hdr = (f"{'symbol':<9} {'win%':>6} {'avgWin%':>8} {'avgLoss%':>9} "
           f"{'R(W/L)':>7} {'exp%/trade':>11}")
    print(hdr); print("-" * len(hdr))
    for r in rows:
        print(f"{r['symbol']:<9} {r['win_rate_pct']:>6.1f} {r['avg_win_pct']:>8.2f} "
              f"{r['avg_loss_pct']:>9.2f} {r['rr']:>7.2f} {r['expectancy_pct']:>11.3f}")

    print("\n=== EXIT REASON MIX ===")
    for r in rows:
        mix = ", ".join(f"{k}:{v}" for k, v in sorted(r["exits"].items(),
                                                       key=lambda kv: -kv[1]))
        print(f"{r['symbol']:<9} {mix}")

    print("\n=== DIRECTION SPLIT (net $ by side) ===")
    for r in rows:
        ds = ", ".join(f"{k}:{cnt} (net ${r['dir_pnl'].get(k, 0):.0f})"
                       for k, cnt in sorted(r["dirs"].items()))
        print(f"{r['symbol']:<9} {ds}")

    # aggregate verdict
    g = sum(r["gross_usd"] for r in rows)
    nnet = sum(r["net_usd"] for r in rows)
    c = sum(r["cost_usd"] for r in rows)
    print("\n=== AGGREGATE ===")
    print(f"gross $ (pre-cost): {g:.0f}   net $ (post-cost): {nnet:.0f}   "
          f"cost $: {c:.0f}")
    if g < 0:
        print("VERDICT: SIGNAL PROBLEM — gross is negative before any costs. "
              "Direction/timing loses; costs are secondary.")
    elif nnet < 0:
        print("VERDICT: COST/SIZING PROBLEM — gross positive but costs flip it "
              "negative. The raw signal has edge; fees/slippage/sizing eat it.")
    else:
        print("VERDICT: net positive in aggregate — re-examine per-symbol.")
    print("NOTE: pre-#223/#224 absolutes inflated (#5). Read direction, not magnitude.")


if __name__ == "__main__":
    _print(diagnose())
