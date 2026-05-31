"""Parity test for /kill_switch endpoints."""
from __future__ import annotations

import json
import pathlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


BASELINE_PATH = pathlib.Path(__file__).parent / "_baselines" / "kill_switch.json"


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient with isolated DB + test-key auth."""
    db_path = tmp_path / "test.db"

    import db.connection as dbconn
    monkeypatch.setattr(dbconn, "DB_FILE", str(db_path))

    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(db_path))

    import api.config as _ac
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"api_key": "test-key"}))
    monkeypatch.setattr(_ac, "CONFIG_FILE", str(config_path))
    monkeypatch.setattr(_ac, "DEFAULTS_FILE", "/tmp/_nonexistent_defaults.json")
    monkeypatch.setattr(_ac, "SECRETS_FILE", "/tmp/_nonexistent_secrets.json")
    monkeypatch.setattr(btc_api, "CONFIG_FILE", str(config_path))

    from db.schema import init_db
    init_db()

    from btc_api import app
    return TestClient(app)


def test_kill_switch_current_state_auth(client):
    """GET /kill_switch/current_state with auth returns mocked state."""
    with patch("observability.get_current_state", return_value={"state": "ok", "symbols": {}}):
        r = client.get("/kill_switch/current_state", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    assert r.json() == {"state": "ok", "symbols": {}}


def test_kill_switch_current_state_no_auth(client):
    """GET /kill_switch/current_state without auth → 401."""
    r = client.get("/kill_switch/current_state")
    assert r.status_code == 401


def test_kill_switch_recommendations_empty(client):
    """GET /kill_switch/recommendations returns empty list when DB is empty."""
    r = client.get("/kill_switch/recommendations", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    assert r.json() == []


def test_kill_switch_recalibrate_no_auth(client):
    """POST /kill_switch/recalibrate without auth → 401."""
    r = client.post("/kill_switch/recalibrate")
    assert r.status_code == 401


def test_kill_switch_apply_no_auth(client):
    """POST /kill_switch/recommendations/1/apply without auth → 401."""
    r = client.post("/kill_switch/recommendations/1/apply")
    assert r.status_code == 401


def test_kill_switch_ignore_no_auth(client):
    """POST /kill_switch/recommendations/1/ignore without auth → 401."""
    r = client.post("/kill_switch/recommendations/1/ignore")
    assert r.status_code == 401


# ── #550: apply/ignore TOCTOU — atomic UPDATE is the authority ───────────────
#
# These call the endpoint functions directly (no TestClient): the admin-role
# dependency reads request.state.user from AuthMiddleware, which the parity
# client cannot easily satisfy. Calling the handler directly exercises exactly
# the apply/ignore body — the only thing #550 changes — and asserts the 409 the
# guarded UPDATE produces on a lost race.


@pytest.fixture
def apidb(monkeypatch, tmp_path):
    """Wire DB_FILE + config at the test DB and init the schema. No TestClient."""
    db_path = tmp_path / "test.db"

    import db.connection as dbconn
    monkeypatch.setattr(dbconn, "DB_FILE", str(db_path))

    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(db_path))

    import api.config as _ac
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"api_key": "test-key"}))
    monkeypatch.setattr(_ac, "CONFIG_FILE", str(config_path))
    monkeypatch.setattr(_ac, "DEFAULTS_FILE", "/tmp/_nonexistent_defaults.json")
    monkeypatch.setattr(_ac, "SECRETS_FILE", "/tmp/_nonexistent_secrets.json")
    monkeypatch.setattr(btc_api, "CONFIG_FILE", str(config_path))

    from db.schema import init_db
    init_db()
    return str(db_path)


def _seed_recommendation(status, slider_value=50):
    """Insert one recommendation row with the given status; return its id."""
    from datetime import datetime, timezone
    from db.transaction import transaction

    now = datetime.now(tz=timezone.utc).isoformat()
    with transaction() as conn:
        cur = conn.execute(
            """INSERT INTO kill_switch_recommendations
                 (ts, triggered_by, slider_value, projected_pnl, projected_dd,
                  status, applied_ts, applied_by, report_json)
               VALUES (?, ?, ?, NULL, NULL, ?, NULL, NULL, ?)""",
            (now, json.dumps(["manual"]), slider_value, status, json.dumps({})),
        )
        return int(cur.lastrowid)


def _stale_pending_cm(rec_id, slider_value):
    """Fake snapshot_connection() whose pre-check read reports the row as
    'pending' — a stale read that lost the race to a concurrent transition. The
    REAL row (seeded non-pending) is what the guarded UPDATE sees, so rowcount=0."""
    class _Cur:
        def fetchone(self_):
            # (id, status, slider_value) matches the apply pre-check SELECT;
            # ignore reads only (id, status) — extra field harmless.
            return (rec_id, "pending", slider_value)

    class _Conn:
        def execute(self_, *a, **k):
            return _Cur()

    class _CM:
        def __enter__(self_):
            return _Conn()

        def __exit__(self_, *exc):
            return False

    return lambda: _CM()


def test_apply_lost_race_returns_409_and_does_not_write_config(apidb, monkeypatch):
    """#550: a stale pre-check that still sees 'pending' must NOT write config
    when the row was concurrently transitioned. The guarded UPDATE matches 0 rows
    -> 409, and save_config is never called.

    Against the pre-#550 code (config written first, unguarded UPDATE) this would
    write config and mark 'applied' — so the test fails on the old path and
    passes only with the atomic-gate fix."""
    from fastapi import HTTPException
    import api.kill_switch as ks

    # A concurrent ignore already transitioned the real row.
    rec_id = _seed_recommendation(status="ignored", slider_value=50)

    called = {"save": False}
    monkeypatch.setattr(ks, "save_config",
                        lambda partial: called.__setitem__("save", True))
    monkeypatch.setattr(ks, "snapshot_connection",
                        _stale_pending_cm(rec_id, 50))

    with pytest.raises(HTTPException) as exc:
        ks.kill_switch_apply_recommendation(rec_id)
    assert exc.value.status_code == 409, exc.value.detail
    assert called["save"] is False, "config must not be written on a lost race"


def test_ignore_lost_race_returns_409(apidb, monkeypatch):
    """#550: ignore must also gate on the atomic UPDATE. A stale pre-check that
    sees 'pending' raises 409 when the row was already transitioned."""
    from fastapi import HTTPException
    import api.kill_switch as ks

    # A concurrent apply already transitioned the real row.
    rec_id = _seed_recommendation(status="applied", slider_value=50)

    monkeypatch.setattr(ks, "snapshot_connection",
                        _stale_pending_cm(rec_id, 50))

    with pytest.raises(HTTPException) as exc:
        ks.kill_switch_ignore_recommendation(rec_id)
    assert exc.value.status_code == 409, exc.value.detail


def test_apply_happy_path_marks_applied_and_writes_config(apidb, monkeypatch):
    """#550 regression: the uncontended apply still writes config with the right
    slider and marks the row applied (the reorder did not break the success
    path)."""
    import api.kill_switch as ks

    rec_id = _seed_recommendation(status="pending", slider_value=72)

    saved = {}
    monkeypatch.setattr(ks, "save_config", lambda partial: saved.update(partial))

    out = ks.kill_switch_apply_recommendation(rec_id)
    assert out["status"] == "applied"
    assert saved["kill_switch"]["v2"]["aggressiveness"] == 72
