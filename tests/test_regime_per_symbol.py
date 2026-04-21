import pandas as pd
import pytest
from btc_scanner import _compute_price_score


def _df_daily(closes, highs=None, lows=None):
    n = len(closes)
    return pd.DataFrame({
        "open":  closes,
        "high":  highs if highs is not None else [c + 1 for c in closes],
        "low":   lows if lows is not None else [c - 1 for c in closes],
        "close": closes,
        "volume": [1000] * n,
    }, index=pd.date_range("2020-01-01", periods=n, freq="D"))


class TestComputePriceScore:
    def test_death_cross_price_below_sma_negative_return(self):
        """SMA50 < SMA200, price < SMA200, 30d return < -10% → 100-40-30-20=10."""
        closes = [100.0] * 170 + [100.0 - i for i in range(30)] + [50.0] * 10
        df = _df_daily(closes)
        assert _compute_price_score(df) == 10

    def test_only_death_cross(self):
        """SMA50 < SMA200, price > SMA200, ret30 positive → 100-40=60."""
        closes = [200.0] * 150 + [160.0] * 30 + [140.0] * 15 + [210.0] * 15
        df = _df_daily(closes)
        score = _compute_price_score(df)
        assert 55 <= score <= 75

    def test_bull_market_clean(self):
        """SMA50 > SMA200, price > SMA200, ret30 positive → 100."""
        closes = list(range(100, 310))
        df = _df_daily(closes)
        assert _compute_price_score(df) == 100

    def test_transition_mild(self):
        """SMA50 > SMA200, price < SMA200, ret30 slightly negative → 100-30-10=60."""
        closes = [100.0] * 150 + [105.0] * 40 + [95.0] * 20
        df = _df_daily(closes)
        score = _compute_price_score(df)
        assert 55 <= score <= 70

    def test_insufficient_data_returns_100(self):
        """< 200 bars → fallback 100 (bullish assumption)."""
        df = _df_daily([100.0] * 150)
        assert _compute_price_score(df) == 100

    def test_empty_dataframe_returns_100(self):
        """Empty df → 100."""
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        assert _compute_price_score(df) == 100

    def test_nan_prices_graceful(self):
        """NaN prices don't crash."""
        closes = [100.0] * 200
        closes[50] = float("nan")
        df = _df_daily(closes)
        score = _compute_price_score(df)
        assert 0 <= score <= 100
