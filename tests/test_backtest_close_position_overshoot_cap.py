"""Regression: backtest's _close_position must CAP single-trade overshoot.

The simulator computes per-symbol PnL with $10K initial capital. The R-multiple
formula `pnl_usd = risk_amount * (pnl_pct / sl_pct_actual)` is unbounded on the
TIME_LIMIT exit path (`backtest.py:641` — `exit_price = float(bar["close"])`)
and on gap-through-SL exits, where the realized exit price can be many
multiples of the SL distance from entry. Without a cap, a single trade with
tight SL (small `sl_pct_actual`) and a volatile bar can produce |pnl_usd| far
exceeding the $10K per-symbol initial capital, breaking the per-symbol floor
invariant the calling simulator relies on.

The 2026-05-04 A.4-1.5 Phase 3 sweep surfaced this on PENDLEUSDT:
$-1,702,401 single-symbol loss vs $10K initial capital (170× overshoot,
regime-invariant across the 4-config sweep). The cap below bounds
|pnl_usd| ≤ MAX_OVERSHOOT_RATIO × risk_amount per trade, symmetric on win/loss.

K=10 is rule-derived: a 10× SL move is already absurd; a real trader exits
manually before that, so backtest pnl beyond this multiple is unrealistic
execution. Documented in CLAUDE.md "Caveats heredados — A.4 (#250) MUST honor"
#4 (per-symbol vs portfolio aggregation gap).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helper: build a LONG position with `entry_price=100, sl=99` (sl_pct_actual=1%)
# so the overshoot ratio equals (exit_price - 100) — convenient for arithmetic.
# Risk_amount with capital=$10K and size_mult=1.0 is $100.
# ─────────────────────────────────────────────────────────────────────────────
def _build_long_position():
    return {
        "entry_price": 100.0,
        "entry_time": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "score": 1,
        "direction": "LONG",
        "sl": 99.0,            # 1% SL distance
        "sl_orig": 99.0,
        "tp": 110.0,
        "size_mult": 1.0,
        "be_threshold": None,
    }


def _build_short_position():
    return {
        "entry_price": 100.0,
        "entry_time": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "score": 1,
        "direction": "SHORT",
        "sl": 101.0,           # 1% SL distance (above entry for SHORT)
        "sl_orig": 101.0,
        "tp": 90.0,
        "size_mult": 1.0,
        "be_threshold": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Within-cap tests: ratios in [-10, +10] must produce unmodified pnl_usd.
# ─────────────────────────────────────────────────────────────────────────────


def test_long_within_cap_positive_unchanged():
    """LONG with ratio = +5 (TP-style win, well within cap) → pnl_usd = +$500."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=105.0,      # +5% pnl_pct, ratio = +5
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TP",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(500.0, abs=0.01)


def test_long_within_cap_negative_unchanged():
    """LONG with ratio = -5 (5× SL overshoot, within cap) → pnl_usd = -$500."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=95.0,       # -5% pnl_pct, ratio = -5
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(-500.0, abs=0.01)


def test_long_just_under_cap_positive_unchanged():
    """LONG with ratio = +9.99 (just under cap) → pnl_usd = +$999, NOT clamped."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=109.99,     # +9.99% pnl_pct, ratio = +9.99
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(999.0, abs=0.01)


def test_long_just_under_cap_negative_unchanged():
    """LONG with ratio = -9.99 (just under cap) → pnl_usd = -$999, NOT clamped."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=90.01,      # -9.99% pnl_pct, ratio = -9.99
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(-999.0, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# Above-cap tests: ratios outside [-10, +10] must be clamped to ±$1000.
# ─────────────────────────────────────────────────────────────────────────────


def test_long_just_over_cap_negative_clamped():
    """LONG with ratio = -10.01 (just over cap, loss side) → clamped to -$1000."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=89.99,      # -10.01% pnl_pct, ratio = -10.01
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(-1000.0, abs=0.01)


def test_long_just_over_cap_positive_clamped():
    """LONG with ratio = +10.01 (just over cap, win side) → clamped to +$1000."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=110.01,     # +10.01% pnl_pct, ratio = +10.01
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(1000.0, abs=0.01)


def test_long_far_above_cap_negative_clamped():
    """LONG with ratio = -50 (5× over cap, catastrophic loss) → clamped to -$1000.

    sl_pct_actual = 1%, pnl_pct = -50% → ratio = -50, clamped to -10."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=50.0,       # -50% pnl_pct
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(-1000.0, abs=0.01)


def test_long_far_above_cap_positive_clamped():
    """LONG with ratio = +50 (impossible-trader-execution win) → clamped to +$1000."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=150.0,      # +50% pnl_pct, ratio = +50
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(1000.0, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# SHORT mirror: cap applies symmetrically across direction.
# ─────────────────────────────────────────────────────────────────────────────


def test_short_far_above_cap_negative_clamped():
    """SHORT with ratio = -50 (price gapped up, overshoot SL) → clamped to -$1000."""
    from backtest import _close_position

    trade = _close_position(
        _build_short_position(),
        # SHORT pnl_pct = (entry - exit) / entry × 100 = (100 - 150)/100 × 100 = -50%
        # sl_pct_actual = (sl - entry) / entry × 100 = 1%
        # ratio = -50, clamped to -10 → pnl_usd = -$1000
        exit_price=150.0,
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(-1000.0, abs=0.01)


def test_short_far_above_cap_positive_clamped():
    """SHORT with ratio = +50 (price collapsed, big win) → clamped to +$1000."""
    from backtest import _close_position

    trade = _close_position(
        _build_short_position(),
        # SHORT pnl_pct = (100 - 50)/100 × 100 = +50%, ratio = +50, clamped to +10
        exit_price=50.0,
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(1000.0, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# Regression: cap must NOT alter pre-existing guarded paths.
# ─────────────────────────────────────────────────────────────────────────────


def test_negative_capital_still_zero_pnl():
    """capital ≤ 0 → effective_capital = 0 → risk_amount = 0 → pnl_usd = 0
    regardless of overshoot. Cap is moot when risk_amount is zero."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=50.0,       # ratio = -50, would clamp to -10 if capital > 0
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=-5000.0,       # capital is already negative
    )
    # effective_capital = max(0, -5000) = 0 → risk_amount = 0 → pnl_usd = 0
    assert trade["pnl_usd"] == 0.0


def test_zero_capital_still_zero_pnl():
    """capital == 0 → effective_capital = 0 → pnl_usd = 0. Cap is moot."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=50.0,
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=0.0,
    )
    assert trade["pnl_usd"] == 0.0


def test_inverted_sl_still_zero_pnl():
    """Inverted SL (sl_pct_actual ≤ 0 in else branch) → pnl_usd = 0 unchanged.
    Cap path is in the `if sl_pct_actual > 0` branch only; phantom-profit guard
    in the else branch is preserved byte-identical."""
    from backtest import _close_position

    position = _build_long_position()
    position["sl"] = 101.0           # ⚠ ABOVE entry — inverted (the pre-fix bug)
    position["sl_orig"] = 101.0
    trade = _close_position(
        position,
        exit_price=101.0,
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="SL",
        capital=10_000.0,
    )
    # Phantom-profit guard (else branch) returns pnl_usd = 0; cap not reached.
    assert trade["pnl_usd"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Constant invariant: K=10 is the rule-derived value lockedin spec discussion.
# Catches accidental tweak to a data-derived value (which would revive leakage).
# ─────────────────────────────────────────────────────────────────────────────


def test_max_overshoot_ratio_is_ten():
    """The cap value MUST stay at 10 (rule-derived). If this test fails, someone
    tuned K to data — that revives the same leakage pattern Caveat #1 fixes for
    ATR multipliers. Treat any change to MAX_OVERSHOOT_RATIO as requiring its
    own pre-registration with explicit external review."""
    from backtest import MAX_OVERSHOOT_RATIO

    assert MAX_OVERSHOOT_RATIO == 10, (
        f"MAX_OVERSHOOT_RATIO changed from rule-derived 10 to {MAX_OVERSHOOT_RATIO}. "
        f"Any change must be pre-registered (rule-derived only — never tuned to data)."
    )
