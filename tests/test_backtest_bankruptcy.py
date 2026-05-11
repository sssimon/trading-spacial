"""Regression tests for the per-symbol bankruptcy handler (#280).

The handler trips when simulate_strategy's `capital` falls below
BANKRUPTCY_THRESHOLD (0.1 × INITIAL_CAPITAL). Once tripped:
  - A synthetic trade with exit_reason="BANKRUPT" is appended exactly once.
  - No new positions open for the rest of the run.
  - Existing open positions can still close naturally (SL/TP/TIME_LIMIT).
  - calculate_metrics excludes BANKRUPT records from win/loss/PF/Sharpe/Sortino.

Layering: this sits on top of the effective_capital = max(0, capital) floor
(A.0.2 / #277) and the K=10 overshoot cap (#309). It does not replace either.
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd
import pytest

from backtest import (
    BANKRUPTCY_THRESHOLD,
    INITIAL_CAPITAL,
    calculate_metrics,
)


def _trade(entry_h: int, exit_h: int, pnl_usd: float, exit_reason: str = "SL",
           score: int = 4, size_mult: float = 1.0) -> dict:
    base = pd.Timestamp("2024-01-01", tz="UTC")
    return {
        "entry_time": base + pd.Timedelta(hours=entry_h),
        "exit_time": base + pd.Timedelta(hours=exit_h),
        "entry_price": 100.0,
        "exit_price": 100.0 + pnl_usd / 100,
        "exit_reason": exit_reason,
        "direction": "LONG",
        "pnl_pct": pnl_usd / 100,
        "pnl_usd": pnl_usd,
        "overshoot_clamped": False,
        "score": score,
        "size_mult": size_mult,
        "duration_hours": float(exit_h - entry_h),
        "atr_sl_mult_used": 1.0,
        "atr_tp_mult_used": 4.0,
        "atr_be_mult_used": 1.5,
    }


def test_threshold_constant_is_ten_percent_of_initial_capital():
    assert BANKRUPTCY_THRESHOLD == pytest.approx(0.1 * INITIAL_CAPITAL)
    assert BANKRUPTCY_THRESHOLD == pytest.approx(1_000.0)


def test_bankrupt_record_is_emitted_when_capital_crosses_threshold():
    """If capital drops below threshold mid-run, exactly one BANKRUPT
    record is appended at the breach bar."""
    trades = [
        _trade(0, 1, pnl_usd=-2_000),  # capital: 10_000 → 8_000
        _trade(2, 3, pnl_usd=-3_000),  # 8_000 → 5_000
        _trade(4, 5, pnl_usd=-4_500),  # 5_000 → 500  ← BREACH here
        _trade(6, 7, pnl_usd=-100),    # would-be: 500 → 400; must NOT appear
    ]
    # Mock the capital accumulator the way simulate_strategy does it.
    # When #280 ships, simulate_strategy itself will emit the BANKRUPT
    # record + halt entries; this test asserts the contract any reasonable
    # implementation must satisfy.
    from backtest import _emit_bankrupt_if_breached

    capital = INITIAL_CAPITAL
    bankrupt = False
    emitted = []
    for t in trades:
        if bankrupt:
            break  # would-be entry skipped — mirror simulate_strategy gate
        capital += t["pnl_usd"]
        rec = _emit_bankrupt_if_breached(capital, t["exit_time"])
        if rec is not None:
            emitted.append(rec)
            bankrupt = True
    assert len(emitted) == 1, f"expected exactly one BANKRUPT record, got {len(emitted)}"
    assert emitted[0]["exit_reason"] == "BANKRUPT"
    assert emitted[0]["pnl_usd"] == 0.0
    assert emitted[0]["pnl_pct"] == 0.0
    # The breach capital should be carried for forensic visibility.
    assert "breach_capital" in emitted[0]
    assert emitted[0]["breach_capital"] == pytest.approx(500.0)


def _build_losing_streak_frames(n_hours: int = 24 * 60):
    """Return (df1h, df4h, df5m, df1d, df_fng, df_funding) for a monotonic
    downtrend large enough to bankrupt a $10K account under forced entries.

    The frames are syntactically valid OHLCV with strictly positive volume;
    real signal generation is bypassed via monkeypatched evaluate_signal.
    """
    start = pd.Timestamp("2024-01-01", tz="UTC")
    hours = pd.date_range(start, periods=n_hours, freq="1h", tz="UTC")
    # 2% drop per bar — drives consecutive SL hits past the bankruptcy floor.
    close = 100.0 * (0.98 ** np.arange(len(hours)))
    df1h = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.0005,
            "low": close * 0.97,
            "close": close,
            "volume": np.full(len(hours), 1_000_000.0),
        },
        index=hours,
    )
    df4h = (
        df1h.resample("4h")
        .agg({"open": "first", "high": "max", "low": "min",
              "close": "last", "volume": "sum"})
        .dropna()
    )
    df5m = df1h.resample("5min").ffill()
    df1d = (
        df1h.resample("1D")
        .agg({"open": "first", "high": "max", "low": "min",
              "close": "last", "volume": "sum"})
        .dropna()
    )
    df_fng = pd.DataFrame({"value": np.full(len(df1d), 50)}, index=df1d.index)
    df_funding = pd.DataFrame({"funding_rate": np.zeros(len(df1d))}, index=df1d.index)
    return df1h, df4h, df5m, df1d, df_fng, df_funding


def _forced_long_signal_factory():
    """Build a stand-in for strategy.core.evaluate_signal that always
    returns a strong LONG entry at the bar's close price. The simulator
    invokes evaluate_signal once per bar after warmup — this guarantees
    a continuous stream of losing trades on the downtrend fixture."""
    from strategy.core import SignalDecision

    def fake(df1h, df4h, df5m, df1d, *, symbol, cfg, regime,
             health_state="NORMAL", now=None):
        price = float(df1h.iloc[-1]["close"])
        atr_val = price * 0.01
        return SignalDecision(
            is_signal=True,
            is_setup=False,
            direction="LONG",
            score=4,
            score_label="PREMIUM",
            entry_price=price,
            sl_price=price - atr_val * 1.0,
            tp_price=price + atr_val * 4.0,
            indicators={"atr_1h": atr_val},
            reasons={
                "atr_sl_mult": 1.0,
                "atr_tp_mult": 4.0,
                "atr_be_mult": 1.5,
            },
            estado="forced-test-entry",
        )

    return fake


def test_simulate_strategy_halts_entries_after_bankruptcy(monkeypatch):
    """End-to-end: when monkeypatched evaluate_signal forces a LONG every
    bar on a monotonic downtrend, simulate_strategy emits exactly one
    BANKRUPT record and opens no further positions afterwards."""
    import strategy.core as strategy_core
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d, df_fng, df_funding = _build_losing_streak_frames()
    monkeypatch.setattr(
        strategy_core, "evaluate_signal", _forced_long_signal_factory(),
    )

    trades, equity_curve = simulate_strategy(
        df1h=df1h, df4h=df4h, df5m=df5m, df1d=df1d,
        df_fng=df_fng, df_funding=df_funding,
        symbol="TESTUSDT",
        enable_slippage=False, enable_spread=False, enable_fees=False,
        regime_disabled=True,
        cfg={"symbol_overrides": {}},
    )

    bankrupt_records = [t for t in trades if t["exit_reason"] == "BANKRUPT"]
    assert len(bankrupt_records) == 1, (
        f"expected exactly 1 BANKRUPT record, got {len(bankrupt_records)}: "
        f"{[t['exit_time'] for t in bankrupt_records]}"
    )
    breach_time = bankrupt_records[0]["exit_time"]
    later_entries = [
        t for t in trades
        if t["exit_reason"] not in ("BANKRUPT", "OPEN")
        and t["entry_time"] > breach_time
    ]
    assert later_entries == [], (
        f"simulator opened {len(later_entries)} entries after bankruptcy"
    )


def test_calculate_metrics_excludes_bankrupt_from_win_pf_sharpe():
    """BANKRUPT records are event markers, not trades. They must not
    contribute to win_rate / profit_factor / Sharpe / Sortino / streaks
    / score-tier breakdowns.

    The equity-curve-derived max_drawdown and net_pnl are computed from
    the equity_curve list and reflect the actual capital path — those
    are unaffected by this filter."""
    trades = [
        _trade(0, 1, pnl_usd=+100, exit_reason="TP"),
        _trade(2, 3, pnl_usd=-50, exit_reason="SL"),
        # Synthetic BANKRUPT event — must be filtered.
        {
            **_trade(4, 4, pnl_usd=0, exit_reason="BANKRUPT", score=0, size_mult=0.0),
            "breach_capital": 500.0,
        },
    ]
    equity_curve = [
        {"time": pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(hours=h),
         "equity": eq}
        for h, eq in [(0, 10_000), (1, 10_100), (3, 10_050), (4, 500)]
    ]
    metrics = calculate_metrics(trades, equity_curve)

    # With 1 win + 1 loss (BANKRUPT excluded), win_rate must be 50.0%,
    # NOT 33.3 (which would mean BANKRUPT was counted as a loss).
    # calculate_metrics returns win_rate as a rounded percentage.
    assert metrics["win_rate"] == pytest.approx(50.0)
    assert metrics["total_trades"] == 2, (
        f"BANKRUPT leaked into total_trades: got {metrics['total_trades']}"
    )
    # max_drawdown_pct still reflects the bankruptcy via equity_curve.
    assert metrics["max_drawdown_pct"] < -90, (
        f"equity curve dropped 95% but max_drawdown_pct reads "
        f"{metrics['max_drawdown_pct']}"
    )
    # Forensic field surfaced for operator visibility.
    assert metrics["bankruptcy_count"] == 1


def test_simulate_strategy_smoke_bankruptcy_no_errors(monkeypatch):
    """End-to-end smoke: simulate_strategy + calculate_metrics complete
    without exception on the bankruptcy fixture; metrics carry
    bankruptcy_count >= 1 and win_rate stays bounded."""
    import strategy.core as strategy_core
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d, df_fng, df_funding = _build_losing_streak_frames()
    monkeypatch.setattr(
        strategy_core, "evaluate_signal", _forced_long_signal_factory(),
    )

    trades, equity_curve = simulate_strategy(
        df1h=df1h, df4h=df4h, df5m=df5m, df1d=df1d,
        df_fng=df_fng, df_funding=df_funding,
        symbol="TESTUSDT",
        enable_slippage=False, enable_spread=False, enable_fees=False,
        regime_disabled=True,
        cfg={"symbol_overrides": {}},
    )
    metrics = calculate_metrics(trades, equity_curve)
    assert isinstance(metrics, dict)
    assert metrics.get("bankruptcy_count", 0) >= 1
    if metrics.get("total_trades", 0) > 0:
        # calculate_metrics returns win_rate as a rounded percentage (0–100).
        assert 0.0 <= metrics["win_rate"] <= 100.0
