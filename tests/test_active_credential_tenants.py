"""Test del enumerador de tenants con credencial Binance ACTIVE (liveness). Spec §1."""
import sqlite3

from db.binance_credentials import db_list_active_credential_tenants


def _con():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE binance_credentials (tenant_id INTEGER PRIMARY KEY, status TEXT NOT NULL)")
    con.execute("INSERT INTO binance_credentials VALUES (2, 'ACTIVE')")
    con.execute("INSERT INTO binance_credentials VALUES (3, 'REVOKED')")
    con.execute("INSERT INTO binance_credentials VALUES (4, 'ACTIVE')")
    return con


def test_lista_solo_active():
    assert sorted(db_list_active_credential_tenants(_con())) == [2, 4]
