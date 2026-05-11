#!/usr/bin/env python3
"""Q2 Grid Topology Diagnostic — pre-expansion decision script (issue #318).

Purpose
-------
Determine whether expanding the ATR grid beyond its current 7 × 5 × 3 = 105
points is methodologically justified, BEFORE spending ~24h of compute on a
wider sweep.

Triggered by the 2026-05-11 A.4-1 sweep returning `NO_DATA` for all 10 symbols
and the subsequent external review (PR #316 comment chain) flagging that
expansion without diagnostic is speculative.

Falsifiable criterion (pre-registered in issue #318)
----------------------------------------------------
Expansion is justified ONLY IF >= 6 of 10 symbols satisfy BOTH:

  (a) top_candidates[0] of that symbol's grid sweep lands on a boundary of
      the grid (sl in {0.5, 2.5}, tp in {2.0, 6.0}, or be in {1.5, 2.5}), AND
  (b) The directional gradient from top_candidates[0] toward the grid
      boundary along that axis exceeds max(2 sigma of the symbol's P&L
      distribution across the 105 grid cells, $200 absolute over the
      12-month train horizon).

The $200 absolute anchor prevents false positives when all grid points sit
deep negative; the 2 sigma alternative captures the case where the
distribution is tight and a small gradient is still signal. AND-gate
between the two prevents both noise-on-flat-grid false positives and
high-threshold-on-truly-flat-grid false negatives.

Status
------
SKELETON — implementation pending. Committed now to ensure reproducibility
when implemented (any future session can extend without re-deriving the
spec). To implement: use auto_tune.run_backtest_with_params via the
cfg + symbol_overrides path (gates active), iterate the full grid per
symbol, emit JSON with per-symbol topology.

Output
------
JSON at data/retune/2026-05-11-pre-holdout-atr-evidence/grid_topology.json
with the following schema (per symbol):

  {
    "symbol": "BTCUSDT",
    "top_candidate": {"sl": 1.0, "tp": 4.0, "be": 1.5, "pnl": -250.50},
    "all_results": [
      {"sl": 0.5, "tp": 2.0, "be": 1.5, "pnl": -500.00},
      ...  # 105 entries total
    ],
    "neighbors_of_top": [  # the 8 grid-adjacent cells in (sl, tp, be) space
      {"sl": 0.7, "tp": 4.0, "be": 1.5, "pnl": -270.00, "axis": "sl-"},
      ...
    ],
    "gradients": {
      "sl_minus": -20.0,  # delta pnl moving sl one step lower
      "sl_plus":  +30.0,
      "tp_minus": +50.0,
      "tp_plus":  -10.0,
      "be_minus":  -5.0,
      "be_plus":  +15.0
    },
    "at_boundary": {
      "sl": false,
      "tp": false,
      "be": false
    },
    "criterion_a_met": false,  # any axis at boundary
    "criterion_b_per_axis": {  # gradient exceeds max(2sigma, $200) per axis
      "sl-": false, "sl+": false,
      "tp-": false, "tp+": true,
      "be-": false, "be+": false
    },
    "expansion_axis_candidates": [],  # axes where (a) AND (b) both hold
    "meets_combined_criterion": false  # this symbol contributes to count
  }

Aggregate at JSON top level:

  {
    "n_symbols_meeting_criterion": 0,
    "threshold_required": 6,
    "expansion_justified": false,
    "per_symbol": [...]
  }

Deliverables on completion (closure criteria for issue #318)
------------------------------------------------------------
1. This script committed and runnable end-to-end.
2. JSON output committed at
   data/retune/2026-05-11-pre-holdout-atr-evidence/grid_topology.json.
3. PR #316 comment with BOTH:
   (a) Mechanical mapping vs criterion (X of 10 symbols).
   (b) Bayesian update explicit: prior cited to
       docs/superpowers/specs/es/2026-04-17-formula-ganadora-resultados-finales.md
       (already discounted per CLAUDE.md as pre-#223/#224), magnitude of
       posterior shift after 1050 backtests on the post-fix simulator,
       reasoning of shift in 2-3 sentences.

References
----------
- Issue #318 (this script's authoritative spec).
- Issue #322 (A.4-3 hard block — depends on this script's output via Issue
  #321 for the stakeholder decision path).
- PR #316 (inflection-point spec).
- CLAUDE.md "Caveats heredados - A.4 (#250)" caveats #1, #5.
- auto_tune.py:177 run_backtest_with_params (cfg + symbol_overrides path).
- tools/retune_pre_holdout.py (parent harness, same grid + symbols).
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from datetime import datetime, timezone
from functools import partial
from multiprocessing import Pool, cpu_count
from pathlib import Path
from typing import Final

from dateutil.relativedelta import relativedelta

# Pre-registered criterion constants (issue #318). DO NOT TUNE without
# pre-registration amendment to issue #318.
BOUNDARY_SL: Final[tuple[float, float]] = (0.5, 2.5)
BOUNDARY_TP: Final[tuple[float, float]] = (2.0, 6.0)
BOUNDARY_BE: Final[tuple[float, float]] = (1.5, 2.5)
GRADIENT_ABSOLUTE_FLOOR_USD: Final[float] = 200.0
GRADIENT_SIGMA_MULTIPLIER: Final[float] = 2.0
SYMBOLS_THRESHOLD_FOR_EXPANSION: Final[int] = 6
TOTAL_SYMBOLS: Final[int] = 10
CUTOFF_ISO: Final[str] = "2025-04-30T00:00:00+00:00"

# Same grid as tools/retune_pre_holdout.py + auto_tune.py GRID. Locked.
GRID_SL: Final[tuple[float, ...]] = (0.5, 0.7, 1.0, 1.2, 1.5, 2.0, 2.5)
GRID_TP: Final[tuple[float, ...]] = (2.0, 3.0, 4.0, 5.0, 6.0)
GRID_BE: Final[tuple[float, ...]] = (1.5, 2.0, 2.5)

CURATED_SYMBOLS: Final[tuple[str, ...]] = (
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
    "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = (
    REPO_ROOT / "data" / "retune" / "2026-05-11-pre-holdout-atr-evidence"
    / "grid_topology.json"
)


def _process_symbol(symbol: str, sim_start, sim_end, cutoff, app_config) -> dict:
    """Run the 105-point grid for one symbol; compute topology + criterion.

    Pure worker — imports happen inside so multiprocessing spawns get fresh
    module state per child (matches the pattern in tools/retune_pre_holdout.py).
    """
    from auto_tune import run_backtest_with_params

    results: list[dict] = []
    for sl in GRID_SL:
        for tp in GRID_TP:
            for be in GRID_BE:
                combo = {
                    "atr_sl_mult": float(sl),
                    "atr_tp_mult": float(tp),
                    "atr_be_mult": float(be),
                }
                try:
                    _trades, metrics = run_backtest_with_params(
                        symbol, combo, sim_start, sim_end,
                        cutoff=cutoff, app_config=app_config,
                    )
                    pnl = float(metrics.get("net_pnl", 0.0))
                    n_trades = int(metrics.get("total_trades", 0))
                    bankruptcy_count = int(metrics.get("bankruptcy_count", 0))
                except Exception as exc:
                    pnl = 0.0
                    n_trades = 0
                    bankruptcy_count = 0
                    sys.stderr.write(
                        f"[q2_diag] {symbol} {combo}: exception "
                        f"{type(exc).__name__}: {exc}\n"
                    )
                results.append({
                    "sl": float(sl), "tp": float(tp), "be": float(be),
                    "pnl": pnl, "trades": n_trades,
                    "bankruptcy_count": bankruptcy_count,
                })

    # Find top candidate (max P&L)
    top = max(results, key=lambda r: r["pnl"])

    # σ of P&L distribution across the 105 grid cells
    pnls = [r["pnl"] for r in results]
    sigma = statistics.stdev(pnls) if len(pnls) > 1 else 0.0
    threshold = max(GRADIENT_SIGMA_MULTIPLIER * sigma, GRADIENT_ABSOLUTE_FLOOR_USD)

    # Boundary checks per axis
    at_boundary_sl = top["sl"] in BOUNDARY_SL
    at_boundary_tp = top["tp"] in BOUNDARY_TP
    at_boundary_be = top["be"] in BOUNDARY_BE

    def _pnl_at(sl: float, tp: float, be: float) -> float | None:
        for r in results:
            if r["sl"] == sl and r["tp"] == tp and r["be"] == be:
                return r["pnl"]
        return None

    gradients: dict[str, float] = {}
    expansion_axis_candidates: list[str] = []

    # When top is at a boundary, the "interior neighbor" is one grid step
    # inward; gradient_toward_boundary = pnl(top) - pnl(interior_neighbor).
    # Positive gradient → P&L improves moving toward the boundary → optimum
    # may lie past it (expansion justified along that axis).

    if at_boundary_sl:
        if top["sl"] == BOUNDARY_SL[0]:
            neighbor_sl, axis_label = GRID_SL[1], "sl_minus"
        else:
            neighbor_sl, axis_label = GRID_SL[-2], "sl_plus"
        n_pnl = _pnl_at(neighbor_sl, top["tp"], top["be"])
        if n_pnl is not None:
            grad = top["pnl"] - n_pnl
            gradients[axis_label] = grad
            if grad > threshold:
                expansion_axis_candidates.append(axis_label)

    if at_boundary_tp:
        if top["tp"] == BOUNDARY_TP[0]:
            neighbor_tp, axis_label = GRID_TP[1], "tp_minus"
        else:
            neighbor_tp, axis_label = GRID_TP[-2], "tp_plus"
        n_pnl = _pnl_at(top["sl"], neighbor_tp, top["be"])
        if n_pnl is not None:
            grad = top["pnl"] - n_pnl
            gradients[axis_label] = grad
            if grad > threshold:
                expansion_axis_candidates.append(axis_label)

    if at_boundary_be:
        if top["be"] == BOUNDARY_BE[0]:
            neighbor_be, axis_label = GRID_BE[1], "be_minus"
        else:
            neighbor_be, axis_label = GRID_BE[-2], "be_plus"
        n_pnl = _pnl_at(top["sl"], top["tp"], neighbor_be)
        if n_pnl is not None:
            grad = top["pnl"] - n_pnl
            gradients[axis_label] = grad
            if grad > threshold:
                expansion_axis_candidates.append(axis_label)

    criterion_a_met = at_boundary_sl or at_boundary_tp or at_boundary_be
    criterion_b_met = len(expansion_axis_candidates) > 0
    meets_combined = criterion_a_met and criterion_b_met

    return {
        "symbol": symbol,
        "top_candidate": top,
        "n_grid_points": len(results),
        "sigma_pnl_usd": round(sigma, 2),
        "threshold_used_usd": round(threshold, 2),
        "at_boundary": {
            "sl": at_boundary_sl, "tp": at_boundary_tp, "be": at_boundary_be,
        },
        "gradients_toward_boundary": {k: round(v, 2) for k, v in gradients.items()},
        "expansion_axis_candidates": expansion_axis_candidates,
        "criterion_a_met": criterion_a_met,
        "criterion_b_met": criterion_b_met,
        "meets_combined_criterion": meets_combined,
        "all_results": results,
    }


def main() -> int:
    cutoff = datetime(2025, 4, 30, tzinfo=timezone.utc)
    train_start = cutoff - relativedelta(months=15)  # 2024-01-30
    train_end = cutoff - relativedelta(months=3)     # 2025-01-30

    with open(REPO_ROOT / "config.defaults.json") as f:
        app_config = json.load(f)

    workers = min(len(CURATED_SYMBOLS), cpu_count())
    n_combos = len(GRID_SL) * len(GRID_TP) * len(GRID_BE)

    sys.stderr.write(f"[q2_diag] Cutoff: {cutoff.isoformat()}\n")
    sys.stderr.write(f"[q2_diag] Train: {train_start.date()} → {train_end.date()}\n")
    sys.stderr.write(
        f"[q2_diag] {len(CURATED_SYMBOLS)} symbols × {n_combos} combos = "
        f"{len(CURATED_SYMBOLS) * n_combos} backtests; {workers} workers\n"
    )

    fn = partial(
        _process_symbol,
        sim_start=train_start, sim_end=train_end,
        cutoff=cutoff, app_config=app_config,
    )
    with Pool(workers) as pool:
        per_symbol = pool.map(fn, list(CURATED_SYMBOLS))

    n_meeting = sum(1 for s in per_symbol if s["meets_combined_criterion"])
    expansion_justified = n_meeting >= SYMBOLS_THRESHOLD_FOR_EXPANSION

    output = {
        "harness": "tools.q2_grid_topology_diag",
        "spec_ref": "issue #318",
        "ran_at_iso": datetime.now(timezone.utc).isoformat(),
        "cutoff_iso": cutoff.isoformat(),
        "train_window_iso": [train_start.isoformat(), train_end.isoformat()],
        "grid": {
            "sl": list(GRID_SL), "tp": list(GRID_TP), "be": list(GRID_BE),
            "total_points_per_symbol": n_combos,
        },
        "criterion": {
            "sigma_multiplier": GRADIENT_SIGMA_MULTIPLIER,
            "absolute_floor_usd": GRADIENT_ABSOLUTE_FLOOR_USD,
            "boundary_sl": list(BOUNDARY_SL),
            "boundary_tp": list(BOUNDARY_TP),
            "boundary_be": list(BOUNDARY_BE),
            "symbols_threshold_for_expansion": SYMBOLS_THRESHOLD_FOR_EXPANSION,
            "total_symbols": TOTAL_SYMBOLS,
        },
        "n_symbols_meeting_criterion": n_meeting,
        "expansion_justified": expansion_justified,
        "per_symbol": per_symbol,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2, sort_keys=True)

    sys.stderr.write(f"\n[q2_diag] Output: {OUTPUT_PATH}\n")
    sys.stderr.write(
        f"[q2_diag] Symbols meeting AND criterion: "
        f"{n_meeting}/{TOTAL_SYMBOLS} (threshold: "
        f"{SYMBOLS_THRESHOLD_FOR_EXPANSION})\n"
    )
    sys.stderr.write(
        f"[q2_diag] Decision: "
        f"{'EXPANSION JUSTIFIED' if expansion_justified else 'NO_DATA ACCEPTED'}\n"
    )

    print("\n=== Q2 Grid Topology Diagnostic Summary ===")
    print(f"Cutoff: {cutoff.date()} | Train: {train_start.date()} → {train_end.date()}")
    print(
        f"Grid: {len(GRID_SL)} × {len(GRID_TP)} × {len(GRID_BE)} "
        f"= {n_combos} combos per symbol"
    )
    print()
    header = (
        f"{'Symbol':<12} {'Top P&L':>10} {'Top combo':>22} "
        f"{'Bound':>7} {'σ':>9} {'Thresh':>9} {'A':>3} {'B':>3} {'Met':>5}"
    )
    print(header)
    print("-" * len(header))
    for s in per_symbol:
        t = s["top_candidate"]
        combo_str = f"({t['sl']}, {t['tp']}, {t['be']})"
        bound_str = (
            ("sl " if s["at_boundary"]["sl"] else "")
            + ("tp " if s["at_boundary"]["tp"] else "")
            + ("be" if s["at_boundary"]["be"] else "")
        ).strip() or "·"
        print(
            f"{s['symbol']:<12} {t['pnl']:>10.2f} {combo_str:>22} "
            f"{bound_str:>7} {s['sigma_pnl_usd']:>9.2f} {s['threshold_used_usd']:>9.2f} "
            f"{('✓' if s['criterion_a_met'] else '·'):>3} "
            f"{('✓' if s['criterion_b_met'] else '·'):>3} "
            f"{('✓' if s['meets_combined_criterion'] else '·'):>5}"
        )
    print()
    print(f"Symbols meeting AND criterion: {n_meeting} / {TOTAL_SYMBOLS}")
    print(f"Threshold for expansion: {SYMBOLS_THRESHOLD_FOR_EXPANSION}")
    print(
        f"Decision: "
        f"{'EXPANSION JUSTIFIED' if expansion_justified else 'NO_DATA ACCEPTED'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
