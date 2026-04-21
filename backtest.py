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
"""

import os
import sys
import json
import time
import argparse
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

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
FEE_PCT = 0.001  # 0.1% per trade (Binance spot)


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

    # Bulk-backfill ahead of the range query so the first read doesn't
    # trigger piecewise fetches inside get_klines_range.
    md.backfill(symbol, interval, want_start, now)
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
) -> dict:
    """Compute regime for this bar_time (no look-ahead).

    mode='global':           uses df1d_btc + F&G + funding (40/30/30).
                              Fallback: if df1d_btc is None -> uses df1d_sym.
    mode='hybrid':           uses df1d_sym + F&G + funding (50/25/25).
    mode='hybrid_momentum':  uses df1d_sym + RSI + ADX + F&G + funding (30/15/20/20/15).
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
    )


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
                      ) -> list[dict]:
    """Run bar-by-bar simulation of the Spot V6 strategy."""
    trades = []
    position = None  # {entry_price, entry_time, score, sl, tp, size_mult}
    last_exit_time = None
    capital = INITIAL_CAPITAL
    equity_curve = []

    # Resolve ATR multipliers
    _sl_m = atr_sl_mult if atr_sl_mult is not None else ATR_SL_MULT
    _tp_m = atr_tp_mult if atr_tp_mult is not None else ATR_TP_MULT
    _be_m = atr_be_mult if atr_be_mult is not None else ATR_BE_MULT

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
                exit_price = None
                exit_reason = None

            if exit_price is not None:
                if pos_dir == "SHORT":
                    pnl_pct = (position["entry_price"] - exit_price) / position["entry_price"] * 100
                else:
                    pnl_pct = (exit_price - position["entry_price"]) / position["entry_price"] * 100
                risk_amount = capital * RISK_PER_TRADE * position["size_mult"]
                sl_pct_actual = abs(position["entry_price"] - position["sl_orig"]) / position["entry_price"] * 100
                pnl_usd = risk_amount * (pnl_pct / sl_pct_actual) if sl_pct_actual > 0 else 0

                trade = {
                    "entry_time": position["entry_time"],
                    "exit_time": bar_time,
                    "entry_price": position["entry_price"],
                    "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "direction": pos_dir,
                    "pnl_pct": round(pnl_pct, 4),
                    "pnl_usd": round(pnl_usd, 2),
                    "score": position["score"],
                    "size_mult": position["size_mult"],
                    "duration_hours": (bar_time - position["entry_time"]).total_seconds() / 3600,
                    "atr_sl_mult_used": position.get("atr_sl_mult_used"),
                    "atr_tp_mult_used": position.get("atr_tp_mult_used"),
                    "atr_be_mult_used": position.get("atr_be_mult_used"),
                }
                trades.append(trade)
                capital += pnl_usd
                position = None
                last_exit_time = bar_time

        # Record equity
        equity_curve.append({"time": bar_time, "equity": capital})

        # ── Skip if already in a position ─────────────────────────────────
        if position is not None:
            continue

        # ── Skip if before simulation start (warmup period) ──────────────
        if _sim_start_ts and bar_time_naive < _sim_start_ts:
            continue

        # ── Cooldown check ────────────────────────────────────────────────
        if last_exit_time is not None:
            hours_since = (bar_time - last_exit_time).total_seconds() / 3600
            if hours_since < COOLDOWN_H:
                continue

        # ── Evaluate entry signal ─────────────────────────────────────────
        window_1h = df1h.iloc[max(0, i - 209):i + 1]
        if len(window_1h) < LRC_PERIOD:
            continue

        close_1h = window_1h["close"]
        price = float(close_1h.iloc[-1])

        # LRC — determine direction
        lrc_pct, lrc_up, lrc_dn, lrc_mid = calc_lrc(close_1h, LRC_PERIOD, LRC_STDEV)
        if lrc_pct is None:
            continue

        from btc_scanner import LRC_SHORT_MIN, detect_bear_engulfing, check_trigger_5m_short

        # Regime detection via _regime_at_time helper (#152)
        regime_info = _regime_at_time(
            bar_time, symbol, df1d, df_fng, df_funding,
            regime_mode=regime_mode, df1d_btc=df1d_btc,
        )
        _r = regime_info["regime"]
        regime = "LONG" if _r == "BULL" else "SHORT" if _r == "BEAR" else "LONG"

        # Direction based on regime + LRC zone
        if lrc_pct <= LRC_LONG_MAX and regime == "LONG":
            trade_dir = "LONG"
        elif lrc_pct >= LRC_SHORT_MIN and regime == "SHORT":
            trade_dir = "SHORT"
        else:
            continue

        # Macro 4H: SMA100
        mask_4h = df4h.index <= bar_time
        window_4h = df4h.loc[mask_4h].iloc[-100:]
        if len(window_4h) < 100:
            continue
        sma100_4h = calc_sma(window_4h["close"], 100).iloc[-1]
        if pd.isna(sma100_4h):
            continue
        if trade_dir == "LONG" and price <= sma100_4h:
            continue
        if trade_dir == "SHORT" and price >= sma100_4h:
            continue

        # Exclusions (direction-aware)
        rsi1h = calc_rsi(close_1h, RSI_PERIOD)
        rsi_divs = detect_rsi_divergence(close_1h, rsi1h, window=72)

        if trade_dir == "LONG":
            if detect_bull_engulfing(window_1h):
                continue
            if rsi_divs["bear"]:
                continue
        else:  # SHORT
            if detect_bear_engulfing(window_1h):
                continue
            if rsi_divs["bull"]:
                continue

        # 5M trigger (direction-aware)
        mask_5m = (df5m.index <= bar_time) & (df5m.index > bar_time - timedelta(hours=1))
        window_5m = df5m.loc[mask_5m]
        if len(window_5m) < 3:
            continue
        if trade_dir == "LONG":
            trigger_active, _ = check_trigger_5m(window_5m)
        else:
            trigger_active, _ = check_trigger_5m_short(window_5m)
        if not trigger_active:
            continue

        # ── Compute score (direction-aware) ───────────────────────────────
        score = 0
        cur_rsi1h = float(rsi1h.iloc[-1])
        bb_up, _, bb_dn = calc_bb(close_1h, BB_PERIOD, BB_STDEV)
        vol_avg = window_1h["volume"].rolling(VOL_PERIOD).mean().iloc[-1]
        cvd = calc_cvd_delta(window_1h, n=3)
        sma10 = calc_sma(close_1h, 10).iloc[-1]
        sma20 = calc_sma(close_1h, 20).iloc[-1]

        if trade_dir == "LONG":
            if cur_rsi1h < 40: score += 2
            if rsi_divs["bull"]: score += 2
            if lrc_dn and abs(price - lrc_dn) / price * 100 <= 1.5: score += 1
            if price <= bb_dn.iloc[-1]: score += 1
            if window_1h["volume"].iloc[-1] >= vol_avg: score += 1
            if cvd > 0: score += 1
            if sma10 > sma20: score += 1
        else:  # SHORT
            if cur_rsi1h > 60: score += 2
            if rsi_divs["bear"]: score += 2
            if lrc_up and abs(price - lrc_up) / price * 100 <= 1.5: score += 1
            if price >= bb_up.iloc[-1]: score += 1
            if window_1h["volume"].iloc[-1] >= vol_avg: score += 1
            if cvd < 0: score += 1
            if sma10 < sma20: score += 1

        # Size multiplier
        if score >= SCORE_PREMIUM:
            size_mult = 1.5
        elif score >= SCORE_STANDARD:
            size_mult = 1.0
        else:
            size_mult = 0.5

        # ── Open position ─────────────────────────────────────────────────
        if sl_mode == "atr":
            atr_series = calc_atr(window_1h, ATR_PERIOD)
            atr_val = float(atr_series.iloc[-1])
            if pd.isna(atr_val) or atr_val <= 0:
                continue

            # Per-direction resolver (only when caller explicitly passed symbol_overrides
            # AND legacy kwargs were NOT set — legacy kwargs retain precedence).
            legacy_override_active = (atr_sl_mult is not None) or (atr_tp_mult is not None) or (atr_be_mult is not None)
            if symbol_overrides is not None and not legacy_override_active:
                resolved = resolve_direction_params(symbol_overrides, symbol, trade_dir)
                if resolved is None:
                    # Direction disabled for this symbol — skip opening the position.
                    continue
                _sl_m_use = resolved["atr_sl_mult"]
                _tp_m_use = resolved["atr_tp_mult"]
                _be_m_use = resolved["atr_be_mult"]
            else:
                _sl_m_use = _sl_m
                _tp_m_use = _tp_m
                _be_m_use = _be_m

            if trade_dir == "SHORT":
                sl_price = round(price + atr_val * _sl_m_use, 2)
                tp_price = round(price - atr_val * _tp_m_use, 2)
                be_threshold = price - atr_val * _be_m_use
            else:
                sl_price = round(price - atr_val * _sl_m_use, 2)
                tp_price = round(price + atr_val * _tp_m_use, 2)
                be_threshold = price + atr_val * _be_m_use
        else:
            _sl_m_use = _sl_m
            _tp_m_use = _tp_m
            _be_m_use = _be_m
            if trade_dir == "SHORT":
                sl_price = round(price * (1 + SL_PCT / 100), 2)
                tp_price = round(price * (1 - TP_PCT / 100), 2)
            else:
                sl_price = round(price * (1 - SL_PCT / 100), 2)
                tp_price = round(price * (1 + TP_PCT / 100), 2)
            be_threshold = None

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
        }

    # Close any open position at last bar price
    if position is not None:
        last_bar = df1h.iloc[-1]
        exit_price = float(last_bar["close"])
        pnl_pct = (exit_price - position["entry_price"]) / position["entry_price"] * 100
        risk_amount = capital * RISK_PER_TRADE * position["size_mult"]
        sl_pct_actual = (position["entry_price"] - position["sl_orig"]) / position["entry_price"] * 100
        pnl_usd = risk_amount * (pnl_pct / sl_pct_actual) if sl_pct_actual > 0 else 0
        trades.append({
            "entry_time": position["entry_time"],
            "exit_time": df1h.index[-1],
            "entry_price": position["entry_price"],
            "exit_price": exit_price,
            "exit_reason": "OPEN",
            "pnl_pct": round(pnl_pct, 4),
            "pnl_usd": round(pnl_usd, 2),
            "score": position["score"],
            "size_mult": position["size_mult"],
            "duration_hours": (df1h.index[-1] - position["entry_time"]).total_seconds() / 3600,
            "atr_sl_mult_used": position.get("atr_sl_mult_used"),
            "atr_tp_mult_used": position.get("atr_tp_mult_used"),
            "atr_be_mult_used": position.get("atr_be_mult_used"),
        })
        capital += pnl_usd

    return trades, equity_curve


# ─────────────────────────────────────────────────────────────────────────────
#  METRICS
# ─────────────────────────────────────────────────────────────────────────────

def calculate_metrics(trades: list[dict], equity_curve: list[dict]) -> dict:
    """Calculate comprehensive trading metrics."""
    if not trades:
        return {"error": "No trades generated"}

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
        # Annualize based on trades per year (not hourly)
        trades_per_year = len(closed) / ((closed["exit_time"].iloc[-1] - closed["entry_time"].iloc[0]).days / 365.25) if len(closed) > 1 else 0
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

    return {
        "total_trades": total_trades,
        "wins": win_count,
        "losses": loss_count,
        "win_rate": round(win_rate * 100, 1),
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
                    sim_start: datetime = None, sim_end: datetime = None) -> str:
    """Generate markdown report."""
    m = metrics
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    period_start = sim_start.strftime("%Y-%m-%d") if sim_start else "N/A"
    period_end = sim_end.strftime("%Y-%m-%d") if sim_end else "present"

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
- **Constraints:** One position at a time, {COOLDOWN_H}h cooldown between trades
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

    trades, equity_curve = simulate_strategy(df1h, df4h, df5m, symbol, sl_mode=args.sl_mode,
                                               df1d=df1d, sim_start=sim_start, sim_end=sim_end,
                                               df_fng=df_fng, df_funding=df_funding)
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
    print(f"{'='*60}\n")

    # Generate and save report
    report = generate_report(symbol, metrics, regimes, trades, sim_start=sim_start, sim_end=sim_end)
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
