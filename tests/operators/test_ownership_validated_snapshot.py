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

    # Try with a wrong sentinel (e.g., another object()).
    with pytest.raises(TypeError, match=r"private validation sentinel"):
        OwnershipValidatedSnapshot(inner=snap, _sentinel=object())


def test_cannot_construct_with_none_sentinel():
    """OwnershipValidatedSnapshot raises TypeError if _sentinel is None."""
    from operators.precheck import OwnershipValidatedSnapshot, PositionSnapshot

    snap = PositionSnapshot(
        pos_id=1, tenant_id=42, status="open",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )

    with pytest.raises(TypeError, match=r"private validation sentinel"):
        OwnershipValidatedSnapshot(inner=snap, _sentinel=None)


def test_internal_factory_builds_validated_snapshot():
    """_build_validated_snapshot is the only legitimate constructor.
    It is module-private (underscore prefix), not exported from precheck's
    public surface."""
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
