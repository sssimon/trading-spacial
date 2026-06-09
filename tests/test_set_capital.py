"""Tests del tool set_capital — fija el equity REAL de un tenant con reset de peak.

El reset del peak es la parte de seguridad: bajar el balance dejando el peak
viejo (nocional, más alto) haría que el sistema calcule un drawdown falso enorme
y dispare el kill-switch. set_capital pone balance=peak=equity, dd=0.
"""
from __future__ import annotations

import os
import sqlite3

import pytest


@pytest.fixture
def con(tmp_path):
    db_path = tmp_path / "cap.db"
    os.environ["MIGRATE_QTY_ALLOW_BULK_QUARANTINE"] = "1"
    try:
        import btc_api
        orig = btc_api.DB_FILE
        btc_api.DB_FILE = str(db_path)
        try:
            from db.schema import init_db
            init_db()
        finally:
            btc_api.DB_FILE = orig
    finally:
        os.environ.pop("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", None)
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def test_set_capital_resets_peak_and_dd(con):
    """Sobre un row nocional con peak alto, fijar equity resetea peak y DD."""
    from db.capital import db_upsert_capital
    from tools.set_capital import set_capital

    db_upsert_capital(con, 2, balance=10095.54, peak_balance=10138.31,
                      max_drawdown_pct=0.42)
    con.commit()

    row = set_capital(con, tenant_id=2, equity=2026.0)
    con.commit()

    assert row["balance"] == 2026.0
    assert row["peak_balance"] == 2026.0, (
        "peak debe resetear al nuevo equity; dejar el viejo daría DD falso ~80%"
    )
    assert row["max_drawdown_pct"] == 0.0


def test_set_capital_creates_if_absent(con):
    """Tenant sin row de capital: lo crea con balance=peak=equity, dd=0."""
    from tools.set_capital import set_capital

    row = set_capital(con, tenant_id=7, equity=500.0)
    con.commit()
    assert row["balance"] == 500.0
    assert row["peak_balance"] == 500.0
    assert row["max_drawdown_pct"] == 0.0
