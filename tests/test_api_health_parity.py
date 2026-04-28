"""Parity test for /health endpoints."""
from __future__ import annotations

import json
import pathlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


BASELINE_PATH = pathlib.Path(__file__).parent / "_baselines" / "health.json"


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


def test_health_liveness_no_scanner(client):
    """GET /health returns 503 when scanner is not running."""
    r = client.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["healthy"] is False
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["scanner"] == "stopped"


def test_health_symbols_auth_empty(client):
    """GET /health/symbols with auth returns empty list when DB is empty."""
    r = client.get("/health/symbols", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    assert r.json() == {"symbols": []}


def test_health_symbols_no_auth(client):
    """GET /health/symbols without auth → 401."""
    r = client.get("/health/symbols")
    assert r.status_code == 401


def test_health_events_auth_empty(client):
    """GET /health/events with auth returns empty list when DB is empty."""
    r = client.get("/health/events", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    assert r.json() == {"events": []}


def test_health_dashboard_auth_mocked(client):
    """GET /health/dashboard with auth returns mocked dashboard state."""
    mock_dashboard = {"symbols": {}, "portfolio": {}, "alerts_24h": []}
    with patch("health.get_dashboard_state", return_value=mock_dashboard):
        r = client.get("/health/dashboard", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    assert r.json() == mock_dashboard


def test_health_reactivate_auth_mocked(client):
    """POST /health/reactivate/{symbol} with auth calls reactivate_symbol and returns ok."""
    with patch("health.reactivate_symbol"), patch("health.get_symbol_state", return_value="PROBATION"):
        r = client.post(
            "/health/reactivate/BTCUSDT",
            json={"reason": "manual"},
            headers={"X-API-Key": "test-key"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["symbol"] == "BTCUSDT"
    assert body["state"] == "PROBATION"
