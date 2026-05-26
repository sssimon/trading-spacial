"""Invariant tests for db.schema._migrate_qty_positive (#471 closure of qty=0 bypass).

The C2 migration (_migrate_qty_not_null) closed qty IS NULL but left qty = 0
as a valid value (72 rows in prod). This migration extends the CHECK to
qty > 0 and quarantines the zero-qty rows as status='legacy_unmeasurable'.
"""
import sqlite3
import pytest


def _init_post_c2_positions_table(con: sqlite3.Connection) -> None:
    """Create the positions table in the post-C2 state: CHECK allows qty=0,
    quarantine status legacy_unmeasurable already exempted."""
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
            CHECK (qty IS NOT NULL OR status = 'legacy_unmeasurable')
        )
        """
    )


def test_quarantines_zero_qty_rows(tmp_path):
    """Rows with qty=0 must be re-statused to 'legacy_unmeasurable'."""
    from db.schema import _migrate_qty_positive

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_c2_positions_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, status) "
        "VALUES (1, 'BTCUSDT', 100.0, '2024-01-01T00:00:00', 0.0, 'closed')"
    )
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, status) "
        "VALUES (2, 'BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'open')"
    )
    con.commit()

    _migrate_qty_positive(con)

    rows = dict(con.execute("SELECT id, status FROM positions").fetchall())
    assert rows[1] == "legacy_unmeasurable"
    assert rows[2] == "open"


def test_post_migration_rejects_qty_zero_on_open_status(tmp_path):
    """After the migration, INSERT with qty=0 and status='open' must be rejected."""
    from db.schema import _migrate_qty_positive

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_c2_positions_table(con)
    con.commit()

    _migrate_qty_positive(con)

    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO positions (symbol, entry_price, entry_ts, qty, status) "
            "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', 0.0, 'open')"
        )


def test_post_migration_rejects_negative_qty(tmp_path):
    """qty < 0 is also rejected (the CHECK is qty > 0, not qty != 0)."""
    from db.schema import _migrate_qty_positive

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_c2_positions_table(con)
    con.commit()

    _migrate_qty_positive(con)

    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO positions (symbol, entry_price, entry_ts, qty, status) "
            "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', -1.0, 'open')"
        )


def test_post_migration_accepts_legacy_unmeasurable_with_null_or_zero(tmp_path):
    """legacy_unmeasurable rows can still carry qty=NULL or qty=0 (quarantine path)."""
    from db.schema import _migrate_qty_positive

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_c2_positions_table(con)
    con.commit()

    _migrate_qty_positive(con)

    con.execute(
        "INSERT INTO positions (symbol, entry_price, entry_ts, qty, status) "
        "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', NULL, 'legacy_unmeasurable')"
    )
    con.execute(
        "INSERT INTO positions (symbol, entry_price, entry_ts, qty, status) "
        "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', 0.0, 'legacy_unmeasurable')"
    )
    con.commit()
    count = con.execute(
        "SELECT COUNT(*) FROM positions WHERE status='legacy_unmeasurable'"
    ).fetchone()[0]
    assert count == 2


def test_idempotent_on_already_migrated(tmp_path):
    """Running the migration twice is a no-op the second time."""
    from db.schema import _migrate_qty_positive

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_c2_positions_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, status) "
        "VALUES (1, 'BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'open')"
    )
    con.commit()

    _migrate_qty_positive(con)
    _migrate_qty_positive(con)  # must not raise

    row = con.execute("SELECT qty, status FROM positions WHERE id=1").fetchone()
    assert row[0] == 10.0
    assert row[1] == "open"
