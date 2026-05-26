"""Cross-rung consistency tests for dual-rung defense-in-depth enums.

Background — Voronov 2026-05-26 post-PR-#500 review:

> "Predictive law: Any defense-in-depth that lists enumerated values at
> multiple rungs without a cross-rung consistency test will drift on its
> first enumeration change. The test is the bridge between rungs. Without
> it, 'defense in depth' becomes 'two places to forget.'"

For every enum that is enforced at BOTH a Pydantic boundary AND a schema
CHECK constraint, this file asserts the two declarations agree on the
membership set. The intent: a change to either rung (extend the Pydantic
Literal, modify the CHECK clause, add a new enum value to the codebase) is
detected by a test failure before the rungs silently drift.

Adding a new dual-rung enum: add a new test function below; mirror the
shape of `test_direction_pydantic_matches_schema_check`.

Sub-task D of #488 (meta-arch).
"""
from __future__ import annotations

import re
import sqlite3
from typing import get_args

import pytest


def _extract_in_set_from_check(schema_sql: str, column: str) -> set[str]:
    """Parse the CHECK clause for `<column> IN ('A', 'B', ...)` and return
    the membership set.

    Returns the empty set if no such CHECK clause is found.

    Whitespace-tolerant. Single-quote-tolerant. Does NOT attempt to parse
    nested clauses (the codebase's current CHECKs are flat).
    """
    # Match: <column> IN ( ... )   where ... is comma-separated quoted strings
    # Allows whitespace around tokens.
    pattern = rf"{re.escape(column)}\s+IN\s*\(([^)]+)\)"
    match = re.search(pattern, schema_sql, re.IGNORECASE)
    if not match:
        return set()
    values_str = match.group(1)
    # Each value is a single-quoted string; split on comma, strip quotes + space.
    return {v.strip().strip("'\"") for v in values_str.split(",")}


def _live_positions_schema_sql(tmp_path) -> str:
    """Initialize a fresh DB with all migrations applied; return the
    sqlite_master.sql for the positions table."""
    import btc_api
    from db.schema import init_db

    db_path = tmp_path / "consistency_check.db"
    # Operator opt-in for the no-qty-column branch — required because the
    # fresh test DB triggers the bulk-quarantine guard from #474. Setting
    # via os.environ rather than monkeypatch because this is a helper,
    # not a test function with the monkeypatch fixture.
    import os
    os.environ["MIGRATE_QTY_ALLOW_BULK_QUARANTINE"] = "1"

    original_db_file = btc_api.DB_FILE
    btc_api.DB_FILE = str(db_path)
    try:
        init_db()
        con = sqlite3.connect(str(db_path))
        try:
            row = con.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='positions'"
            ).fetchone()
        finally:
            con.close()
    finally:
        btc_api.DB_FILE = original_db_file
        os.environ.pop("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", None)

    assert row is not None and row[0], "positions table must exist after init_db()"
    return row[0]


# ─── direction (Pydantic Literal + schema CHECK) ───────────────────────────

def test_direction_pydantic_matches_schema_check(tmp_path):
    """The `direction` enum is enforced at two rungs:

      1. Pydantic Literal['LONG', 'SHORT'] on `OpenPositionRequest.direction`
         (api/positions_birth.py) — rejects malformed HTTP input at boundary.
      2. Schema CHECK (direction IN ('LONG', 'SHORT') OR status = ...) on
         the positions table — rejects manual UPDATEs, legacy clients, shell.

    Both rungs are necessary (Pydantic for UX early rejection; schema for
    non-Pydantic paths). This test asserts the two declarations agree on
    the membership set, so a change to one without the other fails CI.

    Closes #488 sub-task D for the direction enum (the first dual-rung enum
    in the repo; the pattern is extensible to future cases)."""
    from api.positions_birth import OpenPositionRequest

    # Pydantic side: extract Literal args.
    pydantic_annotation = OpenPositionRequest.model_fields["direction"].annotation
    pydantic_values = set(get_args(pydantic_annotation))
    assert pydantic_values == {"LONG", "SHORT"}, (
        f"test setup: expected Pydantic to declare {{LONG, SHORT}}; "
        f"got {pydantic_values}. If you changed OpenPositionRequest.direction, "
        f"also update the schema CHECK in _migrate_direction_enum."
    )

    # Schema side: extract CHECK IN-clause from live sqlite_master.sql.
    schema_sql = _live_positions_schema_sql(tmp_path)
    schema_values = _extract_in_set_from_check(schema_sql, "direction")
    assert schema_values, (
        f"could not extract `direction IN (...)` from schema; got: {schema_sql!r}"
    )

    # The cross-rung consistency assertion.
    assert pydantic_values == schema_values, (
        f"direction enum has drifted between rungs.\n"
        f"  Pydantic Literal (api/positions_birth.py): {sorted(pydantic_values)}\n"
        f"  Schema CHECK (positions table):           {sorted(schema_values)}\n"
        f"\n"
        f"If you extended the enum, both rungs must be updated together:\n"
        f"  - api/positions_birth.py: update `Literal[...]` on direction field\n"
        f"  - db/schema.py: update CHECK clause in _migrate_direction_enum's CREATE TABLE\n"
        f"\n"
        f"This test is the bridge between rungs — without it, defense-in-depth\n"
        f"silently degrades to 'two places to forget'. See #488 sub-task D."
    )


# ─── helper validation ──────────────────────────────────────────────────────

def test_extract_in_set_handles_whitespace_variants():
    """The CHECK-extractor helper must tolerate whitespace and quote variants
    that SQLite can emit when re-storing CREATE TABLE in sqlite_master."""
    cases = [
        # Canonical form
        ("CHECK (direction IN ('LONG', 'SHORT'))", {"LONG", "SHORT"}),
        # Compact whitespace
        ("CHECK(direction IN('LONG','SHORT'))", {"LONG", "SHORT"}),
        # Tabs and newlines
        ("CHECK (direction\tIN\n('LONG',\n'SHORT'))", {"LONG", "SHORT"}),
        # Mixed case keyword
        ("CHECK (direction in ('LONG', 'SHORT'))", {"LONG", "SHORT"}),
    ]
    for sql, expected in cases:
        actual = _extract_in_set_from_check(sql, "direction")
        assert actual == expected, (
            f"extractor failed on: {sql!r}\n"
            f"  expected: {expected}\n"
            f"  got:      {actual}"
        )


def test_extract_in_set_returns_empty_on_no_match():
    """If the column has no IN clause in the schema (e.g., a future column
    that's enforced only by Pydantic), the extractor returns the empty set
    so the caller can detect the missing schema-rung and fail explicitly."""
    sql = "CREATE TABLE foo (symbol TEXT NOT NULL, qty REAL)"
    assert _extract_in_set_from_check(sql, "symbol") == set()
    assert _extract_in_set_from_check(sql, "direction") == set()
