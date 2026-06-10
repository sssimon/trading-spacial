# tests/test_binance_credentials_no_leak.py
import logging
import sqlite3
import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def con(monkeypatch):
    monkeypatch.setenv("TRADING_BINANCE_MASTER_KEY", Fernet.generate_key().decode())
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
    from db.schema import _migrate_binance_credentials
    _migrate_binance_credentials(c)
    return c


def test_metadata_endpoint_never_returns_secret(con):
    from db.binance_credentials import db_upsert_binance_credential
    from api.binance_credentials_api import credential_status_payload
    db_upsert_binance_credential(con, tenant_id=2, api_key_public="PUBKEY1234567890",
                                 secret_plaintext="SUPERSECRET")
    payload = credential_status_payload(con, tenant_id=2)
    blob = str(payload).lower()
    assert "supersecret" not in blob
    assert "secret_enc" not in payload
    assert payload["api_key_last4"] == "7890"


def test_secret_not_in_logs_on_upsert(con, caplog):
    from db.binance_credentials import db_upsert_binance_credential
    with caplog.at_level(logging.DEBUG):
        db_upsert_binance_credential(con, tenant_id=2, api_key_public="P", secret_plaintext="LEAKME")
    assert "LEAKME" not in caplog.text


def test_auth_error_message_excludes_secret():
    from data.providers.binance_account import BinanceAccountClient, BinanceAuthError
    from unittest.mock import patch

    class FakeResp:
        status_code = 401
        def json(self): return {"code": -2015, "msg": "Invalid API-key"}
        text = "{}"

    client = BinanceAccountClient(api_key="PUBKEY", secret="THE_SECRET", server_time_offset_ms=0)
    with patch("data.providers.binance_account._signed_get", return_value=FakeResp()):
        try:
            client.get_spot_balances()
            assert False
        except BinanceAuthError as e:
            assert "THE_SECRET" not in str(e)
