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
