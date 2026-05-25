# Separate Semantic from Mechanical Invariants of read_only_connection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close issues #461 + #464 + #465 (Voronov's reframed Cluster C1). Split `read_only_connection()` — one mechanism currently bearing two semantic contracts — into `precheck_connection()` (feeds a mutation decision) and `snapshot_connection()` (terminal read). Reify `PositionSnapshot` as an immutable value and `PrecheckResult` as a sealed union. Migrate `PositionClosure` to consume the snapshot in its write-tx, making `tenant_id` re-validation obligatory by construction.

**Architecture:** Two new helpers in `db/transaction.py` share implementation (`PRAGMA query_only=1`) but bear distinct contracts. A new `operators/precheck.py` module defines `PositionSnapshot` (frozen dataclass) and `PrecheckResult` (NotFound | AlreadyClosed | OkToProceed(snapshot)). `PositionClosure.__enter__` returns a `PrecheckResult`; `execute()` unwraps `OkToProceed(snapshot)` and validates the mutable fields (tenant_id, status) inside the write-tx against the snapshot. Bug class "stale ownership pre-check" closes by construction (#461). The mechanical invariant ("connection refuses writes") is honestly documented as a cooperative-not-adversarial latch — a detector of helper-contract violations (#465).

**Tech Stack:** Python 3, `sqlite3` stdlib, `dataclasses`, `typing` (Union/Literal), `pytest`.

---

## Context (read before starting)

Voronov reframed Serrano's triage of #464 + #465:

> Hay una sola pregunta, y tiene dos caras: ¿qué cosa es `read_only_connection`? Hoy es dos cosas a la vez: una **invariante semántica** ("esta fase no muta el mundo") y un **mecanismo de motor** ("esta conexión SQLite rehúsa ciertos opcodes mientras un flag esté en 1"). Mientras compartan nombre, todo lo demás es relitigación del mismo error de categoría.

His 5-step strict order:

1. Aceptar `read_only_connection` es **detector**, no sandbox. Reescribir docstring.
2. **Partir en `precheck_connection` + `snapshot_connection`** (Serrano F6) ANTES de tocar el patrón compuesto.
3. **Reificar el snapshot** (no el flujo) como helper estructural.
4. Re-grade **#461 a obligatorio**.
5. Orden de ejecución: F6 (split) → #465 (threat model) → snapshot type → #464 (patrón) → #461 (re-SELECT como consecuencia del snapshot).

His punzante closing:

> C1 es la última oportunidad de separar la invariante semántica de la invariante mecánica antes de que el tercer call site las case por costumbre. Después de eso, ya no es una decisión arquitectónica. Es arqueología.

---

## Scope Check

One cohesive subsystem (DB access primitives + the operator that uses them + the new immutable type system between them). Already a single plan.

---

## File Structure

### New files

- `operators/precheck.py` — `PositionSnapshot` (frozen dataclass) + `PrecheckResult` sealed union (`PrecheckNotFound`, `PrecheckAlreadyClosed`, `PrecheckOkToProceed`).
- `tests/operators/test_precheck.py` — invariant tests for the new types (immutability, sealed union exhaustiveness, equality semantics).

### Modified files

- `db/transaction.py`:
  - Add `precheck_connection()` and `snapshot_connection()` (both share the `PRAGMA query_only=1` body).
  - Each has a distinct docstring naming its contract: precheck feeds a follow-up mutation; snapshot is terminal.
  - **Delete** `read_only_connection()` after both callers migrate (Task 7).
  - Rewrite the module-level "threat model" comment honestly (detector not sandbox; cooperative not adversarial).
- `operators/position_closure.py`:
  - `__enter__` uses `precheck_connection()`, returns `PrecheckResult` (NEW return type).
  - `execute()` accepts the `PrecheckResult` via instance state, unwraps `OkToProceed(snapshot)`, opens `transaction()`, re-SELECTs and **validates against snapshot.tenant_id and snapshot.status** (#461 closure by construction). Uses snapshot.direction/entry_price/qty for `_calc_pnl` (immutable fields trusted from snapshot).
- `tests/operators/test_position_closure.py`:
  - Existing 11 invariants stay valid behaviorally; some need adjustment to the new internal types.
  - Add invariant #12: `test_tenant_reassignment_between_precheck_and_write_rejects_close` (closes #461 with regression test).
- `api/positions.py`:
  - `update_positions_json()` swaps from `read_only_connection()` to `snapshot_connection()` (one-line change).
- `CLAUDE.md`:
  - Rewrite §4 ("Read-only pre-validation") as two sub-sections: §4a `precheck_connection` (feeds mutation decision; produces immutable snapshot for the write-tx) and §4b `snapshot_connection` (terminal reads). Document threat model honestly (detector against helper-contract violations; cooperative latch; not a sandbox).

### Deleted files

None. `read_only_connection` is removed from `db/transaction.py` but the file itself stays.

---

## Locked API Design

### `db/transaction.py` after split

```python
@contextmanager
def precheck_connection() -> Iterator[sqlite3.Connection]:
    """Open a configured connection for a PRECHECK READ that will feed a
    follow-up write transaction.

    Use when an operator needs to read state to decide whether (and how) to
    open a write transaction in a later step. The connection closes on exit;
    no BEGIN/COMMIT is issued.

    Contract (cooperative — see threat model below):
    - MAY use con.execute for SELECT.
    - INSERT/UPDATE/DELETE raise sqlite3.OperationalError via PRAGMA query_only=1.
    - MUST extract any field the write-tx will need into an immutable snapshot
      value (see operators.precheck.PositionSnapshot) BEFORE this block exits.
    - MUST NOT escape the connection past the `with` block.

    Threat model:
    - Detects accidental writes from helpers contracted as read-only.
      A SQL helper that mistakenly mutates state inside this block fails LOUDLY.
    - NOT a sandbox: callers can re-enable writes via PRAGMA query_only=0,
      executescript, or writes to temp.* tables. SQLite does not provide an
      ontologically read-only connection; this is a cooperative latch.
    - The semantic invariant "this phase does not mutate the world" lives at
      the CALL SITE (extract → snapshot → terminate), not in this primitive.
    """
    con = _open_configured_connection()
    try:
        con.execute("PRAGMA query_only = 1")
        yield con
    finally:
        con.close()


@contextmanager
def snapshot_connection() -> Iterator[sqlite3.Connection]:
    """Open a configured connection for a TERMINAL READ (no follow-up write).

    Use for snapshot generation, dashboard queries, audit reads — operations
    whose result is serialized to an output (JSON file, HTTP response, log)
    and NOT used to drive a subsequent mutation.

    Contract: same mechanism as precheck_connection, same threat model
    (cooperative latch, detector-not-sandbox). The distinct name encodes a
    distinct CALL SITE OBLIGATION: terminal reads do not need to produce a
    snapshot for hand-off to a write-tx, so there is no follow-up re-validation
    contract.

    See precheck_connection for the threat model and mechanism details.
    """
    con = _open_configured_connection()
    try:
        con.execute("PRAGMA query_only = 1")
        yield con
    finally:
        con.close()
```

### `operators/precheck.py` (new file)

```python
"""Precheck pattern types — immutable hand-off from read-side to write-tx.

Reifies the pattern Voronov articulated for PR #463 follow-up: a precheck
reads outside any transaction, decides whether a follow-up write is needed,
and produces an immutable snapshot for the write-tx to re-validate against.

PositionSnapshot is intentionally minimal: it carries only the fields that
(a) the precheck has DECIDED upon, and (b) the write-tx must RE-VALIDATE
inside its BEGIN IMMEDIATE block. Adding a field here = the write-tx must
re-validate it. Removing a field = the precheck no longer commits to it.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class PositionSnapshot:
    """Immutable snapshot of a position row as read by the precheck.

    Fields that are MUTABLE in the DB lifetime (tenant_id can be reassigned;
    status can transition open→closed) MUST be re-validated by the write-tx
    against this snapshot. Fields that are immutable (entry_price, qty,
    direction, symbol) are trusted from the snapshot and consumed directly.
    """
    # Identity
    pos_id: int
    # Mutable — write-tx MUST re-validate against this snapshot
    tenant_id: int | None
    status: str
    # Immutable post-creation — trusted from snapshot
    symbol: str
    direction: str
    entry_price: float
    qty: float


@dataclass(frozen=True)
class PrecheckNotFound:
    """Position does not exist OR (USER mode) belongs to a different tenant.
    Observationally identical to actual not-found (IDOR-safe collapse)."""
    pass


@dataclass(frozen=True)
class PrecheckAlreadyClosed:
    """Position exists but is no longer in 'open' status. Idempotent close
    path — caller returns success without firing side-effects."""
    snapshot: PositionSnapshot


@dataclass(frozen=True)
class PrecheckOkToProceed:
    """Position passed all precheck conditions. The snapshot carries the
    fields the write-tx will need (immutable directly; mutable re-validated)."""
    snapshot: PositionSnapshot


PrecheckResult = Union[PrecheckNotFound, PrecheckAlreadyClosed, PrecheckOkToProceed]
```

### `operators/position_closure.py` after migration (changed methods only)

```python
def __enter__(self) -> "PositionClosure":
    if self._consumed:
        raise RuntimeError("PositionClosure is single-use; construct a new one")
    self._precheck_result = self._run_precheck()
    return self


def _run_precheck(self) -> PrecheckResult:
    """Read outside any transaction; return one of the 3 PrecheckResult variants.

    Implements ownership-before-lock (USER mode): a row whose tenant_id does
    not match caller_tenant_id collapses to PrecheckNotFound (IDOR-safe).
    """
    from operators.precheck import (
        PrecheckNotFound, PrecheckAlreadyClosed, PrecheckOkToProceed,
        PositionSnapshot,
    )
    from db.transaction import precheck_connection

    with precheck_connection() as con:
        row = db_get_position_by_id(con, self._pos_id)

    if row is None:
        return PrecheckNotFound()

    snapshot = PositionSnapshot(
        pos_id=row["id"],
        tenant_id=row["tenant_id"],
        status=row["status"],
        symbol=row["symbol"],
        direction=row["direction"],
        entry_price=row["entry_price"],
        qty=row["qty"],
    )

    if self._mode == "USER":
        if snapshot.tenant_id != self._caller_tenant_id:
            return PrecheckNotFound()  # IDOR-safe collapse

    if snapshot.status != "open":
        return PrecheckAlreadyClosed(snapshot=snapshot)

    return PrecheckOkToProceed(snapshot=snapshot)


def execute(self) -> CloseOutcome:
    if self._consumed:
        raise RuntimeError("PositionClosure already executed; single-use")
    self._consumed = True

    from operators.precheck import (
        PrecheckNotFound, PrecheckAlreadyClosed, PrecheckOkToProceed,
    )

    result = self._precheck_result

    if isinstance(result, PrecheckNotFound):
        return CloseOutcome(status="not_found", position=None, pnl_usd=None, pnl_pct=None)

    if isinstance(result, PrecheckAlreadyClosed):
        return CloseOutcome(
            status="already_closed",
            position=self._snapshot_to_dict(result.snapshot),
            pnl_usd=None,
            pnl_pct=None,
        )

    # PrecheckOkToProceed
    snapshot = result.snapshot
    with _tx_module.transaction() as con:
        # Re-SELECT inside write-tx + RE-VALIDATE snapshot's mutable fields.
        # #461 closure: tenant_id re-check is obligatory, not defense-in-depth.
        row = db_get_position_by_id(con, self._pos_id)
        if row is None:
            return CloseOutcome(status="not_found", position=None, pnl_usd=None, pnl_pct=None)
        if row["tenant_id"] != snapshot.tenant_id:
            # Tenant reassigned between precheck and write-tx. IDOR-safe.
            return CloseOutcome(status="not_found", position=None, pnl_usd=None, pnl_pct=None)
        if row["status"] != "open":
            self._result_row = row
            return CloseOutcome(
                status="already_closed", position=row, pnl_usd=None, pnl_pct=None,
            )

        # Snapshot's immutable fields trusted; use them for _calc_pnl.
        pnl_usd, pnl_pct = _calc_pnl(
            snapshot.direction, snapshot.entry_price, self._exit_price, snapshot.qty,
        )
        exit_ts = self._now.isoformat()
        closed_row = db_close_position_sql(
            con, self._pos_id, self._exit_price, self._exit_reason,
            exit_ts, pnl_usd, pnl_pct,
        )
        if snapshot.tenant_id is not None and pnl_usd is not None:
            _capital_module.apply_pnl_to_capital(con, snapshot.tenant_id, pnl_usd)
        elif snapshot.tenant_id is None:
            log.warning(
                "PositionClosure: skipping capital roll-in for legacy tenant_id=NULL pos_id=%s",
                self._pos_id,
            )
        self._result_row = closed_row
        self._result_pnl = (pnl_usd, pnl_pct)
    return CloseOutcome(
        status="closed",
        position=self._result_row,
        pnl_usd=self._result_pnl[0],
        pnl_pct=self._result_pnl[1],
    )


@staticmethod
def _snapshot_to_dict(snapshot) -> dict:
    """Convert a PositionSnapshot back to dict for the CloseOutcome.position
    field (which existing callers expect as a dict). Adds a 'status' marker
    field consistent with the snapshot's recorded status."""
    return {
        "id": snapshot.pos_id,
        "tenant_id": snapshot.tenant_id,
        "status": snapshot.status,
        "symbol": snapshot.symbol,
        "direction": snapshot.direction,
        "entry_price": snapshot.entry_price,
        "qty": snapshot.qty,
    }
```

---

## Tasks

### Task 1: Verify branch + baseline + audit callers of `read_only_connection`

**Files:** none modified.

- [ ] **Step 1: Confirm branch and HEAD**

Run: `git rev-parse --abbrev-ref HEAD && git rev-parse HEAD`
Expected: `feat/separate-semantic-mechanical-invariants-464-465-461` and a commit at or descending from `3ad6b325` (the #463 merge into main).

- [ ] **Step 2: Confirm clean working tree**

Run: `git status --short`
Expected: empty (the plan file is committed in the next step before Task 2 runs).

- [ ] **Step 3: Baseline test count**

Run: `pytest --collect-only -q 2>&1 | tail -3`
Expected: ~2515 tests collected. Note the exact number.

- [ ] **Step 4: Audit callers of read_only_connection across the repo**

Run: `grep -rn "read_only_connection" --include="*.py" .`
Expected: definition in `db/transaction.py` + import + 2 production callers (`operators/position_closure.py` and `api/positions.py::update_positions_json`) + the 3 invariant tests in `tests/db/test_transaction.py`. No other callers.

If any unexpected caller appears, STOP and report DONE_WITH_CONCERNS — the migration plan assumes exactly 2 production callers.

- [ ] **Step 5: Smoke check current 11 PositionClosure invariants pass**

Run: `pytest tests/operators/test_position_closure.py -q 2>&1 | tail -5`
Expected: 11/11 pass.

---

### Task 2: Rewrite `read_only_connection` docstring as detector-not-sandbox

**Files:**
- Modify: `db/transaction.py` (docstring only — partial close of #465)

This task encodes the threat model honestly. It is a stepping stone: Task 3 introduces the two new helpers; Task 7 deletes `read_only_connection` once the migration is complete. But until Task 7, the existing helper carries a docstring that lies. Fix that first so the codebase is internally honest at every commit.

- [ ] **Step 1: Read the current docstring**

Run: `sed -n '/def read_only_connection/,/^@\|^def [a-zA-Z]/p' db/transaction.py | head -25`

Confirm the docstring contains "ENFORCED AT RUNTIME via PRAGMA query_only=1" — that is the line being made honest.

- [ ] **Step 2: Replace the docstring**

Use `Edit` to replace the existing `read_only_connection` docstring with:

```python
    """Open a configured connection for read-only work (DEPRECATED in this PR).

    This helper carries two semantic contracts under one name (precheck +
    snapshot) — see PR #463 review (Voronov, 2026-05-25). It will be removed
    later in this plan, replaced by precheck_connection and snapshot_connection.

    Threat model (cooperative latch — NOT a sandbox):
    - Detects accidental writes from helpers contracted as read-only.
      A SQL helper that mistakenly mutates state inside this block fails loudly.
    - Does NOT protect against PRAGMA query_only=0, executescript with embedded
      PRAGMA, writes to temp.* tables, or AFTER triggers.
    - The semantic invariant "this phase does not mutate the world" lives at
      the CALL SITE, not in this primitive.

    Caller contract:
    - MAY use con.execute for SELECT.
    - INSERT/UPDATE/DELETE raise sqlite3.OperationalError (cooperative).
    - MUST NOT escape the connection past the `with` block.
    """
```

- [ ] **Step 3: Run all 12 transaction tests**

Run: `pytest tests/db/test_transaction.py -v 2>&1 | tail -15`
Expected: 12/12 pass (docstring changes do not break behavior).

- [ ] **Step 4: Commit**

```bash
git add db/transaction.py
git commit -m "docs(db): rewrite read_only_connection docstring as cooperative latch / detector (partial close #465)

Names the threat model honestly: detects accidental writes from helpers
contracted as read-only; cooperative not adversarial; semantic invariant
lives at call site. Helper itself will be split in Task 3 and removed
in Task 7 of plan 2026-05-25-464-465-461."
```

---

### Task 3: Add `precheck_connection` + `snapshot_connection` to `db/transaction.py`

**Files:**
- Modify: `db/transaction.py` (add two new context managers; do NOT delete `read_only_connection` yet — Task 7 handles that)

- [ ] **Step 1: Add the two new helpers**

Append to `db/transaction.py` AFTER `read_only_connection()` (which stays in place for now):

```python
@contextmanager
def precheck_connection() -> Iterator[sqlite3.Connection]:
    """Open a configured connection for a PRECHECK READ that will feed a
    follow-up write transaction.

    Use when an operator needs to read state to decide whether (and how) to
    open a write transaction in a later step. The connection closes on exit;
    no BEGIN/COMMIT is issued.

    Contract (cooperative — see threat model below):
    - MAY use con.execute for SELECT.
    - INSERT/UPDATE/DELETE raise sqlite3.OperationalError via PRAGMA query_only=1.
    - MUST extract any field the write-tx will need into an immutable snapshot
      value (see operators.precheck.PositionSnapshot) BEFORE this block exits.
    - MUST NOT escape the connection past the `with` block.

    Threat model:
    - Detects accidental writes from helpers contracted as read-only.
      A SQL helper that mistakenly mutates state inside this block fails LOUDLY.
    - NOT a sandbox: callers can re-enable writes via PRAGMA query_only=0,
      executescript, or writes to temp.* tables. SQLite does not provide an
      ontologically read-only connection; this is a cooperative latch.
    - The semantic invariant "this phase does not mutate the world" lives at
      the CALL SITE (extract → snapshot → terminate), not in this primitive.
    """
    con = _open_configured_connection()
    try:
        con.execute("PRAGMA query_only = 1")
        yield con
    finally:
        con.close()


@contextmanager
def snapshot_connection() -> Iterator[sqlite3.Connection]:
    """Open a configured connection for a TERMINAL READ (no follow-up write).

    Use for snapshot generation, dashboard queries, audit reads — operations
    whose result is serialized to an output (JSON file, HTTP response, log)
    and NOT used to drive a subsequent mutation.

    Contract: same mechanism as precheck_connection, same threat model
    (cooperative latch, detector-not-sandbox). The distinct name encodes a
    distinct CALL SITE OBLIGATION: terminal reads do not need to produce a
    snapshot for hand-off to a write-tx, so there is no follow-up re-validation
    contract.

    See precheck_connection for the threat model and mechanism details.
    """
    con = _open_configured_connection()
    try:
        con.execute("PRAGMA query_only = 1")
        yield con
    finally:
        con.close()
```

- [ ] **Step 2: Smoke import both new helpers**

```bash
python -c "from db.transaction import precheck_connection, snapshot_connection, read_only_connection, transaction; print('ok')"
```
Expected: `ok`.

- [ ] **Step 3: Add 2 invariant tests confirming both new helpers reject writes**

Append to `tests/db/test_transaction.py`:

```python


# ---- precheck_connection + snapshot_connection enforcement (closes #464 #465) ----

def test_precheck_connection_rejects_writes_same_as_read_only(fresh_db):
    """precheck_connection shares the mechanism of read_only_connection;
    INSERT must raise OperationalError."""
    from db.transaction import precheck_connection, transaction

    with transaction() as con:
        con.execute("CREATE TABLE pc_test (x INTEGER)")

    with pytest.raises(sqlite3.OperationalError, match="(?i)read-only|query_only|attempt to write"):
        with precheck_connection() as con:
            con.execute("INSERT INTO pc_test (x) VALUES (1)")


def test_snapshot_connection_rejects_writes_same_as_read_only(fresh_db):
    """snapshot_connection shares the mechanism; INSERT must raise OperationalError.
    Distinct contract from precheck (terminal read vs feed-write) but same enforcement."""
    from db.transaction import snapshot_connection, transaction

    with transaction() as con:
        con.execute("CREATE TABLE sc_test (x INTEGER)")

    with pytest.raises(sqlite3.OperationalError, match="(?i)read-only|query_only|attempt to write"):
        with snapshot_connection() as con:
            con.execute("INSERT INTO sc_test (x) VALUES (1)")
```

- [ ] **Step 4: Run all transaction tests**

Run: `pytest tests/db/test_transaction.py -v 2>&1 | tail -20`
Expected: 14/14 pass (12 previous + 2 new).

- [ ] **Step 5: Commit**

```bash
git add db/transaction.py tests/db/test_transaction.py
git commit -m "feat(db): introduce precheck_connection + snapshot_connection (closes #464)

Two helpers, one mechanism, distinct call-site contracts. precheck feeds
a follow-up write tx (caller must extract immutable snapshot before exit);
snapshot is terminal (no follow-up). Same threat model as read_only_connection
(cooperative latch). read_only_connection itself removed in Task 7 once
both callers migrate."
```

---

### Task 4: Migrate `update_positions_json` to `snapshot_connection`

**Files:**
- Modify: `api/positions.py`

- [ ] **Step 1: Read the current import + caller**

Run: `grep -n "read_only_connection\|update_positions_json" api/positions.py | head -10`

Confirm:
- The import at the top imports `read_only_connection`.
- `update_positions_json()` uses `with read_only_connection() as con:`.

- [ ] **Step 2: Switch the import**

Replace at top of `api/positions.py`:
```python
from db.transaction import transaction, read_only_connection
```
with:
```python
from db.transaction import transaction, snapshot_connection
```

(Note: `read_only_connection` still exists in `db/transaction.py` until Task 7; we're just no longer importing it here.)

- [ ] **Step 3: Switch the context manager inside `update_positions_json`**

Replace:
```python
with read_only_connection() as con:
    all_pos = db_get_positions(con)
```
with:
```python
with snapshot_connection() as con:
    all_pos = db_get_positions(con)
```

(One-line change. Body of the `with` block is unchanged.)

- [ ] **Step 4: Smoke import + run positions tests**

```bash
python -c "from api.positions import update_positions_json; print('ok')"
pytest tests/operators/ tests/api/ -q --tb=short 2>&1 | tail -10
```
Expected: `ok` + 12/12 (the operators invariants that exercise `update_positions_json` via `PositionClosure.__exit__`) pass.

- [ ] **Step 5: Commit**

```bash
git add api/positions.py
git commit -m "refactor(api): migrate update_positions_json to snapshot_connection (closes #462 follow-up; advances #464)

This is a terminal read (snapshot for JSON output), not a precheck.
Using snapshot_connection makes the call-site contract explicit:
no follow-up write-tx, no re-validation obligation."
```

---

### Task 5: Write failing tests for `PositionSnapshot` + `PrecheckResult` types

**Files:**
- Create: `tests/operators/test_precheck.py`

- [ ] **Step 1: Create the test file**

```python
"""Invariant tests for operators.precheck (PositionSnapshot + PrecheckResult)."""
import pytest

# The module does not exist yet; these tests fail at collection until Task 6.


def test_position_snapshot_is_frozen():
    """PositionSnapshot is a frozen dataclass — mutation raises FrozenInstanceError."""
    from dataclasses import FrozenInstanceError
    from operators.precheck import PositionSnapshot

    snap = PositionSnapshot(
        pos_id=1, tenant_id=42, status="open",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )
    with pytest.raises(FrozenInstanceError):
        snap.tenant_id = 99


def test_position_snapshot_equality_by_value():
    """Two PositionSnapshots with identical fields are equal (dataclass default)."""
    from operators.precheck import PositionSnapshot

    a = PositionSnapshot(
        pos_id=1, tenant_id=42, status="open",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )
    b = PositionSnapshot(
        pos_id=1, tenant_id=42, status="open",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )
    assert a == b
    assert hash(a) == hash(b)


def test_precheck_result_variants_distinguishable():
    """The 3 PrecheckResult variants are distinct types and pattern-matchable
    via isinstance."""
    from operators.precheck import (
        PositionSnapshot, PrecheckNotFound, PrecheckAlreadyClosed, PrecheckOkToProceed,
    )

    snap = PositionSnapshot(
        pos_id=1, tenant_id=42, status="open",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )

    nf = PrecheckNotFound()
    ac = PrecheckAlreadyClosed(snapshot=snap)
    ok = PrecheckOkToProceed(snapshot=snap)

    assert isinstance(nf, PrecheckNotFound)
    assert not isinstance(nf, PrecheckAlreadyClosed)
    assert not isinstance(nf, PrecheckOkToProceed)

    assert isinstance(ac, PrecheckAlreadyClosed)
    assert not isinstance(ac, PrecheckOkToProceed)

    assert isinstance(ok, PrecheckOkToProceed)
    assert ok.snapshot == snap


def test_precheck_not_found_carries_no_snapshot():
    """PrecheckNotFound is IDOR-safe: it does NOT carry the snapshot, so
    USER-mode 'belongs to another tenant' and 'does not exist at all' produce
    observationally identical values."""
    from operators.precheck import PrecheckNotFound

    a = PrecheckNotFound()
    b = PrecheckNotFound()
    assert a == b  # All instances equal — no per-instance state
```

- [ ] **Step 2: Verify the 4 tests fail with ModuleNotFoundError**

Run: `pytest tests/operators/test_precheck.py -v 2>&1 | tail -15`
Expected: `ModuleNotFoundError: No module named 'operators.precheck'`.

- [ ] **Step 3: Commit**

```bash
git add tests/operators/test_precheck.py
git commit -m "test(operators): add failing invariant tests for PositionSnapshot + PrecheckResult (advances #464)

PositionSnapshot is frozen + value-equal. PrecheckResult is a sealed union of
3 variants. PrecheckNotFound carries no snapshot (IDOR-safe — different tenant
and missing row produce identical values)."
```

---

### Task 6: Implement `operators/precheck.py`

**Files:**
- Create: `operators/precheck.py`

- [ ] **Step 1: Create the module**

```python
"""Precheck pattern types — immutable hand-off from read-side to write-tx.

Reifies the pattern Voronov articulated for PR #463 follow-up: a precheck
reads outside any transaction, decides whether a follow-up write is needed,
and produces an immutable snapshot for the write-tx to re-validate against.

PositionSnapshot is intentionally minimal: it carries only the fields that
(a) the precheck has DECIDED upon, and (b) the write-tx must RE-VALIDATE
inside its BEGIN IMMEDIATE block. Adding a field here = the write-tx must
re-validate it. Removing a field = the precheck no longer commits to it.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class PositionSnapshot:
    """Immutable snapshot of a position row as read by the precheck.

    Fields that are MUTABLE in the DB lifetime (tenant_id can be reassigned;
    status can transition open→closed) MUST be re-validated by the write-tx
    against this snapshot. Fields that are immutable (entry_price, qty,
    direction, symbol) are trusted from the snapshot and consumed directly.
    """
    # Identity
    pos_id: int
    # Mutable — write-tx MUST re-validate against this snapshot
    tenant_id: int | None
    status: str
    # Immutable post-creation — trusted from snapshot
    symbol: str
    direction: str
    entry_price: float
    qty: float


@dataclass(frozen=True)
class PrecheckNotFound:
    """Position does not exist OR (USER mode) belongs to a different tenant.
    Observationally identical to actual not-found (IDOR-safe collapse)."""
    pass


@dataclass(frozen=True)
class PrecheckAlreadyClosed:
    """Position exists but is no longer in 'open' status. Idempotent close
    path — caller returns success without firing side-effects."""
    snapshot: PositionSnapshot


@dataclass(frozen=True)
class PrecheckOkToProceed:
    """Position passed all precheck conditions. The snapshot carries the
    fields the write-tx will need (immutable directly; mutable re-validated)."""
    snapshot: PositionSnapshot


PrecheckResult = Union[PrecheckNotFound, PrecheckAlreadyClosed, PrecheckOkToProceed]
```

- [ ] **Step 2: Verify the 4 tests pass**

Run: `pytest tests/operators/test_precheck.py -v 2>&1 | tail -10`
Expected: 4/4 pass.

- [ ] **Step 3: Verify the 11 PositionClosure invariants still pass (no change to the operator yet)**

Run: `pytest tests/operators/test_position_closure.py -q 2>&1 | tail -5`
Expected: 11/11 pass.

- [ ] **Step 4: Commit**

```bash
git add operators/precheck.py
git commit -m "feat(operators): introduce PositionSnapshot + PrecheckResult sealed union (closes #464 type layer)

Immutable snapshot carries the fields the precheck has decided upon.
Mutable fields (tenant_id, status) MUST be re-validated by the write-tx.
Immutable fields (entry_price, qty, direction, symbol) are trusted from
the snapshot. PrecheckResult is a 3-variant sealed union; PrecheckNotFound
carries no snapshot (IDOR-safe collapse)."
```

---

### Task 7: Migrate `PositionClosure` to consume `PrecheckResult` and re-validate snapshot in write-tx

**Files:**
- Modify: `operators/position_closure.py`

This is the architecturally important change. The operator stops re-extracting fields from a re-SELECTed row and instead consumes the snapshot directly, with the write-tx now obligated to re-validate the snapshot's MUTABLE fields (closes #461 by construction).

- [ ] **Step 1: Update imports**

At top of `operators/position_closure.py`, add:
```python
from operators.precheck import (
    PositionSnapshot,
    PrecheckNotFound,
    PrecheckAlreadyClosed,
    PrecheckOkToProceed,
    PrecheckResult,
)
from db.transaction import precheck_connection
```

Remove (if present) the import of `read_only_connection` from `db.transaction` (replaced by `precheck_connection`).

- [ ] **Step 2: Replace `__enter__` method**

Replace the existing `__enter__` body with:

```python
    def __enter__(self) -> "PositionClosure":
        if self._consumed:
            raise RuntimeError("PositionClosure is single-use; construct a new one")
        self._precheck_result = self._run_precheck()
        return self
```

- [ ] **Step 3: Add the `_run_precheck` helper method**

Add as an instance method on `PositionClosure`:

```python
    def _run_precheck(self) -> PrecheckResult:
        """Read outside any transaction; return one of the 3 PrecheckResult variants.

        Implements ownership-before-lock (USER mode): a row whose tenant_id does
        not match caller_tenant_id collapses to PrecheckNotFound (IDOR-safe).
        """
        with precheck_connection() as con:
            row = db_get_position_by_id(con, self._pos_id)

        if row is None:
            return PrecheckNotFound()

        snapshot = PositionSnapshot(
            pos_id=row["id"],
            tenant_id=row["tenant_id"],
            status=row["status"],
            symbol=row["symbol"],
            direction=row["direction"],
            entry_price=row["entry_price"],
            qty=row["qty"],
        )

        if self._mode == "USER":
            if snapshot.tenant_id != self._caller_tenant_id:
                return PrecheckNotFound()  # IDOR-safe collapse

        if snapshot.status != "open":
            return PrecheckAlreadyClosed(snapshot=snapshot)

        return PrecheckOkToProceed(snapshot=snapshot)
```

- [ ] **Step 4: Replace `execute` method**

Replace the existing `execute` body with:

```python
    def execute(self) -> CloseOutcome:
        if self._consumed:
            raise RuntimeError("PositionClosure already executed; single-use")
        self._consumed = True

        result = self._precheck_result

        if isinstance(result, PrecheckNotFound):
            return CloseOutcome(status="not_found", position=None, pnl_usd=None, pnl_pct=None)

        if isinstance(result, PrecheckAlreadyClosed):
            return CloseOutcome(
                status="already_closed",
                position=self._snapshot_to_dict(result.snapshot),
                pnl_usd=None,
                pnl_pct=None,
            )

        # PrecheckOkToProceed: write-tx must re-validate snapshot's mutable fields.
        snapshot = result.snapshot
        with _tx_module.transaction() as con:
            row = db_get_position_by_id(con, self._pos_id)
            if row is None:
                return CloseOutcome(status="not_found", position=None, pnl_usd=None, pnl_pct=None)
            # #461 closure: tenant_id re-validation is obligatory by construction.
            if row["tenant_id"] != snapshot.tenant_id:
                # Tenant reassigned between precheck and write-tx. IDOR-safe collapse.
                return CloseOutcome(status="not_found", position=None, pnl_usd=None, pnl_pct=None)
            if row["status"] != "open":
                self._result_row = row
                return CloseOutcome(
                    status="already_closed", position=row, pnl_usd=None, pnl_pct=None,
                )

            # Snapshot's immutable fields trusted; consume directly.
            pnl_usd, pnl_pct = _calc_pnl(
                snapshot.direction, snapshot.entry_price, self._exit_price, snapshot.qty,
            )
            exit_ts = self._now.isoformat()
            closed_row = db_close_position_sql(
                con, self._pos_id, self._exit_price, self._exit_reason,
                exit_ts, pnl_usd, pnl_pct,
            )
            if snapshot.tenant_id is not None and pnl_usd is not None:
                _capital_module.apply_pnl_to_capital(con, snapshot.tenant_id, pnl_usd)
            elif snapshot.tenant_id is None:
                log.warning(
                    "PositionClosure: skipping capital roll-in for legacy tenant_id=NULL pos_id=%s",
                    self._pos_id,
                )
            self._result_row = closed_row
            self._result_pnl = (pnl_usd, pnl_pct)
        return CloseOutcome(
            status="closed",
            position=self._result_row,
            pnl_usd=self._result_pnl[0],
            pnl_pct=self._result_pnl[1],
        )
```

- [ ] **Step 5: Add the `_snapshot_to_dict` helper method**

Add as a static method on `PositionClosure`:

```python
    @staticmethod
    def _snapshot_to_dict(snapshot: PositionSnapshot) -> dict:
        """Convert a PositionSnapshot back to dict for the CloseOutcome.position
        field (which existing callers expect as a dict)."""
        return {
            "id": snapshot.pos_id,
            "tenant_id": snapshot.tenant_id,
            "status": snapshot.status,
            "symbol": snapshot.symbol,
            "direction": snapshot.direction,
            "entry_price": snapshot.entry_price,
            "qty": snapshot.qty,
        }
```

- [ ] **Step 6: Initialize `_precheck_result` in `__init__`**

Find the `__init__` method. Where `self._consumed = False` is set, add:
```python
self._precheck_result: PrecheckResult | None = None
```

- [ ] **Step 7: Run the 11 PositionClosure invariants**

Run: `pytest tests/operators/test_position_closure.py -v 2>&1 | tail -20`
Expected: 11/11 pass. The behavior contract is unchanged; only the internal hand-off mechanism changed.

If any test fails because it patched something on `read_only_connection` that no longer exists in the operator's import path, update the patch target to `precheck_connection` (the operator now uses that). Check each failing test individually before making the patch update.

- [ ] **Step 8: Run the 4 precheck type tests**

Run: `pytest tests/operators/test_precheck.py -v 2>&1 | tail -10`
Expected: 4/4 still pass.

- [ ] **Step 9: Commit**

```bash
git add operators/position_closure.py
git commit -m "feat(operators): PositionClosure consumes PrecheckResult; write-tx re-validates snapshot (closes #461 #464)

__enter__ produces PrecheckResult (sealed union of NotFound | AlreadyClosed
| OkToProceed(snapshot)). execute() unwraps the result; for OkToProceed,
opens transaction() and re-validates snapshot's MUTABLE fields (tenant_id,
status) against the freshly read row. Snapshot's IMMUTABLE fields
(direction, entry_price, qty, symbol) are trusted directly.

Closes #461 by construction: tenant_id re-validation in write-tx is now
mechanical, not optional. A tenant reassignment between precheck and
BEGIN IMMEDIATE collapses to IDOR-safe NOT_FOUND."
```

---

### Task 8: Add regression test for tenant reassignment race (#461 closure proof)

**Files:**
- Modify: `tests/operators/test_position_closure.py`

This test exercises the exact scenario #461 described: tenant_id is reassigned between the precheck and the write-tx. The new operator (Task 7) must collapse to NOT_FOUND.

- [ ] **Step 1: Append the regression test**

Append to `tests/operators/test_position_closure.py`:

```python


# ---- Invariant 12: tenant reassignment between precheck and write-tx (closes #461) ----

def test_tenant_reassignment_between_precheck_and_write_rejects_close(fresh_db_with_two_tenants):
    """If tenant_id changes between precheck and BEGIN IMMEDIATE, the operator
    must collapse to NOT_FOUND. Validates that snapshot re-validation in the
    write-tx covers the IDOR race window that #461 named.

    Setup: position 1 owned by tenant 1. We construct PositionClosure(USER, 1),
    enter it (precheck reads tenant_id=1), then directly UPDATE the row to
    tenant_id=2 before execute() runs. The write-tx's re-SELECT should detect
    the reassignment and return not_found."""
    from operators.position_closure import PositionClosure

    closure = PositionClosure(
        pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
        mode="USER", caller_tenant_id=1,
    )
    # __enter__ runs the precheck.
    closure.__enter__()

    # Simulate tenant reassignment between precheck and write-tx.
    with transaction() as con:
        con.execute("UPDATE positions SET tenant_id = 2 WHERE id = 1")

    # execute() opens the write-tx and re-validates the snapshot. The mismatch
    # must collapse to NOT_FOUND (IDOR-safe).
    outcome = closure.execute()
    closure.__exit__(None, None, None)

    assert outcome.status == "not_found"

    # The position is unchanged; status is still 'open'.
    with transaction() as con:
        pos = con.execute("SELECT status, tenant_id FROM positions WHERE id=1").fetchone()
    assert pos["status"] == "open"
    assert pos["tenant_id"] == 2  # The reassignment is unchanged (we wrote it, operator did not touch it)
```

- [ ] **Step 2: Run the new test**

Run: `pytest tests/operators/test_position_closure.py::test_tenant_reassignment_between_precheck_and_write_rejects_close -v 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 3: Run all 12 invariants**

Run: `pytest tests/operators/test_position_closure.py -v 2>&1 | tail -25`
Expected: 12/12 pass.

- [ ] **Step 4: Commit**

```bash
git add tests/operators/test_position_closure.py
git commit -m "test(operators): add invariant #12 — tenant reassignment race rejected (closes #461 proof)

Exercises the IDOR race window between precheck and BEGIN IMMEDIATE:
position is owned by tenant 1 at precheck time; we reassign to tenant 2
between __enter__ and execute(); the write-tx's snapshot re-validation
must detect the mismatch and collapse to NOT_FOUND. Locks in the
mechanical closure of #461."
```

---

### Task 9: Delete `read_only_connection` from `db/transaction.py`

**Files:**
- Modify: `db/transaction.py`
- Modify: `tests/db/test_transaction.py` (remove the 3 invariant tests that targeted the deleted helper)

- [ ] **Step 1: Audit remaining callers**

Run: `grep -rn "read_only_connection" --include="*.py" .`
Expected: definition in `db/transaction.py` + 3 invariant tests in `tests/db/test_transaction.py`. NO other callers (PositionClosure migrated in Task 7; update_positions_json migrated in Task 4).

If any production caller remains, return to that caller's task and migrate before continuing.

- [ ] **Step 2: Delete the `read_only_connection` function from `db/transaction.py`**

Remove the entire function body (including the docstring that was rewritten in Task 2). `precheck_connection` and `snapshot_connection` and `transaction` remain.

- [ ] **Step 3: Delete the 3 invariant tests in `tests/db/test_transaction.py` that targeted `read_only_connection`**

Locate and delete:
- `test_read_only_connection_rejects_insert`
- `test_read_only_connection_rejects_update`
- `test_read_only_connection_allows_select_and_does_not_leak_query_only`

The 2 new tests added in Task 3 (`test_precheck_connection_rejects_writes_same_as_read_only`, `test_snapshot_connection_rejects_writes_same_as_read_only`) cover the same mechanism for the surviving helpers.

The third (`_allows_select_and_does_not_leak`) covered the PRAGMA-leak invariant. Add an equivalent for `precheck_connection`:

```python


def test_precheck_connection_does_not_leak_query_only(fresh_db):
    """PRAGMA query_only set inside precheck_connection must NOT leak to
    subsequent transaction() connections."""
    from db.transaction import precheck_connection, transaction

    with transaction() as con:
        con.execute("CREATE TABLE leak_test (x INTEGER)")
        con.execute("INSERT INTO leak_test (x) VALUES (42)")

    # SELECT works through precheck_connection.
    with precheck_connection() as con:
        row = con.execute("SELECT x FROM leak_test").fetchone()
    assert row["x"] == 42

    # After the precheck block exits, a fresh transaction() must accept writes
    # (proves PRAGMA query_only does NOT leak across connections).
    with transaction() as con:
        con.execute("INSERT INTO leak_test (x) VALUES (99)")
    with transaction() as con:
        rows = con.execute("SELECT x FROM leak_test ORDER BY x").fetchall()
    assert [r["x"] for r in rows] == [42, 99]
```

- [ ] **Step 4: Verify the grep is clean**

Run: `grep -rn "read_only_connection" --include="*.py" .`
Expected: empty.

- [ ] **Step 5: Verify import is clean**

```bash
python -c "from db.transaction import transaction, precheck_connection, snapshot_connection; print('ok')"
python -c "from db.transaction import read_only_connection" 2>&1 | tail -3
```
Expected: `ok` from the first command; `ImportError` from the second.

- [ ] **Step 6: Run all transaction tests**

Run: `pytest tests/db/test_transaction.py -v 2>&1 | tail -20`
Expected: count = 9 original + 2 new from Task 3 + 1 new leak-test = 12 passing.

- [ ] **Step 7: Run all PositionClosure invariants**

Run: `pytest tests/operators/test_position_closure.py -q 2>&1 | tail -5`
Expected: 12/12 pass.

- [ ] **Step 8: Commit**

```bash
git add db/transaction.py tests/db/test_transaction.py
git commit -m "refactor(db): delete read_only_connection — replaced by precheck/snapshot_connection (closes #465)

Both production callers migrated (PositionClosure to precheck_connection
in Task 7; update_positions_json to snapshot_connection in Task 4).
Three tests targeting the deleted helper removed; one PRAGMA-leak
invariant kept and migrated to precheck_connection.

Closes #465 fully: the threat model is named in both surviving helpers'
docstrings; the name 'read_only' no longer claims more than the mechanism
delivers."
```

---

### Task 10: Update `CLAUDE.md` §4

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Locate the section**

Run: `grep -n "Read-only pre-validation" CLAUDE.md`
Expected: one match, around line 281.

- [ ] **Step 2: Replace §4 with two sub-sections**

Use `Edit` to replace the existing `### 4. Read-only pre-validation outside any transaction (\`read_only_connection()\`)` section (from heading through the closing paragraph including the "Used by ..." line) with:

```markdown
### 4a. Precheck reads that feed a write transaction (`precheck_connection()`)

When an operator needs to read state BEFORE deciding whether to open a write transaction (e.g., ownership check, idempotency check), use `precheck_connection()` from `db.transaction`. The contract requires the caller to extract any field the write-tx will need into an **immutable snapshot value** (see `operators.precheck.PositionSnapshot`) BEFORE the block exits — the connection MUST NOT escape.

```python
from db.transaction import precheck_connection
from operators.precheck import PositionSnapshot

with precheck_connection() as con:
    row = db_get_position_by_id(con, pos_id)
snapshot = PositionSnapshot(pos_id=row["id"], tenant_id=row["tenant_id"], ...)
# Later: open transaction() and re-validate snapshot's mutable fields.
```

The write-tx that follows MUST re-validate the snapshot's mutable fields (e.g., `tenant_id`, `status`) against a fresh re-SELECT inside `BEGIN IMMEDIATE`. Immutable fields (e.g., `entry_price`, `qty`) are trusted from the snapshot directly. See `operators/position_closure.py` for the canonical implementation.

### 4b. Terminal reads (`snapshot_connection()`)

When a read is **terminal** — its result is serialized to an output (JSON file, HTTP response, log) and NOT used to drive a subsequent mutation — use `snapshot_connection()`:

```python
from db.transaction import snapshot_connection

with snapshot_connection() as con:
    all_pos = db_get_positions(con)
```

No follow-up write-tx, no re-validation obligation. Used today by `update_positions_json` (snapshot to JSON file).

### Threat model (applies to both 4a and 4b)

Both helpers set `PRAGMA query_only = 1` on the connection. INSERT/UPDATE/DELETE raise `sqlite3.OperationalError`. **This is a cooperative latch, not a sandbox:** callers can re-enable writes via `PRAGMA query_only = 0`, `executescript` with embedded PRAGMA, or writes to `temp.*` tables. SQLite does not provide an ontologically read-only connection.

The mechanism is a **detector**, not a defense. Its value is converting bugs of "helper mistakenly mutates when contract says read-only" into LOUD errors at test time. The semantic invariant "this phase does not mutate the world" lives at the CALL SITE (extract → snapshot → terminate or write-tx), not in the primitive. Pure SQL helpers receive `con` from their caller; they never call `precheck_connection` or `snapshot_connection` themselves.

The two helpers share implementation but bear distinct call-site contracts. Mixing them (using `snapshot_connection` for a precheck that will feed a write-tx, or `precheck_connection` for a terminal read) is a documentation error that future contributors should reject in code review.
```

- [ ] **Step 3: Verify the change renders cleanly**

Run: `sed -n '/### 4a. Precheck reads/,/### Known scope gap/p' CLAUDE.md | head -50`
Expected: the new content renders; the §"Known scope gap" section that follows is unchanged.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: rewrite CLAUDE.md §4 — split into precheck/snapshot helpers + honest threat model (closes #464 #465)

§4a documents precheck_connection (feeds write-tx; caller must extract
snapshot before exit). §4b documents snapshot_connection (terminal read).
Shared threat-model section names the cooperative-latch nature honestly:
detector against helper-contract violations; not a sandbox; semantic
invariant lives at the call site."
```

---

### Task 11: Final verification

**Files:** none modified (unless smoke surfaces a fix).

- [ ] **Step 1: Grep that no `read_only_connection` survives anywhere in production or tests**

Run: `grep -rnE "read_only_connection" --include="*.py" --include="*.md" --exclude-dir=docs/superpowers/plans --exclude-dir=docs/superpowers/analysis --exclude-dir=.claude .`
Expected: only historical references in commit log / plan files (excluded). The active codebase shows nothing.

- [ ] **Step 2: Run all transaction tests**

Run: `pytest tests/db/test_transaction.py -v 2>&1 | tail -20`
Expected: 12/12 pass.

- [ ] **Step 3: Run all PositionClosure invariants**

Run: `pytest tests/operators/test_position_closure.py -v 2>&1 | tail -25`
Expected: 12/12 pass.

- [ ] **Step 4: Run precheck type tests**

Run: `pytest tests/operators/test_precheck.py -v 2>&1 | tail -10`
Expected: 4/4 pass.

- [ ] **Step 5: Run atomicity regression test**

Run: `pytest tests/api/test_check_position_stops_atomicity.py -v 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 6: Full suite**

Run: `pytest tests/ --tb=no -q -p no:cacheprovider 2>/dev/null | tail -5`
Expected: baseline (~2493) + 7 new tests (4 precheck + 2 new transaction + 1 invariant 12) = ~2500 passed. Same skip count as baseline (~22, all network).

- [ ] **Step 7: Smoke imports for all touched modules**

```bash
python -c "
import importlib
for m in ('db.transaction', 'operators.precheck', 'operators.position_closure', 'api.positions'):
    importlib.import_module(m)
print('all 4 modules import cleanly')
"
```
Expected: `all 4 modules import cleanly`.

---

### Task 12: Push + open PR + close #461 #464 #465 (REQUIRES USER CONFIRMATION)

**Files:** none modified.

This step is externally visible to `sssimon/trading-spacial`. Confirm before executing in an autonomous session.

- [ ] **Step 1: Push the branch**

Run: `git push -u origin feat/separate-semantic-mechanical-invariants-464-465-461`
Expected: branch published; URL printed.

- [ ] **Step 2: Open the PR**

Run:
```bash
gh pr create --repo sssimon/trading-spacial \
  --title "feat(db,operators): separate semantic from mechanical invariants — precheck/snapshot helpers + PositionSnapshot type (closes #461 #464 #465)" \
  --body "$(cat <<'EOF'
## Summary

Voronov's reframed Cluster C1. Closes three issues by separating what `read_only_connection` was conflating: the **semantic invariant** (\"this phase does not mutate the world\") from the **mechanical invariant** (\"this SQLite connection refuses certain opcodes while a flag is set\").

- **#464** (closes): the compound pattern \"read_only → close → transaction with re-SELECT\" is reified. The read-side returns `PrecheckResult` (a sealed union of `PrecheckNotFound` | `PrecheckAlreadyClosed` | `PrecheckOkToProceed(snapshot)`); the write-side consumes the immutable `PositionSnapshot` and re-validates its mutable fields inside `BEGIN IMMEDIATE`.

- **#465** (closes): the threat model is named honestly in both `precheck_connection` and `snapshot_connection` docstrings — cooperative latch, detector against helper-contract violations, not a sandbox. The semantic invariant lives at the call site, not in the primitive.

- **#461** (closes by construction): tenant_id re-validation in the write-tx is no longer optional defense-in-depth. The snapshot carries `tenant_id`; the write-tx's mandatory re-SELECT compares it to the fresh row. A tenant reassignment between precheck and `BEGIN IMMEDIATE` collapses to IDOR-safe NOT_FOUND. Invariant #12 (\`test_tenant_reassignment_between_precheck_and_write_rejects_close\`) is the regression proof.

## Why this PR (Voronov reframe of Serrano's triage)

Serrano's original triage recommended Cluster C1 as a doc-and-helper exercise. Voronov reframed: the problem was not docs; the problem was that \`read_only_connection\` was **two things with one name** — a semantic invariant about call-site intent and a mechanical invariant about connection state. Per Voronov:

> Mientras compartan nombre, todo lo demás es relitigación del mismo error de categoría. [...] C1 es la última oportunidad de separar la invariante semántica de la invariante mecánica antes de que el tercer call site las case por costumbre.

The split into `precheck_connection` (feeds a write-tx, caller must produce a snapshot before exit) and `snapshot_connection` (terminal read, no follow-up obligation) names the two contracts where they actually live: at the call site, not in the primitive.

## Resolves

- **#461** — re-validate tenant_id in write-tx — closed BY CONSTRUCTION (snapshot pattern makes it mandatory)
- **#464** — name the compound pattern — closed via `PrecheckResult` sealed union + `PositionSnapshot` immutable type
- **#465** — name the threat model — closed via honest docstrings in both new helpers + dedicated \"Threat model\" section in CLAUDE.md §4

## Architectural notes

- **\`db/transaction.py\`** now exposes 3 primitives: \`transaction()\` (BEGIN IMMEDIATE), \`precheck_connection()\` (PRAGMA query_only, expects snapshot), \`snapshot_connection()\` (PRAGMA query_only, terminal). \`read_only_connection\` deleted.
- **\`operators/precheck.py\`** (new) defines \`PositionSnapshot\` (frozen dataclass) and \`PrecheckResult\` (sealed union of 3 variants). Adding a field to \`PositionSnapshot\` = the write-tx must re-validate it. Removing a field = the precheck no longer commits to it.
- **\`operators/position_closure.py\`**: \`__enter__\` returns a \`PrecheckResult\`; \`execute()\` unwraps it and re-validates the snapshot's mutable fields. Immutable fields (direction, entry_price, qty) trusted from snapshot directly.
- **\`api/positions.py::update_positions_json\`**: migrated to \`snapshot_connection\` (terminal read).

## Test plan

- [x] 12 PositionClosure invariants (was 11; added invariant #12 for #461 closure)
- [x] 4 new precheck type tests (immutability, sealed union, IDOR-safe NotFound)
- [x] 12 transaction tests (was 12; deleted 3 read_only tests, added 2 precheck/snapshot tests + 1 PRAGMA-leak test for precheck_connection)
- [x] Atomicity regression test still passes
- [x] Full suite: baseline + 7 net new tests
- [ ] Manual smoke in prod after merge: confirm PositionClosure still closes positions correctly; confirm /health/dashboard and update_positions_json continue to work

## What this PR does NOT do

Per the strict Voronov ordering, this PR stays scoped to the semantic/mechanical split. It does NOT:
- Make \`read_only_connection\` survive as a deprecated alias (deleted clean)
- Introduce a \`validated_transaction(precheck_fn)\` helper (the snapshot itself is the structural hand-off; an over-helper would obscure it)
- Address the other open issues from PR #463 review (#453, #454, #455, #457, #458, #459, #460) — those are separate clusters per Serrano's triage

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
Expected: PR URL printed.

- [ ] **Step 3: Close #461, #464, #465 with cross-link**

Run:
```bash
NEW_PR=$(gh pr view --json number --jq .number)
gh issue comment 461 --repo sssimon/trading-spacial --body "Closed by #$NEW_PR by construction. The new precheck/write-tx hand-off pattern (PositionSnapshot + PrecheckResult sealed union) makes tenant_id re-validation in the write-tx mechanical, not optional. Invariant #12 (\`test_tenant_reassignment_between_precheck_and_write_rejects_close\`) is the regression proof."
gh issue close 461 --repo sssimon/trading-spacial

gh issue comment 464 --repo sssimon/trading-spacial --body "Closed by #$NEW_PR. The compound \"read → close → write-tx with re-SELECT\" pattern is reified as the \`PrecheckResult\` sealed union (NotFound | AlreadyClosed | OkToProceed(snapshot)) and the \`PositionSnapshot\` frozen dataclass. The write-tx is obligated by type to re-validate the snapshot's mutable fields."
gh issue close 464 --repo sssimon/trading-spacial

gh issue comment 465 --repo sssimon/trading-spacial --body "Closed by #$NEW_PR. \`read_only_connection\` deleted; replaced by \`precheck_connection\` and \`snapshot_connection\` with distinct call-site contracts. Both docstrings + CLAUDE.md §4 name the threat model honestly: cooperative latch, detector against helper-contract violations, not a sandbox. The semantic invariant lives at the call site."
gh issue close 465 --repo sssimon/trading-spacial
```

- [ ] **Step 4: Done**

The plan is fully executed when this step completes.

---

## Self-Review

**Spec coverage:**
- #461 (re-validate tenant_id in write-tx): closed by construction via Task 7's `execute()` mandatory snapshot re-validation; locked by Task 8's regression test.
- #464 (name the compound pattern): closed via Tasks 5-7 introducing `PrecheckResult` + `PositionSnapshot` + the operator migration.
- #465 (name the threat model): closed via Task 2's interim docstring rewrite, Task 3's new helpers carrying the honest docstrings, and Task 10's CLAUDE.md §4 rewrite.
- Voronov's strict order (docstring → split helpers → snapshot type → compound pattern → tenant re-validation): Tasks 2 → 3 → 5+6 → 7 → 8.

**Placeholder scan:** None of the disallowed patterns present. All code blocks complete.

**Type consistency:**
- `PositionSnapshot` fields enumerated identically in: Task 5 test, Task 6 implementation, Task 7 `_run_precheck` constructor call, Task 7 `_snapshot_to_dict` static method.
- `PrecheckResult` variants (NotFound, AlreadyClosed, OkToProceed) used identically across Tasks 5, 6, 7.
- `precheck_connection` / `snapshot_connection` signatures identical in Task 3 definition and Tasks 4, 7 usage.

**Caveats:**
- Task 7 Step 7 may surface tests in `tests/operators/test_position_closure.py` that patched `db.transaction.read_only_connection` or `operators.position_closure.read_only_connection`. The implementer is told to update those patches to target `precheck_connection` instead. This is mechanical but check carefully.
- The plan deletes `read_only_connection` in Task 9 only after Task 7 migrated `PositionClosure` and Task 4 migrated `update_positions_json`. If Task 7's tests left any patch targeting the deleted name, Task 9's smoke imports will surface it before commit.
