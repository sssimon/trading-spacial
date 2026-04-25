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
