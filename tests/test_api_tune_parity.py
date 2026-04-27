"""Parity test for /tune endpoints."""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient


BASELINE_PATH = pathlib.Path(__file__).parent / "_baselines" / "tune.json"


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


def test_tune_latest_empty(client):
    """GET /tune/latest returns null when no tune results in DB."""
    r = client.get("/tune/latest")
    assert r.status_code == 200
    assert r.json() is None


def test_tune_apply_no_auth(client):
    """POST /tune/apply without auth → 401."""
    r = client.post("/tune/apply")
    assert r.status_code == 401


def test_tune_reject_no_auth(client):
    """POST /tune/reject without auth → 401."""
    r = client.post("/tune/reject")
    assert r.status_code == 401
