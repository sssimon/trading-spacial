"""Tests for strategy.kill_switch_v2_calibrator — auto-calibrator daemon (#187 #214 B4b.1)."""
import pytest


# ── B4b.1: schema smoke test ────────────────────────────────────────────────


def test_init_db_creates_kill_switch_recommendations_table(tmp_path, monkeypatch):
    """init_db must create kill_switch_recommendations with the expected columns."""
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    conn = btc_api.get_db()
    try:
        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(kill_switch_recommendations)"
        ).fetchall()]
    finally:
        conn.close()

    expected = {
        "id", "ts", "triggered_by", "slider_value",
        "projected_pnl", "projected_dd", "status",
        "applied_ts", "applied_by", "report_json",
    }
    assert expected.issubset(set(cols))


# ── B4b.1: should_run_safety_net ────────────────────────────────────────────


def test_should_run_safety_net_none_returns_true():
    from strategy.kill_switch_v2_calibrator import should_run_safety_net
    from datetime import datetime, timezone
    now = datetime(2026, 4, 25, 0, 0, tzinfo=timezone.utc)
    assert should_run_safety_net(None, now, safety_net_days=30) is True


def test_should_run_safety_net_malformed_returns_true():
    from strategy.kill_switch_v2_calibrator import should_run_safety_net
    from datetime import datetime, timezone
    now = datetime(2026, 4, 25, 0, 0, tzinfo=timezone.utc)
    assert should_run_safety_net("garbage", now, safety_net_days=30) is True


def test_should_run_safety_net_29_days_ago_returns_false():
    from strategy.kill_switch_v2_calibrator import should_run_safety_net
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 4, 25, 0, 0, tzinfo=timezone.utc)
    last = (now - timedelta(days=29)).isoformat()
    assert should_run_safety_net(last, now, safety_net_days=30) is False


def test_should_run_safety_net_31_days_ago_returns_true():
    from strategy.kill_switch_v2_calibrator import should_run_safety_net
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 4, 25, 0, 0, tzinfo=timezone.utc)
    last = (now - timedelta(days=31)).isoformat()
    assert should_run_safety_net(last, now, safety_net_days=30) is True


def test_should_run_safety_net_exactly_30_days_returns_false():
    """Boundary: at exactly 30 days, strict `>` keeps it NOT firing."""
    from strategy.kill_switch_v2_calibrator import should_run_safety_net
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 4, 25, 0, 0, tzinfo=timezone.utc)
    last = (now - timedelta(days=30)).isoformat()
    assert should_run_safety_net(last, now, safety_net_days=30) is False


def test_should_run_safety_net_future_timestamp_returns_true():
    """Clock skew guard: a future last_ts should still fire."""
    from strategy.kill_switch_v2_calibrator import should_run_safety_net
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 4, 25, 0, 0, tzinfo=timezone.utc)
    future = (now + timedelta(days=1)).isoformat()
    assert should_run_safety_net(future, now, safety_net_days=30) is True
