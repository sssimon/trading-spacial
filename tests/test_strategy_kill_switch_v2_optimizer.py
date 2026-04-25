"""Tests for run_optimization_v2 + helpers (#187 #216 B4b.2)."""
import pytest


# ── B4b.2: optimizer helpers ────────────────────────────────────────────────


def test_load_closed_positions_window_filters_by_window(tmp_path, monkeypatch):
    import btc_api
    from strategy.kill_switch_v2_optimizer import _load_closed_positions_window
    from datetime import datetime, timezone, timedelta

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    inside = (now - timedelta(days=10)).isoformat()
    outside = (now - timedelta(days=400)).isoformat()

    conn = btc_api.get_db()
    try:
        # Inside window (10d ago)
        conn.execute(
            "INSERT INTO positions(symbol, direction, entry_price, qty, status, "
            "entry_ts, exit_ts, exit_reason, pnl_usd) VALUES "
            "('BTCUSDT', 'LONG', 50000, 0.01, 'closed', ?, ?, 'TP', 10.0)",
            (inside, inside),
        )
        # Outside window (400d ago)
        conn.execute(
            "INSERT INTO positions(symbol, direction, entry_price, qty, status, "
            "entry_ts, exit_ts, exit_reason, pnl_usd) VALUES "
            "('BTCUSDT', 'LONG', 50000, 0.01, 'closed', ?, ?, 'TP', 10.0)",
            (outside, outside),
        )
        # Open position (no exit_ts)
        conn.execute(
            "INSERT INTO positions(symbol, direction, entry_price, qty, status, "
            "entry_ts) VALUES ('BTCUSDT', 'LONG', 50000, 0.01, 'open', ?)",
            (inside,),
        )
        conn.commit()
    finally:
        conn.close()

    rows = _load_closed_positions_window(window_days=365.0, now=now)
    assert len(rows) == 1
    assert rows[0]["pnl_usd"] == 10.0
    assert rows[0]["exit_reason"] == "TP"


def test_load_closed_positions_window_orders_by_entry_ts(tmp_path, monkeypatch):
    import btc_api
    from strategy.kill_switch_v2_optimizer import _load_closed_positions_window
    from datetime import datetime, timezone, timedelta

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    earlier = (now - timedelta(days=20)).isoformat()
    later = (now - timedelta(days=10)).isoformat()

    conn = btc_api.get_db()
    try:
        # Insert later first (out of order)
        conn.execute(
            "INSERT INTO positions(symbol, direction, entry_price, qty, status, "
            "entry_ts, exit_ts, exit_reason, pnl_usd) VALUES "
            "('BTCUSDT', 'LONG', 50000, 0.01, 'closed', ?, ?, 'TP', 10.0)",
            (later, later),
        )
        conn.execute(
            "INSERT INTO positions(symbol, direction, entry_price, qty, status, "
            "entry_ts, exit_ts, exit_reason, pnl_usd) VALUES "
            "('BTCUSDT', 'LONG', 50000, 0.01, 'closed', ?, ?, 'SL', -5.0)",
            (earlier, earlier),
        )
        conn.commit()
    finally:
        conn.close()

    rows = _load_closed_positions_window(window_days=365.0, now=now)
    assert len(rows) == 2
    assert rows[0]["entry_ts"] == earlier
    assert rows[1]["entry_ts"] == later


def test_override_slider_returns_new_dict_with_slider_set():
    from strategy.kill_switch_v2_optimizer import _override_slider

    cfg = {"kill_switch": {"v2": {"aggressiveness": 50}}}
    result = _override_slider(cfg, 75)
    assert result["kill_switch"]["v2"]["aggressiveness"] == 75
    # Original unchanged
    assert cfg["kill_switch"]["v2"]["aggressiveness"] == 50


def test_override_slider_creates_v2_block_when_missing():
    from strategy.kill_switch_v2_optimizer import _override_slider

    result = _override_slider({}, 80)
    assert result["kill_switch"]["v2"]["aggressiveness"] == 80


# ── B4b.2: _replay_with_slider ──────────────────────────────────────────────


def _basic_optimizer_cfg():
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


def test_replay_with_slider_empty_trades():
    from strategy.kill_switch_v2_optimizer import _replay_with_slider

    result = _replay_with_slider(
        closed_trades=[], cfg_with_slider=_basic_optimizer_cfg(),
        regime_score=None, capital_base=1000.0,
    )
    assert result == {"pnl": pytest.approx(0.0), "dd": pytest.approx(0.0)}


def test_replay_with_slider_single_winning_trade():
    """One profitable trade; v2 takes it at full size → pnl > 0, dd ≈ 0."""
    from strategy.kill_switch_v2_optimizer import _replay_with_slider

    trades = [{
        "symbol": "BTCUSDT",
        "entry_ts": "2026-04-20T10:00:00+00:00",
        "exit_ts": "2026-04-20T12:00:00+00:00",
        "exit_reason": "TP",
        "pnl_usd": 20.0,
    }]
    result = _replay_with_slider(
        closed_trades=trades, cfg_with_slider=_basic_optimizer_cfg(),
        regime_score=None, capital_base=1000.0,
    )
    assert result["pnl"] == pytest.approx(20.0)
    assert result["dd"] == pytest.approx(0.0)


def test_replay_with_slider_single_losing_trade():
    """One losing trade; v2 takes it at full size → pnl < 0, dd < 0."""
    from strategy.kill_switch_v2_optimizer import _replay_with_slider

    trades = [{
        "symbol": "BTCUSDT",
        "entry_ts": "2026-04-20T10:00:00+00:00",
        "exit_ts": "2026-04-20T12:00:00+00:00",
        "exit_reason": "SL",
        "pnl_usd": -30.0,
    }]
    result = _replay_with_slider(
        closed_trades=trades, cfg_with_slider=_basic_optimizer_cfg(),
        regime_score=None, capital_base=1000.0,
    )
    assert result["pnl"] == pytest.approx(-30.0)
    assert result["dd"] == pytest.approx(-0.03)


def test_replay_with_slider_peak_then_drawdown():
    """+50, +30, -100 → equity 1000→1050→1080→980, peak=1080, dd=(980-1080)/1080."""
    from strategy.kill_switch_v2_optimizer import _replay_with_slider

    trades = [
        {"symbol": "X", "entry_ts": "2026-04-20T10:00:00+00:00",
         "exit_ts": "2026-04-20T11:00:00+00:00", "exit_reason": "TP", "pnl_usd": 50.0},
        {"symbol": "X", "entry_ts": "2026-04-21T10:00:00+00:00",
         "exit_ts": "2026-04-21T11:00:00+00:00", "exit_reason": "TP", "pnl_usd": 30.0},
        {"symbol": "X", "entry_ts": "2026-04-22T10:00:00+00:00",
         "exit_ts": "2026-04-22T11:00:00+00:00", "exit_reason": "SL", "pnl_usd": -100.0},
    ]
    result = _replay_with_slider(
        closed_trades=trades, cfg_with_slider=_basic_optimizer_cfg(),
        regime_score=None, capital_base=1000.0,
    )
    assert result["pnl"] == pytest.approx(-20.0)
    expected_dd = (980 - 1080) / 1080
    assert result["dd"] == pytest.approx(expected_dd)
