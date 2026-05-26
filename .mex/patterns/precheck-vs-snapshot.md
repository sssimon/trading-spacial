---
name: precheck-vs-snapshot
description: Decision runbook for picking between precheck_connection() and snapshot_connection() — both set PRAGMA query_only=1, but their call-site contracts differ.
triggers:
  - "precheck_connection"
  - "snapshot_connection"
  - "PRAGMA query_only"
  - "PositionSnapshot"
  - "read-only"
last_updated: 2026-05-26
---

# Pattern: precheck_connection vs snapshot_connection

## Purpose

Both helpers return a `sqlite3.Connection` with `PRAGMA query_only = 1` set. They share implementation. They differ only in the **call-site contract** — and mixing them is a documentation error that future contributors should reject in code review.

See [[../context/conventions.md]] §4a/§4b/Threat-model for the full statement of contract.

## Decision tree

```
Is the read's result going to drive a subsequent mutation in the same logical operation?
├── YES → precheck_connection()
│         The block exits carrying an IMMUTABLE snapshot value (e.g., PositionSnapshot).
│         The connection MUST NOT escape.
│         The follow-up write-tx re-validates the snapshot's mutable fields inside BEGIN IMMEDIATE.
│
└── NO  → snapshot_connection()
          The result is serialised to an output (JSON file, HTTP response, log).
          No follow-up write, no re-validation obligation.
```

## `precheck_connection()` template

```python
from db.transaction import precheck_connection, transaction
from operators.precheck import PositionSnapshot

# Phase 1: read + extract snapshot
with precheck_connection() as con:
    row = db_get_position_by_id(con, pos_id)
    if row is None:
        raise PositionNotFoundError(pos_id)
snapshot = PositionSnapshot(
    pos_id=row["id"], tenant_id=row["tenant_id"],
    status=row["status"], entry_price=row["entry_price"],
    qty=row["qty"], direction=row["direction"],
)

# Phase 2: write-tx, re-validate mutable fields
with transaction() as con:  # BEGIN IMMEDIATE
    fresh = db_get_position_by_id(con, snapshot.pos_id)
    if fresh["tenant_id"] != snapshot.tenant_id or fresh["status"] != snapshot.status:
        raise StaleSnapshotError(snapshot.pos_id)
    # ... mutation using immutable fields from `snapshot` ...
```

## `snapshot_connection()` template

```python
from db.transaction import snapshot_connection

with snapshot_connection() as con:
    all_pos = db_get_positions(con)
# all_pos is now serialised to JSON / returned over HTTP / written to log. No follow-up write.
```

Used today by `update_positions_json`.

## Gotchas

- **PRAGMA query_only is a detector, not a defense.** Callers can re-enable writes via `PRAGMA query_only = 0`, `executescript` with embedded PRAGMA, or writes to `temp.*` tables. SQLite does not provide an ontologically read-only connection. The mechanism converts a class of bugs into LOUD errors at test time — it does not bound the trust surface.
- **The connection MUST NOT escape `with precheck_connection() as con:`.** If you find yourself returning `con` or stashing it on `self`, you have lost the invariant. Extract → snapshot → exit.
- **Immutable vs mutable fields:** trust immutable fields (e.g., `entry_price`, `qty`, `direction`) from the snapshot directly. Re-SELECT mutable fields (`tenant_id`, `status`) inside the write-tx.
- **Pure SQL helpers never call `precheck_connection` or `snapshot_connection` themselves.** They receive `con` from their caller. The call-site contract belongs to the operator, not the helper.

## Verify Checklist

- [ ] If the read feeds a write-tx in the same logical operation: `precheck_connection()` and the block exits with a snapshot value.
- [ ] If the read is terminal (snapshot → output): `snapshot_connection()`.
- [ ] No `con` reference escapes the `with` block of either helper.
- [ ] If `precheck` is used, the follow-up `transaction()` re-SELECTs and validates mutable fields before mutating.
- [ ] Pure SQL helpers in `db/`, `auth/` do NOT call either helper themselves — they accept `con` as the first argument.
