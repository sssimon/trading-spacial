"""Precheck pattern types — immutable hand-off from read-side to write-tx.

Reifies the pattern Voronov articulated for PR #463 follow-up: a precheck
reads outside any transaction, decides whether a follow-up write is needed,
and produces an immutable snapshot for the write-tx to re-validate against.

PositionSnapshot is intentionally minimal: it carries only the fields that
(a) the precheck has DECIDED upon, and (b) the write-tx must RE-VALIDATE
inside its BEGIN IMMEDIATE block. Adding a field here = the write-tx must
re-validate it. Removing a field = the precheck no longer commits to it.

## Provenance vs. safety (Voronov 2026-05-26 fourth meta-review, #477)

`PrecheckOriginatedSnapshot` marks **provenance** ("this snapshot came from
a precheck factory"). It does NOT enforce ownership safety — that is the
job of `PositionClosure.execute()`'s field-by-field re-validation against
a fresh re-SELECT inside `BEGIN IMMEDIATE`.

The previous name `OwnershipValidatedSnapshot` was a misnomer: the type's
runtime check (`_sentinel is _ORIGINATION_SENTINEL`) verifies provenance,
not validation. An attacker who imports `_ORIGINATION_SENTINEL` and
constructs the type with a forged `tenant_id` does NOT bypass the
ownership check — it bypasses the provenance marker, then gets caught by
the downstream re-validation in `execute()`.

This is the structural reframe that closes #477: the type carries a
provenance claim, not a safety claim. The convention boundary
(single-underscore on the factory + sentinel) is acceptable because the
SAFETY frontier is enforced downstream, not at this type's construction
site. Path 6 (honest acceptance + rename) was chosen over Path 3 (frame
inspection, structurally brittle) and Path 5-as-doc-only (insufficient,
because the type name itself overclaimed). The conventions registry has
been split correspondingly: one row for provenance (rung convención), one
row for re-validation (rung runtime check). See `.mex/context/conventions.md`.
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
    """Position passed all precheck conditions. The snapshot is a
    PrecheckOriginatedSnapshot — a provenance marker, not a safety claim.

    The sentinel check in PrecheckOriginatedSnapshot.__post_init__ verifies
    the snapshot was produced by the precheck factory. It does NOT enforce
    ownership safety — `PositionClosure.execute()`'s field-by-field
    re-derivation against a fresh re-SELECT inside BEGIN IMMEDIATE is the
    safety organ. Per Voronov #477 4th meta-review:
    *"The sentinel is not load-bearing. The field-by-field re-validation is."*

    Runtime organ at THIS layer: __post_init__ rejects construction with a
    raw PositionSnapshot (i.e., without going through the type system).
    This makes the precheck/execute hand-off legible at type-signature
    level (the contract "snapshot came from a precheck" is visible without
    reading code). The 'tipo' rung in CLAUDE.md is real only because this
    check exists (mypy is not in CI; annotation alone does not refuse).

    The factory `_build_originated_snapshot` and the sentinel
    `_ORIGINATION_SENTINEL` are both single-underscore-convention-private.
    Python does not enforce these as import barriers. That is acceptable
    here because the type's job is provenance, not safety — see this
    module's docstring and #477.
    """
    snapshot: "PrecheckOriginatedSnapshot"

    def __post_init__(self):
        if not isinstance(self.snapshot, PrecheckOriginatedSnapshot):
            raise TypeError(
                f"PrecheckOkToProceed.snapshot must be a PrecheckOriginatedSnapshot, "
                f"got {type(self.snapshot).__name__}. Use _build_originated_snapshot."
            )


@dataclass(frozen=True)
class PrecheckRejectedState:
    """Position exists but is in a status neither 'open' nor 'closed' (e.g.,
    'cancelled', 'liquidated', etc.). Caller must inspect snapshot.status to
    decide handling. Distinct from PrecheckAlreadyClosed to avoid semantic
    collapse (F2 finding from Serrano, Voronov reframe — PR #466)."""
    snapshot: PositionSnapshot


PrecheckResult = Union[PrecheckNotFound, PrecheckAlreadyClosed, PrecheckOkToProceed, PrecheckRejectedState]


# Module-private origination sentinel. The single-underscore prefix is
# Python convention only — `from operators.precheck import
# _ORIGINATION_SENTINEL` works. Per Voronov 2026-05-26 4th meta-review
# (#477): this is acceptable because the sentinel marks PROVENANCE
# ("snapshot came from the precheck factory"), not SAFETY ("ownership was
# validated"). The safety frontier is enforced in PositionClosure.execute()
# by field-by-field re-validation against a fresh re-SELECT — an attacker
# who imports this sentinel and fabricates a snapshot still gets caught
# there. The convention boundary at THIS layer is honest naming for what
# the type actually carries, not a half-built lock on a door behind a
# corridor where every visitor is searched.
_ORIGINATION_SENTINEL = object()


@dataclass(frozen=True)
class PrecheckOriginatedSnapshot:
    """A PositionSnapshot whose provenance is "produced by the precheck factory."

    Previously named `OwnershipValidatedSnapshot`. The old name overclaimed:
    the runtime check (`_sentinel is _ORIGINATION_SENTINEL`) verifies that
    construction went through `_build_originated_snapshot`, not that the
    snapshot carries any safety guarantee. The precheck logic makes an
    ownership DECISION against possibly-stale state (the `tenant_id`
    comparison that collapses to `PrecheckNotFound` for IDOR-safety); that
    decision is advisory. Safety binds only when `PositionClosure.execute()`
    re-derives the comparison against a fresh re-SELECT inside
    `BEGIN IMMEDIATE`. The word "validated" applies to exactly one frontier
    — the write-tx re-derivation — not redistributed across the read-side
    and the write-side (Voronov 2026-05-26 5th meta-review B1).
    The re-derivation is the safety organ. This type is the provenance marker.

    Per Voronov 2026-05-26 4th meta-review (closes #477):

        "The sentinel is not load-bearing. The field-by-field re-validation
        in PositionClosure.execute() is load-bearing. The sentinel is a
        provenance marker masquerading as a safety check."

    Construction requires `_ORIGINATION_SENTINEL` (rung: convención, not
    tipo — Python does not enforce import barriers; rename + honest
    documentation is the structural commitment, see #477). The intended
    constructor is `_build_originated_snapshot`, whose single call site
    is `operators.position_closure.PositionClosure._run_precheck`.

    A consumer that receives this type knows the snapshot was produced
    via the precheck factory. The consumer MUST STILL re-validate the
    snapshot's mutable fields against a fresh re-SELECT — that is enforced
    in PositionClosure.execute() by explicit field-by-field comparison.

    Per the registry's coherence rule (the weakest organ at a frontier
    bounds the guarantee at that frontier): this type's guarantee is
    "snapshot came from a precheck" (rung convención — the import-surface
    convention is the weakest organ at this frontier). The downstream
    re-validation in execute() is "fields still match the DB" (rung
    runtime check) — a separate predicate at a separate frontier. The
    conventions registry has one row for each.
    """
    inner: PositionSnapshot
    _sentinel: object  # must be _ORIGINATION_SENTINEL at construction

    def __post_init__(self):
        if self._sentinel is not _ORIGINATION_SENTINEL:
            raise TypeError(
                "PrecheckOriginatedSnapshot cannot be constructed directly. "
                "Use operators.precheck._build_originated_snapshot. "
                "This sentinel marks provenance (the snapshot came from a "
                "precheck), not ownership safety — ownership safety is "
                "enforced downstream by PositionClosure.execute()'s "
                "field-by-field re-validation against a fresh re-SELECT "
                "inside BEGIN IMMEDIATE. By convention (not enforced at "
                "runtime), the factory's single call site is "
                "operators.position_closure.PositionClosure._run_precheck. "
                "See #477 (resolved via Path 6: honest acceptance + rename, "
                "Voronov 2026-05-26 4th meta-review)."
            )


def _build_originated_snapshot(snapshot: PositionSnapshot) -> PrecheckOriginatedSnapshot:
    """Module-private factory used by PositionClosure._run_precheck.

    The single-underscore prefix is convention only; Python does not enforce
    the module-private boundary at import time. Calling this from any other
    module will succeed at runtime — the rung is convención, not tipo.

    Previously named `_build_validated_snapshot`. The old name overclaimed:
    the factory does not validate ownership; it marks provenance. The
    precheck logic makes an ownership DECISION against possibly-stale state
    (the read-side `tenant_id` comparison that collapses to `PrecheckNotFound`
    for an IDOR-safe response). That decision is advisory. Safety binds only
    when `PositionClosure.execute()`'s field-by-field re-validation against
    a fresh re-SELECT inside `BEGIN IMMEDIATE` passes — that is the single
    place in the lineage where the word "validated" applies.

    Per Voronov 2026-05-26 4th meta-review (#477 closed via Path 6) +
    5th meta-review B1: "validated" appears in exactly one frontier — the
    write-tx — not redistributed across the read-side and the write-side.
    """
    return PrecheckOriginatedSnapshot(inner=snapshot, _sentinel=_ORIGINATION_SENTINEL)
