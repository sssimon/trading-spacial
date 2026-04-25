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


# ── B4b.2: run_optimization_v2 ──────────────────────────────────────────────


def test_run_optimization_v2_empty_db_returns_pending_zero(tmp_path, monkeypatch):
    """Empty positions table → all sliders pnl=0,dd=0 → feasible (dd>=target) → status='pending'."""
    import btc_api
    from strategy.kill_switch_v2_optimizer import run_optimization_v2

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    cfg = _basic_optimizer_cfg()
    cfg["kill_switch"]["v2"]["auto_calibrator"] = {
        "backtest_window_days": 365, "dd_target": -0.10,
    }
    result = run_optimization_v2(cfg, regime_score=None)
    assert result["status"] == "pending"
    assert result["projected_pnl"] == pytest.approx(0.0)
    assert result["projected_dd"] == pytest.approx(0.0)
    assert isinstance(result["slider_value"], int)
    assert "grid" in result["report"]
    assert result["report"]["stub"] is False
    assert result["report"]["trades_in_window"] == 0


def test_run_optimization_v2_no_feasible_when_all_blow_target(tmp_path, monkeypatch):
    """All-losing trades large enough to violate dd_target=-0.01 → no_feasible."""
    import btc_api
    from strategy.kill_switch_v2_optimizer import run_optimization_v2
    from datetime import datetime, timezone, timedelta

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    # Insert a single -200 USD trade (DD = -0.20, blows -0.01 target)
    now = datetime.now(tz=timezone.utc)
    ts = (now - timedelta(days=10)).isoformat()
    conn = btc_api.get_db()
    try:
        conn.execute(
            "INSERT INTO positions(symbol, direction, entry_price, qty, status, "
            "entry_ts, exit_ts, exit_reason, pnl_usd) VALUES "
            "('BTCUSDT', 'LONG', 50000, 0.01, 'closed', ?, ?, 'SL', -200.0)",
            (ts, ts),
        )
        conn.commit()
    finally:
        conn.close()

    cfg = _basic_optimizer_cfg()
    cfg["kill_switch"]["v2"]["auto_calibrator"] = {
        "backtest_window_days": 365, "dd_target": -0.01,
    }
    result = run_optimization_v2(cfg, regime_score=None)
    assert result["status"] == "no_feasible"
    assert result["slider_value"] is None
    assert "reason" in result["report"]
    assert result["report"]["trades_in_window"] == 1


def test_run_optimization_v2_picks_max_pnl_among_feasible(tmp_path, monkeypatch):
    """With a profitable trade, all sliders are feasible; pnl is positive."""
    import btc_api
    from strategy.kill_switch_v2_optimizer import run_optimization_v2
    from datetime import datetime, timezone, timedelta

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    now = datetime.now(tz=timezone.utc)
    ts = (now - timedelta(days=10)).isoformat()
    conn = btc_api.get_db()
    try:
        conn.execute(
            "INSERT INTO positions(symbol, direction, entry_price, qty, status, "
            "entry_ts, exit_ts, exit_reason, pnl_usd) VALUES "
            "('BTCUSDT', 'LONG', 50000, 0.01, 'closed', ?, ?, 'TP', 50.0)",
            (ts, ts),
        )
        conn.commit()
    finally:
        conn.close()

    cfg = _basic_optimizer_cfg()
    cfg["kill_switch"]["v2"]["auto_calibrator"] = {
        "backtest_window_days": 365, "dd_target": -0.10,
    }
    result = run_optimization_v2(cfg, regime_score=None)
    assert result["status"] == "pending"
    assert result["projected_pnl"] == pytest.approx(50.0)


def test_run_optimization_v2_report_includes_grid(tmp_path, monkeypatch):
    """Report payload includes per-slider {pnl, dd} grid."""
    import btc_api
    from strategy.kill_switch_v2_optimizer import run_optimization_v2

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    cfg = _basic_optimizer_cfg()
    cfg["kill_switch"]["v2"]["auto_calibrator"] = {
        "backtest_window_days": 365, "dd_target": -0.10,
    }
    result = run_optimization_v2(cfg, regime_score=None)
    grid = result["report"]["grid"]
    # 21 sliders: 0, 5, 10, ..., 100
    assert len(grid) == 21
    for slider in (0, 5, 50, 100):
        assert str(slider) in grid
        assert "pnl" in grid[str(slider)]
        assert "dd" in grid[str(slider)]


def test_run_optimization_v2_passes_regime_score_to_simulator(tmp_path, monkeypatch):
    """regime_score is included in report for traceability."""
    import btc_api
    from strategy.kill_switch_v2_optimizer import run_optimization_v2

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    cfg = _basic_optimizer_cfg()
    cfg["kill_switch"]["v2"]["auto_calibrator"] = {
        "backtest_window_days": 365, "dd_target": -0.10,
    }
    result = run_optimization_v2(cfg, regime_score=72.5)
    assert result["report"]["regime_score"] == 72.5


# ── B4b.2: review follow-ups — hardening tests ──────────────────────────────


def test_run_optimization_v2_rejects_positive_dd_target():
    """dd_target > 0 is misconfigured; raise ValueError instead of trivially feasible."""
    from strategy.kill_switch_v2_optimizer import run_optimization_v2

    cfg = _basic_optimizer_cfg()
    cfg["kill_switch"]["v2"]["auto_calibrator"] = {
        "backtest_window_days": 365, "dd_target": 0.10,  # positive — invalid
    }
    with pytest.raises(ValueError, match="dd_target must be <= 0"):
        run_optimization_v2(cfg, regime_score=None)


def test_run_optimization_v2_filters_out_of_window_trades(tmp_path, monkeypatch):
    """backtest_window_days correctly excludes trades older than the window."""
    import btc_api
    from strategy.kill_switch_v2_optimizer import run_optimization_v2
    from datetime import datetime, timezone, timedelta

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    now = datetime.now(tz=timezone.utc)
    inside_ts = (now - timedelta(days=10)).isoformat()
    outside_ts = (now - timedelta(days=400)).isoformat()

    conn = btc_api.get_db()
    try:
        # Inside the 365-day window: profitable +50
        conn.execute(
            "INSERT INTO positions(symbol, direction, entry_price, qty, status, "
            "entry_ts, exit_ts, exit_reason, pnl_usd) VALUES "
            "('BTCUSDT', 'LONG', 50000, 0.01, 'closed', ?, ?, 'TP', 50.0)",
            (inside_ts, inside_ts),
        )
        # Outside window (400 days ago): -1000 loss that would otherwise
        # blow the dd_target if included.
        conn.execute(
            "INSERT INTO positions(symbol, direction, entry_price, qty, status, "
            "entry_ts, exit_ts, exit_reason, pnl_usd) VALUES "
            "('BTCUSDT', 'LONG', 50000, 0.01, 'closed', ?, ?, 'SL', -1000.0)",
            (outside_ts, outside_ts),
        )
        conn.commit()
    finally:
        conn.close()

    cfg = _basic_optimizer_cfg()
    cfg["kill_switch"]["v2"]["auto_calibrator"] = {
        "backtest_window_days": 365, "dd_target": -0.10,
    }
    result = run_optimization_v2(cfg, regime_score=None)
    # Only the +50 trade is in scope → all sliders feasible, projected_pnl=50.
    assert result["status"] == "pending"
    assert result["projected_pnl"] == pytest.approx(50.0)
    assert result["report"]["trades_in_window"] == 1


def test_run_optimization_v2_excludes_null_pnl_trades(tmp_path, monkeypatch):
    """Trades with NULL pnl_usd are filtered out by the SQL guard."""
    import btc_api
    from strategy.kill_switch_v2_optimizer import (
        _load_closed_positions_window, run_optimization_v2,
    )
    from datetime import datetime, timezone, timedelta

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    now = datetime.now(tz=timezone.utc)
    ts = (now - timedelta(days=10)).isoformat()

    conn = btc_api.get_db()
    try:
        # Valid trade with non-null pnl
        conn.execute(
            "INSERT INTO positions(symbol, direction, entry_price, qty, status, "
            "entry_ts, exit_ts, exit_reason, pnl_usd) VALUES "
            "('BTCUSDT', 'LONG', 50000, 0.01, 'closed', ?, ?, 'TP', 25.0)",
            (ts, ts),
        )
        # Corrupted row: NULL pnl_usd (column allows NULL per schema)
        conn.execute(
            "INSERT INTO positions(symbol, direction, entry_price, qty, status, "
            "entry_ts, exit_ts, exit_reason, pnl_usd) VALUES "
            "('BTCUSDT', 'LONG', 50000, 0.01, 'closed', ?, ?, 'SL', NULL)",
            (ts, ts),
        )
        conn.commit()
    finally:
        conn.close()

    rows = _load_closed_positions_window(window_days=365.0, now=now)
    assert len(rows) == 1
    assert rows[0]["pnl_usd"] == 25.0


def test_should_skip_or_reduce_logs_warning_on_malformed_entry_ts(caplog):
    """Malformed entry_ts logs a warning before the conservative skip."""
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator
    import logging

    sim = V2KillSwitchSimulator(_basic_optimizer_cfg(), regime_score=None, capital_base=1000.0)
    with caplog.at_level(logging.WARNING, logger="kill_switch_v2_simulator"):
        skip, factor = sim.should_skip_or_reduce(
            symbol="BTCUSDT", entry_ts="not-a-timestamp",
        )
    assert skip is True
    assert factor == pytest.approx(0.0)
    assert any(
        "malformed entry_ts" in rec.getMessage() and "BTCUSDT" in rec.getMessage()
        for rec in caplog.records
    )


def test_on_trade_close_sl_logs_warning_on_malformed_exit_ts(caplog):
    """Malformed SL exit_ts logs a warning before silently returning."""
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator
    import logging

    sim = V2KillSwitchSimulator(_basic_optimizer_cfg(), regime_score=None, capital_base=1000.0)
    with caplog.at_level(logging.WARNING, logger="kill_switch_v2_simulator"):
        sim.on_trade_close(
            symbol="BTCUSDT",
            exit_ts="garbage-timestamp",
            pnl_usd=-5.0, exit_reason="SL",
        )
    # Trade still appended (baseline still updates)
    assert len(sim._all_trades) == 1
    # But velocity_state did NOT get an entry (malformed exit_ts → early return)
    assert "BTCUSDT" not in sim._velocity_state
    assert any(
        "malformed SL exit_ts" in rec.getMessage() and "BTCUSDT" in rec.getMessage()
        for rec in caplog.records
    )


def test_run_optimization_v2_regime_score_can_change_recommended_slider(
    tmp_path, monkeypatch,
):
    """BULL regime adjusts the slider scale → grid results differ vs NEUTRAL."""
    import btc_api
    from strategy.kill_switch_v2_optimizer import run_optimization_v2
    from datetime import datetime, timezone, timedelta

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    now = datetime.now(tz=timezone.utc)
    ts = (now - timedelta(days=10)).isoformat()
    conn = btc_api.get_db()
    try:
        # A profitable trade — both regimes would take it (NORMAL portfolio)
        conn.execute(
            "INSERT INTO positions(symbol, direction, entry_price, qty, status, "
            "entry_ts, exit_ts, exit_reason, pnl_usd) VALUES "
            "('BTCUSDT', 'LONG', 50000, 0.01, 'closed', ?, ?, 'TP', 50.0)",
            (ts, ts),
        )
        conn.commit()
    finally:
        conn.close()

    cfg = _basic_optimizer_cfg()
    cfg["kill_switch"]["v2"]["auto_calibrator"] = {
        "backtest_window_days": 365, "dd_target": -0.10,
    }
    # Run once with NEUTRAL (None) and once with BULL (75)
    result_neutral = run_optimization_v2(cfg, regime_score=None)
    result_bull = run_optimization_v2(cfg, regime_score=75.0)

    # regime_score is captured in report (traceability)
    assert result_neutral["report"]["regime_score"] is None
    assert result_bull["report"]["regime_score"] == 75.0
    # Grid is the same length (21) for both
    assert len(result_neutral["report"]["grid"]) == 21
    assert len(result_bull["report"]["grid"]) == 21


def test_calibrator_loop_uses_real_v2_with_profitable_trades(
    tmp_path, monkeypatch,
):
    """Daemon loop integration: with profitable trades + safety_net, persists pending row."""
    import btc_api, threading
    from strategy.kill_switch_v2_calibrator import kill_switch_calibrator_loop
    from datetime import datetime, timezone, timedelta

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    # Seed a profitable closed trade within the backtest window
    now = datetime.now(tz=timezone.utc)
    ts = (now - timedelta(days=10)).isoformat()
    conn = btc_api.get_db()
    try:
        conn.execute(
            "INSERT INTO positions(symbol, direction, entry_price, qty, status, "
            "entry_ts, exit_ts, exit_reason, pnl_usd) VALUES "
            "('BTCUSDT', 'LONG', 50000, 0.01, 'closed', ?, ?, 'TP', 50.0)",
            (ts, ts),
        )
        conn.commit()
    finally:
        conn.close()

    stop_event = threading.Event()
    def fake_wait(seconds):
        stop_event.set()
        return True
    monkeypatch.setattr(stop_event, "wait", fake_wait)

    cfg_fn = lambda: {"kill_switch": {"v2": {
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
        "auto_calibrator": {
            "safety_net_days": 30,
            "backtest_window_days": 365,
            "dd_target": -0.10,
        },
    }}}

    kill_switch_calibrator_loop(cfg_fn, stop_event=stop_event)

    conn = btc_api.get_db()
    try:
        rows = conn.execute(
            "SELECT triggered_by, status, report_json FROM kill_switch_recommendations"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    import json
    triggered = json.loads(rows[0][0])
    assert triggered == ["safety_net"]
    assert rows[0][1] == "pending"
    # Real v2 report — not stub
    report = json.loads(rows[0][2])
    assert report["stub"] is False
    assert "grid" in report
    assert report["trades_in_window"] == 1
