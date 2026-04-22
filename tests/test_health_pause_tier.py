"""PAUSED tier integration (#138 PR 4):
- scan() early-returns for PAUSED symbols (no signal, no webhook).
- health.evaluate_and_record emits notify(HealthEvent) on transition to PAUSED.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd
import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    yield db_path


def _insert_closed(conn, symbol, pnl, exit_ts):
    conn.execute(
        """INSERT INTO positions
           (symbol, direction, status, entry_price, entry_ts,
            exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct)
           VALUES (?, 'LONG', 'closed', 100.0, ?, 101.0, ?, 'TP', ?, ?)""",
        (symbol, exit_ts, exit_ts, pnl, pnl / 100.0),
    )
    conn.commit()


CFG = {"kill_switch": {
    "enabled": True, "min_trades_for_eval": 20,
    "alert_win_rate_threshold": 0.15,
    "reduce_pnl_window_days": 30, "reduce_size_factor": 0.5,
    "pause_months_consecutive": 3, "auto_recovery_enabled": True,
}}
NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def test_transition_to_paused_fires_notify(tmp_db):
    """Health #138 PR 4: transition to PAUSED must fire notify(HealthEvent)."""
    from health import evaluate_and_record
    import btc_api

    conn = btc_api.get_db()
    try:
        # 3 full prior months negative + enough trades → PAUSED
        _insert_closed(conn, "JUP", -100.0, "2026-05-10T12:00:00+00:00")
        _insert_closed(conn, "JUP", -100.0, "2026-04-15T12:00:00+00:00")
        _insert_closed(conn, "JUP", -100.0, "2026-03-20T12:00:00+00:00")
        for i in range(22):
            _insert_closed(conn, "JUP", -10.0, (NOW - timedelta(days=40 + i)).isoformat())
    finally:
        conn.close()

    with patch("health.notify") as mock_notify:
        state = evaluate_and_record("JUP", CFG, now=NOW)

    assert state == "PAUSED"
    assert mock_notify.call_count == 1
    event_arg = mock_notify.call_args.args[0]
    assert event_arg.to_state == "PAUSED"
    assert event_arg.reason == "3mo_consec_neg"


def test_paused_no_renotify_when_state_unchanged(tmp_db):
    """Idempotence: a second eval on stable PAUSED does not re-fire."""
    from health import evaluate_and_record
    import btc_api

    conn = btc_api.get_db()
    try:
        _insert_closed(conn, "JUP", -100.0, "2026-05-10T12:00:00+00:00")
        _insert_closed(conn, "JUP", -100.0, "2026-04-15T12:00:00+00:00")
        _insert_closed(conn, "JUP", -100.0, "2026-03-20T12:00:00+00:00")
        for i in range(22):
            _insert_closed(conn, "JUP", -10.0, (NOW - timedelta(days=40 + i)).isoformat())
    finally:
        conn.close()

    with patch("health.notify") as mock_notify:
        evaluate_and_record("JUP", CFG, now=NOW)
        evaluate_and_record("JUP", CFG, now=NOW)

    assert mock_notify.call_count == 1


def _mini_bars(n=210):
    import numpy as np
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    idx1h = [start + timedelta(hours=i) for i in range(n)]
    close = [85000.0 + (i % 10) for i in range(n)]
    df1h = pd.DataFrame({
        "open": close, "high": [c + 10 for c in close], "low": [c - 10 for c in close],
        "close": close, "volume": [1000] * n,
    }, index=pd.DatetimeIndex(idx1h, name="ts"))
    df4h = df1h.iloc[::4].copy()
    df5m = df1h.iloc[0:1].copy()
    return df1h, df4h, df5m


def test_scan_early_returns_for_paused_symbol(tmp_db):
    """scan(symbol) on a PAUSED symbol must return a disabled-like report, no signal."""
    import btc_scanner as scanner
    from health import apply_transition

    metrics = {"trades_count_total": 50, "win_rate_20_trades": 0.5,
               "pnl_30d": -500.0, "pnl_by_month": {}, "months_negative_consecutive": 3}
    apply_transition("BTCUSDT", new_state="PAUSED", reason="3mo_consec_neg",
                     metrics=metrics, from_state="NORMAL")

    df1h, df4h, df5m = _mini_bars()

    with patch("btc_scanner.md.get_klines", side_effect=[df5m, df1h, df4h]):
        rep = scanner.scan("BTCUSDT")

    assert rep["señal_activa"] is False
    assert rep["health_state"] == "PAUSED"
    assert "PAUSED" in rep["estado"]


def test_scan_normal_symbol_proceeds_unchanged(tmp_db):
    """scan() on a NORMAL (or absent-from-DB) symbol behaves as before — no early return."""
    import btc_scanner as scanner

    df1h, df4h, df5m = _mini_bars()

    with patch("btc_scanner.md.get_klines", side_effect=[df5m, df1h, df4h, df1h]):
        rep = scanner.scan("BTCUSDT")

    # NORMAL path produces a full report (with or without a signal, depending on mock).
    # The key assertion is that it did NOT early-return with PAUSED banner.
    assert rep.get("health_state") != "PAUSED"
    assert "PAUSED" not in rep.get("estado", "")
