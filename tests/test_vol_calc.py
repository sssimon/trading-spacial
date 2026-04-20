import numpy as np
import pandas as pd
import pytest


def _daily_df(opens, highs, lows, closes):
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
    })


class TestYangZhangVol:
    def test_zero_variance_bars_returns_tiny_floor(self):
        from btc_scanner import annualized_vol_yang_zhang
        # All bars identical → variance zero → result near zero
        df = _daily_df([100.0] * 30, [100.0] * 30, [100.0] * 30, [100.0] * 30)
        vol = annualized_vol_yang_zhang(df)
        assert 0.0 <= vol < 0.01

    def test_short_series_returns_fallback(self):
        from btc_scanner import annualized_vol_yang_zhang, TARGET_VOL_ANNUAL
        df = _daily_df([100.0] * 3, [101.0] * 3, [99.0] * 3, [100.0] * 3)
        vol = annualized_vol_yang_zhang(df)
        assert vol == TARGET_VOL_ANNUAL

    def test_typical_crypto_volatility_in_range(self):
        from btc_scanner import annualized_vol_yang_zhang
        # Simulate ~2% daily range, 1% daily drift noise
        rng = np.random.default_rng(42)
        n = 30
        closes = 100.0 * np.exp(rng.normal(0, 0.02, n).cumsum())
        opens = np.concatenate([[closes[0]], closes[:-1]])
        highs = np.maximum(opens, closes) * (1 + np.abs(rng.normal(0, 0.01, n)))
        lows = np.minimum(opens, closes) * (1 - np.abs(rng.normal(0, 0.01, n)))
        df = _daily_df(opens, highs, lows, closes)
        vol = annualized_vol_yang_zhang(df)
        # Expect roughly 20-50% annualized for such a series
        assert 0.1 <= vol <= 1.0
