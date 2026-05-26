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
    """Position passed all precheck conditions. The snapshot is an
    OwnershipValidatedSnapshot — a type-level guarantee that ownership was
    checked at precheck time. The write-tx MUST STILL re-validate the
    snapshot's mutable fields against a fresh re-SELECT inside BEGIN
    IMMEDIATE (immutable fields trusted directly)."""
    snapshot: "OwnershipValidatedSnapshot"


@dataclass(frozen=True)
class PrecheckRejectedState:
    """Position exists but is in a status neither 'open' nor 'closed' (e.g.,
    'cancelled', 'liquidated', etc.). Caller must inspect snapshot.status to
    decide handling. Distinct from PrecheckAlreadyClosed to avoid semantic
    collapse (F2 finding from Serrano, Voronov reframe — PR #466)."""
    snapshot: PositionSnapshot


PrecheckResult = Union[PrecheckNotFound, PrecheckAlreadyClosed, PrecheckOkToProceed, PrecheckRejectedState]


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
