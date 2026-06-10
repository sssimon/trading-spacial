# tests/test_binance_credentials.py
import sqlite3
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


def test_upsert_stores_secret_encrypted_not_plaintext(con):
    from db.binance_credentials import db_upsert_binance_credential, db_get_binance_credential_raw
    db_upsert_binance_credential(
        con, tenant_id=2, api_key_public="PUBKEY123",
        secret_plaintext="SUPERSECRET", ip_whitelisted=True, scope_detected="READ_ONLY_SPOT",
    )
    raw = con.execute("SELECT secret_enc FROM binance_credentials WHERE tenant_id=2").fetchone()
    assert b"SUPERSECRET" not in raw["secret_enc"]  # never plaintext at rest


def test_get_decrypts_secret(con):
    from db.binance_credentials import db_upsert_binance_credential, db_get_decrypted_secret
    db_upsert_binance_credential(
        con, tenant_id=2, api_key_public="PUBKEY123", secret_plaintext="SUPERSECRET",
    )
    assert db_get_decrypted_secret(con, tenant_id=2) == "SUPERSECRET"


def test_upsert_is_idempotent_one_row_per_tenant(con):
    from db.binance_credentials import db_upsert_binance_credential
    db_upsert_binance_credential(con, tenant_id=2, api_key_public="A", secret_plaintext="s1")
    db_upsert_binance_credential(con, tenant_id=2, api_key_public="B", secret_plaintext="s2")
    n = con.execute("SELECT COUNT(*) FROM binance_credentials WHERE tenant_id=2").fetchone()[0]
    assert n == 1
    assert con.execute("SELECT api_key_public FROM binance_credentials WHERE tenant_id=2").fetchone()[0] == "B"


def test_metadata_view_never_exposes_secret(con):
    from db.binance_credentials import db_upsert_binance_credential, get_credential_metadata
    db_upsert_binance_credential(con, tenant_id=2, api_key_public="PUBKEY123456", secret_plaintext="SUPERSECRET")
    meta = get_credential_metadata(con, tenant_id=2)
    assert "secret" not in str(meta).lower()
    assert meta["api_key_last4"] == "3456"
    assert "secret_enc" not in meta


def test_set_status_rejects_invalid_status(con):
    # Guard must raise (not assert — `python -O` would strip an assert and
    # silently let a bad fail-closed state through). Review finding, Task 2.
    from db.binance_credentials import db_upsert_binance_credential, db_set_credential_status
    db_upsert_binance_credential(con, tenant_id=2, api_key_public="P", secret_plaintext="s")
    with pytest.raises(ValueError):
        db_set_credential_status(con, tenant_id=2, status="BOGUS")
    db_set_credential_status(con, tenant_id=2, status="REVOKED")  # valid → no raise
    assert con.execute(
        "SELECT status FROM binance_credentials WHERE tenant_id=2"
    ).fetchone()[0] == "REVOKED"
