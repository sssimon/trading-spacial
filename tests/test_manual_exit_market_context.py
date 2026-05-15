"""Tests for tools/manual_exit_market_context.py.

Synthetic fixtures only — NO papá's signals.db.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tools.manual_exit_market_context import (
    CURATED_10,
    EXIT_QUALITY_THRESHOLD_PCT,
    _safe_float,
    compute_atr,
    compute_h1_bar_pattern,
    compute_h2_local_extremum,
    compute_h3_momentum,
    compute_h4_volatility,
    compute_h6_post_exit,
    find_exit_bar_index,
)


def _bar(open_time_ms: int, o: float, h: float, l: float, c: float) -> dict:
    return {"open_time": open_time_ms, "open": o, "high": h, "low": l, "close": c}


class TestSafeFloat:
    def test_nan_returns_none(self):
        assert _safe_float(float("nan")) is None

    def test_inf_returns_none(self):
        assert _safe_float(float("inf")) is None

    def test_normal_passes_through(self):
        assert _safe_float(1.5) == 1.5

    def test_none_returns_none(self):
        assert _safe_float(None) is None


class TestExitBarIndex:
    def test_find_bar_covering_ts(self):
        bars = [_bar(1000, 1, 1, 1, 1), _bar(3600 * 1000 + 1000, 1, 1, 1, 1)]
        # exit_ts within first bar's hour
        target = datetime.fromtimestamp(1.5, tz=timezone.utc)  # 1500ms
        assert find_exit_bar_index(bars, target) == 0


class TestATR:
    def test_atr_insufficient_history(self):
        bars = [_bar(i * 3600_000, 100, 101, 99, 100) for i in range(5)]
        assert compute_atr(bars, end_idx=3, period=14) is None

    def test_atr_constant_range(self):
        # 16 bars, constant TR of 2 (h-l = 2, prev close = same as l).
        # Wait — TR = max(h-l, |h-prev_close|, |l-prev_close|).
        # If all bars are 100/102/100/101 (o/h/l/c), h-l = 2, and h-prev_close
        # could be larger if prev_close differs. Use stable identical bars:
        bars = [_bar(i * 3600_000, 100, 102, 100, 101) for i in range(20)]
        atr = compute_atr(bars, end_idx=15, period=14)
        # TR for bar i: max(2, |102-101_prev|, |100-101_prev|) = max(2, 1, 1) = 2
        assert atr == pytest.approx(2.0)


class TestH1BarPattern:
    def test_green_bar_long_favorable(self):
        bar = _bar(0, 100, 105, 99, 103)  # open 100, close 103, green
        out = compute_h1_bar_pattern(bar, atr=2.0, direction="LONG")
        assert out["exit_bar_color"] == "green"
        assert out["color_relative_to_direction"] == "favorable"
        assert out["exit_bar_close_position"] == pytest.approx((103 - 99) / (105 - 99))
        assert out["exit_bar_range_atr_ratio"] == pytest.approx(6 / 2.0)

    def test_red_bar_long_adverse(self):
        bar = _bar(0, 100, 102, 98, 99)  # open 100, close 99, red
        out = compute_h1_bar_pattern(bar, atr=None, direction="LONG")
        assert out["exit_bar_color"] == "red"
        assert out["color_relative_to_direction"] == "adverse"
        assert out["exit_bar_range_atr_ratio"] is None

    def test_red_bar_short_favorable(self):
        bar = _bar(0, 100, 101, 95, 96)  # red, SHORT favorable
        out = compute_h1_bar_pattern(bar, atr=2.0, direction="SHORT")
        assert out["exit_bar_color"] == "red"
        assert out["color_relative_to_direction"] == "favorable"


class TestH2LocalExtremum:
    def test_long_dist_from_5bar_high(self):
        # 5 prior bars with max high 110
        bars = [_bar(i * 3600_000, 100, 100 + i, 99 + i, 100 + i) for i in range(6)]
        # exit bar high < rolling high
        exit_price = 102.0
        # rolling_high over bars[0:5] = max(100, 101, 102, 103, 104) = 104
        out = compute_h2_local_extremum(bars, exit_idx=5, exit_price=exit_price, direction="LONG")
        assert out["dist_from_local_extremum_pct"] == pytest.approx((104 - 102) / 102 * 100)
        # bar 5 has high = 105 > 104 → new local high
        assert out["is_new_local_extremum"] is True


class TestH3Momentum:
    def test_long_momentum_positive(self):
        bars = [_bar(i * 3600_000, 100, 101, 99, 100 + i) for i in range(5)]
        # closes: 100, 101, 102, 103, 104
        out = compute_h3_momentum(bars, exit_idx=4, direction="LONG")
        # last_3bar_momentum: (104 - 101) / 101 * 100 ≈ 2.97
        assert out["last_3bar_momentum_pct"] == pytest.approx((104 - 101) / 101 * 100)

    def test_short_momentum_sign_flipped(self):
        # Prices going up = adverse for SHORT
        bars = [_bar(i * 3600_000, 100, 101, 99, 100 + i) for i in range(5)]
        out = compute_h3_momentum(bars, exit_idx=4, direction="SHORT")
        # For SHORT, momentum should be NEGATIVE (price went up = bad for short)
        assert out["last_3bar_momentum_pct"] < 0


class TestH4Volatility:
    def test_atr_normalized_long_winning(self):
        # Entry 100, exit 105, ATR=2 → move = +5, atr_norm = +2.5
        out = compute_h4_volatility(100.0, 105.0, atr_entry=2.0, direction="LONG")
        assert out["move_from_entry_atr_normalized"] == pytest.approx(2.5)

    def test_atr_normalized_short_winning(self):
        # SHORT entry 100, exit 95, ATR=2 → favorable = +5, atr_norm = +2.5
        out = compute_h4_volatility(100.0, 95.0, atr_entry=2.0, direction="SHORT")
        assert out["move_from_entry_atr_normalized"] == pytest.approx(2.5)

    def test_atr_none_returns_none(self):
        out = compute_h4_volatility(100.0, 105.0, atr_entry=None, direction="LONG")
        assert out["move_from_entry_atr_normalized"] is None


class TestH6PostExitHindsight:
    def test_long_premature_classification(self):
        # Exit at 100, forward 4 bars go up to 105 (+5% favorable)
        bars = [_bar(i * 3600_000, 100, 101 + i, 100 + i, 100 + i) for i in range(5)]
        exit_dt = datetime.fromtimestamp(0, tz=timezone.utc)
        out = compute_h6_post_exit(bars, exit_dt, exit_price=100.0, direction="LONG")
        # post_exit_4h_favorable should be ~5% → PREMATURE
        assert out["post_exit_4h_favorable_pct"] >= 1.0
        assert out["exit_quality"] == "PREMATURE"

    def test_long_good_classification(self):
        # Exit at 100, forward bars stay flat (no significant movement)
        bars = [_bar(i * 3600_000, 100, 100.1, 99.9, 100) for i in range(5)]
        exit_dt = datetime.fromtimestamp(0, tz=timezone.utc)
        out = compute_h6_post_exit(bars, exit_dt, exit_price=100.0, direction="LONG")
        # < 1% in both directions → GOOD
        assert out["post_exit_4h_favorable_pct"] < 1.0
        assert out["exit_quality"] == "GOOD"

    def test_long_reversal_caught(self):
        # Exit at 100, forward bars drop (favorable for SHORT, adverse for LONG)
        # Adverse for LONG = price drops; if drops > 1% → REVERSAL_CAUGHT
        bars = [_bar(i * 3600_000, 100, 100, 95, 96) for i in range(5)]
        exit_dt = datetime.fromtimestamp(0, tz=timezone.utc)
        out = compute_h6_post_exit(bars, exit_dt, exit_price=100.0, direction="LONG")
        # post_exit_4h_favorable should be small (~0%), adverse should be ~5%
        assert out["post_exit_4h_favorable_pct"] < 1.0
        assert out["post_exit_4h_adverse_pct"] >= 1.0
        assert out["exit_quality"] == "REVERSAL_CAUGHT"


class TestCurated10Constants:
    def test_size(self):
        assert len(CURATED_10) == 10

    def test_known_members(self):
        for sym in ("BTCUSDT", "ETHUSDT", "RUNEUSDT"):
            assert sym in CURATED_10


class TestQualityThresholdLocked:
    def test_quality_threshold_1pct(self):
        assert EXIT_QUALITY_THRESHOLD_PCT == 1.0
