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
