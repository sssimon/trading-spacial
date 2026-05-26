"""Tests for PrecheckOriginatedSnapshot provenance pattern (#469 + F6, #477).

The class is a PROVENANCE marker, not a safety check. It records that
construction went through the precheck factory. Ownership safety is
enforced downstream by `PositionClosure.execute()`'s field-by-field
re-validation against a fresh re-SELECT inside `BEGIN IMMEDIATE`.

Per Voronov 2026-05-26 4th meta-review (#477 closed via Path 6): the
type carries a provenance claim, not a safety claim. The previous name
(`OwnershipValidatedSnapshot`) overclaimed; this file's tests verify the
provenance organ behaves correctly without re-asserting any safety
guarantee the type does not in fact make.

The runtime check (`_sentinel is _ORIGINATION_SENTINEL`) is real (rung:
convención — Python does not enforce the single-underscore boundary;
honesty about the rung is the structural commitment). The factory's
single call site is convention only. These tests verify the marker; the
safety tests live in `test_position_closure.py`'s re-validation suite.
"""
import pytest


def test_cannot_construct_without_sentinel():
    """PrecheckOriginatedSnapshot raises TypeError if constructed with a
    foreign sentinel (i.e., any object other than the module-private
    _ORIGINATION_SENTINEL)."""
    from operators.precheck import PrecheckOriginatedSnapshot, PositionSnapshot

    snap = PositionSnapshot(
        pos_id=1, tenant_id=42, status="open",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )

    # Try with a wrong sentinel (e.g., another object()). The match anchors
    # on the factory name — the most stable identifier in the error message.
    with pytest.raises(TypeError, match=r"_build_originated_snapshot"):
        PrecheckOriginatedSnapshot(inner=snap, _sentinel=object())


def test_cannot_construct_with_none_sentinel():
    """PrecheckOriginatedSnapshot raises TypeError if _sentinel is None."""
    from operators.precheck import PrecheckOriginatedSnapshot, PositionSnapshot

    snap = PositionSnapshot(
        pos_id=1, tenant_id=42, status="open",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )

    with pytest.raises(TypeError, match=r"_build_originated_snapshot"):
        PrecheckOriginatedSnapshot(inner=snap, _sentinel=None)


def test_internal_factory_builds_originated_snapshot():
    """_build_originated_snapshot is the INTENDED constructor by single-
    underscore convention. Python does not prevent direct construction
    via importing _ORIGINATION_SENTINEL — that wider asymmetry is
    acceptable because the type carries provenance, not safety (Voronov
    #477 4th meta-review). This test only asserts the factory path works."""
    from operators.precheck import (
        _build_originated_snapshot,
        PrecheckOriginatedSnapshot,
        PositionSnapshot,
    )

    snap = PositionSnapshot(
        pos_id=1, tenant_id=42, status="open",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )

    originated = _build_originated_snapshot(snap)
    assert isinstance(originated, PrecheckOriginatedSnapshot)
    assert originated.inner == snap


def test_error_message_does_not_overclaim_enforcement():
    """The PrecheckOriginatedSnapshot construction error must not claim
    'callable only from X' — nothing in the runtime enforces caller identity.

    The sentinel pattern is real (rung: convención — Python's single-
    underscore boundary is convention only). The factory's single call
    site is convention. The message must reflect this asymmetry truthfully.

    Additionally (Voronov #477 4th meta-review): the message must NOT
    claim the sentinel enforces ownership safety — that is the job of
    downstream re-validation, not this type. The message names provenance
    and points at the downstream organ.

    See #477 (registry coherence, closed via Path 6) and #481 (doc honesty).
    """
    from operators.precheck import PrecheckOriginatedSnapshot, PositionSnapshot

    snap = PositionSnapshot(
        pos_id=1, tenant_id=42, status="open",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )

    try:
        PrecheckOriginatedSnapshot(inner=snap, _sentinel=object())
    except TypeError as exc:
        message = str(exc)
    else:
        pytest.fail("Expected TypeError when constructing with a foreign sentinel")

    # Positive: must name the factory (so readers know the legitimate entry).
    assert "_build_originated_snapshot" in message, (
        f"message must reference the factory by name; got: {message!r}"
    )
    # Negative: must NOT claim runtime enforcement of the caller. The previous
    # wording 'callable only from operators.position_closure._run_precheck'
    # was a documentation lie — nothing in the code checked the caller frame.
    assert "callable only" not in message, (
        f"message must not promise enforcement that does not exist; got: {message!r}"
    )
    # Positive: must explicitly acknowledge the convention rung, so future
    # readers don't infer a guarantee from the absence of qualifiers.
    assert "convention" in message.lower(), (
        f"message must name the convention rung explicitly; got: {message!r}"
    )
    # Positive (#477 4th meta-review): must name provenance, not validation.
    # The previous error message overclaimed by association with the old
    # type name (`OwnershipValidatedSnapshot`); the rename + new message
    # commit to "this marks origin, not safety."
    assert "provenance" in message.lower(), (
        f"message must name provenance as the actual semantic; got: {message!r}"
    )
    # Positive (#477 4th meta-review): must point at where the safety
    # claim actually lives — PositionClosure.execute()'s re-validation.
    # Without this pointer, a reader who sees only the type name might
    # still infer the type is the safety organ.
    assert "execute" in message.lower() or "re-validation" in message.lower(), (
        f"message must point at where the safety organ actually lives; got: {message!r}"
    )


def test_PrecheckOkToProceed_carries_PrecheckOriginatedSnapshot():
    """PrecheckOkToProceed.snapshot is typed as PrecheckOriginatedSnapshot
    (not PositionSnapshot) — the type-level marker that the snapshot
    originated from the precheck factory. Safety is enforced downstream."""
    from operators.precheck import (
        PrecheckOkToProceed,
        PrecheckOriginatedSnapshot,
        _build_originated_snapshot,
        PositionSnapshot,
    )

    snap = PositionSnapshot(
        pos_id=1, tenant_id=42, status="open",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )
    originated = _build_originated_snapshot(snap)
    ok = PrecheckOkToProceed(snapshot=originated)
    assert isinstance(ok.snapshot, PrecheckOriginatedSnapshot)
    assert ok.snapshot.inner == snap


def test_provenance_marker_does_not_validate_field_contents():
    """Per Voronov #477 4th meta-review, the explicit negative test:
    PrecheckOriginatedSnapshot makes NO claim about the contents of the
    inner PositionSnapshot. A snapshot with absurd field values (e.g.,
    tenant_id pointing at any tenant the caller chose) constructed via
    the factory still passes the type's runtime check.

    This is by design. The safety frontier is downstream — in
    `PositionClosure.execute()`'s field-by-field re-validation. This
    test ensures future readers don't add `__post_init__` checks here
    thinking the type was supposed to validate fields. It is not."""
    from operators.precheck import (
        _build_originated_snapshot,
        PrecheckOriginatedSnapshot,
        PositionSnapshot,
    )

    # An attacker-shaped snapshot: nonsense tenant_id, nonsense price, etc.
    # The factory accepts it. That is the correct behavior — provenance,
    # not validation.
    absurd_snap = PositionSnapshot(
        pos_id=999999,
        tenant_id=-1,                # nonsense
        status="open",
        symbol="FAKEBTC",            # nonsense
        direction="sideways",        # nonsense (not 'long'/'short')
        entry_price=-0.0,            # nonsense
        qty=0.0,                     # nonsense
    )
    originated = _build_originated_snapshot(absurd_snap)
    assert isinstance(originated, PrecheckOriginatedSnapshot)
    assert originated.inner.tenant_id == -1
    assert originated.inner.symbol == "FAKEBTC"
    # If a future contributor adds field validation HERE, this test fails
    # and forces the rename of the type or the relocation of the check.
