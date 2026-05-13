"""Tests for strategy.donchian_ensemble (epic #338 Phase 1A, closes #342 in part).

Covers Donchian channel computation, sticky breakout direction over history,
equal-weight vote aggregation, and the convenience `compute_ensemble_history`
wrapper. All TDD-developed.
"""
import numpy as np
import pandas as pd
import pytest

from strategy.donchian_ensemble import (
    ZARATTINI_LOOKBACKS,
    aggregate_ensemble,
    compute_donchian_channel,
    compute_donchian_direction_history,
    compute_ensemble_history,
)


@pytest.fixture
def daily_index_60():
    """60 consecutive daily timestamps."""
    return pd.date_range("2024-01-01", periods=60, freq="D")


@pytest.fixture
def constant_price_60(daily_index_60):
    """Constant price = 100.0 over 60 days. No breakouts possible."""
    return pd.DataFrame(
        {
            "close": [100.0] * 60,
            "high": [100.0] * 60,
            "low": [100.0] * 60,
        },
        index=daily_index_60,
    )


@pytest.fixture
def linear_uptrend_60(daily_index_60):
    """Linear uptrend 100 → 159 over 60 days. Every bar makes new high."""
    closes = np.arange(100.0, 160.0)
    return pd.DataFrame(
        {"close": closes, "high": closes + 0.5, "low": closes - 0.5},
        index=daily_index_60,
    )


# ---------------------------------------------------------------------------
# compute_donchian_channel — pure point-in-time channel
# ---------------------------------------------------------------------------


class TestComputeDonchianChannel:
    def test_basic_5_day_channel(self):
        """5-bar window: upper = max highs, lower = min lows, mid = avg."""
        highs = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        lows = pd.Series([0.5, 1.5, 2.5, 3.5, 4.5])
        ch = compute_donchian_channel(highs=highs, lows=lows, lookback_days=5)
        assert ch["upper"] == 5.0
        assert ch["lower"] == 0.5
        assert ch["mid"] == pytest.approx(2.75)

    def test_uses_only_last_n_bars(self):
        """Earlier bars outside the window must NOT contribute."""
        # Last 3 bars should give upper=8, lower=6
        highs = pd.Series([100.0, 100.0, 5.0, 8.0, 7.0])
        lows = pd.Series([90.0, 90.0, 4.0, 7.0, 6.0])
        ch = compute_donchian_channel(highs=highs, lows=lows, lookback_days=3)
        assert ch["upper"] == 8.0
        assert ch["lower"] == 4.0

    def test_insufficient_history_returns_nan(self):
        """Fewer bars than lookback → all NaN."""
        highs = pd.Series([1.0, 2.0])
        lows = pd.Series([0.5, 1.5])
        ch = compute_donchian_channel(highs=highs, lows=lows, lookback_days=5)
        assert np.isnan(ch["upper"])
        assert np.isnan(ch["lower"])
        assert np.isnan(ch["mid"])

    def test_exactly_n_bars_works(self):
        """Exactly lookback bars: edge case should compute (not NaN)."""
        highs = pd.Series([1.0, 2.0, 3.0])
        lows = pd.Series([0.5, 1.5, 2.5])
        ch = compute_donchian_channel(highs=highs, lows=lows, lookback_days=3)
        assert ch["upper"] == 3.0
        assert ch["lower"] == 0.5

    def test_lookback_lt_2_raises(self):
        """Sanity: a 1-bar Donchian is just the bar itself; reject as invalid."""
        highs = pd.Series([1.0, 2.0])
        lows = pd.Series([0.5, 1.5])
        with pytest.raises(ValueError, match="lookback_days must be"):
            compute_donchian_channel(highs=highs, lows=lows, lookback_days=1)


# ---------------------------------------------------------------------------
# compute_donchian_direction_history — sticky breakout direction
# ---------------------------------------------------------------------------


class TestComputeDonchianDirectionHistory:
    def test_warmup_returns_zeros(self, daily_index_60):
        """First lookback_days bars are warmup: direction = 0."""
        closes = pd.Series(np.arange(100.0, 160.0), index=daily_index_60)
        highs = closes + 0.5
        lows = closes - 0.5
        dirs = compute_donchian_direction_history(
            closes=closes, highs=highs, lows=lows, lookback_days=5
        )
        # Warmup is the first 5 bars (need lookback_days of history to compute
        # prior N-day extremes, which then need to be shifted by 1 → first 5
        # are NaN in the rolling-shifted-extremes → direction = 0)
        assert all(dirs.iloc[:5] == 0)

    def test_uptrend_eventually_signals_long_throughout(self, linear_uptrend_60):
        """Linear uptrend: after warmup, direction should be LONG (+1) every bar
        because each bar breaks the prior N-day high."""
        df = linear_uptrend_60
        dirs = compute_donchian_direction_history(
            closes=df["close"], highs=df["high"], lows=df["low"], lookback_days=5
        )
        # After warmup (first 5 bars), the uptrend should produce all +1
        post_warmup = dirs.iloc[5:]
        assert (post_warmup == 1).all(), (
            f"uptrend should yield all LONG post-warmup; got {post_warmup.value_counts()}"
        )

    def test_downtrend_signals_short(self, daily_index_60):
        """Linear downtrend produces all SHORT post-warmup."""
        closes = pd.Series(np.arange(160.0, 100.0, -1.0), index=daily_index_60)
        highs = closes + 0.5
        lows = closes - 0.5
        dirs = compute_donchian_direction_history(
            closes=closes, highs=highs, lows=lows, lookback_days=5
        )
        post_warmup = dirs.iloc[5:]
        assert (post_warmup == -1).all()

    def test_constant_price_no_signals(self, constant_price_60):
        """Constant price never breaks any range → direction always 0."""
        df = constant_price_60
        dirs = compute_donchian_direction_history(
            closes=df["close"], highs=df["high"], lows=df["low"], lookback_days=5
        )
        assert (dirs == 0).all()

    def test_sticky_long_through_pullback(self, daily_index_60):
        """Once LONG, direction holds through pullbacks until SHORT breakout fires."""
        # Up for 20 bars to 120, then sideways at 115-119 (pullback that doesn't break lower)
        closes_up = np.linspace(100, 120, 20)
        closes_pullback = np.full(20, 117.0)  # pulls back but well above prior low
        closes_total = np.concatenate([closes_up, closes_pullback, np.full(20, 117.0)])
        highs = closes_total + 0.5
        lows = closes_total - 0.5
        df = pd.DataFrame(
            {"close": closes_total, "high": highs, "low": lows}, index=daily_index_60
        )
        dirs = compute_donchian_direction_history(
            closes=df["close"], highs=df["high"], lows=df["low"], lookback_days=5
        )
        # After uptrend establishes LONG (~bar 6), direction should remain +1
        # through the pullback (since 117 doesn't break the prior 5-day low)
        # Final direction should be +1
        assert dirs.iloc[-1] == 1, f"Expected sticky LONG; got {dirs.iloc[-1]}"

    def test_flip_long_to_short_on_breakout_down(self, daily_index_60):
        """Long established, then sharp drop below prior N-day low → flip to SHORT."""
        # 30 bars uptrend, then 30 bars sharp downtrend below prior lows
        closes_up = np.linspace(100, 150, 30)
        closes_down = np.linspace(149, 80, 30)
        closes_total = np.concatenate([closes_up, closes_down])
        highs = closes_total + 0.5
        lows = closes_total - 0.5
        df = pd.DataFrame(
            {"close": closes_total, "high": highs, "low": lows}, index=daily_index_60
        )
        dirs = compute_donchian_direction_history(
            closes=df["close"], highs=df["high"], lows=df["low"], lookback_days=5
        )
        # By the end of the downtrend, direction should be SHORT
        assert dirs.iloc[-1] == -1

    def test_lookback_lt_2_raises(self, daily_index_60):
        """Bad lookback raises."""
        closes = pd.Series(np.arange(60.0), index=daily_index_60)
        highs = closes + 0.5
        lows = closes - 0.5
        with pytest.raises(ValueError, match="lookback_days must be"):
            compute_donchian_direction_history(
                closes=closes, highs=highs, lows=lows, lookback_days=1
            )

    def test_mismatched_index_raises(self, daily_index_60):
        """Misaligned indexes are caught — protects against silent bugs."""
        closes = pd.Series(np.arange(60.0), index=daily_index_60)
        # Different index
        bad_index = pd.date_range("2025-01-01", periods=60, freq="D")
        highs = pd.Series(np.arange(60.0), index=bad_index)
        lows = pd.Series(np.arange(60.0), index=daily_index_60)
        with pytest.raises(ValueError, match="same index"):
            compute_donchian_direction_history(
                closes=closes, highs=highs, lows=lows, lookback_days=5
            )

    def test_insufficient_data_returns_zero_series(self, daily_index_60):
        """Series with fewer than lookback_days bars → all zeros."""
        idx = daily_index_60[:3]  # only 3 bars
        closes = pd.Series([100.0, 101.0, 102.0], index=idx)
        highs = closes + 0.5
        lows = closes - 0.5
        dirs = compute_donchian_direction_history(
            closes=closes, highs=highs, lows=lows, lookback_days=5
        )
        assert (dirs == 0).all()
        assert len(dirs) == 3


# ---------------------------------------------------------------------------
# aggregate_ensemble — equal-weight vote
# ---------------------------------------------------------------------------


class TestAggregateEnsemble:
    def test_all_long_returns_long_with_full_confidence(self):
        """9 LONG signals → direction=+1, confidence=1.0."""
        directions = {n: 1 for n in (5, 10, 20, 30, 60, 90, 150, 250, 360)}
        result = aggregate_ensemble(directions)
        assert result["direction"] == 1
        assert result["vote"] == 9
        assert result["confidence"] == pytest.approx(1.0)
        assert result["n_long"] == 9
        assert result["n_short"] == 0
        assert result["n_flat"] == 0
        assert result["n_lookbacks"] == 9

    def test_all_short_returns_short_with_full_confidence(self):
        directions = {n: -1 for n in (5, 10, 20, 30, 60, 90, 150, 250, 360)}
        result = aggregate_ensemble(directions)
        assert result["direction"] == -1
        assert result["vote"] == -9
        assert result["confidence"] == pytest.approx(1.0)
        assert result["n_short"] == 9

    def test_all_flat_returns_flat_zero_confidence(self):
        directions = {n: 0 for n in (5, 10, 20, 30, 60, 90, 150, 250, 360)}
        result = aggregate_ensemble(directions)
        assert result["direction"] == 0
        assert result["vote"] == 0
        assert result["confidence"] == 0.0
        assert result["n_flat"] == 9

    def test_majority_long_wins_when_5_vs_4(self):
        """5 LONG + 4 SHORT → vote=+1, direction=+1, confidence=1/9."""
        directions = {5: 1, 10: 1, 20: 1, 30: 1, 60: 1, 90: -1, 150: -1, 250: -1, 360: -1}
        result = aggregate_ensemble(directions)
        assert result["direction"] == 1
        assert result["vote"] == 1
        assert result["confidence"] == pytest.approx(1 / 9, abs=1e-6)
        assert result["n_long"] == 5
        assert result["n_short"] == 4

    def test_tied_vote_returns_flat(self):
        """3 LONG + 3 SHORT + 3 FLAT → vote=0 → flat."""
        directions = {5: 1, 10: 1, 20: 1, 30: -1, 60: -1, 90: -1, 150: 0, 250: 0, 360: 0}
        result = aggregate_ensemble(directions)
        assert result["direction"] == 0
        assert result["vote"] == 0
        assert result["confidence"] == 0.0
        assert result["n_long"] == 3
        assert result["n_short"] == 3
        assert result["n_flat"] == 3

    def test_partial_lookback_set_works(self):
        """Aggregation works with fewer lookbacks (e.g. 4 from a subset config)."""
        directions = {10: 1, 30: 1, 90: -1, 180: 1}
        result = aggregate_ensemble(directions)
        assert result["direction"] == 1
        assert result["vote"] == 2
        assert result["confidence"] == pytest.approx(0.5)
        assert result["n_lookbacks"] == 4

    def test_empty_directions_raises(self):
        with pytest.raises(ValueError, match="empty"):
            aggregate_ensemble({})

    def test_invalid_direction_value_raises(self):
        """Direction outside {-1, 0, +1} is rejected — anti-bug guard."""
        with pytest.raises(ValueError, match="must be in"):
            aggregate_ensemble({5: 2})  # noqa

    def test_invalid_direction_float_raises(self):
        with pytest.raises(ValueError):
            aggregate_ensemble({5: 0.5})  # noqa: float not in {-1,0,1}

    def test_unknown_aggregation_method_raises(self):
        """Future method names should raise NotImplementedError."""
        with pytest.raises(NotImplementedError, match="not implemented"):
            aggregate_ensemble({5: 1}, method="signal_strength_weighted")  # noqa


# ---------------------------------------------------------------------------
# compute_ensemble_history — convenience wrapper
# ---------------------------------------------------------------------------


class TestComputeEnsembleHistory:
    def test_returns_dataframe_with_per_lookback_columns(self, linear_uptrend_60):
        """Output has dir_N columns + ensemble aggregates."""
        df = linear_uptrend_60
        out = compute_ensemble_history(
            closes=df["close"], highs=df["high"], lows=df["low"],
            lookbacks=(5, 10, 20),
        )
        assert "dir_5" in out.columns
        assert "dir_10" in out.columns
        assert "dir_20" in out.columns
        assert "vote" in out.columns
        assert "direction" in out.columns
        assert "confidence" in out.columns
        assert "n_long" in out.columns
        assert len(out) == 60

    def test_uptrend_terminal_direction_is_long(self, linear_uptrend_60):
        """In a clean uptrend, by the end the ensemble votes LONG with full confidence."""
        df = linear_uptrend_60
        out = compute_ensemble_history(
            closes=df["close"], highs=df["high"], lows=df["low"],
            lookbacks=(5, 10, 20),
        )
        assert out["direction"].iloc[-1] == 1
        # In a perfect uptrend, all 3 lookbacks vote LONG → vote = 3, confidence = 1.0
        assert out["vote"].iloc[-1] == 3
        assert out["confidence"].iloc[-1] == pytest.approx(1.0)

    def test_zarattini_default_lookbacks_used(self, linear_uptrend_60):
        """If lookbacks tuple not specified, defaults to ZARATTINI_LOOKBACKS."""
        df = linear_uptrend_60
        out = compute_ensemble_history(
            closes=df["close"], highs=df["high"], lows=df["low"],
        )
        for n in ZARATTINI_LOOKBACKS:
            assert f"dir_{n}" in out.columns
        # 360-day lookback won't have signal in 60-bar data (still warming up)
        assert out["dir_360"].iloc[-1] == 0

    def test_empty_lookbacks_raises(self, linear_uptrend_60):
        df = linear_uptrend_60
        with pytest.raises(ValueError, match="empty"):
            compute_ensemble_history(
                closes=df["close"], highs=df["high"], lows=df["low"],
                lookbacks=(),
            )


# ---------------------------------------------------------------------------
# ZARATTINI_LOOKBACKS constant
# ---------------------------------------------------------------------------


class TestZarattiniLookbacks:
    def test_exact_9_lookbacks_locked(self):
        """§8.4 of epic #338 spec locked 9 lookbacks. This test guards drift."""
        assert ZARATTINI_LOOKBACKS == (5, 10, 20, 30, 60, 90, 150, 250, 360)
        assert len(ZARATTINI_LOOKBACKS) == 9

    def test_lookbacks_monotonically_increasing(self):
        """Sanity: lookbacks should be ordered for legibility."""
        assert list(ZARATTINI_LOOKBACKS) == sorted(ZARATTINI_LOOKBACKS)
