"""Tests for parameterized regime thresholds in _compute_local_regime.

Defaults must preserve byte-identity with pre-parameterization production behavior.
New kwargs enable threshold sweeps used by tools/regime_retune_pre_holdout.
"""
import pandas as pd
import pytest

from strategy.regime import _compute_local_regime


def _make_df_daily(close_values):
    return pd.DataFrame({"close": close_values})


class TestComputeLocalRegimeDefaults:
    def test_default_thresholds_preserve_legacy_60_40_bull(self):
        # composite = 100*0.4 + 80*0.3 + 80*0.3 = 88 > 60 → BULL
        result = _compute_local_regime(
            symbol="BTCUSDT", mode="global",
            df_daily_sym=_make_df_daily([100] * 250),
            fng_score=80, funding_score=80,
        )
        assert result["regime"] == "BULL"
        assert result["score"] == 88.0

    def test_default_thresholds_preserve_legacy_60_40_bear(self):
        # Strongly declining 250-bar series → price_score very low
        # (death cross + below SMA200 + ret30d<-10 → 100-40-30-20=10).
        # composite = 10*0.4 + 0 + 0 = 4 < 40 → BEAR.
        declining = list(range(250, 0, -1))
        result = _compute_local_regime(
            symbol="BTCUSDT", mode="global",
            df_daily_sym=_make_df_daily(declining),
            fng_score=0, funding_score=0,
        )
        assert result["regime"] == "BEAR"

    def test_default_thresholds_preserve_legacy_60_40_neutral(self):
        # composite = 100*0.4 + 10*0.3 + 10*0.3 = 46 → NEUTRAL
        result = _compute_local_regime(
            symbol="BTCUSDT", mode="global",
            df_daily_sym=_make_df_daily([100] * 250),
            fng_score=10, funding_score=10,
        )
        assert result["regime"] == "NEUTRAL"


class TestComputeLocalRegimeParameterized:
    def test_70_30_thresholds_shift_bull_boundary(self):
        # composite = 100*0.4 + 50*0.3 + 20*0.3 = 61 → BULL with (60,40), NEUTRAL with (70,30)
        result_default = _compute_local_regime(
            symbol="BTCUSDT", mode="global",
            df_daily_sym=_make_df_daily([100] * 250),
            fng_score=50, funding_score=20,
        )
        assert result_default["regime"] == "BULL"

        result_70_30 = _compute_local_regime(
            symbol="BTCUSDT", mode="global",
            df_daily_sym=_make_df_daily([100] * 250),
            fng_score=50, funding_score=20,
            bull_above=70, bear_below=30,
        )
        assert result_70_30["regime"] == "NEUTRAL"

    def test_80_20_thresholds_extreme(self):
        # composite = 88 should be BULL with (60,40), (70,30), and (80,20)
        result = _compute_local_regime(
            symbol="BTCUSDT", mode="global",
            df_daily_sym=_make_df_daily([100] * 250),
            fng_score=80, funding_score=80,
            bull_above=80, bear_below=20,
        )
        assert result["regime"] == "BULL"

        # composite = 75 should be NEUTRAL with (80,20)
        # price=100*0.4=40, fng=50*0.3=15, funding=66.67*0.3≈20 → 75
        # use fng=50, funding=67 → 40+15+20.1=75.1
        result_neutral = _compute_local_regime(
            symbol="BTCUSDT", mode="global",
            df_daily_sym=_make_df_daily([100] * 250),
            fng_score=50, funding_score=67,
            bull_above=80, bear_below=20,
        )
        assert result_neutral["regime"] == "NEUTRAL"

    def test_invalid_thresholds_raise(self):
        with pytest.raises(ValueError, match="bear_below must be < bull_above"):
            _compute_local_regime(
                symbol="BTCUSDT", mode="global",
                df_daily_sym=_make_df_daily([100] * 250),
                fng_score=50, funding_score=50,
                bull_above=40, bear_below=60,  # inverted
            )

    def test_equal_thresholds_raise(self):
        with pytest.raises(ValueError, match="bear_below must be < bull_above"):
            _compute_local_regime(
                symbol="BTCUSDT", mode="global",
                df_daily_sym=_make_df_daily([100] * 250),
                fng_score=50, funding_score=50,
                bull_above=50, bear_below=50,
            )


