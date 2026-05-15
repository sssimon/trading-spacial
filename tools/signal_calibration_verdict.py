#!/usr/bin/env python3
"""Signal calibration verdict calculator (epic C Phase 1, module 2 of 4).

Pre-reg: docs/superpowers/plans/2026-05-15-signal-calibration-pre-reg.md

Implements verdict logic for Phase 2 (§4.1), Phase 3 (§4.2), and Phase 4 (§4.3)
of epic C signal calibration. Mirrors the asymmetric halt-guard pattern (§4.6)
from #338 Phase 3 verdict (`tools/regime_allocation_verdict.py`).

## Phase 2 verdict (§4.1)
  Diagnostic step over equal-weight Donchian-9 baseline in Window A:
    A_DETECTED:                 counts_A ∧ magnitudes_A in ≥ 6/8 símbolos
    B_DETECTED:                 ¬counts_A ∧ ¬magnitudes_A in ≥ 6/8 símbolos
    AMBIGUOUS:                  mixed evidence (else clause; includes 4/8 split)
    PHASE_2_INSUFFICIENT_DATA:  Phase 2 sweep halted (e.g., universal bankruptcy)

## Phase 3 verdict (§4.2)
  After A1 intervention (Q-PR6 lock: subset {5, 10, 20}) over Window A:
    PHASE_3_A_PASS:               ≥ 6/8 primary PASS ∧ ≥ 3/4 sensitivity PASS
    PHASE_3_A_PASS_CONDITIONAL:   ≥ 6/8 ∧ 2/4 sensitivity PASS
    SWEET_SPOT_ARTIFACT:          ≥ 6/8 ∧ ≤ 1/4 sensitivity (calibration overfit)
    A_INTERVENTION_INSUFFICIENT:  < 6/8 ∧ no halt
    A_INTERVENTION_HARMFUL:       H-C3 (bankruptcy) ∨ H-C4 (win_rate degraded)
    PHASE_3_INSUFFICIENT_DATA:    halt ∧ favorable naive (§4.6 guard)

## Phase 4 verdict (§4.3)
  Walk-forward over Windows B + C (conjunctive PASS):
    PHASE_4_A_PASS_STRONG:        B PASS ∧ C PASS ∧ sensitivity 3-4/4 in both
    PHASE_4_A_PASS_ROBUST:        B PASS ∧ C PASS ∧ sensitivity 2/4 in both
    PHASE_4_A_PASS_PARTIAL:       B XOR C PASS (only one window passes)
    PHASE_4_A_FAIL_CLEAN:         neither window PASS
    PHASE_4_INSUFFICIENT_DATA:    halt ∧ favorable naive ∧ partial (§4.6 guard)

## §4.6 asymmetric halt-guard
  Favorable naive verdicts are overridden to PHASE_X_INSUFFICIENT_DATA under
  halt. Negative naive verdicts are preserved. Phase 2/3 are single-window
  (halt alone = partial). Phase 4 additionally requires partial windows
  (n_windows_available < n_windows_expected).

## Thresholds (Q-PR locks)
  T_COUNTS_FIRING         = 5     (Q-PR1)
  P50_MAGNITUDE_MAX       = 2.0   (Q-PR2, strict `<`)
  WIN_RATE_FLOOR          = 0.30  (Q-PR3)
  WIN_RATE_DEGRADATION    = 0.50  (Q-PR4, intervention < 50% of baseline)
  AGGREGATE_MATCH_FRACTION = 0.75 (≥ 75% símbolos satisfy criterion)

This module exposes the verdict classifiers as pure functions for unit testing
and for orchestration by tools/signal_calibration_diagnostic.py (Phase 2 runner)
and tools/signal_calibration_sweep.py (Phase 3 + Phase 4 runner). A CLI main
is also provided for post-hoc verdict computation over sweep outputs.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Final

# ─────────────────────────────────────────────────────────────────────────────
# Constants — Q-PR locks (pre-reg §8)
# ─────────────────────────────────────────────────────────────────────────────

T_COUNTS_FIRING: Final[int] = 5
P50_MAGNITUDE_MAX: Final[float] = 2.0
WIN_RATE_FLOOR: Final[float] = 0.30
WIN_RATE_DEGRADATION: Final[float] = 0.50
AGGREGATE_MATCH_FRACTION: Final[float] = 0.75

SHORT_LOOKBACKS_FOR_COUNTS: Final[tuple[int, ...]] = (5, 10, 20)

# Pre-reg §10.4 halt thresholds
H_C3_BANKRUPTCY_THRESHOLD: Final[int] = 1
H_C4_DEGRADED_CELL_COUNT: Final[int] = 4

# Pre-reg §4.6 — favorable verdicts overridable under halt
FAVORABLE_VERDICTS_OVERRIDABLE: Final[frozenset[str]] = frozenset({
    "A_DETECTED",
    "PHASE_3_A_PASS",
    "PHASE_3_A_PASS_CONDITIONAL",
    "PHASE_4_A_PASS_STRONG",
    "PHASE_4_A_PASS_ROBUST",
    "PHASE_4_A_PASS_PARTIAL",
})

# Output dir for verdict.json artifact (when invoked via CLI main)
REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
OUTPUT_DIR: Final[Path] = (
    REPO_ROOT / "data" / "retune" / "2026-05-15-signal-calibration"
)


def _aggregate_match_threshold(in_coverage_count: int) -> int:
    """`ceil(AGGREGATE_MATCH_FRACTION × in_coverage_count)`, minimum 1.

    Example: 8 in-coverage → 6; 9 in-coverage → 7. Matches pre-reg §4.3
    coverage table thresholds (B≥6, C≥7).
    """
    return max(1, math.ceil(AGGREGATE_MATCH_FRACTION * in_coverage_count))


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 verdict (§4.1)
# ─────────────────────────────────────────────────────────────────────────────


def _counts_a_evidence_per_cell(cell: dict) -> bool:
    """Pre-reg §4.1 + Q-PR1: firing_count(N) ≥ T for N ∈ {5, 10, 20}, conjunctive."""
    per_lookback = cell.get("per_lookback", {})
    for n in SHORT_LOOKBACKS_FOR_COUNTS:
        if per_lookback.get(n, {}).get("firing_count", 0) < T_COUNTS_FIRING:
            return False
    return True


def _magnitudes_a_evidence_per_cell(cell: dict) -> bool:
    """Pre-reg §4.1 + Q-PR2: p50(|sum|) < 2 over window (strict `<`)."""
    p50 = cell.get("sum_distribution", {}).get("p50", float("inf"))
    return p50 < P50_MAGNITUDE_MAX


def classify_phase2_verdict(
    observability_cells: list[dict],
    *,
    in_coverage_count: int,
    halt: bool,
) -> dict:
    """Phase 2 diagnostic verdict (pre-reg §4.1).

    Args:
        observability_cells: list of per-symbol observability dicts. Each must
            have keys `symbol`, `per_lookback` (with `firing_count` per N), and
            `sum_distribution` (with `p50`).
        in_coverage_count: total in-coverage symbols expected (e.g., 8 for Window A).
        halt: True if Phase 2 sweep halted before completion.

    Returns dict with `verdict`, `naive_verdict`, `n_symbols_a_evidence`,
    `n_symbols_no_a_evidence`, `n_symbols_total`, `threshold`, `halt`,
    `halt_guard_applied`.
    """
    if halt:
        return {
            "verdict": "PHASE_2_INSUFFICIENT_DATA",
            "naive_verdict": None,
            "n_symbols_a_evidence": 0,
            "n_symbols_no_a_evidence": 0,
            "n_symbols_total": len(observability_cells),
            "threshold": _aggregate_match_threshold(in_coverage_count),
            "halt": True,
            "halt_guard_applied": True,
        }

    threshold = _aggregate_match_threshold(in_coverage_count)

    n_a = 0
    n_not_a = 0
    for cell in observability_cells:
        counts_a = _counts_a_evidence_per_cell(cell)
        mag_a = _magnitudes_a_evidence_per_cell(cell)
        if counts_a and mag_a:
            n_a += 1
        elif (not counts_a) and (not mag_a):
            n_not_a += 1

    if n_a >= threshold:
        naive = "A_DETECTED"
    elif n_not_a >= threshold:
        naive = "B_DETECTED"
    else:
        naive = "AMBIGUOUS"

    return {
        "verdict": naive,
        "naive_verdict": naive,
        "n_symbols_a_evidence": n_a,
        "n_symbols_no_a_evidence": n_not_a,
        "n_symbols_total": len(observability_cells),
        "threshold": threshold,
        "halt": False,
        "halt_guard_applied": False,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 verdict (§4.2 + H-C3/H-C4 from §10.4)
# ─────────────────────────────────────────────────────────────────────────────


def _primary_cell_passes(cell: dict) -> bool:
    """Pre-reg §4.2 PRIMARY conditions per cell:
      n_trades ≥ 5 ∧ no bankruptcies ∧ win_rate ≥ 30% (Q-PR3).
    """
    return (
        cell.get("n_trades", 0) >= 5
        and cell.get("bankruptcy_count", 0) == 0
        and cell.get("win_rate", 0.0) >= WIN_RATE_FLOOR
    )


def _check_h_c3(primary_cells: list[dict]) -> int:
    """Pre-reg §10.4 H-C3: zero-tolerance bankruptcy. Returns count of bankrupt cells."""
    return sum(1 for c in primary_cells if c.get("bankruptcy_count", 0) > 0)


def _check_h_c4(
    primary_cells: list[dict],
    baseline_win_rates: dict[str, float],
) -> int:
    """Pre-reg §10.4 H-C4 + Q-PR4: count of cells where intervention win_rate < 50% of baseline."""
    n = 0
    for cell in primary_cells:
        baseline = baseline_win_rates.get(cell.get("symbol", ""), 0.0)
        if baseline > 0 and cell.get("win_rate", 0.0) < WIN_RATE_DEGRADATION * baseline:
            n += 1
    return n


def _count_sensitivity_vol_target_pass(
    sensitivity_cells: list[dict],
    in_coverage_count: int,
) -> int:
    """For each vol_target, count if ≥ 75% cells PASS primary; return count out of 4."""
    threshold = _aggregate_match_threshold(in_coverage_count)
    by_vt: dict[float, list[dict]] = {}
    for cell in sensitivity_cells:
        vt = cell.get("vol_target", 0.0)
        by_vt.setdefault(vt, []).append(cell)

    n_pass = 0
    for cells in by_vt.values():
        n_cells_pass = sum(1 for c in cells if _primary_cell_passes(c))
        if n_cells_pass >= threshold:
            n_pass += 1
    return n_pass


def classify_phase3_verdict(
    primary_cells: list[dict],
    sensitivity_cells: list[dict],
    *,
    baseline_win_rates: dict[str, float],
    in_coverage_count: int,
    halt: bool,
) -> dict:
    """Phase 3 verdict after A1 intervention (pre-reg §4.2).

    Order of checks:
      1. H-C3 (bankruptcy in primary) → A_INTERVENTION_HARMFUL
      2. H-C4 (win_rate degradation ≥ 4 cells) → A_INTERVENTION_HARMFUL
      3. External halt → asymmetric guard
      4. Standard verdict logic (primary ≥ 6/8 + sensitivity tier)

    Args:
        primary_cells: in-coverage cells × Window A × vol_target=0.30.
        sensitivity_cells: in-coverage × Window A × 4 vol_target.
        baseline_win_rates: `{symbol → equal-weight baseline win_rate}` from Phase 2.
        in_coverage_count: expected coverage (e.g., 8 for Window A).
        halt: external halt signal.
    """
    n_bankrupt = _check_h_c3(primary_cells)
    if n_bankrupt >= H_C3_BANKRUPTCY_THRESHOLD:
        return {
            "verdict": "A_INTERVENTION_HARMFUL",
            "naive_verdict": "A_INTERVENTION_HARMFUL",
            "halt_trigger": "H_C3_bankruptcy",
            "halt_guard_applied": False,
            "n_bankrupt_cells": n_bankrupt,
            "n_primary_pass": 0,
            "n_sensitivity_vol_target_pass": 0,
            "halt": halt,
        }

    n_degraded = _check_h_c4(primary_cells, baseline_win_rates)
    if n_degraded >= H_C4_DEGRADED_CELL_COUNT:
        return {
            "verdict": "A_INTERVENTION_HARMFUL",
            "naive_verdict": "A_INTERVENTION_HARMFUL",
            "halt_trigger": "H_C4_win_rate_degradation",
            "halt_guard_applied": False,
            "n_degraded_cells": n_degraded,
            "n_primary_pass": 0,
            "n_sensitivity_vol_target_pass": 0,
            "halt": halt,
        }

    threshold = _aggregate_match_threshold(in_coverage_count)
    n_pass = sum(1 for c in primary_cells if _primary_cell_passes(c))
    n_sens_pass = _count_sensitivity_vol_target_pass(sensitivity_cells, in_coverage_count)

    if n_pass >= threshold:
        if n_sens_pass >= 3:
            naive = "PHASE_3_A_PASS"
        elif n_sens_pass == 2:
            naive = "PHASE_3_A_PASS_CONDITIONAL"
        else:
            naive = "SWEET_SPOT_ARTIFACT"
    else:
        naive = "A_INTERVENTION_INSUFFICIENT"

    final_verdict = apply_asymmetric_halt_guard(
        naive_verdict=naive,
        halt=halt,
        n_windows_available=1,
        n_windows_expected=1,
        phase=3,
    )

    return {
        "verdict": final_verdict,
        "naive_verdict": naive,
        "halt_guard_applied": final_verdict != naive,
        "halt_trigger": None,
        "n_primary_pass": n_pass,
        "n_sensitivity_vol_target_pass": n_sens_pass,
        "threshold": threshold,
        "halt": halt,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 verdict (§4.3)
# ─────────────────────────────────────────────────────────────────────────────


def _window_pass(summary: dict) -> bool:
    """Pre-reg §4.3 — single-window PASS: `n_pass / n_in_coverage ≥ 0.75`."""
    n_in_cov = summary.get("n_in_coverage", 0)
    if n_in_cov == 0:
        return False
    n_pass = summary.get("n_pass", 0)
    return (n_pass / n_in_cov) >= AGGREGATE_MATCH_FRACTION


def classify_phase4_verdict(
    *,
    window_summaries: dict[str, dict],
    sensitivity_per_window: dict[str, dict],
    halt: bool,
) -> dict:
    """Phase 4 walk-forward verdict over Windows B + C (pre-reg §4.3).

    Args:
        window_summaries: `{window_id → {"n_pass", "n_in_coverage"}}`. Keys B/C.
        sensitivity_per_window: `{window_id → {"n_pass_out_of_4", "n_available_out_of_4"}}`.
        halt: external halt signal.
    """
    available_windows = sorted(window_summaries.keys())
    n_windows_available = len(available_windows)
    n_windows_expected = 2  # B + C

    b_pass = "B" in window_summaries and _window_pass(window_summaries["B"])
    c_pass = "C" in window_summaries and _window_pass(window_summaries["C"])

    if b_pass and c_pass:
        b_sens = sensitivity_per_window.get("B", {}).get("n_pass_out_of_4", 0)
        c_sens = sensitivity_per_window.get("C", {}).get("n_pass_out_of_4", 0)
        if b_sens >= 3 and c_sens >= 3:
            naive = "PHASE_4_A_PASS_STRONG"
        elif b_sens >= 2 and c_sens >= 2:
            naive = "PHASE_4_A_PASS_ROBUST"
        else:
            naive = "PHASE_4_A_PASS_PARTIAL"
    elif b_pass or c_pass:
        naive = "PHASE_4_A_PASS_PARTIAL"
    else:
        naive = "PHASE_4_A_FAIL_CLEAN"

    final_verdict = apply_asymmetric_halt_guard(
        naive_verdict=naive,
        halt=halt,
        n_windows_available=n_windows_available,
        n_windows_expected=n_windows_expected,
        phase=4,
    )

    return {
        "verdict": final_verdict,
        "naive_verdict": naive,
        "halt_guard_applied": final_verdict != naive,
        "n_windows_available": n_windows_available,
        "n_windows_expected": n_windows_expected,
        "b_pass": b_pass,
        "c_pass": c_pass,
        "halt": halt,
    }


# ─────────────────────────────────────────────────────────────────────────────
# §4.6 asymmetric halt-guard
# ─────────────────────────────────────────────────────────────────────────────


def apply_asymmetric_halt_guard(
    *,
    naive_verdict: str,
    halt: bool,
    n_windows_available: int,
    n_windows_expected: int,
    phase: int,
) -> str:
    """Pre-reg §4.6 — favorable verdicts overridden under halt + partial windows.

    Phase 2/3 are single-window: halt alone triggers override on favorable naive.
    Phase 4 requires halt + `n_windows_available < n_windows_expected`.
    Negative verdicts are preserved in both cases.

    Args:
        naive_verdict: pre-guard verdict label.
        halt: external halt signal.
        n_windows_available: windows actually evaluated.
        n_windows_expected: windows expected at this phase.
        phase: 2, 3, or 4.
    """
    if not halt:
        return naive_verdict
    if naive_verdict not in FAVORABLE_VERDICTS_OVERRIDABLE:
        return naive_verdict
    if phase in (2, 3):
        return f"PHASE_{phase}_INSUFFICIENT_DATA"
    if phase == 4 and n_windows_available < n_windows_expected:
        return "PHASE_4_INSUFFICIENT_DATA"
    return naive_verdict


# ─────────────────────────────────────────────────────────────────────────────
# CLI main — verdict.json emitter from sweep outputs
# ─────────────────────────────────────────────────────────────────────────────


def _load_json(name: str):
    path = OUTPUT_DIR / name
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def main() -> int:
    """Compute verdict from sweep outputs in OUTPUT_DIR. Writes verdict.json.

    Exit codes:
      0 — verdict computed
      1 — required inputs missing
    """
    phase2_diag = _load_json("phase2_diagnostic.json")
    phase3_primary = _load_json("sweep_primary_A.json")
    phase3_sensitivity = _load_json("sweep_sensitivity_A.json")
    halt_diag = _load_json("halt_diagnostic.json")
    halt = bool(halt_diag.get("halt", False)) if isinstance(halt_diag, dict) else False

    out: dict = {
        "schema_version": 1,
        "spec_ref": "docs/superpowers/plans/2026-05-15-signal-calibration-pre-reg.md",
        "halt_fired": halt,
        "halt_diagnostic_loaded": halt_diag is not None,
    }

    if phase2_diag is not None and isinstance(phase2_diag, list):
        phase2_result = classify_phase2_verdict(
            phase2_diag, in_coverage_count=8, halt=halt,
        )
        out["phase2"] = phase2_result

    if phase3_primary is not None and isinstance(phase3_primary, list):
        phase3_result = classify_phase3_verdict(
            phase3_primary,
            phase3_sensitivity if isinstance(phase3_sensitivity, list) else [],
            baseline_win_rates={},
            in_coverage_count=8,
            halt=halt,
        )
        out["phase3"] = phase3_result

    if not out.get("phase2") and not out.get("phase3"):
        sys.stderr.write(
            "ERROR: no Phase 2 or Phase 3 sweep results found in "
            f"{OUTPUT_DIR}. Run tools/signal_calibration_diagnostic.py "
            "or tools/signal_calibration_sweep.py first.\n"
        )
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_DIR / "verdict.json", "w") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str, allow_nan=False)
    print(f"Wrote {OUTPUT_DIR / 'verdict.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
