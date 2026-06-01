"""Per-minute USD liquidity proxy, identical to backtest.py:1018.

    usd_per_min = (close * volume) / 60
    liquidity   = usd_per_min.rolling(720, min_periods=120).mean()

Kept pure (takes a DataFrame) so it tests without market-data access.
"""
from __future__ import annotations

import pandas as pd


def liquidity_series(df1h: pd.DataFrame) -> pd.Series:
    usd_per_min = (df1h["close"] * df1h["volume"]) / 60.0
    return usd_per_min.rolling(720, min_periods=120).mean()


def liquidity_at(series: pd.Series, ts) -> float:
    """Last liquidity value at or before ts. NaN if none / empty.

    Aligns ts tz-awareness to the series index. The 1H bars represent UTC
    instants whether get_cached_data returns a tz-naive (datetime64) or
    tz-aware index; the live entry/exit timestamps are tz-aware UTC. Without
    this alignment, `index <= ts` raises "Cannot compare tz-naive and tz-aware".
    """
    if series is None or len(series) == 0:
        return float("nan")
    ts = pd.Timestamp(ts)
    idx_tz = getattr(series.index, "tz", None)
    if idx_tz is None:
        # naive index (UTC wall-clock); drop ts tz to match (keeps UTC instant).
        if ts.tz is not None:
            ts = ts.tz_localize(None)
    else:
        # tz-aware index; normalize ts to UTC-aware.
        ts = ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")
    mask = series.index <= ts
    if not mask.any():
        return float("nan")
    return float(series[mask].iloc[-1])
