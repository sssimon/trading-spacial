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

Closes #471 F5/F6/F7/F9, #470, #473.
"""
from __future__ import annotations

import hashlib
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


# ---------------- SQLite extended error code constants ----------------
#
# Per Voronov / Serrano BLOCKER 3 + Aurelius reframe: translate IntegrityError
# at the layer that originates the error (via the extended error code), not
# via a substring match against SQLite's English prose. Codes are stable
# across SQLite versions; English message wording is not. Available on
# sqlite3.IntegrityError via .sqlite_errorcode (Python 3.11+).
_SQLITE_CONSTRAINT_UNIQUE = 2067
_SQLITE_CONSTRAINT_PRIMARYKEY = 1555
_SQLITE_CONSTRAINT_CHECK = 275
_SQLITE_CONSTRAINT_NOTNULL = 1299

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


# ---------------- Typed error taxonomy (D-Tipo rung, semantic shape) ----------------
#
# Per Aurelius reframe + Serrano BLOCKER 3: translation lives at the layer
# that ORIGINATES the error (BirthRegistrar maps sqlite3.IntegrityError to
# the BirthError subclass by sqlite_errorcode, not by substring match on the
# English prose). The route handler maps BirthError.status_code to HTTP.


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
    """qty and size_usd both provided but inconsistent. Raised by BirthRegistrar
    when the schema CHECK `qty > 0` fires (defense-in-depth — Pydantic should
    have caught it upstream; reaching the schema means the payload bypassed
    the Pydantic boundary or the boundary disagrees with the schema)."""
    status_code = 422


class StaleEntryTsError(BirthError):
    """entry_ts outside the accepted window. (Surfaced via Pydantic for now;
    reserved here in case a future check fires it from the schema.)"""
    status_code = 422


class TenantViolationError(BirthError):
    """Schema rejected the row because tenant_id was NULL (or any other
    tenant_id CHECK fragment fired). Defense-in-depth — Pydantic forbids
    `tenant_id` in the body (extra='forbid') and the JWT-derived tenant_id
    is always present; reaching this error means the registrar received a
    malformed ValidatedOpenRequest or the schema invariant tripped on an
    unexpected path."""
    status_code = 422


class DuplicateIdempotencyKeyError(BirthError):
    """Same Idempotency-Key reused with a DIFFERENT body fingerprint.

    RFC 9457-style: the cache stores the SHA-256 of the canonical-JSON body
    alongside the cached result. A second request that hits the cache with
    a matching key but a different body must NOT replay the cached result
    (that would silently return the wrong position to the wrong call). It
    must be rejected with 409 so the client can fix its key or its body.

    `detail` carries both fingerprints for operator triage.
    """
    status_code = 409


class UniqueViolationError(BirthError):
    """Schema rejected: (tenant_id, scan_id) UNIQUE WHERE status='open' conflict."""
    status_code = 409


class SchemaIntegrityError(BirthError):
    """Catch-all for sqlite3.IntegrityError variants the registrar did not
    pattern-match against (e.g., a CHECK whose semantic origin is ambiguous).

    Distinct from a bare 500: gives the client a structured 422 with the
    triage detail (sqlite_errorname + sqlite_errorcode + the CHECK fragment
    SQLite included in str(e)) instead of leaking str(e) directly. Surfaces
    schema fences we forgot to map — the structured log a layer above lets
    us widen the taxonomy in a follow-up.
    """
    status_code = 422


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
      BodyValidationError (422): Pydantic shape/field/cross-field failed, OR
        tenant_id is not a positive int (Regla de coherencia: the type
        annotation `tenant_id: int` is only enforced if the factory rejects
        non-conforming inputs at the runtime boundary — Voronov post-Serrano).
    """
    # Runtime órgano de rechazo on `tenant_id`. The annotation `tenant_id: int`
    # is convention in a language without a type-checker in CI; lift it to
    # the 'tipo' rung by rejecting non-conforming inputs here. Booleans are
    # int subclasses in Python (`isinstance(True, int) is True`); reject them
    # explicitly so a caller cannot smuggle `True`/`False` past the gate.
    if isinstance(tenant_id, bool) or not isinstance(tenant_id, int) or tenant_id <= 0:
        raise BodyValidationError(
            "tenant_id must be a positive int",
            detail={
                "field": "tenant_id",
                "received_type": type(tenant_id).__name__,
                "reason": "invalid type or non-positive value",
            },
        )
    if idempotency_key is not None and not isinstance(idempotency_key, str):
        raise BodyValidationError(
            "Idempotency-Key must be a string when provided",
            detail={
                "field": "idempotency_key",
                "received_type": type(idempotency_key).__name__,
            },
        )
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


# ---------------- Idempotency-Key cache (D-Tipo HTTP rung) ----------------
#
# Backed by the `idempotency_keys` table (db/schema.py::_migrate_idempotency_keys).
# Per Voronov / Serrano BLOCKER 1: every cache entry carries a SHA-256
# fingerprint of the canonical-JSON body. On cache hit, the registrar
# compares the fingerprint of the CURRENT request against the cached one. A
# match replays the cached result (RFC 9457 idempotent semantics). A
# mismatch is a client bug — same Idempotency-Key reused with a different
# body — and is rejected with DuplicateIdempotencyKeyError (409).

_IDEMPOTENCY_TTL = timedelta(hours=24)


def _canonical_body_fingerprint(payload: "OpenPositionRequest") -> str:
    """SHA-256 of the canonical-JSON serialization of the validated payload.

    Uses Pydantic's `model_dump(mode='json')` which yields a dict with
    JSON-friendly primitives (datetimes → ISO strings, etc.), then
    json.dumps with sort_keys + separators to canonicalize. Two requests
    with the SAME post-validation payload produce the SAME fingerprint;
    two requests with different bodies (even if cosmetically equivalent at
    the wire level) produce different fingerprints once normalized.

    The fingerprint is computed AFTER Pydantic validation so cosmetic
    differences (whitespace, key order, `"symbol":"BTCUSDT"` vs
    `"symbol":"btcusdt"`) collapse to the same fingerprint — replay-safe
    against benign client retries while distinguishing genuine body changes.
    """
    body_dict = payload.model_dump(mode="json")
    canonical = json.dumps(body_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IdempotencyCache:
    """SQLite-backed cache for Idempotency-Key results keyed by (tenant_id, key).

    Schema (db/schema.py::_migrate_idempotency_keys):
      (tenant_id, key) PRIMARY KEY, result_json, body_sha256, created_at, expires_at

    `body_sha256` is the SHA-256 of the canonical-JSON body the request was
    validated against. Callers compare the fingerprint of the CURRENT request
    against the cached value to detect Idempotency-Key reuse with a different
    body (RFC 9457 "Same Idempotency-Key, different body" is a client bug).

    OperationalError handling: if the table is missing or unreachable, both
    paths emit a structured log.error before returning the conservative
    default (cache-miss on get, no-op on set). The cache is a performance/UX
    layer, not the structural correctness boundary — that is owned by the
    partial UNIQUE index at the schema. Silent degradation was Serrano
    MEDIUM 11.
    """

    @staticmethod
    def get(
        con: sqlite3.Connection, tenant_id: int, key: str,
    ) -> Optional[dict]:
        """Return the cached entry `{"result": ..., "body_sha256": ...}` or
        None on miss / expired / unreachable.

        Lazy cleanup: deletes expired rows for THIS (tenant, key) on every
        call. The eager sweeper (using `idx_idempotency_expires`) is a
        deferred follow-up — kept off the hot path for now (Tier 3 / Serrano
        MEDIUM 4).
        """
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            # Lazy cleanup of expired entries for this (tenant, key) pair only.
            con.execute(
                "DELETE FROM idempotency_keys "
                "WHERE tenant_id = ? AND key = ? AND expires_at < ?",
                (tenant_id, key, now_iso),
            )
            row = con.execute(
                "SELECT result_json, body_sha256 FROM idempotency_keys "
                "WHERE tenant_id = ? AND key = ?",
                (tenant_id, key),
            ).fetchone()
        except sqlite3.OperationalError as e:
            # Table missing or unreachable. Structured error before falling
            # back to cache-miss semantics — degraded path must be loud
            # (Serrano MEDIUM 11).
            log.error(
                "IDEMPOTENCY_CACHE_UNREACHABLE op=get tenant=%s key=%s sqlite_error=%s",
                tenant_id, key, e,
            )
            return None
        if row is None:
            return None
        # body_sha256 may be NULL on rows written before the fingerprint
        # column existed (defensive — the migration adds it to fresh tables;
        # treat a NULL as "no fingerprint, replay only when keys match").
        return {
            "result": json.loads(row[0]),
            "body_sha256": row[1],
        }

    @staticmethod
    def set(
        con: sqlite3.Connection,
        tenant_id: int,
        key: str,
        result: dict,
        body_sha256: str,
    ) -> None:
        """Persist `result` under (tenant_id, key) with the given fingerprint.

        INSERT OR REPLACE — a successful retry with the same key + same body
        overwrites the prior row (idempotent at the row level). The
        registrar guards against same-key/different-body BEFORE calling set
        (raises DuplicateIdempotencyKeyError on mismatch), so set is only
        ever called with a fingerprint that matches the row it overwrites
        OR with a brand-new key.
        """
        try:
            now = datetime.now(timezone.utc)
            expires = (now + _IDEMPOTENCY_TTL).isoformat()
            con.execute(
                "INSERT OR REPLACE INTO idempotency_keys "
                "(tenant_id, key, result_json, body_sha256, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    tenant_id, key,
                    json.dumps(result, default=str),
                    body_sha256,
                    now.isoformat(), expires,
                ),
            )
        except sqlite3.OperationalError as e:
            # Table missing or unreachable. The position INSERT has either
            # already committed (the caller wraps both in one tx — see
            # BirthRegistrar.register) or rolls back together. Logging here
            # makes the degradation observable; silent no-op was Serrano
            # MEDIUM 11.
            log.error(
                "IDEMPOTENCY_CACHE_UNREACHABLE op=set tenant=%s key=%s sqlite_error=%s",
                tenant_id, key, e,
            )
            return


# ---------------- BirthRegistrar (Op-ligero) ----------------


class BirthRegistrar:
    """Op-ligero owning the atomic write + post-commit for a position birth.

    NOT a symmetric operator to PositionClosure. Validation already happened
    upstream (Pydantic + _build_open_request). This class owns:
      1. The transactional probe + INSERT + cache write — all under one
         BEGIN IMMEDIATE so the idempotency probe and the position write
         cannot drift apart (Serrano BLOCKER 2 / Aurelius reframe). The
         widening of the write-lock window is acceptable: BirthRegistrar is
         op-ligero, not hot-path.
      2. Body-fingerprint guard on idempotency replay: if the cache hits
         with the same key but a different SHA-256 body fingerprint, raise
         DuplicateIdempotencyKeyError (409) — same Idempotency-Key reused
         with a different body is a client bug (Serrano BLOCKER 1).
      3. Translating sqlite3.IntegrityError by SQLite extended error code
         (`sqlite_errorcode`), not by substring match on English prose
         (Serrano BLOCKER 3 / Aurelius reframe). UNIQUE on the partial
         (tenant_id, scan_id) index → UniqueViolationError (409); CHECK
         violations mapped by inspecting the CHECK fragment SQLite included
         in the error message.
      4. The post-commit update_positions_json (closes F8). If the snapshot
         write itself fails, emit a structured log.error with a distinct
         event name (POSITION_SNAPSHOT_STALE) so the staleness is
         observable — the position is durable in the DB but the JSON
         snapshot may be stale (Serrano HIGH 5). Re-raise is out of scope:
         the DB row is correct; observability is the contract.
      5. Structured logging at birth: POSICION OPENED + scan_id (closes F15
         per the original plan spec; scan_id was dropped from the log line
         in the prior revision — Serrano MEDIUM 13).

    Per Voronov 2026-05-26: NOT a symmetric operator to PositionClosure.
    `close()` validates a transition between two known states of the same
    object; `open()` validates a nomination act. They're cousins, not
    siblings. BirthRegistrar is an op-ligero — validation already happened
    upstream; the registrar only owns "registrar el acto, no validarlo".
    """

    @staticmethod
    def register(validated: ValidatedOpenRequest) -> dict:
        # Local imports avoid a circular at module load (api.positions imports
        # api.positions_birth, and we want update_positions_json from there).
        from db.positions import db_create_position_sql  # noqa: PLC0415

        # Compute the body fingerprint once. Independent of the cache hit;
        # we use it for both the mismatch guard (on hit) and the cache
        # write (on miss).
        body_fingerprint = _canonical_body_fingerprint(validated.payload)

        # ONE transaction spanning: idempotency probe + INSERT + cache write.
        # BEGIN IMMEDIATE serializes concurrent same-key requests at the
        # SQLite reserved-writer lock so the probe-and-write gap that
        # Serrano BLOCKER 2 named cannot open. The Aurelius reframe is
        # exactly this: "collapse the rungs into one transaction".
        try:
            with transaction() as con:
                # --- Step 1: idempotency probe inside the write tx.
                if validated.idempotency_key:
                    cached = IdempotencyCache.get(
                        con, validated.tenant_id, validated.idempotency_key,
                    )
                    if cached is not None:
                        existing_fp = cached.get("body_sha256")
                        # Mismatch → reject (Serrano BLOCKER 1). A NULL
                        # fingerprint on the cached row predates this
                        # migration — treat it as "no fingerprint to
                        # compare", and allow the replay (conservative; a
                        # stricter policy would reject NULL too).
                        if existing_fp is not None and existing_fp != body_fingerprint:
                            log.warning(
                                "BirthRegistrar: Idempotency-Key reuse with "
                                "different body tenant=%s key=%s "
                                "existing_fp=%s new_fp=%s",
                                validated.tenant_id,
                                validated.idempotency_key,
                                existing_fp,
                                body_fingerprint,
                            )
                            raise DuplicateIdempotencyKeyError(
                                "Idempotency-Key reused with a different body",
                                detail={
                                    "tenant_id": validated.tenant_id,
                                    "idempotency_key": validated.idempotency_key,
                                    "existing_body_sha256": existing_fp,
                                    "new_body_sha256": body_fingerprint,
                                },
                            )
                        # Match → replay the cached row. Return inside the
                        # tx; the with-block commits cleanly (the only
                        # work performed was the lazy DELETE of expired
                        # rows inside IdempotencyCache.get, which is safe
                        # to commit).
                        pos = cached["result"]
                        log.info(
                            "BirthRegistrar: idempotent replay tenant=%s "
                            "key=%s pos_id=%s",
                            validated.tenant_id, validated.idempotency_key,
                            pos.get("id"),
                        )
                        return pos

                # --- Step 2: position INSERT in the same tx.
                pos = db_create_position_sql(con, validated)

                # --- Step 3: cache write (same tx — idempotent retry safe).
                if validated.idempotency_key:
                    IdempotencyCache.set(
                        con,
                        validated.tenant_id,
                        validated.idempotency_key,
                        pos,
                        body_fingerprint,
                    )
        except sqlite3.IntegrityError as e:
            # Translate by sqlite_errorcode, not by substring match on prose
            # (Serrano BLOCKER 3 / Aurelius reframe). The extended error
            # code is stable across SQLite versions; English wording is not.
            raise _translate_integrity_error(e, validated) from e
        # DuplicateIdempotencyKeyError is a BirthError, not an IntegrityError
        # — the except above does not swallow it; it propagates from the
        # `with transaction()` block (which rolled back on the way out).

        # --- Step 4: post-commit snapshot regeneration (F8).
        # Cannot fold into the SQL tx — the JSON file is filesystem state.
        # If the write fails, emit a structured error and move on; the DB
        # row is durable (Serrano HIGH 5).
        # Import via api.positions module to honor the test seam that lets
        # the JSON write be monkeypatched at the api.positions namespace.
        from api import positions as _api_positions  # noqa: PLC0415
        try:
            _api_positions.update_positions_json()
        except Exception as snap_err:  # noqa: BLE001
            log.error(
                "POSITION_SNAPSHOT_STALE pos_id=%s tenant=%s symbol=%s "
                "snapshot_error=%s",
                pos.get("id"), validated.tenant_id,
                validated.payload.symbol, snap_err,
            )

        # --- Step 5: F15 structured log at birth (with scan_id per spec).
        log.info(
            "POSICION OPENED #%s %s @ $%s scan_id=%s by tenant=%s",
            pos["id"],
            validated.payload.symbol,
            validated.payload.entry_price,
            validated.payload.scan_id,
            validated.tenant_id,
        )
        return pos


def _translate_integrity_error(
    e: sqlite3.IntegrityError,
    validated: ValidatedOpenRequest,
) -> BirthError:
    """Map sqlite3.IntegrityError to the correct BirthError subclass by
    inspecting the extended error code (`sqlite_errorcode`, Python 3.11+).

    Serrano BLOCKER 3 / Aurelius reframe: error translation belongs to the
    layer that originates the error, not to a substring of its prose. The
    extended code is the only stable cross-version discriminator SQLite
    offers. Where the code is too coarse (CHECK fires for multiple
    constraints), we discriminate on the CHECK fragment SQLite itself
    included in the error message — still inside the originating layer,
    but using SQLite's own constraint-naming output, not English prose.
    """
    code = getattr(e, "sqlite_errorcode", None)
    msg = str(e)

    # SQLITE_CONSTRAINT_UNIQUE (2067) — only one path on positions today:
    # the partial UNIQUE index idx_positions_open_scan_unique on
    # (tenant_id, scan_id) WHERE status='open' AND scan_id IS NOT NULL.
    # SQLite reports the columns in str(e) (e.g. "positions.tenant_id,
    # positions.scan_id"); discriminate on that to future-proof against
    # any new UNIQUE index added to the positions table.
    if code == _SQLITE_CONSTRAINT_UNIQUE:
        if "positions.scan_id" in msg or "positions.tenant_id" in msg:
            return UniqueViolationError(
                "An open position already exists for this scan_id",
                detail={
                    "tenant_id": validated.tenant_id,
                    "scan_id": validated.payload.scan_id,
                    "sqlite_errorname": getattr(e, "sqlite_errorname", None),
                    "sqlite_errorcode": code,
                },
            )
        return SchemaIntegrityError(
            "UNIQUE constraint violated",
            detail={
                "sqlite_errorname": getattr(e, "sqlite_errorname", None),
                "sqlite_errorcode": code,
                "message": msg,
            },
        )

    # SQLITE_CONSTRAINT_CHECK (275) — three CHECK fragments live on
    # positions today (qty>0, tenant_id NOT NULL, qty NOT NULL legacy).
    # SQLite echoes the fragment in str(e); discriminate.
    if code == _SQLITE_CONSTRAINT_CHECK:
        if "qty > 0" in msg or "qty IS NOT NULL" in msg or "qty IS NULL" in msg:
            return AmbiguousQtyError(
                "qty violates schema CHECK (must be > 0 for active positions)",
                detail={
                    "sqlite_errorname": getattr(e, "sqlite_errorname", None),
                    "sqlite_errorcode": code,
                    "check_fragment": msg,
                },
            )
        if "tenant_id" in msg:
            return TenantViolationError(
                "tenant_id violates schema CHECK",
                detail={
                    "sqlite_errorname": getattr(e, "sqlite_errorname", None),
                    "sqlite_errorcode": code,
                    "check_fragment": msg,
                },
            )
        return SchemaIntegrityError(
            "CHECK constraint violated",
            detail={
                "sqlite_errorname": getattr(e, "sqlite_errorname", None),
                "sqlite_errorcode": code,
                "check_fragment": msg,
            },
        )

    # SQLITE_CONSTRAINT_NOTNULL (1299) — a NOT NULL column was missing.
    # Pydantic should have caught this upstream; reaching here is
    # defense-in-depth.
    if code == _SQLITE_CONSTRAINT_NOTNULL:
        return SchemaIntegrityError(
            "NOT NULL constraint violated",
            detail={
                "sqlite_errorname": getattr(e, "sqlite_errorname", None),
                "sqlite_errorcode": code,
                "message": msg,
            },
        )

    # Any other IntegrityError (FK violation, PK violation outside the
    # idempotency table — we never INSERT into idempotency_keys with a
    # client-controlled key collision since the registrar OR-REPLACEs).
    # Surface as SchemaIntegrityError so the route returns a structured
    # 422 with triage detail, not a bare 500 leaking str(e).
    return SchemaIntegrityError(
        "Schema integrity constraint violated",
        detail={
            "sqlite_errorname": getattr(e, "sqlite_errorname", None),
            "sqlite_errorcode": code,
            "message": msg,
        },
    )
