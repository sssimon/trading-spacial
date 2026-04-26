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
    # B4b.2: real fitness; empty positions → status="pending" (or "no_feasible")
    assert rows[0][1] in ("pending", "no_feasible")


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
        # B4b.2: real fitness; empty positions → all sliders feasible at pnl=dd=0
        # → status="pending" with arbitrary slider (pnl tiebreak picks first).
        assert body["status"] in ("pending", "no_feasible")

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
        assert row[1] in ("pending", "no_feasible")
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
    from strategy.kill_switch_v2_calibrator import _persist_recommendation
    from strategy.kill_switch_v2_optimizer import run_optimization_v2
    from datetime import datetime, timezone

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    btc_api.app.dependency_overrides[btc_api.verify_api_key] = lambda: None

    earlier = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
    later = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    # B4b.2: seed with real optimizer so report["stub"] is False.
    result = run_optimization_v2({})
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
        # Report block parsed; stub is False after B4b.2 wiring (real optimizer).
        assert rows[0]["report"]["stub"] is False
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


# ── B4b.1: daemon launch ────────────────────────────────────────────────────


def test_start_scanner_thread_launches_calibrator_thread(monkeypatch):
    """start_scanner_thread spawns a thread named 'kill-switch-calibrator'."""
    import btc_api, threading

    captured_threads = []
    real_thread_init = threading.Thread.__init__

    def capture_init(self, *args, **kwargs):
        captured_threads.append(kwargs.get("name", "<unnamed>"))
        # Don't actually start anything destructive
        kwargs["target"] = lambda *a, **kw: None
        kwargs.pop("args", None)
        return real_thread_init(self, *args, **kwargs)

    monkeypatch.setattr(threading.Thread, "__init__", capture_init)

    btc_api.start_scanner_thread()

    assert "kill-switch-calibrator" in captured_threads


# ── B4b.1: review follow-ups — hardening tests ──────────────────────────────


def test_start_scanner_thread_starts_calibrator_target_callable(monkeypatch):
    """Pin the contract: thread is constructed AND .start() is invoked AND
    target is kill_switch_calibrator_loop.

    Replaces the brittle __init__ patch with a FakeThread that records
    construction args + start() calls. Pins what matters behaviorally.
    """
    import btc_api, threading
    from strategy import kill_switch_v2_calibrator

    captured = []
    original_thread = threading.Thread

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None, name=None, **kwargs):
            captured.append({
                "target": target,
                "args": args,
                "daemon": daemon,
                "name": name,
            })
            self._started = False
            self.name = name

        def start(self):
            self._started = True

    monkeypatch.setattr(threading, "Thread", FakeThread)

    btc_api.start_scanner_thread()

    # Find the calibrator thread
    calibrator = next(
        (c for c in captured if c["name"] == "kill-switch-calibrator"), None,
    )
    assert calibrator is not None, "kill-switch-calibrator thread must be constructed"
    assert calibrator["target"] is kill_switch_v2_calibrator.kill_switch_calibrator_loop, (
        "target must be kill_switch_calibrator_loop"
    )
    assert calibrator["daemon"] is True, "must be daemon=True"
    # args is (cfg_fn,) — the lambda captures load_config
    assert callable(calibrator["args"][0]), "first arg must be a cfg callable"


def test_post_recalibrate_unauthenticated_rejected_when_api_key_configured(
    tmp_path, monkeypatch,
):
    """When api_key is configured, POST without X-API-Key returns 401.

    The API has backwards-compatible "no api_key configured = open access"
    semantics (see verify_api_key in btc_api.py); this test forces a key
    via load_config monkeypatch to validate the auth path actually rejects.
    """
    import btc_api
    from fastapi.testclient import TestClient

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    monkeypatch.setattr(btc_api, "load_config", lambda: {"api_key": "test-secret"})

    client = TestClient(btc_api.app)
    # No X-API-Key header → 401
    resp = client.post("/kill_switch/recalibrate")
    assert resp.status_code == 401

    # Wrong key → 401
    resp_wrong = client.post(
        "/kill_switch/recalibrate", headers={"X-API-Key": "wrong-key"},
    )
    assert resp_wrong.status_code == 401

    # Correct key → 200
    resp_ok = client.post(
        "/kill_switch/recalibrate", headers={"X-API-Key": "test-secret"},
    )
    assert resp_ok.status_code == 200


def test_get_recommendations_unauthenticated_rejected_when_api_key_configured(
    tmp_path, monkeypatch,
):
    """When api_key is configured, GET without X-API-Key returns 401."""
    import btc_api
    from fastapi.testclient import TestClient

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    monkeypatch.setattr(btc_api, "load_config", lambda: {"api_key": "test-secret"})

    client = TestClient(btc_api.app)
    resp = client.get("/kill_switch/recommendations")
    assert resp.status_code == 401


def test_get_recommendations_filter_by_since(tmp_path, monkeypatch):
    """`since` filter returns only rows with ts >= since (boundary inclusive)."""
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
    middle = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)
    later = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    result = run_optimization_stub({})
    _persist_recommendation(triggered_by=["safety_net"], result=result, now=earlier)
    _persist_recommendation(triggered_by=["manual"], result=result, now=middle)
    _persist_recommendation(triggered_by=["safety_net"], result=result, now=later)

    try:
        client = TestClient(btc_api.app)
        # since=middle → returns middle + later (boundary inclusive via >=)
        resp = client.get(
            f"/kill_switch/recommendations?since={middle.isoformat()}",
        )
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 2
        ts_returned = sorted(r["ts"] for r in rows)
        assert ts_returned == sorted([middle.isoformat(), later.isoformat()])
    finally:
        btc_api.app.dependency_overrides.clear()


def test_get_recommendations_limit_caps_results(tmp_path, monkeypatch):
    """`limit` parameter caps the number of returned rows."""
    import btc_api
    from fastapi.testclient import TestClient
    from strategy.kill_switch_v2_calibrator import (
        _persist_recommendation, run_optimization_stub,
    )
    from datetime import datetime, timezone, timedelta

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    btc_api.app.dependency_overrides[btc_api.verify_api_key] = lambda: None

    base = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    result = run_optimization_stub({})
    for i in range(5):
        _persist_recommendation(
            triggered_by=["manual"], result=result,
            now=base + timedelta(minutes=i),
        )

    try:
        client = TestClient(btc_api.app)
        resp = client.get("/kill_switch/recommendations?limit=2")
        assert resp.status_code == 200
        assert len(resp.json()) == 2
    finally:
        btc_api.app.dependency_overrides.clear()


def test_calibrator_loop_persist_failure_logged_with_exc_info(
    tmp_path, monkeypatch, caplog,
):
    """Mid-iteration failure in _persist_recommendation is logged with exc_info
    via the outer try/except. Loop continues (next iteration would retry)."""
    import btc_api, threading
    import strategy.kill_switch_v2_calibrator as cal

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    def _boom_persist(*a, **kw):
        raise RuntimeError("simulated DB persist failure")

    monkeypatch.setattr(cal, "_persist_recommendation", _boom_persist)

    stop_event = threading.Event()
    def fake_wait(seconds):
        stop_event.set()
        return True
    monkeypatch.setattr(stop_event, "wait", fake_wait)

    cfg_fn = lambda: {
        "kill_switch": {"v2": {"auto_calibrator": {"safety_net_days": 30}}}
    }

    import logging
    with caplog.at_level(logging.WARNING, logger="kill_switch_v2_calibrator"):
        cal.kill_switch_calibrator_loop(cfg_fn, stop_event=stop_event)

    # Outer try/except catches RuntimeError and logs it
    matching = [
        rec for rec in caplog.records
        if "kill_switch_calibrator_loop iteration failed" in rec.getMessage()
    ]
    assert len(matching) >= 1
    # Verify exc_info attached (traceback captured)
    assert any(rec.exc_info is not None for rec in matching)


def test_persist_recommendation_raises_on_missing_status_key(tmp_path, monkeypatch):
    """_persist_recommendation raises KeyError if result dict missing 'status'."""
    import btc_api
    from strategy.kill_switch_v2_calibrator import _persist_recommendation
    from datetime import datetime, timezone

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)

    with pytest.raises(KeyError, match="missing required keys"):
        _persist_recommendation(
            triggered_by=["manual"],
            result={"report": {}},  # missing "status"
            now=now,
        )

    with pytest.raises(KeyError, match="missing required keys"):
        _persist_recommendation(
            triggered_by=["manual"],
            result={"status": "no_feasible"},  # missing "report"
            now=now,
        )


def test_post_recalibrate_returns_500_on_internal_failure(
    tmp_path, monkeypatch, caplog,
):
    """If both run_optimization_v2 and the stub fallback raise, the endpoint
    returns 500 with detail (not opaque FastAPI error) and logs with exc_info.

    B4b.2: endpoint now calls run_optimization_v2 first with a stub fallback.
    To exercise the outer 500 path, both must fail.
    """
    import btc_api
    import strategy.kill_switch_v2_calibrator as cal
    import strategy.kill_switch_v2_optimizer as opt
    from fastapi.testclient import TestClient

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    btc_api.app.dependency_overrides[btc_api.verify_api_key] = lambda: None

    def _boom(*a, **kw):
        raise RuntimeError("simulated optimization failure")
    monkeypatch.setattr(opt, "run_optimization_v2", _boom)
    monkeypatch.setattr(cal, "run_optimization_stub", _boom)

    try:
        import logging
        with caplog.at_level(logging.ERROR, logger=btc_api.log.name):
            client = TestClient(btc_api.app, raise_server_exceptions=False)
            resp = client.post("/kill_switch/recalibrate")

        assert resp.status_code == 500
        body = resp.json()
        assert "recalibrate failed" in body.get("detail", "")
        assert "RuntimeError" in body.get("detail", "")
        # log.error captured with exc_info
        assert any(
            "POST /kill_switch/recalibrate failed" in rec.getMessage()
            and rec.exc_info is not None
            for rec in caplog.records
        )
    finally:
        btc_api.app.dependency_overrides.clear()


def test_get_recommendations_logs_warning_on_corrupt_row(
    tmp_path, monkeypatch, caplog,
):
    """If a row has corrupted triggered_by JSON, GET logs a warning with row id
    and returns the raw value (does not silently discard)."""
    import btc_api
    from fastapi.testclient import TestClient

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    btc_api.app.dependency_overrides[btc_api.verify_api_key] = lambda: None

    # Insert a row with corrupted triggered_by (not valid JSON)
    conn = btc_api.get_db()
    try:
        conn.execute(
            "INSERT INTO kill_switch_recommendations "
            "(ts, triggered_by, status, report_json) VALUES (?, ?, ?, ?)",
            ("2026-04-25T12:00:00+00:00", "not-valid-json", "no_feasible", "{}"),
        )
        conn.commit()
    finally:
        conn.close()

    try:
        import logging
        with caplog.at_level(logging.WARNING, logger=btc_api.log.name):
            client = TestClient(btc_api.app)
            resp = client.get("/kill_switch/recommendations")

        assert resp.status_code == 200
        assert any(
            "Corrupted recommendation row" in rec.getMessage()
            for rec in caplog.records
        )
    finally:
        btc_api.app.dependency_overrides.clear()


# ── B4b.2: integration with run_optimization_v2 ─────────────────────────────


def test_post_recalibrate_uses_v2_optimizer_with_grid(tmp_path, monkeypatch):
    """POST endpoint persists a row whose report includes the v2 grid (not stub)."""
    import btc_api
    from fastapi.testclient import TestClient

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    btc_api.app.dependency_overrides[btc_api.verify_api_key] = lambda: None
    monkeypatch.setattr(btc_api, "load_config", lambda: {
        "kill_switch": {"v2": {
            "aggressiveness": 50,
            "thresholds": {
                "portfolio_dd_reduced":     {"min": -0.08, "max": -0.03},
                "portfolio_dd_frozen":      {"min": -0.15, "max": -0.06},
                "velocity_sl_count":        {"min": 10, "max": 3},
                "velocity_window_hours":    {"min": 24, "max": 6},
                "baseline_sigma_multiplier": {"min": 3.0, "max": 1.0},
            },
            "velocity_cooldown_hours": 4,
            "concurrent_alert_threshold": 3,
            "baseline_min_trades": 100,
            "baseline_stale_days": 7,
            "regime_adjustments": {"bull_bonus": 10, "bear_penalty": 10},
            "advanced_overrides": {"regime_adjustment_enabled": True},
            "auto_calibrator": {
                "safety_net_days": 30,
                "backtest_window_days": 365,
                "dd_target": -0.10,
            },
        }},
    })

    try:
        client = TestClient(btc_api.app)
        resp = client.post("/kill_switch/recalibrate")
        assert resp.status_code == 200
        body = resp.json()

        rec_id = body["recommendation_id"]
        conn = btc_api.get_db()
        try:
            row = conn.execute(
                "SELECT report_json FROM kill_switch_recommendations WHERE id = ?",
                (rec_id,),
            ).fetchone()
        finally:
            conn.close()
        import json
        report = json.loads(row[0])
        # Real v2 report has stub=False and includes grid + dd_target
        assert report["stub"] is False
        assert "grid" in report
        assert len(report["grid"]) == 21
        assert report["dd_target"] == pytest.approx(-0.10)
    finally:
        btc_api.app.dependency_overrides.clear()


def test_post_recalibrate_falls_back_to_stub_when_v2_raises(tmp_path, monkeypatch):
    """If run_optimization_v2 raises, the endpoint logs and falls back to stub."""
    import btc_api
    from fastapi.testclient import TestClient
    import strategy.kill_switch_v2_optimizer as opt_mod

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    btc_api.app.dependency_overrides[btc_api.verify_api_key] = lambda: None

    def _boom(*a, **kw):
        raise RuntimeError("simulated optimizer failure")
    monkeypatch.setattr(opt_mod, "run_optimization_v2", _boom)

    try:
        client = TestClient(btc_api.app)
        resp = client.post("/kill_switch/recalibrate")
        assert resp.status_code == 200
        body = resp.json()
        # Fell back to stub → status="no_feasible"
        assert body["status"] == "no_feasible"

        rec_id = body["recommendation_id"]
        conn = btc_api.get_db()
        try:
            row = conn.execute(
                "SELECT report_json FROM kill_switch_recommendations WHERE id = ?",
                (rec_id,),
            ).fetchone()
        finally:
            conn.close()
        import json
        report = json.loads(row[0])
        # Stub is True in fallback path
        assert report.get("stub") is True
    finally:
        btc_api.app.dependency_overrides.clear()


# ── B4b.3: should_run_regime_change ─────────────────────────────────────────


def test_should_run_regime_change_first_call_returns_false():
    """No baseline yet (last_calib_score=None) → False (no crossing to compare)."""
    from strategy.kill_switch_v2_calibrator import should_run_regime_change
    assert should_run_regime_change(None, 75.0) is False


def test_should_run_regime_change_current_none_returns_false():
    """No current data (e.g., regime cache empty) → False."""
    from strategy.kill_switch_v2_calibrator import should_run_regime_change
    assert should_run_regime_change(50.0, None) is False


def test_should_run_regime_change_same_band_returns_false():
    """Both in NEUTRAL band [40, 60) → no crossing."""
    from strategy.kill_switch_v2_calibrator import should_run_regime_change
    assert should_run_regime_change(45.0, 55.0) is False


def test_should_run_regime_change_neutral_to_bull_crosses_60_returns_true():
    """45 (NEUTRAL) → 70 (BULL) → crossed 60 → True."""
    from strategy.kill_switch_v2_calibrator import should_run_regime_change
    assert should_run_regime_change(45.0, 70.0) is True


def test_should_run_regime_change_neutral_to_bear_crosses_40_returns_true():
    """50 (NEUTRAL) → 30 (BEAR) → crossed 40 → True."""
    from strategy.kill_switch_v2_calibrator import should_run_regime_change
    assert should_run_regime_change(50.0, 30.0) is True


def test_should_run_regime_change_bull_to_bear_crosses_both_returns_true():
    """75 → 25 → crossed both 60 and 40 → True."""
    from strategy.kill_switch_v2_calibrator import should_run_regime_change
    assert should_run_regime_change(75.0, 25.0) is True


# ── B4b.3: should_run_portfolio_dd_degradation ──────────────────────────────


def test_should_run_portfolio_dd_degradation_no_baseline_returns_false():
    from strategy.kill_switch_v2_calibrator import should_run_portfolio_dd_degradation
    assert should_run_portfolio_dd_degradation(
        current_dd=-0.10, last_applied_projected_dd=None, multiplier=1.5,
    ) is False


def test_should_run_portfolio_dd_degradation_above_threshold_returns_false():
    """current_dd=-0.04, baseline=-0.05, threshold=1.5*-0.05=-0.075. -0.04 > -0.075 → False."""
    from strategy.kill_switch_v2_calibrator import should_run_portfolio_dd_degradation
    assert should_run_portfolio_dd_degradation(
        current_dd=-0.04, last_applied_projected_dd=-0.05, multiplier=1.5,
    ) is False


def test_should_run_portfolio_dd_degradation_at_threshold_returns_false():
    """Strict `<`: equal threshold doesn't fire."""
    from strategy.kill_switch_v2_calibrator import should_run_portfolio_dd_degradation
    # current=-0.075, baseline=-0.05, threshold=-0.075 exact
    assert should_run_portfolio_dd_degradation(
        current_dd=-0.075, last_applied_projected_dd=-0.05, multiplier=1.5,
    ) is False


def test_should_run_portfolio_dd_degradation_below_threshold_returns_true():
    """current_dd=-0.10, baseline=-0.05, threshold=-0.075. -0.10 < -0.075 → True."""
    from strategy.kill_switch_v2_calibrator import should_run_portfolio_dd_degradation
    assert should_run_portfolio_dd_degradation(
        current_dd=-0.10, last_applied_projected_dd=-0.05, multiplier=1.5,
    ) is True


def test_should_run_portfolio_dd_degradation_zero_baseline_returns_false():
    """If baseline DD=0 (no historical drawdown), threshold is 0. Any negative current
    crosses, but this is an edge case — treat as False (no meaningful baseline)."""
    from strategy.kill_switch_v2_calibrator import should_run_portfolio_dd_degradation
    assert should_run_portfolio_dd_degradation(
        current_dd=-0.05, last_applied_projected_dd=0.0, multiplier=1.5,
    ) is False


# ── B4b.3: should_run_event_cascade ─────────────────────────────────────────


def test_should_run_event_cascade_below_threshold_returns_false():
    from strategy.kill_switch_v2_calibrator import should_run_event_cascade
    assert should_run_event_cascade(symbols_in_alert_count=2, threshold=3) is False


def test_should_run_event_cascade_at_threshold_returns_true():
    """Boundary: count == threshold → True (>= semantics)."""
    from strategy.kill_switch_v2_calibrator import should_run_event_cascade
    assert should_run_event_cascade(symbols_in_alert_count=3, threshold=3) is True


def test_should_run_event_cascade_above_threshold_returns_true():
    from strategy.kill_switch_v2_calibrator import should_run_event_cascade
    assert should_run_event_cascade(symbols_in_alert_count=5, threshold=3) is True


# ── B4b.3: is_rate_limit_ok ─────────────────────────────────────────────────


def test_is_rate_limit_ok_manual_bypasses():
    """Manual trigger always passes regardless of cooldown / max_per_day."""
    from strategy.kill_switch_v2_calibrator import is_rate_limit_ok
    from datetime import datetime, timezone
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    last_run = (now).isoformat()  # just ran
    assert is_rate_limit_ok(
        last_run_ts=last_run, now=now,
        max_per_day_count=1, today_count=5,
        min_cooldown_hours=6.0, trigger_kind="manual",
    ) is True


def test_is_rate_limit_ok_safety_net_bypasses():
    """safety_net guarantees a tick — bypasses cooldown."""
    from strategy.kill_switch_v2_calibrator import is_rate_limit_ok
    from datetime import datetime, timezone
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    assert is_rate_limit_ok(
        last_run_ts=now.isoformat(), now=now,
        max_per_day_count=1, today_count=5,
        min_cooldown_hours=6.0, trigger_kind="safety_net",
    ) is True


def test_is_rate_limit_ok_no_prior_run_returns_true():
    """First-ever run for non-bypass trigger → True."""
    from strategy.kill_switch_v2_calibrator import is_rate_limit_ok
    from datetime import datetime, timezone
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    assert is_rate_limit_ok(
        last_run_ts=None, now=now,
        max_per_day_count=1, today_count=0,
        min_cooldown_hours=6.0, trigger_kind="auto",
    ) is True


def test_is_rate_limit_ok_within_cooldown_returns_false():
    from strategy.kill_switch_v2_calibrator import is_rate_limit_ok
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    # 2h ago < 6h cooldown
    last_run = (now - timedelta(hours=2)).isoformat()
    assert is_rate_limit_ok(
        last_run_ts=last_run, now=now,
        max_per_day_count=1, today_count=0,
        min_cooldown_hours=6.0, trigger_kind="auto",
    ) is False


def test_is_rate_limit_ok_after_cooldown_returns_true():
    from strategy.kill_switch_v2_calibrator import is_rate_limit_ok
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    last_run = (now - timedelta(hours=7)).isoformat()
    assert is_rate_limit_ok(
        last_run_ts=last_run, now=now,
        max_per_day_count=1, today_count=0,
        min_cooldown_hours=6.0, trigger_kind="auto",
    ) is True


def test_is_rate_limit_ok_max_per_day_reached_returns_false():
    """Even after cooldown elapsed, today_count >= max_per_day blocks."""
    from strategy.kill_switch_v2_calibrator import is_rate_limit_ok
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    last_run = (now - timedelta(hours=10)).isoformat()
    assert is_rate_limit_ok(
        last_run_ts=last_run, now=now,
        max_per_day_count=1, today_count=1,
        min_cooldown_hours=6.0, trigger_kind="auto",
    ) is False


# ── B4b.3: DB glue ──────────────────────────────────────────────────────────


def test_count_recalibrations_today_empty_returns_zero(tmp_path, monkeypatch):
    import btc_api
    from strategy.kill_switch_v2_calibrator import _count_recalibrations_today
    from datetime import datetime, timezone

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    assert _count_recalibrations_today(now) == 0


def test_count_recalibrations_today_counts_only_today_utc(tmp_path, monkeypatch):
    """Rows with ts on different UTC days are NOT counted."""
    import btc_api
    from strategy.kill_switch_v2_calibrator import (
        _count_recalibrations_today, _persist_recommendation, run_optimization_stub,
    )
    from datetime import datetime, timezone, timedelta

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    today = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    yesterday = today - timedelta(days=1)
    same_day_earlier = datetime(2026, 4, 25, 1, 0, tzinfo=timezone.utc)

    result = run_optimization_stub({})
    _persist_recommendation(triggered_by=["manual"], result=result, now=yesterday)
    _persist_recommendation(triggered_by=["manual"], result=result, now=same_day_earlier)
    _persist_recommendation(triggered_by=["manual"], result=result, now=today)

    # 2 rows on 2026-04-25 UTC, 1 on 2026-04-24
    assert _count_recalibrations_today(today) == 2


def test_load_last_applied_recommendation_empty_returns_none(tmp_path, monkeypatch):
    import btc_api
    from strategy.kill_switch_v2_calibrator import _load_last_applied_recommendation

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    assert _load_last_applied_recommendation() is None


def test_load_last_applied_recommendation_returns_latest_applied(tmp_path, monkeypatch):
    """Returns the most recent row with status='applied'; ignores pending/ignored."""
    import btc_api
    from strategy.kill_switch_v2_calibrator import (
        _load_last_applied_recommendation,
    )
    from datetime import datetime, timezone

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    conn = btc_api.get_db()
    try:
        # pending row
        conn.execute(
            "INSERT INTO kill_switch_recommendations "
            "(ts, triggered_by, slider_value, projected_pnl, projected_dd, status, report_json) "
            "VALUES (?, '[\"manual\"]', 50, 100.0, -0.05, 'pending', '{}')",
            ("2026-04-20T10:00:00+00:00",),
        )
        # applied row earlier
        conn.execute(
            "INSERT INTO kill_switch_recommendations "
            "(ts, triggered_by, slider_value, projected_pnl, projected_dd, status, report_json) "
            "VALUES (?, '[\"manual\"]', 60, 200.0, -0.04, 'applied', '{}')",
            ("2026-04-22T10:00:00+00:00",),
        )
        # applied row later — this is what we want returned
        conn.execute(
            "INSERT INTO kill_switch_recommendations "
            "(ts, triggered_by, slider_value, projected_pnl, projected_dd, status, report_json) "
            "VALUES (?, '[\"manual\"]', 70, 300.0, -0.03, 'applied', '{}')",
            ("2026-04-24T10:00:00+00:00",),
        )
        conn.commit()
    finally:
        conn.close()

    row = _load_last_applied_recommendation()
    assert row is not None
    assert row["slider_value"] == 70
    assert row["projected_dd"] == pytest.approx(-0.03)


def test_load_last_calibration_regime_score_empty_returns_none(tmp_path, monkeypatch):
    import btc_api
    from strategy.kill_switch_v2_calibrator import _load_last_calibration_regime_score

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    assert _load_last_calibration_regime_score() is None


def test_load_last_calibration_regime_score_extracts_from_report_json(tmp_path, monkeypatch):
    import btc_api
    from strategy.kill_switch_v2_calibrator import _load_last_calibration_regime_score
    import json

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    conn = btc_api.get_db()
    try:
        conn.execute(
            "INSERT INTO kill_switch_recommendations "
            "(ts, triggered_by, slider_value, status, report_json) "
            "VALUES (?, '[\"safety_net\"]', NULL, 'no_feasible', ?)",
            (
                "2026-04-25T10:00:00+00:00",
                json.dumps({"regime_score": 72.5, "stub": False}),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    assert _load_last_calibration_regime_score() == pytest.approx(72.5)


def test_load_last_calibration_regime_score_handles_missing_field(tmp_path, monkeypatch):
    """If report_json lacks regime_score (or is malformed), return None."""
    import btc_api
    from strategy.kill_switch_v2_calibrator import _load_last_calibration_regime_score

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    conn = btc_api.get_db()
    try:
        conn.execute(
            "INSERT INTO kill_switch_recommendations "
            "(ts, triggered_by, slider_value, status, report_json) "
            "VALUES (?, '[\"safety_net\"]', NULL, 'no_feasible', '{}')",
            ("2026-04-25T10:00:00+00:00",),
        )
        conn.commit()
    finally:
        conn.close()

    assert _load_last_calibration_regime_score() is None


# ── B4b.3: DB glue (continued) ──────────────────────────────────────────────


def test_count_symbols_with_recent_alerts_empty_returns_zero(tmp_path, monkeypatch):
    import btc_api
    from strategy.kill_switch_v2_calibrator import _count_symbols_with_recent_alerts

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    assert _count_symbols_with_recent_alerts(window_hours=72.0) == 0


def test_count_symbols_with_recent_alerts_counts_distinct_symbols(tmp_path, monkeypatch):
    """Distinct ALERT/REDUCED/FROZEN symbols within window — multiple rows per symbol = 1."""
    import btc_api
    from strategy.kill_switch_v2_calibrator import _count_symbols_with_recent_alerts
    from datetime import datetime, timezone, timedelta

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    now = datetime.now(tz=timezone.utc)
    inside = (now - timedelta(hours=10)).isoformat()
    outside = (now - timedelta(hours=100)).isoformat()

    conn = btc_api.get_db()
    try:
        # BTC ALERT (inside)
        conn.execute(
            "INSERT INTO kill_switch_decisions "
            "(ts, symbol, engine, per_symbol_tier, portfolio_tier, size_factor, skip) "
            "VALUES (?, 'BTCUSDT', 'v2_shadow', 'ALERT', 'NORMAL', 0.5, 0)",
            (inside,),
        )
        # BTC ALERT again (inside) — should still count as 1 distinct symbol
        conn.execute(
            "INSERT INTO kill_switch_decisions "
            "(ts, symbol, engine, per_symbol_tier, portfolio_tier, size_factor, skip) "
            "VALUES (?, 'BTCUSDT', 'v2_shadow', 'ALERT', 'NORMAL', 0.5, 0)",
            ((now - timedelta(hours=5)).isoformat(),),
        )
        # ETH portfolio REDUCED (inside) — counts
        conn.execute(
            "INSERT INTO kill_switch_decisions "
            "(ts, symbol, engine, per_symbol_tier, portfolio_tier, size_factor, skip) "
            "VALUES (?, 'ETHUSDT', 'v2_shadow', 'NORMAL', 'REDUCED', 0.5, 0)",
            (inside,),
        )
        # ADA NORMAL/NORMAL (inside) — does NOT count
        conn.execute(
            "INSERT INTO kill_switch_decisions "
            "(ts, symbol, engine, per_symbol_tier, portfolio_tier, size_factor, skip) "
            "VALUES (?, 'ADAUSDT', 'v2_shadow', 'NORMAL', 'NORMAL', 1.0, 0)",
            (inside,),
        )
        # SOL ALERT (outside window) — does NOT count
        conn.execute(
            "INSERT INTO kill_switch_decisions "
            "(ts, symbol, engine, per_symbol_tier, portfolio_tier, size_factor, skip) "
            "VALUES (?, 'SOLUSDT', 'v2_shadow', 'ALERT', 'NORMAL', 0.5, 0)",
            (outside,),
        )
        # XRP FROZEN (inside) — counts
        conn.execute(
            "INSERT INTO kill_switch_decisions "
            "(ts, symbol, engine, per_symbol_tier, portfolio_tier, size_factor, skip) "
            "VALUES (?, 'XRPUSDT', 'v2_shadow', 'NORMAL', 'FROZEN', 0.0, 1)",
            (inside,),
        )
        # v1 engine ALERT (inside) — does NOT count (only v2_shadow)
        conn.execute(
            "INSERT INTO kill_switch_decisions "
            "(ts, symbol, engine, per_symbol_tier, portfolio_tier, size_factor, skip) "
            "VALUES (?, 'DOGEUSDT', 'v1', 'ALERT', 'NORMAL', 0.5, 0)",
            (inside,),
        )
        conn.commit()
    finally:
        conn.close()

    # Distinct: BTC, ETH, XRP = 3
    assert _count_symbols_with_recent_alerts(window_hours=72.0) == 3


def test_mark_prior_pending_as_superseded_only_pending(tmp_path, monkeypatch):
    """Only prior 'pending' rows get marked superseded; applied/ignored stay."""
    import btc_api
    from strategy.kill_switch_v2_calibrator import _mark_prior_pending_as_superseded

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    conn = btc_api.get_db()
    try:
        # 3 rows: pending(id=1), applied(id=2), pending(id=3 — the "new" one)
        conn.execute(
            "INSERT INTO kill_switch_recommendations "
            "(ts, triggered_by, status, report_json) "
            "VALUES ('2026-04-20T10:00:00+00:00', '[]', 'pending', '{}')",
        )
        conn.execute(
            "INSERT INTO kill_switch_recommendations "
            "(ts, triggered_by, status, applied_ts, applied_by, report_json) "
            "VALUES ('2026-04-21T10:00:00+00:00', '[]', 'applied', "
            "'2026-04-21T11:00:00+00:00', 'operator', '{}')",
        )
        conn.execute(
            "INSERT INTO kill_switch_recommendations "
            "(ts, triggered_by, status, report_json) "
            "VALUES ('2026-04-25T10:00:00+00:00', '[]', 'pending', '{}')",
        )
        conn.commit()
    finally:
        conn.close()

    # Mark prior pending as superseded; new id is 3
    _mark_prior_pending_as_superseded(new_id=3)

    conn = btc_api.get_db()
    try:
        rows = conn.execute(
            "SELECT id, status FROM kill_switch_recommendations ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    statuses = {r[0]: r[1] for r in rows}
    assert statuses[1] == "superseded"
    assert statuses[2] == "applied"   # untouched
    assert statuses[3] == "pending"    # the new one stays
