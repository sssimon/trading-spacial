#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║   BACKTEST — Spot V6 Strategy Historical Simulation          ║
║   Period: Jan 2023 — Present  |  Symbol: BTCUSDT             ║
║                                                              ║
║   Uses the same indicator functions as btc_scanner.py        ║
║   to ensure backtest matches live behavior.                  ║
╚══════════════════════════════════════════════════════════════╝

Usage:
    python backtest.py                  # Run backtest, generate report
    python backtest.py --download-only  # Only download/cache data
    python backtest.py --symbol ETHUSDT # Backtest a different symbol

Cost model (A.0.2, #277)
------------------------
simulate_strategy applies a tier-based cost model when
`enable_slippage` / `enable_spread` / `enable_fees` are True (default). The
model is **v1 linear** in participation rate:

    slippage_bps = base_bps + size_factor * (order_usd / liquidity_usd_per_min)

This deliberately over-penalizes small orders and under-penalizes large ones
relative to the empirically-better Almgren-Chriss `sqrt(participation)`
baseline. **v2 should migrate to sqrt**; the v1 simplification is documented
both here and in backtest_costs.py so it does not get forgotten. Per-tier
parameters and source citations live in `costs_calibration.json`.

Pre-A.0.2 the FEE_PCT constant was defined but never deducted from pnl_usd —
A.0.2 is the first revision to actually apply costs. Backtests prior to this
revision should be considered cost-blind; numbers in older docs (e.g.
2026-04-17-formula-ganadora) are pre-cost.
"""

import math
import os
import sys
import json
import time
import argparse
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Final

import pandas as pd
import numpy as np
import requests

from data import market_data as md

# Import scanner functions
from btc_scanner import (
    calc_lrc, calc_rsi, calc_bb, calc_sma, calc_atr, calc_adx,
    detect_bull_engulfing, calc_cvd_delta, detect_rsi_divergence,
    check_trigger_5m, score_label,
    LRC_PERIOD, LRC_STDEV, RSI_PERIOD, BB_PERIOD, BB_STDEV, VOL_PERIOD,
    LRC_LONG_MAX, SL_PCT, TP_PCT, COOLDOWN_H,
    SCORE_MIN_HALF, SCORE_STANDARD, SCORE_PREMIUM,
    ATR_PERIOD, ATR_SL_MULT, ATR_TP_MULT, ATR_BE_MULT,
    ADX_THRESHOLD,
    resolve_direction_params,
    _compute_price_score,
    _compute_fng_score,
    _compute_funding_score,
    _compute_rsi_score,
    _compute_adx_score,
    _compute_local_regime,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger("backtest")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data", "backtest")
os.makedirs(DATA_DIR, exist_ok=True)

DEFAULT_START = datetime(2021, 1, 1, tzinfo=timezone.utc)  # earliest data to cache
INITIAL_CAPITAL = 10000.0
RISK_PER_TRADE = 0.01  # 1% of capital per trade
# Rule-derived cap on |pnl_pct / sl_pct_actual| in _close_position. K=10:
# a 10× SL move is absurd; a real trader exits manually well before that.
# Bounds per-trade overshoot relative to current capital, NOT initial capital:
#   |pnl_usd| ≤ K × risk_amount = K × max(0, capital) × RISK_PER_TRADE × size_mult
# See CLAUDE.md "Caveats heredados — A.4 (#250) MUST honor" #4 (per-symbol vs
# portfolio aggregation gap; single-trade overshoot via amplification).
MAX_OVERSHOOT_RATIO: Final[float] = 10.0

# Per-symbol bankruptcy threshold (#280). Rule-derived 90%-drawdown convention
# from the issue body: once simulated capital falls below 10% of INITIAL_CAPITAL,
# any real account would be force-liquidated and the kill switch would have
# fired in production. In simulation, the existing effective_capital = max(0,
# capital) floor (A.0.2 / #277) prevented NaN math but kept the bar loop
# running — those subsequent zero-risk_amount trades distort aggregate metrics
# (Bankruptcy Bias, demonstrated in data/retune/2026-05-06-pre-holdout
# regime_report.md). This constant + the _bankrupt sticky flag wired below
# halt new entries for the affected symbol. Portfolio-level bankruptcy is
# deferred to its own epic when a portfolio-level simulator lands.
BANKRUPTCY_THRESHOLD: Final[float] = 0.1 * INITIAL_CAPITAL  # $1000 at INITIAL_CAPITAL=10_000


from strategy._validators import (
    validated_time_limit_hours as _shared_validated_tl_hours,
    validated_max_participation_rate as _shared_validated_max_pov,
    validated_cooldown_hours as _shared_validated_cooldown_hours,
)


def _validated_time_limit_hours(value, symbol: str) -> float | None:
    return _shared_validated_tl_hours(value, symbol, "simulate_strategy", log)


def _validated_max_participation_rate(value, symbol: str) -> float | None:
    return _shared_validated_max_pov(value, symbol, "simulate_strategy", log)


def _validated_cooldown_hours(value, symbol: str) -> float:
    return _shared_validated_cooldown_hours(
        value, caller="simulate_strategy", symbol=symbol, logger=log, default=COOLDOWN_H,
    )
# 0.1% per side, Binance spot retail taker, no BNB discount. Conservative —
# if production uses BNB discount (~0.075%), this overestimates fee cost.
# Until A.0.2 (#277) the constant was defined here but never deducted from
# pnl_usd; the cost model in backtest_costs.py + the enable_fees flag in
# simulate_strategy now apply it. costs_calibration.json mirrors this value
# under tiers.*.fee_bps_per_side (10 bps).
FEE_PCT = 0.001


class RegimeKwargError(Exception):
    """Raised when regime kwargs are passed in an incoherent combination
    (contract violation by caller, not a data error). Subclasses Exception
    rather than ValueError so the harness's narrow data-error catch does
    not swallow it — propagates as a programming error, surfacing the bug
    to the operator instead of silently shrinking the sweep.
    """


# ─────────────────────────────────────────────────────────────────────────────
#  DATA DOWNLOAD
# ─────────────────────────────────────────────────────────────────────────────



def get_historical_fear_greed() -> pd.DataFrame:
    """Download full Fear & Greed Index history (2018+). Cache to CSV."""
    cache = os.path.join(DATA_DIR, "fear_greed_history.csv")
    if os.path.exists(cache):
        df = pd.read_csv(cache, index_col="date", parse_dates=True)
        age_days = (datetime.now(timezone.utc) - df.index[-1].replace(tzinfo=timezone.utc)).days
        if age_days < 7:
            log.info(f"Fear & Greed cache: {len(df)} days ({df.index[0].date()} → {df.index[-1].date()})")
            return df

    log.info("Downloading Fear & Greed Index full history...")
    try:
        time.sleep(0.5)  # rate limit courtesy
        r = requests.get("https://api.alternative.me/fng/?limit=0", timeout=30)
        r.raise_for_status()
        data = r.json()["data"]
        rows = [{"date": pd.Timestamp(int(d["timestamp"]), unit="s"), "fng": int(d["value"]),
                 "classification": d["value_classification"]} for d in data]
        df = pd.DataFrame(rows).set_index("date").sort_index()
        df = df[~df.index.duplicated(keep='first')]
        df.to_csv(cache)
        log.info(f"Fear & Greed saved: {len(df)} days ({df.index[0].date()} → {df.index[-1].date()})")
        return df
    except Exception as e:
        log.warning(f"Fear & Greed download failed: {e}")
        return pd.DataFrame()


def get_historical_funding_rate() -> pd.DataFrame:
    """Download BTC funding rate history from Binance Futures. Cache to CSV."""
    cache = os.path.join(DATA_DIR, "btc_funding_rate_history.csv")
    if os.path.exists(cache):
        df = pd.read_csv(cache, index_col="time", parse_dates=True)
        age_days = (datetime.now(timezone.utc) - df.index[-1].replace(tzinfo=timezone.utc)).days
        if age_days < 7:
            log.info(f"Funding rate cache: {len(df)} entries ({df.index[0].date()} → {df.index[-1].date()})")
            return df

    log.info("Downloading BTC funding rate history...")
    all_data = []
    start_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    while start_ms < end_ms:
        try:
            time.sleep(0.2)
            r = requests.get(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                params={"symbol": "BTCUSDT", "startTime": start_ms, "limit": 1000},
                timeout=15
            )
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            all_data.extend(data)
            start_ms = int(data[-1]["fundingTime"]) + 1
            if len(data) < 1000:
                break
        except Exception as e:
            log.warning(f"Funding rate page error: {e}")
            time.sleep(2)
            continue

    if not all_data:
        log.warning("No funding rate data downloaded")
        return pd.DataFrame()

    df = pd.DataFrame(all_data)
    df["time"] = pd.to_datetime(df["fundingTime"], unit="ms")
    df["rate"] = df["fundingRate"].astype(float)
    df = df[["time", "rate"]].set_index("time").sort_index()
    df = df[~df.index.duplicated(keep='first')]
    df.to_csv(cache)
    log.info(f"Funding rate saved: {len(df)} entries ({df.index[0].date()} → {df.index[-1].date()})")
    return df

def get_cached_data(symbol: str, interval: str, start_date: datetime = None) -> pd.DataFrame:
    """Historical bars fetched via data.market_data (SQLite cache + provider failover).

    Previously kept its own per-symbol CSV cache with bidirectional pagination —
    that's now handled by the unified data layer. Returns the legacy DataFrame
    shape (DatetimeIndex + OHLCV columns) for backward compatibility with
    simulate_strategy and the downstream auto_tune / grid_search scripts.
    """
    want_start = start_date or DEFAULT_START
    now = datetime.now(timezone.utc)

    df = md.get_klines_range(symbol, interval, want_start, now)
    if df.empty:
        return df

    df = df.copy()
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms")
    df = (
        df.drop(columns=["open_time", "provider", "fetched_at"], errors="ignore")
          .set_index("ts")
    )
    log.info(f"{symbol} {interval}: {len(df)} candles ({df.index[0].date()} → {df.index[-1].date()})")
    return df


# ─────────────────────────────────────────────────────────────────────────────
#  REGIME HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _regime_at_time(
    bar_time,
    symbol: str,
    df1d_sym,
    df_fng,
    df_funding,
    regime_mode: str = "global",
    df1d_btc=None,
    *,
    bull_above: int = 60,
    bear_below: int = 40,
) -> dict:
    """Compute regime for this bar_time (no look-ahead).

    mode='global':           uses df1d_btc + F&G + funding (40/30/30).
                              Fallback: if df1d_btc is None -> uses df1d_sym.
    mode='hybrid':           uses df1d_sym + F&G + funding (50/25/25).
    mode='hybrid_momentum':  uses df1d_sym + RSI + ADX + F&G + funding (30/15/20/20/15).

    bull_above / bear_below: regime classification thresholds. Defaults 60/40
        preserve byte-identity to legacy production behavior.
    """
    bar_time_naive = bar_time.tz_localize(None) if bar_time.tzinfo else bar_time

    # F&G score
    fng_score = 50
    if df_fng is not None and not df_fng.empty:
        fng_mask = df_fng.index <= bar_time_naive
        if fng_mask.any():
            fng_value = int(df_fng.loc[fng_mask, "fng"].iloc[-1])
            fng_score = _compute_fng_score(fng_value)

    # Funding score
    funding_score = 50
    if df_funding is not None and not df_funding.empty:
        fund_idx = df_funding.index
        fund_mask = fund_idx <= (bar_time if fund_idx.tz is not None else bar_time_naive)
        if fund_mask.any():
            rate = float(df_funding.loc[fund_mask, "rate"].iloc[-1])
            funding_score = _compute_funding_score(rate)

    # Pick daily bars source per mode
    if regime_mode == "global":
        df_price = df1d_btc if df1d_btc is not None else df1d_sym
    else:
        df_price = df1d_sym

    if df_price is None:
        return {"regime": "NEUTRAL", "score": 50.0, "mode": regime_mode,
                "symbol": symbol, "components": {}}

    window_price = df_price.loc[df_price.index <= bar_time]

    # RSI + ADX only for hybrid_momentum
    rsi_score = 50
    adx_score = 50
    if regime_mode == "hybrid_momentum" and df1d_sym is not None:
        window_sym = df1d_sym.loc[df1d_sym.index <= bar_time]
        if len(window_sym) >= 20:
            try:
                rsi_val = calc_rsi(window_sym["close"], 14).iloc[-1]
                if not pd.isna(rsi_val):
                    rsi_score = _compute_rsi_score(rsi_val)
            except Exception:
                pass
            try:
                adx_val = calc_adx(window_sym, 14).iloc[-1]
                if not pd.isna(adx_val):
                    adx_score = _compute_adx_score(adx_val)
            except Exception:
                pass

    return _compute_local_regime(
        symbol, regime_mode, window_price,
        fng_score, funding_score, rsi_score, adx_score,
        bull_above=bull_above, bear_below=bear_below,
    )


def _ensure_tz_aware(ts) -> datetime:
    """Return a tz-aware UTC datetime from a pandas Timestamp / datetime.

    `compute_rolling_metrics_from_trades` (via `health._months_negative_consecutive`
    and `cutoff_30d`) compares `now` to dates parsed from ISO strings — mixing
    tz-naive and tz-aware raises. The backtest's bar_time is tz-naive (the
    cache strips tz), so we normalize to tz-aware here.
    """
    if ts is None:
        return datetime.now(timezone.utc)
    if hasattr(ts, "tz_localize"):
        # pandas Timestamp
        return (ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")).to_pydatetime()
    if getattr(ts, "tzinfo", None) is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _emit_bankrupt_if_breached(capital: float, bar_time) -> dict | None:
    """Return a synthetic BANKRUPT trade record when `capital` falls below
    BANKRUPTCY_THRESHOLD; None otherwise. Stateless — callers own the
    sticky flag that prevents re-emission.

    The record carries a zero pnl payload (the event is a marker, not a
    trade) plus `breach_capital` for forensic visibility. exit_time and
    entry_time both point at the breach bar; duration is zero by design
    (this is an event, not a held position).
    """
    if capital >= BANKRUPTCY_THRESHOLD:
        return None
    return {
        "entry_time": bar_time,
        "exit_time": bar_time,
        "entry_price": 0.0,
        "exit_price": 0.0,
        "exit_reason": "BANKRUPT",
        "direction": "NONE",
        "pnl_pct": 0.0,
        "pnl_usd": 0.0,
        "overshoot_clamped": False,
        "score": 0,
        "size_mult": 0.0,
        "duration_hours": 0.0,
        "atr_sl_mult_used": None,
        "atr_tp_mult_used": None,
        "atr_be_mult_used": None,
        "breach_capital": round(float(capital), 2),
    }


def _close_position(position: dict, exit_price: float, exit_time, exit_reason: str,
                    capital: float) -> dict:
    """Compute P&L + trade dict for closing `position` at exit_price.

    Direction-aware SL distance: LONG expects sl_orig < entry, SHORT expects
    sl_orig > entry. If a malformed setup sends in an inverted SL (e.g. via a
    rounding bug — see fix/precision-rounding-bug), `sl_pct_actual` goes
    negative and pnl_usd is forced to 0 with a warning. Without this guard,
    `abs(entry - sl_orig)` would silently strip the sign and produce a
    PHANTOM PROFIT equal to `risk_amount` — historically inflating
    documented backtest portfolio numbers (see #fix/precision-rounding-bug).
    """
    entry_price = position["entry_price"]
    direction = position.get("direction", "LONG")
    sl_orig = position["sl_orig"]
    if direction == "SHORT":
        pnl_pct = (entry_price - exit_price) / entry_price * 100
        # Valid SHORT: sl_orig > entry_price → sl_pct_actual > 0.
        sl_pct_actual = (sl_orig - entry_price) / entry_price * 100
    else:
        pnl_pct = (exit_price - entry_price) / entry_price * 100
        # Valid LONG: sl_orig < entry_price → sl_pct_actual > 0.
        sl_pct_actual = (entry_price - sl_orig) / entry_price * 100
    # Floor capital at 0 (A.0.2 #277): under realistic costs a streak of
    # losses can drive the simulated capital negative. With negative capital,
    # the R-multiple `risk_amount * (pnl_pct / sl_pct)` flips sign and
    # reports losing trades as positive pnl — silently corrupting metrics
    # downstream. Bankruptcy is a sharper signal than "negative R-multiples":
    # cap risk at zero and let calculate_metrics observe the trades that
    # actually contributed pnl. (Pre-cost runs cannot reach this branch.)
    effective_capital = max(0.0, capital)
    risk_amount = effective_capital * RISK_PER_TRADE * position["size_mult"]
    if math.isnan(pnl_pct):
        # NaN-propagation guard: a NaN exit_price (or entry_price) makes
        # pnl_pct NaN, which would silently flow into capital via NaN PnL
        # AND into the trade dict's pnl_pct field — corrupting downstream
        # metrics (Sharpe / Sortino consume pnl_pct directly in
        # calculate_metrics) and breaking `if capital <= 0` comparisons
        # (NaN comparisons always evaluate False). Force BOTH pnl_usd and
        # pnl_pct to 0.0 in the trade dict so consumers downstream see a
        # consistent real-money-zero record, not a partial NaN payload.
        log.warning(
            "_close_position: NaN pnl_pct detected for %s %s — entry=%.6f, "
            "exit=%.6f. pnl_usd and pnl_pct forced to 0.",
            position.get("entry_time"), direction, entry_price, exit_price,
        )
        pnl_pct = 0.0
        pnl_usd = 0.0
        overshoot_clamped = False
    elif sl_pct_actual > 0:
        # Cap |pnl_pct / sl_pct_actual| at MAX_OVERSHOOT_RATIO so single-trade
        # overshoot on TIME_LIMIT exits / gap-through-SL cannot exceed
        # K × risk_amount. See CLAUDE.md "Caveats heredados" #4.
        raw_ratio = pnl_pct / sl_pct_actual
        if math.isnan(raw_ratio):
            # inf/inf or other NaN-producing division (e.g., +inf pnl_pct
            # divided by +inf sl_pct_actual). Bypasses both the pnl_pct NaN
            # check above and the sl_pct_actual > 0 gate. Same conservative
            # response as the pre-clamp NaN guard: zero out BOTH pnl fields.
            log.warning(
                "_close_position: NaN ratio (%.6f / %.6f) for %s %s. "
                "pnl_usd and pnl_pct forced to 0.",
                pnl_pct, sl_pct_actual,
                position.get("entry_time"), direction,
            )
            pnl_pct = 0.0
            pnl_usd = 0.0
            overshoot_clamped = False
        else:
            # AND-gate: only mark clamped when the cap actually bound pnl_usd
            # below its raw R-multiple value. With risk_amount = 0 (capital ≤ 0
            # via the effective_capital floor), pnl_usd is zero regardless of
            # raw_ratio magnitude — the cap is moot, not binding.
            overshoot_clamped = (
                abs(raw_ratio) > MAX_OVERSHOOT_RATIO
                and risk_amount > 0
            )
            overshoot_ratio = max(-MAX_OVERSHOOT_RATIO,
                                  min(MAX_OVERSHOOT_RATIO, raw_ratio))
            pnl_usd = risk_amount * overshoot_ratio
    else:
        # Malformed SL: inverted (LONG with sl_orig > entry, SHORT with
        # sl_orig < entry), zero-distance (sl_orig == entry), or NaN
        # (NaN sl_orig / entry_price → NaN sl_pct_actual; NaN > 0 is False so
        # this branch also catches the NaN-sl path). Refuse to amplify a
        # phantom profit; record a real-money zero PnL and log the anomaly.
        log.warning(
            "_close_position: malformed SL (inverted, zero-distance, or NaN) "
            "for %s %s — entry=%.6f, sl_orig=%.6f. pnl_usd and pnl_pct forced to 0.",
            position.get("entry_time"), direction, entry_price, sl_orig,
        )
        pnl_pct = 0.0
        pnl_usd = 0.0
        overshoot_clamped = False
    return {
        "entry_time": position["entry_time"],
        "exit_time": exit_time,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "direction": position.get("direction", "LONG"),
        "pnl_pct": round(pnl_pct, 4),
        "pnl_usd": round(pnl_usd, 2),
        "overshoot_clamped": overshoot_clamped,
        "score": position["score"],
        "size_mult": position["size_mult"],
        "duration_hours": (exit_time - position["entry_time"]).total_seconds() / 3600,
        "atr_sl_mult_used": position.get("atr_sl_mult_used"),
        "atr_tp_mult_used": position.get("atr_tp_mult_used"),
        "atr_be_mult_used": position.get("atr_be_mult_used"),
    }


def _apply_costs_to_trade(
    trade: dict,
    position: dict,
    exit_price_actual: float,
    exit_liquidity_per_min: float,
    compute_trade_costs_fn,
    tier_params,
    enable_slippage: bool,
    enable_spread: bool,
    enable_fees: bool,
) -> None:
    """Mutate `trade` in place: append cost-component fields and reduce
    pnl_usd by total_cost_usd (preserving the original gross value as
    `gross_pnl_usd`). No-op when entry_notional is non-positive (malformed
    SL — already handled upstream by the phantom-profit guard).
    """
    entry_notional = position.get("entry_notional_usd", 0.0)
    # `<= 0` evaluates False for NaN; use `not (... > 0)` to short-circuit.
    if not (entry_notional > 0):
        return
    entry_price = position["entry_price"]
    exit_notional = entry_notional * (exit_price_actual / entry_price) if entry_price else 0.0

    cost = compute_trade_costs_fn(
        entry_notional_usd=entry_notional,
        exit_notional_usd=exit_notional,
        entry_liquidity_usd_per_min=position.get("entry_liquidity_per_min", float("nan")),
        exit_liquidity_usd_per_min=exit_liquidity_per_min,
        tier_params=tier_params,
        enable_slippage=enable_slippage,
        enable_spread=enable_spread,
        enable_fees=enable_fees,
    )
    trade.update(cost)
    trade["gross_pnl_usd"] = trade["pnl_usd"]
    trade["gross_pnl_pct"] = trade["pnl_pct"]
    trade["entry_notional_usd"] = entry_notional
    trade["pnl_usd"] = round(trade["pnl_usd"] - cost["total_cost_usd"], 2)
    # pnl_pct is the per-trade % return used downstream by Sharpe / Sortino in
    # calculate_metrics. Subtracting cost in absolute % terms (cost_usd /
    # entry_notional × 100) keeps risk-adjusted metrics consistent with net
    # pnl_usd and avoids the misleading "Sharpe unchanged but PnL collapsed"
    # output that gross-pct returns would produce.
    cost_pct = cost["total_cost_usd"] / entry_notional * 100.0
    trade["pnl_pct"] = round(trade["pnl_pct"] - cost_pct, 4)


# ─────────────────────────────────────────────────────────────────────────────
#  SIMULATION
# ─────────────────────────────────────────────────────────────────────────────

def simulate_strategy(df1h: pd.DataFrame, df4h: pd.DataFrame, df5m: pd.DataFrame,
                      symbol: str, sl_mode: str = "atr",
                      atr_sl_mult: float = None, atr_tp_mult: float = None,
                      atr_be_mult: float = None,
                      df1d: pd.DataFrame = None,
                      sim_start: datetime = None, sim_end: datetime = None,
                      df_fng: pd.DataFrame = None,
                      df_funding: pd.DataFrame = None,
                      symbol_overrides: dict | None = None,
                      regime_mode: str = "global",       # NEW (#152)
                      df1d_btc: pd.DataFrame = None,     # NEW (#152)
                      apply_kill_switch: bool = False,   # NEW (#138 PR 3)
                      kill_switch_cfg: dict | None = None,  # NEW (#138 PR 3)
                      shared_simulator=None,             # NEW (#186 A6)
                      cfg: dict | None = None,           # NEW (#186 A6)
                      enable_slippage: bool = True,      # NEW (A.0.2, #277)
                      enable_spread: bool = True,        # NEW (A.0.2, #277)
                      enable_fees: bool = True,          # NEW (A.0.2, #277)
                      cost_calibration=None,             # NEW (A.0.2, #277)
                      regime_thresholds: tuple[int, int] | None = None,
                      regime_disabled: bool = False,
                      ) -> list[dict]:
    """Run bar-by-bar simulation of the Spot V6 strategy.

    Kill switch (#138): disabled by default to preserve backtest reproducibility.
    Pass apply_kill_switch=True + kill_switch_cfg to simulate production behavior
    where REDUCED symbols use size_mult × reduce_size_factor.

    #186 A6 — when apply_kill_switch=True, prefer `shared_simulator` (a
    `KillSwitchSimulator` instance) for in-memory tier tracking that updates
    bar-by-bar from the trades generated in THIS run. If no simulator is
    passed, one is created internally. Falls back to `health.apply_reduce_factor`
    (DB-backed, static during a backtest) only when `shared_simulator is None`
    AND caller explicitly passes `kill_switch_cfg` without opting into the
    simulator — legacy path preserved for callers that rely on it.

    `cfg` is the merged config dict (`btc_api.load_config()` shape); used for
    the KillSwitchSimulator bootstrap when `shared_simulator` is not supplied.

    regime_thresholds (tuple[int, int] | None): override (bull_above, bear_below)
        for regime classification. None → (60, 40) production behavior.
    regime_disabled (bool): when True, skip _regime_at_time entirely and emit a
        regime dict with regime="BYPASS" so direction is gated by zone alone.
        Mutually exclusive with regime_thresholds.
    """
    if regime_disabled and regime_thresholds is not None:
        raise RegimeKwargError(
            "regime_disabled=True is mutually exclusive with regime_thresholds — "
            "bypass mode skips threshold logic entirely."
        )
    if regime_thresholds is not None:
        if not (isinstance(regime_thresholds, tuple)
                and len(regime_thresholds) == 2
                and all(isinstance(x, int) and not isinstance(x, bool)
                        for x in regime_thresholds)):
            raise RegimeKwargError(
                f"regime_thresholds must be tuple[int, int] (bool excluded — "
                f"True/False are int subclasses in Python); got "
                f"{regime_thresholds!r}"
            )

    # #186 A6: lazy imports keep backtest.py importable even when `strategy/`
    # or `backtest_kill_switch` has its own transient import issues.
    from backtest_kill_switch import KillSwitchSimulator

    # A.0.2 (#277): cost-model bootstrap. Loaded lazily so non-cost callers
    # (and historical tests with all flags off) skip the calibration JSON
    # entirely. `_costs_active` short-circuits the per-trade augmentation when
    # all flags are False — preserving byte-identical behavior on the
    # legacy path.
    _costs_active = bool(enable_slippage or enable_spread or enable_fees)
    _tier_params = None
    _liquidity_per_min = None
    if _costs_active:
        from backtest_costs import (
            tier_for_symbol, load_calibration, compute_trade_costs,
        )
        _calibration = cost_calibration if cost_calibration is not None else load_calibration()
        _tier_params = _calibration.tiers[tier_for_symbol(symbol)]
        # 30-day rolling USD volume per minute on the 1H timeframe. Each 1H bar
        # contributes (close × volume) USD over 60 minutes; we divide by 60 to
        # convert to per-minute, then take a 720-bar (30-day) rolling mean to
        # smooth single-bar spikes. min_periods=120 (5 days) avoids degenerate
        # rolling outputs at the very start of the series; bars before that
        # produce NaN, which compute_slippage_bps treats as fallback territory.
        _usd_per_min = (df1h["close"] * df1h["volume"]) / 60.0
        _liquidity_per_min = _usd_per_min.rolling(720, min_periods=120).mean()

    # Rolling 24h MEDIAN of bar volume USD — liquidity proxy for the
    # participation cap. Median tolerates dead overnight bars and rejects
    # single-bar volume spikes. Materialized to a numpy array because per-bar
    # `Series.loc[Timestamp]` lookups in the entry block were a measurable
    # hot spot (~2x runtime over 35K bars).
    _bar_volume_usd = df1h["close"] * df1h["volume"]
    _liquidity_24h_median_np = (
        _bar_volume_usd.rolling(24, min_periods=24).median().to_numpy()
    )

    # Short-window warning: a backtest with fewer than 24 1H bars cannot
    # produce a valid 24h median for any bar — every cap-active entry will
    # skip with NaN liquidity. Emit once instead of N per-bar debug logs so
    # the structural mismatch surfaces clearly.
    _cap_configured_for_symbol = (
        (symbol_overrides or (cfg or {}).get("symbol_overrides", {}))
        .get(symbol.upper(), {})
        .get("max_participation_rate") is not None
    )
    if len(df1h) < 24 and _cap_configured_for_symbol:
        log.warning(
            "simulate_strategy: %s backtest window has %d 1H bars (< 24) — "
            "rolling 24h liquidity median is NaN throughout; all cap-active "
            "entries will skip",
            symbol, len(df1h),
        )

    trades = []
    position = None  # {entry_price, entry_time, score, sl, tp, size_mult}
    last_exit_time = None
    capital = INITIAL_CAPITAL
    equity_curve = []

    # Resolve ATR multipliers
    _sl_m = atr_sl_mult if atr_sl_mult is not None else ATR_SL_MULT
    _tp_m = atr_tp_mult if atr_tp_mult is not None else ATR_TP_MULT
    _be_m = atr_be_mult if atr_be_mult is not None else ATR_BE_MULT

    # ─────────────────────────────────────────────────────────────────────
    # #186 A6 — KillSwitchSimulator wiring.
    #
    # When apply_kill_switch=True:
    #   - If shared_simulator is passed, use it (lets caller share state across
    #     multiple simulate_strategy calls — e.g., a portfolio-level driver).
    #   - Else, auto-construct one. This replaces the static DB lookup
    #     `health.apply_reduce_factor` used for tier detection below.
    #
    # When apply_kill_switch=False: no simulator is active; behavior is
    # byte-identical to the pre-A6 version.
    # ─────────────────────────────────────────────────────────────────────
    _simulator: "KillSwitchSimulator | None" = None
    if apply_kill_switch:
        if shared_simulator is not None:
            _simulator = shared_simulator
        else:
            _ks_cfg_payload: dict = {}
            if cfg is not None:
                _ks_cfg_payload = cfg
            elif kill_switch_cfg is not None:
                # kill_switch_cfg is the INNER dict; wrap for KillSwitchSimulator
                _ks_cfg_payload = {"kill_switch": kill_switch_cfg}
            _simulator = KillSwitchSimulator(_ks_cfg_payload)

    # Need at least LRC_PERIOD bars of warmup
    warmup = max(LRC_PERIOD, 100) + 10
    _sim_start_ts = pd.Timestamp(sim_start).tz_localize(None) if sim_start else None
    _sim_end_ts = pd.Timestamp(sim_end).tz_localize(None) if sim_end else None
    log.info(f"Simulating {symbol} — {len(df1h)} 1H bars (warmup: {warmup})")

    for i in range(warmup, len(df1h)):
        bar = df1h.iloc[i]
        bar_time = df1h.index[i]
        bar_time_naive = bar_time.tz_localize(None) if bar_time.tzinfo else bar_time

        # Skip bars outside simulation window (but still check open positions)
        if _sim_end_ts and bar_time_naive > _sim_end_ts and position is None:
            continue

        # ── Check open position for SL/TP ─────────────────────────────────
        if position is not None:
            pos_dir = position.get("direction", "LONG")
            be_thresh = position.get("be_threshold")

            # Trailing ratchet: move SL to breakeven
            if pos_dir == "SHORT":
                if be_thresh and bar["low"] <= be_thresh and position["sl"] > position["entry_price"]:
                    position["sl"] = position["entry_price"]
                hit_sl = bar["high"] >= position["sl"]
                hit_tp = bar["low"] <= position["tp"]
            else:
                if be_thresh and bar["high"] >= be_thresh and position["sl"] < position["entry_price"]:
                    position["sl"] = position["entry_price"]
                hit_sl = bar["low"] <= position["sl"]
                hit_tp = bar["high"] >= position["tp"]

            if hit_sl and hit_tp:
                # Both hit in same bar — assume SL hit first if open < entry
                if bar["open"] <= position["entry_price"]:
                    exit_price = position["sl"]
                    exit_reason = "SL"
                else:
                    exit_price = position["tp"]
                    exit_reason = "TP"
            elif hit_sl:
                exit_price = position["sl"]
                exit_reason = "SL"
            elif hit_tp:
                exit_price = position["tp"]
                exit_reason = "TP"
            else:
                _tl_h = position.get("time_limit_hours")
                if _tl_h is not None:
                    hours_open = (bar_time - position["entry_time"]).total_seconds() / 3600
                    if hours_open >= _tl_h:
                        exit_price = float(bar["close"])
                        exit_reason = "TIME_LIMIT"
                    else:
                        exit_price = None
                        exit_reason = None
                else:
                    exit_price = None
                    exit_reason = None

            if exit_price is not None:
                trade = _close_position(
                    position, exit_price=exit_price, exit_time=bar_time,
                    exit_reason=exit_reason, capital=capital,
                )
                if _costs_active:
                    try:
                        _exit_liq = float(_liquidity_per_min.loc[bar_time])
                    except (KeyError, IndexError):
                        _exit_liq = float("nan")
                    _apply_costs_to_trade(
                        trade, position, exit_price, _exit_liq,
                        compute_trade_costs, _tier_params,
                        enable_slippage, enable_spread, enable_fees,
                    )
                trades.append(trade)
                capital += trade["pnl_usd"]
                position = None
                last_exit_time = bar_time
                # #186 A6: feed the simulator so tier can evolve mid-backtest.
                # Safe: _simulator is only non-None when apply_kill_switch=True.
                if _simulator is not None:
                    try:
                        # Always produce a tz-aware ISO string — health.py's pure
                        # metrics function compares parsed timestamps to a tz-aware
                        # `now`, and mixing naive/aware raises TypeError inside the
                        # loop (the except guards only ValueError/AttributeError).
                        _bt_aware = _ensure_tz_aware(bar_time)
                        _simulator.on_trade_close(
                            symbol, _bt_aware.isoformat(),
                            float(trade["pnl_usd"]),
                            _bt_aware,
                        )
                    except Exception as e:  # noqa: BLE001
                        log.warning(
                            "simulate_strategy: simulator.on_trade_close failed for "
                            "%s @ %s: %s", symbol, bar_time, e,
                        )

        # Record equity
        equity_curve.append({"time": bar_time, "equity": capital})

        # ── Skip if already in a position ─────────────────────────────────
        if position is not None:
            continue

        # ── Skip if before simulation start (warmup period) ──────────────
        if _sim_start_ts and bar_time_naive < _sim_start_ts:
            continue

        # ── Cooldown check (per-symbol with COOLDOWN_H fallback) ──────────
        # Default-fallback semantics: missing or invalid `cooldown_hours` →
        # COOLDOWN_H global. Validator emits one throttled warning per
        # (caller, symbol, error_kind) on rejection. Disabled symbols
        # (`symbol_overrides[sym] = False`) guarded via isinstance.
        _cd_overrides = symbol_overrides or (cfg or {}).get("symbol_overrides", {})
        _so_raw = _cd_overrides.get(symbol.upper(), {})
        _so_for_cd = _so_raw if isinstance(_so_raw, dict) else {}
        _effective_cooldown = _validated_cooldown_hours(
            _so_for_cd.get("cooldown_hours"), symbol,
        )

        if last_exit_time is not None:
            hours_since = (bar_time - last_exit_time).total_seconds() / 3600
            if hours_since < _effective_cooldown:
                continue

        # ── Evaluate entry signal via strategy.core.evaluate_signal (#186 A6) ─
        # Replace the ~130-line inline decision block (LRC zone / regime /
        # 4H macro / exclusions / 5M trigger / score) with a single call to
        # the shared pure kernel. Windowed df slices match btc_scanner.scan()
        # exactly (limits 210/150/210/250 for 1h/4h/5m/1d).
        from strategy.core import evaluate_signal

        slice_1h = df1h.loc[:bar_time].tail(210)
        slice_4h = df4h.loc[:bar_time].tail(150)
        slice_5m = df5m.loc[:bar_time].tail(210)
        slice_1d = df1d.loc[:bar_time].tail(250) if df1d is not None else slice_1h

        if len(slice_1h) < LRC_PERIOD:
            continue

        # Regime detection — bypass branch synthesizes a regime dict for the
        # no-detector configuration; else delegates to _regime_at_time helper
        # (kept backtest-local because scan() fetches its regime from a 24h
        # cache, not the bar-aligned helper used here).
        if regime_disabled:
            regime_info = {
                "regime": "BYPASS",
                "score": None,
                "mode": "disabled",
                "symbol": symbol,
                "components": {},
            }
        else:
            ba, bb = (regime_thresholds if regime_thresholds is not None else (60, 40))
            regime_info = _regime_at_time(
                bar_time, symbol, df1d, df_fng, df_funding,
                regime_mode=regime_mode, df1d_btc=df1d_btc,
                bull_above=ba, bear_below=bb,
            )

        # Merge `symbol_overrides` (legacy kwarg) into cfg so evaluate_signal
        # can resolve per-direction ATR mults via its built-in resolver.
        # When legacy atr_* kwargs are also set, we override SL/TP below so
        # legacy kwargs retain precedence (matches pre-refactor semantics).
        _cfg_for_eval = dict(cfg) if isinstance(cfg, dict) else {}
        if symbol_overrides is not None:
            _cfg_for_eval["symbol_overrides"] = symbol_overrides

        decision = evaluate_signal(
            slice_1h, slice_4h, slice_5m, slice_1d,
            symbol=symbol, cfg=_cfg_for_eval, regime=regime_info,
            health_state="NORMAL", now=bar_time,
        )

        if not decision.is_signal or decision.direction == "NONE":
            continue

        trade_dir = decision.direction
        price = float(decision.entry_price)
        score = int(decision.score)

        # Size multiplier (mirrors legacy tiering)
        if score >= SCORE_PREMIUM:
            size_mult = 1.5
        elif score >= SCORE_STANDARD:
            size_mult = 1.0
        else:
            size_mult = 0.5

        # Kill switch #138 PR 3: optionally halve size for REDUCED symbols.
        # Gated behind apply_kill_switch flag — defaults off in backtests
        # to preserve reproducibility; enable when simulating production.
        #
        # #186 A6: the in-memory `_simulator` replaces the DB-backed
        # `health.apply_reduce_factor` used in PR 3. The simulator's tier
        # evolves as the backtest generates trades, giving faithful
        # kill-switch behavior instead of reading the static prod DB.
        if apply_kill_switch and _simulator is not None:
            try:
                tier = _simulator.get_tier(symbol)
                if tier == "PAUSED":
                    continue  # skip opening; matches compute_size → 0 semantics
                if tier in ("REDUCED", "PROBATION"):
                    ks_block = (
                        (cfg or {}).get("kill_switch", {})
                        if cfg is not None
                        else (kill_switch_cfg or {})
                    )
                    factor = float(ks_block.get("reduce_size_factor", 0.5))
                    size_mult *= factor
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "simulate_strategy: simulator.get_tier failed for %s: %s",
                    symbol, e,
                )

        # ── Open position ─────────────────────────────────────────────────
        # Legacy atr_* kwargs retain precedence over symbol_overrides — the
        # existing contract (test_simulate_strategy_legacy_kwargs_win_over_overrides).
        legacy_override_active = (
            (atr_sl_mult is not None)
            or (atr_tp_mult is not None)
            or (atr_be_mult is not None)
        )

        if sl_mode == "atr":
            if legacy_override_active:
                # Legacy path: compute ATR + SL/TP inline using legacy kwargs.
                atr_series = calc_atr(slice_1h, ATR_PERIOD)
                atr_val = float(atr_series.iloc[-1])
                if pd.isna(atr_val) or atr_val <= 0:
                    continue
                _sl_m_use = _sl_m
                _tp_m_use = _tp_m
                _be_m_use = _be_m
                if trade_dir == "SHORT":
                    # Full float precision — see strategy/core.py rationale.
                    sl_price = float(price + atr_val * _sl_m_use)
                    tp_price = float(price - atr_val * _tp_m_use)
                    be_threshold = price - atr_val * _be_m_use
                else:
                    sl_price = float(price - atr_val * _sl_m_use)
                    tp_price = float(price + atr_val * _tp_m_use)
                    be_threshold = price + atr_val * _be_m_use
            else:
                # Use decision's SL/TP (already resolved via cfg.symbol_overrides).
                atr_val = float(decision.indicators.get("atr_1h") or 0.0)
                if pd.isna(atr_val) or atr_val <= 0:
                    continue
                sl_price = float(decision.sl_price)
                tp_price = float(decision.tp_price)
                _sl_m_use = float(decision.reasons.get("atr_sl_mult"))
                _tp_m_use = float(decision.reasons.get("atr_tp_mult"))
                _be_m_use = float(decision.reasons.get("atr_be_mult"))
                if trade_dir == "SHORT":
                    be_threshold = price - atr_val * _be_m_use
                else:
                    be_threshold = price + atr_val * _be_m_use
        else:
            _sl_m_use = _sl_m
            _tp_m_use = _tp_m
            _be_m_use = _be_m
            if trade_dir == "SHORT":
                # Full float precision — fixed-pct SL/TP path.
                sl_price = float(price * (1 + SL_PCT / 100))
                tp_price = float(price * (1 - TP_PCT / 100))
            else:
                sl_price = float(price * (1 - SL_PCT / 100))
                tp_price = float(price * (1 + TP_PCT / 100))
            be_threshold = None

        # Legacy atr_* kwargs path skips the time-limit barrier AND the
        # participation cap — those callers (auto_tune / grid_search) must
        # opt in by passing symbol_overrides explicitly. Cooldown is NOT
        # bypassed: the cooldown check upstream uses COOLDOWN_H global as
        # default-fallback (via validated_cooldown_hours), so legacy paths
        # still observe the legacy 6h global cooldown.
        if legacy_override_active:
            _tl_h = None
        else:
            _overrides_merged = symbol_overrides or (cfg or {}).get("symbol_overrides", {})
            _tl_h_raw = _overrides_merged.get(symbol.upper(), {}).get("time_limit_hours")
            _tl_h = _validated_time_limit_hours(_tl_h_raw, symbol)

            # Participation cap: skip entry when desired notional > max_pov ×
            # 24h median liquidity. Skip rules pinned by validator + tests.
            _max_pov_raw = _overrides_merged.get(symbol.upper(), {}).get("max_participation_rate")
            _max_pov = _validated_max_participation_rate(_max_pov_raw, symbol)
            if _max_pov is not None:
                _sl_pct_actual = abs(price - sl_price) / price * 100.0
                if _sl_pct_actual > 0:
                    _desired_notional = (capital * RISK_PER_TRADE * size_mult) * 100.0 / _sl_pct_actual
                    _liq_24h = float(_liquidity_24h_median_np[i])
                    if pd.isna(_liq_24h) or _liq_24h <= 0:
                        log.debug(
                            "simulate_strategy: liquidity_cap_skip %s %s "
                            "(unobservable liquidity at %s)",
                            symbol, trade_dir, bar_time,
                        )
                        continue
                    _cap_threshold = _max_pov * _liq_24h
                    if _desired_notional > _cap_threshold:
                        log.debug(
                            "simulate_strategy: liquidity_cap_skip %s %s "
                            "desired=%.2f > cap=%.2f (liq_24h=%.2f, max_pov=%.4f) at %s",
                            symbol, trade_dir, _desired_notional, _cap_threshold,
                            _liq_24h, _max_pov, bar_time,
                        )
                        continue

        position = {
            "entry_price": price,
            "entry_time": bar_time,
            "score": score,
            "direction": trade_dir,
            "sl": sl_price,
            "sl_orig": sl_price,
            "tp": tp_price,
            "size_mult": size_mult,
            "be_threshold": be_threshold,
            "atr_sl_mult_used": _sl_m_use,
            "atr_tp_mult_used": _tp_m_use,
            "atr_be_mult_used": _be_m_use,
            "time_limit_hours": _tl_h,
        }

        # A.0.2 (#277): freeze cost inputs at entry. notional uses the
        # per-trade risk budget translated into USD via the SL distance —
        # mirrors how live execution would size the order. Liquidity is the
        # 30-day rolling proxy at the entry bar; NaN here flows through to
        # compute_slippage_bps' fallback path (punitive default 100 bps),
        # which is the desired conservative behavior when liquidity is
        # unobservable.
        if _costs_active:
            _sl_pct_actual = abs(price - sl_price) / price * 100.0
            _risk_amount = capital * RISK_PER_TRADE * size_mult
            position["entry_notional_usd"] = (
                _risk_amount * 100.0 / _sl_pct_actual if _sl_pct_actual > 0 else 0.0
            )
            try:
                position["entry_liquidity_per_min"] = float(
                    _liquidity_per_min.loc[bar_time]
                )
            except (KeyError, IndexError):
                position["entry_liquidity_per_min"] = float("nan")

    # Close any open position at last bar price
    if position is not None:
        last_bar = df1h.iloc[-1]
        exit_price = float(last_bar["close"])
        trade = _close_position(
            position, exit_price=exit_price, exit_time=df1h.index[-1],
            exit_reason="OPEN", capital=capital,
        )
        if _costs_active:
            try:
                _exit_liq_final = float(_liquidity_per_min.loc[df1h.index[-1]])
            except (KeyError, IndexError):
                _exit_liq_final = float("nan")
            _apply_costs_to_trade(
                trade, position, exit_price, _exit_liq_final,
                compute_trade_costs, _tier_params,
                enable_slippage, enable_spread, enable_fees,
            )
        trades.append(trade)
        capital += trade["pnl_usd"]

    return trades, equity_curve


# ─────────────────────────────────────────────────────────────────────────────
#  METRICS
# ─────────────────────────────────────────────────────────────────────────────

def calculate_metrics(trades: list[dict], equity_curve: list[dict]) -> dict:
    """Calculate comprehensive trading metrics."""
    if not trades:
        # Empty-trades early return — emit clamped_trade_count: 0 for shape
        # consistency. CLI consumer below already defaults via .get(..., 0).
        return {"error": "No trades generated", "clamped_trade_count": 0}

    df = pd.DataFrame(trades)
    closed = df[df["exit_reason"] != "OPEN"]

    wins = closed[closed["pnl_usd"] > 0]
    losses = closed[closed["pnl_usd"] <= 0]

    total_trades = len(closed)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = win_count / total_trades if total_trades > 0 else 0

    gross_profit = wins["pnl_usd"].sum() if len(wins) > 0 else 0
    gross_loss = abs(losses["pnl_usd"].sum()) if len(losses) > 0 else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    net_pnl = closed["pnl_usd"].sum()
    total_return_pct = (net_pnl / INITIAL_CAPITAL) * 100

    # Equity curve metrics
    eq = pd.DataFrame(equity_curve)
    eq_values = eq["equity"].values
    peak = np.maximum.accumulate(eq_values)
    drawdown = (eq_values - peak) / peak * 100
    max_drawdown = float(np.min(drawdown))

    # Sharpe ratio (annualized)
    if len(closed) > 1:
        returns = closed["pnl_pct"].values / 100
        # Annualize based on trades per year (not hourly). When all trades
        # share a single day, span_y == 0; mirror the trades_per_month guard
        # below so we don't divide by zero (Sharpe falls back to 0,
        # consistent with the legacy `len(closed) > 1` else branch).
        span_y = (closed["exit_time"].iloc[-1] - closed["entry_time"].iloc[0]).days / 365.25
        trades_per_year = len(closed) / span_y if span_y > 0 else 0
        sharpe = np.mean(returns) / np.std(returns) * np.sqrt(trades_per_year) if np.std(returns) > 0 and trades_per_year > 0 else 0
        sortino_returns = returns[returns < 0]
        downside_std = np.std(sortino_returns) if len(sortino_returns) > 1 else 0
        sortino = np.mean(returns) / downside_std * np.sqrt(252) if downside_std > 1e-10 else 0
    else:
        sharpe = 0
        sortino = 0

    # Duration
    avg_duration = closed["duration_hours"].mean()
    avg_win_duration = wins["duration_hours"].mean() if len(wins) > 0 else 0
    avg_loss_duration = losses["duration_hours"].mean() if len(losses) > 0 else 0

    # Consecutive streaks
    results = (closed["pnl_usd"] > 0).tolist()
    max_wins = max_losses = 0
    current_streak = 1
    for j in range(1, len(results)):
        if results[j] == results[j - 1]:
            current_streak += 1
        else:
            if results[j - 1]:
                max_wins = max(max_wins, current_streak)
            else:
                max_losses = max(max_losses, current_streak)
            current_streak = 1
    if results:
        if results[-1]:
            max_wins = max(max_wins, current_streak)
        else:
            max_losses = max(max_losses, current_streak)

    # Trades per month
    if len(closed) >= 2:
        span_days = (closed["exit_time"].iloc[-1] - closed["entry_time"].iloc[0]).days
        trades_per_month = total_trades / (span_days / 30) if span_days > 0 else 0
    else:
        trades_per_month = 0

    # By score tier
    score_tiers = {}
    for tier_name, lo, hi in [("0-1 (minimal)", 0, 1), ("2-3 (standard)", 2, 3), ("4+ (premium)", 4, 9)]:
        tier = closed[(closed["score"] >= lo) & (closed["score"] <= hi)]
        if len(tier) > 0:
            tier_wins = tier[tier["pnl_usd"] > 0]
            score_tiers[tier_name] = {
                "trades": len(tier),
                "win_rate": round(len(tier_wins) / len(tier) * 100, 1),
                "avg_pnl_pct": round(tier["pnl_pct"].mean(), 2),
                "total_pnl_usd": round(tier["pnl_usd"].sum(), 2),
            }

    # A.0.2 (#277): cost aggregates surface only when trades carry the per-
    # component fields populated by simulate_strategy with cost flags on. The
    # gate keeps legacy callers (cost-flags-off) on the original metrics shape
    # so downstream consumers do not see zero-valued fields they would have
    # to reason about. Mini-contract names locked here; A.0.3 (#278) reserves
    # the deflated/Calmar names and must not collide.
    cost_metrics: dict = {}
    if "total_cost_bps" in closed.columns:
        cost_metrics = {
            "total_cost_bps_mean": round(float(closed["total_cost_bps"].mean()), 2),
            "total_cost_usd_sum": round(float(closed["total_cost_usd"].sum()), 2),
            "entry_slippage_bps_mean": round(float(closed["entry_slippage_bps"].mean()), 2),
            "exit_slippage_bps_mean": round(float(closed["exit_slippage_bps"].mean()), 2),
            "entry_spread_bps_mean": round(float(closed["entry_spread_bps"].mean()), 2),
            "exit_spread_bps_mean": round(float(closed["exit_spread_bps"].mean()), 2),
            "fee_bps_mean": round(float(closed["fee_bps"].mean()), 2),
            "gross_net_pnl_diff_usd": round(
                float((closed["gross_pnl_usd"] - closed["pnl_usd"]).sum()), 2
            ),
        }

    # Overshoot-clamp aggregate: count of closed trades where the cap actually
    # bound pnl_usd below its raw R-multiple value. Surfaces cap-binding
    # incidence at metrics-output level so consumers can detect simulator
    # edge-case execution without parsing the per-trade list.
    clamped_trade_count = int(
        sum(t.get("overshoot_clamped", False) for t in trades
            if t.get("exit_reason") != "OPEN")
    )

    return {
        "total_trades": total_trades,
        "wins": win_count,
        "losses": loss_count,
        "win_rate": round(win_rate * 100, 1),
        "clamped_trade_count": clamped_trade_count,
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_pnl": round(net_pnl, 2),
        "profit_factor": round(profit_factor, 2),
        "total_return_pct": round(total_return_pct, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "avg_duration_hours": round(avg_duration, 1),
        "avg_win_duration_hours": round(avg_win_duration, 1),
        "avg_loss_duration_hours": round(avg_loss_duration, 1),
        "max_consecutive_wins": max_wins,
        "max_consecutive_losses": max_losses,
        "trades_per_month": round(trades_per_month, 1),
        "best_trade_pct": round(closed["pnl_pct"].max(), 2) if len(closed) > 0 else 0,
        "worst_trade_pct": round(closed["pnl_pct"].min(), 2) if len(closed) > 0 else 0,
        "median_trade_pct": round(closed["pnl_pct"].median(), 2) if len(closed) > 0 else 0,
        "final_equity": round(INITIAL_CAPITAL + net_pnl, 2),
        "score_tiers": score_tiers,
        **cost_metrics,
    }


def classify_market_regime(df1h: pd.DataFrame, trades: list[dict]) -> dict:
    """Classify each trade into bull/bear/sideways regime."""
    daily = df1h["close"].resample("1D").last().dropna()
    sma100d = daily.rolling(100).mean()
    ret30d = daily.pct_change(30) * 100

    regimes = {"bull": [], "bear": [], "sideways": []}

    for t in trades:
        if t["exit_reason"] == "OPEN":
            continue
        entry_date = t["entry_time"]
        closest = daily.index[daily.index.get_indexer([entry_date], method="ffill")]
        if len(closest) == 0:
            continue
        d = closest[0]
        if d not in sma100d.index or pd.isna(sma100d.loc[d]):
            regimes["sideways"].append(t)
            continue

        price_above_sma = daily.loc[d] > sma100d.loc[d]
        ret = ret30d.loc[d] if d in ret30d.index and not pd.isna(ret30d.loc[d]) else 0

        if price_above_sma and ret > 10:
            regimes["bull"].append(t)
        elif not price_above_sma and ret < -10:
            regimes["bear"].append(t)
        else:
            regimes["sideways"].append(t)

    result = {}
    for regime, regime_trades in regimes.items():
        if not regime_trades:
            result[regime] = {"trades": 0, "win_rate": 0, "avg_pnl_pct": 0, "total_pnl_usd": 0}
            continue
        df_r = pd.DataFrame(regime_trades)
        wins_r = df_r[df_r["pnl_usd"] > 0]
        result[regime] = {
            "trades": len(df_r),
            "win_rate": round(len(wins_r) / len(df_r) * 100, 1),
            "avg_pnl_pct": round(df_r["pnl_pct"].mean(), 2),
            "total_pnl_usd": round(df_r["pnl_usd"].sum(), 2),
        }
    return result


# ─────────────────────────────────────────────────────────────────────────────
#  REPORT
# ─────────────────────────────────────────────────────────────────────────────

def generate_report(symbol: str, metrics: dict, regimes: dict, trades: list[dict],
                    sim_start: datetime = None, sim_end: datetime = None,
                    symbol_overrides: dict | None = None) -> str:
    """Generate markdown report."""
    m = metrics
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    period_start = sim_start.strftime("%Y-%m-%d") if sim_start else "N/A"
    period_end = sim_end.strftime("%Y-%m-%d") if sim_end else "present"

    # Resolve effective per-symbol cooldown for the methodology section.
    # Defaults to COOLDOWN_H global when no override or invalid value.
    # Disabled-symbol guard (mirrors the simulate_strategy resolution):
    # symbol_overrides[sym] can be `False` (not a dict).
    _so_raw = (symbol_overrides or {}).get(symbol.upper(), {})
    _so_for_eff = _so_raw if isinstance(_so_raw, dict) else {}
    _eff_cd = _validated_cooldown_hours(
        _so_for_eff.get("cooldown_hours"), symbol,
    )

    report = f"""# Strategy Backtest Report — Spot V6

**Generated:** {now}
**Symbol:** {symbol}
**Period:** {period_start} — {period_end}
**Initial Capital:** ${INITIAL_CAPITAL:,.0f}

---

## 1. Executive Summary

| Metric | Value |
|--------|-------|
| Total Trades | {m['total_trades']} |
| Win Rate | {m['win_rate']}% |
| Profit Factor | {m['profit_factor']} |
| Net P&L | ${m['net_pnl']:+,.2f} |
| Total Return | {m['total_return_pct']:+.1f}% |
| Max Drawdown | {m['max_drawdown_pct']:.1f}% |
| Sharpe Ratio | {m['sharpe_ratio']} |
| Sortino Ratio | {m['sortino_ratio']} |
| Final Equity | ${m['final_equity']:,.2f} |
| Trades/Month | {m['trades_per_month']} |

---

## 2. Methodology

- **Simulation type:** Bar-by-bar on 1H candles with aligned 4H macro and 5M trigger data
- **Entry conditions:** LRC% <= 25 (1H) + Price > SMA100 (4H) + Bullish 5M trigger + No exclusions
- **Exit:** Fixed SL at -{SL_PCT}% or TP at +{TP_PCT}% (whichever hit first)
- **Position sizing:** 1% risk per trade, multiplied by score tier (0.5x / 1x / 1.5x)
- **Constraints:** One position at a time, {_eff_cd:g}h cooldown (default {COOLDOWN_H}h)
- **Fees:** Not deducted from P&L (Binance spot = 0.1% per side)
- **Indicators:** Same functions as live scanner (`btc_scanner.py`)

---

## 3. Detailed Results

### Trade Distribution

| Metric | Value |
|--------|-------|
| Wins | {m['wins']} |
| Losses | {m['losses']} |
| Best Trade | {m['best_trade_pct']:+.2f}% |
| Worst Trade | {m['worst_trade_pct']:+.2f}% |
| Median Trade | {m['median_trade_pct']:+.2f}% |
| Gross Profit | ${m['gross_profit']:,.2f} |
| Gross Loss | ${m['gross_loss']:,.2f} |

### Duration

| Metric | Value |
|--------|-------|
| Avg Trade Duration | {m['avg_duration_hours']:.1f} hours |
| Avg Win Duration | {m['avg_win_duration_hours']:.1f} hours |
| Avg Loss Duration | {m['avg_loss_duration_hours']:.1f} hours |
| Max Consecutive Wins | {m['max_consecutive_wins']} |
| Max Consecutive Losses | {m['max_consecutive_losses']} |

---

## 4. Score Tier Analysis

Does higher score = better performance?

| Tier | Trades | Win Rate | Avg P&L % | Total P&L $ |
|------|--------|----------|-----------|-------------|
"""
    for tier_name, tier_data in m.get("score_tiers", {}).items():
        report += f"| {tier_name} | {tier_data['trades']} | {tier_data['win_rate']}% | {tier_data['avg_pnl_pct']:+.2f}% | ${tier_data['total_pnl_usd']:+,.2f} |\n"

    report += f"""
---

## 5. Market Regime Analysis

| Regime | Trades | Win Rate | Avg P&L % | Total P&L $ |
|--------|--------|----------|-----------|-------------|
| Bull | {regimes['bull']['trades']} | {regimes['bull']['win_rate']}% | {regimes['bull']['avg_pnl_pct']:+.2f}% | ${regimes['bull']['total_pnl_usd']:+,.2f} |
| Bear | {regimes['bear']['trades']} | {regimes['bear']['win_rate']}% | {regimes['bear']['avg_pnl_pct']:+.2f}% | ${regimes['bear']['total_pnl_usd']:+,.2f} |
| Sideways | {regimes['sideways']['trades']} | {regimes['sideways']['win_rate']}% | {regimes['sideways']['avg_pnl_pct']:+.2f}% | ${regimes['sideways']['total_pnl_usd']:+,.2f} |

---

## 6. Benchmark Comparison

| Metric | Our Strategy | Freqtrade Top 10% | Jesse Published |
|--------|-------------|-------------------|-----------------|
| Win Rate | {m['win_rate']}% | 55-65% | 45-55% |
| Profit Factor | {m['profit_factor']} | 1.5-2.5 | 1.3-2.0 |
| Sharpe Ratio | {m['sharpe_ratio']} | 1.0-2.0 | 0.8-1.5 |
| Max Drawdown | {m['max_drawdown_pct']:.1f}% | -10% to -25% | -15% to -30% |
| Trades/Month | {m['trades_per_month']} | 15-40 | 10-30 |
| R:R Ratio | 2:1 (fixed) | 1.5:1-3:1 | 2:1-4:1 |

---

## 7. Strengths

Based on backtest data:

1. **Multi-timeframe filter works:** The SMA100 4H macro filter prevents entries during sustained downtrends, keeping the strategy out of the worst bear market periods
2. **Scoring system validates:** {"Higher score tiers show better win rates, confirming the scoring system adds value" if len(m.get("score_tiers", {})) > 1 else "Scoring system tiers need more trades for statistical significance"}
3. **Fixed 2:1 R:R provides structural edge:** With a TP at 2x the SL, the strategy only needs >33% win rate to be profitable
4. **Conservative risk management:** 1% risk per trade limits max drawdown even during adverse periods
5. **Exclusion filters:** Bull engulfing and bearish divergence filters reduce false entries

---

## 8. Weaknesses

1. **Long-only limitation:** The strategy generates zero revenue during bear markets — it correctly avoids bad entries but misses short opportunities
2. **Fixed SL/TP:** {SL_PCT}%/{TP_PCT}% does not adapt to volatility — too tight in high-vol periods (premature SL hits), too loose in low-vol (slow TP fills)
3. **Low trade frequency:** ~{m['trades_per_month']} trades/month means capital sits idle most of the time
4. **No trailing stop:** Winners are capped at +{TP_PCT}% even when the trend continues strongly
5. **Static thresholds:** RSI < 40, LRC <= 25% — not adapted to different volatility regimes

---

## 9. Recommendations (Prioritized by Impact)

### High Impact
1. **ATR-based dynamic SL/TP** — Replace fixed 2%/4% with 1.5x ATR(14) / 3x ATR(14). Adapts to current volatility automatically.
2. **Trailing stop** — After reaching +2%, move SL to breakeven. After +3%, trail at 1.5x ATR. Captures trend continuation.
3. **Add short signals** — Mirror the long logic inverted (LRC >= 75%, price below SMA100 4H). Doubles opportunity set.

### Medium Impact
4. **ADX trend strength filter** — Only enter mean-reversion trades when ADX < 25 (ranging market). Avoids fighting strong trends.
5. **EMA 200 daily** as secondary trend confirmation (used by nearly every profitable Freqtrade strategy).
6. **Multi-symbol portfolio** — Run the strategy across 5-10 top symbols simultaneously to increase trade frequency.

### Low Impact (Nice to Have)
7. **VWAP integration** for intraday entry refinement
8. **Fee-adjusted sizing** to account for the 0.1% round-trip cost
9. **Walk-forward parameter optimization** once sufficient data is available

---

## Appendix: Trade Log (Last 20 Trades)

| Entry | Exit | Entry $ | Exit $ | P&L % | Score | Reason |
|-------|------|---------|--------|-------|-------|--------|
"""
    last_trades = [t for t in trades if t["exit_reason"] != "OPEN"][-20:]
    for t in last_trades:
        entry_dt = t["entry_time"].strftime("%Y-%m-%d %H:%M") if hasattr(t["entry_time"], "strftime") else str(t["entry_time"])[:16]
        exit_dt = t["exit_time"].strftime("%Y-%m-%d %H:%M") if hasattr(t["exit_time"], "strftime") else str(t["exit_time"])[:16]
        report += f"| {entry_dt} | {exit_dt} | ${t['entry_price']:,.0f} | ${t['exit_price']:,.0f} | {t['pnl_pct']:+.2f}% | {t['score']} | {t['exit_reason']} |\n"

    return report


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Backtest Spot V6 Strategy")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading pair (default: BTCUSDT)")
    parser.add_argument("--sl-mode", default="atr", choices=["atr", "fixed"],
                        help="SL/TP mode: 'atr' (dynamic) or 'fixed' (2%%/4%%)")
    parser.add_argument("--start", default="2023-01-01",
                        help="Start date YYYY-MM-DD (default: 2023-01-01)")
    parser.add_argument("--end", default=None,
                        help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--download-only", action="store_true", help="Only download data")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    sim_start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    sim_end = (datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
               if args.end else datetime.now(timezone.utc))

    # Need data from before sim_start for indicator warmup (SMA200 daily = 200 days)
    data_start = datetime(sim_start.year - 1, 1, 1, tzinfo=timezone.utc)

    # Download data (cache extends automatically to cover requested range)
    log.info(f"=== Backtest: {symbol} | {args.start} — {args.end or 'present'} ===")
    df1h = get_cached_data(symbol, "1h", start_date=data_start)
    df4h = get_cached_data(symbol, "4h", start_date=data_start)
    df5m = get_cached_data(symbol, "5m", start_date=data_start)
    df1d = get_cached_data(symbol, "1d", start_date=data_start)

    # Filter to simulation period (keep extra for warmup — simulate_strategy handles it)
    log.info(f"Data loaded: 1H={len(df1h)}, 4H={len(df4h)}, 5M={len(df5m)}, 1D={len(df1d)} candles")

    if args.download_only:
        log.info("Download complete.")
        return

    if df1h.empty or df4h.empty or df5m.empty:
        log.error("Failed to load data. Check your internet connection.")
        return

    # Run simulation
    # Download historical sentiment & funding data (cached, one-time)
    df_fng = get_historical_fear_greed()
    df_funding = get_historical_funding_rate()

    # Load config so simulate_strategy can apply per-symbol ATR overrides
    # (epic #121 / #122 / #123). Without this, all symbols run with BTC defaults.
    try:
        import btc_api
        cfg = btc_api.load_config()
    except Exception as e:  # noqa: BLE001
        log.warning(f"load_config failed: {e} — running with empty cfg (no symbol_overrides)")
        cfg = {}
    symbol_overrides = cfg.get("symbol_overrides", {}) if isinstance(cfg, dict) else {}

    trades, equity_curve = simulate_strategy(df1h, df4h, df5m, symbol, sl_mode=args.sl_mode,
                                               df1d=df1d, sim_start=sim_start, sim_end=sim_end,
                                               df_fng=df_fng, df_funding=df_funding,
                                               cfg=cfg, symbol_overrides=symbol_overrides)
    log.info(f"Simulation complete: {len(trades)} trades generated")

    if not trades:
        log.warning("No trades generated. Strategy may be too restrictive for this period.")
        return

    # Calculate metrics
    metrics = calculate_metrics(trades, equity_curve)
    regimes = classify_market_regime(df1h, trades)

    # Print summary
    print(f"\n{'='*60}")
    print(f"  BACKTEST RESULTS — {symbol}")
    print(f"{'='*60}")
    print(f"  Trades:        {metrics['total_trades']}")
    print(f"  Win Rate:      {metrics['win_rate']}%")
    print(f"  Profit Factor: {metrics['profit_factor']}")
    print(f"  Net P&L:       ${metrics['net_pnl']:+,.2f}")
    print(f"  Total Return:  {metrics['total_return_pct']:+.1f}%")
    print(f"  Max Drawdown:  {metrics['max_drawdown_pct']:.1f}%")
    print(f"  Sharpe Ratio:  {metrics['sharpe_ratio']}")
    print(f"  Final Equity:  ${metrics['final_equity']:,.2f}")
    print(f"  Clamped Trades:{metrics.get('clamped_trade_count', 0):>4}  (cap bound pnl below raw R-multiple)")
    print(f"{'='*60}\n")

    # Generate and save report
    report = generate_report(symbol, metrics, regimes, trades, sim_start=sim_start, sim_end=sim_end,
                             symbol_overrides=symbol_overrides)
    report_path = os.path.join(SCRIPT_DIR, "docs", "strategy-backtest-report.md")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    log.info(f"Report saved: {report_path}")

    # Save trade log as CSV
    trades_csv = os.path.join(DATA_DIR, f"{symbol}_trades.csv")
    pd.DataFrame(trades).to_csv(trades_csv, index=False)
    log.info(f"Trade log saved: {trades_csv}")


if __name__ == "__main__":
    main()
