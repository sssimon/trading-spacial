"""Schema + persistence tests for health module."""
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


def test_schema_has_symbol_health_tables(tmp_db):
    """init_db() must create the two health tables."""
    import btc_api
    conn = btc_api.get_db()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('symbol_health', 'symbol_health_events')"
        ).fetchall()
    finally:
        conn.close()
    names = {r[0] for r in rows}
    assert "symbol_health" in names
    assert "symbol_health_events" in names


def test_symbol_health_columns(tmp_db):
    """symbol_health must have the specified columns."""
    import btc_api
    conn = btc_api.get_db()
    try:
        cols = conn.execute("PRAGMA table_info(symbol_health)").fetchall()
    finally:
        conn.close()
    col_names = {c[1] for c in cols}
    for required in ("symbol", "state", "state_since",
                      "last_evaluated_at", "last_metrics_json", "manual_override"):
        assert required in col_names, f"missing column: {required}"


def test_symbol_health_events_columns(tmp_db):
    """symbol_health_events must have the specified columns."""
    import btc_api
    conn = btc_api.get_db()
    try:
        cols = conn.execute("PRAGMA table_info(symbol_health_events)").fetchall()
    finally:
        conn.close()
    col_names = {c[1] for c in cols}
    for required in ("id", "symbol", "from_state", "to_state",
                      "trigger_reason", "metrics_json", "ts"):
        assert required in col_names, f"missing column: {required}"


def test_kill_switch_config_partial_override_preserves_defaults(tmp_path, monkeypatch):
    """Regression: if a user writes {"kill_switch": {"enabled": false}} to
    config.json, the other 6 keys (min_trades_for_eval, thresholds, etc.)
    must survive the deep-merge. Plain dict.update would wipe them out."""
    import json as _json
    import btc_api

    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps({"kill_switch": {"enabled": False}}))
    monkeypatch.setattr(btc_api, "CONFIG_FILE", str(cfg_file))

    cfg = btc_api.load_config()
    ks = cfg["kill_switch"]
    assert ks["enabled"] is False
    # All default keys must still be present
    for required in ("min_trades_for_eval", "alert_win_rate_threshold",
                      "reduce_pnl_window_days", "reduce_size_factor",
                      "pause_months_consecutive", "auto_recovery_enabled"):
        assert required in ks, f"deep-merge dropped kill_switch.{required}"
    # default preserved — value sourced from config.defaults.json (canonical)
    assert ks["min_trades_for_eval"] == 10


from datetime import datetime, timezone


def test_apply_transition_writes_row_and_event(tmp_db):
    from health import apply_transition
    import btc_api
    metrics = {
        "trades_count_total": 50, "win_rate_20_trades": 0.5,
        "pnl_30d": 100.0, "pnl_by_month": {},
        "months_negative_consecutive": 0,
    }
    apply_transition("BTCUSDT", new_state="ALERT", reason="wr_below_threshold",
                     metrics=metrics, from_state="NORMAL")
    conn = btc_api.get_db()
    try:
        row = conn.execute(
            "SELECT state, state_since, manual_override FROM symbol_health WHERE symbol=?",
            ("BTCUSDT",),
        ).fetchone()
        event = conn.execute(
            """SELECT from_state, to_state, trigger_reason FROM symbol_health_events
               WHERE symbol=? ORDER BY ts DESC LIMIT 1""",
            ("BTCUSDT",),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "ALERT"
    assert row[2] == 0  # manual_override default 0
    assert event == ("NORMAL", "ALERT", "wr_below_threshold")


def test_get_symbol_state_returns_normal_for_unknown(tmp_db):
    """A symbol with no row is treated as NORMAL by default."""
    from health import get_symbol_state
    assert get_symbol_state("UNSEEN") == "NORMAL"


def test_get_symbol_state_returns_persisted(tmp_db):
    from health import apply_transition, get_symbol_state
    metrics = {"trades_count_total": 50, "win_rate_20_trades": 0.5,
                "pnl_30d": 0.0, "pnl_by_month": {},
                "months_negative_consecutive": 0}
    apply_transition("JUPUSDT", new_state="PAUSED", reason="3mo_consec_neg",
                      metrics=metrics, from_state="REDUCED")
    assert get_symbol_state("JUPUSDT") == "PAUSED"


def test_apply_transition_same_state_is_idempotent(tmp_db):
    """If new_state == current, update last_evaluated_at but do NOT insert event row."""
    from health import apply_transition, _record_evaluation
    import btc_api
    metrics = {"trades_count_total": 50, "win_rate_20_trades": 0.5,
                "pnl_30d": 0.0, "pnl_by_month": {},
                "months_negative_consecutive": 0}
    apply_transition("BTC", new_state="ALERT", reason="wr_below_threshold",
                      metrics=metrics, from_state="NORMAL")
    _record_evaluation("BTC", metrics, new_state="ALERT")  # idempotent no-op transition
    conn = btc_api.get_db()
    try:
        event_count = conn.execute(
            "SELECT COUNT(*) FROM symbol_health_events WHERE symbol='BTC'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert event_count == 1  # initial transition, not a second event


def test_apply_transition_preserves_state_since_on_same_state(tmp_db):
    """Regression: apply_transition called with from_state != stored but
    new_state == stored must NOT reset state_since. The CASE in ON CONFLICT
    only advances state_since when the state actually changes.

    This matters for 'how long has this symbol been in X' — the feature's
    primary audit value.
    """
    import time
    from health import apply_transition
    import btc_api

    metrics = {"trades_count_total": 50, "win_rate_20_trades": 0.5,
                "pnl_30d": 0.0, "pnl_by_month": {},
                "months_negative_consecutive": 0}

    apply_transition("XLM", new_state="ALERT", reason="wr_below_threshold",
                      metrics=metrics, from_state="NORMAL")
    conn = btc_api.get_db()
    try:
        original_since = conn.execute(
            "SELECT state_since FROM symbol_health WHERE symbol='XLM'"
        ).fetchone()[0]
    finally:
        conn.close()

    # Tiny delay so a clobber would produce a later timestamp
    time.sleep(0.01)

    # Simulate a stale-from_state call where the stored state already matches new_state.
    apply_transition("XLM", new_state="ALERT", reason="wr_below_threshold",
                      metrics=metrics, from_state="NORMAL")

    conn = btc_api.get_db()
    try:
        later_since = conn.execute(
            "SELECT state_since FROM symbol_health WHERE symbol='XLM'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert later_since == original_since, (
        f"state_since was clobbered: {original_since!r} → {later_since!r}")


def test_reactivate_sets_manual_override(tmp_db):
    """reactivate_symbol flips manual_override to 1 and emits event."""
    from health import apply_transition, reactivate_symbol
    metrics = {"trades_count_total": 50, "win_rate_20_trades": 0.5,
                "pnl_30d": 0.0, "pnl_by_month": {},
                "months_negative_consecutive": 0}
    apply_transition("DOGE", new_state="PAUSED", reason="3mo_consec_neg",
                      metrics=metrics, from_state="REDUCED")
    reactivate_symbol("DOGE", reason="backtest_validated")
    import btc_api
    conn = btc_api.get_db()
    try:
        row = conn.execute(
            "SELECT state, manual_override FROM symbol_health WHERE symbol='DOGE'"
        ).fetchone()
        last_event = conn.execute(
            "SELECT trigger_reason FROM symbol_health_events WHERE symbol='DOGE' "
            "ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("NORMAL", 1)
    assert last_event[0] == "manual_override"


def test_compute_rolling_metrics_from_trades_empty():
    from health import compute_rolling_metrics_from_trades
    from datetime import datetime, timezone
    result = compute_rolling_metrics_from_trades([], now=datetime(2026, 4, 23, tzinfo=timezone.utc))
    assert result["trades_count_total"] == 0
    assert result["win_rate_20_trades"] is None
    assert result["pnl_30d"] == 0.0
    assert result["months_negative_consecutive"] == 0


def test_compute_rolling_metrics_from_trades_basic():
    from health import compute_rolling_metrics_from_trades
    from datetime import datetime, timezone
    now = datetime(2026, 4, 23, tzinfo=timezone.utc)
    trades = [
        {"exit_ts": "2026-04-10T12:00:00+00:00", "pnl_usd": 100.0},
        {"exit_ts": "2026-04-15T12:00:00+00:00", "pnl_usd": -50.0},
        {"exit_ts": "2026-04-20T12:00:00+00:00", "pnl_usd": 200.0},
    ]
    result = compute_rolling_metrics_from_trades(trades, now=now)
    assert result["trades_count_total"] == 3
    assert result["win_rate_20_trades"] == pytest.approx(2 / 3)
    assert result["pnl_30d"] == pytest.approx(250.0)


def test_compute_rolling_metrics_from_trades_months_consecutive():
    from health import compute_rolling_metrics_from_trades
    from datetime import datetime, timezone
    now = datetime(2026, 4, 23, tzinfo=timezone.utc)
    # 3 months all negative → months_negative_consecutive = 3
    trades = [
        {"exit_ts": "2026-01-15T12:00:00+00:00", "pnl_usd": -100.0},
        {"exit_ts": "2026-02-10T12:00:00+00:00", "pnl_usd": -80.0},
        {"exit_ts": "2026-03-05T12:00:00+00:00", "pnl_usd": -150.0},
    ]
    result = compute_rolling_metrics_from_trades(trades, now=now)
    assert result["months_negative_consecutive"] == 3


def test_compute_rolling_metrics_from_trades_win_rate_last_20():
    from health import compute_rolling_metrics_from_trades
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 4, 23, tzinfo=timezone.utc)
    trades = []
    for i in range(30):
        ts = (now - timedelta(days=30 - i)).isoformat()
        pnl = 100.0 if i % 5 == 0 else -20.0  # 1 win per 5 trades in last 20
        trades.append({"exit_ts": ts, "pnl_usd": pnl})
    result = compute_rolling_metrics_from_trades(trades, now=now)
    # Last 20 have 4 wins / 20 = 0.20
    assert result["win_rate_20_trades"] == pytest.approx(0.20)


# ── B5: PROBATION schema migration ──────────────────────────────────────────


def test_init_db_adds_probation_columns(tmp_db):
    """init_db must add probation_trades_remaining/started_at/paused_days_at_entry."""
    import btc_api
    conn = btc_api.get_db()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(symbol_health)").fetchall()}
    finally:
        conn.close()
    assert "probation_trades_remaining" in cols
    assert "probation_started_at" in cols
    assert "paused_days_at_entry" in cols


def test_init_db_migration_idempotent(tmp_path, monkeypatch):
    """Re-running init_db on a DB that already has probation columns must not raise."""
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    btc_api.init_db()  # second call must be a no-op
    conn = btc_api.get_db()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(symbol_health)").fetchall()}
    finally:
        conn.close()
    assert {"probation_trades_remaining", "probation_started_at", "paused_days_at_entry"} <= cols


def test_apply_transition_clears_probation_columns_on_exit(tmp_db):
    """When transitioning out of PROBATION, the 3 probation columns are reset to NULL."""
    import btc_api
    from health import apply_transition

    metrics = {"trades_count_total": 50, "win_rate_20_trades": 0.5,
               "pnl_30d": 0.0, "pnl_by_month": {},
               "months_negative_consecutive": 0,
               "win_rate_10_trades": 0.6}

    # Seed PROBATION row with non-NULL probation columns
    conn = btc_api.get_db()
    try:
        conn.execute(
            """INSERT INTO symbol_health
               (symbol, state, state_since, last_evaluated_at, last_metrics_json,
                probation_trades_remaining, probation_started_at, paused_days_at_entry)
               VALUES ('BTC', 'PROBATION', '2026-04-01T00:00:00+00:00',
                       '2026-04-01T00:00:00+00:00', '{}', 13, '2026-04-01T00:00:00+00:00', 15)"""
        )
        conn.commit()
    finally:
        conn.close()

    # Transition out of PROBATION
    apply_transition("BTC", new_state="NORMAL", reason="probation_complete",
                     metrics=metrics, from_state="PROBATION")

    conn = btc_api.get_db()
    try:
        row = conn.execute(
            """SELECT state, probation_trades_remaining, probation_started_at,
                      paused_days_at_entry FROM symbol_health WHERE symbol='BTC'"""
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "NORMAL"
    assert row[1] is None
    assert row[2] is None
    assert row[3] is None
