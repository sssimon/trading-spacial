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
