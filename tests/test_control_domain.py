"""Tests del eje `control_domain` en positions (INTERNAL vs EXTERNAL).

Spec: docs/superpowers/specs/es/2026-06-09-posiciones-externas-control-domain-spec.md (REV 2).

`control_domain` distingue posiciones nacidas DEL sistema (INTERNAL, el sistema
las gobierna) de las abiertas POR FUERA (EXTERNAL, el sistema observa pero no
actúa). v0.1 = fundación: la columna + la exención sistemática.
"""
from __future__ import annotations

import os
import sqlite3


def _fresh_db(tmp_path) -> sqlite3.Connection:
    """init_db() sobre una DB fresca (mismo patrón que test_canonical_positions_schema)."""
    db_path = tmp_path / "control_domain.db"
    os.environ["MIGRATE_QTY_ALLOW_BULK_QUARANTINE"] = "1"
    try:
        import btc_api
        original = btc_api.DB_FILE
        btc_api.DB_FILE = str(db_path)
        try:
            from db.schema import init_db
            init_db()
            return sqlite3.connect(str(db_path))
        finally:
            btc_api.DB_FILE = original
    finally:
        os.environ.pop("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", None)


def test_positions_has_control_domain_column(tmp_path):
    """Una DB fresca tiene `control_domain TEXT NOT NULL DEFAULT 'INTERNAL'`."""
    con = _fresh_db(tmp_path)
    try:
        cols = {r[1]: r for r in con.execute("PRAGMA table_info(positions)").fetchall()}
    finally:
        con.close()
    assert "control_domain" in cols, "falta la columna control_domain en positions"
    # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
    _, _, ctype, notnull, dflt, _ = cols["control_domain"]
    assert ctype == "TEXT", f"tipo esperado TEXT, vivo {ctype!r}"
    assert notnull == 1, "control_domain debe ser NOT NULL"
    assert dflt == "'INTERNAL'", f"default esperado 'INTERNAL', vivo {dflt!r}"


def test_insert_without_control_domain_defaults_internal(tmp_path):
    """Un INSERT que NO menciona control_domain (todos los paths existentes)
    cae a INTERNAL — el comportamiento de las filas previas se preserva."""
    con = _fresh_db(tmp_path)
    try:
        con.execute(
            "INSERT INTO positions "
            "(symbol, direction, status, entry_price, entry_ts, qty, tenant_id) "
            "VALUES ('BTCUSDT', 'LONG', 'open', 1.0, '2026-01-01T00:00:00', 1.0, 1)"
        )
        con.commit()
        val = con.execute("SELECT control_domain FROM positions").fetchone()[0]
    finally:
        con.close()
    assert val == "INTERNAL"


def test_migration_idempotent_second_init(tmp_path):
    """Correr init_db dos veces no rompe ni duplica la columna (idempotencia)."""
    con = _fresh_db(tmp_path)  # primer init_db ya corrió dentro del helper
    con.close()
    # Segundo init_db sobre la misma DB.
    import btc_api
    from db.schema import init_db
    db_path = tmp_path / "control_domain.db"
    original = btc_api.DB_FILE
    btc_api.DB_FILE = str(db_path)
    try:
        init_db()  # no debe lanzar
    finally:
        btc_api.DB_FILE = original
    con = sqlite3.connect(str(db_path))
    try:
        cd_cols = [r for r in con.execute("PRAGMA table_info(positions)").fetchall()
                   if r[1] == "control_domain"]
    finally:
        con.close()
    assert len(cd_cols) == 1, "control_domain no debe duplicarse en el segundo init_db"
