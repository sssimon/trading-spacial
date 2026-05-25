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
