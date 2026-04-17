"""Tests for strategies/trend_following.py — DI components + trend-following signal assessment."""

import numpy as np
import pandas as pd
import pytest


def _make_ohlcv(n=100, base_price=100.0, trend=0.0):
    """Generate synthetic OHLCV data with optional trend."""
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=n, freq="1h")
    closes = [base_price]
    for i in range(1, n):
        closes.append(closes[-1] * (1 + trend + np.random.normal(0, 0.005)))
    closes = np.array(closes)
    highs = closes * (1 + np.abs(np.random.normal(0, 0.003, n)))
    lows = closes * (1 - np.abs(np.random.normal(0, 0.003, n)))
    opens = closes * (1 + np.random.normal(0, 0.001, n))
    volumes = np.random.uniform(100, 1000, n)
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": volumes,
    }, index=dates)


# ---------------------------------------------------------------------------
# Task 2: calc_di_components
# ---------------------------------------------------------------------------

class TestCalcDiComponents:
    def test_returns_two_series(self):
        from strategies.trend_following import calc_di_components
        df = _make_ohlcv(100)
        di_plus, di_minus = calc_di_components(df)
        assert isinstance(di_plus, pd.Series)
        assert isinstance(di_minus, pd.Series)
        assert len(di_plus) == len(df)
        assert len(di_minus) == len(df)

    def test_values_between_0_and_100(self):
        from strategies.trend_following import calc_di_components
        df = _make_ohlcv(200)
        di_plus, di_minus = calc_di_components(df)
        # Drop NaN values at the start, check valid range
        dp = di_plus.dropna()
        dm = di_minus.dropna()
        assert (dp >= 0).all(), f"DI+ has negative values: {dp.min()}"
        assert (dp <= 100).all(), f"DI+ exceeds 100: {dp.max()}"
        assert (dm >= 0).all(), f"DI- has negative values: {dm.min()}"
        assert (dm <= 100).all(), f"DI- exceeds 100: {dm.max()}"

    def test_uptrend_di_plus_greater(self):
        from strategies.trend_following import calc_di_components
        df = _make_ohlcv(200, trend=0.003)
        di_plus, di_minus = calc_di_components(df)
        # In an uptrend, DI+ should be greater than DI- on average (last 50 bars)
        tail_plus = di_plus.iloc[-50:].mean()
        tail_minus = di_minus.iloc[-50:].mean()
        assert tail_plus > tail_minus, (
            f"In uptrend, expected DI+ ({tail_plus:.2f}) > DI- ({tail_minus:.2f})"
        )

    def test_downtrend_di_minus_greater(self):
        from strategies.trend_following import calc_di_components
        df = _make_ohlcv(200, trend=-0.003)
        di_plus, di_minus = calc_di_components(df)
        # In a downtrend, DI- should be greater than DI+ on average (last 50 bars)
        tail_plus = di_plus.iloc[-50:].mean()
        tail_minus = di_minus.iloc[-50:].mean()
        assert tail_minus > tail_plus, (
            f"In downtrend, expected DI- ({tail_minus:.2f}) > DI+ ({tail_plus:.2f})"
        )
