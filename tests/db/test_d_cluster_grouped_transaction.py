"""Tests for the D-cluster migration grouping (Serrano HIGH 7).

The four D-cluster sub-migrations (_migrate_qty_positive,
_migrate_tenant_id_not_null, _migrate_unique_open_scan,
_migrate_idempotency_keys) now run inside ONE outer `transaction()` in
`init_db`. Partial failure of any sub-step must roll back the whole
cluster — the database cannot land in an intermediate state where
qty>0 has been enforced but the partial UNIQUE index is missing.

Each sub-migration remains idempotent on its own. The wrapping tx
only changes the group-failure semantics.
"""
import sqlite3
import pytest

from db.transaction import transaction


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "d_cluster.db"
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(db_path))
    return db_path


def test_init_db_applies_cluster_atomically(tmp_db):
    """Sanity: after init_db, all four D-cluster invariants are in place
    on the live schema. (The schema-level effect of running the cluster
    inside one outer tx is the same as running them separately; this
    test pins the post-state.)"""
    from db.schema import init_db
    init_db()

    with transaction() as con:
        positions_sql = con.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='positions'"
        ).fetchone()[0]
        assert "qty > 0" in positions_sql, (
            "_migrate_qty_positive CHECK missing"
        )
        assert "legacy_no_tenant" in positions_sql, (
            "_migrate_tenant_id_not_null CHECK missing"
        )

        idx = con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND name='idx_positions_open_scan_unique'"
        ).fetchone()
        assert idx is not None, "_migrate_unique_open_scan index missing"

        ik = con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='idempotency_keys'"
        ).fetchone()
        assert ik is not None, "_migrate_idempotency_keys table missing"


def test_cluster_group_rollback_on_partial_failure(monkeypatch, tmp_db):
    """If any sub-migration inside the D-cluster raises, the WHOLE cluster
    rolls back. Simulate by monkeypatching the third sub-migration
    (_migrate_unique_open_scan) to raise, then asserting that after
    `init_db` raises, neither the third nor the fourth sub-migration's
    effects landed AND the second sub-migration (_migrate_tenant_id_not_null)
    also rolled back along with the cluster.

    The first sub-migration in the cluster is _migrate_qty_positive, but
    the C2 _migrate_qty_not_null (above the cluster) is the SECOND
    transaction in init_db and is independent — its CHECK
    'legacy_unmeasurable' is already committed before the D-cluster
    opens, and it stays. That is correct: the cluster boundary is
    {qty_positive, tenant_id_not_null, unique_open_scan, idempotency_keys}.
    """
    import db.schema as schema

    real_unique_open_scan = schema._migrate_unique_open_scan

    def _exploding(_con):
        raise RuntimeError(
            "simulated D-cluster sub-step failure (test fixture)"
        )

    monkeypatch.setattr(schema, "_migrate_unique_open_scan", _exploding)

    with pytest.raises(RuntimeError, match="simulated D-cluster"):
        schema.init_db()

    # The cluster rolled back. We can detect this by opening a fresh
    # connection and reading the positions schema: it must NOT carry
    # the `qty > 0` fragment (which only _migrate_qty_positive
    # installs) nor the `legacy_no_tenant` (only
    # _migrate_tenant_id_not_null). The C2 `legacy_unmeasurable`
    # fragment from _migrate_qty_not_null DOES live (it ran in its
    # own pre-cluster tx).
    #
    # Equally, idempotency_keys must NOT exist (the fourth sub-step
    # never ran, and even if it had, it would have rolled back too).
    with transaction() as con:
        positions_sql_row = con.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='positions'"
        ).fetchone()
        assert positions_sql_row is not None
        positions_sql = positions_sql_row[0]
        # Pre-cluster invariant survives.
        assert "legacy_unmeasurable" in positions_sql, (
            "C2 CHECK (legacy_unmeasurable) should survive the rollback "
            "of the D-cluster — it lives in its own pre-cluster tx."
        )
        # D-cluster invariants did NOT land.
        assert "qty > 0" not in positions_sql, (
            "qty>0 CHECK leaked through the rollback — cluster grouping "
            "did not roll back _migrate_qty_positive."
        )
        assert "legacy_no_tenant" not in positions_sql, (
            "legacy_no_tenant CHECK leaked — cluster grouping did not "
            "roll back _migrate_tenant_id_not_null."
        )

        # idempotency_keys table did not land.
        ik = con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='idempotency_keys'"
        ).fetchone()
        assert ik is None, (
            "idempotency_keys table leaked — cluster grouping did not "
            "roll back _migrate_idempotency_keys."
        )

    # And the cluster is re-applicable: restore the real sub-migration
    # and re-run init_db. The cluster lands cleanly the second time.
    monkeypatch.setattr(schema, "_migrate_unique_open_scan", real_unique_open_scan)
    schema.init_db()

    with transaction() as con:
        positions_sql = con.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='positions'"
        ).fetchone()[0]
        assert "qty > 0" in positions_sql
        assert "legacy_no_tenant" in positions_sql
        idx = con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND name='idx_positions_open_scan_unique'"
        ).fetchone()
        assert idx is not None
        ik = con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='idempotency_keys'"
        ).fetchone()
        assert ik is not None
