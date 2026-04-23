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
