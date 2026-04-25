"""Tests for V2KillSwitchSimulator (#187 #216 B4b.2)."""
import pytest


# ── B4b.2: simulator skeleton + portfolio DD ────────────────────────────────


def test_simulator_init_applies_regime_adjustment_bull():
    """Construction with BULL regime_score adjusts cfg.aggressiveness via apply_regime_adjustment."""
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator

    cfg = {"kill_switch": {"v2": {
        "aggressiveness": 50,
        "regime_adjustments": {"bull_bonus": 10, "bear_penalty": 10},
        "advanced_overrides": {"regime_adjustment_enabled": True},
    }}}
    sim = V2KillSwitchSimulator(cfg, regime_score=75.0, capital_base=1000.0)
    assert sim.cfg_eff["kill_switch"]["v2"]["aggressiveness"] == 60


def test_simulator_init_no_regime_score_keeps_slider():
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator

    cfg = {"kill_switch": {"v2": {"aggressiveness": 50}}}
    sim = V2KillSwitchSimulator(cfg, regime_score=None, capital_base=1000.0)
    assert sim.cfg_eff["kill_switch"]["v2"]["aggressiveness"] == 50


def test_simulator_current_portfolio_dd_empty():
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator

    sim = V2KillSwitchSimulator({}, regime_score=None, capital_base=1000.0)
    assert sim._current_portfolio_dd() == pytest.approx(0.0)


def test_simulator_current_portfolio_dd_after_loss():
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator

    sim = V2KillSwitchSimulator({}, regime_score=None, capital_base=1000.0)
    sim._all_trades.append(
        {"symbol": "BTC", "exit_ts": "2026-04-20T12:00:00+00:00",
         "pnl_usd": -50.0, "exit_reason": "SL"},
    )
    assert sim._current_portfolio_dd() == pytest.approx(-0.05)


def test_simulator_current_portfolio_dd_peak_then_drop():
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator

    sim = V2KillSwitchSimulator({}, regime_score=None, capital_base=1000.0)
    sim._all_trades.append(
        {"symbol": "X", "exit_ts": "2026-04-20T12:00:00+00:00",
         "pnl_usd": 100.0, "exit_reason": "TP"},
    )
    sim._all_trades.append(
        {"symbol": "X", "exit_ts": "2026-04-21T12:00:00+00:00",
         "pnl_usd": 50.0, "exit_reason": "TP"},
    )
    sim._all_trades.append(
        {"symbol": "X", "exit_ts": "2026-04-22T12:00:00+00:00",
         "pnl_usd": -200.0, "exit_reason": "SL"},
    )
    expected = (950 - 1150) / 1150
    assert sim._current_portfolio_dd() == pytest.approx(expected)


# ── B4b.2: velocity active check ────────────────────────────────────────────


def test_simulator_velocity_active_no_state():
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator
    from datetime import datetime, timezone

    sim = V2KillSwitchSimulator({}, regime_score=None, capital_base=1000.0)
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    assert sim._is_velocity_active("BTC", now) is False


def test_simulator_velocity_active_during_cooldown():
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator
    from datetime import datetime, timezone, timedelta

    sim = V2KillSwitchSimulator({}, regime_score=None, capital_base=1000.0)
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    sim._velocity_state["BTC"] = {
        "velocity_cooldown_until": (now + timedelta(hours=2)).isoformat(),
        "velocity_last_trigger_ts": (now - timedelta(hours=2)).isoformat(),
    }
    assert sim._is_velocity_active("BTC", now) is True


def test_simulator_velocity_active_after_cooldown_expired():
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator
    from datetime import datetime, timezone, timedelta

    sim = V2KillSwitchSimulator({}, regime_score=None, capital_base=1000.0)
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    sim._velocity_state["BTC"] = {
        "velocity_cooldown_until": (now - timedelta(hours=1)).isoformat(),
        "velocity_last_trigger_ts": (now - timedelta(hours=5)).isoformat(),
    }
    assert sim._is_velocity_active("BTC", now) is False


def test_simulator_velocity_active_malformed_treated_inactive():
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator
    from datetime import datetime, timezone

    sim = V2KillSwitchSimulator({}, regime_score=None, capital_base=1000.0)
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    sim._velocity_state["BTC"] = {
        "velocity_cooldown_until": "garbage",
        "velocity_last_trigger_ts": None,
    }
    # Malformed → treat as not active (conservative for backtest replay)
    assert sim._is_velocity_active("BTC", now) is False


# ── B4b.2: concurrent failures count ────────────────────────────────────────


def test_simulator_concurrent_failures_empty():
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator
    from datetime import datetime, timezone

    sim = V2KillSwitchSimulator({}, regime_score=None, capital_base=1000.0)
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    assert sim._count_concurrent_failures(now) == 0


def test_simulator_concurrent_failures_counts_active_velocities():
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator
    from datetime import datetime, timezone, timedelta

    sim = V2KillSwitchSimulator({}, regime_score=None, capital_base=1000.0)
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    sim._velocity_state["BTC"] = {
        "velocity_cooldown_until": (now + timedelta(hours=1)).isoformat(),
        "velocity_last_trigger_ts": now.isoformat(),
    }
    sim._velocity_state["ETH"] = {
        "velocity_cooldown_until": (now + timedelta(hours=3)).isoformat(),
        "velocity_last_trigger_ts": now.isoformat(),
    }
    sim._velocity_state["ADA"] = {
        "velocity_cooldown_until": (now - timedelta(hours=1)).isoformat(),
        "velocity_last_trigger_ts": now.isoformat(),
    }
    assert sim._count_concurrent_failures(now) == 2
