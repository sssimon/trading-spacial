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


# ── B4b.2: should_skip_or_reduce composition ────────────────────────────────


def _basic_cfg():
    """Minimal config for simulator tests with all v2 thresholds defined."""
    return {"kill_switch": {"v2": {
        "aggressiveness": 50,
        "thresholds": {
            "portfolio_dd_reduced":     {"min": -0.08, "max": -0.03},
            "portfolio_dd_frozen":      {"min": -0.15, "max": -0.06},
            "velocity_sl_count":        {"min": 10, "max": 3},
            "velocity_window_hours":    {"min": 24, "max": 6},
            "baseline_sigma_multiplier": {"min": 3.0, "max": 1.0},
        },
        "velocity_cooldown_hours": 4,
        "concurrent_alert_threshold": 3,
        "baseline_min_trades": 100,
        "baseline_stale_days": 7,
        "regime_adjustments": {"bull_bonus": 10, "bear_penalty": 10},
        "advanced_overrides": {"regime_adjustment_enabled": True},
    }}}


def test_should_skip_or_reduce_empty_state_full_size():
    """Fresh sim, no trades, no regime → NORMAL/NORMAL/no velocity → (False, 1.0)."""
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator

    sim = V2KillSwitchSimulator(_basic_cfg(), regime_score=None, capital_base=1000.0)
    skip, factor = sim.should_skip_or_reduce(
        symbol="BTCUSDT", entry_ts="2026-04-25T12:00:00+00:00",
    )
    assert skip is False
    assert factor == pytest.approx(1.0)


def test_should_skip_or_reduce_velocity_active_skips():
    """Velocity cooldown active → (True, 0.0) regardless of other tiers."""
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator
    from datetime import datetime, timezone, timedelta

    sim = V2KillSwitchSimulator(_basic_cfg(), regime_score=None, capital_base=1000.0)
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    sim._velocity_state["BTCUSDT"] = {
        "velocity_cooldown_until": (now + timedelta(hours=2)).isoformat(),
        "velocity_last_trigger_ts": (now - timedelta(hours=2)).isoformat(),
    }
    skip, factor = sim.should_skip_or_reduce(
        symbol="BTCUSDT", entry_ts=now.isoformat(),
    )
    assert skip is True
    assert factor == pytest.approx(0.0)


def test_should_skip_or_reduce_portfolio_frozen_skips():
    """Portfolio FROZEN (DD <= frozen_threshold) → (True, 0.0)."""
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator

    cfg = _basic_cfg()
    sim = V2KillSwitchSimulator(cfg, regime_score=None, capital_base=1000.0)
    # Inject heavy losses to trigger FROZEN: at slider=50 → frozen_dd=-0.105
    # 200 USD loss on 1000 capital → -0.20 DD, well past frozen threshold.
    sim._all_trades.append(
        {"symbol": "X", "exit_ts": "2026-04-20T12:00:00+00:00",
         "pnl_usd": -200.0, "exit_reason": "SL"},
    )
    skip, factor = sim.should_skip_or_reduce(
        symbol="BTCUSDT", entry_ts="2026-04-25T12:00:00+00:00",
    )
    assert skip is True
    assert factor == pytest.approx(0.0)


def test_should_skip_or_reduce_portfolio_reduced_halves_size():
    """Portfolio REDUCED → factor 0.5 (not skipped)."""
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator

    cfg = _basic_cfg()
    sim = V2KillSwitchSimulator(cfg, regime_score=None, capital_base=1000.0)
    # Inject loss to trigger REDUCED: at slider=50 → reduced_dd=-0.055.
    # 70 USD loss on 1000 capital → -0.07 DD, between reduced and frozen thresholds.
    sim._all_trades.append(
        {"symbol": "X", "exit_ts": "2026-04-20T12:00:00+00:00",
         "pnl_usd": -70.0, "exit_reason": "TP"},
    )
    skip, factor = sim.should_skip_or_reduce(
        symbol="BTCUSDT", entry_ts="2026-04-25T12:00:00+00:00",
    )
    assert skip is False
    assert factor == pytest.approx(0.5)


def test_should_skip_or_reduce_per_symbol_alert_halves_size():
    """ALERT per_symbol (>=100 trades, rolling_wr below threshold) → factor 0.5.

    Seed data: 100 alternating +1/-1 trades (DD ~ 0, portfolio NORMAL),
    then 20 losses (-1 each) so rolling_wr_20=0 (per-symbol ALERT).
    Total: 50 wins + 70 losses → baseline wr=0.417, sigma~0.493.
    Threshold = 0.417 - 2*(0.493/sqrt(20)) ≈ 0.197. rolling_wr_20=0 → ALERT.
    Final: peak~1000, trough=980 → DD=-0.02 → NORMAL portfolio.
    Composition: NORMAL (1.0) × ALERT (0.5) × no velocity (1.0) = 0.5
    """
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator
    from datetime import datetime, timezone, timedelta

    cfg = _basic_cfg()
    sim = V2KillSwitchSimulator(cfg, regime_score=None, capital_base=1000.0)
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    # Phase 1: 100 alternating trades (+1, -1, +1, -1, ...) — keeps peak ~ 1000.
    for i in range(100):
        ts = (base + timedelta(hours=i)).isoformat()
        if i % 2 == 0:
            t = {"symbol": "BTCUSDT", "exit_ts": ts,
                 "pnl_usd": 1.0, "exit_reason": "TP"}
        else:
            t = {"symbol": "BTCUSDT", "exit_ts": ts,
                 "pnl_usd": -1.0, "exit_reason": "SL"}
        sim._all_trades.append(t)
        sim._symbol_trades.setdefault("BTCUSDT", []).append(t)
    # Phase 2: 20 losses (-1 each) → equity drops by 20 → DD = -20/1000 = -0.02 (NORMAL).
    for i in range(20):
        ts = (base + timedelta(hours=100 + i)).isoformat()
        t = {"symbol": "BTCUSDT", "exit_ts": ts,
             "pnl_usd": -1.0, "exit_reason": "SL"}
        sim._all_trades.append(t)
        sim._symbol_trades["BTCUSDT"].append(t)
    # Last 20 are all losses → rolling_wr_20 = 0
    # Baseline: 50 wins / 120 = 0.417, sigma = sqrt(0.417*0.583) ≈ 0.493
    # Threshold = 0.417 - 2*(0.493/sqrt(20)) ≈ 0.197 → 0 < 0.197 → ALERT
    from strategy.kill_switch_v2 import compute_baseline_metrics
    sim._baselines["BTCUSDT"] = compute_baseline_metrics(sim._symbol_trades["BTCUSDT"])

    skip, factor = sim.should_skip_or_reduce(
        symbol="BTCUSDT", entry_ts="2026-04-26T12:00:00+00:00",
    )
    # NORMAL portfolio (DD=-0.02 above reduced=-0.055), ALERT per-symbol → 0.5
    assert skip is False
    assert factor == pytest.approx(0.5)


def test_should_skip_or_reduce_composition_reduced_alert_quarter():
    """Portfolio REDUCED (0.5) × per_symbol ALERT (0.5) → 0.25 (multiplicative).

    Seed data: 100 alternating +1/-1 (peak ~ 1000), then 20 losses (-4 each)
    → final equity 920, DD = -80/1000 = -0.08 (REDUCED at slider=50).
    Last 20 losses make rolling_wr_20=0 → ALERT.
    """
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator
    from datetime import datetime, timezone, timedelta

    cfg = _basic_cfg()
    sim = V2KillSwitchSimulator(cfg, regime_score=None, capital_base=1000.0)
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    # Phase 1: 100 alternating trades — peak stays ~ 1000.
    for i in range(100):
        ts = (base + timedelta(hours=i)).isoformat()
        if i % 2 == 0:
            t = {"symbol": "BTCUSDT", "exit_ts": ts,
                 "pnl_usd": 1.0, "exit_reason": "TP"}
        else:
            t = {"symbol": "BTCUSDT", "exit_ts": ts,
                 "pnl_usd": -1.0, "exit_reason": "SL"}
        sim._all_trades.append(t)
        sim._symbol_trades.setdefault("BTCUSDT", []).append(t)
    # Phase 2: 20 losses at -4 each → drops 80 USD from equity.
    # Final equity ~ 920, peak ~ 1000, DD = -0.08 → REDUCED at slider=50.
    for i in range(20):
        ts = (base + timedelta(hours=100 + i)).isoformat()
        t = {"symbol": "BTCUSDT", "exit_ts": ts,
             "pnl_usd": -4.0, "exit_reason": "SL"}
        sim._all_trades.append(t)
        sim._symbol_trades["BTCUSDT"].append(t)
    from strategy.kill_switch_v2 import compute_baseline_metrics
    sim._baselines["BTCUSDT"] = compute_baseline_metrics(sim._symbol_trades["BTCUSDT"])

    skip, factor = sim.should_skip_or_reduce(
        symbol="BTCUSDT", entry_ts="2026-04-26T12:00:00+00:00",
    )
    # REDUCED portfolio (0.5) × ALERT per-symbol (0.5) × no velocity = 0.25
    assert skip is False
    assert factor == pytest.approx(0.25)


# ── B4b.2: on_trade_close updates state ─────────────────────────────────────


def test_on_trade_close_appends_trade_to_all_and_per_symbol():
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator

    sim = V2KillSwitchSimulator(_basic_cfg(), regime_score=None, capital_base=1000.0)
    sim.on_trade_close(
        symbol="BTCUSDT", exit_ts="2026-04-25T12:00:00+00:00",
        pnl_usd=10.0, exit_reason="TP",
    )
    assert len(sim._all_trades) == 1
    assert sim._all_trades[0]["pnl_usd"] == 10.0
    assert len(sim._symbol_trades["BTCUSDT"]) == 1


def test_on_trade_close_updates_baseline_for_symbol():
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator

    sim = V2KillSwitchSimulator(_basic_cfg(), regime_score=None, capital_base=1000.0)
    sim.on_trade_close(
        symbol="BTCUSDT", exit_ts="2026-04-25T12:00:00+00:00",
        pnl_usd=10.0, exit_reason="TP",
    )
    sim.on_trade_close(
        symbol="BTCUSDT", exit_ts="2026-04-25T13:00:00+00:00",
        pnl_usd=-5.0, exit_reason="SL",
    )
    # 1 win, 1 loss → wr=0.5, count=2
    assert sim._baselines["BTCUSDT"]["wr"] == pytest.approx(0.5)
    assert sim._baselines["BTCUSDT"]["count"] == 2


def test_on_trade_close_sl_triggers_velocity_when_threshold_met():
    """3 SLs within 6h at slider=100 → velocity cooldown set."""
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator

    cfg = _basic_cfg()
    cfg["kill_switch"]["v2"]["aggressiveness"] = 100  # paranoid: sl_count=3, window=6h
    sim = V2KillSwitchSimulator(cfg, regime_score=None, capital_base=1000.0)

    sim.on_trade_close(
        symbol="BTCUSDT", exit_ts="2026-04-25T10:00:00+00:00",
        pnl_usd=-5.0, exit_reason="SL",
    )
    sim.on_trade_close(
        symbol="BTCUSDT", exit_ts="2026-04-25T11:00:00+00:00",
        pnl_usd=-5.0, exit_reason="SL",
    )
    sim.on_trade_close(
        symbol="BTCUSDT", exit_ts="2026-04-25T12:00:00+00:00",
        pnl_usd=-5.0, exit_reason="SL",
    )
    # Cooldown should be set
    state = sim._velocity_state.get("BTCUSDT", {})
    assert state.get("velocity_cooldown_until") is not None


def test_on_trade_close_tp_does_not_set_velocity():
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator

    cfg = _basic_cfg()
    cfg["kill_switch"]["v2"]["aggressiveness"] = 100
    sim = V2KillSwitchSimulator(cfg, regime_score=None, capital_base=1000.0)
    # Three TPs (not SLs) → no velocity trigger regardless
    for i in range(3):
        sim.on_trade_close(
            symbol="BTCUSDT",
            exit_ts=f"2026-04-25T{10+i:02d}:00:00+00:00",
            pnl_usd=5.0, exit_reason="TP",
        )
    assert "BTCUSDT" not in sim._velocity_state or not sim._velocity_state["BTCUSDT"].get("velocity_cooldown_until")
