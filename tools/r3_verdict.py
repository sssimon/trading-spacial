#!/usr/bin/env python3
"""R3 — Trend-pullback verdict calculator (Phase 2 R3).

Pre-reg: docs/superpowers/plans/2026-05-13-r3-trend-pullback-pre-reg.md

Computes the §4 verdict from sweep results in
`data/retune/2026-05-13-r3-trend-pullback/`:
  - Primary criterion (§4 — conjuntive over 3 sub-windows):
      ≥3 symbols with net_pnl > 0 AND avg PF > 1.2 over positive subset.
  - §4.2 verdict tree (5 outcomes):
      R3_SUCCESS:              Primary ✓ in 3/3 sub-windows
      R3_SUCCESS_CONDITIONAL:  Primary ✓ in 2/3 + cross-window cells stable
      R3_INCONCLUSIVE:         Primary ✓ in 2/3 + cells diverge wildly
      R3_FAIL_SIGNAL_DEGENERATE: ≥2/3 primary ✗ AND ≤2 in-data engage in ≥2 of 3 windows
      R3_FAIL:                 ≥2/3 primary ✗ AND ≥3 in-data engage (default)
  - §4.6 asymmetric halt-guard:
      Halt + n_windows<3 → R3_INSUFFICIENT_DATA ONLY when naive verdict is
      R3_SUCCESS / R3_SUCCESS_CONDITIONAL. Negative verdicts (R3_FAIL /
      R3_INCONCLUSIVE) preserved on partial windows.

Inputs (in OUTPUT_DIR):
  - sweep_results_A.json, sweep_results_B.json, sweep_results_C.json
  - baseline_pre_trend_pullback.json (used for Δ comparison printout)
  - halt_after_a_diagnostic.json (drives §4.6 halt flag)

Outputs:
  - verdict.json (machine-readable)
  - stdout: human-readable verdict summary + cross-window cell stability

Usage:
  python tools/r3_verdict.py

Exit codes:
  0 — verdict computed and written
  1 — missing inputs (sweep result files)
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
OUTPUT_DIR: Final[Path] = REPO_ROOT / "data" / "retune" / "2026-05-13-r3-trend-pullback"

ALL_SYMBOLS: Final[tuple[str, ...]] = (
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
    "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
)

CURRENTLY_BANKRUPT_SYMBOLS: Final[frozenset[str]] = frozenset({
    "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "UNIUSDT", "XLMUSDT",
    "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
})

# Pre-reg §4.1 minimum trades for cell eligibility.
N_TRADES_MIN: Final[int] = 10
# Pre-reg §4 primary criterion thresholds.
PRIMARY_NET_PNL_POSITIVE_SYM_COUNT: Final[int] = 3
PRIMARY_PF_THRESHOLD: Final[float] = 1.2
# Pre-reg §4.2 signal-degenerate threshold — ≤2 in-data symbols engage in ≥2/3 windows.
DEGENERATE_ENGAGE_MAX: Final[int] = 2
DEGENERATE_WINDOWS_MIN: Final[int] = 2


def _load_json(name: str):
    path = OUTPUT_DIR / name
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def _extract_halt_from_diagnostic(halt_diag) -> bool:
    """Extract halt boolean from diagnostic dict, raising on malformed.

    Mirrors `tools/r1_verdict.py:_extract_halt_from_diagnostic` (#332 item 2
    hardened pattern). Defends against silent acceptance of truthy non-bool
    values that could flip verdict classification under §4.6 halt-guard.
    """
    if halt_diag is None:
        return False
    if not isinstance(halt_diag, dict):
        raise ValueError(
            f"halt_diag must be dict or None, got {type(halt_diag).__name__}: {halt_diag!r}"
        )
    halt_value = halt_diag.get("halt", False)
    if not isinstance(halt_value, bool):
        raise ValueError(
            f"halt_diag['halt'] must be bool, got {type(halt_value).__name__}: {halt_value!r}"
        )
    return halt_value


def _argmax_cell(cells: list[dict]) -> dict | None:
    """Pre-reg §4.1: argmax(net_pnl) among cells with n_trades >= N_TRADES_MIN.

    Tie-break order: (net_pnl, -sl, -be, -pullback_distance, symbol).
    Lex-greater symbol on full 4-tuple tie (defensive; per-symbol callers
    pass cells with identical symbol, so 5th key is no-op there).

    Mirrors `tools/r1_verdict.py:_argmax_cell` structure with pullback_distance
    instead of lrc_thr (sweep dimension shift for R3 per pre-reg §2.5).
    """
    eligible = [c for c in cells if c.get("n_trades", 0) >= N_TRADES_MIN]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda c: (
            c["net_pnl"],
            -c["sl"],
            -c["be"],
            -c["pullback_distance"],
            c.get("symbol", ""),  # 5th defensive tie-break
        ),
    )


def _avg_pnl_per_trade(cell: dict | None) -> float | None:
    if cell is None or cell.get("n_trades", 0) == 0:
        return None
    return cell["net_pnl"] / cell["n_trades"]


def _time_limit_pct(cell: dict | None) -> float | None:
    if cell is None or cell.get("n_trades", 0) <= 0:
        return None
    return 100.0 * cell.get("exit_reasons", {}).get("TIME_LIMIT", 0) / cell["n_trades"]


def _signal_exit_pct(cell: dict | None) -> float | None:
    if cell is None or cell.get("n_trades", 0) <= 0:
        return None
    return 100.0 * cell.get("exit_reasons", {}).get("SIGNAL_EXIT", 0) / cell["n_trades"]


def _analyze_window(window_id: str, sweep_results: list[dict]) -> dict:
    """Aggregate argmax cell per symbol + primary criterion for one sub-window."""
    by_symbol: dict[str, list[dict]] = {}
    for r in sweep_results:
        by_symbol.setdefault(r["symbol"], []).append(r)

    per_symbol: dict[str, dict | None] = {sym: None for sym in ALL_SYMBOLS}
    n_symbols_engaged = 0
    for sym in ALL_SYMBOLS:
        cells = by_symbol.get(sym, [])
        # Skip symbols with all-error / no-data cells (e.g., JUP A/B, PENDLE A).
        if not any(c.get("n_trades", 0) > 0 for c in cells):
            per_symbol[sym] = None
            continue
        argmax = _argmax_cell(cells)
        per_symbol[sym] = argmax
        if argmax is not None and argmax.get("n_trades", 0) >= N_TRADES_MIN:
            n_symbols_engaged += 1

    # Primary criterion (pre-reg §4, kickoff strict 3/3):
    # ≥3 symbols with net_pnl > 0 AND avg PF > 1.2 over positive subset.
    syms_net_pnl_positive = [
        sym for sym, c in per_symbol.items() if c is not None and c["net_pnl"] > 0
    ]
    pf_positive_subset = [
        per_symbol[s]["profit_factor"] for s in syms_net_pnl_positive
        if per_symbol[s] is not None and per_symbol[s].get("profit_factor", 0) > 0
    ]
    avg_pf_positive_subset = (
        statistics.mean(pf_positive_subset) if pf_positive_subset else 0.0
    )

    primary_pass = (
        len(syms_net_pnl_positive) >= PRIMARY_NET_PNL_POSITIVE_SYM_COUNT
        and avg_pf_positive_subset > PRIMARY_PF_THRESHOLD
    )

    return {
        "window": window_id,
        "per_symbol_argmax_cell": {
            sym: ({
                "sl": c["sl"], "be": c["be"], "pullback_distance": c["pullback_distance"],
                "n_trades": c["n_trades"], "net_pnl": c["net_pnl"],
                "profit_factor": c["profit_factor"],
                "avg_pnl_per_trade": _avg_pnl_per_trade(c),
                "time_limit_pct": _time_limit_pct(c),
                "signal_exit_pct": _signal_exit_pct(c),
                "exit_reasons": c["exit_reasons"],
            } if c is not None else None)
            for sym, c in per_symbol.items()
        },
        "n_symbols_engaged": n_symbols_engaged,
        "primary_criterion": {
            "pass": primary_pass,
            "syms_net_pnl_positive": sorted(syms_net_pnl_positive),
            "avg_pf_on_positive_subset": round(avg_pf_positive_subset, 4),
            "n_syms_net_pnl_positive": len(syms_net_pnl_positive),
            "required": {
                "min_n_syms_net_pnl_positive": PRIMARY_NET_PNL_POSITIVE_SYM_COUNT,
                "min_avg_pf_on_positive_subset": PRIMARY_PF_THRESHOLD,
            },
        },
    }


def _cross_window_stability(per_window_argmax: dict[str, dict]) -> dict:
    """Pre-reg §4.4: report each symbol's argmax cell across windows."""
    out = {}
    for sym in ALL_SYMBOLS:
        per_w = {}
        for w in ("A", "B", "C"):
            cell = per_window_argmax.get(w, {}).get(sym)
            if cell is None:
                per_w[w] = None
            else:
                per_w[w] = (cell["sl"], cell["be"], cell["pullback_distance"])
        non_none = [v for v in per_w.values() if v is not None]
        same_cell_across = len(set(non_none)) == 1 if len(non_none) >= 2 else None
        out[sym] = {
            "cells_per_window": per_w,
            "same_cell_across_windows": same_cell_across,
            "n_windows_with_data": len(non_none),
        }
    return out


def _classify_verdict(
    per_window_analysis: dict[str, dict],
    *,
    halt: bool = False,
    stability: dict | None = None,
) -> dict:
    """Pre-reg §4.2 verdict classification.

    §4.6 asymmetric halt-guard: if `halt=True` and fewer than 3 sub-windows ran,
    naive `R3_SUCCESS` / `R3_SUCCESS_CONDITIONAL` is overridden to
    `R3_INSUFFICIENT_DATA`. Negative verdicts (`R3_FAIL_*` / `R3_INCONCLUSIVE`)
    are preserved on partial windows — clear negative evidence on the windows
    that did run is not spurious, and halt does not invalidate it. Mirrors
    R1 §4.6 amendment pattern.
    """
    p_per_w = {w: a["primary_criterion"]["pass"] for w, a in per_window_analysis.items()}
    engaged_per_w = {w: a["n_symbols_engaged"] for w, a in per_window_analysis.items()}

    n_primary_pass = sum(1 for v in p_per_w.values() if v)
    n_windows = len(per_window_analysis)

    # Cross-window cell stability for SUCCESS_CONDITIONAL vs INCONCLUSIVE branch
    # split. "Stable" = ≥3 symbols pick the same cell across windows that have data.
    if stability is None:
        cells_stable = False
    else:
        n_stable_symbols = sum(
            1 for s_info in stability.values()
            if s_info.get("same_cell_across_windows") is True
        )
        cells_stable = n_stable_symbols >= 3

    # Engagement degeneracy check — ≤2 in-data symbols engage in ≥DEGENERATE_WINDOWS_MIN windows.
    n_degenerate_windows = sum(
        1 for v in engaged_per_w.values() if v <= DEGENERATE_ENGAGE_MAX
    )
    signal_degenerate = n_degenerate_windows >= DEGENERATE_WINDOWS_MIN

    # Apply verdict tree.
    if n_primary_pass == n_windows == 3:
        verdict = "R3_SUCCESS"
    elif n_primary_pass == 2 and n_windows == 3:
        verdict = "R3_SUCCESS_CONDITIONAL" if cells_stable else "R3_INCONCLUSIVE"
    elif n_primary_pass <= n_windows - 2 and signal_degenerate:
        verdict = "R3_FAIL_SIGNAL_DEGENERATE"
    else:
        verdict = "R3_FAIL"

    # §4.6 asymmetric halt-guard: override favorable verdicts on partial windows.
    if halt and n_windows < 3 and verdict in ("R3_SUCCESS", "R3_SUCCESS_CONDITIONAL"):
        verdict = "R3_INSUFFICIENT_DATA"

    return {
        "verdict": verdict,
        "primary_pass_per_window": p_per_w,
        "n_symbols_engaged_per_window": engaged_per_w,
        "n_primary_pass": n_primary_pass,
        "n_windows": n_windows,
        "halt": halt,
        "signal_degenerate_check": {
            "fires": signal_degenerate,
            "n_windows_with_engage_le_threshold": n_degenerate_windows,
            "engage_max_for_degenerate": DEGENERATE_ENGAGE_MAX,
            "min_windows_required": DEGENERATE_WINDOWS_MIN,
        },
        "cells_stable_check": {
            "stable": cells_stable,
            "stability_provided": stability is not None,
        },
    }


def _print_window_table(analysis: dict) -> None:
    """Human-readable per-window summary."""
    print(f"\n=== Sub-window {analysis['window']} — argmax cell per symbol ===")
    print(f"{'symbol':<12} {'sl':>5} {'be':>5} {'pull':>5} {'n':>4} "
          f"{'net_pnl':>10} {'avg_ppt':>9} {'PF':>6} {'TL%':>6} {'SE%':>6}")
    for sym in ALL_SYMBOLS:
        c = analysis["per_symbol_argmax_cell"].get(sym)
        if c is None:
            print(f"{sym:<12} (no eligible cell)")
            continue
        avg_ppt = c.get("avg_pnl_per_trade")
        tl = c.get("time_limit_pct")
        se = c.get("signal_exit_pct")
        print(
            f"{sym:<12} {c['sl']:>5.2f} {c['be']:>5.2f} {c['pullback_distance']:>5.2f} "
            f"{c['n_trades']:>4} {c['net_pnl']:>10.2f} "
            f"{(avg_ppt if avg_ppt is not None else 0.0):>9.2f} "
            f"{c['profit_factor']:>6.2f} "
            f"{(tl if tl is not None else 0.0):>6.1f} "
            f"{(se if se is not None else 0.0):>6.1f}"
        )
    p = analysis["primary_criterion"]
    print(f"  PRIMARY  pass={p['pass']}  "
          f"(net_pnl>0: {p['n_syms_net_pnl_positive']}, "
          f"avg PF on positive subset: {p['avg_pf_on_positive_subset']:.2f})")
    print(f"  n_symbols_engaged: {analysis['n_symbols_engaged']}/10")


def main() -> int:
    sweep_a = _load_json("sweep_results_A.json")
    sweep_b = _load_json("sweep_results_B.json")
    sweep_c = _load_json("sweep_results_C.json")
    baseline = _load_json("baseline_pre_trend_pullback.json")
    halt_diag = _load_json("halt_after_a_diagnostic.json")
    halt = _extract_halt_from_diagnostic(halt_diag)

    if baseline is None:
        print("WARNING: baseline_pre_trend_pullback.json missing", file=sys.stderr)

    analyses: dict[str, dict] = {}
    if sweep_a is not None:
        analyses["A"] = _analyze_window("A", sweep_a)
    if sweep_b is not None:
        analyses["B"] = _analyze_window("B", sweep_b)
    if sweep_c is not None:
        analyses["C"] = _analyze_window("C", sweep_c)

    if not analyses:
        print("ERROR: no sweep results found", file=sys.stderr)
        return 1

    for w in ("A", "B", "C"):
        if w in analyses:
            _print_window_table(analyses[w])

    per_window_argmax = {
        w: analyses[w]["per_symbol_argmax_cell"] for w in analyses
    }
    stability = _cross_window_stability(per_window_argmax)
    print("\n=== Cross-sub-window cell stability (§4.4) ===")
    for sym, s in stability.items():
        cells = s["cells_per_window"]
        flag = ""
        if s["same_cell_across_windows"] is True:
            flag = "  [stable across all 3]"
        elif s["same_cell_across_windows"] is False:
            flag = "  [diverges]"
        print(f"  {sym:<12}  A:{cells.get('A')}  B:{cells.get('B')}  C:{cells.get('C')}{flag}")

    verdict = _classify_verdict(analyses, halt=halt, stability=stability)
    print(f"\n=== Verdict (§4.2 + §4.6 halt-guard) ===")
    print(f"  Primary pass per window:    {verdict['primary_pass_per_window']}")
    print(f"  Symbols engaged per window: {verdict['n_symbols_engaged_per_window']}")
    if halt:
        halt_reasons = halt_diag.get("halt_reason", []) if isinstance(halt_diag, dict) else []
        print(f"  Pre-reg §10.4 halt fired:   True (reasons: {halt_reasons})")
    print(f"  Signal degenerate check:    fires={verdict['signal_degenerate_check']['fires']}")
    print(f"  Cells stable check:         stable={verdict['cells_stable_check']['stable']}")
    print(f"  ==> VERDICT: {verdict['verdict']}")

    out = {
        "analyses": analyses,
        "cross_window_stability": stability,
        "verdict": verdict,
    }
    with open(OUTPUT_DIR / "verdict.json", "w") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)
    print(f"\nWrote {OUTPUT_DIR / 'verdict.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
