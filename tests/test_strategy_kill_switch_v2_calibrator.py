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


# ── B4b.1: kill_switch_calibrator_loop ──────────────────────────────────────


def test_calibrator_loop_safety_net_fires_when_table_empty(
    tmp_path, monkeypatch,
):
    """First-ever tick with empty table → safety_net fires + persists row."""
    import btc_api, threading
    from strategy.kill_switch_v2_calibrator import kill_switch_calibrator_loop

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    stop_event = threading.Event()
    call_count = {"n": 0}

    def fake_wait(seconds):
        call_count["n"] += 1
        stop_event.set()
        return True

    monkeypatch.setattr(stop_event, "wait", fake_wait)

    cfg_fn = lambda: {
        "kill_switch": {"v2": {"auto_calibrator": {"safety_net_days": 30}}}
    }
    kill_switch_calibrator_loop(cfg_fn, stop_event=stop_event)

    assert call_count["n"] == 1

    conn = btc_api.get_db()
    try:
        rows = conn.execute(
            "SELECT triggered_by, status FROM kill_switch_recommendations"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    import json
    assert json.loads(rows[0][0]) == ["safety_net"]
    assert rows[0][1] == "no_feasible"


def test_calibrator_loop_does_not_fire_when_recent_recalibration(
    tmp_path, monkeypatch,
):
    """If last recalibration was <30d ago, loop iteration skips persistence."""
    import btc_api, threading
    from strategy.kill_switch_v2_calibrator import (
        kill_switch_calibrator_loop, _persist_recommendation, run_optimization_stub,
    )
    from datetime import datetime, timezone, timedelta

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    now = datetime.now(tz=timezone.utc)
    recent = now - timedelta(days=5)
    _persist_recommendation(
        triggered_by=["safety_net"],
        result=run_optimization_stub({}),
        now=recent,
    )

    stop_event = threading.Event()
    def fake_wait(seconds):
        stop_event.set()
        return True
    monkeypatch.setattr(stop_event, "wait", fake_wait)

    cfg_fn = lambda: {
        "kill_switch": {"v2": {"auto_calibrator": {"safety_net_days": 30}}}
    }
    kill_switch_calibrator_loop(cfg_fn, stop_event=stop_event)

    conn = btc_api.get_db()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM kill_switch_recommendations"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_calibrator_loop_exits_cleanly_when_stop_event_set(
    tmp_path, monkeypatch,
):
    """stop_event.set() before loop start → loop exits without doing work."""
    import btc_api, threading
    from strategy.kill_switch_v2_calibrator import kill_switch_calibrator_loop

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    stop_event = threading.Event()
    stop_event.set()

    cfg_fn = lambda: {}
    kill_switch_calibrator_loop(cfg_fn, stop_event=stop_event)

    conn = btc_api.get_db()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM kill_switch_recommendations"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_calibrator_loop_iteration_failure_is_logged_not_propagated(
    tmp_path, monkeypatch, caplog,
):
    """Exception in iteration body → logged with exc_info, loop continues."""
    import btc_api, threading
    import strategy.kill_switch_v2_calibrator as cal

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    def _boom(*a, **kw):
        raise RuntimeError("simulated cfg lookup failure")

    stop_event = threading.Event()
    def fake_wait(seconds):
        stop_event.set()
        return True
    monkeypatch.setattr(stop_event, "wait", fake_wait)

    import logging
    with caplog.at_level(logging.WARNING, logger="kill_switch_v2_calibrator"):
        cal.kill_switch_calibrator_loop(_boom, stop_event=stop_event)

    assert any(
        "kill_switch_calibrator_loop iteration failed" in rec.getMessage()
        for rec in caplog.records
    )


# ── B4b.1: POST /kill_switch/recalibrate ────────────────────────────────────


def test_post_recalibrate_returns_recommendation_id(tmp_path, monkeypatch):
    """POST /kill_switch/recalibrate creates a row + returns id + status."""
    import btc_api
    from fastapi.testclient import TestClient

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    btc_api.app.dependency_overrides[btc_api.verify_api_key] = lambda: None

    try:
        client = TestClient(btc_api.app)
        resp = client.post("/kill_switch/recalibrate")
        assert resp.status_code == 200
        body = resp.json()
        assert "recommendation_id" in body
        assert body["status"] == "no_feasible"

        rec_id = body["recommendation_id"]
        conn = btc_api.get_db()
        try:
            row = conn.execute(
                "SELECT triggered_by, status FROM kill_switch_recommendations "
                "WHERE id = ?", (rec_id,),
            ).fetchone()
        finally:
            conn.close()
        import json
        assert json.loads(row[0]) == ["manual"]
        assert row[1] == "no_feasible"
    finally:
        btc_api.app.dependency_overrides.clear()


# ── B4b.1: GET /kill_switch/recommendations ─────────────────────────────────


def test_get_recommendations_empty_returns_empty_list(tmp_path, monkeypatch):
    import btc_api
    from fastapi.testclient import TestClient

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    btc_api.app.dependency_overrides[btc_api.verify_api_key] = lambda: None

    try:
        client = TestClient(btc_api.app)
        resp = client.get("/kill_switch/recommendations")
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        btc_api.app.dependency_overrides.clear()


def test_get_recommendations_returns_rows_ordered_desc(tmp_path, monkeypatch):
    import btc_api
    from fastapi.testclient import TestClient
    from strategy.kill_switch_v2_calibrator import (
        _persist_recommendation, run_optimization_stub,
    )
    from datetime import datetime, timezone

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    btc_api.app.dependency_overrides[btc_api.verify_api_key] = lambda: None

    earlier = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
    later = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    result = run_optimization_stub({})
    _persist_recommendation(
        triggered_by=["safety_net"], result=result, now=earlier,
    )
    _persist_recommendation(
        triggered_by=["manual"], result=result, now=later,
    )

    try:
        client = TestClient(btc_api.app)
        resp = client.get("/kill_switch/recommendations")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 2
        # Latest first
        assert rows[0]["ts"] == later.isoformat()
        assert rows[0]["triggered_by"] == ["manual"]
        assert rows[1]["ts"] == earlier.isoformat()
        assert rows[1]["triggered_by"] == ["safety_net"]
        # Report block parsed
        assert rows[0]["report"]["stub"] is True
    finally:
        btc_api.app.dependency_overrides.clear()


def test_get_recommendations_filter_by_status(tmp_path, monkeypatch):
    """status=no_feasible filter returns only matching rows."""
    import btc_api
    from fastapi.testclient import TestClient
    from strategy.kill_switch_v2_calibrator import (
        _persist_recommendation, run_optimization_stub,
    )
    from datetime import datetime, timezone

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    btc_api.app.dependency_overrides[btc_api.verify_api_key] = lambda: None

    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    result = run_optimization_stub({})
    _persist_recommendation(
        triggered_by=["manual"], result=result, now=now,
    )

    try:
        client = TestClient(btc_api.app)
        resp_match = client.get(
            "/kill_switch/recommendations?status=no_feasible",
        )
        assert resp_match.status_code == 200
        assert len(resp_match.json()) == 1

        resp_other = client.get(
            "/kill_switch/recommendations?status=applied",
        )
        assert resp_other.status_code == 200
        assert resp_other.json() == []
    finally:
        btc_api.app.dependency_overrides.clear()
