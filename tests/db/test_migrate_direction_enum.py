"""Invariant tests for db.schema._migrate_direction_enum (#484).

Moves the `direction` enum invariant from rung *convención* (only enforced
at the Pydantic boundary on OpenPositionRequest) to rung *schema* (CHECK
constraint in `positions`).

Predicate: `direction IN ('LONG', 'SHORT') OR status = 'legacy_unmeasurable'`.

The migration:
  1. Normalizes existing values via SQL UPPER() (e.g., 'long' → 'LONG').
  2. Quarantines anything that doesn't normalize to LONG/SHORT
     (NULL, empty string, 'NEUTRAL', garbage) as status='legacy_unmeasurable'.
  3. Recreates the positions table with the CHECK constraint.

Idempotent: detects the CHECK fragment in the live schema and skips.
"""
import sqlite3
import pytest


def _init_minimal_positions_table(con: sqlite3.Connection) -> None:
    """Create the positions table matching the post-#471 (tenant_id CHECK)
    state — qty and tenant_id CHECKs already in place, but NO direction CHECK."""
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
            tenant_id   INTEGER,
            CHECK ((qty IS NOT NULL AND qty > 0) OR status = 'legacy_unmeasurable'),
            CHECK (tenant_id IS NOT NULL OR status IN ('legacy_unmeasurable', 'legacy_no_tenant'))
        )
    """)


def test_migration_normalizes_lowercase_direction(tmp_path):
    """Existing rows with 'long' / 'short' (lowercase) get normalized to
    'LONG' / 'SHORT' (uppercase) — they conform to the new CHECK without
    being quarantined."""
    from db.schema import _migrate_direction_enum

    db_path = tmp_path / "test.db"
    con = sqlite3.connect(str(db_path))
    _init_minimal_positions_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, tenant_id, direction) "
        "VALUES (1, 'BTCUSDT', 100.0, '2026-04-20T10:00:00+00:00', 1.0, 1, 'long')"
    )
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, tenant_id, direction) "
        "VALUES (2, 'ETHUSDT', 200.0, '2026-04-20T10:00:00+00:00', 0.5, 1, 'Short')"
    )
    con.commit()

    _migrate_direction_enum(con)
    con.commit()

    rows = con.execute("SELECT id, direction, status FROM positions ORDER BY id").fetchall()
    # 'long' → 'LONG', 'Short' → 'SHORT'. No quarantine — values are recoverable.
    assert rows[0] == (1, "LONG", "open"), f"lowercase 'long' must normalize to 'LONG'; got: {rows[0]}"
    assert rows[1] == (2, "SHORT", "open"), f"'Short' must normalize to 'SHORT'; got: {rows[1]}"


def test_migration_quarantines_garbage_direction(tmp_path):
    """Rows with direction values that don't normalize to LONG/SHORT (typos,
    legacy values like 'NEUTRAL', empty string) get quarantined to
    status='legacy_unmeasurable' — the new CHECK exempts that status."""
    from db.schema import _migrate_direction_enum

    db_path = tmp_path / "test.db"
    con = sqlite3.connect(str(db_path))
    _init_minimal_positions_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, tenant_id, direction) "
        "VALUES (1, 'BTCUSDT', 100.0, '2026-04-20T10:00:00+00:00', 1.0, 1, 'NEUTRAL')"
    )
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, tenant_id, direction) "
        "VALUES (2, 'ETHUSDT', 200.0, '2026-04-20T10:00:00+00:00', 0.5, 1, '')"
    )
    con.commit()

    _migrate_direction_enum(con)
    con.commit()

    rows = con.execute("SELECT id, direction, status FROM positions ORDER BY id").fetchall()
    # Quarantined: status flipped; direction is preserved as historical record.
    assert rows[0][2] == "legacy_unmeasurable", f"'NEUTRAL' row must be quarantined; got: {rows[0]}"
    assert rows[1][2] == "legacy_unmeasurable", f"empty direction row must be quarantined; got: {rows[1]}"


def test_migration_preserves_already_valid_rows(tmp_path):
    """Rows that already conform ('LONG' / 'SHORT' uppercase) are unchanged
    by the migration — neither normalized nor quarantined."""
    from db.schema import _migrate_direction_enum

    db_path = tmp_path / "test.db"
    con = sqlite3.connect(str(db_path))
    _init_minimal_positions_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, tenant_id, direction) "
        "VALUES (1, 'BTCUSDT', 100.0, '2026-04-20T10:00:00+00:00', 1.0, 1, 'LONG')"
    )
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, tenant_id, direction) "
        "VALUES (2, 'ETHUSDT', 200.0, '2026-04-20T10:00:00+00:00', 0.5, 2, 'SHORT')"
    )
    con.commit()

    _migrate_direction_enum(con)
    con.commit()

    rows = con.execute("SELECT id, direction, status FROM positions ORDER BY id").fetchall()
    assert rows[0] == (1, "LONG", "open")
    assert rows[1] == (2, "SHORT", "open")


def test_check_constraint_rejects_invalid_direction_after_migration(tmp_path):
    """After the migration, attempting to INSERT a non-LONG/SHORT direction
    on a non-quarantine status row raises sqlite3.IntegrityError.

    This is the structural guarantee — the same predicate the Pydantic
    Literal enforces at the boundary now lives at the schema rung."""
    from db.schema import _migrate_direction_enum

    db_path = tmp_path / "test.db"
    con = sqlite3.connect(str(db_path))
    _init_minimal_positions_table(con)
    con.commit()

    _migrate_direction_enum(con)
    con.commit()

    # Try to INSERT a row with invalid direction (status='open' so quarantine
    # exemption doesn't apply).
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO positions (symbol, entry_price, entry_ts, qty, tenant_id, direction, status) "
            "VALUES ('BTCUSDT', 100.0, '2026-04-20T10:00:00+00:00', 1.0, 1, 'NEUTRAL', 'open')"
        )

    # And for completeness: a legacy_unmeasurable row CAN have any direction
    # (the OR in the CHECK exempts that status).
    con.execute(
        "INSERT INTO positions (symbol, entry_price, entry_ts, qty, tenant_id, direction, status) "
        "VALUES ('BTCUSDT', 100.0, '2026-04-20T10:00:00+00:00', 1.0, 1, 'NEUTRAL', 'legacy_unmeasurable')"
    )
    con.commit()


def test_migration_realistic_chain_position(tmp_path):
    """In production, _migrate_direction_enum runs AFTER _migrate_qty_not_null,
    _migrate_qty_positive, and _migrate_tenant_id_not_null. Each prior
    migration may have already quarantined some rows (qty=NULL or
    tenant_id=NULL → status='legacy_unmeasurable' / 'legacy_no_tenant').

    Test that the direction migration's table recreation copies those
    pre-quarantined rows through correctly — the OR exemption in the new
    direction CHECK keeps them valid."""
    from db.schema import _migrate_direction_enum

    db_path = tmp_path / "test.db"
    con = sqlite3.connect(str(db_path))
    _init_minimal_positions_table(con)
    # Realistic mix: one active row + one pre-quarantined from prior migrations.
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, tenant_id, direction, status) "
        "VALUES (1, 'BTCUSDT', 100.0, '2026-04-20T10:00:00+00:00', 1.0, 1, 'LONG', 'open')"
    )
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, tenant_id, direction, status) "
        "VALUES (2, 'ETHUSDT', 200.0, '2026-04-20T10:00:00+00:00', NULL, 1, 'long', 'legacy_unmeasurable')"
    )
    con.commit()

    _migrate_direction_enum(con)
    con.commit()

    # The active row stays open; direction is preserved (was already LONG).
    # The pre-quarantined row keeps its status; direction is normalized but
    # the quarantine exempts it from the new CHECK anyway.
    rows = con.execute("SELECT id, direction, status FROM positions ORDER BY id").fetchall()
    assert rows[0] == (1, "LONG", "open"), f"active row preserved; got: {rows[0]}"
    assert rows[1][2] == "legacy_unmeasurable", f"prior quarantine preserved; got: {rows[1]}"

    # Schema-level: CHECK is present.
    schema = con.execute(
        "SELECT sql FROM sqlite_master WHERE name='positions'"
    ).fetchone()[0]
    normalized = "".join(schema.split()).lower()
    assert "directionin('long','short')" in normalized, (
        f"CHECK must be present post-migration; got: {schema}"
    )


def test_idempotent_on_already_migrated_table(tmp_path):
    """Running _migrate_direction_enum twice is a no-op the second time."""
    from db.schema import _migrate_direction_enum

    db_path = tmp_path / "test.db"
    con = sqlite3.connect(str(db_path))
    _init_minimal_positions_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, tenant_id, direction) "
        "VALUES (1, 'BTCUSDT', 100.0, '2026-04-20T10:00:00+00:00', 1.0, 1, 'LONG')"
    )
    con.commit()

    _migrate_direction_enum(con)
    _migrate_direction_enum(con)  # second run must not raise

    row = con.execute("SELECT direction FROM positions WHERE id = 1").fetchone()
    assert row[0] == "LONG"
