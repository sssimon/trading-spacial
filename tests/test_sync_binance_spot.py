"""Fail-closed contract of sync_tenant (Task 8 + review fix: transport handling).

sync_tenant must: skip non-ACTIVE credentials, map client errors to credential
status, and treat a transient transport blip as transient (status TRANSPORT_ERROR,
credential STAYS ACTIVE — a downed network is not a credential problem).
"""
import sqlite3
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def con(monkeypatch):
    monkeypatch.setenv("TRADING_BINANCE_MASTER_KEY", Fernet.generate_key().decode())
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from db.schema import _migrate_binance_credentials
    _migrate_binance_credentials(c)
    return c


def _add_cred(con, status="ACTIVE"):
    from db.binance_credentials import db_upsert_binance_credential, db_set_credential_status
    db_upsert_binance_credential(con, tenant_id=2, api_key_public="PUB", secret_plaintext="s")
    if status != "ACTIVE":
        db_set_credential_status(con, tenant_id=2, status=status)


def _status(con):
    return con.execute("SELECT status FROM binance_credentials WHERE tenant_id=2").fetchone()[0]


def test_no_credential(con):
    from tools.sync_binance_spot import sync_tenant
    assert sync_tenant(con, 2)["status"] == "NO_CREDENTIAL"


def test_skips_non_active_credential(con):
    from tools.sync_binance_spot import sync_tenant
    _add_cred(con, status="REVOKED")
    out = sync_tenant(con, 2)
    assert out["status"] == "REVOKED" and out.get("skipped") is True


def test_transport_error_is_transient_and_keeps_active(con):
    from tools.sync_binance_spot import sync_tenant
    from data.providers.binance_account import BinanceTransportError
    _add_cred(con)
    with patch("tools.sync_binance_spot.get_server_time_offset_ms", return_value=0), \
         patch("tools.sync_binance_spot.BinanceAccountClient") as Cli:
        Cli.return_value.get_spot_balances.side_effect = BinanceTransportError(
            "ConnectionError en GET /api/v3/account"
        )
        out = sync_tenant(con, 2)
    assert out["status"] == "TRANSPORT_ERROR"
    assert out.get("transient") is True
    assert _status(con) == "ACTIVE"  # transient blip does NOT downgrade the credential


def test_auth_error_sets_status_auth_failed(con):
    from tools.sync_binance_spot import sync_tenant
    from data.providers.binance_account import BinanceAuthError
    _add_cred(con)
    with patch("tools.sync_binance_spot.get_server_time_offset_ms", return_value=0), \
         patch("tools.sync_binance_spot.BinanceAccountClient") as Cli:
        Cli.return_value.get_spot_balances.side_effect = BinanceAuthError("-2015")
        out = sync_tenant(con, 2)
    assert out["status"] == "AUTH_FAILED"
    assert _status(con) == "AUTH_FAILED"  # persisted, fail-closed
