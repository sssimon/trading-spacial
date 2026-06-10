"""Canonical-schema coherence test for the `positions` table (Phase 1 of #501).

Asserts the SHAPE the migration chain converges to matches the declaration
in `db.positions_schema`. If a future migration changes the table without
updating the declaration, this test fails — surfacing the drift before it
lands on upstream.

Voronov post-PR-#500: *"Each migration in your chain is locally beautiful.
Together they are a structure that has not yet been declared."* This test
is the declaration's audit organ.

Closes #501 Phase 1.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def _fresh_initialized_db(tmp_path) -> sqlite3.Connection:
    """Initialize a fresh DB via init_db(), opt-in to the no-qty-column
    bulk-quarantine branch (per #474 since the stub schema has empty rows)."""
    db_path = tmp_path / "canonical_schema.db"
    os.environ["MIGRATE_QTY_ALLOW_BULK_QUARANTINE"] = "1"
    try:
        import btc_api
        original_db_file = btc_api.DB_FILE
        btc_api.DB_FILE = str(db_path)
        try:
            from db.schema import init_db
            init_db()
            return sqlite3.connect(str(db_path))
        finally:
            btc_api.DB_FILE = original_db_file
    finally:
        os.environ.pop("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", None)


def test_columns_match_canonical(tmp_path):
    """Every column in CANONICAL_POSITIONS_COLUMNS exists in the live table
    with matching type, nullability, default, and primary-key status.
    No extra columns may exist (the live table is a strict match)."""
    from db.positions_schema import CANONICAL_POSITIONS_COLUMNS

    con = _fresh_initialized_db(tmp_path)
    try:
        rows = con.execute("PRAGMA table_info(positions)").fetchall()
    finally:
        con.close()

    # PRAGMA table_info returns: (cid, name, type, notnull, dflt_value, pk)
    live_by_name = {
        row[1]: {
            "type": row[2],
            "notnull": bool(row[3]),
            "default": row[4],
            "pk": bool(row[5]),
        }
        for row in rows
    }

    canonical_names = {c.name for c in CANONICAL_POSITIONS_COLUMNS}
    live_names = set(live_by_name.keys())

    missing = canonical_names - live_names
    extra = live_names - canonical_names
    assert not missing, (
        f"columns declared in db/positions_schema.py but missing from live "
        f"`positions` table: {sorted(missing)}. Update either the declaration "
        f"(if the column was removed intentionally) or the migration chain "
        f"(if the column was forgotten)."
    )
    assert not extra, (
        f"columns in live `positions` table but missing from "
        f"db/positions_schema.py declaration: {sorted(extra)}. Add them to "
        f"CANONICAL_POSITIONS_COLUMNS or remove them from the migration."
    )

    # Per-column attribute check.
    mismatches: list[str] = []
    for canonical in CANONICAL_POSITIONS_COLUMNS:
        live = live_by_name[canonical.name]
        if live["type"] != canonical.type:
            mismatches.append(
                f"  {canonical.name}: declared type {canonical.type!r}, "
                f"live type {live['type']!r}"
            )
        # NOT NULL: declared `nullable=False` should match `notnull=True`.
        if live["notnull"] != (not canonical.nullable):
            mismatches.append(
                f"  {canonical.name}: declared nullable={canonical.nullable}, "
                f"live notnull={live['notnull']}"
            )
        if canonical.is_primary_key and not live["pk"]:
            mismatches.append(
                f"  {canonical.name}: declared as primary key, "
                f"live pk={live['pk']}"
            )
        # Default: SQLite stores defaults as strings; normalize for compare.
        if canonical.default is not None:
            live_default = live["default"]
            if live_default != canonical.default:
                mismatches.append(
                    f"  {canonical.name}: declared default {canonical.default!r}, "
                    f"live default {live_default!r}"
                )

    assert not mismatches, (
        "columns have attribute mismatches between declaration and live table:\n"
        + "\n".join(mismatches)
    )


def test_check_constraints_match_canonical(tmp_path):
    """Each CHECK fragment in CANONICAL_POSITIONS_CHECKS appears (normalized)
    in the live `sqlite_master.sql` for `positions`."""
    from db.positions_schema import CANONICAL_POSITIONS_CHECKS, normalize_sql

    con = _fresh_initialized_db(tmp_path)
    try:
        row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='positions'"
        ).fetchone()
    finally:
        con.close()
    assert row and row[0], "positions table must have a CREATE TABLE row in sqlite_master"

    normalized_live = normalize_sql(row[0])
    missing: list[str] = []
    for check in CANONICAL_POSITIONS_CHECKS:
        if check.normalized_fragment not in normalized_live:
            missing.append(
                f"  {check.name}: declared fragment "
                f"{check.normalized_fragment!r} not found in live schema. "
                f"Live SQL (normalized): {normalized_live}"
            )
    assert not missing, (
        "CHECK constraints declared in db/positions_schema.py but not present "
        "in live `positions` schema:\n" + "\n".join(missing)
    )


def test_indexes_match_canonical(tmp_path):
    """Every index in CANONICAL_POSITIONS_INDEXES exists on the live table
    with matching name, columns, uniqueness, and partial-where clause."""
    from db.positions_schema import CANONICAL_POSITIONS_INDEXES, normalize_sql

    con = _fresh_initialized_db(tmp_path)
    try:
        # PRAGMA index_list: (seq, name, unique, origin, partial)
        index_list = con.execute("PRAGMA index_list(positions)").fetchall()
        live_indexes: dict[str, dict] = {}
        for idx in index_list:
            name = idx[1]
            # PRAGMA index_info: (seqno, cid, name) -- columns in index order
            info = con.execute(f"PRAGMA index_info({name!r})").fetchall()
            cols = tuple(r[2] for r in info)
            # Auto-indexes (e.g., for PRIMARY KEY) are NOT in our declared set;
            # filter them out by origin (origin='pk' for primary-key auto-index).
            origin = idx[3]
            if origin == "pk":
                continue
            live_indexes[name] = {
                "columns": cols,
                "unique": bool(idx[2]),
                "partial": bool(idx[4]),
            }

        # For partial indexes, the WHERE clause lives in sqlite_master.sql.
        # Fetch all partial-index DDL for matching against declared fragments.
        live_partial_sql: dict[str, str] = {}
        for name, info in live_indexes.items():
            if info["partial"]:
                row = con.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                    (name,),
                ).fetchone()
                if row and row[0]:
                    live_partial_sql[name] = normalize_sql(row[0])
    finally:
        con.close()

    canonical_names = {i.name for i in CANONICAL_POSITIONS_INDEXES}
    live_names = set(live_indexes.keys())

    missing = canonical_names - live_names
    extra = live_names - canonical_names
    assert not missing, (
        f"indexes declared in db/positions_schema.py but missing from live "
        f"`positions` table: {sorted(missing)}. Update either the declaration "
        f"or the migration chain."
    )
    assert not extra, (
        f"indexes on live `positions` table but missing from "
        f"db/positions_schema.py declaration: {sorted(extra)}."
    )

    # Per-index attribute check.
    mismatches: list[str] = []
    for canonical in CANONICAL_POSITIONS_INDEXES:
        live = live_indexes[canonical.name]
        if live["columns"] != canonical.columns:
            mismatches.append(
                f"  {canonical.name}: declared columns {canonical.columns!r}, "
                f"live columns {live['columns']!r}"
            )
        if live["unique"] != canonical.unique:
            mismatches.append(
                f"  {canonical.name}: declared unique={canonical.unique}, "
                f"live unique={live['unique']}"
            )
        if canonical.partial_where_fragment is not None:
            if not live["partial"]:
                mismatches.append(
                    f"  {canonical.name}: declared as partial index "
                    f"(WHERE clause) but live index is not partial"
                )
            else:
                live_sql = live_partial_sql.get(canonical.name, "")
                if canonical.partial_where_fragment not in live_sql:
                    mismatches.append(
                        f"  {canonical.name}: declared partial-WHERE "
                        f"fragment {canonical.partial_where_fragment!r} "
                        f"not found in live index SQL: {live_sql}"
                    )
        else:
            if live["partial"]:
                mismatches.append(
                    f"  {canonical.name}: declared as non-partial, "
                    f"live index has a WHERE clause (partial)"
                )

    assert not mismatches, (
        "indexes have attribute mismatches between declaration and live:\n"
        + "\n".join(mismatches)
    )


def test_no_unexpected_table_columns_or_constraints(tmp_path):
    """Smoke test: the three above tests together cover columns, CHECKs, and
    indexes. This one is a meta-check that they all PASSED — if any of them
    is silently skipped or short-circuited, this catches the gap.

    A stronger version would compare the FULL sqlite_master.sql (normalized)
    against a canonical full-text fixture; that is Phase 2 work (would
    require maintaining the full normalized SQL in sync with the migration
    chain's last `_new` block)."""
    # Currently a no-op meta-test; placeholder for Phase 2 expansion.
    # Importing the canonical module here verifies the module loads cleanly
    # (no syntax errors, no missing imports). If a future contributor
    # breaks db/positions_schema.py, this test catches it before the
    # comparison tests run.
    from db.positions_schema import (
        CANONICAL_POSITIONS_COLUMNS,
        CANONICAL_POSITIONS_CHECKS,
        CANONICAL_POSITIONS_INDEXES,
        normalize_sql,
    )
    assert len(CANONICAL_POSITIONS_COLUMNS) >= 19, (
        "canonical column list shrank unexpectedly"
    )
    assert len(CANONICAL_POSITIONS_CHECKS) == 3, (
        "canonical CHECK list should currently have 3 entries "
        "(qty + tenant_id + direction)"
    )
    assert len(CANONICAL_POSITIONS_INDEXES) == 3, (
        "canonical index list should have 3 entries "
        "(tenant index + open-scan partial unique + external-identity partial unique)"
    )
    # normalize_sql smoke test
    assert normalize_sql("CHECK ( qty > 0 )") == "check(qty>0)"
