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


# ---------------- Typed error taxonomy (D-Tipo rung, semantic shape) ----------------
#
# Note on currently-unraised errors (planner spec decision #5):
#   - AmbiguousQtyError and StaleEntryTsError are DEFINED for taxonomy
#     completeness, but the underlying violations are caught by Pydantic
#     validators above (cross-field check + entry_ts window) and surface as
#     BodyValidationError today. Future extraction may move those checks
#     out of Pydantic and raise the specific subclass directly.
#   - DuplicateIdempotencyKeyError is DEFINED but not raised in this PR;
#     it is reserved for the deferred RFC 9457-style body-canonicalisation
#     check (same Idempotency-Key reused with a different body).


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


# ---------------- Idempotency-Key cache (D-Tipo HTTP rung) ----------------
#
# Task 15 stub: the underlying `idempotency_keys` table is added by Task 16
# (`db/schema.py::_migrate_idempotency_keys`). Until that lands, `get` always
# returns None (no replay) and `set` is a best-effort no-op that swallows the
# OperationalError raised by SQLite when the table doesn't exist. This keeps
# BirthRegistrar's choreography wire-correct now (cache probe at top, cache
# write inside the write-tx) and lets Task 16 light up the persistence layer
# without re-touching the registrar.

_IDEMPOTENCY_TTL = timedelta(hours=24)


class IdempotencyCache:
    """SQLite-backed cache for Idempotency-Key results keyed by (tenant_id, key).

    Task 15: stubbed. Task 16 will wire the real `idempotency_keys` table +
    24h TTL + lazy cleanup. Until then `get` returns None and `set` is a
    best-effort no-op (silently absorbs the missing-table error so the
    BirthRegistrar transaction still commits the position).
    """

    @staticmethod
    def get(con: sqlite3.Connection, tenant_id: int, key: str) -> Optional[dict]:
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            # Lazy cleanup of expired entries for this (tenant, key) pair only.
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
        except sqlite3.OperationalError:
            # Task 16 has not landed yet — table missing. Behave as cache-miss.
            return None
        if row is None:
            return None
        return json.loads(row[0])

    @staticmethod
    def set(con: sqlite3.Connection, tenant_id: int, key: str, result: dict) -> None:
        try:
            now = datetime.now(timezone.utc)
            expires = (now + _IDEMPOTENCY_TTL).isoformat()
            con.execute(
                "INSERT OR REPLACE INTO idempotency_keys "
                "(tenant_id, key, result_json, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (tenant_id, key, json.dumps(result, default=str),
                 now.isoformat(), expires),
            )
        except sqlite3.OperationalError:
            # Task 16 has not landed yet — table missing. No-op so the
            # position INSERT still commits.
            return


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
        from db.positions import db_create_position  # noqa: PLC0415
        # Step 1: idempotency probe (read-only short-circuit).
        # Stub today (Task 16 wires real persistence); structurally correct
        # so the wire-up remains stable when the table lights up.
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
        body_for_db = validated.payload.model_dump(mode="json")
        try:
            with transaction() as con:
                pos = db_create_position(
                    con, body_for_db, tenant_id=validated.tenant_id,
                )
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
                    detail={
                        "tenant_id": validated.tenant_id,
                        "scan_id": validated.payload.scan_id,
                    },
                ) from e
            raise

        # Step 4: post-commit. F8 — update_positions_json was previously
        # outside any transaction; BirthRegistrar being the single owner of
        # the INSERT-then-JSON sequence is the planner's compromise (the
        # JSON file-write cannot fold into the SQL tx; the structured log
        # below surfaces both events for op visibility).
        # Import via api.positions module to honor the test seam that lets
        # the JSON write be monkeypatched at the api.positions namespace.
        from api import positions as _api_positions  # noqa: PLC0415
        _api_positions.update_positions_json()

        # Step 5: F15 structured log at birth.
        log.info(
            "POSICION OPENED #%s %s @ $%s by tenant=%s",
            pos["id"],
            validated.payload.symbol,
            validated.payload.entry_price,
            validated.tenant_id,
        )
        return pos
