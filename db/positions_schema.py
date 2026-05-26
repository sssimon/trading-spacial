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

Phase 2 (deferred, separate WORK — not a PR, a project):
    The declaration in this file is **observational, not constructive**.
    It is a witness to the schema encoded in the vocabulary of the
    verifier — PRAGMA table_info shape, sqlite_master.sql substring
    matching, PRAGMA index_list flags. It does NOT carry the SQL the
    migrations need: `INTEGER PRIMARY KEY AUTOINCREMENT`, `REFERENCES
    scans(id)`, the literal default string with its quotes intact.

    Making the migrations REFERENCE this declaration (so the
    `CREATE TABLE positions_new (...)` block in each helper is generated
    rather than hand-written) therefore requires one of:

      (a) Code-gen path. Grow `ColumnSpec` until it can emit a DDL line:
          re-introduce `AUTOINCREMENT`, foreign-key clauses, literal
          defaults, NOT NULL keywords. A function
          `render_create_table_sql(columns, checks_active_at_step) -> str`
          emits the DDL. Migrations call it instead of copy-pasting.

      (b) Inversion path. The declaration becomes the source, and the
          migrations become *transformations on a schema object* rather
          than copy-pasted DDL. The migration's job becomes "advance the
          schema object from state N to state N+1," not "write a new
          CREATE TABLE that happens to differ by one CHECK."

    Neither (a) nor (b) is "another PR." Both are projects. The
    structural duplication is deeper than `TARGET_COLS` — the full
    `CREATE TABLE positions_new` block appears in each of the four
    migration helpers, not just the column list.

    Until Phase 2 lands, `CANONICAL_POSITIONS_COLUMNS` is a test fixture
    that names the schema, not a source of truth the schema is built
    from. The distinction matters and should not erode in language.
    (Per Voronov 2026-05-26 second meta-review.)

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
# ---------------------------------------------------------------------------
# On the quarantine-exemption asymmetry across the three CHECKs:
# ---------------------------------------------------------------------------
# The three CHECKs do NOT share an identical quarantine clause:
#   - the `tenant_id` CHECK exempts BOTH 'legacy_unmeasurable' AND 'legacy_no_tenant'
#   - the `qty` and `direction` CHECKs exempt ONLY 'legacy_unmeasurable'
#
# This is a PRINCIPLED DISTINCTION, not accidental drift. The "why" lives in
# `_migrate_tenant_id_not_null` (`db/schema.py`):
#
#   > "Production measurement (2026-05-26): 2018/2018 positions had
#   >  tenant_id IS NULL. Of those, 670 are already in legacy_unmeasurable
#   >  from C2 — the new CHECK exempts them via the OR (no double-quarantine).
#   >  The remaining ~1348 get re-statused to 'legacy_no_tenant'."
#
# `legacy_no_tenant` is a distinct ontological category from
# `legacy_unmeasurable`: it labels "this row has a known qty and a known
# direction, but predates multi-tenancy" — a structurally different defect
# from "this row has unmeasurable economics."
#
# Qty and direction do NOT need a parallel `legacy_no_qty` or
# `legacy_no_direction` bucket because their failure mode is already what
# `legacy_unmeasurable` means. There is no third population that has the
# economic facts but lacks the categorical fact, the way the ~1348
# pre-multi-tenancy rows did.
#
# So the asymmetry is correct. The declaration records it here because the
# declaration is where the three CHECK fragments stand next to each other —
# this is the only place a future reader can compare them at once.
# (Per Voronov 2026-05-26 second meta-review: "the declaration is where
# the asymmetry is visible, therefore the declaration is where the
# asymmetry needs explanation.")

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
