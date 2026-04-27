"""Regression: backtest's _close_position must REFUSE to amplify phantom profits.

Catches the 2026-04-15 → 2026-04-27 phantom profit pattern where:
  1. round(price ± dist, 2) inverts SL for sub-$1 symbols (fixed in #fix/precision-rounding-bug)
  2. abs(entry_price - sl_orig) in PnL formula stripped the sign of inverted SL
  3. Result: pnl_usd = risk_amount EXACTLY (phantom = exactly the risk)

The Apr 16-18 strategy docs reported +$98k/+$168k portfolio results that were
65420 USD phantom + (-11741 USD) real strategy. This test ensures that even
if bug #1 reappears (rounding), bug #2 (abs) cannot amplify it into phantom
profits — the position closes with pnl_usd=0 instead.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


def test_close_position_long_with_inverted_SL_returns_zero_pnl():
    """LONG with sl_orig ABOVE entry → inverted SL → pnl_usd MUST be 0, not phantom.

    Pre-fix (with abs()): sl_pct_actual = abs(0.07649 - 0.08) = 4.59% (positive),
    pnl_pct = (0.08 - 0.07649)/0.07649 = 4.59%, pnl_usd = risk * 1.0 = $50 PHANTOM.

    Post-fix: sl_pct_actual = (0.07649 - 0.08)/0.07649 = -4.59% (negative),
    triggers the `else` branch → pnl_usd = 0.
    """
    from backtest import _close_position

    position = {
        "entry_price": 0.07649,
        "entry_time": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "score": 1,
        "direction": "LONG",
        "sl": 0.08,            # ⚠ ABOVE entry — inverted (the bug pattern)
        "sl_orig": 0.08,
        "tp": 0.09,
        "size_mult": 0.5,
        "be_threshold": None,
    }
    trade = _close_position(
        position,
        exit_price=0.08,       # exit at the inverted SL
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="SL",
        capital=10_000.0,
    )
    # Old buggy behavior: pnl_usd = $50 (= 10000 * 0.01 * 0.5 * 1.0)
    # New defensive behavior: pnl_usd = 0
    assert trade["pnl_usd"] == 0.0, (
        f"Inverted-LONG-SL produced pnl_usd={trade['pnl_usd']} — phantom profit "
        f"regression. Defensive check expected 0."
    )


def test_close_position_short_with_inverted_SL_returns_zero_pnl():
    """SHORT with sl_orig BELOW entry → inverted SL → pnl_usd MUST be 0."""
    from backtest import _close_position

    position = {
        "entry_price": 0.10,
        "entry_time": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "score": 1,
        "direction": "SHORT",
        "sl": 0.09,            # ⚠ BELOW entry — inverted for SHORT (should be above)
        "sl_orig": 0.09,
        "tp": 0.08,
        "size_mult": 1.0,
        "be_threshold": None,
    }
    trade = _close_position(
        position,
        exit_price=0.09,
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="SL",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == 0.0, (
        f"Inverted-SHORT-SL produced pnl_usd={trade['pnl_usd']} — phantom profit "
        f"regression. Defensive check expected 0."
    )


def test_close_position_long_valid_SL_loss_normal():
    """LONG with proper SL (below entry) hitting SL → real loss = -risk_amount."""
    from backtest import _close_position

    position = {
        "entry_price": 100.0,
        "entry_time": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "score": 1,
        "direction": "LONG",
        "sl": 95.0,            # 5% below entry (correct LONG SL)
        "sl_orig": 95.0,
        "tp": 110.0,
        "size_mult": 1.0,
        "be_threshold": None,
    }
    trade = _close_position(
        position,
        exit_price=95.0,
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="SL",
        capital=10_000.0,
    )
    # pnl_pct = (95 - 100)/100 = -5%
    # sl_pct_actual = (100 - 95)/100 = 5%
    # pnl_usd = 10000 * 0.01 * 1.0 * (-5/5) = -$100 (the risk amount, lost)
    assert trade["pnl_usd"] == -100.0


def test_close_position_long_valid_SL_partial_tp_normal():
    """LONG with proper SL hitting TP → R-multiple-scaled gain."""
    from backtest import _close_position

    position = {
        "entry_price": 100.0,
        "entry_time": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "score": 1,
        "direction": "LONG",
        "sl": 95.0,            # SL distance 5%
        "sl_orig": 95.0,
        "tp": 120.0,           # TP distance 20% (= 4R)
        "size_mult": 1.0,
        "be_threshold": None,
    }
    trade = _close_position(
        position,
        exit_price=120.0,
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TP",
        capital=10_000.0,
    )
    # pnl_pct = (120 - 100)/100 = 20%
    # sl_pct_actual = 5%
    # pnl_usd = 10000 * 0.01 * 1.0 * (20/5) = $400 (4× risk)
    assert trade["pnl_usd"] == pytest.approx(400.0, abs=0.01)


def test_close_position_short_valid_SL_loss_normal():
    """SHORT with proper SL (above entry) hitting SL → real loss = -risk."""
    from backtest import _close_position

    position = {
        "entry_price": 100.0,
        "entry_time": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "score": 1,
        "direction": "SHORT",
        "sl": 105.0,           # 5% above entry (correct SHORT SL)
        "sl_orig": 105.0,
        "tp": 90.0,
        "size_mult": 1.0,
        "be_threshold": None,
    }
    trade = _close_position(
        position,
        exit_price=105.0,
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="SL",
        capital=10_000.0,
    )
    # pnl_pct = (100 - 105)/100 = -5%
    # sl_pct_actual = (105 - 100)/100 = 5%
    # pnl_usd = 10000 * 0.01 * 1.0 * (-5/5) = -$100
    assert trade["pnl_usd"] == -100.0


def test_close_position_zero_distance_SL_returns_zero_pnl():
    """Edge: SL == entry (zero distance) → cannot scale R-multiple → pnl_usd = 0.

    Without this guard, division by zero would crash. The original code had
    a `> 0` guard but with abs() it never triggered for inverted SL — only
    for the truly-zero case which was rare.
    """
    from backtest import _close_position

    position = {
        "entry_price": 100.0,
        "entry_time": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "score": 1,
        "direction": "LONG",
        "sl": 100.0,           # exactly at entry
        "sl_orig": 100.0,
        "tp": 105.0,
        "size_mult": 1.0,
        "be_threshold": None,
    }
    trade = _close_position(
        position,
        exit_price=100.0,
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="SL",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == 0.0


def test_close_position_no_abs_in_source():
    """Code-level guard: abs() must NOT reappear in _close_position SL formula.

    The original bug was abs(entry - sl_orig) which masked inverted SL into
    phantom profits. If this pattern reappears, the test fails immediately.
    """
    import inspect
    from backtest import _close_position

    source = inspect.getsource(_close_position)
    assert "abs(entry_price - position[\"sl_orig\"])" not in source, (
        "_close_position contains abs(entry - sl_orig) — that's the phantom "
        "profit bug from 2026-04-15 → 2026-04-27. Use direction-aware "
        "(entry - sl_orig) for LONG, (sl_orig - entry) for SHORT."
    )
    assert "abs(entry_price - sl_orig)" not in source, (
        "_close_position contains abs(entry - sl_orig) — phantom profit regression."
    )
