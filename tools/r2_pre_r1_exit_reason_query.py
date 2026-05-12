#!/usr/bin/env python3
"""Pre-R1 exit reason distribution query.

Per derivation_audit.md §11.1 + operator review 2026-05-12: gate before R1
commitment. Extracts {SL, TP, TIME_LIMIT, BANKRUPT} distribution per
currently-bankrupt symbol under current gates on the A.4-1 train window.

Decision tree:
  - TIME_LIMIT% dominant (>40%) → R1 mechanically plausible (proceed)
  - SL% dominant (>60%)         → R1 cannot help (skip to R3 or H5 escalation)
  - Mixed                       → R1 with stricter criterion required

Output (in data/retune/2026-05-11-r2-gates/):
  - pre_r1_exit_reasons.json  # per-symbol exit_reason counts + verdict

Compute: 8 backtests in parallel (one per currently-bankrupt symbol). Each
~2-3 minutes on full A.4-1 train window.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from functools import partial
from multiprocessing import Pool, cpu_count
from pathlib import Path
from typing import Final

from dateutil.relativedelta import relativedelta

# Ensure repo root is on sys.path so workers can import auto_tune.
REPO_ROOT_FOR_IMPORT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT_FOR_IMPORT))

CURRENTLY_BANKRUPT_SYMBOLS: Final = (
    "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "UNIUSDT", "XLMUSDT",
    "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = (
    REPO_ROOT / "data" / "retune" / "2026-05-11-r2-gates"
    / "pre_r1_exit_reasons.json"
)


def _query_symbol(symbol: str, sim_start, sim_end, cutoff, app_config) -> dict:
    """Run 1 backtest with current gates; extract exit_reason distribution."""
    # Worker-side sys.path adjustment for multiprocessing spawned processes.
    import sys as _sys
    from pathlib import Path as _Path
    _worker_repo_root = _Path(__file__).resolve().parent.parent
    if str(_worker_repo_root) not in _sys.path:
        _sys.path.insert(0, str(_worker_repo_root))
    from auto_tune import run_backtest_with_params

    # Use current ATR multipliers per config.defaults.json:symbol_overrides
    overrides = app_config.get("symbol_overrides", {}).get(symbol, {})
    params = {
        "atr_sl_mult": overrides.get("atr_sl_mult", 1.0),
        "atr_tp_mult": overrides.get("atr_tp_mult", 4.0),
        "atr_be_mult": overrides.get("atr_be_mult", 1.5),
    }

    try:
        trades, metrics = run_backtest_with_params(
            symbol, params, sim_start, sim_end,
            cutoff=cutoff, app_config=app_config,
        )
    except Exception as exc:
        return {
            "symbol": symbol,
            "error": f"{type(exc).__name__}: {exc}",
            "params": params,
        }

    # Aggregate exit_reason counts
    reasons = Counter()
    for t in trades:
        reasons[t.get("exit_reason", "UNKNOWN")] += 1
    total = sum(reasons.values())
    pct = {
        k: round(100.0 * v / total, 2) if total else 0.0
        for k, v in reasons.items()
    }

    return {
        "symbol": symbol,
        "params": params,
        "total_trades": metrics.get("total_trades", total),
        "exit_reason_counts": dict(reasons),
        "exit_reason_pct": pct,
        "bankruptcy_count": metrics.get("bankruptcy_count", 0),
        "net_pnl": round(float(metrics.get("net_pnl", 0.0)), 2),
    }


def classify_verdict(results: list[dict]) -> dict:
    """Aggregate exit_reason distribution across all 8 symbols → R1 viability verdict."""
    total_counts: Counter = Counter()
    for r in results:
        if "error" in r:
            continue
        for k, v in r.get("exit_reason_counts", {}).items():
            total_counts[k] += v
    total = sum(total_counts.values())
    pct = {
        k: round(100.0 * v / total, 2) if total else 0.0
        for k, v in total_counts.items()
    }

    tl_pct = pct.get("TIME_LIMIT", 0.0)
    sl_pct = pct.get("SL", 0.0)
    tp_pct = pct.get("TP", 0.0)
    bk_pct = pct.get("BANKRUPT", 0.0)

    if tl_pct > 40:
        verdict = "R1_PLAUSIBLE"
        recommendation = (
            "TIME_LIMIT exits dominate. Dynamic exits (trailing stop, "
            "signal-reversal) could mechanistically compete. Proceed to R1 pre-reg."
        )
    elif sl_pct > 60:
        verdict = "R1_INVIABLE_SKIP_TO_R3"
        recommendation = (
            "SL exits dominate. Trades close at SL before any dynamic-exit logic "
            "could engage. R1 (dynamic exit) cannot rescue. Skip to R3 (signal "
            "alternative) or escalate to H5 (basket re-validation) per audit §A.4."
        )
    else:
        verdict = "R1_STRICTER_CRITERION_REQUIRED"
        recommendation = (
            "Mixed exit distribution. R1 mechanism unclear. If proceeding to R1, "
            "require stricter success criterion: Δexit_reason distribution change "
            "+ Δbankruptcy_rate, not just Δtrade_count."
        )

    return {
        "aggregate_counts": dict(total_counts),
        "aggregate_pct": pct,
        "dominant_exit": max(pct, key=pct.get) if pct else None,
        "verdict": verdict,
        "recommendation": recommendation,
    }


def main() -> int:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with open(REPO_ROOT / "config.defaults.json") as f:
        app_config = json.load(f)

    # A.4-1 train window (same as audit reference)
    cutoff = datetime(2025, 4, 30, tzinfo=timezone.utc)
    sim_start = cutoff - relativedelta(months=15)  # 2024-01-30
    sim_end = cutoff - relativedelta(months=3)     # 2025-01-30

    print("=== Pre-R1 exit reason distribution query ===")
    print(f"Cutoff:  {cutoff.isoformat()}")
    print(f"Window:  {sim_start.date()} → {sim_end.date()}")
    print(f"Symbols: {len(CURRENTLY_BANKRUPT_SYMBOLS)} currently-bankrupt")
    print()

    workers = min(len(CURRENTLY_BANKRUPT_SYMBOLS), cpu_count())
    print(f"Running {len(CURRENTLY_BANKRUPT_SYMBOLS)} backtests with {workers} workers...")

    fn = partial(
        _query_symbol,
        sim_start=sim_start, sim_end=sim_end,
        cutoff=cutoff, app_config=app_config,
    )
    with Pool(workers) as pool:
        results = pool.map(fn, list(CURRENTLY_BANKRUPT_SYMBOLS))

    print()
    print("=== Per-symbol exit reason distribution ===")
    print(f"{'symbol':<12} {'n_trades':>9} {'SL%':>6} {'TP%':>6} {'TIME_LIMIT%':>12} {'BANKRUPT%':>10} {'OPEN%':>6}")
    for r in results:
        if "error" in r:
            print(f"  {r['symbol']:<12} ERROR: {r['error']}")
            continue
        pct = r["exit_reason_pct"]
        print(f"  {r['symbol']:<12} {r['total_trades']:>9} "
              f"{pct.get('SL', 0):>5.1f}% "
              f"{pct.get('TP', 0):>5.1f}% "
              f"{pct.get('TIME_LIMIT', 0):>11.1f}% "
              f"{pct.get('BANKRUPT', 0):>9.1f}% "
              f"{pct.get('OPEN', 0):>5.1f}%")

    verdict_block = classify_verdict(results)

    print()
    print("=== Aggregate across 8 currently-bankrupt symbols ===")
    pct = verdict_block["aggregate_pct"]
    for k, v in sorted(pct.items(), key=lambda x: -x[1]):
        print(f"  {k:<14} {v:>6.2f}%")
    print()
    print(f"Dominant exit:   {verdict_block['dominant_exit']}")
    print(f"Verdict:         {verdict_block['verdict']}")
    print(f"Recommendation:  {verdict_block['recommendation']}")

    # Persist
    output = {
        "harness": "tools.r2_pre_r1_exit_reason_query",
        "spec_ref": "data/retune/2026-05-11-r2-gates/derivation_audit.md §11.1",
        "ran_at_iso": datetime.now(timezone.utc).isoformat(),
        "cutoff_iso": cutoff.isoformat(),
        "window_iso": [sim_start.isoformat(), sim_end.isoformat()],
        "per_symbol": results,
        "aggregate": verdict_block,
    }
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2, sort_keys=True)
    print()
    print(f"Output: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
