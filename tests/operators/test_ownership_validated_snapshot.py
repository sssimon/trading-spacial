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

    # Try with a wrong sentinel (e.g., another object()). The match anchors
    # on the factory name — the most stable identifier in the error message.
    with pytest.raises(TypeError, match=r"_build_validated_snapshot"):
        OwnershipValidatedSnapshot(inner=snap, _sentinel=object())


def test_cannot_construct_with_none_sentinel():
    """OwnershipValidatedSnapshot raises TypeError if _sentinel is None."""
    from operators.precheck import OwnershipValidatedSnapshot, PositionSnapshot

    snap = PositionSnapshot(
        pos_id=1, tenant_id=42, status="open",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )

    with pytest.raises(TypeError, match=r"_build_validated_snapshot"):
        OwnershipValidatedSnapshot(inner=snap, _sentinel=None)


def test_internal_factory_builds_validated_snapshot():
    """_build_validated_snapshot is the INTENDED constructor by single-
    underscore convention. Python does not prevent direct construction
    via importing _VALIDATION_SENTINEL — that wider asymmetry (both
    factory and sentinel surfaces of the convention bound) is tracked in
    #477. This test only asserts the factory path itself works."""
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


def test_error_message_does_not_overclaim_enforcement():
    """The OwnershipValidatedSnapshot construction error must not claim
    'callable only from X' — nothing in the runtime enforces caller identity.

    The sentinel pattern is real (rung: tipo), but the factory's single
    call site is convention only (rung: convención). The message must
    reflect this asymmetry truthfully.

    See #477 (registry coherence) and #481 (doc honesty).
    """
    from operators.precheck import OwnershipValidatedSnapshot, PositionSnapshot

    snap = PositionSnapshot(
        pos_id=1, tenant_id=42, status="open",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )

    try:
        OwnershipValidatedSnapshot(inner=snap, _sentinel=object())
    except TypeError as exc:
        message = str(exc)
    else:
        pytest.fail("Expected TypeError when constructing with a foreign sentinel")

    # Positive: must name the factory (so readers know the legitimate entry).
    assert "_build_validated_snapshot" in message, (
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
    # Anchored to English single-form — the message lives in English; a
    # polyglot `or` would let drift hide a regression (Serrano F4).
    assert "convention" in message.lower(), (
        f"message must name the convention rung explicitly; got: {message!r}"
    )


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
