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


def test_migration_tolerates_stub_positions_table(tmp_path, monkeypatch):
    """Migration must not crash when positions table predates size_usd/qty.

    Simulates a very old DB schema (pre-B.1 stub) — earlier _migrate_* helpers
    add tenant_id incrementally, but _migrate_qty_not_null must not reference
    size_usd/qty in raw SQL if those columns don't yet exist.

    The no-qty-column branch bulk-quarantines all non-quarantined rows. This
    is dangerous in production (#474 — could silently disable active trading
    positions on a restored stale backup), so the branch refuses unless an
    operator explicit opt-in env flag is set. This test sets the flag to
    exercise the legitimate stub-schema path.
    """
    import sqlite3
    from db.schema import _migrate_qty_not_null

    monkeypatch.setenv("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", "1")

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


def test_no_qty_column_branch_refuses_active_rows_without_opt_in(tmp_path, monkeypatch):
    """The no-qty-column branch silently quarantines every non-already-quarantined
    row. In production, this could fire on a restored stale backup and disable
    every active trading position — operator sees 'position not found' in UI;
    kill-switch and notional code paths skip the rows with a log.warning. The
    integrity event is invisible.

    Per Serrano F3 [HIGH, SEC/GAP] (issue #474), this branch must refuse to
    run when there are non-legacy_unmeasurable rows present, unless the
    operator explicitly opts in via env flag.

    Closes #474."""
    import sqlite3
    from db.schema import _migrate_qty_not_null

    # Ensure the opt-in flag is NOT set (the default safe behavior).
    monkeypatch.delenv("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", raising=False)

    db = tmp_path / "restored_stale.db"
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
        INSERT INTO positions (symbol, direction, status, entry_price, entry_ts)
        VALUES ('ETHUSDT', 'LONG', 'closed', 3000.0, '2024-01-02T00:00:00');
    ''')
    con.commit()

    # Without the opt-in flag, the migration must refuse with a clear error
    # naming the count + status distribution + the override flag.
    with pytest.raises(RuntimeError, match=r"MIGRATE_QTY_ALLOW_BULK_QUARANTINE"):
        _migrate_qty_not_null(con)

    # And the table must NOT have been touched (the dangerous UPDATE didn't run).
    rows = con.execute("SELECT symbol, status FROM positions ORDER BY id").fetchall()
    assert rows == [('BTCUSDT', 'open'), ('ETHUSDT', 'closed')]
    con.close()


def test_idempotency_probe_does_not_false_positive_on_misleading_ddl(tmp_path):
    """The probe `"legacy_unmeasurable" in schema_row[0]` false-positives on
    ANY mention of the string in the DDL — column defaults, comments,
    unrelated CHECK constraints. When the probe false-positives, the
    migration silently skips with no warning and the CHECK constraint never
    lands.

    Per Serrano F4 [HIGH, OPS/AMB] (issue #476), the probe must anchor on
    the exact CHECK constraint DDL fragment, not a substring of the string.

    Closes #476."""
    import sqlite3
    from db.schema import _migrate_qty_not_null

    db = tmp_path / "misleading.db"
    con = sqlite3.connect(db)
    # Construct a positions table whose DDL contains the string
    # "legacy_unmeasurable" (here in a column DEFAULT) but does NOT have
    # the actual CHECK constraint. The bare-string probe would skip; the
    # anchored probe must run the migration.
    con.executescript("""
        CREATE TABLE positions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT    NOT NULL,
            direction   TEXT    NOT NULL DEFAULT 'LONG',
            status      TEXT    NOT NULL DEFAULT 'open',
            entry_price REAL    NOT NULL,
            entry_ts    TEXT    NOT NULL,
            size_usd    REAL,
            qty         REAL,
            notes       TEXT    DEFAULT 'pre-legacy_unmeasurable carve-out era'
        );
        INSERT INTO positions (symbol, entry_price, entry_ts, size_usd, qty)
        VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', 1000.0, NULL);
    """)
    con.commit()

    # Pre-condition: DDL contains the string but no real CHECK.
    pre_ddl = con.execute(
        "SELECT sql FROM sqlite_master WHERE name='positions'"
    ).fetchone()[0]
    assert "legacy_unmeasurable" in pre_ddl, "test setup: string must be present"
    assert "CHECK" not in pre_ddl.upper(), "test setup: no CHECK must be present"

    # Run the migration. The fix makes the probe anchor on the actual CHECK
    # fragment, so the migration recognizes the table as NOT yet migrated.
    _migrate_qty_not_null(con)
    con.commit()

    # Post-condition: CHECK constraint is now in place, qty was backfilled.
    post_ddl = con.execute(
        "SELECT sql FROM sqlite_master WHERE name='positions'"
    ).fetchone()[0]
    assert "CHECK" in post_ddl.upper(), (
        f"migration must have run and added CHECK; got DDL: {post_ddl!r}"
    )
    row = con.execute("SELECT qty FROM positions WHERE id = 1").fetchone()
    assert row[0] == 10.0, "qty must have been backfilled: 1000.0 / 100.0 = 10.0"
    con.close()


def test_migration_recovers_from_orphan_positions_new(tmp_path):
    """If a prior migration run was interrupted between DROP TABLE positions
    and ALTER TABLE positions_new RENAME (process killed, OOM, crash), the
    DB is left with an orphan `positions_new` table alongside `positions`.
    The next `init_db()` must recover: drop the orphan before recreation.

    Without recovery, CREATE TABLE positions_new aborts with 'table
    positions_new already exists' and the migration is stuck — the
    idempotency probe checks for the CHECK on `positions`, not for
    `positions_new` existence.

    Per Serrano F12 [MEDIUM, OPS] (issue #480).

    Closes #480."""
    import sqlite3
    from db.schema import _migrate_qty_not_null

    db = tmp_path / "interrupted.db"
    con = sqlite3.connect(db)
    _init_minimal_positions_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, size_usd, qty) "
        "VALUES (1, 'BTCUSDT', 100.0, '2024-01-01T00:00:00', 1000.0, 10.0)"
    )
    # Simulate the orphan from a prior interrupted run: positions_new exists
    # alongside positions, with whatever transient state it had.
    con.execute("CREATE TABLE positions_new (id INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO positions_new (id) VALUES (999)")
    con.commit()

    # Pre-condition: both tables exist before migration.
    tables_pre = {
        row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'positions%'"
        ).fetchall()
    }
    assert tables_pre == {"positions", "positions_new"}, (
        f"test setup: both tables must be present; got: {tables_pre}"
    )

    # Migration must recover: drop the orphan, then proceed normally.
    _migrate_qty_not_null(con)
    con.commit()

    # Post-condition: only positions remains (the orphan was dropped + recreated +
    # renamed back to positions), and our original row is preserved.
    tables_post = {
        row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'positions%'"
        ).fetchall()
    }
    assert tables_post == {"positions"}, (
        f"migration must end with only `positions`; got: {tables_post}"
    )
    row = con.execute("SELECT id, qty FROM positions").fetchone()
    assert row == (1, 10.0), "the original row must survive the migration"
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
