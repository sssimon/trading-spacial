"""Invariant tests for operators.precheck (PositionSnapshot + PrecheckResult)."""
import pytest

# The module does not exist yet; these tests fail at collection until Task 6.


def test_position_snapshot_is_frozen():
    """PositionSnapshot is a frozen dataclass — mutation raises FrozenInstanceError."""
    from dataclasses import FrozenInstanceError
    from operators.precheck import PositionSnapshot

    snap = PositionSnapshot(
        pos_id=1, tenant_id=42, status="open",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )
    with pytest.raises(FrozenInstanceError):
        snap.tenant_id = 99


def test_position_snapshot_equality_by_value():
    """Two PositionSnapshots with identical fields are equal (dataclass default)."""
    from operators.precheck import PositionSnapshot

    a = PositionSnapshot(
        pos_id=1, tenant_id=42, status="open",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )
    b = PositionSnapshot(
        pos_id=1, tenant_id=42, status="open",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )
    assert a == b
    assert hash(a) == hash(b)


def test_precheck_result_variants_distinguishable():
    """The 3 PrecheckResult variants are distinct types and pattern-matchable
    via isinstance."""
    from operators.precheck import (
        PositionSnapshot, PrecheckNotFound, PrecheckAlreadyClosed, PrecheckOkToProceed,
    )

    snap = PositionSnapshot(
        pos_id=1, tenant_id=42, status="open",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )

    nf = PrecheckNotFound()
    ac = PrecheckAlreadyClosed(snapshot=snap)
    ok = PrecheckOkToProceed(snapshot=snap)

    assert isinstance(nf, PrecheckNotFound)
    assert not isinstance(nf, PrecheckAlreadyClosed)
    assert not isinstance(nf, PrecheckOkToProceed)

    assert isinstance(ac, PrecheckAlreadyClosed)
    assert not isinstance(ac, PrecheckOkToProceed)

    assert isinstance(ok, PrecheckOkToProceed)
    assert ok.snapshot == snap


def test_precheck_not_found_carries_no_snapshot():
    """PrecheckNotFound is IDOR-safe: it does NOT carry the snapshot, so
    USER-mode 'belongs to another tenant' and 'does not exist at all' produce
    observationally identical values."""
    from operators.precheck import PrecheckNotFound

    a = PrecheckNotFound()
    b = PrecheckNotFound()
    assert a == b  # All instances equal — no per-instance state


def test_precheck_rejected_state_carries_snapshot():
    """PrecheckRejectedState carries the snapshot so caller can inspect
    the real status."""
    from operators.precheck import PositionSnapshot, PrecheckRejectedState

    snap = PositionSnapshot(
        pos_id=1, tenant_id=42, status="cancelled",
        symbol="BTCUSDT", direction="long",
        entry_price=100.0, qty=1.0,
    )

    rs = PrecheckRejectedState(snapshot=snap)
    assert rs.snapshot == snap
    assert rs.snapshot.status == "cancelled"
