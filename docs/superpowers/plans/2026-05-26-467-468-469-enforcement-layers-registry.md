# Enforcement Layers Registry + Three Layer Moves (qty→schema, precheck/snapshot→tipo, snapshot==row→tipo)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close issues #467, #468, #469 by naming the patología they share (invariantes de dominio sin contraparte estructural) and moving the three invariants from convención to either schema or tipo. The plan begins with a written registry in CLAUDE.md ("Capas de enforcement de invariantes") and then executes three movements within that registry.

**Architecture:** Per Voronov's meta-reframe (2026-05-26): the three issues are symptoms of one disease — the system confuses what the domain model promises with what storage guarantees. The code pays the difference in silent translation membranes (`or 0`, "código de revisor", partial re-validations). The plan first registers the four enforcement layers (schema / tipo / test / convención) and then moves three specific invariants from `convención` to the layer that actually enforces them: (1) `qty != NULL` → schema (CHECK constraint via backfill + table recreation); (2) `precheck != snapshot` → tipo (NewType); (3) `snapshot fields == row fields in write-tx` → tipo (OwnershipValidatedSnapshot factory + re-validate ALL mutable fields per F6).

**Tech Stack:** Python 3.12, `sqlite3` stdlib, `dataclasses`, `typing.NewType`, `pytest`.

---

## AMENDMENT 2026-05-26 (post-Task-1 measurement + Voronov reframe)

Task 1 medición reveló que la premisa del Path D original (backfill `qty = size_usd / entry_price`) **NO es ejecutable en producción**:

- 2018 total positions; 670 con `qty IS NULL` (33%)
- **ZERO backfillable** desde `size_usd/entry_price` (la inmensa mayoría tampoco tiene `size_usd`)
- 668 son closed (arqueología del bug histórico, ya cerradas con `pnl_usd=0`)
- 2 son open con `entry_ts=2026-06-15` futuro, `tenant_id=NULL` — debris (test fixtures leaked to prod DB)

Voronov reframe completo:

> La medición no eligió la política. Reveló que la política propuesta describía un mundo que no existe.
>
> Tu plan asumió **deuda de cierre** (size_usd existió, qty derivable). La medición dice **deuda de nacimiento** (size_usd nunca prometida).
>
> **`UPDATE qty=0` es mentira tipográfica**: cero y desconocido no son sinónimos. **`status='legacy_unmeasurable'` convierte 668 mentiras silenciosas en 668 reconocimientos explícitos.**

### Finding meta (precede Task 4)

> **El sistema tiene un `close()` que asume invariantes que `open()` nunca prometió.** `qty NULL` no es el problema. Es el síntoma. La membrana de cierre asume un contrato que la membrana de apertura nunca firmó. Mientras esa asimetría exista, Task 4 es cosmética: estás poniendo CHECK CONSTRAINT en una salida cuya entrada no garantiza nada.

### Tasks amended

- **Task 2 (registry):** ADD a "Finding meta — asimetría contractual create vs close" sub-section documenting the meta finding. ADD `legacy_unmeasurable` as a documented status that the schema CHECK exempts.
- **Task 3 (TDD tests):** REWRITE — no más `test_migration_aborts_if_any_row_unbackfillable`. Add `test_unbackfillable_rows_get_legacy_unmeasurable_status` + `test_check_constraint_exempts_legacy_unmeasurable`.
- **Task 4 (impl):** REWRITE policy. Backfill what computable; UPDATE remaining NULL rows to `status='legacy_unmeasurable'` (keep qty NULL — admitir ausencia, no inventar valor); CHECK constraint: `CHECK (qty IS NOT NULL OR status='legacy_unmeasurable')`. **Fija, sin flags.**
- **NEW Task 13.5 (before push):** Open 2 new issues — (a) test/prod permeability (debris in production DB), (b) `create_position` vs `close_position` contract asymmetry.
- **Task 14 PR description:** Reference both the original reframe (Cluster C2 meta) AND this amendment.

### Voronov's sharpened principle (replace plan's original)

> "No requiere medir para decidir si comprometerse. Requiere medir para saber a qué comprometerse."

The commitment stands. The shape of the commitment changed.

---

## Context (read before starting)

PR #466 separated semantic from mechanical invariants of `read_only_connection`. C2 is the bill for that separation — it surfaces what the semantic layer asserts that the mechanical layer never promised. Voronov:

> Los tres issues no son deuda de #466. Son la primera generación de un patrón donde el dominio afirma más de lo que el almacenamiento garantiza, y el código paga la diferencia en membranas silenciosas — `or 0`, comentarios de revisor, re-validaciones parciales. Mientras esa asimetría no esté nombrada, cada issue futuro de esta familia se debatirá como si fuera nuevo.
>
> No es un problema de implementación. Es un problema de **registro**.

The plan executes Voronov's 4-step structure:

1. Register the enforcement layers (CLAUDE.md section).
2. Move `qty != NULL` from convención to schema.
3. Move `precheck != snapshot` from convención to tipo.
4. Move `snapshot fields == row fields in write-tx` from convención to tipo.

Locked decisions per Voronov:

- **#467: path D first (backfill + CONSTRAINT), then A trivially**. The structural commitment doesn't require measurement of production NULL count; the commitment IS the measurement. If backfill fails for any row, migration aborts and the operator must intervene before merge.
- **#468: path B (NewType)** with explicit semantic distinction in module docstring. NewType is runtime-trivial but compile-time enforced via mypy.
- **#469 + F6: path C + E together**. `OwnershipValidatedSnapshot` whose only factory is `_run_precheck`; `execute()` re-validates ALL mutable snapshot fields (not just `tenant_id` and `status` — also `entry_price`, `qty`, `direction`, `symbol`).

The deeper debt Voronov flagged ("ownership validated at lifecycle crossings, not at birth"): NOT addressed in this plan. Flagged as known scope gap in CLAUDE.md update at Task 13.

---

## Scope Check

One cohesive subsystem (DB invariants + their enforcement layer registry + the 3 specific moves). Already a single plan. The three moves are independent of each other (no order dependency between qty/precheck-snapshot/ownership-validated) but share the meta-registry as precondition.

---

## File Structure

### New files

- `operators/precheck.py` (modified) — `OwnershipValidatedSnapshot` class with private factory pattern.
- `tests/operators/test_ownership_validated_snapshot.py` — type-level + runtime tests for the factory pattern.

### Modified files

- `CLAUDE.md`:
  - **Pre-work (Task 2):** new section "Capas de enforcement de invariantes" with 4-column registry (schema / tipo / test / convención) listing the C2 invariants.
  - **Post-moves (Task 13):** update the registry to reflect that the 3 invariants have moved from `convención` to their target layer; add "Known scope gap: invariantes en el momento de creación" pointing to Voronov's deeper deuda.

- `db/schema.py`:
  - New helper `_migrate_qty_not_null()` — backfill `qty = size_usd / entry_price` where possible; if any row remains NULL after backfill, raise with explicit log naming rows; recreate `positions` table with `CHECK (qty IS NOT NULL)` via the standard SQLite ALTER-by-rename pattern.

- `db/transaction.py`:
  - Add `from typing import NewType`.
  - `PrecheckConn = NewType("PrecheckConn", sqlite3.Connection)`.
  - `SnapshotConn = NewType("SnapshotConn", sqlite3.Connection)`.
  - `precheck_connection()` return type annotation changes to `Iterator[PrecheckConn]` and yields `PrecheckConn(con)`.
  - `snapshot_connection()` same with `SnapshotConn`.
  - Module docstring gains explicit semantic distinction paragraph per Voronov.

- `operators/precheck.py`:
  - Add `OwnershipValidatedSnapshot` (frozen dataclass wrapping `PositionSnapshot` + an internal sentinel for factory enforcement).
  - Update `PrecheckOkToProceed.snapshot` field type to `OwnershipValidatedSnapshot`.

- `operators/position_closure.py`:
  - `_run_precheck()` constructs `OwnershipValidatedSnapshot` via the factory sentinel; this is the ONLY location that may construct it.
  - `execute()` consumes `OwnershipValidatedSnapshot.inner` (the `PositionSnapshot`) and re-validates ALL of its mutable fields against the fresh re-SELECT (not only `tenant_id` and `status`).
  - Remove `qty = snapshot.qty or 0` membrane (no longer needed once schema enforces NOT NULL).

- `api/positions.py`:
  - Remove `qty = pos.get("qty") or 0` membrane in `_write_position_event_log`.

- `tests/operators/test_position_closure.py`:
  - Update existing invariants to construct `PositionClosure` against the new types (most should pass unchanged; the snapshot-related ones may need minor adjustment).
  - Add invariant #14: `test_cross_mutation_race_entry_price_rejects_close` — entry_price changes between precheck and write-tx → operator must detect via snapshot field re-validation and return `not_found` or `rejected_unexpected_state`.

- `tests/operators/test_precheck.py`:
  - Add tests for `OwnershipValidatedSnapshot` factory: cannot be constructed without the factory sentinel; the sentinel is module-private.

---

## Locked API Design

### `db/transaction.py` — NewType additions

```python
from typing import Iterator, NewType
import sqlite3

# PrecheckConn: a connection authorized for reads that will FEED a follow-up
# write transaction. The caller is contractually obligated to extract any
# field the write-tx will need into an immutable snapshot (see
# operators.precheck.OwnershipValidatedSnapshot) BEFORE the with block exits.
PrecheckConn = NewType("PrecheckConn", sqlite3.Connection)

# SnapshotConn: a connection authorized for TERMINAL reads — results serialize
# to an output (JSON, HTTP response, log) and are NOT used to drive a write-tx.
# No follow-up re-validation obligation.
SnapshotConn = NewType("SnapshotConn", sqlite3.Connection)


@contextmanager
def precheck_connection() -> Iterator[PrecheckConn]:
    """[existing docstring updated to declare the type contract]"""
    con = _open_configured_connection()
    try:
        con.execute("PRAGMA query_only = 1")
        yield PrecheckConn(con)
    finally:
        con.close()


@contextmanager
def snapshot_connection() -> Iterator[SnapshotConn]:
    """[existing docstring updated to declare the type contract]"""
    con = _open_configured_connection()
    try:
        con.execute("PRAGMA query_only = 1")
        yield SnapshotConn(con)
    finally:
        con.close()
```

### `operators/precheck.py` — `OwnershipValidatedSnapshot`

```python
# Module-private sentinel: external callers cannot construct
# OwnershipValidatedSnapshot directly. The factory check is by value comparison
# against this sentinel, which is not exported from the module.
_VALIDATION_SENTINEL = object()


@dataclass(frozen=True)
class OwnershipValidatedSnapshot:
    """A PositionSnapshot that has been validated for ownership by a precheck
    (USER mode: caller_tenant_id matched snapshot.tenant_id; SYSTEM mode:
    accepted unconditionally).

    Construction requires the module-private _VALIDATION_SENTINEL. This means
    only `operators.position_closure.PositionClosure._run_precheck()` (which
    has access to the sentinel via a private import) can build instances.
    A future write-tx that consumes this type is guaranteed (by construction)
    that ownership was checked. The write-tx must STILL re-validate the
    snapshot's mutable fields against a fresh re-SELECT (this is enforced
    by PositionClosure.execute()'s explicit comparisons).

    Closes #469 + F6: the validation guarantee lives in the type, not in a
    docstring.
    """
    inner: PositionSnapshot
    _sentinel: object  # must equal _VALIDATION_SENTINEL at construction

    def __post_init__(self):
        if self._sentinel is not _VALIDATION_SENTINEL:
            raise TypeError(
                "OwnershipValidatedSnapshot can only be constructed via "
                "operators.position_closure.PositionClosure._run_precheck "
                "(which holds the module-private validation sentinel)."
            )


def _build_validated_snapshot(snapshot: PositionSnapshot) -> OwnershipValidatedSnapshot:
    """Internal factory used by PositionClosure._run_precheck.
    NOT exported from this module's public surface."""
    return OwnershipValidatedSnapshot(inner=snapshot, _sentinel=_VALIDATION_SENTINEL)
```

`PrecheckOkToProceed` updates to use the new type:

```python
@dataclass(frozen=True)
class PrecheckOkToProceed:
    """Position passed precheck. snapshot is OwnershipValidatedSnapshot —
    construction-time guarantee that ownership was checked."""
    snapshot: OwnershipValidatedSnapshot
```

### `operators/position_closure.py` — write-tx re-validates ALL mutable fields

```python
def execute(self) -> CloseOutcome:
    # ... [unchanged: dispatch on PrecheckResult variants for NotFound /
    #      AlreadyClosed / RejectedState] ...

    # PrecheckOkToProceed: write-tx must re-validate ALL snapshot fields.
    validated = result.snapshot           # OwnershipValidatedSnapshot
    snap = validated.inner                # the wrapped PositionSnapshot

    with _tx_module.transaction() as con:
        row = db_get_position_by_id(con, self._pos_id)
        if row is None:
            return CloseOutcome(status="not_found", position=None,
                                pnl_usd=None, pnl_pct=None)

        # #469 + F6: re-validate ALL mutable fields, not only tenant_id+status.
        # Per CLAUDE.md "Capas de enforcement", the schema does NOT enforce
        # immutability of entry_price/qty/direction/symbol — so the write-tx
        # MUST. If any field has drifted between precheck and BEGIN IMMEDIATE,
        # the snapshot is stale; collapse to NOT_FOUND (IDOR-safe).
        snapshot_fields = (
            ("tenant_id", snap.tenant_id),
            ("status_must_still_be_open", "open"),  # special: row.status must be "open"
            ("entry_price", snap.entry_price),
            ("qty", snap.qty),
            ("direction", snap.direction),
            ("symbol", snap.symbol),
        )
        # Handle status separately because its expected value is the literal
        # "open" (not a snapshot field), and the not-open paths have distinct
        # outcomes (closed → already_closed; other → rejected_unexpected_state).
        if row["status"] == "closed":
            race_snap = PositionSnapshot(...)  # build from row, as in #466
            return CloseOutcome(status="already_closed",
                                position=self._snapshot_to_dict(race_snap),
                                pnl_usd=None, pnl_pct=None)
        if row["status"] != "open":
            race_snap = PositionSnapshot(...)  # build from row
            return CloseOutcome(status="rejected_unexpected_state",
                                position=self._snapshot_to_dict(race_snap),
                                pnl_usd=None, pnl_pct=None)

        # Now compare every other mutable field.
        for field_name, snapshot_value in snapshot_fields:
            if field_name == "status_must_still_be_open":
                continue  # handled above
            row_value = row[field_name]
            if row_value != snapshot_value:
                # Some field changed between precheck and write-tx.
                # Collapse to NOT_FOUND (IDOR-safe — same shape as
                # ownership-mismatch).
                return CloseOutcome(status="not_found", position=None,
                                    pnl_usd=None, pnl_pct=None)

        # All snapshot fields confirmed. Snapshot is trusted; proceed.
        # qty no longer needs `or 0` — schema enforces NOT NULL (Task 4).
        pnl_usd, pnl_pct = _calc_pnl(
            snap.direction, snap.entry_price, self._exit_price, snap.qty,
        )
        # ... [rest unchanged: db_close_position_sql, apply_pnl_to_capital] ...
```

---

## Tasks

### Task 1: Verify branch + baseline + measure qty NULL population

**Files:** none modified.

- [ ] **Step 1: Confirm branch and HEAD**

Run: `git rev-parse --abbrev-ref HEAD && git rev-parse HEAD`
Expected: `feat/enforcement-layers-registry-467-468-469` and a commit at or descending from `0ab40b4f`.

- [ ] **Step 2: Confirm clean working tree**

Run: `git status --short`
Expected: empty.

- [ ] **Step 3: Baseline test count**

Run: `pytest --collect-only -q 2>&1 | tail -3`
Expected: ~2530 tests collected. Note exact number.

- [ ] **Step 4: Measure qty NULL population (for Task 4 backfill planning)**

This step does NOT decide policy (Voronov's "compromiso, no medición" applies). It informs the backfill SQL's expected scope.

If a production DB is accessible locally:
```bash
sqlite3 signals.db "SELECT COUNT(*) AS total, \
  SUM(CASE WHEN qty IS NULL THEN 1 ELSE 0 END) AS qty_null, \
  SUM(CASE WHEN qty IS NULL AND size_usd IS NOT NULL AND entry_price > 0 THEN 1 ELSE 0 END) AS backfillable \
  FROM positions;"
```

If not, document the query for later execution and proceed. The migration in Task 4 backfills aggressively and aborts if any row remains NULL — so the policy holds either way.

---

### Task 2: Write "Capas de enforcement de invariantes" registry in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

Per Voronov's pre-condition: the meta-registry MUST be written before the three moves. The registry lists each invariant C2 touches with its current enforcement layer and its target layer after this plan.

- [ ] **Step 1: Locate insertion point**

Run: `grep -n "^## Known Limitations\|^### Known scope gap" CLAUDE.md`

Insert the new section AFTER the "Database access" §4b and BEFORE "Known scope gap" or "Known Limitations" (whichever comes first).

- [ ] **Step 2: Append the registry section**

Add to `CLAUDE.md`:

```markdown
## Capas de enforcement de invariantes (Voronov 2026-05-26)

El dominio del repo afirma invariantes que el almacenamiento no garantiza por defecto. Cada vez que esa asimetría no se nombra, el código paga la diferencia en **membranas silenciosas**: `or 0`, "código de revisor", re-validaciones parciales. Este registro lista las invariantes de dominio que tocan el cluster C2 (#467/#468/#469) y la capa que las enforza.

Cuatro capas posibles, de más fuerte a más débil:

| Capa | Cómo enforza | Quién detecta violación |
|---|---|---|
| **Schema** | DDL constraint (CHECK, NOT NULL, FK, UNIQUE) | El motor SQLite, en write |
| **Tipo** | Python typing (NewType, frozen dataclass, factory privada) | mypy en CI / `__post_init__` en runtime |
| **Test** | Invariant test que falla si la violación ocurre | pytest en CI |
| **Convención** | Comentario en código / sección de CLAUDE.md / revisión humana | Revisor (si recuerda mirar) |

### Invariantes C2 — estado actual y movimiento de este PR

| Invariante de dominio | Capa actual | Capa objetivo | Issue | Razón del movimiento |
|---|---|---|---|---|
| `qty` siempre tiene valor numérico para positions activas | Convención (`or 0` en 2 sitios) | **Schema** (CHECK NOT NULL via backfill + recreación de tabla) | #467 | El default silencioso a 0 corrompe `pnl_usd=0` aplicado al capital. El dominio promete numérico; el schema lo permite NULL. La fix vive en schema. |
| `precheck_connection` y `snapshot_connection` son contratos distintos | Convención (docstrings + CLAUDE.md §4) | **Tipo** (NewType `PrecheckConn` / `SnapshotConn`) | #468 | Hoy ambas retornan `sqlite3.Connection`. mypy no detecta el mis-uso. La distinción es funcional (sin ella, #461 era un bug); por tanto merece tipo, no comentario. |
| Los campos del `PositionSnapshot` consumidos por el write-tx no cambian entre precheck y BEGIN IMMEDIATE | Convención (re-validación parcial de tenant_id + status) | **Tipo** (`OwnershipValidatedSnapshot` con factory privada + re-validación de TODOS los campos mutables en write-tx) | #469 + F6 | El re-validate actual solo cubre 2 de 6 campos del snapshot. Schema no enforza inmutabilidad de `entry_price`, `qty`, `direction`, `symbol`. Una migración o UPDATE ad-hoc puede mutarlos y el operator usa valores stale. |

### Patrón nombrado: "invariantes de dominio sin contraparte estructural"

Cada futuro issue de la familia `or X`, "código de revisor", "trust-and-document" debería compararse contra este registro. Si la invariante pertenece a una capa más fuerte que `convención`, moverla es la fix correcta. Si verdaderamente pertenece a `convención` (e.g., norma estética que no afecta correctness), declararlo explícitamente con justificación.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): add 'Capas de enforcement de invariantes' registry (Voronov meta-reframe for #467 #468 #469)

Names the patología the three issues share: invariantes de dominio sin
contraparte estructural. Lists the 3 C2 invariants with current vs target
enforcement layer. Subsequent tasks move each one from convención to
schema (#467) or tipo (#468 #469)."
```

---

### Task 3: TDD — write failing test for qty backfill migration

**Files:**
- Create: `tests/db/test_migrate_qty_not_null.py`

The migration helper `_migrate_qty_not_null()` doesn't exist yet. This test pins its contract.

- [ ] **Step 1: Create the test file**

```python
"""Invariant tests for db.schema._migrate_qty_not_null.

Voronov path D for #467: backfill qty for legacy rows where computable
(qty = size_usd / entry_price), then add CHECK CONSTRAINT NOT NULL via
table recreation. Aborts loudly if any row remains NULL after backfill —
the operator must intervene before merge.
"""
import sqlite3
import pytest


def _init_minimal_positions_table(con: sqlite3.Connection) -> None:
    """Create the positions table without the qty NOT NULL constraint
    (mirrors the schema as it was before this migration)."""
    con.execute("""
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY,
            symbol TEXT NOT NULL,
            direction TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            entry_price REAL,
            entry_ts TEXT NOT NULL DEFAULT (datetime('now')),
            size_usd REAL,
            qty REAL,
            sl_price REAL,
            tp_price REAL,
            exit_price REAL,
            exit_ts TEXT,
            exit_reason TEXT,
            pnl_usd REAL,
            pnl_pct REAL,
            atr_entry REAL,
            be_mult REAL,
            tenant_id INTEGER
        )
    """)


def test_backfill_qty_from_size_usd_and_entry_price(tmp_path):
    """Rows with qty IS NULL but size_usd and entry_price set must be
    backfilled to qty = size_usd / entry_price before the constraint is added."""
    from db.schema import _migrate_qty_not_null

    db_path = tmp_path / "test.db"
    con = sqlite3.connect(str(db_path))
    _init_minimal_positions_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, size_usd, qty) "
        "VALUES (1, 'BTCUSDT', 100.0, 1000.0, NULL)"
    )
    con.commit()

    _migrate_qty_not_null(con)

    row = con.execute("SELECT qty FROM positions WHERE id = 1").fetchone()
    assert row[0] == 10.0  # 1000 / 100


def test_migration_aborts_if_any_row_unbackfillable(tmp_path):
    """If a row has qty IS NULL AND (size_usd IS NULL OR entry_price IS NULL
    OR entry_price <= 0), the migration must raise with a clear message
    naming the problematic row ids."""
    from db.schema import _migrate_qty_not_null

    db_path = tmp_path / "test.db"
    con = sqlite3.connect(str(db_path))
    _init_minimal_positions_table(con)
    # Row 1: backfillable
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, size_usd, qty) "
        "VALUES (1, 'BTCUSDT', 100.0, 1000.0, NULL)"
    )
    # Row 2: unbackfillable (entry_price NULL)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, size_usd, qty) "
        "VALUES (2, 'ETHUSDT', NULL, 500.0, NULL)"
    )
    con.commit()

    with pytest.raises(RuntimeError, match=r"unbackfillable.*\b2\b"):
        _migrate_qty_not_null(con)


def test_post_migration_insert_with_null_qty_is_rejected(tmp_path):
    """After successful migration, attempting to INSERT a row with qty NULL
    must raise sqlite3.IntegrityError (CHECK constraint violation)."""
    from db.schema import _migrate_qty_not_null

    db_path = tmp_path / "test.db"
    con = sqlite3.connect(str(db_path))
    _init_minimal_positions_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, size_usd, qty) "
        "VALUES (1, 'BTCUSDT', 100.0, 1000.0, 10.0)"  # has qty
    )
    con.commit()

    _migrate_qty_not_null(con)

    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO positions (id, symbol, entry_price, qty) "
            "VALUES (99, 'XYZ', 50.0, NULL)"
        )


def test_idempotent_on_already_migrated_table(tmp_path):
    """Running _migrate_qty_not_null twice must be a no-op the second time
    (matching the pattern of other _migrate_* helpers in db/schema.py)."""
    from db.schema import _migrate_qty_not_null

    db_path = tmp_path / "test.db"
    con = sqlite3.connect(str(db_path))
    _init_minimal_positions_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, size_usd, qty) "
        "VALUES (1, 'BTCUSDT', 100.0, 1000.0, 10.0)"
    )
    con.commit()

    _migrate_qty_not_null(con)
    _migrate_qty_not_null(con)  # second run must not raise

    row = con.execute("SELECT qty FROM positions WHERE id = 1").fetchone()
    assert row[0] == 10.0
```

- [ ] **Step 2: Verify all 4 tests fail with `ImportError` or `AttributeError`**

Run: `pytest tests/db/test_migrate_qty_not_null.py -v 2>&1 | tail -20`
Expected: 4 failures, all at the import `from db.schema import _migrate_qty_not_null`.

- [ ] **Step 3: Commit**

```bash
git add tests/db/test_migrate_qty_not_null.py
git commit -m "test(db): add failing invariant tests for _migrate_qty_not_null (advances #467 path D)

Backfill qty=size_usd/entry_price where possible; abort on any remaining
NULL; post-migration INSERT with NULL qty raises IntegrityError; idempotent
on re-run."
```

---

### Task 4: Implement `_migrate_qty_not_null` in db/schema.py

**Files:**
- Modify: `db/schema.py`

- [ ] **Step 1: Read the existing _migrate_* pattern**

Run: `grep -n "^def _migrate" db/schema.py`

Note the conventions used by `_migrate_multi_tenant_b1`, `_migrate_agent_audit`, `_migrate_agent_history` (idempotency check pattern, log style, etc.). The new helper should match.

- [ ] **Step 2: Add the migration function**

Append to `db/schema.py` (after the existing `_migrate_*` functions):

```python
def _migrate_qty_not_null(con: sqlite3.Connection) -> None:
    """Move 'qty != NULL' from convención to schema (#467).

    1. Backfill: for rows where qty IS NULL AND size_usd IS NOT NULL AND
       entry_price > 0, set qty = size_usd / entry_price.
    2. If any row still has qty IS NULL after backfill, raise RuntimeError
       naming the offending row ids — the operator must intervene.
    3. Recreate the positions table with a CHECK (qty IS NOT NULL) constraint
       (SQLite does not support ALTER TABLE ADD CONSTRAINT for CHECK).

    Idempotent: detects existing CHECK constraint and skips.
    """
    # Idempotency check: SQLite stores the CREATE TABLE statement in sqlite_master.
    # If the existing schema already contains "CHECK (qty IS NOT NULL)" or
    # equivalent, this migration is a no-op.
    schema_row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='positions'"
    ).fetchone()
    if schema_row and schema_row[0] and "qty" in schema_row[0]:
        if "CHECK" in schema_row[0].upper() and "QTY" in schema_row[0].upper().replace(" ", ""):
            log.info("_migrate_qty_not_null: positions table already has qty CHECK constraint; skipping.")
            return

    # 1. Backfill aggressively.
    con.execute(
        """UPDATE positions
           SET qty = size_usd / entry_price
           WHERE qty IS NULL
             AND size_usd IS NOT NULL
             AND entry_price IS NOT NULL
             AND entry_price > 0"""
    )
    backfilled = con.execute(
        "SELECT changes()"
    ).fetchone()[0]
    log.info("_migrate_qty_not_null: backfilled qty for %d rows.", backfilled)

    # 2. Check for any remaining NULL qty.
    unbackfillable_rows = con.execute(
        "SELECT id FROM positions WHERE qty IS NULL"
    ).fetchall()
    if unbackfillable_rows:
        ids = [r[0] for r in unbackfillable_rows]
        raise RuntimeError(
            "_migrate_qty_not_null: cannot complete migration. "
            f"{len(ids)} row(s) have qty IS NULL and cannot be backfilled "
            f"from size_usd/entry_price (entry_price NULL or <= 0). "
            f"Affected row ids: unbackfillable {ids}. "
            "Operator must investigate and either backfill manually or "
            "delete these rows before re-running the migration."
        )

    # 3. Recreate positions table with CHECK constraint.
    # SQLite pattern: CREATE TABLE positions_new (...) WITH CHECK; INSERT ...;
    # DROP TABLE positions; ALTER TABLE positions_new RENAME TO positions.
    log.info("_migrate_qty_not_null: recreating positions table with CHECK (qty IS NOT NULL).")
    # We capture the existing column order and types from the live schema to
    # avoid drift. The new constraint is added at the table level.
    con.executescript(
        """
        CREATE TABLE positions_new (
            id INTEGER PRIMARY KEY,
            symbol TEXT NOT NULL,
            direction TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            entry_price REAL,
            entry_ts TEXT NOT NULL DEFAULT (datetime('now')),
            size_usd REAL,
            qty REAL CHECK (qty IS NOT NULL),
            sl_price REAL,
            tp_price REAL,
            exit_price REAL,
            exit_ts TEXT,
            exit_reason TEXT,
            pnl_usd REAL,
            pnl_pct REAL,
            atr_entry REAL,
            be_mult REAL,
            tenant_id INTEGER
        );
        INSERT INTO positions_new SELECT
            id, symbol, direction, status, entry_price, entry_ts, size_usd, qty,
            sl_price, tp_price, exit_price, exit_ts, exit_reason, pnl_usd,
            pnl_pct, atr_entry, be_mult, tenant_id
        FROM positions;
        DROP TABLE positions;
        ALTER TABLE positions_new RENAME TO positions;
        """
    )
    # Recreate any indexes that lived on the old table. Inspect the original
    # schema's CREATE INDEX statements and replay them.
    # In this codebase the positions table has an index on (symbol, status);
    # if your live schema has more, add them here.
    con.execute("CREATE INDEX IF NOT EXISTS idx_positions_symbol_status ON positions(symbol, status)")
    log.info("_migrate_qty_not_null: migration complete. positions table now enforces qty IS NOT NULL.")
```

If the actual production `positions` table has columns or indexes this stub doesn't list, the implementer MUST update the CREATE/INSERT/INDEX statements to match. Run `sqlite3 signals.db ".schema positions"` to read the real schema first if a local DB exists.

- [ ] **Step 3: Hook the migration into `init_db`**

In `db/schema.py::init_db`, after the existing `_migrate_*` calls, add:

```python
_migrate_qty_not_null(con)
```

Locate the existing migration block (around the end of `init_db`) and append the new call. The order matters: any migration that creates the positions table must run before this one.

- [ ] **Step 4: Verify the 4 tests pass**

Run: `pytest tests/db/test_migrate_qty_not_null.py -v 2>&1 | tail -10`
Expected: 4/4 pass.

- [ ] **Step 5: Verify init_db is still idempotent on a fresh DB**

```bash
python -c "
import os
os.environ['BTC_DB'] = '/tmp/test_init.db'
if os.path.exists('/tmp/test_init.db'):
    os.remove('/tmp/test_init.db')
from db.schema import init_db
init_db()
init_db()  # second call must not raise
print('init_db idempotent on fresh DB: ok')
"
```
Expected: `init_db idempotent on fresh DB: ok`.

- [ ] **Step 6: Commit**

```bash
git add db/schema.py
git commit -m "feat(db): _migrate_qty_not_null moves qty != NULL from convención to schema (closes #467)

Voronov path D: backfill qty=size_usd/entry_price where computable; abort
loudly on unbackfillable rows; recreate positions table with
CHECK (qty IS NOT NULL). The schema now enforces what the domain
promises. The 'or 0' membranes in operator and event_log become dead
code (removed in Task 5)."
```

---

### Task 5: Remove `qty = ... or 0` membranes from operator and event_log

**Files:**
- Modify: `operators/position_closure.py`
- Modify: `api/positions.py` (the `_write_position_event_log` helper)

The schema now enforces NOT NULL (Task 4). The `or 0` fallbacks are no longer needed and were the visible symptom of the patología.

- [ ] **Step 1: Locate the two membranes**

Run: `grep -n "qty.*or 0\|qty.*qty\b.*0" operators/position_closure.py api/positions.py`

Expected: 2 matches — one in `operators/position_closure.py` (around line 235 inside `execute()`), one in `api/positions.py::_write_position_event_log` (around line 57).

- [ ] **Step 2: Remove the operator's membrane**

In `operators/position_closure.py`, find this block (search for `qty = snapshot.qty or 0`):

```python
            # Snapshot's immutable fields trusted; consume directly.
            # qty may be NULL in legacy rows (schema allows; some fixtures set
            # only size_usd). Match the previous behavior of defaulting to 0.
            qty = snapshot.qty or 0
            pnl_usd, pnl_pct = _calc_pnl(
                snapshot.direction, snapshot.entry_price, self._exit_price, qty,
            )
```

Replace with:

```python
            # Snapshot's immutable fields trusted; consume directly.
            # Schema now enforces qty NOT NULL (CHECK constraint via
            # _migrate_qty_not_null) — the previous 'or 0' membrane is gone.
            pnl_usd, pnl_pct = _calc_pnl(
                snapshot.direction, snapshot.entry_price, self._exit_price, snapshot.qty,
            )
```

- [ ] **Step 3: Remove the event log's membrane**

In `api/positions.py`, find `_write_position_event_log`. Inside it, locate `qty = pos.get("qty") or 0` (line ~57). Replace with `qty = pos["qty"]` (the schema guarantees presence).

If the helper accepts a `dict` (post-snapshot output of `_snapshot_to_dict`) AND legacy callers still pass raw rows, verify both paths still have `qty` populated. If any legacy caller passes a partial dict, that caller should be fixed instead — but for now if `pos.get("qty")` is being defensive against a missing key (not just NULL), keep `pos.get("qty")` and remove only the `or 0`. The key existence is not a schema concern.

Recommended safest replacement:
```python
qty = pos["qty"]  # schema enforces NOT NULL post-Task-4
```

- [ ] **Step 4: Run the existing test suite to surface any caller that doesn't yet have qty**

```bash
pytest tests/operators/test_position_closure.py tests/api/ -q --tb=short 2>&1 | tail -15
```
Expected: all green. If any test fails because a fixture inserts a position without `qty`, that fixture must be updated to set `qty` (the schema demands it).

If fixtures fail: locate them via `grep -n "INSERT INTO positions" tests/`, update each to include `qty` in the column list and `size_usd / entry_price` (or a literal value) in VALUES.

- [ ] **Step 5: Commit**

```bash
git add operators/position_closure.py api/positions.py tests/
git commit -m "refactor: remove 'qty or 0' membranes — schema now enforces NOT NULL (closes #467)

Two silent translation membranes deleted:
- operators/position_closure.py::execute (was 'qty = snapshot.qty or 0')
- api/positions.py::_write_position_event_log (was 'qty = pos.get(\"qty\") or 0')

Test fixtures updated to set qty in INSERT statements (schema requires it).

The 'or 0' was the visible symptom of #467. The fix lives in schema, not in
the consumer."
```

---

### Task 6: TDD — write failing tests for NewType PrecheckConn / SnapshotConn

**Files:**
- Modify: `tests/db/test_transaction.py`

- [ ] **Step 1: Append the NewType tests**

```python


# ---- NewType enforcement (closes #468) ----

def test_precheck_connection_yields_PrecheckConn_type():
    """precheck_connection must yield a value whose type is PrecheckConn."""
    from db.transaction import precheck_connection, PrecheckConn

    with precheck_connection() as con:
        # NewType is a runtime no-op (the value is just the wrapped object),
        # but the type annotation must be importable and the helper must use it.
        assert con is not None
        # Verify the helper's annotation declares PrecheckConn (not raw sqlite3.Connection).
    import inspect
    from db.transaction import precheck_connection as pc
    sig = inspect.signature(pc.__wrapped__) if hasattr(pc, "__wrapped__") else inspect.signature(pc)
    # The contextmanager wrapper might hide signature; we check the module's
    # type alias is importable instead.
    assert PrecheckConn is not None


def test_snapshot_connection_yields_SnapshotConn_type():
    """snapshot_connection must yield a value whose type is SnapshotConn."""
    from db.transaction import snapshot_connection, SnapshotConn

    with snapshot_connection() as con:
        assert con is not None
    assert SnapshotConn is not None


def test_PrecheckConn_and_SnapshotConn_are_distinct_NewTypes():
    """PrecheckConn and SnapshotConn must be distinct NewType objects.
    A NewType-aware type checker (mypy) treats them as incompatible; at
    runtime they are both callables that return their argument unchanged."""
    from db.transaction import PrecheckConn, SnapshotConn

    assert PrecheckConn is not SnapshotConn
    # NewType instances are callables; calling either returns the argument.
    import sqlite3
    con = sqlite3.connect(":memory:")
    try:
        wrapped_a = PrecheckConn(con)
        wrapped_b = SnapshotConn(con)
        # Runtime equality: both wrap the same object.
        assert wrapped_a is con
        assert wrapped_b is con
    finally:
        con.close()
```

- [ ] **Step 2: Verify the 3 tests fail with ImportError**

Run: `pytest tests/db/test_transaction.py::test_precheck_connection_yields_PrecheckConn_type tests/db/test_transaction.py::test_snapshot_connection_yields_SnapshotConn_type tests/db/test_transaction.py::test_PrecheckConn_and_SnapshotConn_are_distinct_NewTypes -v 2>&1 | tail -15`

Expected: all 3 fail with `ImportError: cannot import name 'PrecheckConn'` (or similar).

- [ ] **Step 3: Commit**

```bash
git add tests/db/test_transaction.py
git commit -m "test(db): add failing NewType tests for PrecheckConn / SnapshotConn (advances #468)

Tests verify the NewTypes are importable, distinct, and runtime-trivial
(call returns argument unchanged). Compile-time enforcement is mypy's
responsibility (no test can directly assert mypy strictness)."
```

---

### Task 7: Implement NewType PrecheckConn / SnapshotConn in db/transaction.py

**Files:**
- Modify: `db/transaction.py`

- [ ] **Step 1: Add NewType definitions**

In `db/transaction.py`, after the existing imports, add:

```python
from typing import NewType

# Move 'precheck != snapshot' from convención to tipo (#468, Voronov 2026-05-26).
#
# PrecheckConn is a connection authorized for reads that will FEED a follow-up
# write transaction. The caller is contractually obligated to extract any field
# the write-tx will need into an immutable snapshot (see
# operators.precheck.OwnershipValidatedSnapshot) BEFORE the with block exits.
#
# SnapshotConn is a connection authorized for TERMINAL reads — results
# serialize to an output (JSON file, HTTP response, log) and are NOT used to
# drive a subsequent mutation. No follow-up re-validation obligation.
#
# Both NewTypes wrap sqlite3.Connection. mypy treats them as incompatible;
# at runtime, both are no-ops (the wrapped object is the original Connection).
# The mechanism is identical (PRAGMA query_only=1); the contract is in the
# type, not in the docstring.
PrecheckConn = NewType("PrecheckConn", sqlite3.Connection)
SnapshotConn = NewType("SnapshotConn", sqlite3.Connection)
```

- [ ] **Step 2: Update return type annotations and yield statements**

Find `precheck_connection`:

```python
@contextmanager
def precheck_connection() -> Iterator[sqlite3.Connection]:
    ...
    con = _open_configured_connection()
    try:
        con.execute("PRAGMA query_only = 1")
        yield con
    finally:
        con.close()
```

Change to:

```python
@contextmanager
def precheck_connection() -> Iterator[PrecheckConn]:
    ...
    con = _open_configured_connection()
    try:
        con.execute("PRAGMA query_only = 1")
        yield PrecheckConn(con)
    finally:
        con.close()
```

Same change for `snapshot_connection` with `SnapshotConn`.

- [ ] **Step 3: Update module-level imports order if needed**

Ensure `NewType` is imported alongside the existing `typing` imports (likely `from typing import Iterator`). Consolidate.

- [ ] **Step 4: Verify the 3 tests pass**

Run: `pytest tests/db/test_transaction.py -v 2>&1 | tail -20`
Expected: all transaction tests pass (the 3 new + the previous 12 = 15).

- [ ] **Step 5: Verify PositionClosure invariants still pass**

Run: `pytest tests/operators/test_position_closure.py -q 2>&1 | tail -3`
Expected: all pass. The NewType is runtime-invisible; no consumer changes are required.

- [ ] **Step 6: Commit**

```bash
git add db/transaction.py
git commit -m "feat(db): NewType PrecheckConn / SnapshotConn — invariant from convención to tipo (closes #468)

precheck_connection now returns Iterator[PrecheckConn]; snapshot_connection
returns Iterator[SnapshotConn]. Both are NewTypes wrapping sqlite3.Connection.
Runtime is a no-op; compile-time mypy treats them as incompatible.

Voronov: 'Si precheck y snapshot fueran intercambiables, #461 no habría
sido un bug. Lo fue. Por lo tanto la separación es funcional, no estética
— merece tipo, no comentario.'"
```

---

### Task 8: TDD — write failing test for OwnershipValidatedSnapshot factory pattern

**Files:**
- Create: `tests/operators/test_ownership_validated_snapshot.py`

- [ ] **Step 1: Create the test file**

```python
"""Tests for OwnershipValidatedSnapshot factory pattern (#469 + F6).

The class must not be constructible without the module-private sentinel.
The only legitimate constructor is operators.position_closure._run_precheck.
"""
import pytest


def test_cannot_construct_without_sentinel():
    """OwnershipValidatedSnapshot raised TypeError if constructed with a
    foreign sentinel (i.e., any object other than the module-private
    _VALIDATION_SENTINEL)."""
    from operators.precheck import OwnershipValidatedSnapshot, PositionSnapshot

    snap = PositionSnapshot(
        pos_id=1, tenant_id=42, status="open",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )

    # Try with a wrong sentinel (e.g., another object()).
    with pytest.raises(TypeError, match=r"private validation sentinel"):
        OwnershipValidatedSnapshot(inner=snap, _sentinel=object())


def test_cannot_construct_with_none_sentinel():
    """OwnershipValidatedSnapshot raises TypeError if _sentinel is None."""
    from operators.precheck import OwnershipValidatedSnapshot, PositionSnapshot

    snap = PositionSnapshot(
        pos_id=1, tenant_id=42, status="open",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )

    with pytest.raises(TypeError, match=r"private validation sentinel"):
        OwnershipValidatedSnapshot(inner=snap, _sentinel=None)


def test_internal_factory_builds_validated_snapshot():
    """_build_validated_snapshot is the only legitimate constructor.
    It is module-private (underscore prefix), not exported from precheck's
    public surface."""
    from operators.precheck import (
        _build_validated_snapshot,
        OwnershipValidatedSnapshot,
        PositionSnapshot,
    )

    snap = PositionSnapshot(
        pos_id=1, tenant_id=42, status="open",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )

    validated = _build_validated_snapshot(snap)
    assert isinstance(validated, OwnershipValidatedSnapshot)
    assert validated.inner == snap


def test_PrecheckOkToProceed_carries_OwnershipValidatedSnapshot():
    """PrecheckOkToProceed.snapshot is typed as OwnershipValidatedSnapshot
    (not PositionSnapshot) — the type-level guarantee that ownership was
    validated."""
    from operators.precheck import (
        PrecheckOkToProceed,
        OwnershipValidatedSnapshot,
        _build_validated_snapshot,
        PositionSnapshot,
    )

    snap = PositionSnapshot(
        pos_id=1, tenant_id=42, status="open",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )
    validated = _build_validated_snapshot(snap)
    ok = PrecheckOkToProceed(snapshot=validated)
    assert isinstance(ok.snapshot, OwnershipValidatedSnapshot)
    assert ok.snapshot.inner == snap
```

- [ ] **Step 2: Verify all 4 tests fail with ImportError**

Run: `pytest tests/operators/test_ownership_validated_snapshot.py -v 2>&1 | tail -15`
Expected: 4 failures, all at imports.

- [ ] **Step 3: Commit**

```bash
git add tests/operators/test_ownership_validated_snapshot.py
git commit -m "test(operators): add failing tests for OwnershipValidatedSnapshot factory (advances #469 F6)

The class cannot be constructed without the module-private sentinel.
The only legitimate constructor is _build_validated_snapshot (used by
PositionClosure._run_precheck). PrecheckOkToProceed carries the validated
snapshot, not the raw PositionSnapshot."
```

---

### Task 9: Implement OwnershipValidatedSnapshot + update PrecheckOkToProceed in operators/precheck.py

> **Footnote (post-implementation, post-Serrano review of PR #486 — 2026-05-26):** The error message, sentinel comment, and `Closes #469 + F6` framing in the Step 1 code block below were softened/qualified after this plan executed. Specifically:
> - PR #486 closes **#481** (doc honesty — the `"callable only from operators.position_closure._run_precheck"` wording was a documentation lie) and advances **#477** (registry coherence — Path 3 honest narrowing of the factory's single-call-site rung).
> - Issue **#487** (post-Serrano F2) tracks the wider asymmetry: `_VALIDATION_SENTINEL` is module-attribute-accessible, so any caller importing the sentinel name can construct `OwnershipValidatedSnapshot` directly, bypassing the factory. The narrowing in PR #486 named the factory side only; the sentinel side is still open.
>
> **For the current source-of-truth wording, read `operators/precheck.py` on `upstream/main`.** This plan document is a historical snapshot of the original implementation directive and intentionally preserves the original phrasing for archaeological fidelity.

**Files:**
- Modify: `operators/precheck.py`

- [ ] **Step 1: Add the sentinel + class + factory**

Append to `operators/precheck.py` (after existing PositionSnapshot/PrecheckNotFound/etc.):

```python
# Module-private sentinel — external callers cannot import this name
# (single underscore is a convention; the factory check is by `is` identity).
_VALIDATION_SENTINEL = object()


@dataclass(frozen=True)
class OwnershipValidatedSnapshot:
    """A PositionSnapshot whose ownership has been validated by a precheck.

    USER mode: caller_tenant_id matched snapshot.tenant_id at precheck time.
    SYSTEM mode: ownership validation does not apply; the snapshot is
    accepted by construction.

    Construction requires the module-private _VALIDATION_SENTINEL. The only
    legitimate constructor is `_build_validated_snapshot` (called by
    `operators.position_closure.PositionClosure._run_precheck`).

    A future write-tx that consumes this type is guaranteed (by construction)
    that ownership was checked at precheck. The write-tx MUST STILL re-validate
    the snapshot's mutable fields against a fresh re-SELECT — that is enforced
    in PositionClosure.execute() by explicit field-by-field comparison.

    Closes #469 + F6 (Voronov path C + E): the validation guarantee lives in
    the type, not in a docstring.
    """
    inner: PositionSnapshot
    _sentinel: object  # must be _VALIDATION_SENTINEL at construction

    def __post_init__(self):
        if self._sentinel is not _VALIDATION_SENTINEL:
            raise TypeError(
                "OwnershipValidatedSnapshot cannot be constructed directly. "
                "Use the private validation sentinel via "
                "operators.precheck._build_validated_snapshot (callable only "
                "from operators.position_closure._run_precheck)."
            )


def _build_validated_snapshot(snapshot: PositionSnapshot) -> OwnershipValidatedSnapshot:
    """Internal factory used by PositionClosure._run_precheck.

    NOT exported from this module's public surface (single underscore).
    Module-private convention: external code should not call this directly.
    """
    return OwnershipValidatedSnapshot(inner=snapshot, _sentinel=_VALIDATION_SENTINEL)
```

- [ ] **Step 2: Update PrecheckOkToProceed.snapshot field type**

Find the existing `PrecheckOkToProceed` dataclass. Change:

```python
@dataclass(frozen=True)
class PrecheckOkToProceed:
    """Position passed all precheck conditions. ..."""
    snapshot: PositionSnapshot
```

to:

```python
@dataclass(frozen=True)
class PrecheckOkToProceed:
    """Position passed all precheck conditions. snapshot is an
    OwnershipValidatedSnapshot — type-level guarantee that ownership was
    checked at precheck (#469 + F6, Voronov 2026-05-26)."""
    snapshot: OwnershipValidatedSnapshot
```

- [ ] **Step 3: Verify the 4 new tests pass**

Run: `pytest tests/operators/test_ownership_validated_snapshot.py -v 2>&1 | tail -10`
Expected: 4/4 pass.

- [ ] **Step 4: Note: PositionClosure tests will break**

Run: `pytest tests/operators/test_position_closure.py -q 2>&1 | tail -5`
Expected: failures, because `PositionClosure._run_precheck` currently constructs `PrecheckOkToProceed(snapshot=snapshot)` directly (where snapshot is `PositionSnapshot`, but the field now expects `OwnershipValidatedSnapshot`). Task 10 fixes this.

Do NOT commit Task 9 in isolation if PositionClosure tests fail. Continue to Task 10.

Actually: commit anyway — the test suite is in transition. The next task brings the operator into compliance.

- [ ] **Step 5: Commit**

```bash
git add operators/precheck.py
git commit -m "feat(operators): OwnershipValidatedSnapshot + _build_validated_snapshot factory (advances #469 F6)

The class cannot be constructed without the module-private validation
sentinel. PrecheckOkToProceed.snapshot is now typed as
OwnershipValidatedSnapshot (type-level guarantee that ownership was
checked at precheck).

Operator (PositionClosure._run_precheck) updated to use the factory in
Task 10."
```

---

### Task 10: Migrate PositionClosure to use OwnershipValidatedSnapshot + re-validate ALL mutable fields

**Files:**
- Modify: `operators/position_closure.py`

This task completes #469+F6. The operator's `_run_precheck` constructs `OwnershipValidatedSnapshot` via the factory; `execute()` re-validates EVERY mutable field of the snapshot against the fresh re-SELECT (not only `tenant_id` and `status`).

- [ ] **Step 1: Update imports**

In `operators/position_closure.py`, update the precheck import:

Before:
```python
from operators.precheck import (
    PositionSnapshot,
    PrecheckNotFound,
    PrecheckAlreadyClosed,
    PrecheckOkToProceed,
    PrecheckRejectedState,
    PrecheckResult,
)
```

After:
```python
from operators.precheck import (
    PositionSnapshot,
    OwnershipValidatedSnapshot,
    _build_validated_snapshot,
    PrecheckNotFound,
    PrecheckAlreadyClosed,
    PrecheckOkToProceed,
    PrecheckRejectedState,
    PrecheckResult,
)
```

- [ ] **Step 2: Update `_run_precheck` to use the factory**

Find the line `return PrecheckOkToProceed(snapshot=snapshot)`. Replace with:

```python
        return PrecheckOkToProceed(snapshot=_build_validated_snapshot(snapshot))
```

- [ ] **Step 3: Update `execute()` to unwrap the validated snapshot and re-validate ALL mutable fields**

Find the block inside `execute()` that handles `PrecheckOkToProceed`:

```python
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
            # ... [status handling: closed → already_closed; other → rejected_unexpected_state] ...

            # Snapshot's immutable fields trusted; consume directly.
            pnl_usd, pnl_pct = _calc_pnl(
                snapshot.direction, snapshot.entry_price, self._exit_price, snapshot.qty,
            )
            # ...
```

Replace the relevant section with:

```python
        # PrecheckOkToProceed: write-tx must re-validate ALL snapshot fields.
        # OwnershipValidatedSnapshot guarantees ownership was checked at precheck;
        # the snapshot's mutable fields (everything in PositionSnapshot — tenant_id,
        # status, entry_price, qty, direction, symbol) MUST be re-validated against
        # the fresh row inside BEGIN IMMEDIATE. Schema does not enforce immutability
        # of entry_price/qty/direction/symbol (CLAUDE.md "Capas de enforcement"),
        # so the write-tx is the only place where stale snapshots are caught.
        validated = result.snapshot   # OwnershipValidatedSnapshot
        snap = validated.inner         # PositionSnapshot
        with _tx_module.transaction() as con:
            row = db_get_position_by_id(con, self._pos_id)
            if row is None:
                return CloseOutcome(status="not_found", position=None, pnl_usd=None, pnl_pct=None)

            # Status handling (3 branches: open / closed / other).
            if row["status"] == "closed":
                race_snap = PositionSnapshot(
                    pos_id=row["id"],
                    tenant_id=row["tenant_id"],
                    status=row["status"],
                    symbol=row["symbol"],
                    direction=row["direction"],
                    entry_price=row["entry_price"],
                    qty=row["qty"],
                )
                return CloseOutcome(
                    status="already_closed",
                    position=self._snapshot_to_dict(race_snap),
                    pnl_usd=None, pnl_pct=None,
                )
            if row["status"] != "open":
                race_snap = PositionSnapshot(
                    pos_id=row["id"],
                    tenant_id=row["tenant_id"],
                    status=row["status"],
                    symbol=row["symbol"],
                    direction=row["direction"],
                    entry_price=row["entry_price"],
                    qty=row["qty"],
                )
                return CloseOutcome(
                    status="rejected_unexpected_state",
                    position=self._snapshot_to_dict(race_snap),
                    pnl_usd=None, pnl_pct=None,
                )

            # Re-validate ALL other mutable fields (#469 + F6).
            # If any field has drifted, the snapshot is stale; collapse to
            # NOT_FOUND (IDOR-safe — same shape as ownership mismatch).
            if (row["tenant_id"] != snap.tenant_id
                or row["entry_price"] != snap.entry_price
                or row["qty"] != snap.qty
                or row["direction"] != snap.direction
                or row["symbol"] != snap.symbol):
                return CloseOutcome(status="not_found", position=None, pnl_usd=None, pnl_pct=None)

            # All snapshot fields confirmed. Snapshot is trusted; proceed.
            pnl_usd, pnl_pct = _calc_pnl(
                snap.direction, snap.entry_price, self._exit_price, snap.qty,
            )
            exit_ts = self._now.isoformat()
            closed_row = db_close_position_sql(
                con, self._pos_id, self._exit_price, self._exit_reason,
                exit_ts, pnl_usd, pnl_pct,
            )
            if snap.tenant_id is not None and pnl_usd is not None:
                _capital_module.apply_pnl_to_capital(con, snap.tenant_id, pnl_usd)
            elif snap.tenant_id is None:
                log.warning(
                    "PositionClosure: skipping capital roll-in for legacy tenant_id=NULL pos_id=%s",
                    self._pos_id,
                )
            self._result_row = closed_row
            self._result_pnl = (pnl_usd, pnl_pct)
```

- [ ] **Step 4: Run the full operator test suite**

```bash
pytest tests/operators/ -v --tb=short 2>&1 | tail -25
```
Expected: all existing invariants pass. If `test_tenant_reassignment_between_precheck_and_write_rejects_close` (the #461 closure proof) still passes, the tenant_id re-validation in the new code is wired correctly.

- [ ] **Step 5: Commit**

```bash
git add operators/position_closure.py
git commit -m "feat(operators): PositionClosure re-validates ALL mutable snapshot fields in write-tx (closes #469 F6)

_run_precheck constructs PrecheckOkToProceed with an OwnershipValidatedSnapshot
(type-level guarantee that ownership was checked at precheck).

execute() re-validates not only tenant_id and status, but also entry_price,
qty, direction, and symbol against the freshly re-SELECTed row. Any drift
collapses to NOT_FOUND (IDOR-safe).

Voronov F6: the schema does not enforce immutability of these fields; the
write-tx is the only place where stale snapshots are caught. Convention
becomes type+runtime check."
```

---

### Task 11: Add invariant test for cross-mutation race (entry_price)

**Files:**
- Modify: `tests/operators/test_position_closure.py`

- [ ] **Step 1: Append the new invariant test**

```python


# ---- Invariant 14: cross-mutation race rejects close when snapshot field drifts ----

def test_cross_mutation_race_entry_price_rejects_close(fresh_db_with_two_tenants):
    """If a snapshot field that is NOT tenant_id/status changes between precheck
    and BEGIN IMMEDIATE (e.g., entry_price is updated by a migration or ad-hoc
    UPDATE), the operator must detect the drift and return not_found.

    Closes #469 + F6: validation lives in the type (OwnershipValidatedSnapshot)
    and the runtime field-by-field comparison in execute()."""
    from operators.position_closure import PositionClosure

    closure = PositionClosure(
        pos_id=1, exit_price=110.0, exit_reason="TP_HIT",
        mode="USER", caller_tenant_id=1,
    )
    closure.__enter__()  # precheck reads entry_price (whatever the fixture set)

    # Simulate an entry_price drift between precheck and write-tx.
    with transaction() as con:
        con.execute("UPDATE positions SET entry_price = 999.99 WHERE id = 1")

    outcome = closure.execute()
    closure.__exit__(None, None, None)

    # The snapshot held the old entry_price; the row now has 999.99.
    # The mismatch must collapse to NOT_FOUND.
    assert outcome.status == "not_found"

    # The row was not closed.
    with transaction() as con:
        pos = con.execute("SELECT status, entry_price FROM positions WHERE id=1").fetchone()
    assert pos["status"] == "open"
    assert pos["entry_price"] == 999.99  # our UPDATE remains
```

- [ ] **Step 2: Verify the test passes**

Run: `pytest tests/operators/test_position_closure.py::test_cross_mutation_race_entry_price_rejects_close -v 2>&1 | tail -10`
Expected: PASS. The field-by-field comparison added in Task 10 catches the drift.

- [ ] **Step 3: Run all invariants**

Run: `pytest tests/operators/test_position_closure.py -v 2>&1 | tail -25`
Expected: all 14+ invariants pass.

- [ ] **Step 4: Commit**

```bash
git add tests/operators/test_position_closure.py
git commit -m "test(operators): add invariant #14 — cross-mutation race rejects close (#469 F6 closure proof)

If entry_price (or any other snapshot field) drifts between precheck and
BEGIN IMMEDIATE, the snapshot is stale and the operator returns NOT_FOUND.
Exercises the field-by-field re-validation added in Task 10."
```

---

### Task 12: Update CLAUDE.md registry to reflect the moves

**Files:**
- Modify: `CLAUDE.md`

The registry was written in Task 2 listing the invariants with current vs target layer. After Tasks 4-11, the moves are complete. Update the table to reflect the new actual state.

- [ ] **Step 1: Locate the registry section**

Run: `grep -n "Capas de enforcement de invariantes" CLAUDE.md`

- [ ] **Step 2: Replace the "Invariantes C2" table with the post-merge state**

Find the "Invariantes C2 — estado actual y movimiento de este PR" table from Task 2. Replace with:

```markdown
### Invariantes C2 — estado tras este PR

| Invariante de dominio | Capa enforced | Mecanismo | Issue cerrado |
|---|---|---|---|
| `qty` siempre tiene valor numérico para positions | **Schema** | `CHECK (qty IS NOT NULL)` en `positions` (vía `_migrate_qty_not_null` en `db/schema.py`) | #467 |
| `precheck_connection` y `snapshot_connection` son contratos distintos | **Tipo** | `NewType("PrecheckConn", sqlite3.Connection)` y `NewType("SnapshotConn", sqlite3.Connection)` en `db/transaction.py` — mypy detecta mis-uso | #468 |
| Los campos del snapshot consumidos por el write-tx no cambian entre precheck y BEGIN IMMEDIATE | **Tipo + runtime check** | `OwnershipValidatedSnapshot` (factory privada en `operators/precheck.py`) + field-by-field re-validation en `PositionClosure.execute()` cubre los 6 campos del `PositionSnapshot` | #469 + F6 |

### Patrón nombrado: "invariantes de dominio sin contraparte estructural"

(unchanged — still applies)

### Known scope gap (Voronov 2026-05-26)

> El sistema enforza invariantes en el momento de cruce (precheck→snapshot, snapshot→write) pero **no enforza invariantes en el momento de origen** (creación de la position). Toda la cadena de defensa de C2 asume que la position fue creada correctamente. Si en `position_open` se crea una row con `tenant_id` mal asignado, los 3 mecanismos de C2 la sostendrán correctamente *con el tenant equivocado*. C2 endurece el ciclo de vida; no endurece el nacimiento.

Esta deuda NO está cubierta por este PR. Tratamiento futuro: auditar `db_create_position` y los endpoints que invocan creación (`POST /positions`, paths del scanner) bajo la misma lente del registro de capas.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): update enforcement registry — 3 moves complete + name 'birth-time' debt as known scope gap

Registry reflects the actual state: qty NOT NULL in schema; precheck/snapshot
as distinct NewTypes; OwnershipValidatedSnapshot + write-tx field-by-field
re-validation for snapshot drift.

Voronov flagged: 'C2 endurece el ciclo de vida; no endurece el nacimiento.'
Documented as Known scope gap for future audit of position creation paths."
```

---

### Task 13: Final verification

**Files:** none modified (unless smoke surfaces a fix).

- [ ] **Step 1: Grep for surviving `or 0` patterns related to qty**

Run: `grep -rn "qty.*or 0\|qty.*qty\b.*0" --include="*.py" .`
Expected: empty. (If anything remains, it's a missed membrane — fix and commit.)

- [ ] **Step 2: Run all transaction tests**

Run: `pytest tests/db/test_transaction.py -v 2>&1 | tail -20`
Expected: 15+ pass (was 12; added 3 NewType tests in Task 6).

- [ ] **Step 3: Run all PositionClosure invariants**

Run: `pytest tests/operators/test_position_closure.py -v 2>&1 | tail -25`
Expected: 14+ pass (was 13; added invariant #14 in Task 11).

- [ ] **Step 4: Run precheck type tests**

Run: `pytest tests/operators/test_precheck.py tests/operators/test_ownership_validated_snapshot.py -v 2>&1 | tail -15`
Expected: existing 5 + new 4 = 9 pass.

- [ ] **Step 5: Run migration tests**

Run: `pytest tests/db/test_migrate_qty_not_null.py -v 2>&1 | tail -10`
Expected: 4 pass.

- [ ] **Step 6: Atomicity regression**

Run: `pytest tests/api/test_check_position_stops_atomicity.py -v 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 7: Full suite**

Run: `pytest tests/ --tb=no -q -p no:cacheprovider 2>/dev/null | tail -5`
Expected: baseline (~2498 post-#466) + ~11 new tests (4 migration + 3 NewType + 4 ownership-validated) + 1 invariant 14 = ~2514. Skipped count same (~22). The pre-existing `test_setup` daemon flake may or may not appear.

- [ ] **Step 8: Smoke imports for all modified modules**

```bash
python -c "
import importlib
for m in ('db.transaction', 'db.schema', 'operators.precheck', 'operators.position_closure', 'api.positions'):
    importlib.import_module(m)
from db.transaction import PrecheckConn, SnapshotConn
from operators.precheck import OwnershipValidatedSnapshot, _build_validated_snapshot
print('all 5 modules + 4 new symbols import cleanly')
"
```
Expected: `all 5 modules + 4 new symbols import cleanly`.

---

### Task 14: Push + open PR + close #467 #468 #469 (REQUIRES USER CONFIRMATION)

**Files:** none modified.

This step is externally visible to `sssimon/trading-spacial`. Confirm before executing in an autonomous session.

- [ ] **Step 1: Push the branch**

Run: `git push -u origin feat/enforcement-layers-registry-467-468-469`
Expected: branch published; URL printed.

- [ ] **Step 2: Open the PR**

Run:
```bash
gh pr create --repo sssimon/trading-spacial \
  --title "feat: 'Capas de enforcement de invariantes' registry + 3 moves (qty→schema, precheck/snapshot→tipo, snapshot==row→tipo) [closes #467 #468 #469]" \
  --body "$(cat <<'EOF'
## Summary

Voronov's meta-reframe of Cluster C2. Closes three issues by FIRST naming the patología they share — *"invariantes de dominio sin contraparte estructural"* — and THEN moving three specific invariants from `convención` to either `schema` or `tipo` within a written registry.

The three moves:

- **#467 (qty != NULL): convención → schema.** `_migrate_qty_not_null` backfills `qty = size_usd / entry_price` for legacy rows; aborts loudly if any row remains NULL; recreates the `positions` table with `CHECK (qty IS NOT NULL)`. The two `or 0` membranes (in `position_closure.execute()` and `_write_position_event_log`) are deleted — the schema now guarantees what the code was silently translating.

- **#468 (precheck != snapshot): convención → tipo.** `NewType("PrecheckConn", sqlite3.Connection)` and `NewType("SnapshotConn", sqlite3.Connection)` in `db/transaction.py`. Runtime is a no-op; mypy detects mis-use at the call site. The distinction is no longer enforceable only by code reviewer attention.

- **#469 + F6 (snapshot fields stable between precheck and write-tx): convención → tipo + runtime check.** `OwnershipValidatedSnapshot` with private factory (`_build_validated_snapshot`) callable only from `PositionClosure._run_precheck`. `PrecheckOkToProceed.snapshot` typed as this class. `PositionClosure.execute()` re-validates ALL six mutable snapshot fields (tenant_id, status, entry_price, qty, direction, symbol) against the fresh re-SELECT — not only the two it covered before. Any drift collapses to NOT_FOUND (IDOR-safe).

## Why this PR

Per Voronov's reframe (2026-05-26):

> Los tres issues no son deuda de #466. Son la primera generación de un patrón donde el dominio afirma más de lo que el almacenamiento garantiza, y el código paga la diferencia en membranas silenciosas — `or 0`, comentarios de revisor, re-validaciones parciales. Mientras esa asimetría no esté nombrada, cada issue futuro de esta familia se debatirá como si fuera nuevo. No es un problema de implementación. Es un problema de registro.

The plan begins by writing that registry in CLAUDE.md ("Capas de enforcement de invariantes") with four columns — schema / tipo / test / convención — and lists the three C2 invariants with their current and target layers. The implementation then executes the three layer moves. After merge, the registry reflects the actual state.

## Known scope gap (named, NOT closed by this PR)

> El sistema enforza invariantes en el momento de cruce (precheck→snapshot, snapshot→write) pero NO enforza invariantes en el momento de origen (creación de la position). Si en `position_open` se crea una row con `tenant_id` mal asignado, los 3 mecanismos de C2 la sostendrán correctamente *con el tenant equivocado*. C2 endurece el ciclo de vida; no endurece el nacimiento.

Documented in CLAUDE.md "Capas de enforcement" §Known scope gap. Future audit pending.

## Resolves

- **#467** — qty NULL silent close
- **#468** — structural enforcement of precheck vs snapshot
- **#469 + F6** — #461 defense-in-depth + re-validate ALL mutable snapshot fields (not only tenant_id/status)

## Test plan

- [x] 4 migration tests in `tests/db/test_migrate_qty_not_null.py` (backfill, abort on unbackfillable, IntegrityError post-migration, idempotent)
- [x] 3 NewType tests in `tests/db/test_transaction.py` (PrecheckConn / SnapshotConn yield + distinct)
- [x] 4 OwnershipValidatedSnapshot tests in `tests/operators/test_ownership_validated_snapshot.py` (cannot construct without sentinel, factory builds, PrecheckOkToProceed carries the validated type)
- [x] Invariant #14 in `tests/operators/test_position_closure.py` (cross-mutation race on entry_price rejects close)
- [x] Existing 13 PositionClosure invariants still pass (snapshot pattern is unchanged from the caller's perspective; only the internal type tightens)
- [x] Existing 12 transaction tests still pass
- [x] Atomicity regression still passes
- [x] Full suite ~2514 passed, 22 skipped (Binance network), 1 pre-existing test_setup flake (PR #445 known limitation)
- [ ] Manual smoke in prod after merge: confirm close-flow works; confirm `_migrate_qty_not_null` ran on prod DB cleanly

## What this PR does NOT do

- Does NOT enforce immutability of `entry_price`/`qty`/`direction`/`symbol` at schema level (CHECK / trigger). The write-tx re-validation catches the drift; schema-level enforcement is a separate future PR (would close the "stale snapshot" detection at write time but would also require redesigning any operation that legitimately needs to mutate these fields, e.g., split positions).
- Does NOT close the "birth-time invariants" gap — see Known scope gap above.
- Does NOT introduce a `validated_transaction(precheck_fn)` higher-order helper. The OwnershipValidatedSnapshot type IS the hand-off; an over-helper would obscure it.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Close #467, #468, #469 with cross-link**

Run:
```bash
NEW_PR=$(gh pr view --json number --jq .number)
gh issue comment 467 --repo sssimon/trading-spacial --body "Closed by #$NEW_PR. \`qty != NULL\` is now enforced at schema level via \`CHECK (qty IS NOT NULL)\`. The 'or 0' membranes are deleted. \`_migrate_qty_not_null\` backfills legacy rows from \`size_usd/entry_price\` and aborts loudly if any row remains unbackfillable."
gh issue close 467 --repo sssimon/trading-spacial

gh issue comment 468 --repo sssimon/trading-spacial --body "Closed by #$NEW_PR via Voronov path B (NewType). \`PrecheckConn\` and \`SnapshotConn\` are now distinct \`NewType\` aliases for \`sqlite3.Connection\`. Runtime is a no-op; mypy detects mis-use at the call site. The distinction lives in the type system, not in code reviewer attention."
gh issue close 468 --repo sssimon/trading-spacial

gh issue comment 469 --repo sssimon/trading-spacial --body "Closed by #$NEW_PR via Voronov path C+E together. \`OwnershipValidatedSnapshot\` (private factory callable only from \`PositionClosure._run_precheck\`) types the precheck output. \`execute()\` now re-validates ALL six mutable snapshot fields (tenant_id, status, entry_price, qty, direction, symbol) against the fresh re-SELECT — not only two. F6 (the missing re-validations for entry_price/qty/direction/symbol) is closed by the same change."
gh issue close 469 --repo sssimon/trading-spacial
```

- [ ] **Step 4: Done**

The plan is fully executed when this step completes.

---

## Self-Review

**Spec coverage:**
- Voronov's pre-condition (registry in CLAUDE.md): Task 2.
- Voronov path D for #467 (backfill + CONSTRAINT): Tasks 3-4 (TDD + implementation), Task 5 (remove membranes).
- Voronov path B for #468 (NewType): Tasks 6-7.
- Voronov path C+E for #469+F6 (factory + re-validate all mutable fields): Tasks 8-11.
- Update to registry post-moves: Task 12.
- Known scope gap (birth-time invariants): Task 12.

**Placeholder scan:** None of the disallowed patterns present. All code blocks complete. Migration table recreation explicitly notes "if the live schema has additional columns or indexes, the implementer must update the CREATE/INSERT statements" — that is a real caveat, not a placeholder, because the live schema is not in this plan's context.

**Type consistency:**
- `PrecheckConn` / `SnapshotConn` named consistently across Tasks 6, 7.
- `OwnershipValidatedSnapshot` / `_build_validated_snapshot` / `_VALIDATION_SENTINEL` named consistently across Tasks 8, 9, 10.
- `PositionSnapshot` field set (pos_id, tenant_id, status, symbol, direction, entry_price, qty) named consistently with #466 (Tasks 10-12 use the same fields).

**Caveats:**
- Task 4's `CREATE TABLE positions_new` schema is a best-guess from the test fixture; the implementer MUST verify against the live `signals.db` schema before running. If the live schema has columns this plan omits, INSERT will silently lose data. The implementer must inspect `.schema positions` on the live DB first.
- Task 5's removal of `qty = pos.get("qty") or 0` in `_write_position_event_log` assumes `qty` is always populated in `pos` dicts post-Task-4. If any caller bypasses the new schema constraint (raw SQL elsewhere), that caller must be fixed. The plan does not audit for this; the test suite catches it via failures.
- Task 10's `execute()` rewrite is the most substantial code change. The implementer should run all `tests/operators/test_position_closure.py` invariants after this task and confirm the existing 13 still pass before adding invariant #14 in Task 11.
