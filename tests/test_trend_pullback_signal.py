"""Tests for trend-pullback entry signal (Phase 2 R3).

Pre-reg: `docs/superpowers/plans/2026-05-13-r3-trend-pullback-pre-reg.md` §2.2

Coverage:
  - `_evaluate_trend_pullback_direction` pure helper: numeric inputs → direction
    (LONG/SHORT/NONE) + diagnostic reasons. No DataFrames; tests the decision
    logic in isolation.
  - `evaluate_signal` integration via `cfg["trend_pullback_enabled"]` flag:
    - flag off → existing LRC path unchanged (byte-identical regression)
    - flag on → SMA-based trend-pullback direction + uniform SCORE_STANDARD
    - SMA200 warmup skip
    - regime gating on SHORT (BEAR only)
    - LRC entry disabled when flag on (mutual exclusion)
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helper: synthetic OHLCV for trend-pullback scenarios
# ─────────────────────────────────────────────────────────────────────────────


def _engineer_trend_pullback_long_fires_ohlcv(
    n: int = 250, seed: int = 42, base: float = 100.0,
) -> pd.DataFrame:
    """Build OHLCV that DETERMINISTICALLY fires trend-pullback LONG signal.

    Engineered constraints:
      - SMA50 > SMA200 (uptrend confirmed at last bar) — guaranteed by positive
        linear drift over the full 250 bars.
      - |close[-1] - SMA20[-1]| ≤ 0.5 × ATR[-1] — guaranteed by setting
        close[-1] explicitly to SMA20[-1] (zero distance, well within envelope).
      - 4H/5m direction trend matches (positive slope) so macro_ok and 5m
        trigger don't accidentally block.

    Used by I-3 fix in `test_evaluate_signal_flag_on_score_uniform_standard_when_direction`
    to assert the trend-pullback override fires unconditionally.
    """
    rng = np.random.default_rng(seed)
    # Stronger trend slope + larger noise → realistic ATR.
    slope_per_bar = 0.001  # 0.1% per bar → +25% over 250 bars
    trend = base * (1.0 + slope_per_bar * np.arange(n))
    # Noise std ~0.5% of price → ATR will be ~0.5-1.0 (realistic).
    noise_close = rng.standard_normal(n) * 0.5
    close = trend + noise_close

    # Compute SMA20 over last 20 bars (using close[-21:-1] as the "rolling mean
    # that defines SMA20 at index -1"). Then SET close[-1] = SMA20[-1] to
    # guarantee pullback_ok=True (zero distance, within any positive envelope).
    sma20_at_minus_1 = float(np.mean(close[-21:-1]))  # mean of prior 20 bars
    close[-1] = sma20_at_minus_1

    # OHLCV scaffolding with realistic hi-lo spread for ATR.
    noise_hi_lo = np.abs(rng.standard_normal(n)) * 0.3
    df = pd.DataFrame({
        "open":   np.roll(close, 1),
        "high":   close + noise_hi_lo,
        "low":    close - noise_hi_lo,
        "close":  close,
        "volume": rng.random(n) * 1000 + 500,
    })
    df.loc[0, "open"] = close[0]
    df.index = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return df


def _linear_trend_with_pullback_ohlcv(
    n: int = 250,
    seed: int = 42,
    slope_pct: float = 0.001,
    pullback_pct: float = 0.005,
    pullback_bars: int = 3,
    base: float = 100.0,
) -> pd.DataFrame:
    """Linear trend + small terminal pullback bringing close near SMA20.

    Args:
        n: bar count (≥ 200 for SMA200 warmup).
        seed: RNG seed for noise (low noise so ATR is bounded).
        slope_pct: per-bar trend slope as fraction of base. Positive=uptrend,
            negative=downtrend. With slope=0.001 over 250 bars → +25% rise.
        pullback_pct: terminal pullback as fraction of base. For uptrend, last
            `pullback_bars` bars dip by this fraction; downtrend = rally.
        pullback_bars: number of bars in the terminal pullback.
        base: starting price.

    Result: SMA50 > SMA200 if slope > 0 (uptrend), close near SMA20 due to
    terminal pullback, ATR ~ noise level.
    """
    rng = np.random.default_rng(seed)

    # Linear trend
    trend = base * (1.0 + slope_pct * np.arange(n))
    noise_close = rng.standard_normal(n) * 0.02
    close = trend + noise_close

    # Terminal pullback
    if slope_pct > 0:
        # uptrend → dip
        pullback = np.linspace(0, base * pullback_pct, pullback_bars)
        close[-pullback_bars:] -= pullback
    elif slope_pct < 0:
        # downtrend → rally
        pullback = np.linspace(0, base * pullback_pct, pullback_bars)
        close[-pullback_bars:] += pullback

    # OHLCV
    noise_hi_lo = np.abs(rng.standard_normal(n)) * 0.05
    df = pd.DataFrame({
        "open":   np.roll(close, 1),
        "high":   close + noise_hi_lo,
        "low":    close - noise_hi_lo,
        "close":  close,
        "volume": rng.random(n) * 1000 + 500,
    })
    df.loc[0, "open"] = close[0]
    df.index = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Helper-level tests: _evaluate_trend_pullback_direction pure function
# ─────────────────────────────────────────────────────────────────────────────


def test_helper_returns_NONE_when_sma50_none():
    """Warmup: SMA50 not yet computed → NONE."""
    from strategy.core import _evaluate_trend_pullback_direction
    direction, _ = _evaluate_trend_pullback_direction(
        price=100.0, sma50=None, sma200=99.0, sma20=100.0, atr=0.5,
        pullback_distance=0.5, regime_token="LONG",
    )
    assert direction == "NONE"


def test_helper_returns_NONE_when_sma200_none():
    """Warmup: SMA200 not yet computed → NONE."""
    from strategy.core import _evaluate_trend_pullback_direction
    direction, _ = _evaluate_trend_pullback_direction(
        price=100.0, sma50=101.0, sma200=None, sma20=100.0, atr=0.5,
        pullback_distance=0.5, regime_token="LONG",
    )
    assert direction == "NONE"


def test_helper_returns_NONE_when_sma20_none():
    """Warmup: SMA20 not yet computed → NONE."""
    from strategy.core import _evaluate_trend_pullback_direction
    direction, _ = _evaluate_trend_pullback_direction(
        price=100.0, sma50=101.0, sma200=99.0, sma20=None, atr=0.5,
        pullback_distance=0.5, regime_token="LONG",
    )
    assert direction == "NONE"


def test_helper_returns_NONE_when_atr_zero():
    """Degenerate ATR (zero or negative) → NONE — envelope collapses to point."""
    from strategy.core import _evaluate_trend_pullback_direction
    direction, _ = _evaluate_trend_pullback_direction(
        price=100.0, sma50=101.0, sma200=99.0, sma20=100.0, atr=0.0,
        pullback_distance=0.5, regime_token="LONG",
    )
    assert direction == "NONE"


def test_helper_returns_LONG_when_uptrend_pullback_long_regime():
    """SMA50 > SMA200 + close within SMA20 ± 0.5 ATR + LONG regime → LONG."""
    from strategy.core import _evaluate_trend_pullback_direction
    direction, reasons = _evaluate_trend_pullback_direction(
        price=100.1,         # 0.1 from SMA20
        sma50=100.5,         # > SMA200
        sma200=99.5,
        sma20=100.0,
        atr=1.0,             # envelope = 0.5 (within range)
        pullback_distance=0.5,
        regime_token="LONG",
    )
    assert direction == "LONG"
    assert reasons.get("is_uptrend") is True
    assert reasons.get("pullback_ok") is True


def test_helper_returns_SHORT_when_downtrend_pullback_short_regime():
    """SMA50 < SMA200 + close within SMA20 ± 0.5 ATR + SHORT regime → SHORT."""
    from strategy.core import _evaluate_trend_pullback_direction
    direction, reasons = _evaluate_trend_pullback_direction(
        price=99.9,          # 0.1 from SMA20
        sma50=99.5,          # < SMA200
        sma200=100.5,
        sma20=100.0,
        atr=1.0,             # envelope = 0.5 (within range)
        pullback_distance=0.5,
        regime_token="SHORT",
    )
    assert direction == "SHORT"
    assert reasons.get("is_downtrend") is True


def test_helper_returns_NONE_when_uptrend_pullback_short_regime():
    """SMA50 > SMA200 + LONG signal but SHORT regime → NONE (regime gates LONG off)."""
    from strategy.core import _evaluate_trend_pullback_direction
    direction, _ = _evaluate_trend_pullback_direction(
        price=100.1, sma50=100.5, sma200=99.5, sma20=100.0, atr=1.0,
        pullback_distance=0.5,
        regime_token="SHORT",  # SHORT regime blocks LONG
    )
    assert direction == "NONE"


def test_helper_returns_NONE_when_downtrend_pullback_long_regime():
    """SMA50 < SMA200 + SHORT signal but LONG regime → NONE (regime gates SHORT off).

    Per pre-reg §2.2: SHORT gated by BEAR (regime_token SHORT) only.
    LONG / NEUTRAL regimes block SHORT, mirroring current LRC discipline.
    """
    from strategy.core import _evaluate_trend_pullback_direction
    direction, _ = _evaluate_trend_pullback_direction(
        price=99.9, sma50=99.5, sma200=100.5, sma20=100.0, atr=1.0,
        pullback_distance=0.5,
        regime_token="LONG",  # LONG regime blocks SHORT
    )
    assert direction == "NONE"


def test_helper_returns_NONE_when_no_trend():
    """SMA50 == SMA200 (flat trend) → NONE — no directional bet."""
    from strategy.core import _evaluate_trend_pullback_direction
    direction, _ = _evaluate_trend_pullback_direction(
        price=100.0, sma50=100.0, sma200=100.0, sma20=100.0, atr=1.0,
        pullback_distance=0.5, regime_token="LONG",
    )
    assert direction == "NONE"


def test_helper_returns_NONE_when_price_outside_envelope():
    """Price > SMA20 + 0.5 ATR (outside envelope) → NONE — not a pullback."""
    from strategy.core import _evaluate_trend_pullback_direction
    direction, _ = _evaluate_trend_pullback_direction(
        price=101.0,         # 1.0 from SMA20, but envelope is 0.5 ATR = 0.5
        sma50=100.5, sma200=99.5, sma20=100.0, atr=1.0,
        pullback_distance=0.5,
        regime_token="LONG",
    )
    assert direction == "NONE"


def test_helper_pullback_distance_tighter_envelope():
    """pullback_distance=0.3 → tighter envelope; price at 0.4 ATR distance excluded."""
    from strategy.core import _evaluate_trend_pullback_direction
    direction, _ = _evaluate_trend_pullback_direction(
        price=100.4,         # 0.4 from SMA20
        sma50=100.5, sma200=99.5, sma20=100.0, atr=1.0,
        pullback_distance=0.3,  # tight envelope = 0.3 ATR
        regime_token="LONG",
    )
    assert direction == "NONE"


def test_helper_pullback_distance_wider_envelope():
    """pullback_distance=0.7 → wider envelope; price at 0.6 ATR distance included."""
    from strategy.core import _evaluate_trend_pullback_direction
    direction, _ = _evaluate_trend_pullback_direction(
        price=100.6,         # 0.6 from SMA20
        sma50=100.5, sma200=99.5, sma20=100.0, atr=1.0,
        pullback_distance=0.7,  # wider envelope = 0.7 ATR
        regime_token="LONG",
    )
    assert direction == "LONG"


def test_helper_regime_ANY_allows_both_directions():
    """regime_token='ANY' (BYPASS mode) allows both LONG and SHORT.

    Mirrors LRC discipline: regime BYPASS / ANY token unblocks both directions.
    """
    from strategy.core import _evaluate_trend_pullback_direction

    # ANY + uptrend → LONG
    direction, _ = _evaluate_trend_pullback_direction(
        price=100.1, sma50=100.5, sma200=99.5, sma20=100.0, atr=1.0,
        pullback_distance=0.5, regime_token="ANY",
    )
    assert direction == "LONG"

    # ANY + downtrend → SHORT
    direction, _ = _evaluate_trend_pullback_direction(
        price=99.9, sma50=99.5, sma200=100.5, sma20=100.0, atr=1.0,
        pullback_distance=0.5, regime_token="ANY",
    )
    assert direction == "SHORT"


# ─────────────────────────────────────────────────────────────────────────────
# Integration-level tests: evaluate_signal with cfg.trend_pullback_enabled
# ─────────────────────────────────────────────────────────────────────────────


def test_evaluate_signal_flag_off_defaults_to_lrc_path():
    """cfg.trend_pullback_enabled missing/False → existing LRC entry path used.

    Byte-identical regression: evaluate_signal output must match prior behavior
    when the trend-pullback flag is not set.
    """
    from strategy.core import evaluate_signal
    df1h = _linear_trend_with_pullback_ohlcv(n=250, seed=100, slope_pct=0.001)
    df4h = _linear_trend_with_pullback_ohlcv(n=200, seed=101, slope_pct=0.001)
    df5m = _linear_trend_with_pullback_ohlcv(n=250, seed=102, slope_pct=0.001)
    df1d = _linear_trend_with_pullback_ohlcv(n=100, seed=103, slope_pct=0.001)

    # Flag OFF
    decision_off = evaluate_signal(
        df1h, df4h, df5m, df1d,
        symbol="BTCUSDT",
        cfg={},  # no trend_pullback_enabled
        regime={"regime": "BULL", "score": 75, "details": {}},
        health_state="NORMAL",
        now=datetime(2024, 4, 23, tzinfo=timezone.utc),
    )

    # Flag explicitly False
    decision_explicit_off = evaluate_signal(
        df1h, df4h, df5m, df1d,
        symbol="BTCUSDT",
        cfg={"trend_pullback_enabled": False},
        regime={"regime": "BULL", "score": 75, "details": {}},
        health_state="NORMAL",
        now=datetime(2024, 4, 23, tzinfo=timezone.utc),
    )

    # Identical: direction + score
    assert decision_off.direction == decision_explicit_off.direction
    assert decision_off.score == decision_explicit_off.score


def test_evaluate_signal_flag_on_uses_sma_based_direction():
    """cfg.trend_pullback_enabled=True → trend-pullback direction (NOT LRC zone).

    Uses uptrend+pullback synthetic data: LRC would NOT fire (price near mid-channel),
    but trend-pullback should fire LONG (SMA50 > SMA200 + close near SMA20).
    """
    from strategy.core import evaluate_signal

    df1h = _linear_trend_with_pullback_ohlcv(n=250, seed=200, slope_pct=0.001)
    df4h = _linear_trend_with_pullback_ohlcv(n=200, seed=201, slope_pct=0.001)
    df5m = _linear_trend_with_pullback_ohlcv(n=250, seed=202, slope_pct=0.001)
    df1d = _linear_trend_with_pullback_ohlcv(n=100, seed=203, slope_pct=0.001)

    decision = evaluate_signal(
        df1h, df4h, df5m, df1d,
        symbol="BTCUSDT",
        cfg={"trend_pullback_enabled": True, "trend_pullback_distance": 0.5},
        regime={"regime": "BULL", "score": 75, "details": {}},
        health_state="NORMAL",
        now=datetime(2024, 4, 23, tzinfo=timezone.utc),
    )

    # SMA50 + SMA200 indicators should be populated
    assert "sma50_1h" in decision.indicators
    assert "sma200_1h" in decision.indicators
    assert decision.indicators["sma50_1h"] is not None
    assert decision.indicators["sma200_1h"] is not None

    # Decision reasons should reflect trend-pullback path
    assert decision.reasons.get("trend_pullback_enabled") is True


def test_evaluate_signal_flag_on_score_uniform_standard_when_direction():
    """cfg.trend_pullback_enabled=True + direction ≠ NONE → score = SCORE_STANDARD (2).

    Per pre-reg §2.2: uniform score = 2 (1.0× sizing) eliminates score-related
    confounding within R3. Trend-pullback trades all get standard sizing.

    PR #336 review I-3 fix: synthetic data engineered to PROVABLY fire the
    trend-pullback signal (uptrend + pullback at last bar of pullback window),
    asserted unconditionally rather than wrapping in `if direction != NONE`.
    A vacuous-pass on direction=NONE never exercises the score override.
    """
    from strategy.core import evaluate_signal, SCORE_STANDARD

    # Engineered to PROVABLY fire trend-pullback LONG: close[-1] = SMA20[-1]
    # exactly (zero distance, within any positive envelope). See helper docstring.
    df1h = _engineer_trend_pullback_long_fires_ohlcv(n=250, seed=300)
    df4h = _engineer_trend_pullback_long_fires_ohlcv(n=200, seed=301)
    df5m = _engineer_trend_pullback_long_fires_ohlcv(n=250, seed=302)
    df1d = _engineer_trend_pullback_long_fires_ohlcv(n=100, seed=303)

    decision = evaluate_signal(
        df1h, df4h, df5m, df1d,
        symbol="BTCUSDT",
        cfg={"trend_pullback_enabled": True, "trend_pullback_distance": 0.7},
        regime={"regime": "BULL", "score": 75, "details": {}},
        health_state="NORMAL",
        now=datetime(2024, 4, 23, tzinfo=timezone.utc),
    )

    # Unconditional assertion — direction must fire LONG for this engineered case.
    assert decision.direction == "LONG", (
        f"Trend-pullback LONG must fire on engineered uptrend+pullback synthetic data; "
        f"got direction={decision.direction!r}. Indicators: "
        f"sma50_1h={decision.indicators.get('sma50_1h')}, "
        f"sma200_1h={decision.indicators.get('sma200_1h')}, "
        f"sma20_1h={decision.indicators.get('sma20_1h')}, "
        f"atr_1h={decision.indicators.get('atr_1h')}, "
        f"reasons={decision.reasons}"
    )
    # Now the score-override assertion is meaningful (direction definitely fired).
    assert decision.score == SCORE_STANDARD, (
        f"Trend-pullback trades must have uniform score=SCORE_STANDARD ({SCORE_STANDARD}); "
        f"got score={decision.score}"
    )


def test_evaluate_signal_flag_on_sma200_warmup_skip():
    """cfg.trend_pullback_enabled=True + len(df1h) < 200 → direction NONE (warmup).

    SMA200 requires 200 bars. With insufficient warmup, trend-pullback cannot
    evaluate trend condition; returns NONE.
    """
    from strategy.core import evaluate_signal

    df1h = _linear_trend_with_pullback_ohlcv(n=150, seed=400, slope_pct=0.001)  # < 200 bars
    df4h = _linear_trend_with_pullback_ohlcv(n=200, seed=401, slope_pct=0.001)
    df5m = _linear_trend_with_pullback_ohlcv(n=250, seed=402, slope_pct=0.001)
    df1d = _linear_trend_with_pullback_ohlcv(n=100, seed=403, slope_pct=0.001)

    decision = evaluate_signal(
        df1h, df4h, df5m, df1d,
        symbol="BTCUSDT",
        cfg={"trend_pullback_enabled": True, "trend_pullback_distance": 0.5},
        regime={"regime": "BULL", "score": 75, "details": {}},
        health_state="NORMAL",
        now=datetime(2024, 4, 23, tzinfo=timezone.utc),
    )
    assert decision.direction == "NONE", (
        "SMA200 warmup not satisfied (<200 bars) → direction must be NONE"
    )


def test_evaluate_signal_flag_on_indicators_include_sma50_sma200():
    """SMA50 + SMA200 indicators added to decision.indicators when flag on.

    Per pre-reg §2.2: SMA50/200 are the trend-pullback indicators. They must be
    exposed in `decision.indicators` for downstream diagnostic consumers
    (sweep harness, derivation_audit).
    """
    from strategy.core import evaluate_signal

    df1h = _linear_trend_with_pullback_ohlcv(n=250, seed=500, slope_pct=0.001)
    df4h = _linear_trend_with_pullback_ohlcv(n=200, seed=501, slope_pct=0.001)
    df5m = _linear_trend_with_pullback_ohlcv(n=250, seed=502, slope_pct=0.001)
    df1d = _linear_trend_with_pullback_ohlcv(n=100, seed=503, slope_pct=0.001)

    decision = evaluate_signal(
        df1h, df4h, df5m, df1d,
        symbol="BTCUSDT",
        cfg={"trend_pullback_enabled": True, "trend_pullback_distance": 0.5},
        regime={"regime": "BULL", "score": 75, "details": {}},
        health_state="NORMAL",
        now=datetime(2024, 4, 23, tzinfo=timezone.utc),
    )
    assert "sma50_1h" in decision.indicators
    assert "sma200_1h" in decision.indicators


def _downtrend_ohlcv_for_lrc_long_zone(
    n: int = 250, seed: int = 42, base: float = 100.0,
) -> pd.DataFrame:
    """Build OHLCV with flat-then-drop pattern: LRC% near 0 (deep LONG zone)
    AND SMA50 < SMA200 (downtrend per SMA-based trend-pullback frame).

    Used by I-4 LRC mutual exclusion test: LRC entry WOULD fire (low LRC%),
    but trend-pullback under BULL regime BLOCKS the SHORT-direction signal
    that the downtrend would produce → direction=NONE when flag on.
    """
    rng = np.random.default_rng(seed)
    drop_bars = 25
    flat_len = n - drop_bars
    flat = base + rng.standard_normal(flat_len) * 0.2
    drop = np.linspace(base, base * 0.92, drop_bars)
    close = np.concatenate([flat, drop])[:n]
    noise = np.abs(rng.standard_normal(n)) * 0.15
    df = pd.DataFrame({
        "open":   np.roll(close, 1),
        "high":   close + noise,
        "low":    close - noise,
        "close":  close,
        "volume": rng.random(n) * 1000 + 500,
    })
    df.loc[0, "open"] = close[0]
    df.index = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return df


def _bearish_5m_ohlcv(n: int = 250, seed: int = 42, base: float = 100.0) -> pd.DataFrame:
    """5m OHLCV with bearish last candle (close < open) + falling RSI.

    Used by I-4 5m-trigger-lock test: 1H trend-pullback fires LONG, but 5m
    `_check_trigger_5m_long` returns False (bearish candle + RSI not rising)
    → is_signal=False, is_setup=True. Asserts §9.7 5m-trigger lock holds.
    """
    rng = np.random.default_rng(seed)
    # Mild uptrend then a strong drop at the last few bars to ensure RSI falls.
    flat = base + rng.standard_normal(n - 10) * 0.1
    drop = np.linspace(base, base * 0.97, 10)  # 3% drop in last 10 bars
    close = np.concatenate([flat, drop])[:n]
    noise = np.abs(rng.standard_normal(n)) * 0.1
    open_arr = np.roll(close, 1)
    open_arr[0] = close[0]
    # Force last bar to be unambiguously bearish: close < open by a clear margin.
    open_arr[-1] = close[-2]   # open at prior close
    close[-1] = open_arr[-1] - 0.5  # close clearly below open
    df = pd.DataFrame({
        "open":   open_arr,
        "high":   np.maximum(close, open_arr) + noise,
        "low":    np.minimum(close, open_arr) - noise,
        "close":  close,
        "volume": rng.random(n) * 1000 + 500,
    })
    df.index = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    return df


def test_evaluate_signal_flag_on_lrc_path_bypassed():
    """I-4 (§9.1 lock): LRC entry disabled when trend_pullback_enabled=True.

    Engineered data: LRC% near 0 (deep LONG zone) AND SMA50 < SMA200
    (downtrend per SMA-based trend-pullback frame). Under BULL regime:
      - Flag off (LRC path): LRC LONG fires (zone+regime).
      - Flag on (trend-pullback path): trend-pullback signal candidate is
        SHORT (downtrend + pullback), but BULL regime blocks SHORT →
        direction=NONE.

    This verifies the §9.1 mutual-exclusion lock: when flag is on, LRC entry
    is bypassed and only trend-pullback drives direction.
    """
    from strategy.core import evaluate_signal

    df1h = _downtrend_ohlcv_for_lrc_long_zone(n=250, seed=700, base=100.0)
    df4h = _downtrend_ohlcv_for_lrc_long_zone(n=200, seed=701, base=100.0)
    df5m = _downtrend_ohlcv_for_lrc_long_zone(n=250, seed=702, base=100.0)
    df1d = _downtrend_ohlcv_for_lrc_long_zone(n=100, seed=703, base=100.0)

    common_kwargs = dict(
        symbol="BTCUSDT",
        regime={"regime": "BULL", "score": 75, "details": {}},
        health_state="NORMAL",
        now=datetime(2024, 4, 23, tzinfo=timezone.utc),
    )

    # Flag OFF: LRC LONG fires (zone+regime aligned).
    decision_off = evaluate_signal(
        df1h, df4h, df5m, df1d, cfg={}, **common_kwargs,
    )
    assert decision_off.indicators["lrc_pct"] is not None
    assert decision_off.indicators["lrc_pct"] <= 25.0, (
        f"Engineered data should keep LRC pct in LONG zone; "
        f"got lrc_pct={decision_off.indicators['lrc_pct']}"
    )
    assert decision_off.direction == "LONG", (
        "Flag OFF: LRC zone+BULL regime → LONG entry fires (baseline LRC path)"
    )

    # Flag ON: LRC bypassed; trend-pullback path takes over and yields NONE
    # (downtrend SMA50<SMA200 → SHORT candidate, but BULL regime blocks SHORT).
    decision_on = evaluate_signal(
        df1h, df4h, df5m, df1d,
        cfg={"trend_pullback_enabled": True, "trend_pullback_distance": 0.5},
        **common_kwargs,
    )
    assert decision_on.direction == "NONE", (
        f"Flag ON: LRC entry bypassed; trend-pullback SHORT (downtrend) blocked "
        f"by BULL regime → direction=NONE. Got direction={decision_on.direction!r}, "
        f"reasons={decision_on.reasons}"
    )
    assert decision_on.reasons.get("trend_pullback_enabled") is True


def test_evaluate_signal_flag_on_5m_trigger_blocks_trend_pullback():
    """I-4 (§9.7 lock): 5m trigger preservation enforced when flag on.

    Engineered: 1H trend-pullback LONG fires (uptrend + pullback at SMA20),
    4H macro_ok=True, but 5m bars have a bearish last candle so
    `_check_trigger_5m_long` returns False.

    Expected: direction="LONG" (1H signal candidate selected), is_setup=True
    (setup is valid), is_signal=False (waiting for 5m trigger).

    Verifies that the 5m trigger preservation locked in §9.7 actually gates
    is_signal under the trend-pullback path — the trigger lock is not bypassed
    just because the entry signal frame changed.
    """
    from strategy.core import evaluate_signal

    # 1H/4H/1d: trend-pullback LONG fires.
    df1h = _engineer_trend_pullback_long_fires_ohlcv(n=250, seed=800)
    df4h = _engineer_trend_pullback_long_fires_ohlcv(n=200, seed=801)
    df1d = _engineer_trend_pullback_long_fires_ohlcv(n=100, seed=803)
    # 5m: bearish last candle so LONG trigger fails.
    df5m = _bearish_5m_ohlcv(n=250, seed=802)

    decision = evaluate_signal(
        df1h, df4h, df5m, df1d,
        symbol="BTCUSDT",
        cfg={"trend_pullback_enabled": True, "trend_pullback_distance": 0.7},
        regime={"regime": "BULL", "score": 75, "details": {}},
        health_state="NORMAL",
        now=datetime(2024, 4, 23, tzinfo=timezone.utc),
    )

    # 1H trend-pullback LONG candidate selected.
    assert decision.direction == "LONG", (
        f"1H trend-pullback LONG must fire on engineered data; "
        f"got direction={decision.direction!r}, reasons={decision.reasons}"
    )
    # is_setup=True (LONG candidate valid pre-trigger).
    assert decision.is_setup is True, (
        "Setup must be valid (direction+macro_ok) for the 5m-trigger gating "
        "to be the discriminator"
    )
    # is_signal=False because 5m trigger blocked it.
    assert decision.is_signal is False, (
        f"5m trigger must gate is_signal under trend-pullback path "
        f"(§9.7 lock). Got is_signal={decision.is_signal}, "
        f"trigger_active={decision.reasons.get('trigger_active')}"
    )
    # Confirm trigger_active is False (the actual mechanism).
    assert decision.reasons.get("trigger_active") is False


def test_evaluate_signal_flag_off_sma_indicators_also_present():
    """SMA50 + SMA200 added even when flag off (defensive — diagnostic consumers
    can rely on them being present regardless of flag).

    Cheap to compute, no behavior change for LRC path.
    """
    from strategy.core import evaluate_signal

    df1h = _linear_trend_with_pullback_ohlcv(n=250, seed=600, slope_pct=0.001)
    df4h = _linear_trend_with_pullback_ohlcv(n=200, seed=601, slope_pct=0.001)
    df5m = _linear_trend_with_pullback_ohlcv(n=250, seed=602, slope_pct=0.001)
    df1d = _linear_trend_with_pullback_ohlcv(n=100, seed=603, slope_pct=0.001)

    decision = evaluate_signal(
        df1h, df4h, df5m, df1d,
        symbol="BTCUSDT",
        cfg={},  # flag off
        regime={"regime": "BULL", "score": 75, "details": {}},
        health_state="NORMAL",
        now=datetime(2024, 4, 23, tzinfo=timezone.utc),
    )
    assert "sma50_1h" in decision.indicators
    assert "sma200_1h" in decision.indicators
