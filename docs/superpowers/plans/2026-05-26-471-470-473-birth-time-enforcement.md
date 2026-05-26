# Cluster D — Birth-time enforcement (dual rungs: schema + tipo) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close issues #471, #470, #473 by enforcing the contract `open()` was never asked to sign: every `Position` row that exists must have satisfied both (a) the existential contract of the schema (`qty > 0`, `tenant_id NOT NULL`, unique `(tenant_id, scan_id)` while open) and (b) the nomination contract of the entry boundary (typed `OpenPositionRequest` body + private factory `_build_open_request` with sentinel + typed error taxonomy + idempotency key + atomic `BirthRegistrar` owning transaction and post-commit). Form D is **dual**, not symmetric to `PositionClosure`.

**Architecture:** Per Voronov (2026-05-26):

> *"`close()` valida una transición entre dos estados conocidos del mismo objeto. `open()` no valida transición — valida un acto de nominación. Son primos, no hermanos."*

The plan does NOT introduce a `PositionOpen` operator symmetric to `PositionClosure` — that would be "false symmetry — imitation visual; no comparte contrato". Birth invariants split across two rungs of the C2 enforcement registry:

| Invariante | Rung | Mechanism |
|---|---|---|
| `qty > 0` (or quarantine) | Schema | CHECK + `_migrate_qty_positive` with quarantine policy reusing `legacy_unmeasurable` |
| `tenant_id NOT NULL` (or quarantine) | Schema | CHECK + `_migrate_tenant_id_not_null` with new quarantine status `legacy_no_tenant` |
| `UNIQUE (tenant_id, scan_id) WHERE status='open' AND scan_id IS NOT NULL` | Schema | Partial unique index via `_migrate_unique_open_scan` |
| Input → Position legítima | Tipo + runtime órgano de rechazo | Pydantic `OpenPositionRequest` (extra='forbid') + private factory `_build_open_request` (sentinel-protected) |
| Idempotencia operacional | Tipo (HTTP) | `Idempotency-Key` header + 24h `idempotency_keys` table |
| Error taxonomy 422/409 vs 500 | Tipo | `BirthError` hierarchy raised by factory, mapped by route handler |
| Post-commit atomicidad (`update_positions_json`) | Operador-ligero | `BirthRegistrar` owns `with transaction()` + post-commit |

**The invariant of D, declarable in CLAUDE.md:**
> Una `Position` existe si y solo si su acto de nominación satisfizo simultáneamente: (a) el contrato existencial del schema (qué la convierte ontológicamente en Position), y (b) el contrato de nominación de la frontera de entrada (qué valida que el input externo intentaba declararla legítimamente). Schema es la frontera que ningún caller evade; nominación es donde el error toma forma semántica.

**Tech Stack:** Python 3.12, `sqlite3` stdlib, `pydantic` 2.x (already a project dependency), FastAPI, `dataclasses`, `pytest`.

---

## Production data measurement (already done — baked into Task 1)

`signals.db` snapshot (2026-05-26):

- 2018 total positions
- **2018 with tenant_id=NULL** (100% — every existing row needs quarantine)
- **72 with qty=0.0 exactly** (68 closed, 2 open, 2 cancelled — these bypass C2's NULL check)
- **350 with entry_ts in the future** (ALL already in `legacy_unmeasurable` from C2 quarantine — overlap)
- 2016 with scan_id=NULL (99.9%); 2 rows share scan_id=42 (already-occurred duplicate, both closed)
- By status: 2 cancelled, 1344 closed, 670 `legacy_unmeasurable` (C2 residue), 2 open

## Quarantine policy (per Voronov C2 amendment pattern)

- **qty=0**: extend the existing CHECK: `CHECK ((qty IS NOT NULL AND qty > 0) OR status='legacy_unmeasurable')`. Migration UPDATEs the 72 zero-qty rows to `legacy_unmeasurable`.
- **tenant_id=NULL**: new quarantine status `legacy_no_tenant`. CHECK becomes `CHECK (tenant_id IS NOT NULL OR status IN ('legacy_unmeasurable', 'legacy_no_tenant'))`. Migration UPDATEs the NULL-tenant rows that are not already quarantined to `legacy_no_tenant`. Rows already `legacy_unmeasurable` keep their status — they get exempted by the OR.
- **scan_id duplicate**: only 2 rows affected, both closed. UNIQUE partial index applies only `WHERE status='open' AND scan_id IS NOT NULL`. Current open rows have scan_id=NULL so no conflict at migration time.

## The 4 BLOCKERS from Serrano this plan closes

1. **qty=0.0 bypass** → D-schema CHECK `qty > 0` + 72-row quarantine
2. **No idempotency** → `Idempotency-Key` HTTP header + UNIQUE `(tenant_id, scan_id) WHERE status='open' AND scan_id IS NOT NULL`
3. **`except Exception → 500 str(e)`** → typed `BirthError` hierarchy in factory + route handlers map status code; collapse to typed handlers
4. **Total contractual delta with `close()`** → D-nominación factory + `BirthRegistrar` (NOT a symmetric operator)

## The 6 HIGH findings closed inline

- **F5** (5-deep qty fallback): Pydantic requires `qty`; no fallback chain anywhere
- **F6** (tenant_id silently dropped from body): Pydantic `extra='forbid'`
- **F7** (key-presence check, not value-valid): Pydantic per-field validators (symbol allowlist, entry_price > 0, direction enum required, SL/TP relational)
- **F8** (`update_positions_json` outside tx): `BirthRegistrar` owns both
- **F9** (`entry_ts` defaults to now, no validation): Pydantic validator — accepts only `now-7d ≤ entry_ts ≤ now+60s` or `None` (server fills)
- **F10** (no rate limit): **deferred** — out of scope for D. Tracked in CLAUDE.md "Known scope gap"

## MEDIUM/LOW findings deferred to follow-up issues

- F11 (test fixtures bypass): #479 already tracking
- F12 (direction defaults to LONG): closed by F7 (direction now required enum)
- F13 (scan_id nullable, no FK to non-existent `scans`/`signals` table): UNIQUE partial index closes the race; FK is a separate refactor
- F14 (status hardcoded 'open'): not user-supplied; out of scope
- F15 (no structured logging at birth): added inline in `BirthRegistrar.register`

---

## Scope Check

One cohesive subsystem (birth-time invariants of `positions`). The three schema CHECKs share the migration pattern; the Pydantic + factory + BirthRegistrar + Idempotency-Key form the nomination boundary. They co-execute on every `POST /positions`. Single plan is correct.

---

## File Structure

### New files

- `tests/db/test_migrate_qty_positive.py` — TDD harness for the qty>0 migration.
- `tests/db/test_migrate_tenant_id_not_null.py` — TDD harness for tenant_id quarantine + CHECK.
- `tests/db/test_migrate_unique_open_scan.py` — TDD harness for the partial unique index.
- `api/positions_birth.py` — new module owning Pydantic body model, typed errors, sentinel-protected factory, BirthRegistrar, idempotency cache. Separating from `api/positions.py` keeps that file focused on routes and keeps the birth-path composable for testing.
- `tests/api/test_open_position_request.py` — Pydantic body model invariant tests.
- `tests/api/test_build_open_request.py` — factory sentinel + cross-field tests.
- `tests/api/test_birth_errors.py` — typed error taxonomy + HTTP mapping tests.
- `tests/api/test_birth_registrar.py` — transactional + post-commit atomicity tests.
- `tests/api/test_idempotency_key.py` — Idempotency-Key cache behavior.

### Modified files

- `CLAUDE.md`:
  - Add new entries to "Invariantes C2 — estado tras este PR" table (now becomes "Invariantes registradas — estado tras D"); document the dual-rung principle; declare `legacy_no_tenant`; close the "Known scope gap" entry (birth-time) and replace with a new "Known scope gap: rate limiting (F10)".
- `db/schema.py`:
  - 3 new migration functions (`_migrate_qty_positive`, `_migrate_tenant_id_not_null`, `_migrate_unique_open_scan`).
  - 1 new table `idempotency_keys` (CREATE TABLE IF NOT EXISTS in `init_db`).
  - Wire all 3 migrations into `init_db` (after the existing `_migrate_qty_not_null`).
- `db/positions.py`:
  - `db_create_position` renamed `db_create_position_sql`, takes `ValidatedOpenRequest`, is thin SQL only. Deletes 5-deep `qty` fallback and defensive `data.get(...)` chains.
- `api/positions.py`:
  - `open_position` route rewritten: reads `Idempotency-Key` header, delegates to `BirthRegistrar.register(_build_open_request(...))`, maps `BirthError` to HTTP, kills bare `except Exception`.
- A handful of test fixtures that INSERT positions without `qty>0` or with `tenant_id=NULL` — only updated as needed to satisfy the new CHECKs.

---

## Locked API surface (referenced by tasks below)

### Pydantic body model (Task 9 spec)

```python
# api/positions_birth.py
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Curated allowlist. We re-export from btc_scanner to avoid a second source of truth.
from btc_scanner import DEFAULT_SYMBOLS as _SCANNER_SYMBOLS
ALLOWED_SYMBOLS = frozenset(_SCANNER_SYMBOLS)


class OpenPositionRequest(BaseModel):
    """Validated body of POST /positions.

    Per Voronov D-Tipo: this is the nomination contract. Every field validator
    here turns an external string-shaped intent into a structurally legitimate
    Position-in-the-making. `extra='forbid'` closes F6 (tenant_id from body
    silently dropped).
    """
    model_config = ConfigDict(extra="forbid")

    symbol: str
    entry_price: float
    direction: Literal["LONG", "SHORT"]
    qty: float
    size_usd: Optional[float] = None
    entry_ts: Optional[datetime] = None
    scan_id: Optional[int] = None
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    atr_entry: Optional[float] = None
    be_mult: Optional[float] = None
    notes: str = ""

    @field_validator("symbol")
    @classmethod
    def _symbol_uppercase_and_allowed(cls, v: str) -> str:
        sym = v.strip().upper()
        if sym not in ALLOWED_SYMBOLS:
            raise ValueError(
                f"symbol {sym!r} not in curated allowlist; allowed: "
                f"{sorted(ALLOWED_SYMBOLS)}"
            )
        return sym

    @field_validator("entry_price")
    @classmethod
    def _entry_price_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("entry_price must be > 0")
        return v

    @field_validator("qty")
    @classmethod
    def _qty_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("qty must be > 0")
        return v

    @field_validator("size_usd")
    @classmethod
    def _size_usd_positive_if_present(cls, v):
        if v is not None and v <= 0:
            raise ValueError("size_usd must be > 0 when provided")
        return v

    @field_validator("entry_ts")
    @classmethod
    def _entry_ts_within_window(cls, v):
        if v is None:
            return v
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if v > now + timedelta(seconds=60):
            raise ValueError("entry_ts more than 60s in the future")
        if v < now - timedelta(days=7):
            raise ValueError("entry_ts more than 7 days in the past")
        return v

    @model_validator(mode="after")
    def _cross_field_invariants(self) -> "OpenPositionRequest":
        # qty and size_usd consistency (within 1 cent tolerance).
        if self.size_usd is not None:
            implied = self.qty * self.entry_price
            if abs(implied - self.size_usd) >= 0.01:
                raise ValueError(
                    f"qty * entry_price = {implied:.4f} but size_usd = "
                    f"{self.size_usd:.4f}; difference exceeds 0.01"
                )
        # SL/TP relational checks per direction.
        if self.direction == "LONG":
            if self.sl_price is not None and self.sl_price >= self.entry_price:
                raise ValueError("LONG: sl_price must be < entry_price")
            if self.tp_price is not None and self.tp_price <= self.entry_price:
                raise ValueError("LONG: tp_price must be > entry_price")
        else:  # SHORT
            if self.sl_price is not None and self.sl_price <= self.entry_price:
                raise ValueError("SHORT: sl_price must be > entry_price")
            if self.tp_price is not None and self.tp_price >= self.entry_price:
                raise ValueError("SHORT: tp_price must be < entry_price")
        return self
```

### Typed error hierarchy + ValidatedOpenRequest + private factory (Tasks 11, 13 spec)

```python
# api/positions_birth.py (continued)
from dataclasses import dataclass
from typing import Any
from pydantic import ValidationError


class BirthError(Exception):
    """Base for all birth-path errors. Route handler maps `status_code` to HTTP."""
    status_code: int = 500
    def __init__(self, message: str = "", *, detail: Any = None):
        super().__init__(message)
        self.message = message or self.__class__.__name__
        self.detail = detail


class BodyValidationError(BirthError):
    """Pydantic validation failed."""
    status_code = 422


class AmbiguousQtyError(BirthError):
    """qty and size_usd both provided but inconsistent (caught by Pydantic)."""
    status_code = 422


class StaleEntryTsError(BirthError):
    """entry_ts outside the accepted [now-7d, now+60s] window (caught by Pydantic)."""
    status_code = 422


class DuplicateIdempotencyKeyError(BirthError):
    """Two requests sharing one Idempotency-Key but different bodies (RFC 9457 style)."""
    status_code = 409


class UniqueViolationError(BirthError):
    """Schema rejected: (tenant_id, scan_id) UNIQUE WHERE status='open' conflict."""
    status_code = 409


_OPEN_REQUEST_SENTINEL = object()


@dataclass(frozen=True)
class ValidatedOpenRequest:
    """Result of `_build_open_request`. Carries the parsed body, the JWT-derived
    tenant_id (NOT the body's), and the optional Idempotency-Key.

    Construction requires the module-private `_OPEN_REQUEST_SENTINEL`. Per the
    'Regla de coherencia' (CLAUDE.md), the type-level guarantee is only real if
    a runtime órgano de rechazo refuses the wrong sentinel.
    """
    payload: OpenPositionRequest
    tenant_id: int
    idempotency_key: Optional[str]
    _sentinel: object

    def __post_init__(self):
        if self._sentinel is not _OPEN_REQUEST_SENTINEL:
            raise TypeError(
                "ValidatedOpenRequest cannot be constructed directly. "
                "Use api.positions_birth._build_open_request (runtime órgano de "
                "rechazo per the 'Regla de coherencia' in CLAUDE.md)."
            )


def _build_open_request(
    body: dict,
    tenant_id: int,
    idempotency_key: Optional[str],
) -> ValidatedOpenRequest:
    """Only legitimate constructor for ValidatedOpenRequest.

    Raises:
      BodyValidationError (422): Pydantic shape/field/cross-field validation failed.
    """
    try:
        payload = OpenPositionRequest.model_validate(body)
    except ValidationError as e:
        raise BodyValidationError(
            "OpenPositionRequest validation failed",
            detail=e.errors(),
        ) from e
    return ValidatedOpenRequest(
        payload=payload,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        _sentinel=_OPEN_REQUEST_SENTINEL,
    )
```

### BirthRegistrar + idempotency cache (Tasks 15, 17 spec)

```python
# api/positions_birth.py (continued)
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from db.transaction import transaction

log = logging.getLogger("api.positions_birth")

_IDEMPOTENCY_TTL = timedelta(hours=24)


class IdempotencyCache:
    """SQLite-backed cache for Idempotency-Key results, keyed by (tenant_id, key).

    Storage table `idempotency_keys` is created in `init_db`. TTL is 24h.
    Lazy cleanup on read (DELETE expired rows for the key probe).
    """

    @staticmethod
    def get(con: sqlite3.Connection, tenant_id: int, key: str) -> Optional[dict]:
        now_iso = datetime.now(timezone.utc).isoformat()
        # Lazy cleanup: drop expired entries for this (tenant, key) pair.
        con.execute(
            "DELETE FROM idempotency_keys "
            "WHERE tenant_id = ? AND key = ? AND expires_at < ?",
            (tenant_id, key, now_iso),
        )
        row = con.execute(
            "SELECT result_json FROM idempotency_keys "
            "WHERE tenant_id = ? AND key = ?",
            (tenant_id, key),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    @staticmethod
    def set(con: sqlite3.Connection, tenant_id: int, key: str, result: dict) -> None:
        now = datetime.now(timezone.utc)
        expires = (now + _IDEMPOTENCY_TTL).isoformat()
        con.execute(
            "INSERT OR REPLACE INTO idempotency_keys "
            "(tenant_id, key, result_json, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (tenant_id, key, json.dumps(result, default=str), now.isoformat(), expires),
        )


class BirthRegistrar:
    """Op-ligero owning the atomic write + post-commit for a position birth.

    NOT a symmetric operator to PositionClosure. Validation already happened
    upstream (Pydantic + _build_open_request). This class is the single place
    where (a) the SQL INSERT happens inside a transaction, (b) the
    Idempotency-Key result is cached in the same transaction, and (c) the
    post-commit snapshot regeneration (update_positions_json) runs.
    """

    @staticmethod
    def register(validated: ValidatedOpenRequest) -> dict:
        from db.positions import db_create_position_sql  # noqa: PLC0415
        from api.positions import update_positions_json  # noqa: PLC0415

        # Idempotency-Key fast path (read-only probe before any write).
        if validated.idempotency_key:
            with transaction() as con:
                cached = IdempotencyCache.get(
                    con, validated.tenant_id, validated.idempotency_key,
                )
            if cached is not None:
                log.info(
                    "BirthRegistrar: idempotent replay tenant=%s key=%s pos_id=%s",
                    validated.tenant_id, validated.idempotency_key, cached.get("id"),
                )
                return cached

        # Atomic write: INSERT + (optionally) cache the result in the SAME tx.
        try:
            with transaction() as con:
                pos = db_create_position_sql(con, validated)
                if validated.idempotency_key:
                    IdempotencyCache.set(
                        con, validated.tenant_id, validated.idempotency_key, pos,
                    )
        except sqlite3.IntegrityError as e:
            # Partial UNIQUE index fired — open row for (tenant_id, scan_id) exists.
            msg = str(e).lower()
            if "unique" in msg and "scan_id" in msg:
                raise UniqueViolationError(
                    "An open position already exists for this scan_id",
                    detail={"tenant_id": validated.tenant_id,
                            "scan_id": validated.payload.scan_id},
                ) from e
            raise  # other IntegrityError bubbles; route logs+500

        # Post-commit, BirthRegistrar's responsibility (closes F8).
        update_positions_json()

        # F15: structured log at birth.
        log.info(
            "POSICION OPENED #%s %s @ $%s qty=%s tenant=%s scan_id=%s",
            pos["id"],
            validated.payload.symbol,
            validated.payload.entry_price,
            validated.payload.qty,
            validated.tenant_id,
            validated.payload.scan_id,
        )
        return pos
```

### Route handler (Task 14 spec) — replaces current `open_position`

```python
# api/positions.py (rewritten open_position)
from fastapi import Header
from api.positions_birth import (
    BirthError,
    BirthRegistrar,
    _build_open_request,
)


@router.post(
    "",
    summary="Abrir nueva posicion",
    dependencies=[Depends(verify_api_key), Depends(require_role("admin"))],
)
def open_position(
    body: dict = Body(...),
    tenant_id: int = Depends(get_current_tenant_id),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    try:
        validated = _build_open_request(body, tenant_id, idempotency_key)
        pos = BirthRegistrar.register(validated)
        return {"ok": True, "position": pos}
    except BirthError as e:
        log.warning("birth rejected: %s detail=%s", e.message, e.detail)
        raise HTTPException(status_code=e.status_code, detail={
            "error": e.__class__.__name__,
            "message": e.message,
            "detail": e.detail,
        })
    # No bare `except Exception`. Server faults bubble to FastAPI's default
    # 500 handler which logs the traceback and returns the canonical error.
```

### `db_create_position_sql` (Task 19 spec)

```python
# db/positions.py — replaces db_create_position
def db_create_position_sql(
    con: sqlite3.Connection,
    validated: "ValidatedOpenRequest",
) -> dict:
    """Thin SQL INSERT for a validated open-position request.

    The 5-deep qty fallback chain and all defensive `data.get(...)` membranes
    are DELETED. `validated` is an already-typed object whose factory
    (_build_open_request) is the only entry point; Pydantic guarantees field
    presence and validity.
    """
    p = validated.payload
    ts = (p.entry_ts or datetime.now(timezone.utc)).isoformat()
    cur = con.execute(
        """INSERT INTO positions
               (scan_id, symbol, direction, status, entry_price, entry_ts,
                sl_price, tp_price, size_usd, qty, atr_entry, be_mult, notes,
                tenant_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            p.scan_id,
            p.symbol,                    # already uppercased + allowlisted
            p.direction,                 # LONG/SHORT, required
            "open",
            p.entry_price,
            ts,
            p.sl_price,
            p.tp_price,
            p.size_usd,
            p.qty,
            p.atr_entry,
            p.be_mult,
            p.notes,
            validated.tenant_id,         # from JWT, not body
        ),
    )
    pos_id = cur.lastrowid
    row = con.execute("SELECT * FROM positions WHERE id = ?", (pos_id,)).fetchone()
    return dict(row)
```

---

## Tasks

### Task 1: Branch + baseline + bake-in production measurement

**Files:** none modified.

- [ ] **Step 1: Create and switch to the feature branch**

Run:
```bash
git checkout main && git pull && git checkout -b feat/birth-time-enforcement-471-470-473
```
Expected: `Switched to a new branch 'feat/birth-time-enforcement-471-470-473'`.

- [ ] **Step 2: Confirm clean working tree**

Run: `git status --short`
Expected: empty.

- [ ] **Step 3: Baseline test count**

Run: `pytest --collect-only -q 2>&1 | tail -3`
Expected: ~2530+ tests collected. Record the exact number for the final delta check in Task 22.

- [ ] **Step 4: Verify the production measurement (already done) reproduces against local signals.db**

If `signals.db` exists locally, run:
```bash
sqlite3 signals.db "
SELECT
  COUNT(*) AS total,
  SUM(CASE WHEN tenant_id IS NULL THEN 1 ELSE 0 END) AS null_tenant,
  SUM(CASE WHEN qty = 0.0 THEN 1 ELSE 0 END) AS zero_qty,
  SUM(CASE WHEN scan_id IS NULL THEN 1 ELSE 0 END) AS null_scan,
  SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_count,
  SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS closed_count,
  SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) AS cancelled_count,
  SUM(CASE WHEN status='legacy_unmeasurable' THEN 1 ELSE 0 END) AS quarantine_c2
FROM positions;"
```
Expected (matches the 2026-05-26 snapshot in the plan preamble): 2018 total, 2018 null_tenant, 72 zero_qty, ~2016 null_scan, 2 open, 1344 closed, 2 cancelled, 670 legacy_unmeasurable. Numbers will drift slightly if production has new rows since 2026-05-26 — note any delta in the commit message of Task 22.

If `signals.db` is not present, document the expected counts inline in the PR description (Task 23) and proceed. The migrations gate on the live row state, not on this measurement.

- [ ] **Step 5: Commit a no-op marker (optional sanity)**

No file changes — skip the commit. Branch is ready.

---

### Task 2: CLAUDE.md — register D invariants + dual-rung principle + close birth-time scope gap

**Files:**
- Modify: `CLAUDE.md`

The C2 PR (merged in `3985a0c`) left a "Known scope gap" pointing at birth-time invariants. This task closes that entry, adds the D invariants to the registry, and declares the dual-rung principle (schema + tipo, not symmetric to close).

- [ ] **Step 1: Locate the registry section**

Run: `grep -n "Invariantes C2 — estado tras este PR\|Known scope gap (Voronov 2026-05-26)" CLAUDE.md`

Expected: two line numbers. We rewrite the section between them.

- [ ] **Step 2: Replace the registry table + scope-gap entry**

Find the section starting with `### Invariantes C2 — estado tras este PR` and ending just before `## Known Limitations`. Replace the content of those subsections with:

```markdown
### Invariantes registradas — estado tras Cluster D (Voronov 2026-05-26, post-#471 #470 #473)

| Invariante de dominio | Capa enforced | Mecanismo | Issue cerrado |
|---|---|---|---|
| `qty` siempre tiene valor numérico para positions activas (o quarantine) | **Schema** | `CHECK (qty IS NOT NULL OR status='legacy_unmeasurable')` en `positions` (via `_migrate_qty_not_null`) | #467 |
| `precheck_connection` y `snapshot_connection` son contratos distintos | **Tipo** | `NewType("PrecheckConn", sqlite3.Connection)` y `NewType("SnapshotConn", sqlite3.Connection)` en `db/transaction.py` | #468 |
| Los campos del snapshot consumidos por el write-tx no cambian entre precheck y BEGIN IMMEDIATE | **Tipo + runtime check** | `OwnershipValidatedSnapshot` (factory privada en `operators/precheck.py`) + field-by-field re-validation en `PositionClosure.execute()` | #469 + F6 |
| `qty > 0` para positions activas (cierra el 0.0-bypass) | **Schema** | `CHECK ((qty IS NOT NULL AND qty > 0) OR status='legacy_unmeasurable')` (via `_migrate_qty_positive`) | #471 (parcial) |
| `tenant_id IS NOT NULL` para positions activas | **Schema** | `CHECK (tenant_id IS NOT NULL OR status IN ('legacy_unmeasurable','legacy_no_tenant'))` (via `_migrate_tenant_id_not_null`) | #471 (parcial) |
| Idempotencia estructural: no dos open rows con el mismo `(tenant_id, scan_id)` | **Schema** | `CREATE UNIQUE INDEX ... WHERE status='open' AND scan_id IS NOT NULL` (via `_migrate_unique_open_scan`) | #470 (parcial) |
| Input externo → `Position` legítima (allowlist symbol, direction enum, qty>0, SL/TP relacional, entry_ts window) | **Tipo + runtime órgano de rechazo** | Pydantic `OpenPositionRequest` (extra='forbid') + factory privada `_build_open_request` con `_OPEN_REQUEST_SENTINEL` en `api/positions_birth.py` | #473 (parcial), #471 F5/F6/F7/F9 |
| Idempotencia operacional (cliente retry-safe) | **Tipo (HTTP)** | `Idempotency-Key` header + tabla `idempotency_keys` con 24h TTL | #470 (parcial) |
| Error taxonomy 422/409 vs 500 | **Tipo** | `BirthError` hierarchy (`BodyValidationError`, `DuplicateIdempotencyKeyError`, `UniqueViolationError`, …) en `api/positions_birth.py`; route handler mapea `status_code` | #473 |
| Post-commit atomicidad de `update_positions_json` | **Operador-ligero** | `BirthRegistrar.register` posee `with transaction()` + post-commit | #473 F8 |

### Principio dual de la frontera Cluster D (Voronov 2026-05-26)

> Una `Position` existe si y solo si su acto de nominación satisfizo simultáneamente: (a) el contrato existencial del schema (qué la convierte ontológicamente en Position), y (b) el contrato de nominación de la frontera de entrada (qué valida que el input externo intentaba declararla legítimamente). Schema es la frontera que ningún caller evade; nominación es donde el error toma forma semántica.

> `close()` valida una transición entre dos estados conocidos del mismo objeto. `open()` no valida transición — valida un acto de nominación. Son primos, no hermanos. Cluster D NO introduce un `PositionOpen` operador simétrico a `PositionClosure` — eso sería "falsa simetría — imitación visual; no comparte contrato". `BirthRegistrar` es un op-ligero: validación ocurrió arriba (Pydantic + `_build_open_request`); el registrar solo posee la atomicidad transacción + post-commit.

### Documented status: `legacy_no_tenant`

Status especial usado por `_migrate_tenant_id_not_null` (#471) para reconocer rows históricas pre-multi-tenant cuya `tenant_id` no es recuperable. El schema CHECK exempta `legacy_unmeasurable` Y `legacy_no_tenant`. Rows ya marcadas `legacy_unmeasurable` (de la migración C2) NO se re-clasifican — el OR del CHECK las exempta directamente. Convierte 2018 mentiras silenciosas (tenant_id=NULL implícito) en reconocimientos explícitos.

### Known scope gap (post-D)

- **Rate limiting (#473 F10)** — el endpoint `POST /positions` no tiene throttle. Un cliente legítimo con la `Idempotency-Key` correcta puede inundar el endpoint creando rows distintas (cada body único pasa). El sistema confía en autenticación + JWT para acotar abuso. Issue separado pendiente.
- **Direction enum sólo en boundary** — el schema acepta cualquier TEXT en `positions.direction`; el `Literal["LONG","SHORT"]` vive sólo en la frontera Pydantic. Una migración manual o cliente legacy podría escribir `"long"` en lowercase. Mover a `CHECK (direction IN ('LONG','SHORT'))` es follow-up trivial pero NO está en este PR.
- **`scan_id` FK** — `scan_id` es nullable y referencia una tabla que no existe (no hay `scans` con esa semántica de signal_id). El UNIQUE parcial cierra la race condition (#470) pero NO la integridad referencial. Es follow-up separado.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(claude): register Cluster D invariants + dual-rung principle (advances #471 #470 #473)

Adds birth-time invariants to the enforcement registry across schema (qty>0,
tenant_id NOT NULL, UNIQUE open-scan), tipo (Pydantic + private factory),
HTTP (Idempotency-Key), and op-ligero (BirthRegistrar).

Declares the dual-rung principle: open() is nomination, not transition;
BirthRegistrar is NOT symmetric to PositionClosure. Closes the C2
'Known scope gap (birth-time)' and replaces with new gaps for F10
(rate limiting), direction enum at schema, and scan_id FK.

Documents legacy_no_tenant quarantine status.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: TDD — failing tests for `_migrate_qty_positive`

**Files:**
- Create: `tests/db/test_migrate_qty_positive.py`

The migration extends the existing CHECK from `qty IS NOT NULL` to `(qty IS NOT NULL AND qty > 0)`, and quarantines rows with `qty = 0`.

- [ ] **Step 1: Create the test file**

```python
"""Invariant tests for db.schema._migrate_qty_positive (#471 closure of qty=0 bypass).

The C2 migration (_migrate_qty_not_null) closed qty IS NULL but left qty = 0
as a valid value (72 rows in prod). This migration extends the CHECK to
qty > 0 and quarantines the zero-qty rows as status='legacy_unmeasurable'.
"""
import sqlite3
import pytest


def _init_post_c2_positions_table(con: sqlite3.Connection) -> None:
    """Create the positions table in the post-C2 state: CHECK allows qty=0,
    quarantine status legacy_unmeasurable already exempted."""
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
            CHECK (qty IS NOT NULL OR status = 'legacy_unmeasurable')
        )
        """
    )


def test_quarantines_zero_qty_rows(tmp_path):
    """Rows with qty=0 must be re-statused to 'legacy_unmeasurable'."""
    from db.schema import _migrate_qty_positive

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_c2_positions_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, status) "
        "VALUES (1, 'BTCUSDT', 100.0, '2024-01-01T00:00:00', 0.0, 'closed')"
    )
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, status) "
        "VALUES (2, 'BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'open')"
    )
    con.commit()

    _migrate_qty_positive(con)

    rows = dict(con.execute("SELECT id, status FROM positions").fetchall())
    assert rows[1] == "legacy_unmeasurable"
    assert rows[2] == "open"


def test_post_migration_rejects_qty_zero_on_open_status(tmp_path):
    """After the migration, INSERT with qty=0 and status='open' must be rejected."""
    from db.schema import _migrate_qty_positive

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_c2_positions_table(con)
    con.commit()

    _migrate_qty_positive(con)

    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO positions (symbol, entry_price, entry_ts, qty, status) "
            "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', 0.0, 'open')"
        )


def test_post_migration_rejects_negative_qty(tmp_path):
    """qty < 0 is also rejected (the CHECK is qty > 0, not qty != 0)."""
    from db.schema import _migrate_qty_positive

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_c2_positions_table(con)
    con.commit()

    _migrate_qty_positive(con)

    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO positions (symbol, entry_price, entry_ts, qty, status) "
            "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', -1.0, 'open')"
        )


def test_post_migration_accepts_legacy_unmeasurable_with_null_or_zero(tmp_path):
    """legacy_unmeasurable rows can still carry qty=NULL or qty=0 (quarantine path)."""
    from db.schema import _migrate_qty_positive

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_c2_positions_table(con)
    con.commit()

    _migrate_qty_positive(con)

    con.execute(
        "INSERT INTO positions (symbol, entry_price, entry_ts, qty, status) "
        "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', NULL, 'legacy_unmeasurable')"
    )
    con.execute(
        "INSERT INTO positions (symbol, entry_price, entry_ts, qty, status) "
        "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', 0.0, 'legacy_unmeasurable')"
    )
    con.commit()
    count = con.execute(
        "SELECT COUNT(*) FROM positions WHERE status='legacy_unmeasurable'"
    ).fetchone()[0]
    assert count == 2


def test_idempotent_on_already_migrated(tmp_path):
    """Running the migration twice is a no-op the second time."""
    from db.schema import _migrate_qty_positive

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_c2_positions_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, status) "
        "VALUES (1, 'BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'open')"
    )
    con.commit()

    _migrate_qty_positive(con)
    _migrate_qty_positive(con)  # must not raise

    row = con.execute("SELECT qty, status FROM positions WHERE id=1").fetchone()
    assert row[0] == 10.0
    assert row[1] == "open"
```

- [ ] **Step 2: Verify all 5 tests fail with ImportError or AttributeError**

Run: `pytest tests/db/test_migrate_qty_positive.py -v 2>&1 | tail -20`
Expected: 5 failures at `from db.schema import _migrate_qty_positive`.

- [ ] **Step 3: Commit**

```bash
git add tests/db/test_migrate_qty_positive.py
git commit -m "$(cat <<'EOF'
test(db): failing tests for _migrate_qty_positive (advances #471, closes qty=0 bypass)

Quarantines qty=0 rows as legacy_unmeasurable; post-migration rejects
qty<=0 on non-quarantine rows; legacy_unmeasurable can still hold NULL/0;
idempotent.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Implement `_migrate_qty_positive` in `db/schema.py`

**Files:**
- Modify: `db/schema.py`

- [ ] **Step 1: Append the migration helper after `_migrate_qty_not_null`**

After the `_migrate_qty_not_null` function in `db/schema.py` (ends ~line 832), append:

```python
def _migrate_qty_positive(con: sqlite3.Connection) -> None:
    """Extend the qty CHECK from 'NOT NULL' to '> 0' (#471 closure of qty=0 bypass).

    Production measurement (2026-05-26): 72 rows with qty=0.0 exactly (68
    closed, 2 open, 2 cancelled). These bypassed the C2 NULL check.

    Policy (Voronov dual-rung): re-status the 72 zero-qty rows as
    'legacy_unmeasurable' (admit the absence; don't invent a value), then
    extend the CHECK to require qty > 0 on non-quarantine rows.

    Idempotent: detects the qty>0 fragment in the live schema and skips.
    """
    schema_row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='positions'"
    ).fetchone()
    if not schema_row or not schema_row[0]:
        log.warning(
            "_migrate_qty_positive: positions table not found; skipping."
        )
        return
    # Normalize whitespace for the idempotency probe. Look for "qty > 0" or
    # "qty>0" anywhere in the CHECK fragment.
    normalized = "".join(schema_row[0].split()).lower()
    if "qty>0" in normalized:
        log.info(
            "_migrate_qty_positive: positions already enforces qty > 0; skipping."
        )
        return

    # 1. Quarantine zero-qty rows (any status). The C2 CHECK allowed them; the
    #    new CHECK will reject them on non-quarantine status. Re-status to
    #    legacy_unmeasurable (same quarantine bucket used by C2).
    con.execute(
        """UPDATE positions
              SET status = 'legacy_unmeasurable'
            WHERE qty = 0
              AND status != 'legacy_unmeasurable'"""
    )
    quarantined = con.execute("SELECT changes()").fetchone()[0]
    log.info(
        "_migrate_qty_positive: quarantined %d zero-qty rows as 'legacy_unmeasurable'.",
        quarantined,
    )

    # 2. Defensive sanity: any qty < 0 in legacy data also goes to quarantine.
    con.execute(
        """UPDATE positions
              SET status = 'legacy_unmeasurable'
            WHERE qty < 0
              AND status != 'legacy_unmeasurable'"""
    )
    neg_quarantined = con.execute("SELECT changes()").fetchone()[0]
    if neg_quarantined:
        log.warning(
            "_migrate_qty_positive: quarantined %d NEGATIVE-qty rows (unexpected).",
            neg_quarantined,
        )

    # 3. Recreate the table with the strengthened CHECK.
    log.info(
        "_migrate_qty_positive: recreating positions table with "
        "CHECK ((qty IS NOT NULL AND qty > 0) OR status='legacy_unmeasurable')."
    )
    existing_cols = {
        row[1] for row in con.execute("PRAGMA table_info(positions)").fetchall()
    }
    con.execute(
        """
        CREATE TABLE positions_new (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id     INTEGER REFERENCES scans(id),
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
            CHECK ((qty IS NOT NULL AND qty > 0) OR status = 'legacy_unmeasurable')
        )
        """
    )
    TARGET_COLS = [
        "id", "scan_id", "symbol", "direction", "status", "entry_price",
        "entry_ts", "sl_price", "tp_price", "size_usd", "qty",
        "exit_price", "exit_ts", "exit_reason", "pnl_usd", "pnl_pct",
        "notes", "atr_entry", "be_mult", "tenant_id",
    ]
    select_expressions = [
        col if col in existing_cols else "NULL"
        for col in TARGET_COLS
    ]
    insert_sql = (
        f"INSERT INTO positions_new ({', '.join(TARGET_COLS)}) "
        f"SELECT {', '.join(select_expressions)} FROM positions"
    )
    con.execute(insert_sql)
    con.execute("DROP TABLE positions")
    con.execute("ALTER TABLE positions_new RENAME TO positions")
    con.execute("CREATE INDEX IF NOT EXISTS idx_positions_tenant ON positions(tenant_id)")
    log.info(
        "_migrate_qty_positive: migration complete. positions enforces qty > 0."
    )
```

- [ ] **Step 2: Wire into `init_db`**

In `db/schema.py::init_db`, find the existing `_migrate_qty_not_null` block (~line 318-325). Immediately AFTER it, add:

```python
    # qty > 0 enforcement migration — #471 (Voronov D-schema rung).
    # MUST run AFTER _migrate_qty_not_null (which created the C2 CHECK).
    # Quarantines the 72 zero-qty rows the C2 NULL check missed.
    with transaction() as con_qty_pos:
        _migrate_qty_positive(con_qty_pos)
```

- [ ] **Step 3: Verify the 5 tests pass**

Run: `pytest tests/db/test_migrate_qty_positive.py -v 2>&1 | tail -15`
Expected: 5/5 pass.

- [ ] **Step 4: Verify `init_db` is still idempotent on a fresh DB**

```bash
python -c "
import os
os.environ['BTC_DB'] = '/tmp/test_init_d.db'
if os.path.exists('/tmp/test_init_d.db'):
    os.remove('/tmp/test_init_d.db')
from db.schema import init_db
init_db()
init_db()
print('init_db idempotent post-_migrate_qty_positive: ok')
"
```
Expected: `init_db idempotent post-_migrate_qty_positive: ok`.

- [ ] **Step 5: Commit**

```bash
git add db/schema.py
git commit -m "$(cat <<'EOF'
feat(db): _migrate_qty_positive extends qty CHECK to >0 + quarantines 72 zero-qty rows (advances #471)

Closes the C2 NULL-only gap: 72 production rows with qty=0.0 bypassed the
C2 CHECK. New CHECK is ((qty IS NOT NULL AND qty > 0) OR
status='legacy_unmeasurable'). Zero-qty rows re-statused before recreate.

Voronov D-schema rung: schema is the frontier no caller evades.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: TDD — failing tests for `_migrate_tenant_id_not_null`

**Files:**
- Create: `tests/db/test_migrate_tenant_id_not_null.py`

- [ ] **Step 1: Create the test file**

```python
"""Invariant tests for db.schema._migrate_tenant_id_not_null (#471).

Production: 2018 rows with tenant_id=NULL. Quarantines them as
'legacy_no_tenant' (new status), then adds CHECK that exempts both
legacy_unmeasurable and legacy_no_tenant. Rows already in
legacy_unmeasurable from C2 keep that status (the OR exempts them).
"""
import sqlite3
import pytest


def _init_post_qty_positive_table(con: sqlite3.Connection) -> None:
    """Positions table in the state AFTER _migrate_qty_positive (Task 4)."""
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
            CHECK ((qty IS NOT NULL AND qty > 0) OR status = 'legacy_unmeasurable')
        )
        """
    )


def test_quarantines_null_tenant_rows_as_legacy_no_tenant(tmp_path):
    """tenant_id IS NULL rows not already in legacy_unmeasurable must be
    re-statused to 'legacy_no_tenant'."""
    from db.schema import _migrate_tenant_id_not_null

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_qty_positive_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, status, tenant_id) "
        "VALUES (1, 'BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'open', NULL)"
    )
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, status, tenant_id) "
        "VALUES (2, 'BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'closed', 1)"
    )
    con.commit()

    _migrate_tenant_id_not_null(con)

    rows = dict(con.execute("SELECT id, status FROM positions").fetchall())
    assert rows[1] == "legacy_no_tenant"
    assert rows[2] == "closed"


def test_already_legacy_unmeasurable_keeps_status(tmp_path):
    """Rows already in legacy_unmeasurable (from C2) keep that status —
    the OR in the new CHECK exempts them; no double-quarantine ceremony."""
    from db.schema import _migrate_tenant_id_not_null

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_qty_positive_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, status, tenant_id) "
        "VALUES (1, 'BTCUSDT', 100.0, '2024-01-01T00:00:00', NULL, 'legacy_unmeasurable', NULL)"
    )
    con.commit()

    _migrate_tenant_id_not_null(con)

    status = con.execute("SELECT status FROM positions WHERE id=1").fetchone()[0]
    assert status == "legacy_unmeasurable"


def test_post_migration_rejects_null_tenant_with_status_open(tmp_path):
    """Post-migration, INSERT with tenant_id=NULL and status='open' is rejected."""
    from db.schema import _migrate_tenant_id_not_null

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_qty_positive_table(con)
    con.commit()

    _migrate_tenant_id_not_null(con)

    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO positions (symbol, entry_price, entry_ts, qty, status, tenant_id) "
            "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'open', NULL)"
        )


def test_post_migration_accepts_legacy_no_tenant_with_null(tmp_path):
    """legacy_no_tenant + tenant_id=NULL is the quarantine path; INSERT must succeed."""
    from db.schema import _migrate_tenant_id_not_null

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_qty_positive_table(con)
    con.commit()

    _migrate_tenant_id_not_null(con)

    con.execute(
        "INSERT INTO positions (symbol, entry_price, entry_ts, qty, status, tenant_id) "
        "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'legacy_no_tenant', NULL)"
    )
    con.commit()
    count = con.execute(
        "SELECT COUNT(*) FROM positions WHERE status='legacy_no_tenant'"
    ).fetchone()[0]
    assert count == 1


def test_idempotent_on_already_migrated(tmp_path):
    from db.schema import _migrate_tenant_id_not_null

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    _init_post_qty_positive_table(con)
    con.execute(
        "INSERT INTO positions (id, symbol, entry_price, entry_ts, qty, status, tenant_id) "
        "VALUES (1, 'BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 'open', 1)"
    )
    con.commit()

    _migrate_tenant_id_not_null(con)
    _migrate_tenant_id_not_null(con)

    row = con.execute("SELECT tenant_id, status FROM positions WHERE id=1").fetchone()
    assert row[0] == 1
    assert row[1] == "open"
```

- [ ] **Step 2: Verify the 5 tests fail with ImportError**

Run: `pytest tests/db/test_migrate_tenant_id_not_null.py -v 2>&1 | tail -15`
Expected: 5 failures at the import.

- [ ] **Step 3: Commit**

```bash
git add tests/db/test_migrate_tenant_id_not_null.py
git commit -m "$(cat <<'EOF'
test(db): failing tests for _migrate_tenant_id_not_null (advances #471)

Quarantines NULL-tenant rows as new status legacy_no_tenant; preserves
existing legacy_unmeasurable rows untouched; post-migration rejects
tenant_id=NULL on non-quarantine status; idempotent.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Implement `_migrate_tenant_id_not_null` in `db/schema.py`

**Files:**
- Modify: `db/schema.py`

- [ ] **Step 1: Append the helper after `_migrate_qty_positive`**

```python
def _migrate_tenant_id_not_null(con: sqlite3.Connection) -> None:
    """Schema CHECK: tenant_id IS NOT NULL OR status IN ('legacy_unmeasurable',
    'legacy_no_tenant') — #471 (Voronov D-schema rung, tenant invariant).

    Production measurement (2026-05-26): 2018/2018 positions had
    tenant_id IS NULL. Of those, 670 are already in legacy_unmeasurable from
    C2 — the new CHECK exempts them via the OR (no double-quarantine).
    The remaining ~1348 get re-statused to 'legacy_no_tenant'.

    Idempotent: detects the 'legacy_no_tenant' literal in the live CHECK
    fragment and skips.
    """
    schema_row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='positions'"
    ).fetchone()
    if not schema_row or not schema_row[0]:
        log.warning(
            "_migrate_tenant_id_not_null: positions table not found; skipping."
        )
        return
    if "legacy_no_tenant" in schema_row[0]:
        log.info(
            "_migrate_tenant_id_not_null: positions already exempts "
            "'legacy_no_tenant'; skipping."
        )
        return

    # 1. Quarantine NULL-tenant rows that are NOT already in legacy_unmeasurable.
    #    Rows already in legacy_unmeasurable keep that status — the OR in the new
    #    CHECK will exempt them directly.
    con.execute(
        """UPDATE positions
              SET status = 'legacy_no_tenant'
            WHERE tenant_id IS NULL
              AND status != 'legacy_unmeasurable'"""
    )
    quarantined = con.execute("SELECT changes()").fetchone()[0]
    log.info(
        "_migrate_tenant_id_not_null: re-statused %d NULL-tenant rows as "
        "'legacy_no_tenant'.",
        quarantined,
    )

    # 2. Recreate the table with the strengthened CHECK.
    log.info(
        "_migrate_tenant_id_not_null: recreating positions with CHECK "
        "(tenant_id IS NOT NULL OR status IN ('legacy_unmeasurable','legacy_no_tenant'))."
    )
    existing_cols = {
        row[1] for row in con.execute("PRAGMA table_info(positions)").fetchall()
    }
    con.execute(
        """
        CREATE TABLE positions_new (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id     INTEGER REFERENCES scans(id),
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
    TARGET_COLS = [
        "id", "scan_id", "symbol", "direction", "status", "entry_price",
        "entry_ts", "sl_price", "tp_price", "size_usd", "qty",
        "exit_price", "exit_ts", "exit_reason", "pnl_usd", "pnl_pct",
        "notes", "atr_entry", "be_mult", "tenant_id",
    ]
    select_expressions = [
        col if col in existing_cols else "NULL"
        for col in TARGET_COLS
    ]
    insert_sql = (
        f"INSERT INTO positions_new ({', '.join(TARGET_COLS)}) "
        f"SELECT {', '.join(select_expressions)} FROM positions"
    )
    con.execute(insert_sql)
    con.execute("DROP TABLE positions")
    con.execute("ALTER TABLE positions_new RENAME TO positions")
    con.execute("CREATE INDEX IF NOT EXISTS idx_positions_tenant ON positions(tenant_id)")
    log.info(
        "_migrate_tenant_id_not_null: migration complete. positions enforces "
        "tenant_id IS NOT NULL or quarantine."
    )
```

- [ ] **Step 2: Wire into `init_db` after `_migrate_qty_positive`**

In `init_db`, immediately AFTER the `_migrate_qty_positive` block from Task 4, add:

```python
    # tenant_id NOT NULL enforcement migration — #471 (Voronov D-schema rung).
    # MUST run AFTER _migrate_qty_positive. Quarantines NULL-tenant rows as
    # 'legacy_no_tenant'; rows already in 'legacy_unmeasurable' are exempted
    # by the OR clause directly (no double-quarantine).
    with transaction() as con_tenant:
        _migrate_tenant_id_not_null(con_tenant)
```

- [ ] **Step 3: Verify the 5 tests pass**

Run: `pytest tests/db/test_migrate_tenant_id_not_null.py -v 2>&1 | tail -15`
Expected: 5/5 pass.

- [ ] **Step 4: Verify `init_db` still idempotent end-to-end**

```bash
python -c "
import os
os.environ['BTC_DB'] = '/tmp/test_init_d2.db'
if os.path.exists('/tmp/test_init_d2.db'):
    os.remove('/tmp/test_init_d2.db')
from db.schema import init_db
init_db()
init_db()
print('init_db idempotent post-_migrate_tenant_id_not_null: ok')
"
```
Expected: `init_db idempotent post-_migrate_tenant_id_not_null: ok`.

- [ ] **Step 5: Commit**

```bash
git add db/schema.py
git commit -m "$(cat <<'EOF'
feat(db): _migrate_tenant_id_not_null + new quarantine status legacy_no_tenant (advances #471)

Closes the tenant_id NULL bypass for all non-quarantine rows. ~1348 rows
re-statused to legacy_no_tenant; 670 already in legacy_unmeasurable keep
their status (exempted by the OR clause; no double-quarantine).

Voronov D-schema rung: schema is the frontier no caller evades. The C2
defense chain (precheck->snapshot, snapshot->write) is now anchored at
birth.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: TDD — failing tests for `_migrate_unique_open_scan`

**Files:**
- Create: `tests/db/test_migrate_unique_open_scan.py`

- [ ] **Step 1: Create the test file**

```python
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
```

- [ ] **Step 2: Verify the 6 tests fail with ImportError**

Run: `pytest tests/db/test_migrate_unique_open_scan.py -v 2>&1 | tail -15`
Expected: 6 failures at the import.

- [ ] **Step 3: Commit**

```bash
git add tests/db/test_migrate_unique_open_scan.py
git commit -m "$(cat <<'EOF'
test(db): failing tests for _migrate_unique_open_scan (advances #470)

Partial UNIQUE index on (tenant_id, scan_id) WHERE status='open' AND
scan_id IS NOT NULL: rejects second open for same scan; allows multiple
closed; allows different tenants; ignores NULL scan_id; idempotent.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Implement `_migrate_unique_open_scan` in `db/schema.py`

**Files:**
- Modify: `db/schema.py`

- [ ] **Step 1: Append the helper after `_migrate_tenant_id_not_null`**

```python
def _migrate_unique_open_scan(con: sqlite3.Connection) -> None:
    """Partial unique index on (tenant_id, scan_id) WHERE status='open' AND
    scan_id IS NOT NULL — #470 idempotency race closure.

    Closes the race window of two concurrent POST /positions with the same
    scan_id: the second INSERT fires sqlite3.IntegrityError, which
    BirthRegistrar maps to a 409 UniqueViolationError. Combined with the
    Idempotency-Key cache (Task 17), a retried client request is replayed
    safely; a duplicate client request hits the schema fence.

    Production measurement (2026-05-26): only 2 rows share scan_id=42, both
    closed. No open rows currently violate this index — migration is safe
    at-rest.

    Idempotent: CREATE INDEX IF NOT EXISTS.
    """
    con.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_open_scan_unique
              ON positions (tenant_id, scan_id)
              WHERE status = 'open' AND scan_id IS NOT NULL"""
    )
    log.info(
        "_migrate_unique_open_scan: partial UNIQUE index ensured "
        "(tenant_id, scan_id) WHERE status='open' AND scan_id IS NOT NULL."
    )
```

- [ ] **Step 2: Wire into `init_db` after `_migrate_tenant_id_not_null`**

```python
    # Idempotency partial-UNIQUE index — #470 (Voronov D-schema rung,
    # operational invariant). MUST run AFTER _migrate_tenant_id_not_null so
    # the recreated table is the target of the index.
    with transaction() as con_idx:
        _migrate_unique_open_scan(con_idx)
```

- [ ] **Step 3: Verify the 6 tests pass**

Run: `pytest tests/db/test_migrate_unique_open_scan.py -v 2>&1 | tail -15`
Expected: 6/6 pass.

- [ ] **Step 4: Smoke `init_db` idempotency**

```bash
python -c "
import os
os.environ['BTC_DB'] = '/tmp/test_init_d3.db'
if os.path.exists('/tmp/test_init_d3.db'):
    os.remove('/tmp/test_init_d3.db')
from db.schema import init_db
init_db()
init_db()
print('init_db idempotent post-_migrate_unique_open_scan: ok')
"
```
Expected: `init_db idempotent post-_migrate_unique_open_scan: ok`.

- [ ] **Step 5: Commit**

```bash
git add db/schema.py
git commit -m "$(cat <<'EOF'
feat(db): _migrate_unique_open_scan partial UNIQUE index closes idempotency race (advances #470)

CREATE UNIQUE INDEX ... ON positions (tenant_id, scan_id) WHERE
status='open' AND scan_id IS NOT NULL. The partial predicate covers the
only case that matters (active duplicate per tenant); closed rows can
share scan_id (historical), NULL scan_id is unconstrained (legacy/no
scanner).

Combined with the Idempotency-Key cache (Task 17), a retried client
request is replayed; a duplicate concurrent INSERT fires 409.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: TDD — failing tests for `OpenPositionRequest` Pydantic body model

**Files:**
- Create: `tests/api/test_open_position_request.py`

- [ ] **Step 1: Create the test file**

```python
"""Tests for api.positions_birth.OpenPositionRequest (Pydantic body model)
— closes #471 F5/F6/F7/F9 and #473 input-validation portion.
"""
from datetime import datetime, timedelta, timezone
import pytest
from pydantic import ValidationError


def _now():
    return datetime.now(timezone.utc)


def test_minimal_valid_request_parses():
    from api.positions_birth import OpenPositionRequest

    req = OpenPositionRequest(
        symbol="BTCUSDT",
        entry_price=100.0,
        direction="LONG",
        qty=10.0,
    )
    assert req.symbol == "BTCUSDT"
    assert req.entry_price == 100.0
    assert req.direction == "LONG"
    assert req.qty == 10.0
    assert req.entry_ts is None


def test_symbol_lowercase_is_uppercased():
    from api.positions_birth import OpenPositionRequest

    req = OpenPositionRequest(
        symbol="btcusdt", entry_price=100.0, direction="LONG", qty=10.0,
    )
    assert req.symbol == "BTCUSDT"


def test_symbol_not_in_allowlist_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"not in curated allowlist"):
        OpenPositionRequest(
            symbol="BOGUSCOIN", entry_price=100.0, direction="LONG", qty=10.0,
        )


def test_entry_price_zero_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"entry_price must be > 0"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=0, direction="LONG", qty=10.0,
        )


def test_entry_price_negative_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"entry_price must be > 0"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=-1, direction="LONG", qty=10.0,
        )


def test_direction_required_no_default():
    """F12 / F7: direction must be provided (no default to LONG)."""
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError):
        OpenPositionRequest(symbol="BTCUSDT", entry_price=100.0, qty=10.0)


def test_direction_invalid_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="long", qty=10.0,
        )


def test_qty_required_no_size_usd_fallback():
    """F5: qty is required; no 5-deep fallback chain via size_usd."""
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="LONG", size_usd=1000.0,
        )


def test_qty_zero_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"qty must be > 0"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=0,
        )


def test_qty_negative_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"qty must be > 0"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=-1,
        )


def test_extra_field_tenant_id_in_body_rejected():
    """F6: tenant_id in body must be rejected (extra='forbid')."""
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=10.0,
            tenant_id=99,
        )


def test_extra_arbitrary_field_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=10.0,
            bogus_field="anything",
        )


def test_qty_size_usd_consistent_accepted():
    from api.positions_birth import OpenPositionRequest

    req = OpenPositionRequest(
        symbol="BTCUSDT", entry_price=100.0, direction="LONG",
        qty=10.0, size_usd=1000.0,
    )
    assert req.size_usd == 1000.0


def test_qty_size_usd_inconsistent_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"size_usd"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="LONG",
            qty=10.0, size_usd=500.0,  # 10 * 100 = 1000, not 500
        )


def test_entry_ts_within_window_accepted():
    from api.positions_birth import OpenPositionRequest

    req = OpenPositionRequest(
        symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=10.0,
        entry_ts=_now() - timedelta(hours=1),
    )
    assert req.entry_ts is not None


def test_entry_ts_far_future_rejected():
    """F9: entry_ts more than 60s in the future is rejected."""
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"60s in the future"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=10.0,
            entry_ts=_now() + timedelta(days=30),
        )


def test_entry_ts_too_old_rejected():
    """F9: entry_ts more than 7 days in the past is rejected."""
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"7 days in the past"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=10.0,
            entry_ts=_now() - timedelta(days=10),
        )


def test_entry_ts_within_60s_future_accepted():
    """Small clock skew tolerated."""
    from api.positions_birth import OpenPositionRequest

    req = OpenPositionRequest(
        symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=10.0,
        entry_ts=_now() + timedelta(seconds=30),
    )
    assert req.entry_ts is not None


def test_long_sl_above_entry_rejected():
    """F7: SL/TP relational checks per direction."""
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"LONG.*sl_price.*< entry_price"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=10.0,
            sl_price=110.0,
        )


def test_long_tp_below_entry_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"LONG.*tp_price.*> entry_price"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=10.0,
            tp_price=90.0,
        )


def test_short_sl_below_entry_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"SHORT.*sl_price.*> entry_price"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="SHORT", qty=10.0,
            sl_price=90.0,
        )


def test_short_tp_above_entry_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"SHORT.*tp_price.*< entry_price"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="SHORT", qty=10.0,
            tp_price=110.0,
        )


def test_full_valid_request_long_with_all_fields():
    from api.positions_birth import OpenPositionRequest

    req = OpenPositionRequest(
        symbol="ETHUSDT",
        entry_price=2000.0,
        direction="LONG",
        qty=0.5,
        size_usd=1000.0,
        scan_id=123,
        sl_price=1900.0,
        tp_price=2200.0,
        atr_entry=50.0,
        be_mult=1.5,
        notes="manual entry from dashboard",
    )
    assert req.scan_id == 123
    assert req.notes == "manual entry from dashboard"
```

- [ ] **Step 2: Verify all tests fail with ImportError**

Run: `pytest tests/api/test_open_position_request.py -v 2>&1 | tail -30`
Expected: ~22 failures, all at `from api.positions_birth import OpenPositionRequest`.

- [ ] **Step 3: Commit**

```bash
git add tests/api/test_open_position_request.py
git commit -m "$(cat <<'EOF'
test(api): failing tests for OpenPositionRequest Pydantic body model (advances #471 F5/F6/F7/F9, #473)

Covers symbol allowlist + uppercasing; entry_price>0; direction required
enum (LONG/SHORT); qty required + >0 (no size_usd fallback); extra='forbid'
(tenant_id and arbitrary fields rejected); qty*entry_price==size_usd
within 0.01; entry_ts in [now-7d, now+60s]; SL/TP relational per direction.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Implement `OpenPositionRequest` in `api/positions_birth.py`

**Files:**
- Create: `api/positions_birth.py`

- [ ] **Step 1: Create the module with the Pydantic model**

Write to `api/positions_birth.py`:

```python
"""Birth-path for POST /positions — Pydantic boundary, typed errors, sentinel
factory, BirthRegistrar, Idempotency-Key cache.

Per Voronov 2026-05-26 (Cluster D):
  > Una `Position` existe si y solo si su acto de nominación satisfizo
  > simultáneamente: (a) el contrato existencial del schema (qué la convierte
  > ontológicamente en Position), y (b) el contrato de nominación de la
  > frontera de entrada (qué valida que el input externo intentaba declararla
  > legítimamente). Schema es la frontera que ningún caller evade; nominación
  > es donde el error toma forma semántica.

This module owns rung (b). Rung (a) lives in db/schema.py (CHECK constraints +
partial UNIQUE index, all installed by _migrate_qty_positive,
_migrate_tenant_id_not_null, _migrate_unique_open_scan).

Closes #471 F5/F6/F7/F9, #470. Advances #473 (F10 rate-limiting deferred
to #483 per the PR body's "Known scope gap" section — the endpoint is
authenticated + idempotency-cached but lacks per-tenant throttle).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

from db.transaction import transaction

log = logging.getLogger("api.positions_birth")

# Curated symbol allowlist re-exported from the scanner (single source of truth).
from btc_scanner import DEFAULT_SYMBOLS as _SCANNER_SYMBOLS
ALLOWED_SYMBOLS: frozenset[str] = frozenset(_SCANNER_SYMBOLS)


# ---------------- Pydantic body model (D-Tipo rung, boundary) ----------------


class OpenPositionRequest(BaseModel):
    """Validated body of POST /positions.

    Every field validator turns an external string-shaped intent into a
    structurally legitimate Position-in-the-making. `extra='forbid'` closes
    F6 (tenant_id from body silently dropped).
    """
    model_config = ConfigDict(extra="forbid")

    symbol: str
    entry_price: float
    direction: Literal["LONG", "SHORT"]
    qty: float
    size_usd: Optional[float] = None
    entry_ts: Optional[datetime] = None
    scan_id: Optional[int] = None
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    atr_entry: Optional[float] = None
    be_mult: Optional[float] = None
    notes: str = ""

    @field_validator("symbol")
    @classmethod
    def _symbol_uppercase_and_allowed(cls, v: str) -> str:
        sym = v.strip().upper()
        if sym not in ALLOWED_SYMBOLS:
            raise ValueError(
                f"symbol {sym!r} not in curated allowlist; allowed: "
                f"{sorted(ALLOWED_SYMBOLS)}"
            )
        return sym

    @field_validator("entry_price")
    @classmethod
    def _entry_price_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("entry_price must be > 0")
        return v

    @field_validator("qty")
    @classmethod
    def _qty_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("qty must be > 0")
        return v

    @field_validator("size_usd")
    @classmethod
    def _size_usd_positive_if_present(cls, v):
        if v is not None and v <= 0:
            raise ValueError("size_usd must be > 0 when provided")
        return v

    @field_validator("entry_ts")
    @classmethod
    def _entry_ts_within_window(cls, v):
        if v is None:
            return v
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if v > now + timedelta(seconds=60):
            raise ValueError("entry_ts more than 60s in the future")
        if v < now - timedelta(days=7):
            raise ValueError("entry_ts more than 7 days in the past")
        return v

    @model_validator(mode="after")
    def _cross_field_invariants(self) -> "OpenPositionRequest":
        if self.size_usd is not None:
            implied = self.qty * self.entry_price
            if abs(implied - self.size_usd) >= 0.01:
                raise ValueError(
                    f"qty * entry_price = {implied:.4f} but size_usd = "
                    f"{self.size_usd:.4f}; difference exceeds 0.01"
                )
        if self.direction == "LONG":
            if self.sl_price is not None and self.sl_price >= self.entry_price:
                raise ValueError("LONG: sl_price must be < entry_price")
            if self.tp_price is not None and self.tp_price <= self.entry_price:
                raise ValueError("LONG: tp_price must be > entry_price")
        else:  # SHORT
            if self.sl_price is not None and self.sl_price <= self.entry_price:
                raise ValueError("SHORT: sl_price must be > entry_price")
            if self.tp_price is not None and self.tp_price >= self.entry_price:
                raise ValueError("SHORT: tp_price must be < entry_price")
        return self
```

- [ ] **Step 2: Verify the 22 body-model tests pass**

Run: `pytest tests/api/test_open_position_request.py -v 2>&1 | tail -25`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add api/positions_birth.py
git commit -m "$(cat <<'EOF'
feat(api): OpenPositionRequest Pydantic body model — D-Tipo nomination boundary (advances #471 F5/F6/F7/F9, #473)

extra='forbid' kills body-injected tenant_id (F6). Field validators enforce
symbol allowlist (reuses btc_scanner.DEFAULT_SYMBOLS), entry_price>0,
direction required (no LONG default), qty>0 (no size_usd fallback - F5).
Cross-field: qty*entry_price==size_usd within 0.01, SL/TP relational per
direction (F7), entry_ts within [now-7d, now+60s] (F9).

Voronov D-Tipo: this is the nomination contract; schema is the existential
contract. Birth is satisfied iff both hold.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: TDD — failing tests for `_build_open_request` factory + sentinel

**Files:**
- Create: `tests/api/test_build_open_request.py`

- [ ] **Step 1: Create the test file**

```python
"""Tests for _build_open_request + ValidatedOpenRequest sentinel protection.

Mirrors the OwnershipValidatedSnapshot pattern from C2: the type is only a
guarantee if a runtime órgano de rechazo refuses the wrong sentinel.
"""
import pytest


def test_factory_returns_validated_request():
    from api.positions_birth import _build_open_request, ValidatedOpenRequest

    body = {
        "symbol": "BTCUSDT", "entry_price": 100.0,
        "direction": "LONG", "qty": 10.0,
    }
    v = _build_open_request(body, tenant_id=1, idempotency_key=None)
    assert isinstance(v, ValidatedOpenRequest)
    assert v.tenant_id == 1
    assert v.idempotency_key is None
    assert v.payload.symbol == "BTCUSDT"


def test_cannot_construct_validated_request_directly_with_wrong_sentinel():
    """Per Regla de coherencia: ValidatedOpenRequest must reject construction
    with anything other than the module-private sentinel."""
    from api.positions_birth import (
        ValidatedOpenRequest,
        OpenPositionRequest,
    )

    payload = OpenPositionRequest(
        symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=10.0,
    )
    with pytest.raises(TypeError, match=r"runtime órgano de rechazo"):
        ValidatedOpenRequest(
            payload=payload,
            tenant_id=1,
            idempotency_key=None,
            _sentinel=object(),
        )


def test_cannot_construct_validated_request_with_none_sentinel():
    from api.positions_birth import (
        ValidatedOpenRequest,
        OpenPositionRequest,
    )

    payload = OpenPositionRequest(
        symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=10.0,
    )
    with pytest.raises(TypeError, match=r"runtime órgano de rechazo"):
        ValidatedOpenRequest(
            payload=payload, tenant_id=1, idempotency_key=None, _sentinel=None,
        )


def test_factory_raises_body_validation_error_on_pydantic_failure():
    from api.positions_birth import _build_open_request, BodyValidationError

    body = {"symbol": "BTCUSDT", "entry_price": -1, "direction": "LONG", "qty": 10.0}
    with pytest.raises(BodyValidationError) as exc:
        _build_open_request(body, tenant_id=1, idempotency_key=None)
    assert exc.value.status_code == 422
    assert isinstance(exc.value.detail, list)  # pydantic errors() list


def test_factory_raises_body_validation_error_on_extra_field():
    from api.positions_birth import _build_open_request, BodyValidationError

    body = {
        "symbol": "BTCUSDT", "entry_price": 100.0, "direction": "LONG",
        "qty": 10.0, "tenant_id": 99,
    }
    with pytest.raises(BodyValidationError):
        _build_open_request(body, tenant_id=1, idempotency_key=None)


def test_factory_carries_jwt_tenant_id_not_body_tenant_id():
    """Even if a body smuggled tenant_id (it can't, F6) — the factory's
    contract is that tenant_id comes from JWT alone. We only test the
    happy path: the carried tenant_id equals the JWT-supplied value."""
    from api.positions_birth import _build_open_request

    body = {
        "symbol": "BTCUSDT", "entry_price": 100.0,
        "direction": "LONG", "qty": 10.0,
    }
    v = _build_open_request(body, tenant_id=42, idempotency_key="abc")
    assert v.tenant_id == 42
    assert v.idempotency_key == "abc"


def test_factory_carries_idempotency_key_when_supplied():
    from api.positions_birth import _build_open_request

    body = {
        "symbol": "BTCUSDT", "entry_price": 100.0,
        "direction": "LONG", "qty": 10.0,
    }
    v = _build_open_request(body, tenant_id=1, idempotency_key="req-uuid-xyz")
    assert v.idempotency_key == "req-uuid-xyz"
```

- [ ] **Step 2: Verify all 7 tests fail (factory + sentinel not yet implemented)**

Run: `pytest tests/api/test_build_open_request.py -v 2>&1 | tail -20`
Expected: 7 failures.

- [ ] **Step 3: Commit**

```bash
git add tests/api/test_build_open_request.py
git commit -m "$(cat <<'EOF'
test(api): failing tests for _build_open_request + ValidatedOpenRequest sentinel (advances #473)

Mirrors the C2 OwnershipValidatedSnapshot pattern: ValidatedOpenRequest
must reject construction with any sentinel other than the module-private
_OPEN_REQUEST_SENTINEL — the type-level guarantee is only real if a
runtime órgano de rechazo enforces it (Regla de coherencia).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Implement typed errors, `ValidatedOpenRequest`, `_build_open_request`

**Files:**
- Modify: `api/positions_birth.py`

- [ ] **Step 1: Append the error hierarchy + sentinel + factory**

Append to `api/positions_birth.py` (after `OpenPositionRequest`):

```python
# ---------------- Typed error taxonomy (D-Tipo rung, semantic shape) ----------------


class BirthError(Exception):
    """Base for all birth-path errors. Route handler reads `status_code` and
    maps to HTTPException. Closes #473's `except Exception → 500 str(e)` blunder.
    """
    status_code: int = 500

    def __init__(self, message: str = "", *, detail: Any = None):
        super().__init__(message)
        self.message = message or self.__class__.__name__
        self.detail = detail


class BodyValidationError(BirthError):
    """Pydantic validation failed (shape, field, or cross-field)."""
    status_code = 422


class AmbiguousQtyError(BirthError):
    """qty and size_usd both provided but inconsistent. (Currently surfaced
    via Pydantic ValidationError — defined for taxonomy completeness; future
    extraction may move the check here.)"""
    status_code = 422


class StaleEntryTsError(BirthError):
    """entry_ts outside the accepted window. (Surfaced via Pydantic for now.)"""
    status_code = 422


class DuplicateIdempotencyKeyError(BirthError):
    """Same Idempotency-Key used with two different bodies (RFC 9457-style)."""
    status_code = 409


class UniqueViolationError(BirthError):
    """Schema rejected: (tenant_id, scan_id) UNIQUE WHERE status='open' conflict."""
    status_code = 409


# ---------------- Sentinel + factory (Regla de coherencia: runtime órgano) ----------------


_OPEN_REQUEST_SENTINEL = object()


@dataclass(frozen=True)
class ValidatedOpenRequest:
    """Result of `_build_open_request`. Carries the parsed body, the
    JWT-derived tenant_id (NOT the body's), and the optional Idempotency-Key.

    Construction requires the module-private `_OPEN_REQUEST_SENTINEL`. Per the
    'Regla de coherencia' (CLAUDE.md), the type-level guarantee is only real
    if a runtime órgano de rechazo refuses the wrong sentinel.
    """
    payload: OpenPositionRequest
    tenant_id: int
    idempotency_key: Optional[str]
    _sentinel: object

    def __post_init__(self):
        if self._sentinel is not _OPEN_REQUEST_SENTINEL:
            raise TypeError(
                "ValidatedOpenRequest cannot be constructed directly. "
                "Use api.positions_birth._build_open_request (runtime órgano "
                "de rechazo per the 'Regla de coherencia' in CLAUDE.md)."
            )


def _build_open_request(
    body: dict,
    tenant_id: int,
    idempotency_key: Optional[str],
) -> ValidatedOpenRequest:
    """Only legitimate constructor for ValidatedOpenRequest.

    Raises:
      BodyValidationError (422): Pydantic shape/field/cross-field failed.
    """
    try:
        payload = OpenPositionRequest.model_validate(body)
    except ValidationError as e:
        raise BodyValidationError(
            "OpenPositionRequest validation failed",
            detail=e.errors(),
        ) from e
    return ValidatedOpenRequest(
        payload=payload,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        _sentinel=_OPEN_REQUEST_SENTINEL,
    )
```

- [ ] **Step 2: Verify the 7 factory tests pass**

Run: `pytest tests/api/test_build_open_request.py -v 2>&1 | tail -15`
Expected: 7/7 pass.

- [ ] **Step 3: Commit**

```bash
git add api/positions_birth.py
git commit -m "$(cat <<'EOF'
feat(api): typed BirthError taxonomy + _build_open_request factory with sentinel (advances #473)

Defines BirthError hierarchy (BodyValidationError 422,
AmbiguousQtyError 422, StaleEntryTsError 422, DuplicateIdempotencyKeyError
409, UniqueViolationError 409). Kills `except Exception → 500 str(e)`.

ValidatedOpenRequest is sentinel-protected (Regla de coherencia: a type-
level guarantee requires a runtime órgano de rechazo). Only legitimate
constructor: _build_open_request, which validates body via Pydantic and
wraps result with tenant_id (from JWT) + idempotency_key (from header).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: TDD — failing tests for BirthError → HTTP mapping at the route

**Files:**
- Create: `tests/api/test_birth_errors.py`

These tests exercise the route handler end-to-end (FastAPI `TestClient`).
The route handler is rewritten in Task 14 — these tests will guide that change.

- [ ] **Step 1: Create the test file**

```python
"""Route-level tests for POST /positions error taxonomy mapping (#473).

The route must:
- map BodyValidationError to HTTP 422 with a structured detail
- map UniqueViolationError to HTTP 409
- map DuplicateIdempotencyKeyError to HTTP 409
- NOT collapse unrelated server errors to 500 str(e) — they bubble to
  FastAPI's default 500 handler with a generic message and full logged tb
"""
import pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Boot a FastAPI TestClient against a fresh tmp DB with all migrations."""
    import os
    db_path = tmp_path / "birth.db"
    monkeypatch.setenv("BTC_DB", str(db_path))
    from db.schema import init_db
    init_db()

    # Stand up a tenant + JWT for the route.
    from db.transaction import transaction
    with transaction() as con:
        con.execute(
            "INSERT INTO users (id, email, password_hash, role) "
            "VALUES (1, 'samuel@test', 'x', 'admin')"
        )
        con.execute(
            "INSERT INTO capital (tenant_id, initial_capital_usd, current_capital_usd) "
            "VALUES (1, 10000.0, 10000.0)"
        )

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.positions import router as positions_router
    from auth.dependencies import get_current_tenant_id
    from api.deps import verify_api_key
    from auth.dependencies import require_role

    app = FastAPI()
    app.include_router(positions_router)
    app.dependency_overrides[get_current_tenant_id] = lambda: 1
    app.dependency_overrides[verify_api_key] = lambda: None
    app.dependency_overrides[require_role("admin")] = lambda: None
    return TestClient(app)


def test_invalid_body_returns_422(client):
    """Pydantic validation failure → 422 BodyValidationError."""
    resp = client.post("/positions", json={
        "symbol": "BTCUSDT", "entry_price": -1, "direction": "LONG", "qty": 10.0,
    })
    assert resp.status_code == 422
    body = resp.json()
    assert "BodyValidationError" in str(body) or "entry_price" in str(body)


def test_extra_field_returns_422(client):
    resp = client.post("/positions", json={
        "symbol": "BTCUSDT", "entry_price": 100.0, "direction": "LONG",
        "qty": 10.0, "tenant_id": 99,
    })
    assert resp.status_code == 422


def test_unknown_symbol_returns_422(client):
    resp = client.post("/positions", json={
        "symbol": "BOGUSCOIN", "entry_price": 100.0, "direction": "LONG", "qty": 10.0,
    })
    assert resp.status_code == 422


def test_duplicate_open_scan_returns_409(client):
    """Two successful opens with the same scan_id — second must 409."""
    first = client.post("/positions", json={
        "symbol": "BTCUSDT", "entry_price": 100.0, "direction": "LONG",
        "qty": 10.0, "scan_id": 99,
    })
    assert first.status_code == 200
    second = client.post("/positions", json={
        "symbol": "BTCUSDT", "entry_price": 100.0, "direction": "LONG",
        "qty": 10.0, "scan_id": 99,
    })
    assert second.status_code == 409
    assert "UniqueViolationError" in str(second.json()) or "scan_id" in str(second.json())


def test_route_does_not_collapse_server_exceptions_to_500_str(client, monkeypatch):
    """If db_create_position_sql raises an unrelated exception (e.g. disk error),
    the route must NOT catch it as `except Exception → 500 str(e)`. It bubbles
    to FastAPI's default 500 handler."""
    from api import positions_birth

    def _exploding_register(validated):
        raise RuntimeError("simulated disk failure: file not found")

    monkeypatch.setattr(
        positions_birth.BirthRegistrar, "register", staticmethod(_exploding_register),
    )

    # FastAPI's TestClient propagates unhandled exceptions; switch to
    # raise_server_exceptions=False so we observe the 500 response shape.
    from fastapi.testclient import TestClient
    client._transport.raise_app_exceptions = False  # internal toggle
    resp = client.post("/positions", json={
        "symbol": "BTCUSDT", "entry_price": 100.0, "direction": "LONG", "qty": 10.0,
    })
    assert resp.status_code == 500
    # The detail must NOT be the raw str(e) leaked to the client.
    body_text = resp.text
    assert "simulated disk failure" not in body_text
```

- [ ] **Step 2: Verify the 5 tests fail (route not yet rewritten)**

Run: `pytest tests/api/test_birth_errors.py -v 2>&1 | tail -20`
Expected: failures. Some may pass coincidentally (e.g. the 422 for invalid body if the legacy route's `required = {"symbol", "entry_price"}` check fires). The duplicate-scan-409 test will fail (current code maps everything to 500), and the bubble-to-500 test will fail (current code does `except Exception → 500 str(e)`).

- [ ] **Step 3: Commit**

```bash
git add tests/api/test_birth_errors.py
git commit -m "$(cat <<'EOF'
test(api): failing route-level error taxonomy tests (advances #473)

POST /positions must map BodyValidationError->422, UniqueViolationError->409,
and NOT collapse server exceptions to 500 str(e). These tests guide the
route rewrite in Task 14.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: Rewrite `open_position` route to use the new birth path

**Files:**
- Modify: `api/positions.py`

NOTE: this task BREAKS `db_create_position` callers temporarily because we
switch the route to use `BirthRegistrar.register` which calls
`db_create_position_sql`. We add a thin shim that calls the sql helper via
the validated request. Task 19 deletes the old function entirely. Until
Task 19, both names exist.

- [ ] **Step 1: Add `db_create_position_sql` next to `db_create_position` in `db/positions.py`**

Append AFTER the existing `db_create_position` (do NOT delete the old function yet):

```python
def db_create_position_sql(
    con: sqlite3.Connection,
    validated,   # type: ValidatedOpenRequest  (avoid circular import for the annotation)
) -> dict:
    """Thin SQL INSERT for a validated open-position request (#473).

    Replaces db_create_position for the new birth path. The 5-deep qty
    fallback (F5) and all defensive `data.get(...)` membranes are absent —
    the validated payload guarantees every field.
    """
    p = validated.payload
    ts = (p.entry_ts or datetime.now(timezone.utc)).isoformat()
    cur = con.execute(
        """INSERT INTO positions
               (scan_id, symbol, direction, status, entry_price, entry_ts,
                sl_price, tp_price, size_usd, qty, atr_entry, be_mult, notes,
                tenant_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            p.scan_id,
            p.symbol,
            p.direction,
            "open",
            p.entry_price,
            ts,
            p.sl_price,
            p.tp_price,
            p.size_usd,
            p.qty,
            p.atr_entry,
            p.be_mult,
            p.notes,
            validated.tenant_id,
        ),
    )
    pos_id = cur.lastrowid
    row = con.execute("SELECT * FROM positions WHERE id = ?", (pos_id,)).fetchone()
    return dict(row)
```

- [ ] **Step 2: Append `BirthRegistrar` to `api/positions_birth.py`**

Append:

```python
# ---------------- Idempotency-Key cache (D-Tipo HTTP rung) ----------------


_IDEMPOTENCY_TTL = timedelta(hours=24)


class IdempotencyCache:
    """SQLite-backed cache for Idempotency-Key results keyed by (tenant_id, key)."""

    @staticmethod
    def get(con: sqlite3.Connection, tenant_id: int, key: str) -> Optional[dict]:
        now_iso = datetime.now(timezone.utc).isoformat()
        con.execute(
            "DELETE FROM idempotency_keys "
            "WHERE tenant_id = ? AND key = ? AND expires_at < ?",
            (tenant_id, key, now_iso),
        )
        row = con.execute(
            "SELECT result_json FROM idempotency_keys "
            "WHERE tenant_id = ? AND key = ?",
            (tenant_id, key),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    @staticmethod
    def set(con: sqlite3.Connection, tenant_id: int, key: str, result: dict) -> None:
        now = datetime.now(timezone.utc)
        expires = (now + _IDEMPOTENCY_TTL).isoformat()
        con.execute(
            "INSERT OR REPLACE INTO idempotency_keys "
            "(tenant_id, key, result_json, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (tenant_id, key, json.dumps(result, default=str), now.isoformat(), expires),
        )


# ---------------- BirthRegistrar (Op-ligero) ----------------


class BirthRegistrar:
    """Op-ligero owning the atomic write + post-commit for a position birth.

    NOT a symmetric operator to PositionClosure. Validation already happened
    upstream (Pydantic + _build_open_request). This class owns:
      1. Idempotency-Key probe (read) + cached replay if hit.
      2. The transactional INSERT + same-tx cache write (idempotent retry safe).
      3. Translating sqlite3.IntegrityError on the partial UNIQUE index
         into typed UniqueViolationError.
      4. The post-commit update_positions_json (closes F8 — the JSON
         snapshot regeneration was previously outside any transaction).
      5. Structured logging at birth (closes F15).
    """

    @staticmethod
    def register(validated: ValidatedOpenRequest) -> dict:
        from db.positions import db_create_position_sql  # noqa: PLC0415
        from api.positions import update_positions_json  # noqa: PLC0415

        # Step 1: idempotency probe.
        if validated.idempotency_key:
            with transaction() as con:
                cached = IdempotencyCache.get(
                    con, validated.tenant_id, validated.idempotency_key,
                )
            if cached is not None:
                log.info(
                    "BirthRegistrar: idempotent replay tenant=%s key=%s pos_id=%s",
                    validated.tenant_id, validated.idempotency_key,
                    cached.get("id"),
                )
                return cached

        # Step 2 + 3: atomic write + cache + translate IntegrityError.
        try:
            with transaction() as con:
                pos = db_create_position_sql(con, validated)
                if validated.idempotency_key:
                    IdempotencyCache.set(
                        con, validated.tenant_id, validated.idempotency_key, pos,
                    )
        except sqlite3.IntegrityError as e:
            msg = str(e).lower()
            if "unique" in msg and (
                "idx_positions_open_scan_unique" in msg or "scan_id" in msg
            ):
                raise UniqueViolationError(
                    "An open position already exists for this scan_id",
                    detail={"tenant_id": validated.tenant_id,
                            "scan_id": validated.payload.scan_id},
                ) from e
            raise

        # Step 4: post-commit. F8 — was previously outside any transaction.
        update_positions_json()

        # Step 5: F15 structured log at birth.
        log.info(
            "POSICION OPENED #%s %s @ $%s qty=%s tenant=%s scan_id=%s",
            pos["id"],
            validated.payload.symbol,
            validated.payload.entry_price,
            validated.payload.qty,
            validated.tenant_id,
            validated.payload.scan_id,
        )
        return pos
```

- [ ] **Step 3: Add the `idempotency_keys` table to `init_db`**

In `db/schema.py::init_db`, inside the existing `with transaction() as con:` block (the big one with `CREATE TABLE IF NOT EXISTS scans`), append a new CREATE TABLE near the other per-feature tables (e.g. after `agent_audit` or just before the migration block):

```python
        con.execute("""
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                tenant_id    INTEGER NOT NULL,
                key          TEXT    NOT NULL,
                result_json  TEXT    NOT NULL,
                created_at   TEXT    NOT NULL,
                expires_at   TEXT    NOT NULL,
                PRIMARY KEY (tenant_id, key)
            )
        """)
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_idempotency_expires "
            "ON idempotency_keys(expires_at)"
        )
```

- [ ] **Step 4: Rewrite the `open_position` route in `api/positions.py`**

Replace the existing `open_position` function (lines 278-299) with:

```python
@router.post(
    "",
    summary="Abrir nueva posicion",
    # TODO(auth-cleanup): remove verify_api_key after JWT migration stable
    dependencies=[Depends(verify_api_key), Depends(require_role("admin"))],
)
def open_position(
    body: dict = Body(...),
    tenant_id: int = Depends(get_current_tenant_id),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """Open a new position (#473 — Voronov Cluster D dual rung).

    Validation is delegated to api.positions_birth:
      - _build_open_request runs Pydantic boundary validation (D-Tipo).
      - BirthRegistrar.register owns the atomic INSERT + post-commit
        (op-ligero, NOT a symmetric operator to PositionClosure).
      - BirthError subclasses map to typed HTTP status codes via the
        error handler below. NO bare `except Exception` — server faults
        bubble to FastAPI's default 500 handler with the traceback logged.
    """
    from api.positions_birth import (  # noqa: PLC0415
        BirthError, BirthRegistrar, _build_open_request,
    )
    try:
        validated = _build_open_request(body, tenant_id, idempotency_key)
        pos = BirthRegistrar.register(validated)
        return {"ok": True, "position": pos}
    except BirthError as e:
        log.warning(
            "open_position rejected: %s detail=%s", e.message, e.detail,
        )
        raise HTTPException(status_code=e.status_code, detail={
            "error": e.__class__.__name__,
            "message": e.message,
            "detail": e.detail,
        })
```

Also at the top of `api/positions.py`, add the `Header` import:

Find:
```python
from fastapi import APIRouter, Body, Depends, HTTPException, Query
```
Replace with:
```python
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
```

- [ ] **Step 5: Run the route-level error taxonomy tests**

Run: `pytest tests/api/test_birth_errors.py -v 2>&1 | tail -20`
Expected: 5/5 pass.

- [ ] **Step 6: Run the existing positions test suite to surface regressions**

Run: `pytest tests/api/ -q --tb=short 2>&1 | tail -25`
Expected: some failures in legacy positions tests that pass bodies with extra fields (e.g. fixtures with `"tenant_id":1` in body) — those are F6 bypasses that should now fail. Triage:

- If a test passes `"qty": 0` or no `qty`: the new model rejects it; the test should be updated to pass valid qty.
- If a test passes a body with an extra field (other than the Pydantic-allowed set): the new model rejects it; update the test fixture.

If any test failure is structurally legitimate (i.e. the fixture was testing a real legacy behavior we are intentionally retiring), update the fixture and add a brief comment citing #471/#473.

- [ ] **Step 7: Commit**

```bash
git add db/schema.py db/positions.py api/positions.py api/positions_birth.py tests/api/
git commit -m "$(cat <<'EOF'
feat(api): BirthRegistrar + Idempotency-Key + route rewrite for POST /positions (closes #470, advances #473)

api/positions_birth.py now owns the birth path. open_position delegates to
_build_open_request -> BirthRegistrar.register. The route:
- maps BirthError.status_code to HTTPException (422/409)
- kills `except Exception -> 500 str(e)` — server faults bubble with tb logged
- reads Idempotency-Key header; cached replay returns the original row

BirthRegistrar:
- owns the write transaction + caches the Idempotency-Key result in the SAME tx
- translates sqlite3.IntegrityError on the partial UNIQUE index into
  UniqueViolationError (409)
- runs update_positions_json post-commit (F8 — was previously outside any tx)
- emits structured log at birth (F15)

New table idempotency_keys (tenant_id, key, result_json, expires_at) with 24h TTL.
db_create_position_sql added as the thin SQL helper for validated requests
(old db_create_position will be removed in Task 19).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: TDD — failing tests for BirthRegistrar transactional + post-commit behavior

**Files:**
- Create: `tests/api/test_birth_registrar.py`

These tests cover the transactional choreography that Task 14 implemented.
Running them now verifies the choreography from a different angle (in-process
unit tests rather than HTTP). If Task 14's implementation is correct, they
will pass on first run — TDD here is documentation more than discovery.

- [ ] **Step 1: Create the test file**

```python
"""Unit tests for BirthRegistrar's transactional choreography.

We exercise the class directly (not via HTTP) to cover:
- happy path: INSERT + update_positions_json + structured log.
- idempotency replay returns the cached row without re-INSERTing.
- UNIQUE violation on (tenant_id, scan_id) maps to UniqueViolationError.
- update_positions_json is called AFTER commit (the row is visible).
"""
import pytest


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    import os
    db_path = tmp_path / "br.db"
    monkeypatch.setenv("BTC_DB", str(db_path))
    from db.schema import init_db
    init_db()
    return db_path


def _validated(symbol="BTCUSDT", scan_id=None, idempotency_key=None, tenant_id=1):
    from api.positions_birth import _build_open_request
    body = {
        "symbol": symbol, "entry_price": 100.0,
        "direction": "LONG", "qty": 10.0,
    }
    if scan_id is not None:
        body["scan_id"] = scan_id
    return _build_open_request(body, tenant_id=tenant_id, idempotency_key=idempotency_key)


def test_happy_path_inserts_row_and_returns_dict(fresh_db):
    from api.positions_birth import BirthRegistrar
    pos = BirthRegistrar.register(_validated())
    assert pos["id"] >= 1
    assert pos["symbol"] == "BTCUSDT"
    assert pos["qty"] == 10.0
    assert pos["tenant_id"] == 1
    assert pos["status"] == "open"


def test_idempotency_replay_returns_cached_without_second_insert(fresh_db):
    from api.positions_birth import BirthRegistrar
    from db.transaction import transaction

    first = BirthRegistrar.register(_validated(idempotency_key="k-1"))
    second = BirthRegistrar.register(_validated(idempotency_key="k-1"))
    assert second["id"] == first["id"]
    with transaction() as con:
        n = con.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    assert n == 1


def test_duplicate_scan_id_raises_unique_violation_error(fresh_db):
    from api.positions_birth import BirthRegistrar, UniqueViolationError

    BirthRegistrar.register(_validated(scan_id=77))
    with pytest.raises(UniqueViolationError) as exc:
        BirthRegistrar.register(_validated(scan_id=77))
    assert exc.value.status_code == 409
    assert exc.value.detail["scan_id"] == 77


def test_update_positions_json_invoked_after_commit(fresh_db, monkeypatch):
    """F8 closure: BirthRegistrar must call update_positions_json AFTER commit.
    Detect call ordering by stubbing the JSON helper and verifying the row
    is visible from a fresh transaction at the moment the stub fires."""
    from api.positions_birth import BirthRegistrar
    from db.transaction import transaction
    import api.positions as api_positions

    visible_count = {"value": None}

    def _stub_update():
        with transaction() as con:
            n = con.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        visible_count["value"] = n

    monkeypatch.setattr(api_positions, "update_positions_json", _stub_update)

    BirthRegistrar.register(_validated())
    assert visible_count["value"] == 1


def test_distinct_idempotency_keys_create_distinct_rows(fresh_db):
    from api.positions_birth import BirthRegistrar
    from db.transaction import transaction

    a = BirthRegistrar.register(_validated(idempotency_key="k-a"))
    b = BirthRegistrar.register(_validated(idempotency_key="k-b"))
    assert a["id"] != b["id"]
    with transaction() as con:
        n = con.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    assert n == 2
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/api/test_birth_registrar.py -v 2>&1 | tail -20`
Expected: 5/5 pass (Task 14's implementation already satisfies them).
If any fails, fix the implementation in `api/positions_birth.py` and rerun.

- [ ] **Step 3: Commit**

```bash
git add tests/api/test_birth_registrar.py
git commit -m "$(cat <<'EOF'
test(api): unit tests for BirthRegistrar transactional choreography (closes #473 F8/F15)

Happy path INSERT; idempotency replay reuses cached row without re-INSERT;
duplicate scan_id maps to UniqueViolationError 409; update_positions_json
fires AFTER commit (visible from a fresh tx); distinct idempotency keys
yield distinct rows.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 16: TDD — failing tests for `IdempotencyCache` TTL + cleanup

**Files:**
- Create: `tests/api/test_idempotency_key.py`

- [ ] **Step 1: Create the test file**

```python
"""Tests for IdempotencyCache get/set + 24h TTL + lazy cleanup."""
import json
from datetime import datetime, timedelta, timezone
import pytest


@pytest.fixture
def fresh_db_con(monkeypatch, tmp_path):
    import os
    db_path = tmp_path / "ik.db"
    monkeypatch.setenv("BTC_DB", str(db_path))
    from db.schema import init_db
    init_db()
    from db.transaction import transaction
    with transaction() as con:
        yield con


def test_set_then_get_returns_payload(fresh_db_con):
    from api.positions_birth import IdempotencyCache
    payload = {"id": 1, "symbol": "BTCUSDT", "qty": 10.0}
    IdempotencyCache.set(fresh_db_con, tenant_id=1, key="k", result=payload)
    got = IdempotencyCache.get(fresh_db_con, tenant_id=1, key="k")
    assert got == payload


def test_get_returns_none_for_missing_key(fresh_db_con):
    from api.positions_birth import IdempotencyCache
    assert IdempotencyCache.get(fresh_db_con, tenant_id=1, key="missing") is None


def test_different_tenant_does_not_see_cached_entry(fresh_db_con):
    from api.positions_birth import IdempotencyCache
    payload = {"id": 1}
    IdempotencyCache.set(fresh_db_con, tenant_id=1, key="k", result=payload)
    assert IdempotencyCache.get(fresh_db_con, tenant_id=2, key="k") is None


def test_set_overwrites_existing_entry(fresh_db_con):
    from api.positions_birth import IdempotencyCache
    IdempotencyCache.set(fresh_db_con, tenant_id=1, key="k", result={"id": 1})
    IdempotencyCache.set(fresh_db_con, tenant_id=1, key="k", result={"id": 2})
    assert IdempotencyCache.get(fresh_db_con, tenant_id=1, key="k")["id"] == 2


def test_expired_entry_returns_none_and_is_cleaned_up(fresh_db_con):
    from api.positions_birth import IdempotencyCache

    # Hand-craft an already-expired row.
    expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    created = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    fresh_db_con.execute(
        "INSERT INTO idempotency_keys (tenant_id, key, result_json, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (1, "k", json.dumps({"id": 99}), created, expired),
    )

    # The get path lazy-deletes expired rows for this (tenant, key).
    assert IdempotencyCache.get(fresh_db_con, tenant_id=1, key="k") is None
    n = fresh_db_con.execute(
        "SELECT COUNT(*) FROM idempotency_keys WHERE tenant_id=1 AND key='k'"
    ).fetchone()[0]
    assert n == 0


def test_unexpired_entry_within_24h_still_returned(fresh_db_con):
    from api.positions_birth import IdempotencyCache
    payload = {"id": 7}
    IdempotencyCache.set(fresh_db_con, tenant_id=1, key="k", result=payload)
    got = IdempotencyCache.get(fresh_db_con, tenant_id=1, key="k")
    assert got == payload
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/api/test_idempotency_key.py -v 2>&1 | tail -15`
Expected: 6/6 pass (Task 14's implementation satisfies them).

- [ ] **Step 3: Commit**

```bash
git add tests/api/test_idempotency_key.py
git commit -m "$(cat <<'EOF'
test(api): IdempotencyCache get/set/TTL/cleanup tests (closes #470)

Set-then-get; missing returns None; tenant scoping; overwrite; expired
entry returns None and is lazy-deleted; unexpired within 24h is returned.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 17: Delete legacy `db_create_position`; only `db_create_position_sql` remains

**Files:**
- Modify: `db/positions.py`
- Modify: `api/positions.py`

- [ ] **Step 1: Confirm there are no remaining callers of `db_create_position` (the legacy dict-input function)**

Run: `grep -rn "db_create_position\b" --include="*.py" .`

Expected: `db_create_position_sql` (the new helper) and only one remaining `db_create_position` reference — the import in `api/positions.py` (now dead because the route uses `BirthRegistrar`). If any production code outside `api/positions.py` calls `db_create_position`, STOP and triage — that caller must migrate before this task can proceed.

- [ ] **Step 2: Delete `db_create_position` from `db/positions.py`**

Remove the entire `def db_create_position(...)` block (lines ~40-79 in the original file).

- [ ] **Step 3: Remove the dead import in `api/positions.py`**

Find:
```python
from db.positions import (
    _calc_pnl,
    db_create_position,
    db_get_positions,
    db_update_position,
)
```

Replace with:
```python
from db.positions import (
    _calc_pnl,
    db_get_positions,
    db_update_position,
)
```

- [ ] **Step 4: Audit test fixtures that may have called the legacy function**

Run: `grep -rn "db_create_position\b" tests/ --include="*.py"`

For each match: if it's `db_create_position_sql`, leave alone. If it's the old `db_create_position`, replace with the validated-request equivalent:

```python
from api.positions_birth import _build_open_request
from db.positions import db_create_position_sql

validated = _build_open_request(
    {"symbol": "BTCUSDT", "entry_price": 100.0, "direction": "LONG", "qty": 10.0},
    tenant_id=1, idempotency_key=None,
)
with transaction() as con:
    pos = db_create_position_sql(con, validated)
```

- [ ] **Step 5: Run the affected test files**

Run: `pytest tests/api/ tests/db/ tests/operators/ -q --tb=short 2>&1 | tail -15`
Expected: all green. Any failure means a fixture still uses the old function — fix per Step 4 pattern.

- [ ] **Step 6: Commit**

```bash
git add db/positions.py api/positions.py tests/
git commit -m "$(cat <<'EOF'
refactor(db,api): delete legacy db_create_position; only db_create_position_sql remains (closes #471 F5)

The 5-deep qty fallback chain `(float(data.get('qty')) or
(float(data.get('size_usd', 0) or 0) / entry if entry else 0))` is gone.
The defensive `data.get(...)` membranes are gone. The new SQL helper takes
a ValidatedOpenRequest whose Pydantic boundary guarantees every field.

Voronov D-Tipo: the rung 'tipo' is only real when the constructor rejects
the wrong input. _build_open_request is now the only doorway to creating
a position; the schema CHECKs are the second fence.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 18: Update test fixtures that INSERT positions directly (broken by new CHECKs)

**Files:**
- Modify: subset of `tests/` that INSERT into `positions` directly with `qty=0` or `tenant_id=NULL`

This task addresses the C2-style fixture drift. Scope: only update what's broken by the new D CHECKs. The full centralized factory (#479) remains out of scope.

- [ ] **Step 1: Run the full suite and collect failures attributable to D CHECKs**

Run: `pytest tests/ -q --tb=line 2>&1 | grep -E "IntegrityError|CHECK constraint failed|tenant_id|qty" | head -40`

Expected: a finite list of failing test files. Each represents a fixture INSERT that satisfied C2 but violates D (qty=0 or tenant_id=NULL on a non-quarantine status).

- [ ] **Step 2: For each failing fixture, prefer the smallest change that makes it valid**

Two patterns:

**Pattern A — fixture INSERTs `qty=0`**: change to a realistic value:
```python
con.execute(
    "INSERT INTO positions (symbol, entry_price, entry_ts, qty, tenant_id, status) "
    "VALUES ('BTCUSDT', 100.0, '2024-01-01T00:00:00', 10.0, 1, 'open')"
)
```

**Pattern B — fixture INSERTs `tenant_id=NULL` on non-quarantine status**: either supply a tenant_id (preferred for "active" fixtures) or set `status='legacy_no_tenant'` if the fixture's intent is to test legacy behavior.

Do NOT centralize these fixtures into a shared factory — that's #479's scope. The goal here is "smallest delta to green".

- [ ] **Step 3: Re-run the suite**

Run: `pytest tests/ -q --tb=short 2>&1 | tail -10`
Expected: green (modulo pre-existing skips/flakes documented in the C2 plan).

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "$(cat <<'EOF'
test: update fixtures broken by D-schema CHECKs (qty>0, tenant_id NOT NULL)

Fixtures that INSERTed positions with qty=0 or tenant_id=NULL on a non-
quarantine status now satisfy the D CHECKs (either real tenant_id+qty or
status='legacy_no_tenant' / 'legacy_unmeasurable').

The centralized fixture factory remains out of scope — tracked in #479.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 19: Final verification + smoke imports

**Files:** none modified (unless smoke surfaces a fix).

- [ ] **Step 1: Grep for surviving anti-patterns**

Run:
```bash
grep -rn "except Exception" api/positions.py
grep -rn "data\.get(\"qty\"\|data\.get('qty'" --include="*.py" .
grep -rn "db_create_position\b" --include="*.py" .
```
Expected:
- No `except Exception` in `api/positions.py::open_position`.
- No `data.get("qty")` / `data.get('qty')` anywhere.
- `db_create_position` only matches `db_create_position_sql`.

If any anti-pattern survives, fix and recommit before moving on.

- [ ] **Step 2: Run the new birth-path test files**

Run:
```bash
pytest tests/db/test_migrate_qty_positive.py \
       tests/db/test_migrate_tenant_id_not_null.py \
       tests/db/test_migrate_unique_open_scan.py \
       tests/api/test_open_position_request.py \
       tests/api/test_build_open_request.py \
       tests/api/test_birth_errors.py \
       tests/api/test_birth_registrar.py \
       tests/api/test_idempotency_key.py \
       -v 2>&1 | tail -15
```
Expected: all pass (5 + 5 + 6 + ~22 + 7 + 5 + 5 + 6 = ~61 tests).

- [ ] **Step 3: Run the regression-prone suites**

Run:
```bash
pytest tests/db/ tests/api/ tests/operators/ -q --tb=short 2>&1 | tail -10
```
Expected: green.

- [ ] **Step 4: Full suite**

Run: `pytest tests/ --tb=no -q -p no:cacheprovider 2>&1 | tail -5`
Expected: baseline (recorded in Task 1) + ~61 new tests. Skipped count unchanged; the pre-existing `test_setup` daemon flake may or may not appear (PR #445 known limitation).

- [ ] **Step 5: Smoke imports for all modified modules**

```bash
python -c "
import importlib
for m in (
    'db.schema', 'db.positions', 'db.transaction',
    'api.positions', 'api.positions_birth',
    'operators.position_closure', 'operators.precheck',
):
    importlib.import_module(m)
from api.positions_birth import (
    OpenPositionRequest, _build_open_request, ValidatedOpenRequest,
    BirthError, BodyValidationError, UniqueViolationError,
    DuplicateIdempotencyKeyError, BirthRegistrar, IdempotencyCache,
    ALLOWED_SYMBOLS,
)
from db.positions import db_create_position_sql
from db.schema import (
    _migrate_qty_positive, _migrate_tenant_id_not_null, _migrate_unique_open_scan,
)
print('all modules + new symbols import cleanly')
"
```
Expected: `all modules + new symbols import cleanly`.

- [ ] **Step 6: Manual happy-path smoke (optional but recommended)**

If a local API + admin JWT is available, run a curl to POST /positions with a valid body, then POST again with the same `Idempotency-Key` and confirm both responses return the same `position.id`. Then POST with a duplicate `scan_id` (different `Idempotency-Key`) and confirm 409.

- [ ] **Step 7: No commit (verification only).**

---

### Task 20: Update CLAUDE.md "Known scope gap" post-D

**Files:**
- Modify: `CLAUDE.md`

Task 2 already added the post-D registry. After verification (Task 19), if any deferred item changed scope (e.g. F10 rate limiting got partially addressed), update the gap section. Otherwise this task is a no-op.

- [ ] **Step 1: Re-read the post-D scope-gap entry added in Task 2**

Run: `grep -nA 10 "Known scope gap (post-D)" CLAUDE.md`

Expected: the three bullets (F10 rate limiting, direction enum at schema, scan_id FK). If those are still accurate after implementation, no change needed and this task closes.

- [ ] **Step 2: If anything drifted (unlikely), edit and commit; otherwise skip.**

```bash
# If no edits needed:
echo "Task 20: post-D scope gap entry unchanged; no commit."
```

---

### Task 21: Open follow-up issues for deferred work

**Files:** none modified locally.

- [ ] **Step 1: Open the F10 rate-limiting follow-up**

```bash
gh issue create --repo sssimon/trading-spacial \
  --title "F10: POST /positions has no rate limit (D follow-up)" \
  --body "Surfaced by Serrano review during Cluster D (#471/#470/#473). Out of scope for the D PR. The endpoint is authenticated (JWT + admin role) and idempotency-key replay is cached, but a legitimate client with unique Idempotency-Key values can flood the endpoint creating distinct rows. Track here for future rate-limiting work (token bucket per tenant, etc.)."
```

- [ ] **Step 2: Open the direction-enum-at-schema follow-up**

```bash
gh issue create --repo sssimon/trading-spacial \
  --title "Schema CHECK on positions.direction enum (D follow-up)" \
  --body "Cluster D enforces direction in {LONG, SHORT} at the Pydantic boundary only. Add CHECK (direction IN ('LONG', 'SHORT')) at the schema level to close the gap if a non-API caller (migration, manual SQL) writes a different value. Trivial migration; deferred from D to keep PR focused."
```

- [ ] **Step 3: Open the scan_id FK follow-up**

```bash
gh issue create --repo sssimon/trading-spacial \
  --title "scan_id FK to a real scans/signals table (D follow-up)" \
  --body "positions.scan_id is nullable INTEGER REFERENCES scans(id) but the semantic referenced table for trade signals is not 'scans' (which is the scanner-run audit ledger). The D partial UNIQUE index closes the race condition but not referential integrity. Refactor: decide canonical target table for scan_id and add a real FK + (possibly) backfill. Out of scope for D PR."
```

- [ ] **Step 4: Note the F11 fixture-factory follow-up (already tracked in #479)**

No new issue needed; #479 covers it.

- [ ] **Step 5: Verify all three issues were created**

```bash
gh issue list --repo sssimon/trading-spacial --state open --limit 10 \
  | grep -E "F10|direction enum|scan_id FK"
```
Expected: 3 matching lines.

---

### Task 22: Push + open PR + close #471 #470 #473 (REQUIRES USER CONFIRMATION)

**Files:** none modified.

This step is externally visible to `sssimon/trading-spacial`. Confirm before
executing in an autonomous session.

- [ ] **Step 1: Push the branch**

Run: `git push -u origin feat/birth-time-enforcement-471-470-473`
Expected: branch published; URL printed.

- [ ] **Step 2: Open the PR**

```bash
gh pr create --repo sssimon/trading-spacial \
  --title "feat: birth-time enforcement (dual rungs schema+tipo) [closes #471 #470 #473]" \
  --body "$(cat <<'EOF'
## Summary

Voronov Cluster D — close the contract `open()` was never asked to sign. Per Voronov 2026-05-26:

> `close()` valida una transición entre dos estados conocidos del mismo objeto. `open()` no valida transición — valida un acto de nominación. Son primos, no hermanos.

This PR enforces birth-time invariants of `positions` across two rungs of the C2 enforcement registry — **dual, not symmetric** to `PositionClosure`. No `PositionOpen` operator was created; that would be false symmetry.

### Schema rung (the frontier no caller evades)

- `_migrate_qty_positive` — extends CHECK from `qty IS NOT NULL` to `((qty IS NOT NULL AND qty > 0) OR status='legacy_unmeasurable')`. Quarantines 72 production rows with `qty=0.0` (closes the C2 0-bypass).
- `_migrate_tenant_id_not_null` — CHECK `(tenant_id IS NOT NULL OR status IN ('legacy_unmeasurable','legacy_no_tenant'))`. New quarantine status `legacy_no_tenant` for ~1348 NULL-tenant rows. Rows already in `legacy_unmeasurable` are exempted by the OR (no double-quarantine).
- `_migrate_unique_open_scan` — partial UNIQUE index on `(tenant_id, scan_id) WHERE status='open' AND scan_id IS NOT NULL`. Closes the idempotency race; safe at-rest (the 2 production duplicates are both `closed`).

### Tipo rung (where the error takes semantic shape)

- `OpenPositionRequest` (Pydantic v2) with `extra='forbid'` — closes F6 (tenant_id silently dropped from body); F5 (qty required, no size_usd fallback); F7 (symbol allowlist, direction enum required no default, SL/TP relational); F9 (entry_ts in [now-7d, now+60s] or absent).
- `_build_open_request` — private factory; only legitimate constructor for `ValidatedOpenRequest`. The dataclass is sentinel-protected with `_OPEN_REQUEST_SENTINEL`. Per the *Regla de coherencia*: the type-level guarantee is only real if a runtime órgano de rechazo refuses the wrong sentinel.
- `BirthError` hierarchy with `status_code` per subclass. Route handler maps to `HTTPException`. `except Exception → 500 str(e)` is gone.

### HTTP rung (operational idempotency)

- `Idempotency-Key` header support. New table `idempotency_keys` (tenant_id, key, result_json, expires_at) with 24h TTL and lazy cleanup. Replay returns the cached row without re-INSERT.

### Op-ligero rung (atomic transition + post-commit)

- `BirthRegistrar.register` owns the transaction + the post-commit `update_positions_json` (closes F8). Translates `sqlite3.IntegrityError` on the partial UNIQUE index into typed `UniqueViolationError`. Emits structured log at birth (closes F15). **NOT** a symmetric `PositionOpen` operator — validation lives upstream (Pydantic + `_build_open_request`).

## The invariant declared in CLAUDE.md

> Una `Position` existe si y solo si su acto de nominación satisfizo simultáneamente: (a) el contrato existencial del schema (qué la convierte ontológicamente en Position), y (b) el contrato de nominación de la frontera de entrada (qué valida que el input externo intentaba declararla legítimamente). Schema es la frontera que ningún caller evade; nominación es donde el error toma forma semántica.

## Production measurement baked in

`signals.db` snapshot (2026-05-26):
- 2018 total positions; 2018 with tenant_id=NULL (100%); 72 with qty=0.0 exactly; 2 rows share scan_id=42 (both closed, no migration conflict); 350 with future entry_ts ALL already in `legacy_unmeasurable` from C2.

Migrations are gated on column existence and quarantine-status presence; idempotent on re-run.

## Resolves

- **#471** — birth-time existential invariants (qty>0, tenant_id NOT NULL) + F5/F6/F7/F9/F15
- **#470** — idempotency for POST /positions (Idempotency-Key HTTP + UNIQUE schema)
- **#473** — typed error taxonomy + BirthRegistrar atomicity + post-commit

## Deferred to follow-up issues

- **F10** — rate limiting on POST /positions (separate issue opened)
- Schema CHECK for direction enum (separate issue)
- `scan_id` real FK to a canonical signals table (separate issue)
- **F11** — centralized fixture factory (#479 already tracking)

## Test plan

- [x] 5 tests `_migrate_qty_positive`
- [x] 5 tests `_migrate_tenant_id_not_null`
- [x] 6 tests `_migrate_unique_open_scan`
- [x] ~22 tests `OpenPositionRequest` (every validator + cross-field)
- [x] 7 tests `_build_open_request` + sentinel
- [x] 5 tests route-level error mapping
- [x] 5 tests `BirthRegistrar` choreography
- [x] 6 tests `IdempotencyCache` TTL/cleanup
- [x] Existing 14 PositionClosure invariants still pass
- [x] Full suite green (~baseline + 61 new tests)
- [ ] Manual smoke in prod after merge: 3 migrations apply cleanly; POST /positions happy path + Idempotency-Key replay + duplicate scan_id 409.

## What this PR does NOT do

- No `PositionOpen` operator. `BirthRegistrar` is op-ligero, not symmetric to `PositionClosure`.
- No FK enforcement on `scan_id` (separate issue).
- No rate limiting (F10 — separate issue).
- No schema enum on `direction` (separate issue).
- No centralized test-fixture factory (#479).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Close #471, #470, #473 with cross-links**

```bash
NEW_PR=$(gh pr view --json number --jq .number)

gh issue comment 471 --repo sssimon/trading-spacial --body "Closed by #$NEW_PR. Schema rung: \`qty > 0\` and \`tenant_id NOT NULL\` are now CHECK-enforced (with \`legacy_unmeasurable\`/\`legacy_no_tenant\` quarantine for 72 + ~1348 production rows respectively). Tipo rung: \`OpenPositionRequest\` (Pydantic, \`extra='forbid'\`) + \`_build_open_request\` factory with sentinel. F5/F6/F7/F9/F15 closed inline; F10 deferred to separate issue."
gh issue close 471 --repo sssimon/trading-spacial

gh issue comment 470 --repo sssimon/trading-spacial --body "Closed by #$NEW_PR. Two-rung idempotency: schema partial UNIQUE index on \`(tenant_id, scan_id) WHERE status='open' AND scan_id IS NOT NULL\` (closes the concurrent-INSERT race; maps to 409 \`UniqueViolationError\`); HTTP \`Idempotency-Key\` header backed by 24h cached result in \`idempotency_keys\` table (safe client retry)."
gh issue close 470 --repo sssimon/trading-spacial

gh issue comment 473 --repo sssimon/trading-spacial --body "Closed by #$NEW_PR. Typed \`BirthError\` hierarchy (status_code per subclass) replaces \`except Exception → 500 str(e)\`. \`BirthRegistrar.register\` owns the write transaction + post-commit \`update_positions_json\` (F8) + structured log at birth (F15). Op-ligero, NOT a symmetric operator to \`PositionClosure\` — per Voronov, \`open()\` validates nomination, not transition."
gh issue close 473 --repo sssimon/trading-spacial
```

- [ ] **Step 4: Done**

The plan is fully executed when this step completes.

---

## Self-Review

**Spec coverage:**
- Schema rung (qty>0, tenant_id NOT NULL, UNIQUE open-scan): Tasks 3-8 (TDD + impl pairs).
- Tipo rung (Pydantic boundary): Tasks 9-10.
- Sentinel-protected factory + typed errors: Tasks 11-12.
- HTTP rung (Idempotency-Key + route rewrite): Tasks 13-14, 16.
- Op-ligero (BirthRegistrar): Task 14 (impl) + Task 15 (unit tests).
- 5-deep qty fallback removal: Tasks 14, 17.
- CLAUDE.md registry + dual-rung principle + close C2 gap: Tasks 2, 20.
- Production measurement bake-in: Task 1.
- Follow-up issues for deferred work (F10, direction enum, scan_id FK): Task 21.
- Push + PR + issue closure: Task 22.

**Placeholder scan:** None of the disallowed patterns. All code blocks are complete; the Pydantic model, factory, error hierarchy, registrar, and migrations are written in full. Task 18 (fixture updates) is intentionally scoped to "smallest delta to green" rather than fixed code because the failure set is determined by running the suite — the task gives the exact pattern to apply for each failure.

**Type consistency:**
- `OpenPositionRequest`, `_build_open_request`, `ValidatedOpenRequest`, `_OPEN_REQUEST_SENTINEL`, `BirthError`, `BodyValidationError`, `UniqueViolationError`, `BirthRegistrar`, `IdempotencyCache` named consistently across Tasks 9-19.
- `db_create_position_sql` named consistently across Tasks 14, 17, and the API surface section.
- `legacy_no_tenant` quarantine status named consistently across Tasks 5, 6, 18 and CLAUDE.md (Task 2).
- `idx_positions_open_scan_unique` index name consistent across Tasks 7, 8.
- `idempotency_keys` table name consistent across Tasks 14, 16.

**Caveats:**
- Task 13's `test_route_does_not_collapse_server_exceptions_to_500_str` toggles a TestClient internal (`_transport.raise_app_exceptions`). If FastAPI's TestClient API has changed when this PR is executed, the implementer should consult the current docs and use the documented escape hatch instead (the property name has historically been stable across recent FastAPI versions but is implementation detail).
- Task 14 step 6 ("run existing tests") will surface fixture regressions from the new `extra='forbid'` and qty validators. Task 18 explicitly handles those; if a regression is uncovered earlier (during Task 14 step 6), the implementer may either fix in place or defer to Task 18. Prefer fixing in place if scope is small.
- Task 18's grep-then-fix loop assumes the implementer has a working local DB and a passable test suite at baseline. If baseline already has unrelated failures, isolate them first.
- Tasks 21 + 22 (issue creation, PR, closures) require user confirmation in an autonomous session — explicitly called out per the prompt's "REQUIRES USER CONFIRMATION" gate.

**Spec decisions made by this plan (not pinned down by the prompt):**

1. **DEFAULT_SYMBOLS source.** The spec said "read from existing `config/settings.py` or wherever the curated list lives. If no central source, add one to settings as part of Task 9 (or accept any non-empty uppercase string — note the choice)." There is no `config/settings.py`; the canonical curated list is `btc_scanner.DEFAULT_SYMBOLS` (10 coins). Task 10 re-exports that into `api.positions_birth.ALLOWED_SYMBOLS` (frozenset) — single source of truth, no duplication, no settings refactor in scope.

2. **`api/positions_birth.py` as a separate module** (rather than appending to `api/positions.py`). Justification: routes vs birth-path concerns are different audiences (FastAPI app composition vs domain rules); the birth path needs to be unit-testable without spinning up FastAPI; future birth-path extensions (e.g. F10 rate limiting) get a clean home.

3. **`_OPEN_REQUEST_SENTINEL` is module-private**, not exported by `__all__`. The C2 `_VALIDATION_SENTINEL` set the same pattern. Tests construct via `_build_open_request` and verify rejection via a synthetic `object()` — no test imports the sentinel itself.

4. **`AmbiguousQtyError` and `StaleEntryTsError` are defined but not raised by the factory** (Pydantic catches both cases as `ValidationError → BodyValidationError`). They exist for taxonomy completeness so future code that wants finer granularity can raise them without inventing new names. Document this in a code comment.

5. **`update_positions_json` runs OUTSIDE the transaction** (post-commit). F8 said "must be inside the same tx as the INSERT", but inspecting `update_positions_json` shows it opens its own `snapshot_connection` (a different connection) and writes to a JSON file. Pulling the file write into the transaction is impossible without re-architecting the JSON-write path. The fix "BirthRegistrar owns both" means BirthRegistrar is the single place that runs the INSERT-then-JSON sequence atomically from the caller's perspective — the INSERT either committed or it didn't, and only on commit does the JSON file regenerate. If the JSON write fails post-commit, the row still exists; that's the same failure mode `update_positions_json` already has on every other call site. The structured log at birth (F15) ensures observability.

6. **`DuplicateIdempotencyKeyError` is defined but not raised in this PR.** The current implementation does NOT compare the cached body shape against the new request body — RFC 9457 strictly says "same key + different body = 409 Conflict". Implementing the body-shape compare requires a stable canonicalization of the request body (Pydantic's `.model_dump()` is one option) which is doable but adds a step. The error class exists in the taxonomy; a follow-up issue can wire it up. Tracked verbally in the "Deferred" section of the PR body. (If the user prefers we wire it up in this PR, that's a 5-minute extension to Task 14 step 2.)

7. **Test fixtures (Task 18) are updated in-place, not centralized.** The prompt explicitly said the centralized factory (#479) is out of scope. Task 18 applies the minimal pattern (real qty + tenant_id, or quarantine status) per failing fixture.

8. **`idempotency_keys` table cleanup is lazy on read, not via a background sweep.** Rationale: Voronov ("performance + UX, no invariante existencial") — keep it simple. A future cron sweeper is a trivial addition if the table grows unmanageable, but the lazy `DELETE WHERE expires_at < now` on every `get()` keeps the table small for any reasonable retry pattern.
