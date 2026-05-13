#!/usr/bin/env python3
"""R3 — Trend-pullback sweep harness (Phase 2 R3).

Pre-reg: docs/superpowers/plans/2026-05-13-r3-trend-pullback-pre-reg.md

Sweep (per sub-window, per symbol, per cell):
  atr_sl_mult       in {0.5, 0.7, 1.0, 1.5, 2.5}       (§2.5, 5 values)
  atr_be_mult       in {1.5, 2.0, 2.5}                  (§2.5, 3 values)
  pullback_distance in {0.3, 0.4, 0.5, 0.6, 0.7}        (§2.5, 5 values — NEW)
  atr_tp_mult       = current per-symbol value (§2.4 kept active, NOT swept)
  lrc_exit_threshold= 50.0  (§2.3 R1 SIGNAL_EXIT kept active at midline, fixed)
  time_limit_hours  = 36.0  (§9.2 (a) operator-confirmed conservative uniform)
  trend_pullback_enabled = True (sweep cells); False (baseline cells)

Total compute: 10 symbols × 3 sub-windows × 75 cells = 2,250 backtests
              + 30 baselines (10 sym × 3 sub-win, trend_pullback_enabled=False)

Pre-reg §10.4 halt conditions (BOTH evaluated on sub-window A argmax cells):
  H1 — signal degenerate: ≥6 of 8 in-data symbols have NO argmax cell with
       n_trades ≥ 10 (signal fires too rarely to evaluate).
  H2 — TL horizon mismatch: ≥6 of 8 in-data symbols have TIME_LIMIT% > 50% on
       their argmax cell (TL=36h too short to capture trend-pullback move).
  Either fires → abort B+C, write halt diagnostic; verdict classifies as
  R3_FAIL per pre-reg §4.2 (signal-degenerate or clean variant).

Output (data/retune/2026-05-13-r3-trend-pullback/):
  - baseline_pre_trend_pullback.json     (30 cells, LRC entry baseline)
  - sweep_results_A.json                 (750 cells, sub-window A)
  - sweep_results_B.json                 (sub-window B; only if §10.4 NOT fired)
  - sweep_results_C.json                 (sub-window C; only if §10.4 NOT fired)
  - signal_diagnostics.json              (per-symbol signal fire counts)
  - coverage.json                        (per-(symbol, sub-window) usable_bars)
  - manifest.json
  - halt_after_a_diagnostic.json         (always written for window A)
  - halt_after_a.txt                     (only if §10.4 fires)

Usage:
  python tools/r3_trend_pullback_sweep.py                  # all three windows
  python tools/r3_trend_pullback_sweep.py --window A       # one window
  python tools/r3_trend_pullback_sweep.py --baselines-only # skip sweep
  python tools/r3_trend_pullback_sweep.py --skip-baselines # honor existing baseline JSON
  python tools/r3_trend_pullback_sweep.py --smoke          # 1 cell smoke test

Exit codes:
  0 — clean completion
  2 — pre-reg §10.4 halt condition fired (B+C aborted)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from functools import partial
from multiprocessing import Pool, cpu_count
from pathlib import Path
from typing import Final

# Pre-registered constants. DO NOT TUNE without amending pre-reg
# docs/superpowers/plans/2026-05-13-r3-trend-pullback-pre-reg.md.
GRID_SL: Final[tuple[float, ...]] = (0.5, 0.7, 1.0, 1.5, 2.5)
GRID_BE: Final[tuple[float, ...]] = (1.5, 2.0, 2.5)
GRID_PULLBACK_DISTANCE: Final[tuple[float, ...]] = (0.3, 0.4, 0.5, 0.6, 0.7)

# Pre-reg §2.3 SIGNAL_EXIT kept active at midline, fixed (NOT swept in R3).
LRC_EXIT_THRESHOLD_FIXED: Final[float] = 50.0

# Pre-reg §9.2 (a) operator-confirmed: 36h conservative uniform TL.
# Justified in §10.1 against RW theory (~2.7 ATR target horizon).
UNIFORM_TIME_LIMIT_HOURS: Final[float] = 36.0

CUTOFF_ISO: Final[str] = "2025-04-30T00:00:00+00:00"  # data/ leakage cliff

CURATED_SYMBOLS: Final[tuple[str, ...]] = (
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
    "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
)

# Pre-reg §3 sub-windows. All non-overlapping and BEFORE the leakage cliff.
SUB_WINDOWS: Final[dict[str, tuple[str, str]]] = {
    "A": ("2022-04-01T00:00:00+00:00", "2022-07-01T00:00:00+00:00"),
    "B": ("2023-04-01T00:00:00+00:00", "2023-07-01T00:00:00+00:00"),
    "C": ("2025-01-30T00:00:00+00:00", "2025-04-30T00:00:00+00:00"),
}

# Pre-reg §4.1 min trade count for cell eligibility.
N_TRADES_MIN: Final[int] = 10
# Pre-reg §3 usable_bars threshold for (symbol, sub-window) inclusion.
USABLE_BARS_MIN: Final[int] = 500
# Pre-reg §10.4 halt H1 (signal degenerate) — ≥6 in-data symbols WITHOUT any
# cell n_trades ≥ N_TRADES_MIN.
HALT_H1_SYMBOLS_THRESHOLD: Final[int] = 6
# Pre-reg §10.4 halt H2 (TL horizon mismatch) — ≥6 in-data symbols with
# TIME_LIMIT% > threshold on argmax cell.
HALT_H2_TL_PCT_THRESHOLD: Final[float] = 50.0
HALT_H2_SYMBOLS_THRESHOLD: Final[int] = 6
# §1.1 currently-bankrupt symbols (subset of §4 aggregation).
CURRENTLY_BANKRUPT_SYMBOLS: Final[frozenset[str]] = frozenset({
    "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "UNIUSDT", "XLMUSDT",
    "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
})

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
OUTPUT_DIR: Final[Path] = REPO_ROOT / "data" / "retune" / "2026-05-13-r3-trend-pullback"

# Allow `from auto_tune import ...` and `from backtest import ...` when this
# script is invoked directly (`python tools/r3_trend_pullback_sweep.py`).
# Workers spawned by multiprocessing inherit sys.path.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _iso_to_utc_dt(iso: str) -> datetime:
    """Parse an ISO 8601 string into a tz-aware UTC datetime."""
    return datetime.fromisoformat(iso).astimezone(timezone.utc)


def _resolve_per_symbol_tp(app_config: dict, symbol: str) -> float:
    """Read the per-symbol atr_tp_mult kept active per pre-reg §2.4."""
    overrides = app_config.get("symbol_overrides", {}) or {}
    entry = overrides.get(symbol.upper(), {})
    if not isinstance(entry, dict):
        return 4.0
    return float(entry.get("atr_tp_mult", 4.0))


def _apply_uniform_tl(cfg: dict, tl_hours: float) -> dict:
    """Override per-symbol time_limit_hours uniformly across curated symbols.

    Pre-reg §9.2 (a): uniform 36h for trend-pullback frame. The mean-reversion
    anchor (~5h) does NOT apply to trend-pullback — see §10.1 RW math.

    Returns the same cfg with `symbol_overrides[sym]["time_limit_hours"]` set
    to `tl_hours` for every curated symbol. Other override fields are
    preserved (atr_sl_mult, atr_tp_mult, atr_be_mult, max_participation_rate,
    cooldown_hours, etc.).
    """
    overrides = dict(cfg.get("symbol_overrides", {}) or {})
    for sym in CURATED_SYMBOLS:
        entry = dict(overrides.get(sym.upper(), {}))
        entry["time_limit_hours"] = float(tl_hours)
        overrides[sym.upper()] = entry
    cfg["symbol_overrides"] = overrides
    return cfg


def _process_cell(args: dict) -> dict:
    """Worker: run one (symbol, sub-window, cell) backtest.

    Pure function (config path + dict args). Imports inside the function body
    so multiprocessing spawns get fresh per-child module state.
    """
    from auto_tune import run_backtest_with_params

    symbol = args["symbol"]
    sl = args["sl"]
    be = args["be"]
    pullback_distance = args["pullback_distance"]
    sim_start = _iso_to_utc_dt(args["sim_start_iso"])
    sim_end = _iso_to_utc_dt(args["sim_end_iso"])
    cutoff = _iso_to_utc_dt(args["cutoff_iso"])
    trend_pullback_enabled = bool(args["trend_pullback_enabled"])
    app_config_path = args["app_config_path"]

    with open(app_config_path) as f:
        app_config = json.load(f)

    cfg = dict(app_config)
    # Pre-reg §9.2 (a): uniform 36h TL across all symbols for trend-pullback.
    # Applied to BOTH baseline and sweep cells so the comparison is apples-to-apples.
    cfg = _apply_uniform_tl(cfg, UNIFORM_TIME_LIMIT_HOURS)
    cfg["trend_pullback_enabled"] = trend_pullback_enabled
    cfg["trend_pullback_distance"] = float(pullback_distance)
    # Pre-reg §2.3: R1 SIGNAL_EXIT kept active during R3 (also for baseline so
    # baseline-vs-sweep Δ isolates the entry-signal-frame question).
    cfg["dynamic_exit_enabled"] = True
    cfg["lrc_exit_threshold"] = LRC_EXIT_THRESHOLD_FIXED

    # Per pre-reg §2.4: atr_tp_mult kept active at the symbol's current value.
    tp = _resolve_per_symbol_tp(app_config, symbol)
    params = {
        "atr_sl_mult": float(sl),
        "atr_tp_mult": float(tp),
        "atr_be_mult": float(be),
    }

    err = None
    n_trades = 0
    net_pnl = 0.0
    profit_factor = 0.0
    win_rate = 0.0
    max_dd_pct = 0.0
    bankruptcy_count = 0
    clamped_trade_count = 0
    exit_counts: dict[str, int] = {}
    trade_durations: list[float] = []
    real_trades_signature = "no_data"

    try:
        trades, metrics = run_backtest_with_params(
            symbol, params, sim_start, sim_end,
            cutoff=cutoff, app_config=cfg,
        )
        if isinstance(metrics, dict):
            if "error" in metrics and metrics.get("total_trades", 0) == 0:
                err = str(metrics.get("error"))
            n_trades = int(metrics.get("total_trades", 0))
            net_pnl = float(metrics.get("net_pnl", 0.0))
            profit_factor = float(metrics.get("profit_factor", 0.0))
            win_rate = float(metrics.get("win_rate", 0.0))
            max_dd_pct = float(metrics.get("max_drawdown_pct", 0.0))
            bankruptcy_count = int(metrics.get("bankruptcy_count", 0))
            clamped_trade_count = int(metrics.get("clamped_trade_count", 0))
        if trades:
            real = [t for t in trades if t.get("exit_reason") not in (None, "OPEN")]
            real_trades_signature = f"{len(real)}_closed"
            exit_counts = dict(Counter(t.get("exit_reason") for t in real))
            for t in real:
                d = t.get("duration_hours")
                if d is not None:
                    trade_durations.append(float(d))
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"

    avg_duration_h = (
        sum(trade_durations) / len(trade_durations) if trade_durations else 0.0
    )

    out = {
        "symbol": symbol,
        "sub_window": args["sub_window"],
        "sl": float(sl),
        "tp": float(tp),
        "be": float(be),
        "pullback_distance": float(pullback_distance),
        "lrc_thr": LRC_EXIT_THRESHOLD_FIXED,  # fixed; kept for diagnostic
        "time_limit_hours": UNIFORM_TIME_LIMIT_HOURS,
        "trend_pullback_enabled": trend_pullback_enabled,
        "n_trades": n_trades,
        "net_pnl": round(net_pnl, 4),
        "profit_factor": round(profit_factor, 4),
        "win_rate": round(win_rate, 4),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "bankruptcy_count": bankruptcy_count,
        "clamped_trade_count": clamped_trade_count,
        "avg_duration_hours": round(avg_duration_h, 2),
        "exit_reasons": exit_counts,
        "real_trades_signature": real_trades_signature,
    }
    if err is not None:
        out["error"] = err
    return out


def _check_usable_bars(symbol: str, sim_start: datetime, sim_end: datetime, cutoff: datetime) -> int:
    """Return the count of 1H bars available for `symbol` inside [sim_start, sim_end), below cutoff."""
    from backtest import get_cached_data
    from dateutil.relativedelta import relativedelta

    df = get_cached_data(symbol, "1h", start_date=sim_start - relativedelta(months=1))
    if df is None or df.empty:
        return 0
    cutoff_naive = cutoff.replace(tzinfo=None) if cutoff.tzinfo else cutoff
    start_naive = sim_start.replace(tzinfo=None) if sim_start.tzinfo else sim_start
    end_naive = sim_end.replace(tzinfo=None) if sim_end.tzinfo else sim_end
    mask = (df.index >= start_naive) & (df.index < end_naive) & (df.index < cutoff_naive)
    return int(mask.sum())


def _build_baseline_jobs(app_config_path: str) -> list[dict]:
    """30 baseline cells = 10 symbols × 3 sub-windows, trend_pullback_enabled=False.

    Baseline uses LRC entry path (existing strategy) with SAME R1+TL fixes
    (dynamic_exit_enabled=True, lrc_exit_threshold=50, uniform TL=36h) applied,
    so the sweep-vs-baseline Δ isolates the entry-signal-frame question
    (trend-pullback vs LRC) rather than confounding with R1 changes.
    """
    jobs = []
    for win_id, (start_iso, end_iso) in SUB_WINDOWS.items():
        for sym in CURATED_SYMBOLS:
            jobs.append({
                "symbol": sym,
                "sub_window": win_id,
                "sim_start_iso": start_iso,
                "sim_end_iso": end_iso,
                "cutoff_iso": CUTOFF_ISO,
                "sl": None,   # filled in below — baseline uses symbol's current SL
                "be": None,
                "pullback_distance": 0.5,  # ignored when trend_pullback_enabled=False
                "trend_pullback_enabled": False,
                "app_config_path": app_config_path,
            })

    # Fill SL/BE from current per-symbol overrides.
    with open(app_config_path) as f:
        app_config = json.load(f)
    overrides = app_config.get("symbol_overrides", {}) or {}
    for job in jobs:
        entry = overrides.get(job["symbol"].upper(), {})
        entry = entry if isinstance(entry, dict) else {}
        job["sl"] = float(entry.get("atr_sl_mult", 1.0))
        job["be"] = float(entry.get("atr_be_mult", 1.5))
    return jobs


def _build_sweep_jobs(window_id: str, app_config_path: str) -> list[dict]:
    """For a given sub-window: 10 symbols × 75 cells = 750 jobs."""
    start_iso, end_iso = SUB_WINDOWS[window_id]
    jobs = []
    for sym in CURATED_SYMBOLS:
        for sl in GRID_SL:
            for be in GRID_BE:
                for pull in GRID_PULLBACK_DISTANCE:
                    jobs.append({
                        "symbol": sym,
                        "sub_window": window_id,
                        "sim_start_iso": start_iso,
                        "sim_end_iso": end_iso,
                        "cutoff_iso": CUTOFF_ISO,
                        "sl": float(sl),
                        "be": float(be),
                        "pullback_distance": float(pull),
                        "trend_pullback_enabled": True,
                        "app_config_path": app_config_path,
                    })
    return jobs


def _coverage_for_window(window_id: str, app_config_path: str) -> dict[str, int]:
    """Return {symbol -> usable_bars} for a window. Surfaces INSUFFICIENT_DATA in deliverables."""
    start_iso, end_iso = SUB_WINDOWS[window_id]
    sim_start = _iso_to_utc_dt(start_iso)
    sim_end = _iso_to_utc_dt(end_iso)
    cutoff = _iso_to_utc_dt(CUTOFF_ISO)
    coverage = {}
    for sym in CURATED_SYMBOLS:
        coverage[sym] = _check_usable_bars(sym, sim_start, sim_end, cutoff)
    return coverage


def _argmax_cell_per_symbol(results: list[dict]) -> dict[str, dict | None]:
    """Pre-reg §4.1: argmax(net_pnl) per symbol, subject to n_trades >= N_TRADES_MIN.

    Tie-break: when two cells tie on net_pnl, favor lower `sl`, then lower `be`,
    then lower `pullback_distance` — i.e., the more conservative parameter
    combination. Mirrors `tools/r1_verdict.py:_argmax_cell` (modulo 5th key —
    per-symbol grouping below guarantees all eligible cells share the same
    symbol, so a 5th tie-break key would be a no-op).

    Returns {symbol -> cell dict or None if no cell satisfies constraint}.
    """
    by_symbol: dict[str, list[dict]] = {}
    for r in results:
        # Hardened-pattern filter (#332 item 4): is not None, not truthy.
        if r.get("error") is not None and r.get("n_trades", 0) == 0:
            continue
        by_symbol.setdefault(r["symbol"], []).append(r)

    out: dict[str, dict | None] = {}
    for sym, cells in by_symbol.items():
        eligible = [c for c in cells if c["n_trades"] >= N_TRADES_MIN]
        if not eligible:
            out[sym] = None
        else:
            # Deterministic tuple tie-break per #332 item 1 + pre-reg §4.1.
            out[sym] = max(
                eligible,
                key=lambda c: (
                    c["net_pnl"], -c["sl"], -c["be"], -c["pullback_distance"],
                ),
            )
    # Ensure all symbols have an entry
    for sym in CURATED_SYMBOLS:
        out.setdefault(sym, None)
    return out


def _time_limit_pct(cell: dict | None) -> float | None:
    """Return TIME_LIMIT% for a cell. None when cell missing or has zero trades."""
    if cell is None:
        return None
    exit_reasons = cell.get("exit_reasons", {})
    n_trades = cell.get("n_trades", 0)
    if n_trades <= 0:
        return None
    tl = exit_reasons.get("TIME_LIMIT", 0)
    return 100.0 * tl / n_trades


def _check_halt_after_a(a_results: list[dict]) -> dict:
    """Pre-reg §10.4 halt-after-A: H1 (signal degenerate) OR H2 (TL horizon mismatch).

    H1: ≥HALT_H1_SYMBOLS_THRESHOLD in-data symbols have NO argmax cell with
        n_trades ≥ N_TRADES_MIN. Signal fires too rarely to evaluate.
    H2: ≥HALT_H2_SYMBOLS_THRESHOLD in-data symbols have TIME_LIMIT% > threshold
        on their argmax cell. TL=36h too short for trend-pullback frame.

    Either fires → halt=True. Reasons captured in `halt_reason` list.
    """
    argmax = _argmax_cell_per_symbol(a_results)

    # H1: signal degenerate — count symbols with NO eligible argmax cell.
    symbols_h1 = sorted(s for s, c in argmax.items() if c is None)
    h1_fires = len(symbols_h1) >= HALT_H1_SYMBOLS_THRESHOLD

    # H2: TL horizon mismatch — TIME_LIMIT% above threshold on argmax cell.
    per_symbol_tl_pct: dict[str, float | None] = {
        s: _time_limit_pct(c) for s, c in argmax.items()
    }
    symbols_h2 = sorted(
        s for s, p in per_symbol_tl_pct.items()
        if p is not None and p > HALT_H2_TL_PCT_THRESHOLD
    )
    h2_fires = len(symbols_h2) >= HALT_H2_SYMBOLS_THRESHOLD

    halt = h1_fires or h2_fires
    halt_reason: list[str] = []
    if h1_fires:
        halt_reason.append("H1_signal_degenerate")
    if h2_fires:
        halt_reason.append("H2_tl_horizon_mismatch")

    return {
        "halt": halt,
        "halt_reason": halt_reason,
        "halt_h1_symbols_threshold": HALT_H1_SYMBOLS_THRESHOLD,
        "halt_h2_tl_pct_threshold": HALT_H2_TL_PCT_THRESHOLD,
        "halt_h2_symbols_threshold": HALT_H2_SYMBOLS_THRESHOLD,
        "h1_n_symbols_no_eligible_cell": len(symbols_h1),
        "h1_symbols_no_eligible_cell": symbols_h1,
        "h2_n_symbols_over_tl_threshold": len(symbols_h2),
        "h2_symbols_over_tl_threshold": symbols_h2,
        "per_symbol_tl_pct": {
            s: (round(p, 2) if p is not None else None)
            for s, p in per_symbol_tl_pct.items()
        },
        "argmax_cell_per_symbol": {
            s: ({
                "sl": c["sl"], "be": c["be"], "pullback_distance": c["pullback_distance"],
                "n_trades": c["n_trades"], "net_pnl": c["net_pnl"],
                "exit_reasons": c["exit_reasons"],
            } if c is not None else None)
            for s, c in argmax.items()
        },
    }


def _build_signal_diagnostics(a_results: list[dict]) -> dict:
    """Per-(symbol, cell) signal-firing diagnostics for sub-window A.

    Useful for pre-sweep operator review when §10.4 H1 looks borderline.
    Surfaces signal frequency before the verdict tool's argmax aggregation.
    """
    by_symbol_n_trades: dict[str, list[int]] = {}
    for r in a_results:
        sym = r["symbol"]
        n = int(r.get("n_trades", 0))
        by_symbol_n_trades.setdefault(sym, []).append(n)

    diagnostics: dict[str, dict] = {}
    for sym in CURATED_SYMBOLS:
        n_list = by_symbol_n_trades.get(sym, [])
        if not n_list:
            diagnostics[sym] = {
                "n_cells": 0,
                "max_n_trades": 0,
                "min_n_trades": 0,
                "median_n_trades": 0,
                "n_cells_above_threshold": 0,
            }
            continue
        sorted_n = sorted(n_list)
        diagnostics[sym] = {
            "n_cells": len(sorted_n),
            "max_n_trades": int(sorted_n[-1]),
            "min_n_trades": int(sorted_n[0]),
            "median_n_trades": int(sorted_n[len(sorted_n) // 2]),
            "n_cells_above_threshold": int(sum(1 for n in sorted_n if n >= N_TRADES_MIN)),
        }
    return diagnostics


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True,
        ).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _save_json(path: Path, payload):
    """Write `payload` as JSON. NaN / Inf raise rather than serialize.

    The default `json.dump` emits `NaN` / `Infinity` tokens that are not valid
    JSON — `json.loads` accepts them but the standard does not, and downstream
    parsers (verdict calculator, audit doc consumers) may silently mishandle
    them. Failing here surfaces the upstream metric pollution at write time.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True, default=str, allow_nan=False)


def _summarize_worker_errors(results: list[dict]) -> str | None:
    """Aggregate the `error` field across sweep results.

    Returns a one-line summary if any worker reported an error, else None.
    Mirrors `tools/r1_signal_exit_sweep.py:_summarize_worker_errors` —
    `is not None` filter (#332 item 4 hardened pattern).
    """
    errors = [r.get("error") for r in results if r.get("error") is not None]
    if not errors:
        return None
    distinct = sorted(set(errors))
    return (
        f"[r3_sweep] {len(errors)} workers errored "
        f"({len(distinct)} distinct): {distinct}"
    )


def _emit_worker_error_summary(results: list[dict]) -> None:
    """Write `_summarize_worker_errors` output to sys.stderr if non-None.

    Mirrors `tools/r1_signal_exit_sweep.py:_emit_worker_error_summary` —
    extracted wiring point (#332 item 5 hardened pattern).
    """
    err_summary = _summarize_worker_errors(results)
    if err_summary:
        sys.stderr.write(err_summary + "\n")


def _run_jobs_parallel(jobs: list[dict], workers: int, label: str) -> list[dict]:
    """Run jobs in parallel via multiprocessing.Pool. Progress on stderr."""
    if not jobs:
        return []
    sys.stderr.write(f"[r3_sweep] {label}: {len(jobs)} jobs × {workers} workers...\n")
    t0 = time.monotonic()
    with Pool(workers) as pool:
        results = pool.map(_process_cell, jobs)
    elapsed = time.monotonic() - t0
    sys.stderr.write(f"[r3_sweep] {label}: completed in {elapsed:.1f}s\n")
    _emit_worker_error_summary(results)
    return results


def parse_args():
    p = argparse.ArgumentParser(description="R3 trend-pullback sweep harness.")
    p.add_argument("--window", choices=["A", "B", "C", "all"], default="all",
                   help="Which sub-window(s) to sweep (default: all).")
    p.add_argument("--baselines-only", action="store_true",
                   help="Run only the 30 baseline cells (no sweep).")
    p.add_argument("--skip-baselines", action="store_true",
                   help="Skip baselines (assume baseline_pre_trend_pullback.json exists).")
    p.add_argument("--workers", type=int, default=min(8, cpu_count()),
                   help="Parallel workers (default: min(8, cpu_count)).")
    p.add_argument("--smoke", action="store_true",
                   help="Smoke mode: 1 cell only (1 symbol × 1 sub-win × 1 grid point).")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    app_config_path = str(REPO_ROOT / "config.defaults.json")

    # Smoke mode — quick validation of harness wiring.
    if args.smoke:
        sys.stderr.write("[r3_sweep] SMOKE MODE: 1 cell only\n")
        smoke_job = {
            "symbol": "UNIUSDT",  # has the most trades per R1 pre-R1 query
            "sub_window": "C",
            "sim_start_iso": SUB_WINDOWS["C"][0],
            "sim_end_iso": SUB_WINDOWS["C"][1],
            "cutoff_iso": CUTOFF_ISO,
            "sl": 1.0, "be": 1.5, "pullback_distance": 0.5,
            "trend_pullback_enabled": True,
            "app_config_path": app_config_path,
        }
        result = _process_cell(smoke_job)
        _save_json(OUTPUT_DIR / "smoke_test.json", result)
        sys.stderr.write(f"[r3_sweep] SMOKE result: {json.dumps(result, indent=2, default=str)}\n")
        return 0

    # Coverage report (writes the §3 usable_bars table for derivation_audit).
    sys.stderr.write("[r3_sweep] computing per-(symbol, sub-window) coverage...\n")
    coverage_by_window = {w: _coverage_for_window(w, app_config_path) for w in SUB_WINDOWS}
    _save_json(OUTPUT_DIR / "coverage.json", coverage_by_window)

    # Phase 1 — Baselines (30 cells).
    if not args.skip_baselines:
        baseline_jobs = _build_baseline_jobs(app_config_path)
        baseline_results = _run_jobs_parallel(baseline_jobs, args.workers, "baselines")
        _save_json(OUTPUT_DIR / "baseline_pre_trend_pullback.json", baseline_results)
    else:
        sys.stderr.write("[r3_sweep] skipping baselines (--skip-baselines)\n")
        baseline_path = OUTPUT_DIR / "baseline_pre_trend_pullback.json"
        if not baseline_path.exists():
            sys.stderr.write(f"[r3_sweep] ERROR: --skip-baselines but {baseline_path} missing\n")
            return 1

    if args.baselines_only:
        sys.stderr.write("[r3_sweep] --baselines-only: done.\n")
        _save_json(OUTPUT_DIR / "manifest.json", _build_manifest(args, halt_diag=None))
        return 0

    # Phase 2 — Sub-window A.
    halt_diag = None
    if args.window in ("A", "all"):
        a_jobs = _build_sweep_jobs("A", app_config_path)
        a_results = _run_jobs_parallel(a_jobs, args.workers, "sweep A")
        _save_json(OUTPUT_DIR / "sweep_results_A.json", a_results)

        # Signal diagnostics (per-symbol firing frequency in sub-window A).
        signal_diag = _build_signal_diagnostics(a_results)
        _save_json(OUTPUT_DIR / "signal_diagnostics.json", signal_diag)

        # Pre-reg §10.4 halt-after-A check (H1 OR H2).
        halt_diag = _check_halt_after_a(a_results)
        _save_json(OUTPUT_DIR / "halt_after_a_diagnostic.json", halt_diag)
        if halt_diag["halt"]:
            reasons = halt_diag["halt_reason"]
            details_lines = []
            if "H1_signal_degenerate" in reasons:
                details_lines.append(
                    f"H1 (signal degenerate): {halt_diag['h1_n_symbols_no_eligible_cell']} "
                    f"in-data symbols had NO argmax cell with n_trades ≥ {N_TRADES_MIN} "
                    f"(threshold: ≥{HALT_H1_SYMBOLS_THRESHOLD}).\n"
                    f"  Symbols: {halt_diag['h1_symbols_no_eligible_cell']}\n"
                )
            if "H2_tl_horizon_mismatch" in reasons:
                details_lines.append(
                    f"H2 (TL horizon mismatch): {halt_diag['h2_n_symbols_over_tl_threshold']} "
                    f"in-data symbols had TIME_LIMIT% > {HALT_H2_TL_PCT_THRESHOLD}% on argmax cell "
                    f"(threshold: ≥{HALT_H2_SYMBOLS_THRESHOLD}).\n"
                    f"  Symbols: {halt_diag['h2_symbols_over_tl_threshold']}\n"
                )
            txt = (
                f"§10.4 HALT FIRED at {datetime.now(timezone.utc).isoformat()}\n"
                f"Reasons: {reasons}\n\n"
                + "".join(details_lines)
                + "\nAborting sweep windows B+C. Operator review required per pre-reg §10.4.\n"
                "Verdict tool will classify as R3_FAIL (signal_degenerate or clean variant) "
                "per pre-reg §4.2 + §4.6 asymmetric halt-guard.\n"
            )
            (OUTPUT_DIR / "halt_after_a.txt").write_text(txt)
            _save_json(OUTPUT_DIR / "manifest.json", _build_manifest(args, halt_diag=halt_diag))
            sys.stderr.write(txt)
            return 2

    # Phase 3 — Sub-windows B + C.
    if args.window in ("B", "all"):
        b_jobs = _build_sweep_jobs("B", app_config_path)
        b_results = _run_jobs_parallel(b_jobs, args.workers, "sweep B")
        _save_json(OUTPUT_DIR / "sweep_results_B.json", b_results)

    if args.window in ("C", "all"):
        c_jobs = _build_sweep_jobs("C", app_config_path)
        c_results = _run_jobs_parallel(c_jobs, args.workers, "sweep C")
        _save_json(OUTPUT_DIR / "sweep_results_C.json", c_results)

    # Manifest at the end.
    _save_json(OUTPUT_DIR / "manifest.json", _build_manifest(args, halt_diag=halt_diag))
    sys.stderr.write("[r3_sweep] complete.\n")
    return 0


def _build_manifest(args, halt_diag) -> dict:
    return {
        "harness": "tools.r3_trend_pullback_sweep",
        "spec_ref": "docs/superpowers/plans/2026-05-13-r3-trend-pullback-pre-reg.md",
        "ran_at_iso": datetime.now(timezone.utc).isoformat(),
        "code_commit": _git_commit(),
        "cutoff_iso": CUTOFF_ISO,
        "sub_windows": {w: {"start_iso": s, "end_iso": e} for w, (s, e) in SUB_WINDOWS.items()},
        "sweep_grid": {
            "atr_sl_mult": list(GRID_SL),
            "atr_be_mult": list(GRID_BE),
            "pullback_distance": list(GRID_PULLBACK_DISTANCE),
            "cells_per_symbol_per_window": (
                len(GRID_SL) * len(GRID_BE) * len(GRID_PULLBACK_DISTANCE)
            ),
        },
        "fixed_params": {
            "lrc_exit_threshold": LRC_EXIT_THRESHOLD_FIXED,
            "uniform_time_limit_hours": UNIFORM_TIME_LIMIT_HOURS,
            "dynamic_exit_enabled": True,
        },
        "symbols": list(CURATED_SYMBOLS),
        "currently_bankrupt_symbols": sorted(CURRENTLY_BANKRUPT_SYMBOLS),
        "n_trades_min": N_TRADES_MIN,
        "usable_bars_min": USABLE_BARS_MIN,
        "halt_after_a_h1_symbols_threshold": HALT_H1_SYMBOLS_THRESHOLD,
        "halt_after_a_h2_tl_pct_threshold": HALT_H2_TL_PCT_THRESHOLD,
        "halt_after_a_h2_symbols_threshold": HALT_H2_SYMBOLS_THRESHOLD,
        "halt_after_a_fired": bool(halt_diag and halt_diag.get("halt")),
        "halt_after_a_reasons": (halt_diag.get("halt_reason", []) if halt_diag else []),
        "leakage_check": {
            "all_sub_windows_below_cutoff": all(
                _iso_to_utc_dt(end_iso) <= _iso_to_utc_dt(CUTOFF_ISO)
                for _, end_iso in SUB_WINDOWS.values()
            ),
            "method": (
                "cutoff drops bars >= cutoff in run_backtest_with_params; "
                "sub-windows independently constrained below cutoff."
            ),
        },
        "cli_args": vars(args),
    }


if __name__ == "__main__":
    raise SystemExit(main())
