"""Invariant tests for db.schema._migrate_qty_not_null (amended Voronov policy).

Voronov path (C) for #467 post-Task-1 measurement:
- Backfill qty = size_usd / entry_price where computable.
- UNbackfillable rows → status='legacy_unmeasurable' (qty remains NULL).
- CHECK constraint: qty IS NOT NULL OR status='legacy_unmeasurable'.

The status admite the deuda explicitly instead of inventing values via
UPDATE qty=0. Voronov: 'convierte mentiras silenciosas en reconocimientos
explícitos'.
"""
import sqlite3
import pytest


def _init_minimal_positions_table(con: sqlite3.Connection) -> None:
    """Create the positions table matching the live signals.db schema
    (CREATE TABLE positions ...) WITHOUT the qty CHECK constraint."""
    con.execute("""
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
            tenant_id   INTEGER
        )
    """)


def test_backfill_qty_from_size_usd_and_entry_price(tmp_path):
    """Rows with qty=NULL but size_usd and entry_price set must be backfilled
    to qty = size_usd / entry_price. Status is unchanged."""
    from db.schema import _migrate_qty_not_null

    db_path = tmp_path / "test.db"
    con = sqlite3.connect(str(db_path))
    _init_minimal_positions_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, size_usd, qty) "
        "VALUES (1, 'BTCUSDT', 100.0, '2026-04-20T10:00:00+00:00', 1000.0, NULL)"
    )
    con.commit()

    _migrate_qty_not_null(con)

    row = con.execute("SELECT qty, status FROM positions WHERE id = 1").fetchone()
    assert row[0] == 10.0  # 1000 / 100
    assert row[1] == "open"  # status unchanged


def test_unbackfillable_closed_rows_get_legacy_unmeasurable_status(tmp_path):
    """Closed rows with qty=NULL and size_usd=NULL cannot be backfilled.
    They must get status='legacy_unmeasurable'; qty remains NULL.
    (Voronov: admite ausencia, no inventar valor.)"""
    from db.schema import _migrate_qty_not_null

    db_path = tmp_path / "test.db"
    con = sqlite3.connect(str(db_path))
    _init_minimal_positions_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, size_usd, qty, status) "
        "VALUES (1, 'BTCUSDT', 100.0, '2026-04-20T10:00:00+00:00', NULL, NULL, 'closed')"
    )
    con.commit()

    _migrate_qty_not_null(con)

    row = con.execute("SELECT qty, status FROM positions WHERE id = 1").fetchone()
    assert row[0] is None  # qty admitido como ausente
    assert row[1] == "legacy_unmeasurable"


def test_unbackfillable_open_rows_also_get_legacy_unmeasurable_status(tmp_path):
    """Open rows with qty=NULL and size_usd=NULL are treated the same as
    closed+NULL — quarantine status (Voronov: 'tratalas como las
    closed+NULL en esta migración')."""
    from db.schema import _migrate_qty_not_null

    db_path = tmp_path / "test.db"
    con = sqlite3.connect(str(db_path))
    _init_minimal_positions_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, size_usd, qty, status) "
        "VALUES (1, 'BTCUSDT', 100.0, '2026-04-20T10:00:00+00:00', NULL, NULL, 'open')"
    )
    con.commit()

    _migrate_qty_not_null(con)

    row = con.execute("SELECT qty, status FROM positions WHERE id = 1").fetchone()
    assert row[0] is None
    assert row[1] == "legacy_unmeasurable"


def test_check_constraint_rejects_null_qty_for_active_status(tmp_path):
    """Post-migration, INSERT with qty=NULL and status='open' raises
    IntegrityError. INSERT with qty=NULL and status='legacy_unmeasurable'
    succeeds (the schema exempts the quarantine status)."""
    from db.schema import _migrate_qty_not_null

    db_path = tmp_path / "test.db"
    con = sqlite3.connect(str(db_path))
    _init_minimal_positions_table(con)
    # Seed one valid row so migration runs cleanly.
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, size_usd, qty, status) "
        "VALUES (1, 'BTCUSDT', 100.0, '2026-04-20T10:00:00+00:00', 1000.0, 10.0, 'closed')"
    )
    con.commit()

    _migrate_qty_not_null(con)

    # Active status with NULL qty must be rejected.
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, status) "
            "VALUES (99, 'XYZ', 50.0, '2026-04-20T10:00:00+00:00', NULL, 'open')"
        )

    # legacy_unmeasurable status with NULL qty must be accepted (exempted).
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, status) "
        "VALUES (100, 'XYZ', 50.0, '2026-04-20T10:00:00+00:00', NULL, 'legacy_unmeasurable')"
    )
    con.commit()
    row = con.execute("SELECT qty, status FROM positions WHERE id = 100").fetchone()
    assert row[0] is None
    assert row[1] == "legacy_unmeasurable"


def test_migration_tolerates_stub_positions_table(tmp_path):
    """Migration must not crash when positions table predates size_usd/qty.

    Simulates a very old DB schema (pre-B.1 stub) — earlier _migrate_* helpers
    add tenant_id incrementally, but _migrate_qty_not_null must not reference
    size_usd/qty in raw SQL if those columns don't yet exist.
    """
    import sqlite3
    from db.schema import _migrate_qty_not_null

    db = tmp_path / "stub.db"
    con = sqlite3.connect(db)
    con.executescript('''
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT 'LONG',
            status TEXT NOT NULL DEFAULT 'open',
            entry_price REAL NOT NULL,
            entry_ts TEXT NOT NULL
        );
        INSERT INTO positions (symbol, direction, status, entry_price, entry_ts)
        VALUES ('BTCUSDT', 'LONG', 'open', 50000.0, '2024-01-01T00:00:00');
    ''')
    con.commit()

    # Should not raise.
    _migrate_qty_not_null(con)
    con.commit()

    # Row preserved.
    row = con.execute("SELECT symbol, status, qty FROM positions").fetchone()
    assert row[0] == 'BTCUSDT'
    # qty column now exists (recreated), value is NULL.
    assert row[2] is None
    # Status quarantined since qty is NULL.
    assert row[1] == 'legacy_unmeasurable'

    # CHECK constraint is in place.
    schema = con.execute(
        "SELECT sql FROM sqlite_master WHERE name='positions'"
    ).fetchone()[0]
    assert "legacy_unmeasurable" in schema
    con.close()


def test_idempotent_on_already_migrated_table(tmp_path):
    """Running _migrate_qty_not_null twice is a no-op the second time."""
    from db.schema import _migrate_qty_not_null

    db_path = tmp_path / "test.db"
    con = sqlite3.connect(str(db_path))
    _init_minimal_positions_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, size_usd, qty) "
        "VALUES (1, 'BTCUSDT', 100.0, '2026-04-20T10:00:00+00:00', 1000.0, 10.0)"
    )
    con.commit()

    _migrate_qty_not_null(con)
    _migrate_qty_not_null(con)  # second run must not raise

    row = con.execute("SELECT qty FROM positions WHERE id = 1").fetchone()
    assert row[0] == 10.0
