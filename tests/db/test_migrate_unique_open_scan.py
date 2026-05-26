"""Invariant tests for db.schema._migrate_unique_open_scan (#470).

Closes the idempotency race: two concurrent POST /positions with the same
scan_id must not both create open rows for the same tenant.

Partial unique index: WHERE status='open' AND scan_id IS NOT NULL — covers
the only case that matters (active duplicate). Closed rows are historical
record and can share scan_id; NULL scan_id (legacy or backfill) is
explicitly out of scope.
"""
import sqlite3
import pytest


def _init_post_tenant_table(con: sqlite3.Connection) -> None:
    """positions table in the state AFTER _migrate_tenant_id_not_null (Task 6)."""
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
            CHECK ((qty IS NOT NULL AND qty > 0) OR status = 'legacy_unmeasurable'),
            CHECK (tenant_id IS NOT NULL OR status IN ('legacy_unmeasurable', 'legacy_no_tenant'))
        )
        """
    )


def test_index_created(tmp_path):
    """The migration creates an index named idx_positions_open_scan_unique."""
    from db.schema import _migrate_unique_open_scan

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_tenant_table(con)
    con.commit()

    _migrate_unique_open_scan(con)

    idx = con.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name='idx_positions_open_scan_unique'"
    ).fetchone()
    assert idx is not None


def test_rejects_second_open_with_same_tenant_and_scan_id(tmp_path):
    from db.schema import _migrate_unique_open_scan

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_tenant_table(con)
    _migrate_unique_open_scan(con)

    con.execute(
        "INSERT INTO positions (symbol, entry_price, entry_ts, qty, status, "
        "tenant_id, scan_id) "
        "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'open', 1, 42)"
    )
    con.commit()

    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO positions (symbol, entry_price, entry_ts, qty, status, "
            "tenant_id, scan_id) "
            "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'open', 1, 42)"
        )


def test_allows_closed_sharing_scan_id(tmp_path):
    """The partial index excludes status!='open'; two closed rows may share
    scan_id (historical record)."""
    from db.schema import _migrate_unique_open_scan

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_tenant_table(con)
    _migrate_unique_open_scan(con)

    con.execute(
        "INSERT INTO positions (symbol, entry_price, entry_ts, qty, status, "
        "tenant_id, scan_id) "
        "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'closed', 1, 42)"
    )
    con.execute(
        "INSERT INTO positions (symbol, entry_price, entry_ts, qty, status, "
        "tenant_id, scan_id) "
        "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'closed', 1, 42)"
    )
    con.commit()
    count = con.execute(
        "SELECT COUNT(*) FROM positions WHERE scan_id=42 AND status='closed'"
    ).fetchone()[0]
    assert count == 2


def test_allows_different_tenants_sharing_scan_id_open(tmp_path):
    """Two open rows with same scan_id but different tenants are allowed —
    each tenant has their own per-scan slot."""
    from db.schema import _migrate_unique_open_scan

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_tenant_table(con)
    _migrate_unique_open_scan(con)

    con.execute(
        "INSERT INTO positions (symbol, entry_price, entry_ts, qty, status, "
        "tenant_id, scan_id) "
        "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'open', 1, 42)"
    )
    con.execute(
        "INSERT INTO positions (symbol, entry_price, entry_ts, qty, status, "
        "tenant_id, scan_id) "
        "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'open', 2, 42)"
    )
    con.commit()
    count = con.execute(
        "SELECT COUNT(*) FROM positions WHERE scan_id=42 AND status='open'"
    ).fetchone()[0]
    assert count == 2


def test_allows_multiple_open_with_null_scan_id_same_tenant(tmp_path):
    """scan_id IS NULL is explicitly excluded — the index does not constrain
    legacy/scanner-less rows."""
    from db.schema import _migrate_unique_open_scan

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_tenant_table(con)
    _migrate_unique_open_scan(con)

    con.execute(
        "INSERT INTO positions (symbol, entry_price, entry_ts, qty, status, "
        "tenant_id, scan_id) "
        "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'open', 1, NULL)"
    )
    con.execute(
        "INSERT INTO positions (symbol, entry_price, entry_ts, qty, status, "
        "tenant_id, scan_id) "
        "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'open', 1, NULL)"
    )
    con.commit()
    count = con.execute(
        "SELECT COUNT(*) FROM positions WHERE scan_id IS NULL AND status='open'"
    ).fetchone()[0]
    assert count == 2


def test_idempotent_on_re_run(tmp_path):
    from db.schema import _migrate_unique_open_scan

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_tenant_table(con)
    con.commit()

    _migrate_unique_open_scan(con)
    _migrate_unique_open_scan(con)  # must not raise

    idx_count = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='index' "
        "AND name='idx_positions_open_scan_unique'"
    ).fetchone()[0]
    assert idx_count == 1
