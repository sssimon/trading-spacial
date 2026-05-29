#!/usr/bin/env python3
"""Regime-allocation sweep harness (epic #338 Phase 3).

Pre-reg: docs/superpowers/plans/2026-05-14-regime-allocation-phase2-pre-reg.md

This harness runs the Phase 3 sweep that locks the regime-allocation strategy
class verdict (PASS / SUCCESS-CONDITIONAL / INCONCLUSIVE / FAIL per pre-reg §4).

## Sweep structure (pre-reg §2.5)

Primary pass (locked vol_target=30%):
  10 symbols × 3 sub-windows × 1 vol_target = 30 cells submitted
  25 cells running (pre-reg §3 coverage: A=8, B=8, C=9)
  5 NO_DATA cells (PENDLE excluded from A+B; JUP excluded from A+B+C)

Sensitivity sweep (vol_target ∈ {0.25, 0.30, 0.35, 0.40}):
  10 × 3 × 4 = 120 cells submitted
  100 cells running (same coverage exclusions × 4)

Baseline benchmarks (separate from sweep, pre-reg §2.5 + §9.5):
  - BTC B&H per sub-window (3 backtests, long-only spot, 1 buy + 1 sell fee, no funding)
  - Hubrich 200-DMA filter on BTC per sub-window (3, long when close > 200-day SMA)
  - LRC archived strategy per sub-window (3, current params + cost model v2)

## Halt conditions (pre-reg §10.4)

Single source of truth — H1 (universal bankruptcy) and H2 (signal degenerate)
both evaluated on sub-window A primary at vol_target=30%:
  H1: ≥75% of in-coverage bankrupt → halt B+C + sensitivity sweep
  H2: ≥75% of in-coverage with n_trades<5 → halt B+C + sensitivity sweep
Concrete thresholds: ≥6 in A/B; ≥7 in C (operationalized integer counts).

§4.6 asymmetric halt-guard: applied at verdict time, not here. The sweep
unconditionally writes halt_diagnostic.json when either H1 or H2 fires.

## Output (data/retune/2026-05-14-regime-allocation/)

  - sweep_primary_{A,B,C}.json        (only if §10 halt NOT fired for B+C)
  - sweep_sensitivity_{A,B,C}.json    (only if §10 halt NOT fired)
  - baseline_btc_bh_{A,B,C}.json
  - baseline_hubrich_{A,B,C}.json
  - baseline_lrc_archived_{A,B,C}.json
  - signal_diagnostics.json
  - cost_attribution.json
  - bankruptcy_diagnostics.json
  - halt_diagnostic.json              (ALWAYS written; halt=True when fired)
  - coverage.json
  - manifest.json
  - smoke_test.json                   (if --smoke)

## Usage

  python tools/regime_allocation_sweep.py                  # full pipeline
  python tools/regime_allocation_sweep.py --baselines-only # only BTC/Hubrich/LRC
  python tools/regime_allocation_sweep.py --skip-baselines # honor existing
  python tools/regime_allocation_sweep.py --window A       # one window
  python tools/regime_allocation_sweep.py --smoke          # 1-cell wiring check
  python tools/regime_allocation_sweep.py --skip-sensitivity # primary only

Exit codes:
  0 — clean completion (or halt fired with B+C aborted as designed)
  1 — missing inputs / harness wiring error
  2 — §10.4 halt fired AND --strict-halt passed (default: 0, halt is expected behavior)
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from multiprocessing import Pool, cpu_count
from pathlib import Path
from typing import Final

# ─────────────────────────────────────────────────────────────────────────────
# Pre-registered constants. DO NOT TUNE without amending pre-reg
# docs/superpowers/plans/2026-05-14-regime-allocation-phase2-pre-reg.md.
# ─────────────────────────────────────────────────────────────────────────────

# Pre-reg §2.1 + epic §8.4 — Zarattini exact 9 lookbacks (carry-forward).
ZARATTINI_LOOKBACKS: Final[tuple[int, ...]] = (
    5, 10, 20, 30, 60, 90, 150, 250, 360,
)
# Pre-reg §2.1 — vol estimation window (30 daily bars) + longest lookback (360)
# → warmup minimum = 390 daily bars.
WARMUP_DAILY_BARS: Final[int] = 390

# Pre-reg §2.5 — primary pass uses locked vol_target=30%.
PRIMARY_VOL_TARGET: Final[float] = 0.30
# Pre-reg §2.5 + §4.2 — sensitivity sweep grid.
SENSITIVITY_VOL_TARGETS: Final[tuple[float, ...]] = (0.25, 0.30, 0.35, 0.40)

# Pre-reg §3 — sub-windows (R3-exact dates per operator decision §1.1).
SUB_WINDOWS: Final[dict[str, tuple[str, str]]] = {
    "A": ("2022-04-01T00:00:00+00:00", "2022-07-01T00:00:00+00:00"),
    "B": ("2023-04-01T00:00:00+00:00", "2023-07-01T00:00:00+00:00"),
    "C": ("2025-01-30T00:00:00+00:00", "2025-04-30T00:00:00+00:00"),
}

# Pre-reg §3 (anti-leakage) — Window C end = holdout_start exclusive.
# data/holdout/ is read-only and out of scope until Phase 5 per CLAUDE.md.
CUTOFF_ISO: Final[str] = "2025-04-30T00:00:00+00:00"

# Pre-reg §3 + CLAUDE.md DEFAULT_SYMBOLS — curated 10 (locked since epic #135).
CURATED_SYMBOLS: Final[tuple[str, ...]] = (
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
    "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
)

# Pre-reg §3 + §5.1 + §10.1 — empirically-verified coverage table.
# PENDLE first 1H bar 2023-07-03 → excluded from A (didn't exist) AND B
#   (first bar is AFTER B end 2023-07-01).
# JUP first 1H bar 2024-01-31 → excluded from A + B (didn't exist) AND C
#   (only ~364 daily bars by C start 2025-01-30 < 390 warmup threshold).
# Coverage: A=8, B=8, C=9.
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

# Pre-reg §4.1 — cell exclusion: n_trades < 5 → INSUFFICIENT_DATA marker.
# Loosens epic §6.3 H2 anchor 10→5 per CR1 review fix; rationale in pre-reg
# §9.2 + §10.4 (daily-frequency ensemble produces 5-25 trades/cell expected).
N_TRADES_MIN_FOR_ELIGIBILITY: Final[int] = 5

# Pre-reg §10.4 — halt thresholds (single source of truth).
# ≥75% of in-coverage symbols → halt. Concrete counts:
#   Window A (8): ⌈8 × 0.75⌉ = 6
#   Window B (8): 6
#   Window C (9): ⌈9 × 0.75⌉ = 7
# Halt evaluated ONLY on Window A primary at vol_target=30%.
HALT_FRACTION_THRESHOLD: Final[float] = 0.75

# Pre-reg §5.8 — drop partial daily bars (< 24 hours coverage) at edges.
DAILY_BAR_HOURS_REQUIRED: Final[int] = 24

# Hubrich §5.2 epic baseline — long BTC when close > 200-day SMA, else cash.
HUBRICH_SMA_DAYS: Final[int] = 200

# Pre-reg §2.4 — cost model v2 fee bps (entry + exit). For BTC B&H baseline:
# 1 buy + 1 sell fee, NO funding (long spot, no perp). 10 bps total round-trip
# is a common spot fee anchor (Binance 0.1% taker tier); kept conservative.
SPOT_FEE_BPS_PER_FILL: Final[float] = 5.0

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
OUTPUT_DIR: Final[Path] = (
    REPO_ROOT / "data" / "retune" / "2026-05-14-regime-allocation"
)
APP_CONFIG_PATH_DEFAULT: Final[Path] = REPO_ROOT / "config.defaults.json"

# Allow `from auto_tune import ...` / `from backtest import ...` when invoked
# directly. Workers spawned via multiprocessing inherit sys.path.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Trial registry (#278 Part 1). Imported after the sys.path.insert so `db` is
# importable when the harness is invoked directly. Trial writes happen in the
# PARENT process (_run_jobs_parallel), never in pool children.
from db.trials import claim_trial, finalize_trial  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _iso_to_utc_dt(iso: str) -> datetime:
    """Parse an ISO 8601 string into a tz-aware UTC datetime."""
    return datetime.fromisoformat(iso).astimezone(timezone.utc)


def _is_in_coverage(symbol: str, window_id: str) -> bool:
    """Pre-reg §3 + §5.1 — return True if (symbol, window) has ≥390 daily bars."""
    return symbol.upper() in COVERAGE_BY_WINDOW[window_id]


def _halt_count_threshold(n_in_coverage: int) -> int:
    """Pre-reg §10.4 — ⌈n × 0.75⌉ ceil rounding. Used for both H1 and H2."""
    return math.ceil(n_in_coverage * HALT_FRACTION_THRESHOLD)


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True,
        ).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _save_json(path: Path, payload):
    """Write `payload` as JSON. NaN / Inf raise rather than serialize.

    Mirrors tools/r3_trend_pullback_sweep.py:_save_json — fails at write time
    rather than silently emitting non-standard JSON tokens that downstream
    parsers (verdict tool, audit doc) may mishandle.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True, default=str, allow_nan=False)


# Pre-reg §11 (compute estimate) R3 risk realized 2026-05-14: Binance
# occasionally returns TCP RST (Windows 10054) or ConnectTimeout on
# historical chunks; data layer's fetch_with_failover switches to Bybit
# which often lacks the same historical range; both exhausted → workers
# crash. Mirrors warmer pattern (tools/warmup_ohlcv_cache.py). Each retry
# re-calls get_klines_range, which re-scans the cache for gaps and
# resumes from the (smaller) gap set after partial chunks persisted.
_FETCH_RETRY_MAX_ATTEMPTS: Final[int] = 6
_FETCH_RETRY_BASE_BACKOFF_SEC: Final[float] = 3.0

# Sentinel used in place of math.inf for profit_factor when all trades won
# (zero gross_loss). _save_json has allow_nan=False (CHANGES_REQUESTED #5
# review fix 2026-05-14) — inf/NaN must not reach the JSON writer.
# Downstream readers (verdict tool, derivation_audit) treat 99999 as
# "very profitable, no losses recorded".
_PROFIT_FACTOR_INF_SENTINEL: Final[float] = 99999.0


def _finite_or(value, fallback: float) -> float:
    """Coerce float to JSON-compliant finite. Use on metric fields where
    upstream calculate_metrics may emit math.inf (zero denominator).
    Mirrors _save_json's allow_nan=False discipline at the worker layer."""
    v = float(value)
    return v if math.isfinite(v) else fallback


def _get_cached_data_with_retry(symbol, timeframe, start_date):
    """get_cached_data with exponential backoff on AllProvidersFailedError.

    Multiprocessing-safe — imports inside the function body. Subprocess
    workers inherit module state per fork/spawn; this helper sits at
    module scope so they get a fresh re-import per process.
    """
    import time
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
    raise last_err  # exhausted — propagate so worker can build error result


# ─────────────────────────────────────────────────────────────────────────────
# Cell workers (multiprocessing-safe; imports inside the function body)
# ─────────────────────────────────────────────────────────────────────────────


def _process_regime_allocation_cell(args: dict) -> dict:
    """Worker: run one regime-allocation (symbol, sub-window, vol_target) cell.

    Pure function (config path + dict args). Imports inside so multiprocessing
    workers get fresh per-child module state.

    Returns a dict mirroring the per-cell schema. NO_DATA cells short-circuit
    BEFORE running the ensemble (warmup-fail marker) — `_is_in_coverage`
    must be checked at job-build time; this worker assumes the cell is in
    coverage.
    """
    from backtest import (
        calculate_metrics, get_cached_data, simulate_strategy,
    )
    from dateutil.relativedelta import relativedelta
    import pandas as pd

    symbol = args["symbol"]
    window_id = args["sub_window"]
    vol_target = float(args["vol_target"])
    sim_start = _iso_to_utc_dt(args["sim_start_iso"])
    sim_end = _iso_to_utc_dt(args["sim_end_iso"])
    cutoff = _iso_to_utc_dt(args["cutoff_iso"])
    app_config_path = args["app_config_path"]

    # Normalize sim_start/sim_end to tz-naive — df.index is normalized to
    # naive below (lines 264-269), and backtest.py:_simulate_strategy_
    # regime_allocation does `df1d.index >= sim_start` (line 675) which
    # raises TypeError on tz-aware vs tz-naive mismatch.
    sim_start = sim_start.replace(tzinfo=None) if sim_start.tzinfo else sim_start
    sim_end = sim_end.replace(tzinfo=None) if sim_end.tzinfo else sim_end

    with open(app_config_path) as f:
        app_config = json.load(f)

    # Build cfg with regime_allocation enabled and primary vol_target.
    cfg = dict(app_config)
    ra_block = dict(cfg.get("regime_allocation", {}))
    ra_block["enabled"] = True
    ra_block["portfolio_vol_target"] = vol_target
    cfg["regime_allocation"] = ra_block

    # df4h/df5m are unused by the regime-allocation path but required by
    # simulate_strategy's signature. Pass empty placeholders.
    #
    # CONTRACT (per epic #338 §4.2 + Phase 1C PR #345): when
    # cfg.regime_allocation.enabled = True, _simulate_strategy_regime_allocation
    # MUST NOT read df4h or df5m. The branch dispatches on the flag and
    # only consumes df1h + df1d. If a future change to backtest.py begins
    # touching df4h/df5m under the flag, this placeholder will silently
    # propagate empty frames and produce undefined results. Asserted
    # implicitly by the 104-test sweep + #345 byte-identical regression.
    empty_ohlcv = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    err = None
    trades: list[dict] = []
    metrics: dict = {}
    df1h = pd.DataFrame()
    df1d = pd.DataFrame()

    try:
        # Load data with ample pre-window for 390-day warmup. df1d needs
        # ~13 months of history before sub-window start; df1h drives the
        # liquidity proxy and can be tighter. Retry wrapper absorbs
        # transient AllProvidersFailedError (Binance TCP RST / ConnectTimeout
        # patterns on Windows + Bybit missing historical 5m) — exhaustion
        # bubbles up to the `except` below as a soft error.
        df1h = _get_cached_data_with_retry(
            symbol, "1h", sim_start - relativedelta(months=14),
        )
        df1d = _get_cached_data_with_retry(
            symbol, "1d", sim_start - relativedelta(months=14),
        )

        # Anti-leakage: slice all dfs to < cutoff. Pre-reg §3 + holdout policy.
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
            if trades:
                metrics = calculate_metrics(trades, equity_curve)
            else:
                metrics = {
                    "total_trades": 0, "net_pnl": 0.0,
                    "profit_factor": 0.0, "win_rate": 0.0,
                    "max_drawdown_pct": 0.0, "bankruptcy_count": 0,
                    "clamped_trade_count": 0,
                }
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"

    # Per-cell exit-reason histogram + cost attribution split.
    exit_counts: dict[str, int] = {}
    funding_usd_sum = 0.0
    slippage_usd_sum = 0.0
    gross_pnl_usd_sum = 0.0
    if trades:
        real = [t for t in trades if t.get("exit_reason") not in (None, "OPEN")]
        exit_counts = dict(Counter(t.get("exit_reason") for t in real))
        for t in real:
            # funding_cost_bps is on the notional; convert to USD via the
            # ratio funding/total_cost × total_cost_usd. Cheap and avoids
            # re-computing from bps.
            #
            # INVARIANT (per backtest.py cost model v2 PR #341): for any
            # closed trade, total_cost_bps >= funding_cost_bps because
            # total_cost_bps = slippage_cost_bps + funding_cost_bps (both
            # non-negative). Therefore tc_bps == 0 implies f_bps == 0, and
            # the silent `funding_usd = 0` in the else-branch is correct
            # under invariant. If the invariant ever breaks upstream
            # (e.g., negative slippage allowed), this site silently
            # under-attributes funding cost — diagnose at backtest_costs.py
            # rather than here.
            tc_bps = float(t.get("total_cost_bps", 0.0)) or 0.0
            tc_usd = float(t.get("total_cost_usd", 0.0)) or 0.0
            f_bps = float(t.get("funding_cost_bps", 0.0)) or 0.0
            funding_usd = (f_bps / tc_bps * tc_usd) if tc_bps > 0 else 0.0
            funding_usd_sum += funding_usd
            slippage_usd_sum += (tc_usd - funding_usd)
            gross_pnl_usd_sum += float(t.get("gross_pnl_usd", 0.0)) or 0.0

    out = {
        "symbol": symbol,
        "sub_window": window_id,
        "vol_target": vol_target,
        "n_trades": int(metrics.get("total_trades", 0)),
        "net_pnl_usd": round(float(metrics.get("net_pnl", 0.0)), 4),
        "gross_pnl_usd": round(gross_pnl_usd_sum, 4),
        # profit_factor coerced to _PROFIT_FACTOR_INF_SENTINEL when
        # calculate_metrics returns math.inf (zero gross_loss = all trades
        # won). _save_json has allow_nan=False per CHANGES_REQUESTED #5.
        "profit_factor": round(
            _finite_or(metrics.get("profit_factor", 0.0),
                       _PROFIT_FACTOR_INF_SENTINEL), 4,
        ),
        "win_rate": round(_finite_or(metrics.get("win_rate", 0.0), 0.0), 4),
        "max_drawdown_pct": round(
            _finite_or(metrics.get("max_drawdown_pct", 0.0), 0.0), 4,
        ),
        "bankruptcy_count": int(metrics.get("bankruptcy_count", 0)),
        "clamped_trade_count": int(metrics.get("clamped_trade_count", 0)),
        "total_funding_usd": round(funding_usd_sum, 4),
        "total_slippage_usd": round(slippage_usd_sum, 4),
        "exit_reasons": exit_counts,
        # Pre-reg §4.1 — cell-level INSUFFICIENT_DATA flag (post-simulation).
        # NO_DATA (warmup-fail / coverage exclusion) is handled at job-build
        # time and never reaches this worker.
        "insufficient_data": (
            err is None
            and int(metrics.get("total_trades", 0)) < N_TRADES_MIN_FOR_ELIGIBILITY
        ),
    }
    if err is not None:
        out["error"] = err
    return out


def _compute_btc_bh_baseline(args: dict) -> dict:
    """Worker: BTC long-only spot baseline per sub-window.

    Pre-reg §4 notes — "1 buy fee + 1 sell fee, no funding (long spot, no perp)."
    Total round-trip fee: 2 × SPOT_FEE_BPS_PER_FILL bps. Capital basis:
    n_in_coverage × INITIAL_CAPITAL (mirrors strategy aggregate per pre-reg §4).
    """
    from backtest import INITIAL_CAPITAL, get_cached_data
    from dateutil.relativedelta import relativedelta

    window_id = args["sub_window"]
    sim_start = _iso_to_utc_dt(args["sim_start_iso"])
    sim_end = _iso_to_utc_dt(args["sim_end_iso"])
    cutoff = _iso_to_utc_dt(args["cutoff_iso"])
    n_in_coverage = int(args["n_in_coverage"])

    df = get_cached_data("BTCUSDT", "1d", start_date=sim_start - relativedelta(months=14))

    # CHANGES_REQUESTED #6 review fix 2026-05-14 — normalize df.index to
    # tz-naive in-place once, then all downstream comparisons use naive
    # timestamps. Avoids tz-aware vs tz-naive comparison TypeError that
    # would surface only against real tz-aware OHLCV cache (synthetic
    # tests don't exercise the path).
    cutoff_naive = cutoff.replace(tzinfo=None) if cutoff.tzinfo else cutoff
    sim_start_naive = sim_start.replace(tzinfo=None) if sim_start.tzinfo else sim_start
    sim_end_naive = sim_end.replace(tzinfo=None) if sim_end.tzinfo else sim_end
    if not df.empty and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    if not df.empty:
        df = df[df.index < cutoff_naive]

    # Find the first daily close >= sim_start and the last daily close <= sim_end.
    #
    # CONVENTION: `< sim_end_naive` (strict, NOT `<=`). The exact-boundary
    # bar at sim_end is excluded. For Window C, sim_end = 2025-04-30 =
    # holdout_start cutoff — strict < preserves anti-leakage (no bar at
    # holdout boundary touched). For Windows A/B, the boundary bar at
    # 2022-07-01 / 2023-07-01 is symmetrically excluded for consistency.
    # All sub-window boundary handling in this file (BTC B&H + Hubrich)
    # follows the same `[sim_start_naive, sim_end_naive)` semi-open interval.
    in_window = df[(df.index >= sim_start_naive) & (df.index < sim_end_naive)]

    if in_window.empty or len(in_window) < 2:
        return {
            "sub_window": window_id,
            "baseline": "btc_bh",
            "error": f"insufficient bars in window: {len(in_window)}",
            "total_return_pct": 0.0,
            "total_return_usd": 0.0,
            "n_in_coverage": n_in_coverage,
            "capital_basis_usd": n_in_coverage * INITIAL_CAPITAL,
        }

    entry_price = float(in_window["close"].iloc[0])
    exit_price = float(in_window["close"].iloc[-1])
    gross_return_pct = (exit_price - entry_price) / entry_price * 100.0
    # Fees: 1 buy + 1 sell = 2 × SPOT_FEE_BPS_PER_FILL/100 (bps→pct).
    fees_pct = 2.0 * (SPOT_FEE_BPS_PER_FILL / 100.0)
    net_return_pct = gross_return_pct - fees_pct

    capital_basis = n_in_coverage * INITIAL_CAPITAL
    total_return_usd = capital_basis * (net_return_pct / 100.0)

    return {
        "sub_window": window_id,
        "baseline": "btc_bh",
        "entry_date": str(in_window.index[0]),
        "exit_date": str(in_window.index[-1]),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "gross_return_pct": round(gross_return_pct, 4),
        "fees_pct": round(fees_pct, 4),
        "total_return_pct": round(net_return_pct, 4),
        "total_return_usd": round(total_return_usd, 4),
        "n_in_coverage": n_in_coverage,
        "capital_basis_usd": capital_basis,
    }


def _compute_hubrich_baseline(args: dict) -> dict:
    """Worker: Hubrich 200-DMA filter on BTC (long when close > SMA(200), else cash).

    Pre-reg §9.5 — operationalization: long BTC when previous-day close exceeds
    200-day SMA computed on daily closes; otherwise cash (0% return on cash days).
    Fee handling: a transaction (buy or sell) is charged each time the filter
    state changes (cash→long buys, long→cash sells). No funding (spot).
    """
    from backtest import INITIAL_CAPITAL, get_cached_data
    from dateutil.relativedelta import relativedelta

    window_id = args["sub_window"]
    sim_start = _iso_to_utc_dt(args["sim_start_iso"])
    sim_end = _iso_to_utc_dt(args["sim_end_iso"])
    cutoff = _iso_to_utc_dt(args["cutoff_iso"])
    n_in_coverage = int(args["n_in_coverage"])

    # Load BTC daily with enough history for the 200-day SMA pre-window.
    df = get_cached_data(
        "BTCUSDT", "1d", start_date=sim_start - relativedelta(months=14),
    )

    # CHANGES_REQUESTED #6 review fix 2026-05-14 — normalize tz once, then
    # operate naive throughout (mirror _compute_btc_bh_baseline fix).
    cutoff_naive = cutoff.replace(tzinfo=None) if cutoff.tzinfo else cutoff
    sim_start_naive = sim_start.replace(tzinfo=None) if sim_start.tzinfo else sim_start
    sim_end_naive = sim_end.replace(tzinfo=None) if sim_end.tzinfo else sim_end
    if not df.empty and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    if not df.empty:
        df = df[df.index < cutoff_naive]

    # 200-day SMA on closes; use prior-day SMA to avoid same-day look-ahead.
    closes = df["close"].copy()
    sma200 = closes.rolling(HUBRICH_SMA_DAYS, min_periods=HUBRICH_SMA_DAYS).mean().shift(1)
    above = (closes > sma200).fillna(False)

    mask = (closes.index >= sim_start_naive) & (closes.index < sim_end_naive)
    window_closes = closes[mask]
    window_above = above[mask]

    if window_closes.empty or len(window_closes) < 2:
        return {
            "sub_window": window_id,
            "baseline": "hubrich",
            "error": f"insufficient bars in window: {len(window_closes)}",
            "total_return_pct": 0.0,
            "total_return_usd": 0.0,
            "n_in_coverage": n_in_coverage,
            "capital_basis_usd": n_in_coverage * INITIAL_CAPITAL,
        }

    # Day-by-day equity simulation.
    equity_pct = 100.0
    in_position = False
    n_transitions = 0
    daily_returns = window_closes.pct_change().fillna(0.0)
    prev_above = bool(window_above.iloc[0])
    in_position = prev_above  # start position matches filter at sim_start
    if in_position:
        n_transitions += 1  # initial buy

    for i in range(1, len(window_closes)):
        # Apply yesterday's daily return if in position at yesterday's close.
        if in_position:
            equity_pct *= 1.0 + float(daily_returns.iloc[i])
        # Check today's filter state for tomorrow.
        today_above = bool(window_above.iloc[i])
        if today_above != prev_above:
            n_transitions += 1
            in_position = today_above
        prev_above = today_above

    # Final sell if still in position at window end.
    if in_position:
        n_transitions += 1

    fees_pct = n_transitions * (SPOT_FEE_BPS_PER_FILL / 100.0)
    gross_return_pct = equity_pct - 100.0
    net_return_pct = gross_return_pct - fees_pct

    capital_basis = n_in_coverage * INITIAL_CAPITAL
    total_return_usd = capital_basis * (net_return_pct / 100.0)

    return {
        "sub_window": window_id,
        "baseline": "hubrich",
        "entry_date": str(window_closes.index[0]),
        "exit_date": str(window_closes.index[-1]),
        "n_transitions": n_transitions,
        "gross_return_pct": round(gross_return_pct, 4),
        "fees_pct": round(fees_pct, 4),
        "total_return_pct": round(net_return_pct, 4),
        "total_return_usd": round(total_return_usd, 4),
        "n_in_coverage": n_in_coverage,
        "capital_basis_usd": capital_basis,
    }


def _process_lrc_archived_baseline_cell(args: dict) -> dict:
    """Worker: per-symbol LRC archived baseline (flag-off, current params + v2 costs).

    Pre-reg §5.4 epic + §9.5 — internal control benchmark. Uses
    cfg.regime_allocation.enabled = False (default) + current symbol_overrides
    from config.defaults.json. Structural fixes #223 + #309 + #313 active.
    """
    from backtest import (
        calculate_metrics, get_cached_data, simulate_strategy,
    )
    from dateutil.relativedelta import relativedelta
    import pandas as pd

    symbol = args["symbol"]
    window_id = args["sub_window"]
    sim_start = _iso_to_utc_dt(args["sim_start_iso"])
    sim_end = _iso_to_utc_dt(args["sim_end_iso"])
    cutoff = _iso_to_utc_dt(args["cutoff_iso"])
    app_config_path = args["app_config_path"]

    # Normalize sim_start/sim_end to tz-naive for consistency with the
    # tz-naive df.index mutation below (lines 559-560). Mirrors the fix in
    # _process_regime_allocation_cell — keeps the worker's tz invariant
    # uniform across all internal comparisons and the simulate_strategy call.
    sim_start = sim_start.replace(tzinfo=None) if sim_start.tzinfo else sim_start
    sim_end = sim_end.replace(tzinfo=None) if sim_end.tzinfo else sim_end

    with open(app_config_path) as f:
        app_config = json.load(f)

    # Flag off — defensive (config.defaults.json already has it False, but be
    # explicit so a hand-edited config can't silently flip the baseline path).
    cfg = dict(app_config)
    ra_block = dict(cfg.get("regime_allocation", {}))
    ra_block["enabled"] = False
    cfg["regime_allocation"] = ra_block

    err = None
    trades: list[dict] = []
    metrics: dict = {}
    df1h = pd.DataFrame()
    df4h = pd.DataFrame()
    df5m = pd.DataFrame()
    df1d = pd.DataFrame()

    try:
        # Load data with retry wrapper. Retry absorbs transient
        # AllProvidersFailedError (Binance TCP RST / ConnectTimeout +
        # Bybit missing historical 5m); exhaustion bubbles up to the
        # `except` below as a soft error in the result dict.
        df1h = _get_cached_data_with_retry(
            symbol, "1h", sim_start - relativedelta(months=14),
        )
        df4h = _get_cached_data_with_retry(
            symbol, "4h", sim_start - relativedelta(months=14),
        )
        df5m = _get_cached_data_with_retry(
            symbol, "5m", sim_start - relativedelta(months=2),
        )
        df1d = _get_cached_data_with_retry(
            symbol, "1d", sim_start - relativedelta(months=14),
        )

        # CHANGES_REQUESTED #6 review fix 2026-05-14 — normalize tz once,
        # mutate df.index in-place; downstream comparisons (simulate_strategy
        # internals + sim_start/sim_end slicing) handle tz uniformly.
        cutoff_naive = cutoff.replace(tzinfo=None) if cutoff.tzinfo else cutoff
        for df in (df1h, df4h, df5m, df1d):
            if df.empty:
                continue
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            keep_mask = df.index < cutoff_naive
            df.drop(df.index[~keep_mask], inplace=True)  # type: ignore[arg-type]

        if df1h.empty or df4h.empty or df5m.empty:
            err = "missing intraday OHLCV — LRC path requires df1h+df4h+df5m"
        else:
            trades, equity_curve = simulate_strategy(
                df1h=df1h, df4h=df4h, df5m=df5m, df1d=df1d, symbol=symbol,
                sim_start=sim_start, sim_end=sim_end, cfg=cfg,
                enable_slippage=True, enable_spread=True,
                enable_fees=True, enable_funding=True,
            )
            if trades:
                metrics = calculate_metrics(trades, equity_curve)
            else:
                metrics = {
                    "total_trades": 0, "net_pnl": 0.0,
                    "profit_factor": 0.0, "max_drawdown_pct": 0.0,
                    "bankruptcy_count": 0, "clamped_trade_count": 0,
                    "win_rate": 0.0,
                }
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"

    out = {
        "symbol": symbol,
        "sub_window": window_id,
        "baseline": "lrc_archived",
        "n_trades": int(metrics.get("total_trades", 0)),
        "net_pnl_usd": round(float(metrics.get("net_pnl", 0.0)), 4),
        # profit_factor coerced to _PROFIT_FACTOR_INF_SENTINEL when
        # calculate_metrics returns math.inf (zero gross_loss).
        "profit_factor": round(
            _finite_or(metrics.get("profit_factor", 0.0),
                       _PROFIT_FACTOR_INF_SENTINEL), 4,
        ),
        "win_rate": round(_finite_or(metrics.get("win_rate", 0.0), 0.0), 4),
        "max_drawdown_pct": round(
            _finite_or(metrics.get("max_drawdown_pct", 0.0), 0.0), 4,
        ),
        "bankruptcy_count": int(metrics.get("bankruptcy_count", 0)),
        "clamped_trade_count": int(metrics.get("clamped_trade_count", 0)),
    }
    if err is not None:
        out["error"] = err
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Job builders
# ─────────────────────────────────────────────────────────────────────────────


def _build_primary_jobs(app_config_path: str) -> list[dict]:
    """Pre-reg §2.5 primary — in-coverage cells at vol_target=30.

    Returns 25 cells (8 in A + 8 in B + 9 in C). Out-of-coverage symbols are
    NOT scheduled — they get a NO_DATA marker post-hoc by the aggregator,
    not a worker call.
    """
    jobs = []
    for window_id, (start_iso, end_iso) in SUB_WINDOWS.items():
        for sym in COVERAGE_BY_WINDOW[window_id]:
            jobs.append({
                "symbol": sym,
                "sub_window": window_id,
                "vol_target": PRIMARY_VOL_TARGET,
                "sim_start_iso": start_iso,
                "sim_end_iso": end_iso,
                "cutoff_iso": CUTOFF_ISO,
                "app_config_path": app_config_path,
            })
    return jobs


def _build_sensitivity_jobs(app_config_path: str) -> list[dict]:
    """Pre-reg §2.5 sensitivity — in-coverage cells × 4 vol_target values.

    Returns 100 cells (25 in-coverage × 4 vol_target).
    """
    jobs = []
    for window_id, (start_iso, end_iso) in SUB_WINDOWS.items():
        for sym in COVERAGE_BY_WINDOW[window_id]:
            for vt in SENSITIVITY_VOL_TARGETS:
                jobs.append({
                    "symbol": sym,
                    "sub_window": window_id,
                    "vol_target": float(vt),
                    "sim_start_iso": start_iso,
                    "sim_end_iso": end_iso,
                    "cutoff_iso": CUTOFF_ISO,
                    "app_config_path": app_config_path,
                })
    return jobs


def _build_btc_bh_jobs() -> list[dict]:
    """3 BTC B&H baselines, one per sub-window."""
    jobs = []
    for window_id, (start_iso, end_iso) in SUB_WINDOWS.items():
        jobs.append({
            "sub_window": window_id,
            "sim_start_iso": start_iso,
            "sim_end_iso": end_iso,
            "cutoff_iso": CUTOFF_ISO,
            "n_in_coverage": len(COVERAGE_BY_WINDOW[window_id]),
        })
    return jobs


def _build_hubrich_jobs() -> list[dict]:
    """3 Hubrich 200-DMA baselines, one per sub-window."""
    return _build_btc_bh_jobs()  # same job shape


def _build_lrc_archived_jobs(app_config_path: str) -> list[dict]:
    """LRC archived baseline per (symbol, sub-window) in-coverage cell.

    Same coverage rule as the regime-allocation sweep so the comparison is
    apples-to-apples (pre-reg §4 notes — portfolio aggregate over in-coverage).
    """
    jobs = []
    for window_id, (start_iso, end_iso) in SUB_WINDOWS.items():
        for sym in COVERAGE_BY_WINDOW[window_id]:
            jobs.append({
                "symbol": sym,
                "sub_window": window_id,
                "sim_start_iso": start_iso,
                "sim_end_iso": end_iso,
                "cutoff_iso": CUTOFF_ISO,
                "app_config_path": app_config_path,
            })
    return jobs


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation + halt detection
# ─────────────────────────────────────────────────────────────────────────────


def _aggregate_primary_portfolio(primary_results: list[dict]) -> dict[str, dict]:
    """Pre-reg §4 — portfolio aggregate per sub-window for primary pass (vol=30).

    Returns {window_id → {strategy_total_return_usd, n_in_coverage,
    per_symbol_net_pnl_usd, n_trades_total, bankruptcy_count_total}}.
    Only cells at vol_target=PRIMARY_VOL_TARGET are aggregated.
    """
    by_window: dict[str, dict] = {}
    for r in primary_results:
        if abs(r["vol_target"] - PRIMARY_VOL_TARGET) > 1e-9:
            continue
        win = r["sub_window"]
        agg = by_window.setdefault(win, {
            "window_id": win,
            "vol_target": PRIMARY_VOL_TARGET,
            "per_symbol_net_pnl_usd": {},
            "strategy_total_return_usd": 0.0,
            "n_trades_total": 0,
            "bankruptcy_count_total": 0,
            "insufficient_data_count": 0,
            "in_coverage_symbols": list(COVERAGE_BY_WINDOW[win]),
            "n_in_coverage": len(COVERAGE_BY_WINDOW[win]),
        })
        agg["per_symbol_net_pnl_usd"][r["symbol"]] = r["net_pnl_usd"]
        agg["strategy_total_return_usd"] += r["net_pnl_usd"]
        agg["n_trades_total"] += int(r.get("n_trades", 0))
        agg["bankruptcy_count_total"] += int(r.get("bankruptcy_count", 0))
        if r.get("insufficient_data", False):
            agg["insufficient_data_count"] += 1
    # Round at the end to avoid floating-point drift on the sum.
    for agg in by_window.values():
        agg["strategy_total_return_usd"] = round(agg["strategy_total_return_usd"], 4)
        agg["per_symbol_net_pnl_usd"] = {
            sym: round(v, 4) for sym, v in agg["per_symbol_net_pnl_usd"].items()
        }
    return by_window


def _check_halt_after_a(primary_results: list[dict]) -> dict:
    """Pre-reg §10.4 halt H1 (universal bankruptcy) + H2 (signal degenerate).

    Both halts evaluated on Window A primary at vol_target=30%. Thresholds:
    ≥75% of in-coverage (8 → ≥6).

    Returns dict with halt bool, halt_reasons list, per-symbol breach details.

    NIT note (per review 2026-05-14): worker exceptions in
    `_process_regime_allocation_cell` return cells with n_trades=0. These
    cells count toward H2 (signal degenerate) by virtue of n_trades < 5,
    even when the actual root cause is infra (disk, pool exception, transient
    network). Operator should inspect halt_diagnostic.json post-firing to
    distinguish "signal didn't fire" from "compute didn't run" — the
    per_symbol_a_cell map carries n_trades + bankruptcy_count + net_pnl
    for each cell to surface this. A future iteration could split errored
    cells into a separate `h2_n_errored_cells` bucket; deferred since
    operator review is the safety net.
    """
    in_coverage_a = COVERAGE_BY_WINDOW["A"]
    n_in_cov_a = len(in_coverage_a)
    threshold = _halt_count_threshold(n_in_cov_a)  # 6 for n=8

    # Filter to Window A primary cells only.
    a_cells = [
        r for r in primary_results
        if r["sub_window"] == "A"
        and abs(r["vol_target"] - PRIMARY_VOL_TARGET) < 1e-9
        and r["symbol"] in in_coverage_a
    ]

    # H1 — symbols with at least one BANKRUPT event in this cell.
    h1_bankrupt_symbols = sorted(
        r["symbol"] for r in a_cells
        if int(r.get("bankruptcy_count", 0)) > 0
    )
    h1_fires = len(h1_bankrupt_symbols) >= threshold

    # H2 — symbols with n_trades < N_TRADES_MIN_FOR_ELIGIBILITY (== 5).
    h2_low_trade_symbols = sorted(
        r["symbol"] for r in a_cells
        if int(r.get("n_trades", 0)) < N_TRADES_MIN_FOR_ELIGIBILITY
    )
    h2_fires = len(h2_low_trade_symbols) >= threshold

    halt = h1_fires or h2_fires
    halt_reasons: list[str] = []
    if h1_fires:
        halt_reasons.append("H1_universal_bankruptcy")
    if h2_fires:
        halt_reasons.append("H2_signal_degenerate")

    return {
        "halt": halt,
        "halt_reasons": halt_reasons,
        "window_evaluated": "A",
        "vol_target_evaluated": PRIMARY_VOL_TARGET,
        "n_in_coverage": n_in_cov_a,
        "halt_count_threshold": threshold,
        "halt_fraction_threshold": HALT_FRACTION_THRESHOLD,
        "h1_n_symbols_bankrupt": len(h1_bankrupt_symbols),
        "h1_symbols_bankrupt": h1_bankrupt_symbols,
        "h2_n_symbols_low_trade": len(h2_low_trade_symbols),
        "h2_symbols_low_trade": h2_low_trade_symbols,
        "h2_n_trades_min_for_eligibility": N_TRADES_MIN_FOR_ELIGIBILITY,
        "per_symbol_a_cell": {
            r["symbol"]: {
                "n_trades": r.get("n_trades", 0),
                "bankruptcy_count": r.get("bankruptcy_count", 0),
                "net_pnl_usd": r.get("net_pnl_usd", 0.0),
            } for r in a_cells
        },
    }


def _build_signal_diagnostics(all_primary: list[dict]) -> dict:
    """Per-(symbol, sub-window, vol_target) signal-firing diagnostic.

    Used by operator review post-halt or as a forensic appendix.
    """
    diag: dict[str, dict] = {}
    for r in all_primary:
        sym = r["symbol"]
        win = r["sub_window"]
        vt = r["vol_target"]
        key = f"{sym}_{win}_{vt:.2f}"
        diag[key] = {
            "symbol": sym,
            "sub_window": win,
            "vol_target": vt,
            "n_trades": r.get("n_trades", 0),
            "bankruptcy_count": r.get("bankruptcy_count", 0),
            "exit_reasons": r.get("exit_reasons", {}),
            "insufficient_data": r.get("insufficient_data", False),
        }
    return diag


def _build_cost_attribution(all_results: list[dict]) -> dict:
    """Per-cell cost split — gross, slippage, funding, net.

    Surfaces cost-dominated flags per pre-reg §5.4 (>30% of gross_pnl in
    >50% of cells = "cost-dominated outcome" flag, informational only).
    """
    out: dict[str, dict] = {}
    n_cost_dominated = 0
    n_cells_with_trades = 0
    for r in all_results:
        sym = r["symbol"]
        win = r["sub_window"]
        vt = r["vol_target"]
        key = f"{sym}_{win}_{vt:.2f}"
        gross = float(r.get("gross_pnl_usd", 0.0))
        slip = float(r.get("total_slippage_usd", 0.0))
        funding = float(r.get("total_funding_usd", 0.0))
        net = float(r.get("net_pnl_usd", 0.0))
        total_cost = slip + funding
        out[key] = {
            "symbol": sym, "sub_window": win, "vol_target": vt,
            "gross_pnl_usd": round(gross, 4),
            "total_slippage_usd": round(slip, 4),
            "total_funding_usd": round(funding, 4),
            "total_cost_usd": round(total_cost, 4),
            "net_pnl_usd": round(net, 4),
        }
        if int(r.get("n_trades", 0)) > 0:
            n_cells_with_trades += 1
            if abs(gross) > 0 and abs(total_cost) / abs(gross) > 0.30:
                n_cost_dominated += 1
    return {
        "per_cell": out,
        "n_cells_with_trades": n_cells_with_trades,
        "n_cost_dominated": n_cost_dominated,
        "cost_dominated_flag": (
            n_cells_with_trades > 0
            and n_cost_dominated / n_cells_with_trades > 0.50
        ),
        "cost_dominated_threshold_gross_pct": 30.0,
        "cost_dominated_flag_min_cells_fraction": 0.50,
    }


def _build_bankruptcy_diagnostics(all_results: list[dict]) -> dict:
    """Per-cell bankruptcy events."""
    out: dict[str, dict] = {}
    n_total = 0
    for r in all_results:
        bc = int(r.get("bankruptcy_count", 0))
        if bc > 0:
            sym = r["symbol"]
            win = r["sub_window"]
            vt = r["vol_target"]
            key = f"{sym}_{win}_{vt:.2f}"
            out[key] = {
                "symbol": sym, "sub_window": win, "vol_target": vt,
                "bankruptcy_count": bc,
                "n_trades": r.get("n_trades", 0),
                "net_pnl_usd": r.get("net_pnl_usd", 0.0),
            }
            n_total += bc
    return {"per_cell": out, "n_total_bankruptcies": n_total}


# ─────────────────────────────────────────────────────────────────────────────
# Coverage utility (verification — does NOT decide coverage)
# ─────────────────────────────────────────────────────────────────────────────


def _coverage_verification(app_config_path: str) -> dict:
    """Empirically verify the hardcoded COVERAGE_BY_WINDOW table.

    Loads daily bars for each symbol; for each (symbol, sub_window) pair,
    checks whether the symbol had ≥WARMUP_DAILY_BARS daily bars before the
    sub_window_start. Writes coverage.json with both the hardcoded table
    and the verification result.
    """
    from backtest import get_cached_data
    from dateutil.relativedelta import relativedelta

    result: dict[str, dict] = {}
    for sym in CURATED_SYMBOLS:
        df = get_cached_data(
            sym, "1d",
            start_date=_iso_to_utc_dt(SUB_WINDOWS["A"][0]) - relativedelta(months=24),
        )
        if df.empty:
            result[sym] = {
                "first_bar": None,
                "daily_bars_pre_A": 0,
                "daily_bars_pre_B": 0,
                "daily_bars_pre_C": 0,
                "in_coverage": {"A": False, "B": False, "C": False},
            }
            continue
        first_bar = df.index[0]
        first_bar_naive = (
            first_bar.tz_localize(None) if first_bar.tz is not None else first_bar
        )
        per_window: dict[str, dict] = {}
        for win_id, (start_iso, _end_iso) in SUB_WINDOWS.items():
            start_dt = _iso_to_utc_dt(start_iso)
            start_naive = (
                start_dt.replace(tzinfo=None) if start_dt.tzinfo else start_dt
            )
            idx = df.index.tz_localize(None) if df.index.tz is not None else df.index
            n_bars_pre = int((idx < start_naive).sum())
            per_window[win_id] = {
                "daily_bars_pre_window_start": n_bars_pre,
                "in_coverage_empirical": n_bars_pre >= WARMUP_DAILY_BARS,
                "in_coverage_pre_reg": _is_in_coverage(sym, win_id),
            }
        result[sym] = {
            "first_bar": str(first_bar_naive),
            "per_window": per_window,
        }
    return {
        "warmup_daily_bars_required": WARMUP_DAILY_BARS,
        "coverage_by_window_pre_reg": {
            w: list(s) for w, s in COVERAGE_BY_WINDOW.items()
        },
        "n_in_coverage_pre_reg": {w: len(s) for w, s in COVERAGE_BY_WINDOW.items()},
        "per_symbol": result,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Multiprocess runner + worker error aggregation
# ─────────────────────────────────────────────────────────────────────────────


def _summarize_worker_errors(results: list[dict]) -> str | None:
    """Aggregate `error` across results. Mirrors r1/r3 pattern."""
    errors = [r.get("error") for r in results if r.get("error") is not None]
    if not errors:
        return None
    distinct = sorted(set(errors))
    return (
        f"[regime_allocation_sweep] {len(errors)} workers errored "
        f"({len(distinct)} distinct): {distinct}"
    )


def _emit_worker_error_summary(results: list[dict]) -> None:
    err_summary = _summarize_worker_errors(results)
    if err_summary:
        sys.stderr.write(err_summary + "\n")


def _run_jobs_parallel(
    jobs: list[dict], workers: int, label: str, worker_fn=None,
) -> list[dict]:
    """Run jobs in parallel via multiprocessing.Pool. Progress on stderr.

    Trial registration (claim-then-execute) happens in THIS parent process,
    never in the pool child: child crashes leave a 'pending' row that still
    counts toward N. pool.map preserves order, so results[i] <-> jobs[i] <->
    trial_ids[i]. Only cell workers that run calculate_metrics produce trials;
    arithmetic baselines (btc_bh / hubrich) are gated out.
    """
    if not jobs:
        return []
    worker_fn = worker_fn or _process_regime_allocation_cell

    # NOTE: function-IDENTITY matching. A future caller that wraps the worker in
    # functools.partial / a lambda / a bound method would NOT be `in` this tuple
    # and would silently skip trial registration (N under-count). Register any
    # new trial-producing worker here by its bare function object.
    produces_trials = worker_fn in (
        _process_regime_allocation_cell,
        _process_lrc_archived_baseline_cell,
    )
    trial_ids: list[int | None] = [None] * len(jobs)
    if produces_trials:
        for i, job in enumerate(jobs):
            combo = {k: job[k] for k in ("symbol", "sub_window", "vol_target") if k in job}
            trial_ids[i] = claim_trial(
                source="regime_allocation_sweep",
                symbol=job.get("symbol"),
                combo=combo,
                window_label=str(job.get("sub_window") or job.get("window") or ""),
            )

    sys.stderr.write(
        f"[regime_allocation_sweep] {label}: {len(jobs)} jobs × {workers} workers...\n"
    )
    t0 = time.monotonic()
    with Pool(workers) as pool:
        results = pool.map(worker_fn, jobs)
    elapsed = time.monotonic() - t0
    sys.stderr.write(
        f"[regime_allocation_sweep] {label}: completed in {elapsed:.1f}s\n"
    )

    if produces_trials:
        # POST-COMPUTE finalize loop runs AFTER pool.map returns — every
        # backtest cell has already executed and `results` is in memory. A
        # finalize failure here (e.g. a persistent DB lock) must NOT propagate:
        # that would discard hours of compute AND leave completed trials stuck
        # 'pending'. Tolerate per-trial finalize failures — the orphan 'pending'
        # row still counts toward N, and `results` is returned regardless. The
        # CLAIM loop above stays LOUD on purpose: a claim failure before compute
        # is cheap to abort, so it is allowed to propagate.
        for tid, res in zip(trial_ids, results):
            if tid is None:
                continue
            err = res.get("error") if isinstance(res, dict) else None
            try:
                if err:
                    finalize_trial(tid, status="failed", error=str(err))
                else:
                    finalize_trial(
                        tid, status="ok",
                        metrics=res if isinstance(res, dict) else None,
                    )
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(
                    f"[regime_allocation_sweep] finalize_trial failed for "
                    f"trial {tid}: {type(e).__name__}: {e} (orphan 'pending' "
                    f"row preserved; compute results kept)\n"
                )

    _emit_worker_error_summary(results)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Partial window sequencing — pre-reg §10.4 BLOCK #2 review fix 2026-05-14.
# ─────────────────────────────────────────────────────────────────────────────


def _validate_partial_window_sequencing(
    args, output_dir: Path,
) -> tuple[bool, str | None]:
    """Refuse --window B/C standalone if Window A halt check hasn't run.

    Pre-reg §10.4 — halt evaluation runs on Window A primary at
    vol_target=30%. Running B or C standalone skips this evaluation;
    combined with verdict tool's §4.6 halt-guard (which defaults halt to
    False when halt_diagnostic.json is missing), this would mask a
    partial-run as a favorable verdict.

    BLOCK #2 review fix 2026-05-14 — sweep tool refuses --window B|C
    when sweep_primary_A.json or halt_diagnostic.json missing, OR when
    halt_diagnostic reports halt=True (B+C are halted per pre-reg §10.4).

    Returns:
        (ok, error_message) — ok=True means execution may proceed.
    """
    if args.window not in ("B", "C"):
        return True, None
    a_results = output_dir / "sweep_primary_A.json"
    halt_diag = output_dir / "halt_diagnostic.json"
    if not a_results.exists():
        return False, (
            f"--window {args.window} requires sweep_primary_A.json to "
            f"exist (Window A primary must run first per pre-reg §10.4 "
            f"halt evaluation). Run --window A or --window all first."
        )
    if not halt_diag.exists():
        return False, (
            f"--window {args.window} requires halt_diagnostic.json to "
            f"exist. Run --window A first to evaluate §10.4 halt."
        )
    try:
        with open(halt_diag) as f:
            halt_data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"Cannot read halt_diagnostic.json: {exc}"
    if halt_data.get("halt") is True:
        return False, (
            f"--window {args.window} blocked: halt_diagnostic.json reports "
            f"halt=True (reasons={halt_data.get('halt_reasons', [])}). "
            f"B+C sweep is halted per pre-reg §10.4 — sensitivity sweep "
            f"is also halted. Verdict tool will classify per §4.6 "
            f"asymmetric halt-guard."
        )
    return True, None


# ─────────────────────────────────────────────────────────────────────────────
# Manifest
# ─────────────────────────────────────────────────────────────────────────────


def _build_manifest(args, halt_diag) -> dict:
    return {
        "harness": "tools.regime_allocation_sweep",
        "spec_ref": (
            "docs/superpowers/plans/2026-05-14-regime-allocation-phase2-pre-reg.md"
        ),
        "epic_spec_ref": (
            "docs/superpowers/specs/es/2026-05-13-epic-regime-allocation-strategy-pivot.md"
        ),
        "ran_at_iso": datetime.now(timezone.utc).isoformat(),
        "code_commit": _git_commit(),
        "cutoff_iso": CUTOFF_ISO,
        "sub_windows": {
            w: {"start_iso": s, "end_iso": e}
            for w, (s, e) in SUB_WINDOWS.items()
        },
        "params_locked": {
            "zarattini_lookbacks": list(ZARATTINI_LOOKBACKS),
            "warmup_daily_bars": WARMUP_DAILY_BARS,
            "primary_vol_target": PRIMARY_VOL_TARGET,
            "sensitivity_vol_targets": list(SENSITIVITY_VOL_TARGETS),
            "n_trades_min_for_eligibility": N_TRADES_MIN_FOR_ELIGIBILITY,
            "halt_fraction_threshold": HALT_FRACTION_THRESHOLD,
            "halt_count_threshold_per_window": {
                w: _halt_count_threshold(len(s))
                for w, s in COVERAGE_BY_WINDOW.items()
            },
            "spot_fee_bps_per_fill": SPOT_FEE_BPS_PER_FILL,
            "hubrich_sma_days": HUBRICH_SMA_DAYS,
        },
        "symbols_curated": list(CURATED_SYMBOLS),
        "coverage_by_window": {
            w: list(s) for w, s in COVERAGE_BY_WINDOW.items()
        },
        "halt_after_a_fired": bool(halt_diag and halt_diag.get("halt")),
        "halt_after_a_reasons": (
            halt_diag.get("halt_reasons", []) if halt_diag else []
        ),
        "leakage_check": {
            "all_sub_windows_end_le_cutoff": all(
                _iso_to_utc_dt(end_iso) <= _iso_to_utc_dt(CUTOFF_ISO)
                for _, end_iso in SUB_WINDOWS.values()
            ),
            "method": (
                "Worker slices all OHLCV dfs to index < cutoff before "
                "passing to simulate_strategy; sub-window end == cutoff "
                "(Window C only) → all bars strictly before cutoff."
            ),
            "holdout_isolation_policy": (
                "Locked holdout dataset is read-only and out of scope until "
                "Phase 5 per #246 + #322; sweep tool never references it "
                "(verified by tests/test_holdout_isolation.py)."
            ),
        },
        "cli_args": vars(args),
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def parse_args():
    p = argparse.ArgumentParser(
        description="Regime-allocation sweep harness (epic #338 Phase 3)."
    )
    p.add_argument(
        "--window", choices=["A", "B", "C", "all"], default="all",
        help="Which sub-window(s) to sweep (default: all).",
    )
    p.add_argument(
        "--baselines-only", action="store_true",
        help="Run only the 3 baselines (BTC B&H + Hubrich + LRC archived).",
    )
    p.add_argument(
        "--skip-baselines", action="store_true",
        help="Skip baselines (assume existing JSON in OUTPUT_DIR).",
    )
    p.add_argument(
        "--skip-sensitivity", action="store_true",
        help="Skip the sensitivity sweep (primary + baselines only).",
    )
    p.add_argument(
        "--workers", type=int, default=min(8, cpu_count()),
        help="Parallel workers (default: min(8, cpu_count)).",
    )
    p.add_argument(
        "--smoke", action="store_true",
        help="Smoke mode: 1 cell only (BTCUSDT, Window C, vol=0.30).",
    )
    p.add_argument(
        "--strict-halt", action="store_true",
        help="Exit code 2 when §10.4 halt fires (default: 0 — halt is "
             "expected behavior; verdict tool classifies).",
    )
    p.add_argument(
        "--app-config", type=str, default=str(APP_CONFIG_PATH_DEFAULT),
        help=f"Path to app config (default: {APP_CONFIG_PATH_DEFAULT}).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    app_config_path = args.app_config

    # Pre-reg §10.4 + BLOCK #2 review fix 2026-05-14: refuse --window B/C
    # standalone without prior Window A halt evaluation. Halt is part of
    # the methodology, not optional — see _validate_partial_window_sequencing.
    ok, err = _validate_partial_window_sequencing(args, OUTPUT_DIR)
    if not ok:
        sys.stderr.write(f"[regime_allocation_sweep] ABORT: {err}\n")
        return 1

    # Smoke mode — quick validation of harness wiring.
    if args.smoke:
        sys.stderr.write("[regime_allocation_sweep] SMOKE MODE: 1 cell only\n")
        smoke_job = {
            "symbol": "BTCUSDT",
            "sub_window": "C",
            "vol_target": PRIMARY_VOL_TARGET,
            "sim_start_iso": SUB_WINDOWS["C"][0],
            "sim_end_iso": SUB_WINDOWS["C"][1],
            "cutoff_iso": CUTOFF_ISO,
            "app_config_path": app_config_path,
        }
        result = _process_regime_allocation_cell(smoke_job)
        _save_json(OUTPUT_DIR / "smoke_test.json", result)
        sys.stderr.write(
            f"[regime_allocation_sweep] SMOKE result: "
            f"{json.dumps(result, indent=2, default=str)}\n"
        )
        return 0

    # Coverage verification (always runs; quick).
    sys.stderr.write(
        "[regime_allocation_sweep] computing coverage verification...\n"
    )
    coverage_report = _coverage_verification(app_config_path)
    _save_json(OUTPUT_DIR / "coverage.json", coverage_report)

    # NIT review fix 2026-05-14 — surface coverage drift (empirical vs
    # pre-reg-locked table). Hardcoded COVERAGE_BY_WINDOW remains
    # authoritative (pre-reg-locked) but operator should know if data
    # refresh shifted any (symbol, window) classification.
    for sym, info in coverage_report.get("per_symbol", {}).items():
        for win, w_info in info.get("per_window", {}).items():
            if w_info.get("in_coverage_empirical") != w_info.get(
                "in_coverage_pre_reg"
            ):
                sys.stderr.write(
                    f"[regime_allocation_sweep] WARNING: coverage mismatch "
                    f"for {sym} Window {win}: empirical="
                    f"{w_info.get('in_coverage_empirical')} vs pre-reg="
                    f"{w_info.get('in_coverage_pre_reg')}. Using pre-reg "
                    f"table (locked); operator review recommended.\n"
                )

    # Baselines (BTC B&H, Hubrich, LRC archived).
    if not args.skip_baselines:
        # BTC B&H + Hubrich — 3 + 3 = 6 cheap sequential cells.
        btc_bh_jobs = _build_btc_bh_jobs()
        hubrich_jobs = _build_hubrich_jobs()
        sys.stderr.write(
            f"[regime_allocation_sweep] baselines BTC-BH + Hubrich: "
            f"{len(btc_bh_jobs) + len(hubrich_jobs)} cells sequential...\n"
        )
        btc_bh_results = [_compute_btc_bh_baseline(j) for j in btc_bh_jobs]
        hubrich_results = [_compute_hubrich_baseline(j) for j in hubrich_jobs]
        for win in SUB_WINDOWS:
            _save_json(
                OUTPUT_DIR / f"baseline_btc_bh_{win}.json",
                next(r for r in btc_bh_results if r["sub_window"] == win),
            )
            _save_json(
                OUTPUT_DIR / f"baseline_hubrich_{win}.json",
                next(r for r in hubrich_results if r["sub_window"] == win),
            )

        # LRC archived — parallelized (25 in-coverage cells).
        lrc_jobs = _build_lrc_archived_jobs(app_config_path)
        lrc_results = _run_jobs_parallel(
            lrc_jobs, args.workers, "baseline LRC archived",
            worker_fn=_process_lrc_archived_baseline_cell,
        )
        for win in SUB_WINDOWS:
            win_results = [r for r in lrc_results if r["sub_window"] == win]
            _save_json(
                OUTPUT_DIR / f"baseline_lrc_archived_{win}.json", win_results,
            )

    if args.baselines_only:
        sys.stderr.write("[regime_allocation_sweep] --baselines-only: done.\n")
        _save_json(
            OUTPUT_DIR / "manifest.json",
            _build_manifest(args, halt_diag=None),
        )
        return 0

    # Primary sweep — start with Window A, evaluate halt before B+C.
    halt_diag = None
    primary_a: list[dict] = []
    primary_b: list[dict] = []
    primary_c: list[dict] = []

    if args.window in ("A", "all"):
        a_jobs = [
            j for j in _build_primary_jobs(app_config_path)
            if j["sub_window"] == "A"
        ]
        primary_a = _run_jobs_parallel(a_jobs, args.workers, "primary A")
        _save_json(OUTPUT_DIR / "sweep_primary_A.json", primary_a)

        # Halt check on Window A primary.
        halt_diag = _check_halt_after_a(primary_a)
        _save_json(OUTPUT_DIR / "halt_diagnostic.json", halt_diag)
        if halt_diag["halt"]:
            sys.stderr.write(
                f"[regime_allocation_sweep] §10.4 HALT FIRED: "
                f"{halt_diag['halt_reasons']}\n"
            )
            sys.stderr.write(
                f"  H1 bankrupt symbols ({halt_diag['h1_n_symbols_bankrupt']}/"
                f"{halt_diag['n_in_coverage']}): "
                f"{halt_diag['h1_symbols_bankrupt']}\n"
            )
            sys.stderr.write(
                f"  H2 low-trade symbols ({halt_diag['h2_n_symbols_low_trade']}"
                f"/{halt_diag['n_in_coverage']}, threshold "
                f"<{N_TRADES_MIN_FOR_ELIGIBILITY} trades): "
                f"{halt_diag['h2_symbols_low_trade']}\n"
            )
            sys.stderr.write(
                "[regime_allocation_sweep] Halting B+C primary + sensitivity "
                "sweep per pre-reg §10.4. Verdict tool will apply §4.6 "
                "asymmetric halt-guard.\n"
            )
            # Write minimal diagnostics + manifest, then exit.
            _save_json(
                OUTPUT_DIR / "signal_diagnostics.json",
                _build_signal_diagnostics(primary_a),
            )
            _save_json(
                OUTPUT_DIR / "cost_attribution.json",
                _build_cost_attribution(primary_a),
            )
            _save_json(
                OUTPUT_DIR / "bankruptcy_diagnostics.json",
                _build_bankruptcy_diagnostics(primary_a),
            )
            _save_json(
                OUTPUT_DIR / "manifest.json",
                _build_manifest(args, halt_diag=halt_diag),
            )
            return 2 if args.strict_halt else 0

    # Primary B + C (only reached if halt NOT fired).
    if args.window in ("B", "all"):
        b_jobs = [
            j for j in _build_primary_jobs(app_config_path)
            if j["sub_window"] == "B"
        ]
        primary_b = _run_jobs_parallel(b_jobs, args.workers, "primary B")
        _save_json(OUTPUT_DIR / "sweep_primary_B.json", primary_b)

    if args.window in ("C", "all"):
        c_jobs = [
            j for j in _build_primary_jobs(app_config_path)
            if j["sub_window"] == "C"
        ]
        primary_c = _run_jobs_parallel(c_jobs, args.workers, "primary C")
        _save_json(OUTPUT_DIR / "sweep_primary_C.json", primary_c)

    all_primary = primary_a + primary_b + primary_c

    # Sensitivity sweep (only if halt NOT fired AND not skipped).
    all_sensitivity: list[dict] = []
    if not args.skip_sensitivity:
        sens_jobs = _build_sensitivity_jobs(app_config_path)
        if args.window != "all":
            sens_jobs = [j for j in sens_jobs if j["sub_window"] == args.window]
        sens_results = _run_jobs_parallel(
            sens_jobs, args.workers, "sensitivity",
        )
        # Split by window for output.
        for win in SUB_WINDOWS:
            if args.window not in (win, "all"):
                continue
            win_results = [r for r in sens_results if r["sub_window"] == win]
            _save_json(
                OUTPUT_DIR / f"sweep_sensitivity_{win}.json", win_results,
            )
        all_sensitivity = sens_results

    # Diagnostics — span primary + sensitivity.
    all_results = all_primary + all_sensitivity
    _save_json(
        OUTPUT_DIR / "signal_diagnostics.json",
        _build_signal_diagnostics(all_primary),
    )
    _save_json(
        OUTPUT_DIR / "cost_attribution.json",
        _build_cost_attribution(all_results),
    )
    _save_json(
        OUTPUT_DIR / "bankruptcy_diagnostics.json",
        _build_bankruptcy_diagnostics(all_results),
    )

    # Manifest.
    _save_json(
        OUTPUT_DIR / "manifest.json",
        _build_manifest(args, halt_diag=halt_diag),
    )
    sys.stderr.write("[regime_allocation_sweep] complete.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
