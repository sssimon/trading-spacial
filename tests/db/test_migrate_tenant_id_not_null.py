"""Invariant tests for db.schema._migrate_tenant_id_not_null (#471).

Production: 2018 rows with tenant_id=NULL. Quarantines them as
'legacy_no_tenant' (new status), then adds CHECK that exempts both
legacy_unmeasurable and legacy_no_tenant. Rows already in
legacy_unmeasurable from C2 keep that status (the OR exempts them).
"""
import sqlite3
import pytest


def _init_post_qty_positive_table(con: sqlite3.Connection) -> None:
    """Positions table in the state AFTER _migrate_qty_positive (Task 4)."""
    con.execute(
        """
        CREATE TABLE positions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id     INTEGER,
            symbol      TEXT    NOT NULL,
            direction   TEXT    NOT NULL DEFAULT 'LONG',
            status      TEXT    NOT NULL DEFAULT 'open',
            entry_price REAL    NOT NULL,
            entry_ts    TEXT    NOT NULL,
            sl_price    REAL,
            tp_price    REAL,
            size_usd    REAL,
            qty         REAL,
            exit_price  REAL,
            exit_ts     TEXT,
            exit_reason TEXT,
            pnl_usd     REAL,
            pnl_pct     REAL,
            notes       TEXT,
            atr_entry   REAL,
            be_mult     REAL,
            tenant_id   INTEGER,
            CHECK ((qty IS NOT NULL AND qty > 0) OR status = 'legacy_unmeasurable')
        )
        """
    )


def test_quarantines_null_tenant_rows_as_legacy_no_tenant(tmp_path):
    """tenant_id IS NULL rows not already in legacy_unmeasurable must be
    re-statused to 'legacy_no_tenant'."""
    from db.schema import _migrate_tenant_id_not_null

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_qty_positive_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, status, tenant_id) "
        "VALUES (1, 'BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'open', NULL)"
    )
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, status, tenant_id) "
        "VALUES (2, 'BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'closed', 1)"
    )
    con.commit()

    _migrate_tenant_id_not_null(con)

    rows = dict(con.execute("SELECT id, status FROM positions").fetchall())
    assert rows[1] == "legacy_no_tenant"
    assert rows[2] == "closed"


def test_already_legacy_unmeasurable_keeps_status(tmp_path):
    """Rows already in legacy_unmeasurable (from C2) keep that status —
    the OR in the new CHECK exempts them; no double-quarantine ceremony."""
    from db.schema import _migrate_tenant_id_not_null

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_qty_positive_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, status, tenant_id) "
        "VALUES (1, 'BTCUSDT', 100.0, '2024-01-01T00:00:00', NULL, 'legacy_unmeasurable', NULL)"
    )
    con.commit()

    _migrate_tenant_id_not_null(con)

    status = con.execute("SELECT status FROM positions WHERE id=1").fetchone()[0]
    assert status == "legacy_unmeasurable"


def test_post_migration_rejects_null_tenant_with_status_open(tmp_path):
    """Post-migration, INSERT with tenant_id=NULL and status='open' is rejected."""
    from db.schema import _migrate_tenant_id_not_null

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_qty_positive_table(con)
    con.commit()

    _migrate_tenant_id_not_null(con)

    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO positions (symbol, entry_price, entry_ts, qty, status, tenant_id) "
            "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'open', NULL)"
        )


def test_post_migration_accepts_legacy_no_tenant_with_null(tmp_path):
    """legacy_no_tenant + tenant_id=NULL is the quarantine path; INSERT must succeed."""
    from db.schema import _migrate_tenant_id_not_null

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_qty_positive_table(con)
    con.commit()

    _migrate_tenant_id_not_null(con)

    con.execute(
        "INSERT INTO positions (symbol, entry_price, entry_ts, qty, status, tenant_id) "
        "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'legacy_no_tenant', NULL)"
    )
    con.commit()
    count = con.execute(
        "SELECT COUNT(*) FROM positions WHERE status='legacy_no_tenant'"
    ).fetchone()[0]
    assert count == 1


def test_idempotent_on_already_migrated(tmp_path):
    from db.schema import _migrate_tenant_id_not_null

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_qty_positive_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, status, tenant_id) "
        "VALUES (1, 'BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'open', 1)"
    )
    con.commit()

    _migrate_tenant_id_not_null(con)
    _migrate_tenant_id_not_null(con)

    row = con.execute("SELECT tenant_id, status FROM positions WHERE id=1").fetchone()
    assert row[0] == 1
    assert row[1] == "open"
