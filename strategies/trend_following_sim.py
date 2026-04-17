"""
Trend-Following Backtest Simulation
=====================================

Bar-by-bar simulation helpers for the trend-following strategy.
Called from backtest.py when the ADX router selects ``trend_following``.

Mirrors the entry / scoring / sizing logic of
``strategies.trend_following.assess_signal`` but adds:
  - mutable ``tf_state`` that tracks the open position + trailing stop
  - ``_update_trailing_stop`` that ratchets the stop in the position's favour
  - ``assess_tf_bar`` that returns "enter" | "exit" | "hold" | "skip"
"""

import pandas as pd
import numpy as np
from datetime import timedelta

from btc_scanner import (
    calc_rsi,
    calc_atr,
    calc_adx,
    calc_sma,
    check_trigger_5m,
    check_trigger_5m_short,
    RSI_PERIOD,
    ATR_PERIOD,
    VOL_PERIOD,
    COOLDOWN_H,
    SCORE_MIN_HALF,
    SCORE_STANDARD,
    SCORE_PREMIUM,
)
from strategies.trend_following import calc_di_components, _get_tf_params

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RISK_PER_TRADE = 0.01  # 1 % of capital per trade


# ---------------------------------------------------------------------------
# State factory
# ---------------------------------------------------------------------------

def create_tf_state() -> dict:
    """Return a fresh trend-following state dict."""
    return {
        "position": None,
        "highest_high": None,
        "lowest_low": None,
        "trailing_stop": None,
        "last_trade": None,
        "last_exit_time": None,
    }


# ---------------------------------------------------------------------------
# Trailing stop
# ---------------------------------------------------------------------------

def _update_trailing_stop(state: dict, high: float, low: float,
                          atr_val: float, atr_trail: float) -> float:
    """Move trailing stop in favour of the open position.

    LONG:  trail = highest_high - atr * trail_mult.
           Stop = max(old_stop, new_trail).  Track highest_high.
    SHORT: trail = lowest_low + atr * trail_mult.
           Stop = min(old_stop, new_trail).  Track lowest_low.

    Returns the updated stop price.
    """
    pos = state["position"]
    direction = pos["direction"]

    if direction == "LONG":
        state["highest_high"] = max(state["highest_high"], high)
        new_trail = state["highest_high"] - atr_val * atr_trail
        state["trailing_stop"] = max(state["trailing_stop"], new_trail)
    else:  # SHORT
        state["lowest_low"] = min(state["lowest_low"], low)
        new_trail = state["lowest_low"] + atr_val * atr_trail
        state["trailing_stop"] = min(state["trailing_stop"], new_trail)

    return state["trailing_stop"]


# ---------------------------------------------------------------------------
# Per-bar assessment
# ---------------------------------------------------------------------------

def assess_tf_bar(
    window_1h: pd.DataFrame,
    df4h: pd.DataFrame,
    df5m: pd.DataFrame,
    bar_time,
    price: float,
    symbol: str,
    regime: str,
    cur_adx: float,
    config: dict,
    tf_state: dict,
) -> str:
    """Evaluate one bar for the trend-following strategy.

    Returns
    -------
    "enter" | "exit" | "hold" | "skip"
    """
    params = _get_tf_params(symbol, config)

    ema_fast_period = params["tf_ema_fast"]
    ema_slow_period = params["tf_ema_slow"]
    ema_filter_period = params["tf_ema_filter"]
    atr_trail_mult = params["tf_atr_trail"]
    rsi_entry_long = params["tf_rsi_entry_long"]
    rsi_entry_short = params["tf_rsi_entry_short"]
    allow_short = params["allow_short"]
    use_5m_trigger = params["use_5m_trigger"]

    close_1h = window_1h["close"]

    # -- Common indicators --
    atr_series = calc_atr(window_1h, ATR_PERIOD)
    atr_val = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else 0.0

    ema_fast = close_1h.ewm(span=ema_fast_period, adjust=False).mean()
    ema_slow = close_1h.ewm(span=ema_slow_period, adjust=False).mean()

    # ==================================================================
    # OPEN POSITION  ->  check for exit
    # ==================================================================
    if tf_state["position"] is not None:
        pos = tf_state["position"]
        direction = pos["direction"]
        bar_high = float(window_1h["high"].iloc[-1])
        bar_low = float(window_1h["low"].iloc[-1])

        # Update trailing stop
        if atr_val > 0:
            _update_trailing_stop(tf_state, bar_high, bar_low, atr_val, atr_trail_mult)

        # Check EMA reversal (fast crosses slow against position)
        cur_ema_fast = float(ema_fast.iloc[-1])
        cur_ema_slow = float(ema_slow.iloc[-1])
        ema_reversal = False
        if direction == "LONG" and cur_ema_fast < cur_ema_slow:
            ema_reversal = True
        elif direction == "SHORT" and cur_ema_fast > cur_ema_slow:
            ema_reversal = True

        # Check trailing stop hit
        trailing_stop_hit = False
        if direction == "LONG" and bar_low <= tf_state["trailing_stop"]:
            trailing_stop_hit = True
        elif direction == "SHORT" and bar_high >= tf_state["trailing_stop"]:
            trailing_stop_hit = True

        # Determine exit
        exit_reason = None
        if trailing_stop_hit:
            exit_reason = "TRAILING_STOP"
        elif ema_reversal:
            exit_reason = "EMA_REVERSAL"

        if exit_reason is not None:
            # Calculate exit price
            if exit_reason == "TRAILING_STOP":
                exit_price = tf_state["trailing_stop"]
            else:
                exit_price = price  # close of bar for EMA reversal

            # PnL
            if direction == "SHORT":
                pnl_pct = (pos["entry_price"] - exit_price) / pos["entry_price"] * 100
            else:
                pnl_pct = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100

            sl_pct_actual = abs(pos["entry_price"] - pos["sl_orig"]) / pos["entry_price"] * 100
            risk_amount = RISK_PER_TRADE * pos["size_mult"] * 10000  # $10k capital
            pnl_usd = risk_amount * (pnl_pct / sl_pct_actual) if sl_pct_actual > 0 else 0

            duration_hours = (bar_time - pos["entry_time"]).total_seconds() / 3600

            tf_state["last_trade"] = {
                "entry_time": pos["entry_time"],
                "exit_time": bar_time,
                "entry_price": pos["entry_price"],
                "exit_price": round(exit_price, 2),
                "exit_reason": exit_reason,
                "direction": direction,
                "pnl_pct": round(pnl_pct, 4),
                "pnl_usd": round(pnl_usd, 2),
                "score": pos["score"],
                "size_mult": pos["size_mult"],
                "duration_hours": round(duration_hours, 2),
                "strategy": "trend_following",
                "adx_at_entry": pos.get("adx_at_entry", 0),
                "trailing_stop_final": round(tf_state["trailing_stop"], 2),
            }
            # Clear position state
            tf_state["position"] = None
            tf_state["highest_high"] = None
            tf_state["lowest_low"] = None
            tf_state["trailing_stop"] = None
            tf_state["last_exit_time"] = bar_time
            return "exit"

        return "hold"

    # ==================================================================
    # NO POSITION  ->  check for entry
    # ==================================================================

    # Cooldown
    if tf_state["last_exit_time"] is not None:
        hours_since = (bar_time - tf_state["last_exit_time"]).total_seconds() / 3600
        if hours_since < COOLDOWN_H:
            return "skip"

    if len(window_1h) < 60:
        return "skip"

    # EMAs
    ema_filt = close_1h.ewm(span=ema_filter_period, adjust=False).mean()

    cur_ema_fast = float(ema_fast.iloc[-1])
    cur_ema_slow = float(ema_slow.iloc[-1])
    cur_ema_filt = float(ema_filt.iloc[-1])

    # RSI
    rsi_1h = calc_rsi(close_1h, RSI_PERIOD)
    cur_rsi = float(rsi_1h.iloc[-1]) if not pd.isna(rsi_1h.iloc[-1]) else 50.0

    # DI+/DI-
    di_plus, di_minus = calc_di_components(window_1h)
    dp = float(di_plus.iloc[-1]) if not pd.isna(di_plus.iloc[-1]) else 0.0
    dm = float(di_minus.iloc[-1]) if not pd.isna(di_minus.iloc[-1]) else 0.0

    # Direction logic (same as assess_signal)
    ema_cross_long = cur_ema_fast > cur_ema_slow
    ema_cross_short = cur_ema_fast < cur_ema_slow

    long_conditions = (
        ema_cross_long
        and price > cur_ema_filt
        and cur_rsi > rsi_entry_long
        and dp > dm
        and regime != "SHORT"
    )

    short_conditions = (
        ema_cross_short
        and price < cur_ema_filt
        and cur_rsi < rsi_entry_short
        and dm > dp
        and regime != "LONG"
        and allow_short
    )

    direction = None
    if long_conditions:
        direction = "LONG"
    elif short_conditions:
        direction = "SHORT"

    if direction is None:
        return "skip"

    # Macro 4H filter: SMA100
    mask_4h = df4h.index <= bar_time
    window_4h = df4h.loc[mask_4h].iloc[-100:]
    if len(window_4h) < 100:
        return "skip"
    sma100_4h = calc_sma(window_4h["close"], 100).iloc[-1]
    if pd.isna(sma100_4h):
        return "skip"
    if direction == "LONG" and price <= sma100_4h:
        return "skip"
    if direction == "SHORT" and price >= sma100_4h:
        return "skip"

    # 5M trigger (optional)
    if use_5m_trigger:
        mask_5m = (df5m.index <= bar_time) & (df5m.index > bar_time - timedelta(hours=1))
        window_5m = df5m.loc[mask_5m]
        if len(window_5m) < 3:
            return "skip"
        if direction == "SHORT":
            trigger_active, _ = check_trigger_5m_short(window_5m)
        else:
            trigger_active, _ = check_trigger_5m(window_5m)
        if not trigger_active:
            return "skip"

    # -- Scoring (T1-T7, max 9) --
    score = 0

    # T1: EMA cross freshness (in last 3 bars)
    ema_diff = ema_fast - ema_slow
    if direction == "SHORT":
        cross_fresh = any(
            float(ema_diff.iloc[-(j + 1)]) > 0
            for j in range(1, min(4, len(ema_diff)))
            if not pd.isna(ema_diff.iloc[-(j + 1)])
        ) and float(ema_diff.iloc[-1]) < 0
    else:
        cross_fresh = any(
            float(ema_diff.iloc[-(j + 1)]) < 0
            for j in range(1, min(4, len(ema_diff)))
            if not pd.isna(ema_diff.iloc[-(j + 1)])
        ) and float(ema_diff.iloc[-1]) > 0
    if cross_fresh:
        score += 2

    # T2: ADX strength
    if cur_adx > 30:
        score += 2

    # T3: Price vs EMA filter
    if direction == "SHORT":
        if price < cur_ema_filt:
            score += 1
    else:
        if price > cur_ema_filt:
            score += 1

    # T4: RSI momentum
    if direction == "SHORT":
        if cur_rsi < 40:
            score += 1
    else:
        if cur_rsi > 60:
            score += 1

    # T5: Volume above average
    vol_avg = float(window_1h["volume"].rolling(VOL_PERIOD).mean().iloc[-1])
    cur_vol = float(window_1h["volume"].iloc[-1])
    if vol_avg > 0 and cur_vol > vol_avg:
        score += 1

    # T6: DI spread
    di_spread = abs(dp - dm)
    if di_spread > 10:
        score += 1

    # T7: Macro aligned (price vs SMA100 4H)
    if direction == "SHORT":
        if price < sma100_4h:
            score += 1
    else:
        if price > sma100_4h:
            score += 1

    # Size multiplier
    if score >= SCORE_PREMIUM:
        size_mult = 1.5
    elif score >= SCORE_STANDARD:
        size_mult = 1.0
    else:
        size_mult = 0.5

    # Trailing stop from ATR
    if atr_val <= 0:
        return "skip"

    sl_dist = atr_val * atr_trail_mult
    if direction == "SHORT":
        sl_price = price + sl_dist
        tf_state["lowest_low"] = float(window_1h["low"].iloc[-1])
        tf_state["trailing_stop"] = sl_price
    else:
        sl_price = price - sl_dist
        tf_state["highest_high"] = float(window_1h["high"].iloc[-1])
        tf_state["trailing_stop"] = sl_price

    tf_state["position"] = {
        "entry_price": price,
        "entry_time": bar_time,
        "score": score,
        "direction": direction,
        "sl_orig": sl_price,
        "size_mult": size_mult,
        "strategy": "trend_following",
        "adx_at_entry": round(cur_adx, 2),
    }

    return "enter"
