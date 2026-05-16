"""Tests for B.5 follow-up B: capital + user_preferences endpoints.

Synthetic fixtures only — no production data.
Pre-reg: docs/superpowers/plans/2026-05-16-multi-tenant-b5-capital-prefs-pre-reg.md
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def initialized_db(tmp_path, monkeypatch):
    """Fresh schema'd DB per test using real btc_api pattern."""
    import btc_api
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    from db.schema import init_db
    init_db()
    yield db_path


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with fresh DB + minimal test config (api_key='test-key')."""
    import btc_api
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()
    # Test config
    import json
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"api_key": "test-key"}))
    monkeypatch.setattr(btc_api, "CONFIG_FILE", str(cfg_path), raising=False)
    import api.config as _ac
    monkeypatch.setattr(_ac, "CONFIG_FILE", str(cfg_path), raising=False)
    monkeypatch.setattr(btc_api, "DEFAULTS_FILE", str(tmp_path / "no_def.json"), raising=False)
    monkeypatch.setattr(_ac, "DEFAULTS_FILE", str(tmp_path / "no_def.json"), raising=False)
    monkeypatch.setattr(btc_api, "SECRETS_FILE", str(tmp_path / "no_sec.json"), raising=False)
    monkeypatch.setattr(_ac, "SECRETS_FILE", str(tmp_path / "no_sec.json"), raising=False)
    return TestClient(btc_api.app)


# ---------------------------------------------------------------------------
# DB layer: db/capital.py
# ---------------------------------------------------------------------------


class TestCapitalDB:
    def test_get_returns_none_when_missing(self, initialized_db):
        from db.capital import db_get_capital
        assert db_get_capital(tenant_id=1) is None

    def test_upsert_creates_row(self, initialized_db):
        from db.capital import db_upsert_capital, db_get_capital
        row = db_upsert_capital(tenant_id=1, balance=10000.0)
        assert row["tenant_id"] == 1
        assert row["balance"] == 10000.0
        # peak_balance defaulted to balance when not provided + row new
        assert row["peak_balance"] == 10000.0
        # max_drawdown_pct None for new row
        assert row["max_drawdown_pct"] is None
        # Persists
        assert db_get_capital(tenant_id=1)["balance"] == 10000.0

    def test_upsert_replaces_existing(self, initialized_db):
        from db.capital import db_upsert_capital
        db_upsert_capital(tenant_id=1, balance=10000.0)
        updated = db_upsert_capital(tenant_id=1, balance=11000.0, peak_balance=12000.0)
        assert updated["balance"] == 11000.0
        assert updated["peak_balance"] == 12000.0

    def test_upsert_preserves_peak_when_omitted(self, initialized_db):
        from db.capital import db_upsert_capital
        db_upsert_capital(tenant_id=1, balance=10000.0, peak_balance=15000.0)
        # Update balance only, peak should stay
        updated = db_upsert_capital(tenant_id=1, balance=9500.0)
        assert updated["balance"] == 9500.0
        assert updated["peak_balance"] == 15000.0  # preserved

    def test_upsert_preserves_max_drawdown_when_omitted(self, initialized_db):
        from db.capital import db_upsert_capital
        db_upsert_capital(tenant_id=1, balance=10000.0, max_drawdown_pct=-15.0)
        updated = db_upsert_capital(tenant_id=1, balance=11000.0)
        assert updated["max_drawdown_pct"] == -15.0  # preserved

    def test_tenant_isolation(self, initialized_db):
        from db.capital import db_upsert_capital, db_get_capital
        db_upsert_capital(tenant_id=1, balance=10000.0)
        db_upsert_capital(tenant_id=2, balance=20000.0)
        assert db_get_capital(tenant_id=1)["balance"] == 10000.0
        assert db_get_capital(tenant_id=2)["balance"] == 20000.0


# ---------------------------------------------------------------------------
# DB layer: db/user_preferences.py
# ---------------------------------------------------------------------------


class TestUserPreferencesDB:
    def test_get_returns_none_when_missing(self, initialized_db):
        from db.user_preferences import db_get_user_preferences
        assert db_get_user_preferences(tenant_id=1) is None

    def test_upsert_creates_with_defaults(self, initialized_db):
        from db.user_preferences import db_upsert_user_preferences
        row = db_upsert_user_preferences(tenant_id=1)
        assert row["tenant_id"] == 1
        # min_score defaults to 4 per schema
        assert row["min_score"] == 4
        assert row["symbol_filter"] is None
        assert row["notify_channels"] is None

    def test_upsert_with_all_fields(self, initialized_db):
        from db.user_preferences import db_upsert_user_preferences
        row = db_upsert_user_preferences(
            tenant_id=1,
            symbol_filter=["BTCUSDT", "ETHUSDT"],
            min_score=5,
            notify_channels={"telegram_chat_id": "12345"},
        )
        assert row["symbol_filter"] == ["BTCUSDT", "ETHUSDT"]
        assert row["min_score"] == 5
        assert row["notify_channels"] == {"telegram_chat_id": "12345"}

    def test_upsert_preserves_fields_when_none_passed(self, initialized_db):
        from db.user_preferences import db_upsert_user_preferences
        db_upsert_user_preferences(
            tenant_id=1, symbol_filter=["BTC"], min_score=6,
            notify_channels={"email": "a@b.c"},
        )
        # Update only min_score, others preserved
        updated = db_upsert_user_preferences(tenant_id=1, min_score=7)
        assert updated["min_score"] == 7
        assert updated["symbol_filter"] == ["BTC"]
        assert updated["notify_channels"] == {"email": "a@b.c"}


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


class TestCapitalEndpoint:
    def test_get_uninitialized_returns_404(self, client):
        resp = client.get("/capital")
        assert resp.status_code == 404
        assert "not initialized" in resp.json()["detail"].lower()

    def test_put_then_get_roundtrip(self, client):
        # PUT requires api_key
        resp = client.put(
            "/capital",
            json={"balance": 10000.0, "peak_balance": 11000.0},
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 200
        assert resp.json()["capital"]["balance"] == 10000.0

        # GET sees it
        resp = client.get("/capital")
        assert resp.status_code == 200
        body = resp.json()
        assert body["balance"] == 10000.0
        assert body["peak_balance"] == 11000.0

    def test_put_validates_balance_non_negative(self, client):
        resp = client.put(
            "/capital",
            json={"balance": -100},
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 422


class TestPreferencesEndpoint:
    def test_get_unset_returns_defaults(self, client):
        resp = client.get("/preferences")
        assert resp.status_code == 200
        body = resp.json()
        assert body["min_score"] == 4
        assert body["symbol_filter"] is None
        assert body["notify_channels"] is None

    def test_put_then_get_roundtrip(self, client):
        resp = client.put(
            "/preferences",
            json={
                "symbol_filter": ["BTCUSDT", "ETHUSDT"],
                "min_score": 5,
                "notify_channels": {"telegram_chat_id": "abc"},
            },
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 200
        body = resp.json()["preferences"]
        assert body["symbol_filter"] == ["BTCUSDT", "ETHUSDT"]
        assert body["min_score"] == 5

        # GET sees it
        resp = client.get("/preferences")
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol_filter"] == ["BTCUSDT", "ETHUSDT"]
        assert body["min_score"] == 5

    def test_put_validates_min_score_range(self, client):
        resp = client.put(
            "/preferences",
            json={"min_score": 10},  # out of range (max 9)
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 422

    def test_partial_put_preserves_other_fields(self, client):
        # Initial PUT with all fields
        client.put(
            "/preferences",
            json={"symbol_filter": ["BTC"], "min_score": 6,
                  "notify_channels": {"email": "x@y"}},
            headers={"X-API-Key": "test-key"},
        )
        # Partial PUT only min_score
        resp = client.put(
            "/preferences",
            json={"min_score": 7},
            headers={"X-API-Key": "test-key"},
        )
        body = resp.json()["preferences"]
        assert body["min_score"] == 7
        assert body["symbol_filter"] == ["BTC"]  # preserved
        assert body["notify_channels"] == {"email": "x@y"}  # preserved
