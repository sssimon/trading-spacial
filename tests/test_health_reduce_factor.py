"""apply_reduce_factor — scales size by config.reduce_size_factor when state is REDUCED."""
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


_CFG = {"kill_switch": {"enabled": True, "reduce_size_factor": 0.5}}


def test_reduce_factor_applied_when_state_reduced(tmp_db):
    from health import apply_reduce_factor, apply_transition
    metrics = {"trades_count_total": 50, "win_rate_20_trades": 0.5,
               "pnl_30d": -100.0, "pnl_by_month": {}, "months_negative_consecutive": 0}
    apply_transition("BTC", new_state="REDUCED", reason="pnl_neg_30d",
                     metrics=metrics, from_state="NORMAL")
    assert apply_reduce_factor(1.0, "BTC", _CFG) == 0.5
    assert apply_reduce_factor(1000.0, "BTC", _CFG) == 500.0


def test_reduce_factor_normal_unchanged(tmp_db):
    from health import apply_reduce_factor
    # No row → defaults to NORMAL → no reduction
    assert apply_reduce_factor(1.0, "UNSEEN", _CFG) == 1.0


def test_reduce_factor_alert_unchanged(tmp_db):
    """ALERT symbols keep full size (only REDUCED halves it)."""
    from health import apply_reduce_factor, apply_transition
    metrics = {"trades_count_total": 50, "win_rate_20_trades": 0.1,
               "pnl_30d": 0.0, "pnl_by_month": {}, "months_negative_consecutive": 0}
    apply_transition("DOGE", new_state="ALERT", reason="wr_below_threshold",
                     metrics=metrics, from_state="NORMAL")
    assert apply_reduce_factor(1.0, "DOGE", _CFG) == 1.0


def test_reduce_factor_disabled_by_config(tmp_db):
    """kill_switch.enabled=False → always return size unchanged."""
    from health import apply_reduce_factor, apply_transition
    metrics = {"trades_count_total": 50, "win_rate_20_trades": 0.5,
               "pnl_30d": -100.0, "pnl_by_month": {}, "months_negative_consecutive": 0}
    apply_transition("JUP", new_state="REDUCED", reason="pnl_neg_30d",
                     metrics=metrics, from_state="NORMAL")
    cfg = {"kill_switch": {"enabled": False, "reduce_size_factor": 0.5}}
    assert apply_reduce_factor(1.0, "JUP", cfg) == 1.0


def test_reduce_factor_custom_value(tmp_db):
    """reduce_size_factor config value is honored."""
    from health import apply_reduce_factor, apply_transition
    metrics = {"trades_count_total": 50, "win_rate_20_trades": 0.5,
               "pnl_30d": -100.0, "pnl_by_month": {}, "months_negative_consecutive": 0}
    apply_transition("ETH", new_state="REDUCED", reason="pnl_neg_30d",
                     metrics=metrics, from_state="NORMAL")
    cfg = {"kill_switch": {"enabled": True, "reduce_size_factor": 0.25}}
    assert apply_reduce_factor(100.0, "ETH", cfg) == 25.0


# ── B5: PROBATION size factor ──────────────────────────────────────────────


def test_reduce_factor_applied_when_state_probation(tmp_db):
    """PROBATION halves size like REDUCED."""
    from health import apply_reduce_factor
    import btc_api
    # Seed PROBATION row directly
    conn = btc_api.get_db()
    try:
        conn.execute(
            """INSERT INTO symbol_health
               (symbol, state, state_since, last_evaluated_at, last_metrics_json,
                probation_trades_remaining, probation_started_at, paused_days_at_entry)
               VALUES ('UNI', 'PROBATION', '2026-04-01T00:00:00+00:00',
                       '2026-04-01T00:00:00+00:00', '{}', 13, '2026-04-01T00:00:00+00:00', 15)"""
        )
        conn.commit()
    finally:
        conn.close()
    cfg = {"kill_switch": {"enabled": True, "reduce_size_factor": 0.5}}
    assert apply_reduce_factor(1.0, "UNI", cfg) == 0.5
    assert apply_reduce_factor(1000.0, "UNI", cfg) == 500.0


def test_probation_size_factor_config_override(tmp_db):
    """v2.probation.size_factor overrides reduce_size_factor for PROBATION only."""
    from health import apply_reduce_factor
    import btc_api
    conn = btc_api.get_db()
    try:
        conn.execute(
            """INSERT INTO symbol_health
               (symbol, state, state_since, last_evaluated_at, last_metrics_json,
                probation_trades_remaining, probation_started_at, paused_days_at_entry)
               VALUES ('JUP', 'PROBATION', '2026-04-01T00:00:00+00:00',
                       '2026-04-01T00:00:00+00:00', '{}', 13, '2026-04-01T00:00:00+00:00', 15)"""
        )
        conn.commit()
    finally:
        conn.close()
    cfg = {"kill_switch": {
        "enabled": True, "reduce_size_factor": 0.5,
        "v2": {"probation": {"size_factor": 0.25}},
    }}
    assert apply_reduce_factor(1000.0, "JUP", cfg) == 250.0
