"""Tests del eje `origin` en positions (SIGNAL / OPERATOR / AUTO_DERIVED).

Spec: docs/superpowers/specs/es/2026-06-10-binance-v02-autocreacion-observabilidad-spec.md (REV 3, §2).

`origin` es el eje de PROCEDENCIA: quién FABRICÓ la fila.
- SIGNAL       — nacida de un scan del sistema (scan_id NOT NULL).
- OPERATOR     — el operador la registró deliberadamente (EXTERNAL manual, scan_id NULL).
- AUTO_DERIVED — el sistema la reconstruyó del trade history de Binance (v0.2).

El read-model de conducta lee SOLO SIGNAL/OPERATOR; AUTO_DERIVED es
observabilidad, NUNCA conducta (BNC-12). Resuelve el hallazgo de Voronov:
`scan_id=NULL` era un eje de autoría con 2 valores cargando 3.
"""
from __future__ import annotations

import os
import sqlite3


def _fresh_db(tmp_path) -> sqlite3.Connection:
    """init_db() sobre una DB fresca (mismo patrón que test_control_domain)."""
    db_path = tmp_path / "origin.db"
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


def test_positions_has_origin_column(tmp_path):
    """Una DB fresca tiene `origin TEXT NOT NULL DEFAULT 'SIGNAL'`."""
    con = _fresh_db(tmp_path)
    try:
        cols = {r[1]: r for r in con.execute("PRAGMA table_info(positions)").fetchall()}
    finally:
        con.close()
    assert "origin" in cols, "falta la columna origin en positions"
    # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
    _, _, ctype, notnull, dflt, _ = cols["origin"]
    assert ctype == "TEXT", f"tipo esperado TEXT, vivo {ctype!r}"
    assert notnull == 1, "origin debe ser NOT NULL"
    assert dflt == "'SIGNAL'", f"default esperado 'SIGNAL', vivo {dflt!r}"


def test_insert_without_origin_defaults_signal(tmp_path):
    """Un INSERT que NO menciona origin (todos los paths existentes de señal)
    cae a SIGNAL — el comportamiento de las filas previas se preserva."""
    con = _fresh_db(tmp_path)
    try:
        con.execute(
            "INSERT INTO positions "
            "(scan_id, symbol, direction, status, entry_price, entry_ts, qty, tenant_id) "
            "VALUES (5, 'BTCUSDT', 'LONG', 'open', 1.0, '2026-01-01T00:00:00', 1.0, 1)"
        )
        con.commit()
        val = con.execute("SELECT origin FROM positions").fetchone()[0]
    finally:
        con.close()
    assert val == "SIGNAL"


def test_origin_survives_reinit_recreations(tmp_path):
    """Una fila AUTO_DERIVED sobrevive a un segundo init_db.

    Regresión (lección market/control_domain): las 4 migraciones recrean la tabla
    positions. Si `origin` no viaja DENTRO de las recreaciones (CREATE + TARGET_COLS
    + select-expr fallback 'SIGNAL'), un boot lo resetearía → una fila AUTO_DERIVED
    se re-etiquetaría SIGNAL → entraría al read-model de conducta (BNC-12 roto).
    """
    con = _fresh_db(tmp_path)
    # AUTO_DERIVED es EXTERNAL + market (trigger BNC-4 lo exige juntos).
    con.execute(
        "INSERT INTO positions (symbol,direction,status,entry_price,entry_ts,"
        "qty,tenant_id,control_domain,market,origin) VALUES "
        "('BNBUSDT','LONG','open',600,'2026-06-04T00:56:00+00:00',1.0,2,'EXTERNAL','SPOT','AUTO_DERIVED')"
    )
    con.commit()
    con.close()

    import btc_api
    from db.schema import init_db
    db_path = tmp_path / "origin.db"
    orig = btc_api.DB_FILE
    btc_api.DB_FILE = str(db_path)
    try:
        init_db()  # simula un deploy restart
    finally:
        btc_api.DB_FILE = orig

    con = sqlite3.connect(str(db_path))
    try:
        val = con.execute(
            "SELECT origin FROM positions WHERE symbol='BNBUSDT'"
        ).fetchone()[0]
    finally:
        con.close()
    assert val == "AUTO_DERIVED", (
        "init_db reseteó origin AUTO_DERIVED → SIGNAL: la fila fabricada por el "
        "sistema entraría al read-model de conducta (BNC-12 roto)"
    )


def _run_migrate_origin(con: sqlite3.Connection) -> None:
    from db.schema import _migrate_origin
    _migrate_origin(con)
    con.commit()


def test_backfill_external_manual_to_operator(tmp_path):
    """El backfill etiqueta una fila EXTERNAL manual (scan_id NULL) como OPERATOR."""
    con = _fresh_db(tmp_path)
    try:
        con.execute(
            "INSERT INTO positions (scan_id,symbol,direction,status,entry_price,"
            "entry_ts,qty,tenant_id,control_domain) VALUES "
            "(NULL,'BTCUSDT','LONG','open',64390,'2026-06-04T00:56:00+00:00',0.01967,2,'EXTERNAL')"
        )
        con.commit()
        _run_migrate_origin(con)
        val = con.execute("SELECT origin FROM positions WHERE symbol='BTCUSDT'").fetchone()[0]
    finally:
        con.close()
    assert val == "OPERATOR"


def test_backfill_internal_stays_signal(tmp_path):
    """Una fila INTERNAL (de señal, scan_id set) NO se toca: queda SIGNAL."""
    con = _fresh_db(tmp_path)
    try:
        con.execute(
            "INSERT INTO positions (scan_id,symbol,direction,status,entry_price,"
            "entry_ts,qty,tenant_id,control_domain) VALUES "
            "(7,'ETHUSDT','LONG','open',1700,'2026-06-04T00:56:00+00:00',0.4,2,'INTERNAL')"
        )
        con.commit()
        _run_migrate_origin(con)
        val = con.execute("SELECT origin FROM positions WHERE symbol='ETHUSDT'").fetchone()[0]
    finally:
        con.close()
    assert val == "SIGNAL"


def test_backfill_does_not_touch_auto_derived(tmp_path):
    """El backfill NUNCA re-etiqueta una fila AUTO_DERIVED (guard origin='SIGNAL').

    Crítico (Adrian HIGH-9): si el backfill se predicara solo por
    control_domain='EXTERNAL', re-etiquetaría AUTO_DERIVED → OPERATOR en cada
    init_db → la fila fabricada entraría a conducta (Kill (a) del spec).
    """
    con = _fresh_db(tmp_path)
    try:
        con.execute(
            "INSERT INTO positions (scan_id,symbol,direction,status,entry_price,"
            "entry_ts,qty,tenant_id,control_domain,market,origin) VALUES "
            "(NULL,'PEPEUSDT','LONG','open',0.00001,'2026-06-04T00:56:00+00:00',1e6,2,'EXTERNAL','SPOT','AUTO_DERIVED')"
        )
        con.commit()
        _run_migrate_origin(con)
        _run_migrate_origin(con)  # idempotencia: re-correr NO debe re-etiquetar
        val = con.execute("SELECT origin FROM positions WHERE symbol='PEPEUSDT'").fetchone()[0]
    finally:
        con.close()
    assert val == "AUTO_DERIVED", "el backfill re-etiquetó AUTO_DERIVED (BNC-12/Kill-a)"


def test_backfill_idempotent_second_init(tmp_path):
    """Correr init_db dos veces no re-etiqueta ni rompe (idempotencia total)."""
    con = _fresh_db(tmp_path)
    con.execute(
        "INSERT INTO positions (scan_id,symbol,direction,status,entry_price,"
        "entry_ts,qty,tenant_id,control_domain) VALUES "
        "(NULL,'BTCUSDT','LONG','open',64390,'2026-06-04T00:56:00+00:00',0.01967,2,'EXTERNAL')"
    )
    con.commit()
    con.close()

    import btc_api
    from db.schema import init_db
    db_path = tmp_path / "origin.db"
    orig = btc_api.DB_FILE
    btc_api.DB_FILE = str(db_path)
    try:
        init_db()  # backfill corre: EXTERNAL manual → OPERATOR
        init_db()  # segundo: NO debe re-tocar
    finally:
        btc_api.DB_FILE = orig

    con = sqlite3.connect(str(db_path))
    try:
        val = con.execute("SELECT origin FROM positions WHERE symbol='BTCUSDT'").fetchone()[0]
        n_origin_cols = len([r for r in con.execute("PRAGMA table_info(positions)").fetchall()
                             if r[1] == "origin"])
    finally:
        con.close()
    assert val == "OPERATOR", "el backfill no llevó la EXTERNAL manual a OPERATOR vía init_db"
    assert n_origin_cols == 1, "origin se duplicó en el segundo init_db"
