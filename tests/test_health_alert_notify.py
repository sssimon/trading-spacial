"""evaluate_and_record must fire notify(HealthEvent) once when a symbol
transitions to ALERT, and must NOT re-fire on subsequent evaluations where
the state stays ALERT."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    from notifier import ratelimit
    ratelimit.reset_all_for_tests()
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


def _seed_for_alert(conn):
    """25 closed trades: 24 tiny losses, 1 big win.
    compute_rolling_metrics uses LIMIT 20 for win_rate, so only the most recent
    20 trades (i=5..24) count → wr = 1/20 = 0.05 (< 0.15 threshold).
    Aggregate pnl over 30d is positive (≈$976) so REDUCED doesn't fire first."""
    for i in range(25):
        pnl = 1000.0 if i == 24 else -1.0  # single big winner dominates → agg positive
        _insert_closed(conn, "BTC", pnl, (NOW - timedelta(days=25 - i)).isoformat())


def test_transition_to_alert_fires_notify(tmp_db):
    from health import evaluate_and_record
    import btc_api

    conn = btc_api.get_db()
    try:
        _seed_for_alert(conn)
    finally:
        conn.close()

    with patch("health.notify") as mock_notify:
        state = evaluate_and_record("BTC", CFG, now=NOW)

    assert state == "ALERT", f"expected ALERT, got {state}"
    assert mock_notify.call_count == 1
    event_arg = mock_notify.call_args.args[0]
    # Arg is a HealthEvent — check its fields
    assert event_arg.symbol == "BTC"
    assert event_arg.to_state == "ALERT"
    assert event_arg.from_state == "NORMAL"


def test_alert_no_renotify_when_state_unchanged(tmp_db):
    """After the first ALERT transition, a second evaluate_and_record with the
    same data must NOT fire notify again (state stays ALERT)."""
    from health import evaluate_and_record
    import btc_api

    conn = btc_api.get_db()
    try:
        _seed_for_alert(conn)
    finally:
        conn.close()

    with patch("health.notify") as mock_notify:
        evaluate_and_record("BTC", CFG, now=NOW)
        evaluate_and_record("BTC", CFG, now=NOW)

    assert mock_notify.call_count == 1  # not 2


def test_transition_to_reduced_fires_notify(tmp_db):
    """PR 3 (#138) extends notify gate to REDUCED transitions."""
    from health import evaluate_and_record
    import btc_api

    conn = btc_api.get_db()
    try:
        for i in range(25):
            _insert_closed(conn, "DOGE", -100.0, (NOW - timedelta(days=25 - i)).isoformat())
    finally:
        conn.close()

    with patch("health.notify") as mock_notify:
        state = evaluate_and_record("DOGE", CFG, now=NOW)

    assert state == "REDUCED"
    assert mock_notify.call_count == 1
    event_arg = mock_notify.call_args.args[0]
    assert event_arg.to_state == "REDUCED"
    assert event_arg.reason == "pnl_neg_30d"


def test_reduced_no_renotify_when_state_unchanged(tmp_db):
    """Idempotence: second eval on stable REDUCED does not re-fire."""
    from health import evaluate_and_record
    import btc_api

    conn = btc_api.get_db()
    try:
        for i in range(25):
            _insert_closed(conn, "DOGE", -100.0, (NOW - timedelta(days=25 - i)).isoformat())
    finally:
        conn.close()

    with patch("health.notify") as mock_notify:
        evaluate_and_record("DOGE", CFG, now=NOW)
        evaluate_and_record("DOGE", CFG, now=NOW)

    assert mock_notify.call_count == 1
