"""Tests for strategy.kill_switch_v2 — portfolio circuit breaker (#187 B2)."""
import pytest


def test_interpolate_threshold_at_slider_0():
    from strategy.kill_switch_v2 import interpolate_threshold
    # slider=0 → t_min
    assert interpolate_threshold(0, t_min=-0.08, t_max=-0.03) == pytest.approx(-0.08)


def test_interpolate_threshold_at_slider_100():
    from strategy.kill_switch_v2 import interpolate_threshold
    # slider=100 → t_max (more strict)
    assert interpolate_threshold(100, t_min=-0.08, t_max=-0.03) == pytest.approx(-0.03)


def test_interpolate_threshold_at_slider_50():
    from strategy.kill_switch_v2 import interpolate_threshold
    # slider=50 → midpoint
    assert interpolate_threshold(50, t_min=-0.08, t_max=-0.03) == pytest.approx(-0.055)


def test_interpolate_threshold_linear():
    from strategy.kill_switch_v2 import interpolate_threshold
    # slider=25 → 25% of the way
    assert interpolate_threshold(25, t_min=0.0, t_max=100.0) == pytest.approx(25.0)


def test_get_thresholds_from_config_default_aggressiveness():
    from strategy.kill_switch_v2 import get_portfolio_thresholds
    cfg = {
        "kill_switch": {
            "v2": {
                "aggressiveness": 50,
                "thresholds": {
                    "portfolio_dd_reduced": {"min": -0.08, "max": -0.03},
                    "portfolio_dd_frozen": {"min": -0.15, "max": -0.06},
                },
            },
        },
    }
    thresholds = get_portfolio_thresholds(cfg)
    assert thresholds["reduced_dd"] == pytest.approx(-0.055)
    assert thresholds["frozen_dd"] == pytest.approx(-0.105)


def test_get_thresholds_from_config_aggressiveness_0():
    from strategy.kill_switch_v2 import get_portfolio_thresholds
    cfg = {
        "kill_switch": {
            "v2": {
                "aggressiveness": 0,
                "thresholds": {
                    "portfolio_dd_reduced": {"min": -0.08, "max": -0.03},
                    "portfolio_dd_frozen": {"min": -0.15, "max": -0.06},
                },
            },
        },
    }
    thresholds = get_portfolio_thresholds(cfg)
    assert thresholds["reduced_dd"] == pytest.approx(-0.08)
    assert thresholds["frozen_dd"] == pytest.approx(-0.15)


def test_get_thresholds_missing_config_returns_defaults():
    from strategy.kill_switch_v2 import get_portfolio_thresholds
    # No v2 config present — should return sensible defaults (slider=50)
    thresholds = get_portfolio_thresholds({})
    # With defaults t_min=-0.08/-0.15 t_max=-0.03/-0.06 and slider=50
    assert thresholds["reduced_dd"] == pytest.approx(-0.055)
    assert thresholds["frozen_dd"] == pytest.approx(-0.105)
