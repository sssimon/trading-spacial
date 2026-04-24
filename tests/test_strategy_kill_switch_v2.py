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


def test_compute_portfolio_equity_curve_empty():
    from strategy.kill_switch_v2 import compute_portfolio_equity_curve
    curve = compute_portfolio_equity_curve(
        closed_trades=[],
        open_positions=[],
        capital_base=100_000.0,
        now_price_by_symbol={},
    )
    # Empty history — single snapshot at capital_base
    assert len(curve) == 1
    assert curve[0]["equity"] == pytest.approx(100_000.0)


def test_compute_portfolio_equity_curve_closed_trades_only():
    from strategy.kill_switch_v2 import compute_portfolio_equity_curve
    # 2 closed trades: +200, -50 → cumulative equity steps
    closed_trades = [
        {"symbol": "BTCUSDT", "exit_ts": "2026-04-20T12:00:00+00:00", "pnl_usd": 200.0},
        {"symbol": "ETHUSDT", "exit_ts": "2026-04-21T14:00:00+00:00", "pnl_usd": -50.0},
    ]
    curve = compute_portfolio_equity_curve(
        closed_trades=closed_trades,
        open_positions=[],
        capital_base=100_000.0,
        now_price_by_symbol={},
    )
    # 3 points: start, after trade 1, after trade 2
    assert len(curve) == 3
    assert curve[0]["equity"] == pytest.approx(100_000.0)
    assert curve[1]["equity"] == pytest.approx(100_200.0)
    assert curve[2]["equity"] == pytest.approx(100_150.0)


def test_compute_portfolio_equity_curve_open_positions_mtm():
    """Open positions add an MTM point at the end using now_price_by_symbol."""
    from strategy.kill_switch_v2 import compute_portfolio_equity_curve
    # 1 closed trade (+100), 1 open position entered at $50k, now $51k with 0.01 qty
    closed_trades = [
        {"symbol": "BTCUSDT", "exit_ts": "2026-04-20T12:00:00+00:00", "pnl_usd": 100.0},
    ]
    open_positions = [
        {
            "symbol": "BTCUSDT",
            "entry_price": 50_000.0,
            "qty": 0.01,
            "direction": "LONG",
        },
    ]
    now_prices = {"BTCUSDT": 51_000.0}
    curve = compute_portfolio_equity_curve(
        closed_trades=closed_trades,
        open_positions=open_positions,
        capital_base=100_000.0,
        now_price_by_symbol=now_prices,
    )
    # Start 100k → after trade +100 → +MTM of (51k-50k)*0.01 = 10
    # 3 points: [100_000, 100_100, 100_110]
    assert len(curve) == 3
    assert curve[-1]["equity"] == pytest.approx(100_110.0)


def test_compute_portfolio_equity_curve_short_mtm():
    """SHORT position MTM is (entry - current) * qty."""
    from strategy.kill_switch_v2 import compute_portfolio_equity_curve
    open_positions = [
        {
            "symbol": "ETHUSDT",
            "entry_price": 3_000.0,
            "qty": 1.0,
            "direction": "SHORT",
        },
    ]
    now_prices = {"ETHUSDT": 2_950.0}
    curve = compute_portfolio_equity_curve(
        closed_trades=[],
        open_positions=open_positions,
        capital_base=10_000.0,
        now_price_by_symbol=now_prices,
    )
    # SHORT won (+50 per coin × 1 coin = +50)
    # 2 points: start, end
    assert curve[-1]["equity"] == pytest.approx(10_050.0)


def test_compute_portfolio_equity_curve_missing_price_skips_mtm():
    """If now_price_by_symbol is missing the open position's symbol, skip MTM for it."""
    from strategy.kill_switch_v2 import compute_portfolio_equity_curve
    open_positions = [
        {
            "symbol": "UNKNOWNUSDT",
            "entry_price": 1.0,
            "qty": 100.0,
            "direction": "LONG",
        },
    ]
    now_prices = {}  # empty
    curve = compute_portfolio_equity_curve(
        closed_trades=[],
        open_positions=open_positions,
        capital_base=100_000.0,
        now_price_by_symbol=now_prices,
    )
    # Only the start point remains (no MTM applied)
    assert len(curve) == 1
    assert curve[0]["equity"] == pytest.approx(100_000.0)
