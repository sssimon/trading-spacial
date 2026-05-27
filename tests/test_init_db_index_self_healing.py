"""Lock-in regression test: init_db() is self-healing for indexes.

This test exists to anchor the empirical refutation of #518 (Halberg's
runtime-review side-finding on #497) into the test suite.

#518 hypothesized that idx_positions_tenant lives only inside four
short-circuiting migration helpers (db/schema.py:1037, :1154, :1267, :1403),
so on a steady-state DB where the index is dropped externally,
init_db() would not heal it. Empirical check showed this is false:
_migrate_multi_tenant_b1:502 contains a top-level
`CREATE INDEX IF NOT EXISTS idx_positions_tenant ON positions(tenant_id)`
and that helper has NO short-circuit — it runs every init_db() call.

The broader claim: every index created by init_db() is self-healing. If
any index is dropped between calls, the next init_db() recreates it.

If this test ever fails, it means:
  (a) A new index has been added inside a short-circuiting helper, with
      no top-level guarantor — file an issue and decide whether to add
      a top-level CREATE INDEX IF NOT EXISTS or accept the gap.
  (b) A top-level guarantor was moved behind a short-circuit — revert
      that move.
  (c) The full migration chain itself short-circuits in a way that bypasses
      every CREATE INDEX statement — much larger structural change to
      investigate.

Related:
  - #518 (closed wontfix — premise refuted by this test)
  - #497 (closed wontfix — the parent issue that spawned the #518 side-finding)
  - db/schema.py:502 (the load-bearing CREATE INDEX IF NOT EXISTS idx_positions_tenant)
"""
from __future__ import annotations

import os
import sqlite3

import pytest


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    """Fresh DB pointed at by btc_api.DB_FILE. The MIGRATE_QTY_ALLOW_BULK_QUARANTINE
    flag is unused on an empty fresh DB (no rows to quarantine), but set defensively
    in case future migration shapes need it on initial creation.
    """
    db_path = tmp_path / "index_healing.db"
    monkeypatch.setenv("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", "1")
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(db_path))
    return str(db_path)


def _list_user_indexes(db_path: str) -> list[str]:
    """Return all user-created (non-sqlite_*) indexes on the DB."""
    con = sqlite3.connect(db_path)
    try:
        return [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()]
    finally:
        con.close()


def test_init_db_recreates_idx_positions_tenant_after_drop(fresh_db):
    """Specific regression for #518 (Halberg side-finding). On a steady-state
    DB where idx_positions_tenant has been manually dropped, the next
    init_db() call must recreate it.

    The load-bearing helper is _migrate_multi_tenant_b1, which runs
    unconditionally on every init_db() call and contains:
        CREATE INDEX IF NOT EXISTS idx_positions_tenant ON positions(tenant_id)
    at db/schema.py:502.
    """
    from db.schema import init_db

    init_db()
    assert "idx_positions_tenant" in _list_user_indexes(fresh_db)

    # Drop the index externally — simulates manual `DROP INDEX`, restore
    # from partial backup, schema repair gone wrong, etc.
    con = sqlite3.connect(fresh_db, isolation_level=None)
    con.execute("DROP INDEX idx_positions_tenant")
    con.close()
    assert "idx_positions_tenant" not in _list_user_indexes(fresh_db)

    # Re-run init_db. The index must reappear.
    init_db()
    assert "idx_positions_tenant" in _list_user_indexes(fresh_db), (
        "init_db() did not heal a dropped idx_positions_tenant. "
        "Check that db/schema.py:502 (CREATE INDEX IF NOT EXISTS idx_positions_tenant "
        "inside _migrate_multi_tenant_b1) has not moved behind a short-circuit. "
        "If it has, file the regression as a real instance of #518."
    )


def test_init_db_recreates_every_user_index_after_drop(fresh_db):
    """Broader property: init_db() is self-healing for ALL its indexes.

    Drops every user-created index on the DB after a successful init_db(),
    re-runs init_db(), asserts every index is back. If a future helper
    introduces an index that lives only inside a short-circuit path, this
    test will catch it.
    """
    from db.schema import init_db

    init_db()
    initial_indexes = _list_user_indexes(fresh_db)
    assert initial_indexes, "fresh init_db() produced no user indexes — schema broken?"

    # Drop every user index.
    con = sqlite3.connect(fresh_db, isolation_level=None)
    try:
        for ix in initial_indexes:
            con.execute(f"DROP INDEX {ix}")
    finally:
        con.close()
    assert _list_user_indexes(fresh_db) == [], (
        "DROP INDEX did not clear all user indexes"
    )

    # Re-run init_db. Every index must reappear.
    init_db()
    after_indexes = _list_user_indexes(fresh_db)

    missing = sorted(set(initial_indexes) - set(after_indexes))
    assert not missing, (
        f"init_db() did not heal {len(missing)} dropped index(es): {missing}. "
        f"Initial set: {initial_indexes}. After re-init: {after_indexes}. "
        f"A new helper has introduced an index without a top-level guarantor; "
        f"investigate as a #518-class structural defect."
    )
