"""Integration tests for regime-allocation dispatch in evaluate_signal
(epic #338 Phase 1B, part of #342).

Covers:
- Flag-off byte-identical behavior (LRC path untouched)
- Flag-on dispatch to ensemble path
- Warmup gating (insufficient daily bars)
- Direction mapping (LONG / SHORT / NONE from ensemble vote)
- Score tier mapping (PREMIUM / STANDARD / MINIMA from confidence)
- Indicators population (regime_allocation_* + realized_vol_annualized_30d)
- Edge cases (empty / None df1d, all-flat ensemble, NaN vol)
"""
import numpy as np
import pandas as pd
import pytest

from strategy.core import (
    _REGIME_ALLOC_PREMIUM_THRESHOLD,
    _REGIME_ALLOC_STANDARD_THRESHOLD,
    _REGIME_ALLOCATION_MIN_DAYS,
    SignalDecision,
    evaluate_signal,
)


@pytest.fixture
def daily_uptrend_500():
    """500 days of clean linear uptrend 100 → 599. Long enough to warm up the
    360-day Donchian lookback + 30-day vol window."""
    idx = pd.date_range("2024-01-01", periods=500, freq="D")
    closes = np.arange(100.0, 600.0)
    return pd.DataFrame(
        {
            "open": closes - 0.2,
            "high": closes + 0.5,
            "low": closes - 0.5,
            "close": closes,
            "volume": [1_000_000.0] * 500,
        },
        index=idx,
    )


@pytest.fixture
def daily_downtrend_500():
    """500 days of clean linear downtrend 600 → 101."""
    idx = pd.date_range("2024-01-01", periods=500, freq="D")
    closes = np.arange(600.0, 100.0, -1.0)
    return pd.DataFrame(
        {
            "open": closes + 0.2,
            "high": closes + 0.5,
            "low": closes - 0.5,
            "close": closes,
            "volume": [1_000_000.0] * 500,
        },
        index=idx,
    )


@pytest.fixture
def daily_flat_500():
    """500 days of constant price = 100.0. No breakouts possible → ensemble flat."""
    idx = pd.date_range("2024-01-01", periods=500, freq="D")
    closes = np.full(500, 100.0)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1_000_000.0] * 500,
        },
        index=idx,
    )


@pytest.fixture
def daily_warmup_short(daily_uptrend_500):
    """Truncated to 100 days — below the 390-day warmup threshold."""
    return daily_uptrend_500.iloc[:100].copy()


@pytest.fixture
def hourly_minimal():
    """Minimal 1H bars sufficient to NOT short-circuit the LRC guard
    (only used to test flag-off path)."""
    idx = pd.date_range("2024-01-01", periods=250, freq="h")
    closes = np.linspace(100.0, 110.0, 250)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes + 0.1,
            "low": closes - 0.1,
            "close": closes,
            "volume": [10_000.0] * 250,
        },
        index=idx,
    )


@pytest.fixture
def four_hourly_minimal():
    """Minimal 4H bars sufficient for LRC guard."""
    idx = pd.date_range("2024-01-01", periods=200, freq="4h")
    closes = np.linspace(100.0, 110.0, 200)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes + 0.1,
            "low": closes - 0.1,
            "close": closes,
            "volume": [10_000.0] * 200,
        },
        index=idx,
    )


@pytest.fixture
def empty_5m():
    """Empty 5m frame (acceptable when LRC path's 5m trigger logic isn't reached)."""
    return pd.DataFrame(
        columns=["open", "high", "low", "close", "volume"],
    )


# ---------------------------------------------------------------------------
# Flag-off byte-identical regression
# ---------------------------------------------------------------------------


class TestFlagOffByteIdentical:
    """When cfg.regime_allocation_enabled is absent or False, evaluate_signal
    must hit the existing LRC path (not the regime-allocation dispatch)."""

    def test_no_flag_in_cfg_takes_lrc_path(
        self, hourly_minimal, four_hourly_minimal, empty_5m, daily_uptrend_500
    ):
        """cfg without `regime_allocation_enabled` key → LRC path runs."""
        decision = evaluate_signal(
            df1h=hourly_minimal,
            df4h=four_hourly_minimal,
            df5m=empty_5m,
            df1d=daily_uptrend_500,
            symbol="BTCUSDT",
            cfg={},  # no flag
            regime={"regime": "BYPASS"},
        )
        # Hallmark of LRC path: indicators include lrc_pct, lrc_upper, etc.
        # (regime-allocation path does NOT set these)
        assert "lrc_pct" in decision.indicators
        assert "regime_allocation_vote" not in decision.indicators

    def test_explicit_false_flag_takes_lrc_path(
        self, hourly_minimal, four_hourly_minimal, empty_5m, daily_uptrend_500
    ):
        """cfg with regime_allocation_enabled=False → LRC path runs."""
        decision = evaluate_signal(
            df1h=hourly_minimal,
            df4h=four_hourly_minimal,
            df5m=empty_5m,
            df1d=daily_uptrend_500,
            symbol="BTCUSDT",
            cfg={"regime_allocation_enabled": False},
            regime={"regime": "BYPASS"},
        )
        assert "lrc_pct" in decision.indicators
        assert "regime_allocation_vote" not in decision.indicators

    def test_none_cfg_takes_lrc_path(
        self, hourly_minimal, four_hourly_minimal, empty_5m, daily_uptrend_500
    ):
        """cfg=None should not crash and should default to LRC path."""
        decision = evaluate_signal(
            df1h=hourly_minimal,
            df4h=four_hourly_minimal,
            df5m=empty_5m,
            df1d=daily_uptrend_500,
            symbol="BTCUSDT",
            cfg=None,
            regime={"regime": "BYPASS"},
        )
        # NOT regime-allocation path
        assert "regime_allocation_vote" not in decision.indicators


# ---------------------------------------------------------------------------
# Flag-on dispatch — uptrend / downtrend / flat
# ---------------------------------------------------------------------------


class TestFlagOnDispatch:
    def test_uptrend_yields_long_direction(
        self, hourly_minimal, four_hourly_minimal, empty_5m, daily_uptrend_500
    ):
        """500-day clean uptrend → ensemble votes LONG with high confidence."""
        decision = evaluate_signal(
            df1h=hourly_minimal,
            df4h=four_hourly_minimal,
            df5m=empty_5m,
            df1d=daily_uptrend_500,
            symbol="BTCUSDT",
            cfg={"regime_allocation_enabled": True},
            regime={"regime": "BULL"},
        )
        assert decision.direction == "LONG"
        assert decision.indicators["regime_allocation_vote"] > 0
        assert decision.indicators["regime_allocation_n_long"] > 0
        assert decision.is_signal is True

    def test_downtrend_yields_short_direction(
        self, hourly_minimal, four_hourly_minimal, empty_5m, daily_downtrend_500
    ):
        """500-day clean downtrend → ensemble votes SHORT."""
        decision = evaluate_signal(
            df1h=hourly_minimal,
            df4h=four_hourly_minimal,
            df5m=empty_5m,
            df1d=daily_downtrend_500,
            symbol="BTCUSDT",
            cfg={"regime_allocation_enabled": True},
            regime={"regime": "BEAR"},
        )
        assert decision.direction == "SHORT"
        assert decision.indicators["regime_allocation_vote"] < 0
        assert decision.indicators["regime_allocation_n_short"] > 0

    def test_flat_price_yields_none(
        self, hourly_minimal, four_hourly_minimal, empty_5m, daily_flat_500
    ):
        """Constant price → no breakouts → all lookbacks flat → direction NONE."""
        decision = evaluate_signal(
            df1h=hourly_minimal,
            df4h=four_hourly_minimal,
            df5m=empty_5m,
            df1d=daily_flat_500,
            symbol="BTCUSDT",
            cfg={"regime_allocation_enabled": True},
            regime={"regime": "NEUTRAL"},
        )
        assert decision.direction == "NONE"
        assert decision.indicators["regime_allocation_vote"] == 0
        assert decision.is_signal is False

    def test_regime_allocation_does_not_require_df1h_df4h(
        self, empty_5m, daily_uptrend_500
    ):
        """Regime-allocation path uses only df1d; empty df1h/df4h must not block."""
        empty_hourly = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        empty_4h = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        decision = evaluate_signal(
            df1h=empty_hourly,
            df4h=empty_4h,
            df5m=empty_5m,
            df1d=daily_uptrend_500,
            symbol="BTCUSDT",
            cfg={"regime_allocation_enabled": True},
            regime={"regime": "BULL"},
        )
        # Should still produce a LONG signal (LRC path would have returned empty)
        assert decision.direction == "LONG"

    def test_entry_price_set_when_direction_nonzero(
        self, hourly_minimal, four_hourly_minimal, empty_5m, daily_uptrend_500
    ):
        """entry_price = latest daily close when direction != NONE."""
        decision = evaluate_signal(
            df1h=hourly_minimal,
            df4h=four_hourly_minimal,
            df5m=empty_5m,
            df1d=daily_uptrend_500,
            symbol="BTCUSDT",
            cfg={"regime_allocation_enabled": True},
            regime={"regime": "BULL"},
        )
        expected_close = float(daily_uptrend_500["close"].iloc[-1])
        assert decision.entry_price == pytest.approx(expected_close)
        assert decision.sl_price is None
        assert decision.tp_price is None

    def test_entry_price_none_when_direction_none(
        self, hourly_minimal, four_hourly_minimal, empty_5m, daily_flat_500
    ):
        """entry_price = None when ensemble votes flat."""
        decision = evaluate_signal(
            df1h=hourly_minimal,
            df4h=four_hourly_minimal,
            df5m=empty_5m,
            df1d=daily_flat_500,
            symbol="BTCUSDT",
            cfg={"regime_allocation_enabled": True},
            regime={"regime": "NEUTRAL"},
        )
        assert decision.entry_price is None


# ---------------------------------------------------------------------------
# Warmup gating
# ---------------------------------------------------------------------------


class TestWarmup:
    def test_below_warmup_returns_none_with_reason(
        self, hourly_minimal, four_hourly_minimal, empty_5m, daily_warmup_short
    ):
        """Fewer than 390 daily bars → direction NONE + warmup reason flagged."""
        decision = evaluate_signal(
            df1h=hourly_minimal,
            df4h=four_hourly_minimal,
            df5m=empty_5m,
            df1d=daily_warmup_short,
            symbol="BTCUSDT",
            cfg={"regime_allocation_enabled": True},
            regime={"regime": "BULL"},
        )
        assert decision.direction == "NONE"
        assert "regime_allocation_warmup" in decision.reasons
        warmup_info = decision.reasons["regime_allocation_warmup"]
        assert warmup_info["have_days"] == 100
        assert warmup_info["need_days"] == _REGIME_ALLOCATION_MIN_DAYS

    def test_warmup_threshold_is_390_days(self):
        """Threshold = longest lookback (360) + vol window (30) = 390."""
        assert _REGIME_ALLOCATION_MIN_DAYS == 390

    def test_empty_df1d_returns_none_with_reason(
        self, hourly_minimal, four_hourly_minimal, empty_5m
    ):
        """Empty df1d → flagged with `regime_allocation_no_daily_data`."""
        empty_df1d = pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"]
        )
        decision = evaluate_signal(
            df1h=hourly_minimal,
            df4h=four_hourly_minimal,
            df5m=empty_5m,
            df1d=empty_df1d,
            symbol="BTCUSDT",
            cfg={"regime_allocation_enabled": True},
            regime={"regime": "BULL"},
        )
        assert decision.direction == "NONE"
        assert decision.reasons.get("regime_allocation_no_daily_data") is True


# ---------------------------------------------------------------------------
# Score tier mapping
# ---------------------------------------------------------------------------


class TestScoreTiers:
    def test_premium_threshold_value(self):
        """≥7/9 lookbacks aligned → PREMIUM."""
        assert _REGIME_ALLOC_PREMIUM_THRESHOLD == pytest.approx(0.78)

    def test_standard_threshold_value(self):
        """≥5/9 lookbacks aligned → STANDARD."""
        assert _REGIME_ALLOC_STANDARD_THRESHOLD == pytest.approx(0.45)

    def test_uptrend_yields_premium_tier(
        self, hourly_minimal, four_hourly_minimal, empty_5m, daily_uptrend_500
    ):
        """Clean uptrend → all 9 lookbacks LONG → confidence=1.0 → PREMIUM."""
        decision = evaluate_signal(
            df1h=hourly_minimal,
            df4h=four_hourly_minimal,
            df5m=empty_5m,
            df1d=daily_uptrend_500,
            symbol="BTCUSDT",
            cfg={"regime_allocation_enabled": True},
            regime={"regime": "BULL"},
        )
        assert decision.score == 5
        assert decision.score_label == "PREMIUM"
        assert decision.indicators["regime_allocation_confidence"] >= _REGIME_ALLOC_PREMIUM_THRESHOLD

    def test_flat_yields_zero_score(
        self, hourly_minimal, four_hourly_minimal, empty_5m, daily_flat_500
    ):
        """Flat → direction NONE → score 0, label empty."""
        decision = evaluate_signal(
            df1h=hourly_minimal,
            df4h=four_hourly_minimal,
            df5m=empty_5m,
            df1d=daily_flat_500,
            symbol="BTCUSDT",
            cfg={"regime_allocation_enabled": True},
            regime={"regime": "NEUTRAL"},
        )
        assert decision.score == 0
        assert decision.score_label == ""


# ---------------------------------------------------------------------------
# Indicators population
# ---------------------------------------------------------------------------


class TestIndicators:
    def test_all_regime_allocation_fields_present(
        self, hourly_minimal, four_hourly_minimal, empty_5m, daily_uptrend_500
    ):
        """Every regime_allocation_* field is populated when path is taken."""
        decision = evaluate_signal(
            df1h=hourly_minimal,
            df4h=four_hourly_minimal,
            df5m=empty_5m,
            df1d=daily_uptrend_500,
            symbol="BTCUSDT",
            cfg={"regime_allocation_enabled": True},
            regime={"regime": "BULL"},
        )
        required = {
            "regime_allocation_direction_signed",
            "regime_allocation_vote",
            "regime_allocation_confidence",
            "regime_allocation_n_long",
            "regime_allocation_n_short",
            "regime_allocation_n_flat",
            "regime_allocation_n_lookbacks",
            "realized_vol_annualized_30d",
            "daily_close",
        }
        missing = required - set(decision.indicators.keys())
        assert not missing, f"Missing regime-allocation indicators: {missing}"

    def test_n_lookbacks_is_9_for_zarattini(
        self, hourly_minimal, four_hourly_minimal, empty_5m, daily_uptrend_500
    ):
        """ZARATTINI_LOOKBACKS has 9 entries; confirmed exposed in indicators."""
        decision = evaluate_signal(
            df1h=hourly_minimal,
            df4h=four_hourly_minimal,
            df5m=empty_5m,
            df1d=daily_uptrend_500,
            symbol="BTCUSDT",
            cfg={"regime_allocation_enabled": True},
            regime={"regime": "BULL"},
        )
        assert decision.indicators["regime_allocation_n_lookbacks"] == 9

    def test_long_short_flat_sum_equals_n_lookbacks(
        self, hourly_minimal, four_hourly_minimal, empty_5m, daily_uptrend_500
    ):
        """Sanity: n_long + n_short + n_flat = n_lookbacks."""
        decision = evaluate_signal(
            df1h=hourly_minimal,
            df4h=four_hourly_minimal,
            df5m=empty_5m,
            df1d=daily_uptrend_500,
            symbol="BTCUSDT",
            cfg={"regime_allocation_enabled": True},
            regime={"regime": "BULL"},
        )
        ind = decision.indicators
        total = ind["regime_allocation_n_long"] + ind["regime_allocation_n_short"] + ind["regime_allocation_n_flat"]
        assert total == ind["regime_allocation_n_lookbacks"]

    def test_realized_vol_is_finite_when_history_sufficient(
        self, hourly_minimal, four_hourly_minimal, empty_5m, daily_uptrend_500
    ):
        """500 days of returns → realized_vol must be finite."""
        decision = evaluate_signal(
            df1h=hourly_minimal,
            df4h=four_hourly_minimal,
            df5m=empty_5m,
            df1d=daily_uptrend_500,
            symbol="BTCUSDT",
            cfg={"regime_allocation_enabled": True},
            regime={"regime": "BULL"},
        )
        vol = decision.indicators["realized_vol_annualized_30d"]
        assert np.isfinite(vol)
        assert vol > 0.0


# ---------------------------------------------------------------------------
# Estado / reasons
# ---------------------------------------------------------------------------


class TestEstadoAndReasons:
    def test_estado_describes_decision(
        self, hourly_minimal, four_hourly_minimal, empty_5m, daily_uptrend_500
    ):
        decision = evaluate_signal(
            df1h=hourly_minimal,
            df4h=four_hourly_minimal,
            df5m=empty_5m,
            df1d=daily_uptrend_500,
            symbol="BTCUSDT",
            cfg={"regime_allocation_enabled": True},
            regime={"regime": "BULL"},
        )
        assert "regime-allocation:" in decision.estado
        assert "LONG" in decision.estado

    def test_reasons_includes_regime_allocation_enabled(
        self, hourly_minimal, four_hourly_minimal, empty_5m, daily_uptrend_500
    ):
        decision = evaluate_signal(
            df1h=hourly_minimal,
            df4h=four_hourly_minimal,
            df5m=empty_5m,
            df1d=daily_uptrend_500,
            symbol="BTCUSDT",
            cfg={"regime_allocation_enabled": True},
            regime={"regime": "BULL"},
        )
        assert decision.reasons.get("regime_allocation_enabled") is True
