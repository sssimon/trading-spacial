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
