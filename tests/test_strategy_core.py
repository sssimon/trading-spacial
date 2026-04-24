"""Tests for strategy.core.evaluate_signal — parity with btc_scanner.scan() (#186 A1)."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest


# ─────────────────────────────────────────────────────────────────────────────
#  Commit A — SignalDecision dataclass tests
# ─────────────────────────────────────────────────────────────────────────────


def test_signal_decision_dataclass_constructs():
    from strategy.core import SignalDecision
    d = SignalDecision()
    assert d.direction == "NONE"
    assert d.score == 0
    assert d.score_label == ""
    assert d.is_signal is False
    assert d.is_setup is False
    assert d.entry_price is None
    assert d.sl_price is None
    assert d.tp_price is None
    assert d.reasons == {}
    assert d.indicators == {}
    assert d.estado == ""


def test_signal_decision_fields_populated():
    from strategy.core import SignalDecision
    d = SignalDecision(
        direction="LONG",
        score=6,
        score_label="PREMIUM",
        is_signal=True,
        entry_price=50_000.0,
        sl_price=49_000.0,
        tp_price=55_000.0,
    )
    assert d.direction == "LONG"
    assert d.is_signal is True
    assert d.entry_price == 50_000.0
    assert d.sl_price == 49_000.0
    assert d.tp_price == 55_000.0


def test_evaluate_signal_stub_returns_decision_on_empty_df():
    """With insufficient data the function should return a NONE decision — not raise."""
    from strategy.core import evaluate_signal
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    decision = evaluate_signal(
        df1h=empty,
        df4h=empty,
        df5m=empty,
        df1d=empty,
        symbol="BTCUSDT",
        cfg={},
        regime={"regime": "NEUTRAL", "score": 50, "details": {}},
        health_state="NORMAL",
        now=datetime(2026, 4, 23, tzinfo=timezone.utc),
    )
    assert decision.direction == "NONE"
    assert decision.is_signal is False


# ─────────────────────────────────────────────────────────────────────────────
#  Commit B — indicators computation
# ─────────────────────────────────────────────────────────────────────────────


def _synth_ohlcv(n: int = 250, seed: int = 42, base: float = 100.0) -> pd.DataFrame:
    """Build a synthetic OHLCV DataFrame with a DatetimeIndex.

    Uses a simple random walk so indicators (LRC, RSI, BB, ATR, ADX) produce
    non-degenerate values.
    """
    rng = np.random.default_rng(seed)
    close = base + np.cumsum(rng.standard_normal(n) * 0.5)
    noise = np.abs(rng.standard_normal(n)) * 0.3
    df = pd.DataFrame({
        "open":   np.roll(close, 1),
        "high":   close + noise,
        "low":    close - noise,
        "close":  close,
        "volume": rng.random(n) * 1000 + 100,
    })
    df.loc[0, "open"] = close[0]
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    df.index = idx
    return df


def test_evaluate_signal_populates_indicators_lrc_rsi():
    """indicators dict must contain lrc_pct, rsi_1h, adx_1h, atr_1h, sma100_4h."""
    from strategy.core import evaluate_signal
    df1h = _synth_ohlcv(n=250, seed=1, base=100.0)
    df4h = _synth_ohlcv(n=200, seed=2, base=100.0)
    df5m = _synth_ohlcv(n=250, seed=3, base=100.0)
    df1d = _synth_ohlcv(n=100, seed=4, base=100.0)
    decision = evaluate_signal(
        df1h, df4h, df5m, df1d,
        symbol="BTCUSDT",
        cfg={},
        regime={"regime": "NEUTRAL", "score": 50, "details": {}},
        health_state="NORMAL",
        now=datetime(2024, 4, 23, tzinfo=timezone.utc),
    )
    ind = decision.indicators
    # Presence checks — values may vary with inputs but must exist and be numeric
    assert "lrc_pct" in ind
    assert ind["lrc_pct"] is None or (0.0 <= ind["lrc_pct"] <= 100.0)
    assert "rsi_1h" in ind
    assert 0.0 <= ind["rsi_1h"] <= 100.0
    assert "adx_1h" in ind
    assert "atr_1h" in ind
    assert ind["atr_1h"] >= 0.0
    assert "sma100_4h" in ind
    assert ind["sma100_4h"] > 0.0
    # Last price should be recorded
    assert "price" in ind
    assert ind["price"] > 0.0


def test_evaluate_signal_indicators_match_btc_scanner():
    """Side-by-side: evaluate_signal indicators must match btc_scanner calc_* on same inputs."""
    from strategy.core import evaluate_signal
    from strategy.indicators import (
        calc_lrc, calc_rsi, calc_sma, calc_atr, calc_adx,
    )
    import btc_scanner

    df1h = _synth_ohlcv(n=250, seed=11, base=50_000.0)
    df4h = _synth_ohlcv(n=200, seed=12, base=50_000.0)
    df5m = _synth_ohlcv(n=250, seed=13, base=50_000.0)
    df1d = _synth_ohlcv(n=100, seed=14, base=50_000.0)

    decision = evaluate_signal(
        df1h, df4h, df5m, df1d,
        symbol="BTCUSDT",
        cfg={},
        regime={"regime": "NEUTRAL", "score": 50, "details": {}},
        health_state="NORMAL",
        now=datetime(2024, 4, 23, tzinfo=timezone.utc),
    )

    # Re-run same indicator calls the OLD scan() would run and compare.
    expected_lrc_pct, _, _, _ = calc_lrc(df1h["close"], btc_scanner.LRC_PERIOD, btc_scanner.LRC_STDEV)
    expected_rsi_last = round(calc_rsi(df1h["close"], btc_scanner.RSI_PERIOD).iloc[-1], 2)
    expected_adx_last_series = calc_adx(df1h, 14)
    expected_adx_last = round(float(expected_adx_last_series.iloc[-1]), 2)
    expected_atr_last = float(calc_atr(df1h, btc_scanner.ATR_PERIOD).iloc[-1])
    expected_sma100_4h = float(calc_sma(df4h["close"], 100).iloc[-1])

    assert decision.indicators["lrc_pct"] == pytest.approx(expected_lrc_pct, rel=1e-9)
    assert decision.indicators["rsi_1h"] == pytest.approx(expected_rsi_last, rel=1e-9)
    assert decision.indicators["adx_1h"] == pytest.approx(expected_adx_last, rel=1e-9)
    assert decision.indicators["atr_1h"] == pytest.approx(expected_atr_last, rel=1e-9)
    assert decision.indicators["sma100_4h"] == pytest.approx(expected_sma100_4h, rel=1e-9)


# ─────────────────────────────────────────────────────────────────────────────
#  Commit C — score 0-9 + direction selection
# ─────────────────────────────────────────────────────────────────────────────


def _downtrend_ohlcv(n: int = 250, seed: int = 42, start: float = 100.0,
                     drop_bars: int = 20, drop_pct: float = 0.08) -> pd.DataFrame:
    """Flat series then abrupt drop at the end → LRC pct ≈ 0 (deep LONG zone)."""
    rng = np.random.default_rng(seed)
    flat_len = n - drop_bars
    flat = start + rng.standard_normal(flat_len) * 0.2
    drop = np.linspace(start, start * (1 - drop_pct), drop_bars)
    close = np.concatenate([flat, drop])[:n]
    noise = np.abs(rng.standard_normal(n)) * 0.2
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


def _uptrend_ohlcv(n: int = 250, seed: int = 42, start: float = 100.0,
                   rise_bars: int = 20, rise_pct: float = 0.08) -> pd.DataFrame:
    """Flat series then abrupt rise at the end → LRC pct ≈ 100 (deep SHORT zone)."""
    rng = np.random.default_rng(seed)
    flat_len = n - rise_bars
    flat = start + rng.standard_normal(flat_len) * 0.2
    rise = np.linspace(start, start * (1 + rise_pct), rise_bars)
    close = np.concatenate([flat, rise])[:n]
    noise = np.abs(rng.standard_normal(n)) * 0.2
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


def _flat_ohlcv(n: int = 250, seed: int = 42, start: float = 100.0) -> pd.DataFrame:
    """Flat series with low noise → LRC% near 50 (middle zone)."""
    rng = np.random.default_rng(seed)
    close = start + rng.standard_normal(n) * 0.1
    noise = np.abs(rng.standard_normal(n)) * 0.05
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


def test_evaluate_signal_direction_long_in_bull_regime():
    """A downtrend (LRC low) under BULL/NEUTRAL regime should classify LONG."""
    from strategy.core import evaluate_signal
    df1h = _downtrend_ohlcv(n=250, seed=100, start=100.0)
    df4h = _downtrend_ohlcv(n=200, seed=101, start=100.0)
    df5m = _downtrend_ohlcv(n=250, seed=102, start=100.0)
    df1d = _downtrend_ohlcv(n=100, seed=103, start=100.0)
    decision = evaluate_signal(
        df1h, df4h, df5m, df1d,
        symbol="BTCUSDT",
        cfg={},
        regime={"regime": "BULL", "score": 75, "details": {}},
        health_state="NORMAL",
        now=datetime(2024, 4, 23, tzinfo=timezone.utc),
    )
    # Direction is LONG because LRC pct ≤ 25 and regime maps BULL → LONG
    assert decision.indicators["lrc_pct"] is not None
    assert decision.indicators["lrc_pct"] <= 25.0, \
        f"Synthetic downtrend should stay in LONG zone; got lrc_pct={decision.indicators['lrc_pct']}"
    assert decision.direction == "LONG"
    assert 0 <= decision.score <= 9


def test_evaluate_signal_direction_short_in_bear_regime():
    """An uptrend (LRC high) under BEAR regime should classify SHORT."""
    from strategy.core import evaluate_signal
    df1h = _uptrend_ohlcv(n=250, seed=200, start=100.0)
    df4h = _uptrend_ohlcv(n=200, seed=201, start=100.0)
    df5m = _uptrend_ohlcv(n=250, seed=202, start=100.0)
    df1d = _uptrend_ohlcv(n=100, seed=203, start=100.0)
    decision = evaluate_signal(
        df1h, df4h, df5m, df1d,
        symbol="BTCUSDT",
        cfg={},
        regime={"regime": "BEAR", "score": 20, "details": {}},
        health_state="NORMAL",
        now=datetime(2024, 4, 23, tzinfo=timezone.utc),
    )
    assert decision.indicators["lrc_pct"] is not None
    assert decision.indicators["lrc_pct"] >= 75.0, \
        f"Synthetic uptrend should stay in SHORT zone; got lrc_pct={decision.indicators['lrc_pct']}"
    assert decision.direction == "SHORT"
    assert 0 <= decision.score <= 9


def test_evaluate_signal_direction_none_when_uptrend_under_bull():
    """Uptrend (LRC high) under BULL regime → NONE (SHORT gated by BEAR only)."""
    from strategy.core import evaluate_signal
    df1h = _uptrend_ohlcv(n=250, seed=210, start=100.0)
    df4h = _uptrend_ohlcv(n=200, seed=211, start=100.0)
    df5m = _uptrend_ohlcv(n=250, seed=212, start=100.0)
    df1d = _uptrend_ohlcv(n=100, seed=213, start=100.0)
    decision = evaluate_signal(
        df1h, df4h, df5m, df1d,
        symbol="BTCUSDT",
        cfg={},
        regime={"regime": "BULL", "score": 80, "details": {}},
        health_state="NORMAL",
        now=datetime(2024, 4, 23, tzinfo=timezone.utc),
    )
    # In SHORT zone but regime is BULL → SHORT is gated off, direction stays NONE
    assert decision.indicators["lrc_pct"] >= 75.0
    assert decision.direction == "NONE"


def test_evaluate_signal_neutral_when_out_of_zone():
    """LRC in the 25-75 middle band → direction is NONE regardless of regime."""
    from strategy.core import evaluate_signal
    df1h = _flat_ohlcv(n=250, seed=999)
    df4h = _flat_ohlcv(n=200, seed=998)
    df5m = _flat_ohlcv(n=250, seed=997)
    df1d = _flat_ohlcv(n=100, seed=996)

    decision = evaluate_signal(
        df1h, df4h, df5m, df1d,
        symbol="BTCUSDT",
        cfg={},
        regime={"regime": "BULL", "score": 80, "details": {}},
        health_state="NORMAL",
        now=datetime(2024, 4, 23, tzinfo=timezone.utc),
    )
    # Flat series tends to keep LRC near 50%; when in middle band, direction is NONE
    lrc_pct = decision.indicators["lrc_pct"]
    if lrc_pct is not None and 25.0 < lrc_pct < 75.0:
        assert decision.direction == "NONE"
        assert decision.is_signal is False


def test_evaluate_signal_score_label_populated_when_direction():
    """When direction is chosen, score_label matches tier classification."""
    from strategy.core import evaluate_signal
    df1h = _downtrend_ohlcv(n=250, seed=300, start=100.0)
    df4h = _downtrend_ohlcv(n=200, seed=301, start=100.0)
    df5m = _downtrend_ohlcv(n=250, seed=302, start=100.0)
    df1d = _downtrend_ohlcv(n=100, seed=303, start=100.0)
    decision = evaluate_signal(
        df1h, df4h, df5m, df1d,
        symbol="BTCUSDT",
        cfg={},
        regime={"regime": "BULL", "score": 75, "details": {}},
        health_state="NORMAL",
        now=datetime(2024, 4, 23, tzinfo=timezone.utc),
    )
    assert decision.direction != "NONE"
    # Valid tiers per btc_scanner.score_label
    assert decision.score_label in ("MINIMA", "STANDARD", "PREMIUM", "INSUFICIENTE")


def test_evaluate_signal_score_matches_scanner_logic_long():
    """Replicate scan()'s C1-C7 checks and assert evaluate_signal's score matches."""
    from strategy.core import evaluate_signal
    from strategy.indicators import (
        calc_lrc, calc_rsi, calc_bb, calc_sma, calc_cvd_delta,
    )
    import btc_scanner

    df1h = _downtrend_ohlcv(n=250, seed=400, start=100.0)
    df4h = _downtrend_ohlcv(n=200, seed=401, start=100.0)
    df5m = _downtrend_ohlcv(n=250, seed=402, start=100.0)
    df1d = _downtrend_ohlcv(n=100, seed=403, start=100.0)

    decision = evaluate_signal(
        df1h, df4h, df5m, df1d,
        symbol="BTCUSDT",
        cfg={},
        regime={"regime": "BULL", "score": 75, "details": {}},
        health_state="NORMAL",
        now=datetime(2024, 4, 23, tzinfo=timezone.utc),
    )
    assert decision.direction == "LONG", \
        f"Expected LONG for seed=400 downtrend; got {decision.direction}"

    # Replicate scan()'s C1-C7 manually
    price = float(df1h["close"].iloc[-1])
    rsi1h = calc_rsi(df1h["close"], btc_scanner.RSI_PERIOD)
    cur_rsi1h = round(float(rsi1h.iloc[-1]), 2)
    _, lrc_up, lrc_dn, _ = calc_lrc(df1h["close"], btc_scanner.LRC_PERIOD, btc_scanner.LRC_STDEV)
    bb_up1h, _, bb_dn1h = calc_bb(df1h["close"], btc_scanner.BB_PERIOD, btc_scanner.BB_STDEV)
    sma10_1h = float(calc_sma(df1h["close"], 10).iloc[-1])
    sma20_1h = float(calc_sma(df1h["close"], 20).iloc[-1])
    vol_avg1h = float(df1h["volume"].rolling(btc_scanner.VOL_PERIOD).mean().iloc[-1])
    vol_1h = float(df1h["volume"].iloc[-1])
    cvd_1h = calc_cvd_delta(df1h, n=3)
    rsi_divs = btc_scanner.detect_rsi_divergence(df1h["close"], rsi1h, window=72)
    bull_div = rsi_divs["bull"]

    expected_score = 0
    if cur_rsi1h < 40: expected_score += 2           # C1
    if bull_div: expected_score += 2                 # C2
    dist_sup = abs(price - lrc_dn) / price * 100 if lrc_dn else 999
    if dist_sup <= 1.5: expected_score += 1          # C3
    if price <= bb_dn1h.iloc[-1]: expected_score += 1  # C4
    if vol_1h >= vol_avg1h: expected_score += 1      # C5
    if cvd_1h > 0: expected_score += 1               # C6
    if sma10_1h > sma20_1h: expected_score += 1      # C7

    assert decision.score == expected_score


def test_evaluate_signal_score_matches_scanner_logic_short():
    """Replicate scan()'s SHORT C1-C7 checks and assert evaluate_signal's score matches."""
    from strategy.core import evaluate_signal
    from strategy.indicators import (
        calc_lrc, calc_rsi, calc_bb, calc_sma, calc_cvd_delta,
    )
    import btc_scanner

    df1h = _uptrend_ohlcv(n=250, seed=500, start=100.0)
    df4h = _uptrend_ohlcv(n=200, seed=501, start=100.0)
    df5m = _uptrend_ohlcv(n=250, seed=502, start=100.0)
    df1d = _uptrend_ohlcv(n=100, seed=503, start=100.0)

    decision = evaluate_signal(
        df1h, df4h, df5m, df1d,
        symbol="BTCUSDT",
        cfg={},
        regime={"regime": "BEAR", "score": 20, "details": {}},
        health_state="NORMAL",
        now=datetime(2024, 4, 23, tzinfo=timezone.utc),
    )
    assert decision.direction == "SHORT", \
        f"Expected SHORT for seed=500 uptrend under BEAR; got {decision.direction}"

    # Replicate scan()'s SHORT C1-C7
    price = float(df1h["close"].iloc[-1])
    rsi1h = calc_rsi(df1h["close"], btc_scanner.RSI_PERIOD)
    cur_rsi1h = round(float(rsi1h.iloc[-1]), 2)
    _, lrc_up, lrc_dn, _ = calc_lrc(df1h["close"], btc_scanner.LRC_PERIOD, btc_scanner.LRC_STDEV)
    bb_up1h, _, bb_dn1h = calc_bb(df1h["close"], btc_scanner.BB_PERIOD, btc_scanner.BB_STDEV)
    sma10_1h = float(calc_sma(df1h["close"], 10).iloc[-1])
    sma20_1h = float(calc_sma(df1h["close"], 20).iloc[-1])
    vol_avg1h = float(df1h["volume"].rolling(btc_scanner.VOL_PERIOD).mean().iloc[-1])
    vol_1h = float(df1h["volume"].iloc[-1])
    cvd_1h = calc_cvd_delta(df1h, n=3)
    rsi_divs = btc_scanner.detect_rsi_divergence(df1h["close"], rsi1h, window=72)
    bear_div = rsi_divs["bear"]

    expected_score = 0
    if cur_rsi1h > 60: expected_score += 2           # C1
    if bear_div: expected_score += 2                 # C2
    dist_res = abs(price - lrc_up) / price * 100 if lrc_up else 999
    if dist_res <= 1.5: expected_score += 1          # C3
    if price >= bb_up1h.iloc[-1]: expected_score += 1  # C4
    if vol_1h >= vol_avg1h: expected_score += 1      # C5
    if cvd_1h < 0: expected_score += 1               # C6
    if sma10_1h < sma20_1h: expected_score += 1      # C7

    assert decision.score == expected_score
