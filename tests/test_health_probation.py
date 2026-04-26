"""B5 PROBATION tier — pure functions + state machine + DB lifecycle (#187 #199)."""
import pytest


# ── compute_probation_trades_remaining ──────────────────────────────────────


def test_compute_probation_trades_remaining_zero_days():
    """No days in PAUSED → trades_base unchanged."""
    from health import compute_probation_trades_remaining
    assert compute_probation_trades_remaining(0, trades_base=10, per_pause_day=0.2) == 10


def test_compute_probation_trades_remaining_negative_days_clamps():
    """Negative days_paused (clock skew, etc.) → trades_base."""
    from health import compute_probation_trades_remaining
    assert compute_probation_trades_remaining(-3, trades_base=10, per_pause_day=0.2) == 10


def test_compute_probation_trades_remaining_seven_days():
    """7 days * 0.2 = 1.4 → rounds to 11 (10 + 1)."""
    from health import compute_probation_trades_remaining
    assert compute_probation_trades_remaining(7, trades_base=10, per_pause_day=0.2) == 11


def test_compute_probation_trades_remaining_fifteen_days():
    """15 days * 0.2 = 3 → 13 (spec example)."""
    from health import compute_probation_trades_remaining
    assert compute_probation_trades_remaining(15, trades_base=10, per_pause_day=0.2) == 13


def test_compute_probation_trades_remaining_thirty_days():
    """30 days * 0.2 = 6 → 16."""
    from health import compute_probation_trades_remaining
    assert compute_probation_trades_remaining(30, trades_base=10, per_pause_day=0.2) == 16


def test_compute_probation_trades_remaining_default_args():
    """Defaults match spec: trades_base=10, per_pause_day=0.2."""
    from health import compute_probation_trades_remaining
    assert compute_probation_trades_remaining(15) == 13
