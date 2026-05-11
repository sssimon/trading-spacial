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

import os
import sys
from typing import Final

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


def main() -> int:
    """Entry point. Implementation pending.

    When implemented, will:
    1. Load the 10 curated symbols + their OHLCV up to CUTOFF_ISO.
    2. For each symbol, iterate the 105-point grid via
       auto_tune.run_backtest_with_params(..., cutoff=..., app_config=...).
    3. Compute neighbor gradients around top_candidates[0] per symbol.
    4. Apply the pre-registered (a) AND (b) criterion per symbol.
    5. Aggregate: count symbols meeting both. Compare against threshold.
    6. Emit JSON output.
    7. Print mechanical mapping summary (for the PR comment).

    Output absolute dollar values, not percentages — the $200 anchor is
    in USD over the 12-month train horizon.
    """
    sys.stderr.write(
        "[q2_grid_topology_diag] SKELETON — implementation pending.\n"
        "See module docstring + issue #318 for the deliverable spec.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
