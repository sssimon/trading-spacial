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
    OwnershipValidatedSnapshot.

    The sentinel check in OwnershipValidatedSnapshot.__post_init__ is the
    runtime órgano (rung: tipo); the factory's single call site is
    convention only (rung: convención). The sentinel itself is module-
    attribute-accessible, which widens the convención surface beyond the
    factory — both surfaces (factory name and sentinel name) of the
    convention bound are tracked in #477.

    The write-tx MUST STILL re-validate the snapshot's mutable fields
    against a fresh re-SELECT inside BEGIN IMMEDIATE (immutable fields
    trusted directly).

    Runtime organ: __post_init__ rejects construction with a raw
    PositionSnapshot (i.e., without the sentinel). The 'tipo' rung in
    CLAUDE.md is real only because this check exists (mypy is not in CI;
    annotation alone does not refuse). Anyone who imports
    _VALIDATION_SENTINEL can construct directly without going through the
    factory — see #477.
    """
    snapshot: "OwnershipValidatedSnapshot"

    def __post_init__(self):
        if not isinstance(self.snapshot, OwnershipValidatedSnapshot):
            raise TypeError(
                f"PrecheckOkToProceed.snapshot must be an OwnershipValidatedSnapshot, "
                f"got {type(self.snapshot).__name__}. Use _build_validated_snapshot."
            )


@dataclass(frozen=True)
class PrecheckRejectedState:
    """Position exists but is in a status neither 'open' nor 'closed' (e.g.,
    'cancelled', 'liquidated', etc.). Caller must inspect snapshot.status to
    decide handling. Distinct from PrecheckAlreadyClosed to avoid semantic
    collapse (F2 finding from Serrano, Voronov reframe — PR #466)."""
    snapshot: PositionSnapshot


PrecheckResult = Union[PrecheckNotFound, PrecheckAlreadyClosed, PrecheckOkToProceed, PrecheckRejectedState]


# Module-private sentinel — single-underscore convention only; Python does
# NOT prevent `from operators.precheck import _VALIDATION_SENTINEL`. The
# __post_init__ check is by `is` identity, so any caller who imports this
# name can construct an OwnershipValidatedSnapshot for any PositionSnapshot.
# This is one of two surfaces of the convention bound tracked in #477
# (factory name + sentinel name both single-underscore-convention only).
_VALIDATION_SENTINEL = object()


@dataclass(frozen=True)
class OwnershipValidatedSnapshot:
    """A PositionSnapshot whose ownership has been validated by a precheck.

    USER mode: caller_tenant_id matched snapshot.tenant_id at precheck time.
    SYSTEM mode: ownership validation does not apply; the snapshot is
    accepted by construction.

    Construction requires the module-private _VALIDATION_SENTINEL. The
    runtime organ is the sentinel check in `__post_init__` (rung: tipo). The
    intended constructor is `_build_validated_snapshot`, whose single call
    site is `operators.position_closure.PositionClosure._run_precheck`; the
    factory's single-call-site property is convention only (rung: convención
    — see #477 for the registry-coherence follow-up).

    A future write-tx that consumes this type is guaranteed (by construction)
    that ownership was checked at precheck. The write-tx MUST STILL re-validate
    the snapshot's mutable fields against a fresh re-SELECT — that is enforced
    in PositionClosure.execute() by explicit field-by-field comparison.

    Originally framed as "Closes #469 + F6 (Voronov path C + E): the
    validation guarantee lives in the type, not in a docstring." The
    Voronov registry-coherence rule (post-Serrano) qualifies this: the
    guarantee is bounded by the weakest organ that enforces it. #469's
    closure is rung tipo (sentinel `is` check) PLUS rung convención
    (factory name + sentinel name both single-underscore-convention only).
    The wider invariant (both surfaces of the convention bound) is tracked
    in #477, advanced by PR #486 (Path 3 honest narrowing).
    """
    inner: PositionSnapshot
    _sentinel: object  # must be _VALIDATION_SENTINEL at construction

    def __post_init__(self):
        if self._sentinel is not _VALIDATION_SENTINEL:
            raise TypeError(
                "OwnershipValidatedSnapshot cannot be constructed directly. "
                "Use operators.precheck._build_validated_snapshot. "
                "By convention (not enforced at runtime), the factory's "
                "single call site is "
                "operators.position_closure.PositionClosure._run_precheck. "
                "See #477."
            )


def _build_validated_snapshot(snapshot: PositionSnapshot) -> OwnershipValidatedSnapshot:
    """Module-private factory used by PositionClosure._run_precheck.

    The single-underscore prefix is convention only; Python does not enforce
    the module-private boundary at import time. Calling this from any other
    module will succeed at runtime — the rung is convención, not tipo.

    See #477 for the open registry-coherence follow-up (whether to install a
    real organ via frame inspection / closure pattern / module relocation, or
    accept the asymmetry permanently).
    """
    return OwnershipValidatedSnapshot(inner=snapshot, _sentinel=_VALIDATION_SENTINEL)
