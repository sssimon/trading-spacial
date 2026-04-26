"""End-to-end: insert positions → run evaluate_and_record → verify state + events."""
from datetime import datetime, timedelta, timezone

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
    "enabled": True,
    "min_trades_for_eval": 20,
    "alert_win_rate_threshold": 0.15,
    "reduce_pnl_window_days": 30,
    "reduce_size_factor": 0.5,
    "pause_months_consecutive": 3,
    "auto_recovery_enabled": True,
}}
NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def test_evaluate_and_record_healthy_leaves_normal_no_event(tmp_db):
    from health import evaluate_and_record
    import btc_api
    conn = btc_api.get_db()
    try:
        for i in range(25):
            _insert_closed(conn, "BTC", 100.0, (NOW - timedelta(days=25 - i)).isoformat())
        evaluate_and_record("BTC", CFG, now=NOW)
        state = conn.execute(
            "SELECT state FROM symbol_health WHERE symbol='BTC'"
        ).fetchone()
        events = conn.execute(
            "SELECT COUNT(*) FROM symbol_health_events WHERE symbol='BTC'"
        ).fetchone()
    finally:
        conn.close()
    assert state[0] == "NORMAL"
    assert events[0] == 0


def test_evaluate_and_record_transitions_emit_event(tmp_db):
    from health import evaluate_and_record
    import btc_api
    conn = btc_api.get_db()
    try:
        _insert_closed(conn, "DOGE", -100.0, "2026-05-10T12:00:00+00:00")
        _insert_closed(conn, "DOGE", -100.0, "2026-04-15T12:00:00+00:00")
        _insert_closed(conn, "DOGE", -100.0, "2026-03-20T12:00:00+00:00")
        for i in range(22):
            _insert_closed(conn, "DOGE", -10.0, (NOW - timedelta(days=40 + i)).isoformat())
        evaluate_and_record("DOGE", CFG, now=NOW)
        state_row = conn.execute(
            "SELECT state FROM symbol_health WHERE symbol='DOGE'"
        ).fetchone()
        events = conn.execute(
            "SELECT to_state, trigger_reason FROM symbol_health_events WHERE symbol='DOGE'"
        ).fetchall()
    finally:
        conn.close()
    assert state_row[0] == "PAUSED"
    assert len(events) == 1
    assert events[0] == ("PAUSED", "3mo_consec_neg")


def test_evaluate_all_symbols_iterates_default_list(tmp_db, monkeypatch):
    from health import evaluate_all_symbols
    import btc_api
    monkeypatch.setattr("btc_scanner.DEFAULT_SYMBOLS", ["ALPHA", "BETA"])
    conn = btc_api.get_db()
    try:
        for i in range(25):
            _insert_closed(conn, "ALPHA", 100.0, (NOW - timedelta(days=25 - i)).isoformat())
        evaluate_all_symbols(CFG, now=NOW)
        rows = conn.execute(
            "SELECT symbol, state FROM symbol_health"
        ).fetchall()
    finally:
        conn.close()
    rows_dict = {r[0]: r[1] for r in rows}
    assert rows_dict.get("ALPHA") == "NORMAL"
    # BETA has 0 trades → insufficient_data → state stays at default NORMAL
    assert rows_dict.get("BETA") == "NORMAL"


def test_kill_switch_disabled_in_config_skips_evaluation(tmp_db, monkeypatch):
    from health import evaluate_all_symbols
    import btc_api
    monkeypatch.setattr("btc_scanner.DEFAULT_SYMBOLS", ["X"])
    cfg = {"kill_switch": {"enabled": False}}
    conn = btc_api.get_db()
    try:
        for i in range(25):
            _insert_closed(conn, "X", -100.0, (NOW - timedelta(days=25 - i)).isoformat())
        evaluate_all_symbols(cfg, now=NOW)
        rows = conn.execute("SELECT COUNT(*) FROM symbol_health").fetchone()
    finally:
        conn.close()
    assert rows[0] == 0


# ── B5 full lifecycle: PAUSED → PROBATION → NORMAL ──────────────────────────


def test_paused_reactivate_to_probation_then_complete_after_n_trades(tmp_db):
    """End-to-end: reactivate → PROBATION (13 trades) → 13 wins → NORMAL."""
    from datetime import datetime, timezone, timedelta
    import btc_api
    from health import (
        reactivate_symbol, trigger_health_evaluation, get_symbol_state,
    )

    # Seed 25 closed losing trades (so total >= min_trades_for_eval and pnl_30d > 0
    # via subsequent wins). Using losses dated > 30 days ago to keep pnl_30d clean.
    conn = btc_api.get_db()
    try:
        for i in range(25):
            ts = (datetime.now(timezone.utc) - timedelta(days=180 + i)).isoformat()
            conn.execute(
                """INSERT INTO positions
                   (symbol, direction, status, entry_price, entry_ts,
                    exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct)
                   VALUES ('BTC', 'LONG', 'closed', 100.0, ?, 95.0, ?, 'SL', -5.0, -0.05)""",
                (ts, ts),
            )
        # Insert PAUSED row for BTC, set 15 days ago
        state_since = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
        conn.execute(
            """INSERT INTO symbol_health
               (symbol, state, state_since, last_evaluated_at, last_metrics_json)
               VALUES ('BTC', 'PAUSED', ?, ?, '{}')""",
            (state_since, state_since),
        )
        conn.commit()
    finally:
        conn.close()

    cfg = {"kill_switch": {
        "enabled": True, "min_trades_for_eval": 20,
        "alert_win_rate_threshold": 0.15, "reduce_size_factor": 0.5,
        "pause_months_consecutive": 3, "auto_recovery_enabled": True,
        "v2": {"probation": {
            "trades_base": 10, "trades_per_pause_day": 0.2,
            "regression_wr_threshold": 0.10, "regression_window_trades": 10,
            "paused_to_probation_days": 14, "size_factor": 0.5,
        }},
    }}

    # Manual reactivate → PROBATION with trades_remaining=13 (15 days * 0.2 + 10)
    reactivate_symbol("BTC", reason="manual", cfg=cfg)
    assert get_symbol_state("BTC") == "PROBATION"
    conn = btc_api.get_db()
    try:
        row = conn.execute(
            "SELECT probation_trades_remaining FROM symbol_health WHERE symbol='BTC'"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == 13

    # Simulate 13 winning closed trades + trade hook each time.
    # After the 13th, counter hits 0 → next eval transitions to NORMAL.
    for i in range(13):
        ts = (datetime.now(timezone.utc) - timedelta(hours=24 - i)).isoformat()
        conn = btc_api.get_db()
        try:
            conn.execute(
                """INSERT INTO positions
                   (symbol, direction, status, entry_price, entry_ts,
                    exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct)
                   VALUES ('BTC', 'LONG', 'closed', 100.0, ?, 110.0, ?, 'TP', 10.0, 0.10)""",
                (ts, ts),
            )
            conn.commit()
        finally:
            conn.close()
        trigger_health_evaluation("BTC", cfg)

    # After 13 wins, state must be NORMAL and probation columns NULL
    assert get_symbol_state("BTC") == "NORMAL"
    conn = btc_api.get_db()
    try:
        row = conn.execute(
            """SELECT probation_trades_remaining, probation_started_at,
                      paused_days_at_entry FROM symbol_health WHERE symbol='BTC'"""
        ).fetchone()
    finally:
        conn.close()
    assert row[0] is None
    assert row[1] is None
    assert row[2] is None
