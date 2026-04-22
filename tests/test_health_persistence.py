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
    assert ks["min_trades_for_eval"] == 20  # default preserved
