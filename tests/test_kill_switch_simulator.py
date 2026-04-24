"""Tests for the in-memory KillSwitchSimulator (#186 A6)."""
from datetime import datetime, timezone, timedelta

import pytest  # noqa: F401 — kept for future parametrize/fixtures


def _cfg():
    """Minimal kill-switch config that makes the state machine move with < 20 trades.

    min_trades_for_eval is the gate the state machine opens only after enough trades
    have been observed; lowering it to 10 lets the test fire transitions quickly.
    """
    return {
        "kill_switch": {
            "enabled": True,
            "min_trades_for_eval": 10,
            "alert_win_rate_threshold": 0.30,
            "reduce_pnl_window_days": 14,
            "reduce_size_factor": 0.5,
            "pause_months_consecutive": 2,
            "auto_recovery_enabled": True,
        },
    }


def test_simulator_starts_normal():
    from backtest_kill_switch import KillSwitchSimulator
    sim = KillSwitchSimulator(_cfg())
    assert sim.get_tier("BTCUSDT") == "NORMAL"


def test_simulator_closed_trade_updates_state():
    """A single closed trade below the min_trades gate holds NORMAL (insufficient_data)."""
    from backtest_kill_switch import KillSwitchSimulator
    sim = KillSwitchSimulator(_cfg())
    now = datetime(2026, 4, 23, tzinfo=timezone.utc)
    tier = sim.on_trade_close(
        symbol="BTCUSDT",
        exit_ts_iso="2026-04-20T12:00:00+00:00",
        pnl_usd=100.0,
        now=now,
    )
    assert tier == "NORMAL"


def test_simulator_many_losses_trigger_degraded_tier():
    """15 losses in a row should trip the state machine past NORMAL.

    With min_trades_for_eval=10, after 10+ trades the rules activate. All trades
    are losers, so pnl_30d < 0 and win rate = 0 → either REDUCED or ALERT.
    """
    from backtest_kill_switch import KillSwitchSimulator
    sim = KillSwitchSimulator(_cfg())
    now = datetime(2026, 4, 23, tzinfo=timezone.utc)
    for i in range(15):
        ts = (now - timedelta(days=14 - i)).isoformat()
        sim.on_trade_close("ETHUSDT", ts, -50.0, now)
    assert sim.get_tier("ETHUSDT") in ("ALERT", "REDUCED", "PAUSED")


def test_simulator_shared_across_symbols_independent():
    """Each symbol's state is tracked independently — a bad ETHUSDT doesn't
    poison BTCUSDT."""
    from backtest_kill_switch import KillSwitchSimulator
    sim = KillSwitchSimulator(_cfg())
    now = datetime(2026, 4, 23, tzinfo=timezone.utc)
    for i in range(15):
        sim.on_trade_close(
            "ETHUSDT", (now - timedelta(days=14 - i)).isoformat(), -50.0, now,
        )
    # BTCUSDT has a single winning trade — it stays NORMAL.
    sim.on_trade_close("BTCUSDT", now.isoformat(), 100.0, now)
    assert sim.get_tier("ETHUSDT") in ("ALERT", "REDUCED", "PAUSED")
    assert sim.get_tier("BTCUSDT") == "NORMAL"
