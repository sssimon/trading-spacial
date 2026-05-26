"""Canonical declaration of the `positions` table schema (Phase 1 of #501).

Background — Voronov 2026-05-26 post-PR-#500 meta-review:

> "Each migration in your chain is locally beautiful. Together they are a
> structure that has not yet been declared. You are not building a
> database schema; you are building the proof of a schema by induction,
> and the inductive step is duplicated across four functions with no
> base case."

> "The fix is naming a thing the codebase has never named: the positions
> schema is what `_migrate_direction_enum` says it is, at the bottom of
> `init_db`, after four prior helpers have run. Write that source of
> truth. Then the chain becomes verifiable instead of just executable."

This file is the source of truth Voronov named. It declares the SHAPE
of the `positions` table (columns, CHECK constraints, indexes) in a form
that is **legible without running the migration chain**. The companion
test `tests/test_canonical_positions_schema.py` runs `init_db()` on a
fresh DB and asserts the live schema matches this declaration.

If the migration chain drifts (someone renames a column, adds a CHECK,
removes an index) without updating this file, the test fails — surfacing
the drift before it lands on `upstream/main`.

Phase 1 (this commit): declaration + test. The migrations still own the
schema by construction; this file is the verifier.

Phase 2 (deferred, separate work): refactor the four migration helpers
to REFERENCE this declaration instead of duplicating its column list.
The TARGET_COLS list appears four times today; collapsing it to one
reference is its own PR.

Closes #501 Phase 1.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnSpec:
    """A column in the `positions` table.

    Matches the granularity returned by `PRAGMA table_info(positions)`:
    name, type, NOT NULL flag, default value, primary-key flag.
    """
    name: str
    type: str          # "INTEGER", "TEXT", "REAL"
    nullable: bool = True
    default: str | None = None
    is_primary_key: bool = False


@dataclass(frozen=True)
class CheckSpec:
    """A CHECK constraint, identified by a whitespace-normalized + case-folded
    fragment of its expression.

    The fragment is what the comparison test searches for inside the live
    `sqlite_master.sql` for the table (after the same normalization). It
    must be specific enough to uniquely identify the CHECK but not so
    rigid that benign SQL-formatting differences cause false positives.
    """
    name: str               # human label for error messages
    normalized_fragment: str  # whitespace-stripped + lowercased


@dataclass(frozen=True)
class IndexSpec:
    """An index on the `positions` table.

    Matches the granularity returned by `PRAGMA index_list(positions)`
    and `PRAGMA index_info(<index_name>)`. The `partial_where_fragment`
    is the WHERE clause of a partial index, normalized for substring
    matching against the index's `sqlite_master.sql` entry.
    """
    name: str
    columns: tuple[str, ...]
    unique: bool = False
    partial_where_fragment: str | None = None


# --- The canonical declaration ---
#
# This is the SHAPE the `positions` table converges to after all four
# migration helpers in `init_db()` have run. The migrations construct it
# by accretion; this declaration names it once.

# SQLite quirk: `INTEGER PRIMARY KEY AUTOINCREMENT` is a rowid alias.
# `PRAGMA table_info` reports it with `notnull=0` even though the rowid is
# effectively non-null (auto-assigned on INSERT). We declare nullable=True
# to match what PRAGMA actually returns; the `is_primary_key=True` flag is
# what carries the NOT NULL guarantee at the rowid level.
CANONICAL_POSITIONS_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("id", "INTEGER", nullable=True, is_primary_key=True),
    ColumnSpec("scan_id", "INTEGER"),
    ColumnSpec("symbol", "TEXT", nullable=False),
    ColumnSpec("direction", "TEXT", nullable=False, default="'LONG'"),
    ColumnSpec("status", "TEXT", nullable=False, default="'open'"),
    ColumnSpec("entry_price", "REAL", nullable=False),
    ColumnSpec("entry_ts", "TEXT", nullable=False),
    ColumnSpec("sl_price", "REAL"),
    ColumnSpec("tp_price", "REAL"),
    ColumnSpec("size_usd", "REAL"),
    ColumnSpec("qty", "REAL"),
    ColumnSpec("exit_price", "REAL"),
    ColumnSpec("exit_ts", "TEXT"),
    ColumnSpec("exit_reason", "TEXT"),
    ColumnSpec("pnl_usd", "REAL"),
    ColumnSpec("pnl_pct", "REAL"),
    ColumnSpec("notes", "TEXT"),
    ColumnSpec("atr_entry", "REAL"),
    ColumnSpec("be_mult", "REAL"),
    ColumnSpec("tenant_id", "INTEGER"),
)


# The three composite CHECK constraints that the migration chain installs.
# Identified by their normalized expression fragments (whitespace stripped,
# case-folded). The test asserts each fragment appears in the live
# sqlite_master.sql, normalized the same way.
#
# Note on the quarantine drift Voronov flagged (PR #500 review): the three
# CHECKs do NOT share an identical quarantine clause -- the tenant CHECK
# exempts both 'legacy_unmeasurable' AND 'legacy_no_tenant', while qty and
# direction CHECKs exempt only 'legacy_unmeasurable'. This canonical
# declaration accepts that drift as the current state; resolving the
# drift (either unifying the exemption set or explicitly justifying the
# asymmetry) is its own work (out of scope for #501 Phase 1).

CANONICAL_POSITIONS_CHECKS: tuple[CheckSpec, ...] = (
    CheckSpec(
        name="qty_not_null_and_positive_or_quarantine",
        normalized_fragment="check((qtyisnotnullandqty>0)orstatus='legacy_unmeasurable')",
    ),
    CheckSpec(
        name="tenant_id_not_null_or_quarantine_set",
        normalized_fragment="check(tenant_idisnotnullorstatusin('legacy_unmeasurable','legacy_no_tenant'))",
    ),
    CheckSpec(
        name="direction_enum_or_quarantine",
        normalized_fragment="check(directionin('long','short')orstatus='legacy_unmeasurable')",
    ),
)


CANONICAL_POSITIONS_INDEXES: tuple[IndexSpec, ...] = (
    IndexSpec(
        name="idx_positions_tenant",
        columns=("tenant_id",),
        unique=False,
    ),
    IndexSpec(
        name="idx_positions_open_scan_unique",
        columns=("tenant_id", "scan_id"),
        unique=True,
        partial_where_fragment="status='open'andscan_idisnotnull",
    ),
)


def normalize_sql(sql: str) -> str:
    """Strip whitespace + case-fold for substring comparison against
    `sqlite_master.sql`. Same normalization the test applies."""
    return "".join(sql.split()).lower()
