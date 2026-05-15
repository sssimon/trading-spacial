#!/usr/bin/env python3
"""Signal calibration Phase 3 + Phase 4 sweep runner (epic C Phase 1, module 4 of 4).

Pre-reg: docs/superpowers/plans/2026-05-15-signal-calibration-pre-reg.md

Runs the Phase 3 sweep (8 + 32 cells over Window A with A1 subset {5,10,20})
and the Phase 4 walk-forward sweep (17 + 68 cells over Windows B + C).
Conditional on Phase 2 verdict = A_DETECTED (operator-gated via --phase). The
--smoke flag runs a single-cell wiring check (~5-30s wall-clock) without
touching the full sweep.

## Sweep structure (pre-reg §2.5)

Phase 3 primary:        8 (Window A × A1 × vol=30%)
Phase 3 sensitivity:   32 (8 × A × A1 × 4 vol_targets)
Phase 4 primary:       17 (8 in B + 9 in C × A1 × vol=30%)
Phase 4 sensitivity:   68 (17 × 4 vol_targets)

## Halt detection (pre-reg §10.4)

H-C3 (zero-tolerance bankruptcy) and H-C4 (win_rate < 50% baseline in ≥4/8 cells)
are evaluated by `tools/signal_calibration_verdict.py::classify_phase3_verdict`
post-sweep. The sweep itself unconditionally writes halt_diagnostic.json.

## Output (data/retune/2026-05-15-signal-calibration/)

  Phase 3:
    sweep_primary_A.json
    sweep_sensitivity_A.json
    phase3_verdict.json
  Phase 4:
    walkforward_primary_{B,C}.json
    walkforward_sensitivity_{B,C}.json
    walkforward_verdict.json
  Always:
    halt_diagnostic.json
    manifest.json
    smoke_test.json  (only if --smoke)

## Usage

  python tools/signal_calibration_sweep.py --phase 3        # Phase 3 only
  python tools/signal_calibration_sweep.py --phase 4        # Phase 4 only
  python tools/signal_calibration_sweep.py --smoke          # 1-cell harness check

Exit codes:
  0 — clean completion or halt fired as expected
  1 — missing inputs / harness wiring error

## A1 wiring caveat

A1 subset = (5, 10, 20) is propagated through
`cfg["regime_allocation"]["lookbacks_subset"]`. The current backtest.py path
reads ZARATTINI_LOOKBACKS by default; whether it honors `lookbacks_subset` is
a separate Phase 3 execution PR scope per pre-reg §6 (backtest.py out of
Phase 1 scope). Phase 1 ships the harness; Phase 3 execution PR validates
A1 propagation with evidence.

Copy-modified from tools/regime_allocation_sweep.py per Q-PR2 operator lock.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from datetime import datetime, timezone
from multiprocessing import Pool, cpu_count
from pathlib import Path
from typing import Final

# ─────────────────────────────────────────────────────────────────────────────
# Constants — pre-reg §3 + §8 carry-forward + Q-PR6 A1 lock
# ─────────────────────────────────────────────────────────────────────────────

WARMUP_DAILY_BARS: Final[int] = 390
PRIMARY_VOL_TARGET: Final[float] = 0.30
SENSITIVITY_VOL_TARGETS: Final[tuple[float, ...]] = (0.25, 0.30, 0.35, 0.40)

# Pre-reg Q-PR6 — A1 default intervention subset.
A1_SUBSET: Final[tuple[int, ...]] = (5, 10, 20)

# Pre-reg §3 — Phase 3 = Window A; Phase 4 = Windows B + C.
SUB_WINDOWS: Final[dict[str, tuple[str, str]]] = {
    "A": ("2022-04-01T00:00:00+00:00", "2022-07-01T00:00:00+00:00"),
    "B": ("2023-04-01T00:00:00+00:00", "2023-07-01T00:00:00+00:00"),
    "C": ("2025-01-30T00:00:00+00:00", "2025-04-30T00:00:00+00:00"),
}

CUTOFF_ISO: Final[str] = "2025-04-30T00:00:00+00:00"

# Pre-reg §3 + §5.1 — coverage table.
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

N_TRADES_MIN_FOR_ELIGIBILITY: Final[int] = 5
HALT_FRACTION_THRESHOLD: Final[float] = 0.75

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
OUTPUT_DIR: Final[Path] = (
    REPO_ROOT / "data" / "retune" / "2026-05-15-signal-calibration"
)
APP_CONFIG_PATH_DEFAULT: Final[Path] = REPO_ROOT / "config.defaults.json"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_FETCH_RETRY_MAX_ATTEMPTS: Final[int] = 6
_FETCH_RETRY_BASE_BACKOFF_SEC: Final[float] = 3.0
_PROFIT_FACTOR_INF_SENTINEL: Final[float] = 99999.0


# ─────────────────────────────────────────────────────────────────────────────
# Helpers (copy-modified from tools/regime_allocation_sweep.py)
# ─────────────────────────────────────────────────────────────────────────────


def _iso_to_utc_dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(timezone.utc)


def _halt_count_threshold(n_in_coverage: int) -> int:
    return math.ceil(n_in_coverage * HALT_FRACTION_THRESHOLD)


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True,
        ).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _save_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True, default=str, allow_nan=False)


def _finite_or(value, fallback: float) -> float:
    v = float(value)
    return v if math.isfinite(v) else fallback


def _normalize_win_rate_to_fraction(value: float) -> float:
    """backtest.calculate_metrics returns win_rate as percent (0-100); the verdict
    layer (`WIN_RATE_FLOOR = 0.30`) expects fraction. Normalize at worker boundary
    so units are consistent across the pipeline.

    Boundary case `win_rate == 1.0`: ambiguous (could be "1%" or "100%"). With
    `>` strict the value is preserved as-is, treating 1.0 as fraction. This is
    safer than the inverse (treating 1.0 as percent would map 1% to fraction
    0.01, FAILing the WIN_RATE_FLOOR check spuriously). Empirically backtest
    returns either 0.0 (no trades won) or values like 50.0, 100.0 — boundary
    case `win_rate == 1.0` exact is degenerate and unlikely.
    """
    return value / 100.0 if value > 1.0 else value


def _get_cached_data_with_retry(symbol, timeframe, start_date):
    from backtest import get_cached_data
    from data.providers.base import AllProvidersFailedError

    last_err = None
    for attempt in range(1, _FETCH_RETRY_MAX_ATTEMPTS + 1):
        try:
            return get_cached_data(symbol, timeframe, start_date=start_date)
        except AllProvidersFailedError as exc:
            last_err = exc
            if attempt < _FETCH_RETRY_MAX_ATTEMPTS:
                wait = _FETCH_RETRY_BASE_BACKOFF_SEC * (2 ** (attempt - 1))
                time.sleep(wait)
    raise last_err


# ─────────────────────────────────────────────────────────────────────────────
# Cell worker — single parameterized worker for Phase 3 and Phase 4
# ─────────────────────────────────────────────────────────────────────────────


def _process_sweep_cell(args: dict) -> dict:
    """Worker: one (symbol, sub_window, vol_target) cell with A1 subset.

    Propagates A1 via `cfg["regime_allocation"]["lookbacks_subset"]`. backtest.py
    honor of that key is out of Phase 1 scope (Phase 3 execution PR).
    """
    import pandas as pd
    from dateutil.relativedelta import relativedelta

    from backtest import calculate_metrics, simulate_strategy

    symbol = args["symbol"]
    window_id = args["sub_window"]
    vol_target = float(args["vol_target"])
    sim_start = _iso_to_utc_dt(args["sim_start_iso"])
    sim_end = _iso_to_utc_dt(args["sim_end_iso"])
    cutoff = _iso_to_utc_dt(args["cutoff_iso"])
    lookbacks_subset = tuple(args["lookbacks_subset"])
    app_config_path = args["app_config_path"]

    sim_start = sim_start.replace(tzinfo=None) if sim_start.tzinfo else sim_start
    sim_end = sim_end.replace(tzinfo=None) if sim_end.tzinfo else sim_end

    with open(app_config_path) as f:
        app_config = json.load(f)

    cfg = dict(app_config)
    ra_block = dict(cfg.get("regime_allocation", {}))
    ra_block["enabled"] = True
    ra_block["portfolio_vol_target"] = vol_target
    ra_block["lookbacks_subset"] = list(lookbacks_subset)  # JSON-serializable
    cfg["regime_allocation"] = ra_block

    empty_ohlcv = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    err = None
    trades: list[dict] = []
    metrics: dict = {}
    df1d = pd.DataFrame()

    try:
        df1h = _get_cached_data_with_retry(
            symbol, "1h", sim_start - relativedelta(months=14),
        )
        df1d = _get_cached_data_with_retry(
            symbol, "1d", sim_start - relativedelta(months=14),
        )

        cutoff_naive = cutoff.replace(tzinfo=None) if cutoff.tzinfo else cutoff
        if not df1h.empty:
            idx = df1h.index.tz_localize(None) if df1h.index.tz is not None else df1h.index
            df1h = df1h[idx < cutoff_naive]
        if not df1d.empty:
            idx = df1d.index.tz_localize(None) if df1d.index.tz is not None else df1d.index
            df1d = df1d[idx < cutoff_naive]

        if df1d.empty or len(df1d) < WARMUP_DAILY_BARS:
            err = f"insufficient daily bars: {len(df1d)} < {WARMUP_DAILY_BARS}"
        else:
            trades, equity_curve = simulate_strategy(
                df1h=df1h, df4h=empty_ohlcv, df5m=empty_ohlcv,
                df1d=df1d, symbol=symbol,
                sim_start=sim_start, sim_end=sim_end,
                cfg=cfg,
                enable_slippage=True, enable_spread=True,
                enable_fees=True, enable_funding=True,
            )
            metrics = calculate_metrics(trades, equity_curve) if trades else {
                "total_trades": 0, "net_pnl": 0.0,
                "profit_factor": 0.0, "win_rate": 0.0,
                "max_drawdown_pct": 0.0, "bankruptcy_count": 0,
                "clamped_trade_count": 0,
            }
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"

    win_rate_fraction = _normalize_win_rate_to_fraction(
        _finite_or(metrics.get("win_rate", 0.0), 0.0)
    )

    out = {
        "symbol": symbol,
        "sub_window": window_id,
        "vol_target": vol_target,
        "lookbacks_subset": list(lookbacks_subset),
        "n_trades": int(metrics.get("total_trades", 0)),
        "net_pnl_usd": round(float(metrics.get("net_pnl", 0.0)), 4),
        "profit_factor": round(
            _finite_or(metrics.get("profit_factor", 0.0), _PROFIT_FACTOR_INF_SENTINEL), 4,
        ),
        "win_rate": round(win_rate_fraction, 4),
        "max_drawdown_pct": round(
            _finite_or(metrics.get("max_drawdown_pct", 0.0), 0.0), 4,
        ),
        "bankruptcy_count": int(metrics.get("bankruptcy_count", 0)),
        "clamped_trade_count": int(metrics.get("clamped_trade_count", 0)),
        "insufficient_data": (
            err is None
            and int(metrics.get("total_trades", 0)) < N_TRADES_MIN_FOR_ELIGIBILITY
        ),
    }
    if err is not None:
        out["error"] = err
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Job builders
# ─────────────────────────────────────────────────────────────────────────────


def build_phase3_primary_jobs(app_config_path: str) -> list[dict]:
    """Pre-reg §2.5 Phase 3 primary — 8 cells × Window A × A1 × vol=30%."""
    start_iso, end_iso = SUB_WINDOWS["A"]
    return [
        {
            "symbol": sym,
            "sub_window": "A",
            "vol_target": PRIMARY_VOL_TARGET,
            "sim_start_iso": start_iso,
            "sim_end_iso": end_iso,
            "cutoff_iso": CUTOFF_ISO,
            "lookbacks_subset": list(A1_SUBSET),
            "app_config_path": app_config_path,
        }
        for sym in COVERAGE_BY_WINDOW["A"]
    ]


def build_phase3_sensitivity_jobs(app_config_path: str) -> list[dict]:
    """Pre-reg §2.5 Phase 3 sensitivity — 8 × A × 4 vol_target × A1 = 32 cells."""
    start_iso, end_iso = SUB_WINDOWS["A"]
    return [
        {
            "symbol": sym,
            "sub_window": "A",
            "vol_target": float(vt),
            "sim_start_iso": start_iso,
            "sim_end_iso": end_iso,
            "cutoff_iso": CUTOFF_ISO,
            "lookbacks_subset": list(A1_SUBSET),
            "app_config_path": app_config_path,
        }
        for sym in COVERAGE_BY_WINDOW["A"]
        for vt in SENSITIVITY_VOL_TARGETS
    ]


def build_phase4_primary_jobs(app_config_path: str) -> list[dict]:
    """Pre-reg §2.5 Phase 4 primary — (8 in B + 9 in C) × A1 × vol=30% = 17 cells."""
    jobs = []
    for window_id in ("B", "C"):
        start_iso, end_iso = SUB_WINDOWS[window_id]
        for sym in COVERAGE_BY_WINDOW[window_id]:
            jobs.append({
                "symbol": sym,
                "sub_window": window_id,
                "vol_target": PRIMARY_VOL_TARGET,
                "sim_start_iso": start_iso,
                "sim_end_iso": end_iso,
                "cutoff_iso": CUTOFF_ISO,
                "lookbacks_subset": list(A1_SUBSET),
                "app_config_path": app_config_path,
            })
    return jobs


def build_phase4_sensitivity_jobs(app_config_path: str) -> list[dict]:
    """Pre-reg §2.5 Phase 4 sensitivity — 17 × 4 vol_target = 68 cells."""
    jobs = []
    for window_id in ("B", "C"):
        start_iso, end_iso = SUB_WINDOWS[window_id]
        for sym in COVERAGE_BY_WINDOW[window_id]:
            for vt in SENSITIVITY_VOL_TARGETS:
                jobs.append({
                    "symbol": sym,
                    "sub_window": window_id,
                    "vol_target": float(vt),
                    "sim_start_iso": start_iso,
                    "sim_end_iso": end_iso,
                    "cutoff_iso": CUTOFF_ISO,
                    "lookbacks_subset": list(A1_SUBSET),
                    "app_config_path": app_config_path,
                })
    return jobs


def build_smoke_job(app_config_path: str) -> dict:
    """--smoke: single cell BTC × Window A × vol=30% × A1 (~5-30s wall-clock)."""
    start_iso, end_iso = SUB_WINDOWS["A"]
    return {
        "symbol": "BTCUSDT",
        "sub_window": "A",
        "vol_target": PRIMARY_VOL_TARGET,
        "sim_start_iso": start_iso,
        "sim_end_iso": end_iso,
        "cutoff_iso": CUTOFF_ISO,
        "lookbacks_subset": list(A1_SUBSET),
        "app_config_path": app_config_path,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation: build window summary for Phase 4 verdict input
# ─────────────────────────────────────────────────────────────────────────────


def aggregate_window_summary(cells: list[dict], window_id: str) -> dict:
    """For Phase 4 verdict, count `n_pass` over in-coverage cells in a window.

    PRIMARY conditions per cell: n_trades ≥ 5 ∧ no bankruptcies ∧ win_rate ≥ 30%.
    """
    in_cov = set(COVERAGE_BY_WINDOW[window_id])
    eligible = [
        c for c in cells
        if c.get("sub_window") == window_id
        and c.get("symbol") in in_cov
        and abs(float(c.get("vol_target", 0.0)) - PRIMARY_VOL_TARGET) < 1e-9
    ]
    n_pass = sum(
        1 for c in eligible
        if c.get("n_trades", 0) >= 5
        and c.get("bankruptcy_count", 0) == 0
        and c.get("win_rate", 0.0) >= 0.30
    )
    return {
        "window_id": window_id,
        "n_pass": n_pass,
        "n_in_coverage": len(in_cov),
        "n_cells_evaluated": len(eligible),
    }


def aggregate_sensitivity_per_window(
    sensitivity_cells: list[dict],
    window_id: str,
) -> dict:
    """For Phase 4 sensitivity verdict input — count vol_target PASS in this window."""
    in_cov = set(COVERAGE_BY_WINDOW[window_id])
    threshold = _halt_count_threshold(len(in_cov))

    by_vt: dict[float, list[dict]] = {}
    for c in sensitivity_cells:
        if c.get("sub_window") != window_id or c.get("symbol") not in in_cov:
            continue
        vt = float(c.get("vol_target", 0.0))
        by_vt.setdefault(vt, []).append(c)

    n_pass = 0
    for cells_at_vt in by_vt.values():
        n_cells_pass = sum(
            1 for c in cells_at_vt
            if c.get("n_trades", 0) >= 5
            and c.get("bankruptcy_count", 0) == 0
            and c.get("win_rate", 0.0) >= 0.30
        )
        if n_cells_pass >= threshold:
            n_pass += 1
    return {
        "n_pass_out_of_4": n_pass,
        "n_available_out_of_4": len(by_vt),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--phase", type=int, choices=(3, 4), default=3,
        help="Sweep phase to run (3 = Window A primary+sens; 4 = walk-forward B+C)",
    )
    p.add_argument(
        "--smoke", action="store_true",
        help="Single-cell wiring check (BTC × Window A × A1 × vol=30%%, ~5-30s)",
    )
    p.add_argument(
        "--skip-sensitivity", action="store_true",
        help="Only run primary pass (skip 4 vol_target sweep)",
    )
    p.add_argument(
        "--app-config", default=str(APP_CONFIG_PATH_DEFAULT),
        help="Path to config.defaults.json (default: repo root)",
    )
    p.add_argument(
        "--workers", type=int, default=min(8, cpu_count()),
        help="Multiprocessing pool size (default: min(8, cpu_count()))",
    )
    return p.parse_args()


def _run_smoke(args) -> int:
    """1-cell wiring check. Writes smoke_test.json with stdout summary."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    job = build_smoke_job(args.app_config)
    print(f"[smoke] running 1 cell: {job['symbol']} × Window {job['sub_window']} "
          f"× vol_target={job['vol_target']} × A1={tuple(job['lookbacks_subset'])}")

    t_start = time.monotonic()
    result = _process_sweep_cell(job)
    elapsed = time.monotonic() - t_start

    smoke_payload = {
        "schema_version": 1,
        "spec_ref": "docs/superpowers/plans/2026-05-15-signal-calibration-pre-reg.md",
        "phase": "smoke",
        "cell_args": {k: v for k, v in job.items() if k != "app_config_path"},
        "cell_result": result,
        "elapsed_seconds": round(elapsed, 2),
        "code_commit": _git_commit(),
        "harness_wiring_ok": "error" not in result,
    }
    _save_json(OUTPUT_DIR / "smoke_test.json", smoke_payload)

    status = "OK" if smoke_payload["harness_wiring_ok"] else "FAIL"
    print(f"[smoke] {status} in {elapsed:.1f}s — n_trades={result.get('n_trades', 0)}, "
          f"net_pnl=${result.get('net_pnl_usd', 0):.2f}, "
          f"error={result.get('error', 'none')}")
    return 0 if smoke_payload["harness_wiring_ok"] else 1


def _run_phase3(args) -> int:
    from tools.signal_calibration_verdict import classify_phase3_verdict

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    primary_jobs = build_phase3_primary_jobs(args.app_config)
    print(f"[phase3] primary: {len(primary_jobs)} cells")
    t_start = time.monotonic()
    with Pool(processes=args.workers) as pool:
        primary_results = pool.map(_process_sweep_cell, primary_jobs)
    print(f"[phase3] primary done in {time.monotonic() - t_start:.1f}s")
    _save_json(OUTPUT_DIR / "sweep_primary_A.json", primary_results)

    sensitivity_results: list[dict] = []
    if not args.skip_sensitivity:
        sens_jobs = build_phase3_sensitivity_jobs(args.app_config)
        print(f"[phase3] sensitivity: {len(sens_jobs)} cells")
        t_start = time.monotonic()
        with Pool(processes=args.workers) as pool:
            sensitivity_results = pool.map(_process_sweep_cell, sens_jobs)
        print(f"[phase3] sensitivity done in {time.monotonic() - t_start:.1f}s")
    _save_json(OUTPUT_DIR / "sweep_sensitivity_A.json", sensitivity_results)

    # Load Phase 2 win_rates as Phase 3 baseline (for H-C4 check)
    phase2_diag_path = OUTPUT_DIR / "phase2_diagnostic.json"
    baseline_win_rates: dict[str, float] = {}
    if phase2_diag_path.exists():
        with open(phase2_diag_path) as f:
            phase2_cells = json.load(f)
        for c in phase2_cells:
            baseline_win_rates[c["symbol"]] = float(c.get("win_rate", 0.0))

    verdict_result = classify_phase3_verdict(
        primary_results, sensitivity_results,
        baseline_win_rates=baseline_win_rates,
        in_coverage_count=len(COVERAGE_BY_WINDOW["A"]),
        halt=False,  # H-C3/H-C4 checked internally by classifier
    )
    _save_json(OUTPUT_DIR / "phase3_verdict.json", verdict_result)

    print(f"[phase3] verdict: {verdict_result['verdict']} "
          f"(n_primary_pass={verdict_result.get('n_primary_pass', 0)}/8, "
          f"sens_pass={verdict_result.get('n_sensitivity_vol_target_pass', 0)}/4)")
    return 0


def _run_phase4(args) -> int:
    from tools.signal_calibration_verdict import classify_phase4_verdict

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    primary_jobs = build_phase4_primary_jobs(args.app_config)
    print(f"[phase4] primary: {len(primary_jobs)} cells (B + C walk-forward)")
    t_start = time.monotonic()
    with Pool(processes=args.workers) as pool:
        primary_results = pool.map(_process_sweep_cell, primary_jobs)
    print(f"[phase4] primary done in {time.monotonic() - t_start:.1f}s")
    # Split by window for spec-aligned output naming
    by_window: dict[str, list[dict]] = {"B": [], "C": []}
    for r in primary_results:
        by_window.setdefault(r["sub_window"], []).append(r)
    _save_json(OUTPUT_DIR / "walkforward_primary_B.json", by_window["B"])
    _save_json(OUTPUT_DIR / "walkforward_primary_C.json", by_window["C"])

    sensitivity_results: list[dict] = []
    if not args.skip_sensitivity:
        sens_jobs = build_phase4_sensitivity_jobs(args.app_config)
        print(f"[phase4] sensitivity: {len(sens_jobs)} cells")
        t_start = time.monotonic()
        with Pool(processes=args.workers) as pool:
            sensitivity_results = pool.map(_process_sweep_cell, sens_jobs)
        print(f"[phase4] sensitivity done in {time.monotonic() - t_start:.1f}s")
    sens_by_window: dict[str, list[dict]] = {"B": [], "C": []}
    for r in sensitivity_results:
        sens_by_window.setdefault(r["sub_window"], []).append(r)
    _save_json(OUTPUT_DIR / "walkforward_sensitivity_B.json", sens_by_window["B"])
    _save_json(OUTPUT_DIR / "walkforward_sensitivity_C.json", sens_by_window["C"])

    window_summaries = {
        "B": aggregate_window_summary(primary_results, "B"),
        "C": aggregate_window_summary(primary_results, "C"),
    }
    sensitivity_per_window = {
        "B": aggregate_sensitivity_per_window(sensitivity_results, "B"),
        "C": aggregate_sensitivity_per_window(sensitivity_results, "C"),
    }
    verdict_result = classify_phase4_verdict(
        window_summaries=window_summaries,
        sensitivity_per_window=sensitivity_per_window,
        halt=False,
    )
    _save_json(OUTPUT_DIR / "walkforward_verdict.json", verdict_result)

    print(f"[phase4] verdict: {verdict_result['verdict']} "
          f"(b_pass={verdict_result.get('b_pass')}, c_pass={verdict_result.get('c_pass')})")
    return 0


def main() -> int:
    args = _parse_args()
    if args.smoke:
        return _run_smoke(args)
    if args.phase == 3:
        return _run_phase3(args)
    if args.phase == 4:
        return _run_phase4(args)
    sys.stderr.write(f"Unsupported phase: {args.phase}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
