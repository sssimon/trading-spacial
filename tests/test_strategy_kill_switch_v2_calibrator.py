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


# ── B4b.1: build_no_feasible_report ─────────────────────────────────────────


def test_build_no_feasible_report_shape():
    from strategy.kill_switch_v2_calibrator import build_no_feasible_report
    from datetime import datetime, timezone
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    report = build_no_feasible_report(reason="test reason", now=now)
    assert report["status"] == "no_feasible"
    assert report["reason"] == "test reason"
    assert report["ts"] == now.isoformat()
    assert report["stub"] is True


# ── B4b.1: run_optimization_stub ────────────────────────────────────────────


def test_run_optimization_stub_returns_no_feasible_for_empty_cfg():
    from strategy.kill_switch_v2_calibrator import run_optimization_stub
    result = run_optimization_stub({})
    assert result["status"] == "no_feasible"
    assert result["slider_value"] is None
    assert result["projected_pnl"] is None
    assert result["projected_dd"] is None
    assert result["report"]["stub"] is True


def test_run_optimization_stub_returns_no_feasible_for_full_cfg():
    """Stub ignores cfg contents; always returns no_feasible."""
    from strategy.kill_switch_v2_calibrator import run_optimization_stub
    cfg = {"kill_switch": {"v2": {"aggressiveness": 75}}}
    result = run_optimization_stub(cfg)
    assert result["status"] == "no_feasible"
    assert "v2 backtest pending B4b.2" in result["report"]["reason"]


# ── B4b.1: DB glue ──────────────────────────────────────────────────────────


def test_persist_recommendation_inserts_row(tmp_path, monkeypatch):
    import btc_api
    from strategy.kill_switch_v2_calibrator import (
        _persist_recommendation, run_optimization_stub,
    )
    from datetime import datetime, timezone
    import json

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    result = run_optimization_stub({})
    rec_id = _persist_recommendation(
        triggered_by=["manual"], result=result, now=now,
    )
    assert isinstance(rec_id, int)
    assert rec_id > 0

    conn = btc_api.get_db()
    try:
        row = conn.execute(
            "SELECT ts, triggered_by, slider_value, projected_pnl, "
            "projected_dd, status, report_json "
            "FROM kill_switch_recommendations WHERE id = ?",
            (rec_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == now.isoformat()
    assert json.loads(row[1]) == ["manual"]
    assert row[2] is None
    assert row[3] is None
    assert row[4] is None
    assert row[5] == "no_feasible"
    parsed_report = json.loads(row[6])
    assert parsed_report["status"] == "no_feasible"
    assert parsed_report["stub"] is True


def test_persist_recommendation_returns_distinct_ids(tmp_path, monkeypatch):
    import btc_api
    from strategy.kill_switch_v2_calibrator import (
        _persist_recommendation, run_optimization_stub,
    )
    from datetime import datetime, timezone

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    result = run_optimization_stub({})
    id1 = _persist_recommendation(
        triggered_by=["safety_net"], result=result, now=now,
    )
    id2 = _persist_recommendation(
        triggered_by=["manual"], result=result, now=now,
    )
    assert id2 > id1


def test_load_last_recalibration_ts_empty_table(tmp_path, monkeypatch):
    import btc_api
    from strategy.kill_switch_v2_calibrator import _load_last_recalibration_ts

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    assert _load_last_recalibration_ts() is None


def test_load_last_recalibration_ts_returns_max_ts(tmp_path, monkeypatch):
    import btc_api
    from strategy.kill_switch_v2_calibrator import (
        _persist_recommendation, _load_last_recalibration_ts, run_optimization_stub,
    )
    from datetime import datetime, timezone

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    earlier = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
    later = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    result = run_optimization_stub({})
    _persist_recommendation(
        triggered_by=["safety_net"], result=result, now=earlier,
    )
    _persist_recommendation(
        triggered_by=["manual"], result=result, now=later,
    )

    assert _load_last_recalibration_ts() == later.isoformat()
