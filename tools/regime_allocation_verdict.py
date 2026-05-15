#!/usr/bin/env python3
"""Regime-allocation verdict calculator (epic #338 Phase 3).

Pre-reg: docs/superpowers/plans/2026-05-14-regime-allocation-phase2-pre-reg.md

Reads the sweep + baseline + halt outputs written by
`tools/regime_allocation_sweep.py` and emits the §4 verdict per pre-reg §4 +
§4.2 + §4.3 + §4.6 (asymmetric halt-guard).

## Primary criterion (pre-reg §4, conjunctive 3/3)

Strategy total return > BTC B&H total return per sub-window
(portfolio aggregate of in-coverage symbols, net of v2 costs)

Phase 3 PASS = strict > in 3/3 sub-windows AT vol_target=30%.

## Sensitivity verdict map (pre-reg §4.2)

  4/4 vol_target pass → STRONG (advance Phase 4)
  3/4                  → ROBUST (advance Phase 4)
  2/4                  → SUCCESS_CONDITIONAL (operator §4.5)
  1/4                  → SWEET_SPOT_FAIL (overfit; archive)
  0/4                  → FAIL_CLEAN

## §4.3 verdict table outcomes

  STRONG_PASS                 — primary 3/3 + sensitivity 4/4
  ROBUST_PASS                 — primary 3/3 + sensitivity 3/4
  SUCCESS_CONDITIONAL         — primary 3/3 + sensitivity 2/4 (operator §4.5)
  SWEET_SPOT_FAIL             — primary 3/3 + sensitivity 1/4 (archived)
  PARTIAL_SUCCESS             — primary 2/3 (operator §4.5 default INCONCLUSIVE)
  INCONCLUSIVE                — primary ≤1/3 + sensitivity ≥1/4 (default FAIL clean)
  FAIL_CLEAN                  — primary ≤1/3 + sensitivity 0/4 + mechanism engaged
  FAIL_DEGENERATE             — ≥75% of in-coverage cells with n_trades<5 (single window)
  PHASE_3_INSUFFICIENT_DATA   — §4.6 asymmetric halt-guard override

## §4.6 asymmetric halt-guard

Under §10 halt + n_windows < 3, favorable verdicts (STRONG_PASS, ROBUST_PASS,
SUCCESS_CONDITIONAL, PARTIAL_SUCCESS) are overridden to
PHASE_3_INSUFFICIENT_DATA. Negative verdicts (FAIL_CLEAN, FAIL_DEGENERATE,
SWEET_SPOT_FAIL, INCONCLUSIVE) are preserved.

Operator override paths in §4.5 are NOT covered by this guard (§4.5
self-policing requirement applies separately).

## Inputs (OUTPUT_DIR)

  - sweep_primary_{A,B,C}.json
  - sweep_sensitivity_{A,B,C}.json
  - baseline_btc_bh_{A,B,C}.json
  - baseline_hubrich_{A,B,C}.json
  - baseline_lrc_archived_{A,B,C}.json
  - halt_diagnostic.json

## Outputs

  - verdict.json — machine-readable summary
  - stdout    — human-readable verdict tree + per-window summary

Usage: python tools/regime_allocation_verdict.py

Exit codes:
  0 — verdict computed
  1 — required inputs missing (no primary sweep results)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
OUTPUT_DIR: Final[Path] = (
    REPO_ROOT / "data" / "retune" / "2026-05-14-regime-allocation"
)

# Pre-reg §2.5 — primary vol_target locked at 30%.
PRIMARY_VOL_TARGET: Final[float] = 0.30
SENSITIVITY_VOL_TARGETS: Final[tuple[float, ...]] = (0.25, 0.30, 0.35, 0.40)

# Pre-reg §3 — coverage table (mirrors sweep tool).
COVERAGE_BY_WINDOW: Final[dict[str, tuple[str, ...]]] = {
    "A": (
        "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
        "UNIUSDT", "XLMUSDT", "RUNEUSDT",
    ),
    "B": (
        "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
        "UNIUSDT", "XLMUSDT", "RUNEUSDT",
    ),
    "C": (
        "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
        "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "RUNEUSDT",
    ),
}
ALL_SUB_WINDOWS: Final[tuple[str, ...]] = ("A", "B", "C")

# Pre-reg §4.1 — cell exclusion threshold (mirror sweep).
N_TRADES_MIN_FOR_ELIGIBILITY: Final[int] = 5

# Pre-reg §4.3 row 7 — per-window degenerate threshold.
#
# This constant is the PER-WINDOW threshold: a single sub-window's primary
# cell at vol_target=30% is considered "degenerate" when ≥75% of its
# in-coverage cells have n_trades < N_TRADES_MIN_FOR_ELIGIBILITY.
#
# The verdict-level FAIL_DEGENERATE classification (in _classify_verdict)
# additionally requires that this per-window flag hold for a PREDOMINANT
# share of available windows (≥majority of n_windows, computed as
# `max(1, n_windows // 2 + 1)`). Option A locked per BLOCK #3 review fix
# 2026-05-14: predominance prevents a single anomalous window from
# spuriously triggering FAIL_DEGENERATE.
#
# Operator note: pre-reg §4.3 row 7 wording ("≥75% of in-coverage símbolos
# n_trades<5") is per-window in spirit but verdict-level requires the
# additional predominance gate, locked here.
DEGENERATE_FRACTION_THRESHOLD: Final[float] = 0.75

# Pre-reg §4.2 — sensitivity verdict map.
SENSITIVITY_VERDICT_MAP: Final[dict[int, str]] = {
    4: "STRONG",
    3: "ROBUST",
    2: "SUCCESS_CONDITIONAL",
    1: "SWEET_SPOT_FAIL",
    0: "FAIL_CLEAN",
}

# Pre-reg §4.6 — verdicts overridden by asymmetric halt-guard under
# partial windows. Negative verdicts NOT in this set are preserved.
FAVORABLE_VERDICTS_OVERRIDABLE: Final[frozenset[str]] = frozenset({
    "STRONG_PASS",
    "ROBUST_PASS",
    "SUCCESS_CONDITIONAL",
    "PARTIAL_SUCCESS",
})


# ─────────────────────────────────────────────────────────────────────────────
# I/O helpers
# ─────────────────────────────────────────────────────────────────────────────


def _load_json(name: str):
    path = OUTPUT_DIR / name
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def _extract_halt_from_diagnostic(halt_diag) -> bool:
    """Extract halt bool from halt_diagnostic.json with defensive type check.

    Mirrors tools/r3_verdict.py:_extract_halt_from_diagnostic (#332 item 2).
    Defends against silent acceptance of truthy non-bool values that could
    flip verdict classification under §4.6 halt-guard.
    """
    if halt_diag is None:
        return False
    if not isinstance(halt_diag, dict):
        raise ValueError(
            f"halt_diag must be dict or None, got {type(halt_diag).__name__}: "
            f"{halt_diag!r}"
        )
    halt_value = halt_diag.get("halt", False)
    if not isinstance(halt_value, bool):
        raise ValueError(
            f"halt_diag['halt'] must be bool, got {type(halt_value).__name__}: "
            f"{halt_value!r}"
        )
    return halt_value


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation
# ─────────────────────────────────────────────────────────────────────────────


def _aggregate_window_at_vol_target(
    sweep_results: list[dict],
    window_id: str,
    vol_target: float,
) -> dict | None:
    """Sum per-symbol net_pnl over in-coverage symbols at a given vol_target.

    Returns None if no cells found (e.g., window not run due to halt).
    """
    in_coverage = set(COVERAGE_BY_WINDOW[window_id])
    cells = [
        r for r in sweep_results
        if r.get("sub_window") == window_id
        and abs(float(r.get("vol_target", 0.0)) - vol_target) < 1e-9
        and r.get("symbol") in in_coverage
    ]
    if not cells:
        return None
    total_pnl = sum(float(c.get("net_pnl_usd", 0.0)) for c in cells)
    n_trades_total = sum(int(c.get("n_trades", 0)) for c in cells)
    bankruptcy_count_total = sum(int(c.get("bankruptcy_count", 0)) for c in cells)
    insufficient_count = sum(
        1 for c in cells if c.get("insufficient_data", False)
    )
    # Pre-reg §4.1 — tie-break: (strategy_total_return, -btc_bh_total_return,
    # alphabetical_symbol). Applied to per-symbol details ordering only; the
    # window-level aggregate is a sum (associative).
    per_symbol = sorted(
        [
            {
                "symbol": c["symbol"],
                "net_pnl_usd": round(float(c.get("net_pnl_usd", 0.0)), 4),
                "n_trades": int(c.get("n_trades", 0)),
                "bankruptcy_count": int(c.get("bankruptcy_count", 0)),
                "insufficient_data": bool(c.get("insufficient_data", False)),
            }
            for c in cells
        ],
        key=lambda d: (-d["net_pnl_usd"], d["symbol"]),
    )
    return {
        "window_id": window_id,
        "vol_target": vol_target,
        "n_in_coverage": len(in_coverage),
        "strategy_total_return_usd": round(total_pnl, 4),
        "n_trades_total": n_trades_total,
        "bankruptcy_count_total": bankruptcy_count_total,
        "insufficient_data_count": insufficient_count,
        "per_symbol": per_symbol,
    }


def _load_btc_bh_baseline(window_id: str) -> dict:
    """Load BTC B&H baseline for window; raise FileNotFoundError if missing.

    Pre-reg §4 lockea BTC B&H as primary criterion comparator. Missing
    file means the methodology cannot be evaluated honestly — silent
    default to FAIL clean would hide misconfiguration. CHANGES_REQUESTED
    #4 review fix 2026-05-14: raise loud instead of returning None.
    """
    path = OUTPUT_DIR / f"baseline_btc_bh_{window_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Baseline file missing: {path}. Cannot evaluate primary "
            f"criterion for Window {window_id}. Re-run "
            f"tools/regime_allocation_sweep.py to generate baselines, "
            f"or run with --baselines-only."
        )
    with open(path) as f:
        return json.load(f)


def _load_hubrich_baseline(window_id: str) -> dict | None:
    """Load Hubrich baseline for window; warn if missing (informational).

    Hubrich is informational only (§5.2 epic baseline) — not part of the
    primary criterion comparison. Returns None if missing, with a stderr
    warning to surface the misconfiguration.
    """
    path = OUTPUT_DIR / f"baseline_hubrich_{window_id}.json"
    if not path.exists():
        sys.stderr.write(
            f"[verdict] WARNING: baseline_hubrich_{window_id}.json missing — "
            f"Hubrich comparison will be omitted from Window {window_id} "
            f"output (informational only; does not affect verdict).\n"
        )
        return None
    with open(path) as f:
        return json.load(f)


def _load_lrc_archived_baseline(window_id: str) -> list[dict] | None:
    """Load LRC archived baseline for window; warn if missing (informational).

    LRC archived is internal control benchmark (§5.4 epic) — not part of
    the primary criterion comparison. Returns None if missing.
    """
    path = OUTPUT_DIR / f"baseline_lrc_archived_{window_id}.json"
    if not path.exists():
        sys.stderr.write(
            f"[verdict] WARNING: baseline_lrc_archived_{window_id}.json "
            f"missing — LRC archived comparison will be omitted from "
            f"Window {window_id} output (informational only).\n"
        )
        return None
    with open(path) as f:
        return json.load(f)


def _lrc_archived_total_return(window_id: str) -> float | None:
    """Sum per-symbol LRC archived net_pnl over in-coverage."""
    data = _load_lrc_archived_baseline(window_id)
    if data is None:
        return None
    in_coverage = set(COVERAGE_BY_WINDOW[window_id])
    total = sum(
        float(r.get("net_pnl_usd", 0.0))
        for r in data if r.get("symbol") in in_coverage
    )
    return round(total, 4)


# ─────────────────────────────────────────────────────────────────────────────
# Primary criterion + sensitivity verdict
# ─────────────────────────────────────────────────────────────────────────────


def _primary_criterion_pass(
    strategy_total_return_usd: float,
    btc_bh_total_return_usd: float,
) -> bool:
    """Pre-reg §4 — strict `>` comparison. Ties FAIL by design.

    Tie-break per §4.1 (strategy_total_return, -btc_bh_total_return,
    alphabetical_symbol) does not affect this strict comparison — applies
    only to per-symbol detail ordering in the verdict output.
    """
    return strategy_total_return_usd > btc_bh_total_return_usd


def _compute_primary_per_window(
    primary_results: list[dict],
) -> dict[str, dict]:
    """For each sub-window in primary results, compute aggregate + comparison."""
    out: dict[str, dict] = {}
    for window_id in ALL_SUB_WINDOWS:
        agg = _aggregate_window_at_vol_target(
            primary_results, window_id, PRIMARY_VOL_TARGET,
        )
        if agg is None:
            continue
        # _load_btc_bh_baseline raises FileNotFoundError if missing
        # (CHANGES_REQUESTED #4) — primary criterion cannot be evaluated
        # without it.
        btc_bh = _load_btc_bh_baseline(window_id)
        hubrich = _load_hubrich_baseline(window_id)
        lrc_archived_total = _lrc_archived_total_return(window_id)

        btc_bh_total = float(btc_bh.get("total_return_usd", 0.0))
        hubrich_total = (
            float(hubrich.get("total_return_usd", 0.0)) if hubrich else None
        )

        pass_btc_bh = _primary_criterion_pass(
            agg["strategy_total_return_usd"], btc_bh_total
        )

        out[window_id] = {
            **agg,
            "btc_bh_total_return_usd": btc_bh_total,
            "hubrich_total_return_usd": hubrich_total,
            "lrc_archived_total_return_usd": lrc_archived_total,
            "primary_criterion_pass": pass_btc_bh,
        }
    return out


def _compute_sensitivity_per_vol_target(
    sensitivity_results: list[dict],
) -> dict[float, dict]:
    """For each vol_target in sensitivity sweep, evaluate primary criterion
    across 3/3 sub-windows.

    Returns {vol_target → {pass_count_3_3, pass_per_window, ...}}.
    """
    out: dict[float, dict] = {}
    for vt in SENSITIVITY_VOL_TARGETS:
        per_window: dict[str, dict] = {}
        for window_id in ALL_SUB_WINDOWS:
            agg = _aggregate_window_at_vol_target(
                sensitivity_results, window_id, vt,
            )
            if agg is None:
                per_window[window_id] = {"available": False}
                continue
            # _load_btc_bh_baseline raises FileNotFoundError if missing
            # (CHANGES_REQUESTED #4); see _compute_primary_per_window.
            btc_bh = _load_btc_bh_baseline(window_id)
            btc_bh_total = float(btc_bh.get("total_return_usd", 0.0))
            primary_pass = _primary_criterion_pass(
                agg["strategy_total_return_usd"], btc_bh_total
            )
            per_window[window_id] = {
                "available": True,
                "strategy_total_return_usd": agg["strategy_total_return_usd"],
                "btc_bh_total_return_usd": btc_bh_total,
                "primary_criterion_pass": primary_pass,
            }
        n_available = sum(1 for w in per_window.values() if w.get("available"))
        n_pass = sum(
            1 for w in per_window.values()
            if w.get("available") and w.get("primary_criterion_pass")
        )
        # Pre-reg §4.2 — vol_target value "passes" the sensitivity verdict
        # map only if primary criterion holds 3/3 sub-windows.
        vol_target_pass = (n_available == 3 and n_pass == 3)
        out[vt] = {
            "per_window": per_window,
            "n_available_windows": n_available,
            "n_primary_pass_windows": n_pass,
            "vol_target_pass": vol_target_pass,
        }
    return out


def _sensitivity_verdict(
    sensitivity_per_vol_target: dict[float, dict],
) -> dict:
    """Apply pre-reg §4.2 verdict map based on count of vol_target PASS values."""
    n_pass_4 = sum(
        1 for d in sensitivity_per_vol_target.values()
        if d.get("vol_target_pass")
    )
    n_available_4 = sum(
        1 for d in sensitivity_per_vol_target.values()
        if d.get("n_available_windows", 0) == 3
    )
    return {
        "n_pass_out_of_4": n_pass_4,
        "n_available_out_of_4": n_available_4,
        "sensitivity_verdict_label": SENSITIVITY_VERDICT_MAP.get(
            n_pass_4, "FAIL_CLEAN"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Verdict classifier (§4.3 + §4.6)
# ─────────────────────────────────────────────────────────────────────────────


def _is_degenerate_window(window_agg: dict) -> bool:
    """Per-window degenerate predicate (pre-reg §4.3 row 7, per-window).

    Returns True when ≥DEGENERATE_FRACTION_THRESHOLD (75%) of the window's
    in-coverage cells have n_trades < N_TRADES_MIN_FOR_ELIGIBILITY.

    NOTE: this is a per-window predicate. The verdict-level FAIL_DEGENERATE
    classification (in `_classify_verdict`) requires a PREDOMINANT share
    of available windows to satisfy this predicate (Option A locked per
    BLOCK #3 review fix 2026-05-14). See DEGENERATE_FRACTION_THRESHOLD
    docstring above for the full predominance + per-window framing.
    """
    n_in_cov = window_agg.get("n_in_coverage", 0)
    if n_in_cov == 0:
        return False
    n_low = window_agg.get("insufficient_data_count", 0)
    return (n_low / n_in_cov) >= DEGENERATE_FRACTION_THRESHOLD


def _classify_verdict(
    primary_per_window: dict[str, dict],
    sensitivity_label: str,
    n_sensitivity_pass: int,
    halt: bool,
) -> dict:
    """Pre-reg §4.3 + §4.6 — classify the Phase 3 verdict.

    `primary_per_window` may be partial (1-2 windows) if halt fired.
    `halt=True` triggers §4.6 asymmetric guard against favorable verdicts.
    """
    available_windows = sorted(primary_per_window.keys())
    n_windows = len(available_windows)
    n_primary_pass = sum(
        1 for w in primary_per_window.values()
        if w.get("primary_criterion_pass")
    )

    # Pre-reg §4 + §4.6 defensive gate (BLOCK #1 review fix 2026-05-14):
    # n_windows<3 + halt=False is an invalid sweep state — pre-reg §4 locks
    # 3/3 conjunctive holding for PASS, and §4.6 only covers halt=True
    # under partial windows. The opposite case (partial without halt)
    # means the sweep didn't complete properly (e.g., operator did
    # --window A standalone, or the pipeline crashed without writing
    # halt_diagnostic.json). Refuse to produce a favorable verdict from
    # such partial data; without halt evidence the inferential weight of
    # partial windows is undefined.
    if n_windows < 3 and not halt:
        return {
            "verdict": "PHASE_3_INSUFFICIENT_DATA",
            "naive_verdict_before_halt_guard": (
                "INVALID_STATE_partial_without_halt"
            ),
            "halt_guard_applied": False,
            "halt": halt,
            "n_windows_available": n_windows,
            "n_primary_pass_windows": n_primary_pass,
            "primary_pass_per_window": {
                w: v.get("primary_criterion_pass", False)
                for w, v in primary_per_window.items()
            },
            "n_degenerate_windows": 0,
            "degenerate_predominant": False,
            "n_sensitivity_pass_out_of_4": n_sensitivity_pass,
            "sensitivity_label": sensitivity_label,
            "defensive_gate_fired": "partial_windows_without_halt",
        }

    # Pre-reg §4.3 row 7 — FAIL_DEGENERATE check.
    # Per-window degenerate: a single sub-window is "degenerate" when ≥75%
    # of its in-coverage cells have n_trades<5 (see _is_degenerate_window).
    # Verdict-level FAIL_DEGENERATE additionally requires the per-window
    # flag to hold for a PREDOMINANT share of available windows (Option A
    # locked per BLOCK #3 review fix 2026-05-14).
    n_degenerate_windows = sum(
        1 for w in primary_per_window.values() if _is_degenerate_window(w)
    )
    degenerate_predominant = n_degenerate_windows >= max(1, n_windows // 2 + 1)

    # Compute naive verdict (pre-halt-guard).
    if n_windows >= 3 and n_primary_pass == 3:
        # 3/3 primary pass — sensitivity decides the strength tier.
        if sensitivity_label == "STRONG":
            naive = "STRONG_PASS"
        elif sensitivity_label == "ROBUST":
            naive = "ROBUST_PASS"
        elif sensitivity_label == "SUCCESS_CONDITIONAL":
            naive = "SUCCESS_CONDITIONAL"
        elif sensitivity_label == "SWEET_SPOT_FAIL":
            naive = "SWEET_SPOT_FAIL"
        else:  # FAIL_CLEAN sensitivity
            naive = "SWEET_SPOT_FAIL"  # primary pass with 0/4 sensitivity = isolated → SWEET_SPOT
    elif n_windows >= 1 and n_primary_pass >= max(1, n_windows - 1) and n_windows < 3:
        # Partial windows + halt fired (only reached when halt=True per
        # BLOCK #1 defensive gate above). Naive verdict is favorable if
        # all available windows pass primary; will be overridden by §4.6
        # guard below.
        if n_primary_pass == n_windows:
            naive = "STRONG_PASS"  # placeholder; overridden by halt-guard
        else:
            naive = "PARTIAL_SUCCESS"
    elif n_windows == 3 and n_primary_pass == 2:
        # Pre-reg §4.3 — PARTIAL_SUCCESS (operator §4.5 default INCONCLUSIVE).
        naive = "PARTIAL_SUCCESS"
    elif n_windows >= 1 and n_primary_pass <= max(0, n_windows - 2) and degenerate_predominant:
        # Pre-reg §4.3 row 7 — signal degenerate predominates → FAIL_DEGENERATE.
        naive = "FAIL_DEGENERATE"
    elif n_windows >= 1 and n_primary_pass <= max(0, n_windows - 2) and n_sensitivity_pass >= 1:
        # Pre-reg §4.3 row 5 — INCONCLUSIVE: primary fails ≥2/3 but sensitivity
        # ≥1/4 passes at some vol_target. Default INCONCLUSIVE (operator §4.5
        # may invoke override path with self-policing requirement).
        naive = "INCONCLUSIVE"
    else:
        # Default: FAIL_CLEAN — mechanism engaged but no edge.
        naive = "FAIL_CLEAN"

    # §4.6 asymmetric halt-guard: favorable verdicts overridden if halt fired
    # AND fewer than 3 windows available. Negative verdicts preserved.
    if halt and n_windows < 3 and naive in FAVORABLE_VERDICTS_OVERRIDABLE:
        verdict = "PHASE_3_INSUFFICIENT_DATA"
        guard_applied = True
    else:
        verdict = naive
        guard_applied = False

    return {
        "verdict": verdict,
        "naive_verdict_before_halt_guard": naive,
        "halt_guard_applied": guard_applied,
        "halt": halt,
        "n_windows_available": n_windows,
        "n_primary_pass_windows": n_primary_pass,
        "primary_pass_per_window": {
            w: v.get("primary_criterion_pass", False)
            for w, v in primary_per_window.items()
        },
        "n_degenerate_windows": n_degenerate_windows,
        "degenerate_predominant": degenerate_predominant,
        "n_sensitivity_pass_out_of_4": n_sensitivity_pass,
        "sensitivity_label": sensitivity_label,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 advance + operator decision hooks (pre-reg §4.5)
# ─────────────────────────────────────────────────────────────────────────────


def _phase_4_advance_decision(verdict: str) -> dict:
    """Pre-reg §4.5 — determine advance/non-advance status + operator gate.

    Returns:
      - auto_advance_to_phase_4: bool — true for STRONG/ROBUST_PASS only
      - operator_decision_required: bool — true for SUCCESS_CONDITIONAL /
        PARTIAL_SUCCESS / INCONCLUSIVE
      - self_policing_required: bool — true when operator override of a
        non-PASS-strong/robust verdict would be needed (§4.5 4-element check)
    """
    if verdict in ("STRONG_PASS", "ROBUST_PASS"):
        return {
            "auto_advance_to_phase_4": True,
            "operator_decision_required": False,
            "self_policing_required": False,
            "decision_note": (
                "Auto-advance to Phase 4 paper trade. Pre-reg §4.5: "
                "PASS strong/robust → no operator decision needed."
            ),
        }
    if verdict in ("SUCCESS_CONDITIONAL", "PARTIAL_SUCCESS", "INCONCLUSIVE"):
        return {
            "auto_advance_to_phase_4": False,
            "operator_decision_required": True,
            "self_policing_required": True,
            "decision_note": (
                "Operator decision required (pre-reg §4.5). Default: treat as "
                "INCONCLUSIVE → FAIL_CLEAN. Override path requires 4-element "
                "self-policing: (1) Bayesian update in derivation_audit.md, "
                "(2) separate sub-spec doc, (3) auditor counter-signoff, "
                "(4) verdict.json operator_override block."
            ),
        }
    # FAIL_CLEAN, FAIL_DEGENERATE, SWEET_SPOT_FAIL, PHASE_3_INSUFFICIENT_DATA
    return {
        "auto_advance_to_phase_4": False,
        "operator_decision_required": False,
        "self_policing_required": False,
        "decision_note": (
            f"No advance to Phase 4. Verdict={verdict} per pre-reg §4.5: "
            "FAIL variants archive the strategy class (FAIL_CLEAN: open "
            "question on basket adequacy; FAIL_DEGENERATE: signal "
            "miscalibration; SWEET_SPOT_FAIL: calibration overfit; "
            "PHASE_3_INSUFFICIENT_DATA: §4.6 halt-guard activated)."
        ),
    }


def _bayesian_update_template(verdict: str, sensitivity_label: str) -> str:
    """Pre-reg §12 — prose template for Bayesian update post-Phase 3.

    Operator fills in the auditor-prior-vs-posterior magnitude shift in
    `derivation_audit.md`. This template documents the expected direction
    based on the verdict.
    """
    templates = {
        "STRONG_PASS": (
            "P(strategy viable for live) updates from ~8-12% (auditor prior) "
            "to ~40-60% (post-Phase-3 strong PASS). Strong primary + robust "
            "sensitivity reduces overfitting concern materially. Advance to "
            "Phase 4 paper trade to validate live cost vs modeled cost."
        ),
        "ROBUST_PASS": (
            "P(strategy viable) updates from ~10-15% prior to ~30-45% "
            "post-robust-PASS. Edge present with acceptable sensitivity (3/4). "
            "Advance to Phase 4 with caveat: 1 vol_target value didn't pass, "
            "monitor sensitivity in paper trade window."
        ),
        "SUCCESS_CONDITIONAL": (
            "P(viable) ~20-30%. Edge concentrated in 2/4 vol_target values; "
            "operator §4.5 decides. Default: INCONCLUSIVE → FAIL_CLEAN unless "
            "self-policing override executed."
        ),
        "PARTIAL_SUCCESS": (
            "P(viable) ~15-25%. Regime-conditional edge (PASS in 2/3 "
            "sub-windows). Operator §4.5 decides between (a) regime-gating "
            "notation + Phase 4, or (b) default INCONCLUSIVE → FAIL_CLEAN."
        ),
        "INCONCLUSIVE": (
            "P(viable) ~10-15%. Primary FAIL at vol=30 in ≥2/3 windows but "
            "some sensitivity sweep PASS. Default §4.5: treat as FAIL clean. "
            "Override requires sub-spec doc + auditor counter-signoff."
        ),
        "FAIL_CLEAN": (
            "P(viable) drops to ~5-10%. Mechanism engaged but profitability "
            "absent. Strategy class archived under current basket. Open "
            "question: basket adequacy (separate epic if pursued)."
        ),
        "FAIL_DEGENERATE": (
            "P(viable) drops to ~3-7%. Ensemble doesn't fire enough to "
            "evaluate. Signal calibration issue OR basket non-trending in "
            "evaluation windows. Strategy class archived; basket revision "
            "may be considered (separate future epic)."
        ),
        "SWEET_SPOT_FAIL": (
            "P(viable) drops to ~5-8%. Primary PASS at vol=30 was an "
            "isolated sweet spot (sensitivity ≤1/4). Calibration overfit. "
            "Strategy class archived."
        ),
        "PHASE_3_INSUFFICIENT_DATA": (
            "P(viable) preserved at pre-Phase-3 prior (~26-39% range). §4.6 "
            "halt-guard activated under partial windows. Operator decides "
            "next step (re-run with adjusted halt thresholds OR archive). "
            "NO inferential weight from the partial windows."
        ),
    }
    return templates.get(verdict, f"No template for verdict={verdict}")


# ─────────────────────────────────────────────────────────────────────────────
# Printers
# ─────────────────────────────────────────────────────────────────────────────


def _print_primary_per_window(primary_per_window: dict[str, dict]) -> None:
    if not primary_per_window:
        print("  (no primary windows available)")
        return
    for window_id in ALL_SUB_WINDOWS:
        if window_id not in primary_per_window:
            print(f"  Window {window_id}: NOT RUN (halted before)")
            continue
        w = primary_per_window[window_id]
        strat = w["strategy_total_return_usd"]
        btc_bh = w.get("btc_bh_total_return_usd")
        hub = w.get("hubrich_total_return_usd")
        lrc = w.get("lrc_archived_total_return_usd")
        passes = "PASS" if w["primary_criterion_pass"] else "FAIL"
        n_trades = w.get("n_trades_total", 0)
        bcount = w.get("bankruptcy_count_total", 0)
        insf = w.get("insufficient_data_count", 0)
        print(
            f"  Window {window_id}: strategy=${strat:>12,.2f}  "
            f"btc_bh=${(btc_bh or 0):>12,.2f}  hubrich=${(hub or 0):>12,.2f}  "
            f"lrc_arch=${(lrc or 0):>12,.2f}  "
            f"n_trades={n_trades:>4} bankrupt={bcount} insf={insf} → {passes}"
        )


def _print_sensitivity(sensitivity_per_vol: dict[float, dict]) -> None:
    if not sensitivity_per_vol:
        print("  (no sensitivity sweep available — halted before)")
        return
    for vt in SENSITIVITY_VOL_TARGETS:
        d = sensitivity_per_vol.get(vt, {})
        passes = "PASS" if d.get("vol_target_pass") else "FAIL"
        n_pass = d.get("n_primary_pass_windows", 0)
        n_avail = d.get("n_available_windows", 0)
        print(
            f"  vol_target={vt:.2f}: "
            f"primary_pass={n_pass}/{n_avail} → {passes}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


def main() -> int:
    # Load primary sweep (per-window).
    primary_results: list[dict] = []
    for win in ALL_SUB_WINDOWS:
        data = _load_json(f"sweep_primary_{win}.json")
        if isinstance(data, list):
            primary_results.extend(data)

    if not primary_results:
        sys.stderr.write(
            "ERROR: no primary sweep results in sweep_primary_*.json. "
            "Run tools/regime_allocation_sweep.py first.\n"
        )
        return 1

    # Load halt diagnostic + sensitivity.
    halt_diag = _load_json("halt_diagnostic.json")
    halt = _extract_halt_from_diagnostic(halt_diag)

    sensitivity_results: list[dict] = []
    for win in ALL_SUB_WINDOWS:
        data = _load_json(f"sweep_sensitivity_{win}.json")
        if isinstance(data, list):
            sensitivity_results.extend(data)

    # Compute primary per-window aggregates + comparisons.
    primary_per_window = _compute_primary_per_window(primary_results)

    # Compute sensitivity per-vol_target aggregates (may be empty if halt fired).
    sensitivity_per_vt = _compute_sensitivity_per_vol_target(sensitivity_results)
    sens_summary = _sensitivity_verdict(sensitivity_per_vt)

    # Classify verdict per §4.3 + §4.6.
    classification = _classify_verdict(
        primary_per_window=primary_per_window,
        sensitivity_label=sens_summary["sensitivity_verdict_label"],
        n_sensitivity_pass=sens_summary["n_pass_out_of_4"],
        halt=halt,
    )
    verdict = classification["verdict"]
    advance = _phase_4_advance_decision(verdict)
    bayesian_template = _bayesian_update_template(
        verdict, sens_summary["sensitivity_verdict_label"]
    )

    # ── Human-readable summary ────────────────────────────────────────────
    print(f"\n=== Phase 3 verdict (epic #338, pre-reg §4) ===")
    print(f"\n--- Primary criterion (vol_target=30%, 3/3 conjunctive) ---")
    _print_primary_per_window(primary_per_window)
    print(f"\n--- Sensitivity sweep (vol_target ∈ {list(SENSITIVITY_VOL_TARGETS)}) ---")
    _print_sensitivity(sensitivity_per_vt)
    print(
        f"\n--- Sensitivity verdict (pre-reg §4.2) ---\n"
        f"  n_pass_out_of_4: {sens_summary['n_pass_out_of_4']}/4 "
        f"(available: {sens_summary['n_available_out_of_4']}/4)\n"
        f"  sensitivity_label: {sens_summary['sensitivity_verdict_label']}"
    )
    print(f"\n--- Verdict classification (pre-reg §4.3 + §4.6) ---")
    print(f"  Halt fired:                {halt}")
    print(f"  N windows available:       {classification['n_windows_available']}/3")
    print(f"  N primary pass:            {classification['n_primary_pass_windows']}/3")
    print(f"  N degenerate windows:      {classification['n_degenerate_windows']}/3")
    print(f"  Halt-guard applied:        {classification['halt_guard_applied']}")
    print(f"  Naive verdict:             {classification['naive_verdict_before_halt_guard']}")
    print(f"  ==> FINAL VERDICT: {verdict}")
    print(f"\n--- Phase 4 advance decision (pre-reg §4.5) ---")
    print(f"  auto_advance_to_phase_4:   {advance['auto_advance_to_phase_4']}")
    print(f"  operator_decision_required: {advance['operator_decision_required']}")
    print(f"  self_policing_required:    {advance['self_policing_required']}")
    print(f"  note: {advance['decision_note']}")
    print(f"\n--- Bayesian update template (pre-reg §12) ---")
    print(f"  {bayesian_template}")

    # ── Machine-readable verdict.json ─────────────────────────────────────
    out = {
        # schema_version bumped to 2 — added operator_override block per
        # CHANGES_REQUESTED #5 review fix 2026-05-14.
        "schema_version": 2,
        "spec_ref": (
            "docs/superpowers/plans/2026-05-14-regime-allocation-phase2-pre-reg.md"
        ),
        "verdict": verdict,
        "classification": classification,
        "primary_per_window": primary_per_window,
        "sensitivity_per_vol_target": {
            str(vt): v for vt, v in sensitivity_per_vt.items()
        },
        "sensitivity_summary": sens_summary,
        "halt_diagnostic_loaded": halt_diag is not None,
        "halt_fired": halt,
        "phase_4_advance_decision": advance,
        "bayesian_update_template": bayesian_template,
        # Pre-reg §4.5 self-policing requirement element (4): operator
        # override block. Populated by operator (not by this tool) when
        # invoking Phase 4 advance from a non-PASS-strong/robust verdict.
        # The schema field below documents the expected shape so operator
        # has guidance for the manual edit. Element 4 atomic with
        # element 1 (Bayesian update in derivation_audit.md) + element 2
        # (separate sub-spec doc, mirror A.4-1.5 mechanism) + element 3
        # (auditor counter-signoff).
        "operator_override": None,
        "operator_override_schema": {
            "description": (
                "Operator override block per pre-reg §4.5 self-policing "
                "requirement (4-element check). Populate when invoking "
                "Phase 4 advance from a non-PASS-strong/robust verdict. "
                "Element 4: this block; plus element 1 (Bayesian update "
                "in derivation_audit.md), element 2 (separate sub-spec "
                "doc mirror A.4-1.5 mechanism), element 3 (auditor "
                "counter-signoff). All 4 elements required atomically; "
                "any Phase 4 advance lacking these is methodologically "
                "invalid under this pre-reg."
            ),
            "fields": {
                "timestamp": (
                    "ISO 8601 UTC datetime when operator invoked override"
                ),
                "rationale": (
                    "Operator's written reason for overriding default "
                    "verdict; must reference the Bayesian update prose"
                ),
                "sub_spec_doc": (
                    "Relative path to sub-spec doc capturing override "
                    "scope (e.g., docs/superpowers/plans/2026-MM-DD-"
                    "phase-3-override-XXX.md)"
                ),
                "auditor_counter_signoff": {
                    "agent_id": (
                        "Agent name/model that performed counter-signoff "
                        "(e.g., code-review-excellence)"
                    ),
                    "signoff_timestamp": (
                        "ISO 8601 UTC datetime of counter-signoff"
                    ),
                },
            },
        },
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_DIR / "verdict.json", "w") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str, allow_nan=False)
    print(f"\nWrote {OUTPUT_DIR / 'verdict.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
