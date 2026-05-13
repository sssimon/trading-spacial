#!/usr/bin/env python3
"""R1 verdict calculator — reads sweep + baseline JSONs and prints pre-reg §4 tables.

Pre-reg: docs/superpowers/plans/2026-05-12-r1-dynamic-exit-pre-reg.md

Reads (from data/retune/2026-05-12-r1-dynamic-exit/):
  - baseline_pre_signal_exit.json
  - sweep_results_{A,B,C}.json (whichever exist)

Emits to stdout: per-sub-window tables of:
  - argmax cell per symbol (§4.1)
  - primary criterion check (§4 primary)
  - secondary criterion check (§4 secondary)
  - cross-sub-window cell stability (§4.4)
  - overall verdict per §4.2

Also writes data/retune/2026-05-12-r1-dynamic-exit/verdict.json.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
OUTPUT_DIR: Final[Path] = REPO_ROOT / "data" / "retune" / "2026-05-12-r1-dynamic-exit"

N_TRADES_MIN: Final[int] = 10
SECONDARY_TL_PCT_THRESHOLD: Final[float] = 20.0  # pre-reg §4 secondary
SECONDARY_SYMBOLS_REQUIRED: Final[int] = 6        # of 8 currently-bankrupt
PRIMARY_PF_THRESHOLD: Final[float] = 1.2
PRIMARY_AVG_PPT_POSITIVE_SYM_COUNT: Final[int] = 1
PRIMARY_NET_PNL_POSITIVE_SYM_COUNT: Final[int] = 3
CURRENTLY_BANKRUPT_SYMBOLS: Final[frozenset[str]] = frozenset({
    "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "UNIUSDT", "XLMUSDT",
    "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
})
ALL_SYMBOLS: Final[tuple[str, ...]] = (
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
    "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
)


def _load_json(name: str):
    path = OUTPUT_DIR / name
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def _extract_halt_from_diagnostic(halt_diag) -> bool:
    """Extract halt boolean from diagnostic dict, raising on malformed input.

    Closes #332 item 2. Replaces the previous
    `bool(halt_diag and halt_diag.get("halt"))` coercion which silently
    accepted truthy non-bool values (e.g., `{"halt": "false"}` → True via
    truthy-string coercion, `{"halt": 0}` → False) — both methodologically
    wrong because a malformed halt diagnostic should be loud, not silent.

    Accepts:
      - `None` → False (no halt diagnostic file written by sweep)
      - `{}` (no "halt" key) → False (key-missing default)
      - `{"halt": True}` → True
      - `{"halt": False}` → False

    Raises ValueError on:
      - non-dict (e.g., list, str, int) — JSON parser returned unexpected type
      - non-bool "halt" value (e.g., string, int) — producer contract violation
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

    Tie-break order (5 levels, all deterministic):
      1. higher net_pnl wins
      2. lower `sl` wins (more conservative stop)
      3. lower `be` wins (more conservative break-even)
      4. lower `lrc_thr` wins (more aggressive exit threshold)
      5. lex-greater `symbol` wins (#332 item 3 defensive 5th key)

    Tie-break levels 1-4 are the methodologically-justified contract: among
    cells with same P&L, prefer more-conservative parameters. Level 5 is
    purely defensive: in normal per-symbol use (cells all share same symbol)
    it's a no-op, but it makes the function safe against any future
    multi-symbol caller — falling back to Python's insertion-order tie-break
    would be contract-fragile against concurrent-worker batching.

    Determinism matters because the previous insertion-order tie-break made
    cell selection fragile to job-execution order across parallel workers.

    Contract: the 4-tuple `(net_pnl, sl, be, lrc_thr)` is expected to uniquely
    identify a cell in the canonical sweep grid for a single symbol. True
    duplicates (same 4-tuple within same symbol) indicate upstream bug — the
    5th key resolves the order silently rather than raising, but the grid
    constructor in `tools/r1_signal_exit_sweep.py` should be audited if
    diagnostic output shows duplicate cell counts.
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
            -c["lrc_thr"],
            c.get("symbol", ""),  # 5th defensive tie-break: lex order on symbol
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
    """Aggregate argmax cell per symbol + primary/secondary criteria for one sub-window."""
    by_symbol: dict[str, list[dict]] = {}
    for r in sweep_results:
        by_symbol.setdefault(r["symbol"], []).append(r)

    per_symbol: dict[str, dict | None] = {sym: None for sym in ALL_SYMBOLS}
    for sym in ALL_SYMBOLS:
        cells = by_symbol.get(sym, [])
        # Skip symbols with all-error / no-data cells (e.g., JUP A/B, PENDLE A).
        if not any(c.get("n_trades", 0) > 0 for c in cells):
            per_symbol[sym] = None
            continue
        per_symbol[sym] = _argmax_cell(cells)

    # Primary criterion (§4):
    # ≥1 sym avg_ppt>0 AND ≥3 sym net_pnl>0 AND avg PF>1.2 on positive subset.
    syms_avg_ppt_positive = [
        sym for sym, c in per_symbol.items()
        if c is not None and _avg_pnl_per_trade(c) is not None and _avg_pnl_per_trade(c) > 0
    ]
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
        len(syms_avg_ppt_positive) >= PRIMARY_AVG_PPT_POSITIVE_SYM_COUNT
        and len(syms_net_pnl_positive) >= PRIMARY_NET_PNL_POSITIVE_SYM_COUNT
        and avg_pf_positive_subset > PRIMARY_PF_THRESHOLD
    )

    # Secondary criterion (§4 secondary):
    # For ≥6 of 8 currently-bankrupt symbols with n_trades >= 10 on argmax cell:
    # TIME_LIMIT% < 20%.
    bankrupt_eligible = []
    for sym in CURRENTLY_BANKRUPT_SYMBOLS:
        c = per_symbol.get(sym)
        if c is None or c.get("n_trades", 0) < N_TRADES_MIN:
            continue
        bankrupt_eligible.append(sym)
    bankrupt_pass = [
        sym for sym in bankrupt_eligible
        if _time_limit_pct(per_symbol[sym]) is not None
        and _time_limit_pct(per_symbol[sym]) < SECONDARY_TL_PCT_THRESHOLD
    ]
    secondary_pass = len(bankrupt_pass) >= SECONDARY_SYMBOLS_REQUIRED

    return {
        "window": window_id,
        "per_symbol_argmax_cell": {
            sym: ({
                "sl": c["sl"], "be": c["be"], "lrc_thr": c["lrc_thr"],
                "n_trades": c["n_trades"], "net_pnl": c["net_pnl"],
                "profit_factor": c["profit_factor"],
                "avg_pnl_per_trade": _avg_pnl_per_trade(c),
                "time_limit_pct": _time_limit_pct(c),
                "signal_exit_pct": _signal_exit_pct(c),
                "exit_reasons": c["exit_reasons"],
            } if c is not None else None)
            for sym, c in per_symbol.items()
        },
        "primary_criterion": {
            "pass": primary_pass,
            "syms_avg_ppt_positive": sorted(syms_avg_ppt_positive),
            "syms_net_pnl_positive": sorted(syms_net_pnl_positive),
            "avg_pf_on_positive_subset": round(avg_pf_positive_subset, 4),
            "n_syms_avg_ppt_positive": len(syms_avg_ppt_positive),
            "n_syms_net_pnl_positive": len(syms_net_pnl_positive),
            "required": {
                "min_n_syms_avg_ppt_positive": PRIMARY_AVG_PPT_POSITIVE_SYM_COUNT,
                "min_n_syms_net_pnl_positive": PRIMARY_NET_PNL_POSITIVE_SYM_COUNT,
                "min_avg_pf_on_positive_subset": PRIMARY_PF_THRESHOLD,
            },
        },
        "secondary_criterion": {
            "pass": secondary_pass,
            "bankrupt_eligible": sorted(bankrupt_eligible),
            "bankrupt_pass_tl_under_20pct": sorted(bankrupt_pass),
            "n_bankrupt_pass": len(bankrupt_pass),
            "required_n_pass": SECONDARY_SYMBOLS_REQUIRED,
            "tl_pct_threshold": SECONDARY_TL_PCT_THRESHOLD,
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
                per_w[w] = (cell["sl"], cell["be"], cell["lrc_thr"])
        non_none = [v for v in per_w.values() if v is not None]
        same_cell_across = len(set(non_none)) == 1 if len(non_none) >= 2 else None
        out[sym] = {
            "cells_per_window": per_w,
            "same_cell_across_windows": same_cell_across,
            "n_windows_with_data": len(non_none),
        }
    return out


def _classify_verdict(per_window_analysis: dict[str, dict], *, halt: bool = False) -> dict:
    """Pre-reg §4.2 verdict classification.

    Gap #4 (halt guard): if `halt=True` and fewer than 3 sub-windows ran, a naive
    `R1_SUCCESS` / `R1_SUCCESS_CONDITIONAL` is overridden to `R1_INSUFFICIENT_DATA`.
    Rationale: a favorable verdict on partial evidence is "spurious" — we can't
    declare success without seeing B+C. `R1_FAIL` / `R1_INCONCLUSIVE` are kept
    as-is: clear negative evidence on the windows that did run is not spurious,
    and halt does not invalidate it.
    """
    p_per_w = {w: a["primary_criterion"]["pass"] for w, a in per_window_analysis.items()}
    s_per_w = {w: a["secondary_criterion"]["pass"] for w, a in per_window_analysis.items()}

    n_primary_pass = sum(1 for v in p_per_w.values() if v)
    n_secondary_pass = sum(1 for v in s_per_w.values() if v)
    n_windows = len(per_window_analysis)

    if n_primary_pass == n_windows and n_secondary_pass == n_windows:
        verdict = "R1_SUCCESS"
    elif n_primary_pass < n_windows and n_secondary_pass >= max(1, n_windows - 1):
        if n_primary_pass == n_windows - 1 and n_secondary_pass == n_windows:
            verdict = "R1_SUCCESS_CONDITIONAL"
        else:
            verdict = "R1_INCONCLUSIVE"
    else:
        verdict = "R1_FAIL"

    if halt and n_windows < 3 and verdict in ("R1_SUCCESS", "R1_SUCCESS_CONDITIONAL"):
        verdict = "R1_INSUFFICIENT_DATA"

    return {
        "verdict": verdict,
        "primary_pass_per_window": p_per_w,
        "secondary_pass_per_window": s_per_w,
        "n_primary_pass": n_primary_pass,
        "n_secondary_pass": n_secondary_pass,
        "n_windows": n_windows,
        "halt": halt,
    }


def _print_window_table(analysis: dict):
    w = analysis["window"]
    print(f"\n=== Sub-window {w} ===")
    print(f"{'Symbol':<12} {'cell (sl,be,lrc)':<22} {'n_trades':>8} "
          f"{'net_pnl':>10} {'avg_ppt':>9} {'PF':>6} {'TL%':>6} {'SE%':>6}")
    print("-" * 84)
    for sym, c in analysis["per_symbol_argmax_cell"].items():
        if c is None:
            print(f"{sym:<12} INSUFFICIENT_DATA / no eligible cell")
            continue
        cell_str = f"({c['sl']}, {c['be']}, {c['lrc_thr']})"
        avg_ppt = c.get("avg_pnl_per_trade")
        tl = c.get("time_limit_pct")
        se = c.get("signal_exit_pct")
        print(
            f"{sym:<12} {cell_str:<22} {c['n_trades']:>8} "
            f"{c['net_pnl']:>10.2f} "
            f"{(avg_ppt if avg_ppt is not None else 0.0):>9.2f} "
            f"{c['profit_factor']:>6.2f} "
            f"{(tl if tl is not None else 0.0):>6.1f} "
            f"{(se if se is not None else 0.0):>6.1f}"
        )
    p = analysis["primary_criterion"]
    s = analysis["secondary_criterion"]
    print(f"  PRIMARY  pass={p['pass']}  "
          f"(avg_ppt>0: {p['n_syms_avg_ppt_positive']}, "
          f"net_pnl>0: {p['n_syms_net_pnl_positive']}, "
          f"avg PF: {p['avg_pf_on_positive_subset']:.2f})")
    print(f"  SECONDARY pass={s['pass']}  "
          f"(bankrupt symbols with TL%<20: {s['n_bankrupt_pass']}/{s['required_n_pass']} required; "
          f"eligible: {len(s['bankrupt_eligible'])} of 8)")


def main() -> int:
    sweep_a = _load_json("sweep_results_A.json")
    sweep_b = _load_json("sweep_results_B.json")
    sweep_c = _load_json("sweep_results_C.json")
    baseline = _load_json("baseline_pre_signal_exit.json")
    halt_diag = _load_json("halt_after_a_diagnostic.json")
    halt = _extract_halt_from_diagnostic(halt_diag)

    if baseline is None:
        print("WARNING: baseline_pre_signal_exit.json missing", file=sys.stderr)

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

    verdict = _classify_verdict(analyses, halt=halt)
    print(f"\n=== Verdict (§4.2) ===")
    print(f"  Primary pass per window:   {verdict['primary_pass_per_window']}")
    print(f"  Secondary pass per window: {verdict['secondary_pass_per_window']}")
    if halt:
        print(f"  Pre-reg §10 halt fired:    True (loaded from halt_after_a_diagnostic.json)")
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
