"""Fail-closed contract of sync_tenant (Task 8 refactor: I/O fuera del writer-lock).

sync_tenant ahora maneja sus PROPIAS conexiones (vía btc_api.DB_FILE): FASE 1
lecturas (snapshot) + I/O de red, ambas SIN writer-lock; FASE 2 writes en una tx
corta. Halberg (revisión holística): no sostener BEGIN IMMEDIATE durante la red.

Los tests apuntan btc_api.DB_FILE a una DB de archivo con schema + credencial, y
mockean BinanceAccountClient. Debe: saltar credenciales no-ACTIVE, mapear errores
de cliente al estado de la credencial, y tratar un blip de transporte como
transitorio (TRANSPORT_ERROR, credencial SIGUE ACTIVE).
"""
import sqlite3
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_BINANCE_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", "1")
    p = tmp_path / "sync.db"
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(p), raising=False)
    from db.schema import init_db
    init_db()
    return str(p)


def _add_cred(db_path, status="ACTIVE"):
    from db.binance_credentials import db_upsert_binance_credential, db_set_credential_status
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        db_upsert_binance_credential(con, tenant_id=2, api_key_public="PUB", secret_plaintext="s")
        if status != "ACTIVE":
            db_set_credential_status(con, tenant_id=2, status=status)
        con.commit()
    finally:
        con.close()


def _status(db_path):
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT status FROM binance_credentials WHERE tenant_id=2").fetchone()[0]
    finally:
        con.close()


def test_no_credential(db_path):
    from tools.sync_binance_spot import sync_tenant
    assert sync_tenant(2)["status"] == "NO_CREDENTIAL"


def test_skips_non_active_credential(db_path):
    from tools.sync_binance_spot import sync_tenant
    _add_cred(db_path, status="REVOKED")
    out = sync_tenant(2)
    assert out["status"] == "REVOKED" and out.get("skipped") is True


def test_transport_error_is_transient_and_keeps_active(db_path):
    from tools.sync_binance_spot import sync_tenant
    from data.providers.binance_account import BinanceTransportError
    _add_cred(db_path)
    with patch("tools.sync_binance_spot.get_server_time_offset_ms", return_value=0), \
         patch("tools.sync_binance_spot.BinanceAccountClient") as Cli:
        Cli.return_value.get_spot_balances.side_effect = BinanceTransportError(
            "ConnectionError en GET /api/v3/account"
        )
        out = sync_tenant(2)
    assert out["status"] == "TRANSPORT_ERROR"
    assert out.get("transient") is True
    assert _status(db_path) == "ACTIVE"  # blip transitorio NO degrada la credencial


def test_auth_error_sets_status_auth_failed(db_path):
    from tools.sync_binance_spot import sync_tenant
    from data.providers.binance_account import BinanceAuthError
    _add_cred(db_path)
    with patch("tools.sync_binance_spot.get_server_time_offset_ms", return_value=0), \
         patch("tools.sync_binance_spot.BinanceAccountClient") as Cli:
        Cli.return_value.get_spot_balances.side_effect = BinanceAuthError("-2015")
        out = sync_tenant(2)
    assert out["status"] == "AUTH_FAILED"
    assert _status(db_path) == "AUTH_FAILED"  # persistido, fail-closed
