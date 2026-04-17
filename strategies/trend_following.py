"""
Trend-Following Strategy Engine
================================

EMA crossover strategy with DI+/DI- directional filters, ADX strength,
trailing stop sizing, and optional 5M trigger confirmation.

Used when ADX >= threshold (trending market), as routed by strategies.router.
"""

import numpy as np
import pandas as pd

from btc_scanner import (
    calc_rsi,
    calc_atr,
    calc_sma,
    check_trigger_5m,
    check_trigger_5m_short,
    score_label,
    RSI_PERIOD,
    ATR_PERIOD,
    VOL_PERIOD,
    SCORE_MIN_HALF,
    SCORE_STANDARD,
    SCORE_PREMIUM,
)

# ---------------------------------------------------------------------------
# Constants (defaults, overridable per-symbol via config)
# ---------------------------------------------------------------------------
TF_EMA_FAST = 9
TF_EMA_SLOW = 21
TF_EMA_FILTER = 50
TF_ATR_TRAIL = 2.5
TF_RSI_ENTRY_LONG = 55
TF_RSI_ENTRY_SHORT = 45


# ---------------------------------------------------------------------------
# DI+/DI- Calculation
# ---------------------------------------------------------------------------

def calc_di_components(df: pd.DataFrame, period: int = 14):
    """
    Compute DI+ and DI- directional indicators.

    Same math as btc_scanner.calc_adx() but returns the two DI series
    instead of the final ADX value.

    Args:
        df: DataFrame with 'high', 'low', 'close' columns.
        period: Smoothing period (default 14, Wilder).

    Returns:
        (di_plus, di_minus): Tuple of two pd.Series.
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]

    # True Range
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    # +DM and -DM
    up_move = high.diff()
    down_move = (-low).diff()  # equivalent to low.shift(1) - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_dm_s = pd.Series(plus_dm, index=df.index)
    minus_dm_s = pd.Series(minus_dm, index=df.index)

    # Smooth with EMA (Wilder: alpha = 1/period)
    alpha = 1.0 / period
    atr_smooth = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_dm_smooth = plus_dm_s.ewm(alpha=alpha, adjust=False).mean()
    minus_dm_smooth = minus_dm_s.ewm(alpha=alpha, adjust=False).mean()

    # DI+ and DI-
    di_plus = (plus_dm_smooth / atr_smooth.replace(0, np.nan)) * 100
    di_minus = (minus_dm_smooth / atr_smooth.replace(0, np.nan)) * 100

    return di_plus, di_minus
