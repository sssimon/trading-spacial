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
