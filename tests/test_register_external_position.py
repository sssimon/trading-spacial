"""Tests del registrador one-shot de posiciones EXTERNAL.

Camino de nacimiento dedicado (NO el INSERT de señal): registra una posición
abierta POR FUERA del sistema como control_domain='EXTERNAL', scan_id=NULL,
size_usd=qty×entry. Idempotente. Spec REV 2 §3.
"""
from __future__ import annotations

import os
import sqlite3

import pytest


@pytest.fixture
def con(tmp_path):
    db_path = tmp_path / "reg.db"
    os.environ["MIGRATE_QTY_ALLOW_BULK_QUARANTINE"] = "1"
    try:
        import btc_api
        original = btc_api.DB_FILE
        btc_api.DB_FILE = str(db_path)
        try:
            from db.schema import init_db
            init_db()
        finally:
            btc_api.DB_FILE = original
    finally:
        os.environ.pop("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", None)
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def test_register_external_creates_external_row(con):
    from tools.register_external_position import register_external

    row = register_external(
        con, tenant_id=2, symbol="BTCUSDT", direction="LONG",
        qty=0.01967, entry_price=64390.0, entry_ts="2026-06-04T00:56:00+00:00",
    )
    con.commit()
    assert row is not None
    assert row["control_domain"] == "EXTERNAL"
    assert row["scan_id"] is None
    assert row["status"] == "open"
    assert row["sl_price"] is None and row["tp_price"] is None
    assert row["size_usd"] == pytest.approx(0.01967 * 64390.0)
    assert row["tenant_id"] == 2


def test_register_external_idempotent(con):
    from tools.register_external_position import register_external

    kw = dict(tenant_id=2, symbol="ETHUSDT", direction="LONG", qty=0.448,
              entry_price=1700.0, entry_ts="2026-06-02T11:11:00+00:00")
    first = register_external(con, **kw)
    con.commit()
    second = register_external(con, **kw)
    con.commit()
    assert first is not None, "primer registro crea la fila"
    assert second is None, "segundo registro es no-op (idempotente)"
    n = con.execute(
        "SELECT COUNT(*) FROM positions WHERE symbol='ETHUSDT' AND control_domain='EXTERNAL'"
    ).fetchone()[0]
    assert n == 1, "no debe duplicar la posición EXTERNAL"
