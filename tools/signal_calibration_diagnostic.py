#!/usr/bin/env python3
"""Signal calibration Phase 2 diagnostic runner (epic C Phase 1, module 3 of 4).

Pre-reg: docs/superpowers/plans/2026-05-15-signal-calibration-pre-reg.md

Runs the Phase 2 diagnostic over Window A only with equal-weight Donchian-9
baseline (heredado #338 §8.1 + §8.4) + observability emission per cell. Used
to distinguish H-signal-A (dilution) from H-signal-B (no-firing-enough).

## Sweep structure (pre-reg §2.5, Phase 2)
  Window A only: 8 in-coverage cells × baseline equal-weight × vol_target=30%

## Halt detection (pre-reg §10.4 + §4.1 row 4)
  Universal bankruptcy: ≥6/8 in-coverage cells bankrupt → halt=True
  Triggers verdict = PHASE_2_INSUFFICIENT_DATA via classify_phase2_verdict.

## Output (data/retune/2026-05-15-signal-calibration/)
  - phase2_diagnostic.json                 # per-cell metrics + observability
  - observability_<symbol>_<window>.json   # 8 sidecar files
  - phase2_verdict.json                    # verdict via classify_phase2_verdict
  - halt_diagnostic.json                   # ALWAYS written; halt=True when fired
  - manifest.json                          # cutoff, commit, sub-window, coverage

## Usage
  python tools/signal_calibration_diagnostic.py

Exit codes:
  0 — clean completion (or halt fired as expected)
  1 — missing inputs / harness wiring error

Copy-modified from tools/regime_allocation_sweep.py per pre-reg §11 + Q-PR2
operator lock: helpers and constants duplicated rather than imported, to keep
epic C and epic D independent at the module boundary.
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
# Constants — pre-reg §3 + §8 carry-forward from #338 + epic C locks
# ─────────────────────────────────────────────────────────────────────────────

WARMUP_DAILY_BARS: Final[int] = 390
PRIMARY_VOL_TARGET: Final[float] = 0.30

# Pre-reg §3 — Phase 2 runs only Window A.
WINDOW_A_ID: Final[str] = "A"
WINDOW_A_START_ISO: Final[str] = "2022-04-01T00:00:00+00:00"
WINDOW_A_END_ISO: Final[str] = "2022-07-01T00:00:00+00:00"

CUTOFF_ISO: Final[str] = "2025-04-30T00:00:00+00:00"

# Pre-reg §3 + §5.1 — Window A coverage (8 symbols; PENDLE+JUP excluded).
WINDOW_A_COVERAGE: Final[tuple[str, ...]] = (
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
    "UNIUSDT", "XLMUSDT", "RUNEUSDT",
)

N_TRADES_MIN_FOR_ELIGIBILITY: Final[int] = 5

# Pre-reg §10.4 + §4.1 row 4 — Phase 2 halt threshold (universal bankruptcy ≥75% in coverage).
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
# Helpers (copy-modified from regime_allocation_sweep.py per Q-PR2)
# ─────────────────────────────────────────────────────────────────────────────


def _iso_to_utc_dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(timezone.utc)


def _halt_count_threshold(n_in_coverage: int) -> int:
    """`ceil(n × 0.75)` — same rounding as #338 §10.4."""
    return math.ceil(n_in_coverage * HALT_FRACTION_THRESHOLD)


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True,
        ).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _save_json(path: Path, payload):
    """Write `payload` as JSON. NaN/Inf raise rather than serialize."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True, default=str, allow_nan=False)


def _finite_or(value, fallback: float) -> float:
    v = float(value)
    return v if math.isfinite(v) else fallback


def _get_cached_data_with_retry(symbol, timeframe, start_date):
    """get_cached_data with exponential backoff. Multiprocessing-safe (re-imports)."""
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
# Phase 2 worker — sim + ensemble history + observability emit
# ─────────────────────────────────────────────────────────────────────────────


def _process_phase2_cell(args: dict) -> dict:
    """Worker: one (symbol, Window A) Phase 2 cell.

    Runs:
      1. Loads df1h + df1d with warmup.
      2. simulate_strategy with `cfg.regime_allocation.enabled = True` + baseline
         9 lookbacks (no A1 subset). Returns trades + metrics + per-cell P&L.
      3. Resamples df1h to daily + runs compute_ensemble_history (ZARATTINI 9).
      4. Slices history to [sim_start, sim_end) + emits observability metrics.
      5. Returns cell with metrics + nested `observability` sub-dict.
    """
    import pandas as pd
    from dateutil.relativedelta import relativedelta

    from backtest import calculate_metrics, simulate_strategy
    from strategy.donchian_ensemble import (
        compute_ensemble_history,
        emit_observability_metrics,
    )

    symbol = args["symbol"]
    sim_start = _iso_to_utc_dt(args["sim_start_iso"])
    sim_end = _iso_to_utc_dt(args["sim_end_iso"])
    cutoff = _iso_to_utc_dt(args["cutoff_iso"])
    app_config_path = args["app_config_path"]

    sim_start = sim_start.replace(tzinfo=None) if sim_start.tzinfo else sim_start
    sim_end = sim_end.replace(tzinfo=None) if sim_end.tzinfo else sim_end

    with open(app_config_path) as f:
        app_config = json.load(f)

    cfg = dict(app_config)
    ra_block = dict(cfg.get("regime_allocation", {}))
    ra_block["enabled"] = True
    ra_block["portfolio_vol_target"] = PRIMARY_VOL_TARGET
    cfg["regime_allocation"] = ra_block

    empty_ohlcv = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    err = None
    trades: list[dict] = []
    metrics: dict = {}
    df1d = pd.DataFrame()
    observability: dict = {}

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

            history_df = compute_ensemble_history(
                closes=df1d["close"], highs=df1d["high"], lows=df1d["low"],
            )
            in_window = history_df[
                (history_df.index >= sim_start) & (history_df.index < sim_end)
            ]
            if not in_window.empty:
                observability = emit_observability_metrics(in_window)
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"

    out = {
        "symbol": symbol,
        "sub_window": WINDOW_A_ID,
        "vol_target": PRIMARY_VOL_TARGET,
        "n_trades": int(metrics.get("total_trades", 0)),
        "net_pnl_usd": round(float(metrics.get("net_pnl", 0.0)), 4),
        "profit_factor": round(
            _finite_or(metrics.get("profit_factor", 0.0), _PROFIT_FACTOR_INF_SENTINEL), 4,
        ),
        "win_rate": round(_finite_or(metrics.get("win_rate", 0.0), 0.0), 4),
        "max_drawdown_pct": round(
            _finite_or(metrics.get("max_drawdown_pct", 0.0), 0.0), 4,
        ),
        "bankruptcy_count": int(metrics.get("bankruptcy_count", 0)),
        "clamped_trade_count": int(metrics.get("clamped_trade_count", 0)),
        "insufficient_data": (
            err is None
            and int(metrics.get("total_trades", 0)) < N_TRADES_MIN_FOR_ELIGIBILITY
        ),
        "observability": observability,
    }
    if err is not None:
        out["error"] = err
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Job builder + halt detection
# ─────────────────────────────────────────────────────────────────────────────


def build_phase2_jobs(app_config_path: str) -> list[dict]:
    """Pre-reg §2.5 Phase 2 — 8 cells × Window A × baseline equal-weight."""
    jobs = []
    for sym in WINDOW_A_COVERAGE:
        jobs.append({
            "symbol": sym,
            "sub_window": WINDOW_A_ID,
            "vol_target": PRIMARY_VOL_TARGET,
            "sim_start_iso": WINDOW_A_START_ISO,
            "sim_end_iso": WINDOW_A_END_ISO,
            "cutoff_iso": CUTOFF_ISO,
            "app_config_path": app_config_path,
        })
    return jobs


def check_phase2_halt(phase2_results: list[dict]) -> dict:
    """Pre-reg §10.4 + §4.1 row 4 — Phase 2 universal-bankruptcy halt.

    Triggers verdict = PHASE_2_INSUFFICIENT_DATA when ≥75% of in-coverage cells
    have at least one BANKRUPT event in the baseline run.
    """
    n_in_cov = len(WINDOW_A_COVERAGE)
    threshold = _halt_count_threshold(n_in_cov)  # ≥6 of 8

    a_cells = [r for r in phase2_results if r.get("sub_window") == WINDOW_A_ID]
    bankrupt_symbols = sorted(
        r["symbol"] for r in a_cells if int(r.get("bankruptcy_count", 0)) > 0
    )
    halt = len(bankrupt_symbols) >= threshold

    return {
        "halt": halt,
        "halt_reasons": ["H_universal_bankruptcy"] if halt else [],
        "window_evaluated": WINDOW_A_ID,
        "vol_target_evaluated": PRIMARY_VOL_TARGET,
        "n_in_coverage": n_in_cov,
        "halt_count_threshold": threshold,
        "halt_fraction_threshold": HALT_FRACTION_THRESHOLD,
        "n_symbols_bankrupt": len(bankrupt_symbols),
        "symbols_bankrupt": bankrupt_symbols,
        "per_symbol_a_cell": {
            r["symbol"]: {
                "n_trades": r.get("n_trades", 0),
                "bankruptcy_count": r.get("bankruptcy_count", 0),
                "net_pnl_usd": r.get("net_pnl_usd", 0.0),
            } for r in a_cells
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation: build observability cells list for verdict input
# ─────────────────────────────────────────────────────────────────────────────


def aggregate_observability_cells(phase2_results: list[dict]) -> list[dict]:
    """Extract observability sub-dicts from each cell, keyed by symbol.

    Output shape matches what `classify_phase2_verdict` expects: list of dicts
    with `symbol`, `per_lookback` (keyed by int N), and `sum_distribution`.
    """
    cells = []
    for r in phase2_results:
        obs = r.get("observability") or {}
        if not obs:
            continue
        cells.append({
            "symbol": r["symbol"],
            "per_lookback": obs.get("per_lookback", {}),
            "sum_distribution": obs.get("sum_distribution", {}),
        })
    return cells


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--app-config", default=str(APP_CONFIG_PATH_DEFAULT),
        help="Path to config.defaults.json (default: repo root)",
    )
    p.add_argument(
        "--workers", type=int, default=min(8, cpu_count()),
        help="Multiprocessing pool size (default: min(8, cpu_count()))",
    )
    return p.parse_args()


def main() -> int:
    from tools.signal_calibration_verdict import classify_phase2_verdict

    args = _parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    jobs = build_phase2_jobs(args.app_config)
    print(f"[phase2] {len(jobs)} cells × Window A × baseline equal-weight Donchian-9")

    t_start = time.monotonic()
    with Pool(processes=args.workers) as pool:
        results = pool.map(_process_phase2_cell, jobs)
    elapsed = time.monotonic() - t_start
    print(f"[phase2] sweep done in {elapsed:.1f}s")

    # Write per-cell observability sidecars
    for r in results:
        obs = r.get("observability") or {}
        if obs:
            sidecar_path = OUTPUT_DIR / f"observability_{r['symbol']}_{WINDOW_A_ID}.json"
            _save_json(sidecar_path, obs)

    # Write aggregate phase2_diagnostic.json (cell metrics + nested observability)
    _save_json(OUTPUT_DIR / "phase2_diagnostic.json", results)

    # Halt detection
    halt_diag = check_phase2_halt(results)
    _save_json(OUTPUT_DIR / "halt_diagnostic.json", halt_diag)

    # Verdict
    observability_cells = aggregate_observability_cells(results)
    verdict_result = classify_phase2_verdict(
        observability_cells,
        in_coverage_count=len(WINDOW_A_COVERAGE),
        halt=halt_diag["halt"],
    )
    _save_json(OUTPUT_DIR / "phase2_verdict.json", verdict_result)

    # Manifest
    _save_json(OUTPUT_DIR / "manifest.json", {
        "schema_version": 1,
        "spec_ref": "docs/superpowers/plans/2026-05-15-signal-calibration-pre-reg.md",
        "phase": 2,
        "cutoff_iso": CUTOFF_ISO,
        "code_commit": _git_commit(),
        "sub_window": WINDOW_A_ID,
        "sub_window_start_iso": WINDOW_A_START_ISO,
        "sub_window_end_iso": WINDOW_A_END_ISO,
        "in_coverage": list(WINDOW_A_COVERAGE),
        "n_in_coverage": len(WINDOW_A_COVERAGE),
        "vol_target": PRIMARY_VOL_TARGET,
        "warmup_daily_bars": WARMUP_DAILY_BARS,
        "elapsed_seconds": round(elapsed, 1),
    })

    print(
        f"[phase2] verdict: {verdict_result['verdict']} "
        f"(n_a={verdict_result.get('n_symbols_a_evidence', 0)}, "
        f"n_not_a={verdict_result.get('n_symbols_no_a_evidence', 0)}, "
        f"halt={halt_diag['halt']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
