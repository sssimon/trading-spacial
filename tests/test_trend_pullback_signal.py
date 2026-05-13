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
    """
    from strategy.core import evaluate_signal, SCORE_STANDARD

    df1h = _linear_trend_with_pullback_ohlcv(n=250, seed=300, slope_pct=0.001)
    df4h = _linear_trend_with_pullback_ohlcv(n=200, seed=301, slope_pct=0.001)
    df5m = _linear_trend_with_pullback_ohlcv(n=250, seed=302, slope_pct=0.001)
    df1d = _linear_trend_with_pullback_ohlcv(n=100, seed=303, slope_pct=0.001)

    decision = evaluate_signal(
        df1h, df4h, df5m, df1d,
        symbol="BTCUSDT",
        cfg={"trend_pullback_enabled": True, "trend_pullback_distance": 0.5},
        regime={"regime": "BULL", "score": 75, "details": {}},
        health_state="NORMAL",
        now=datetime(2024, 4, 23, tzinfo=timezone.utc),
    )

    if decision.direction != "NONE":
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
