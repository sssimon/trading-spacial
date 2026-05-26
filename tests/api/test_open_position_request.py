"""Tests for api.positions_birth.OpenPositionRequest (Pydantic body model)
— closes #471 F5/F6/F7/F9 and #473 input-validation portion.
"""
from datetime import datetime, timedelta, timezone
import pytest
from pydantic import ValidationError


def _now():
    return datetime.now(timezone.utc)


def test_minimal_valid_request_parses():
    from api.positions_birth import OpenPositionRequest

    req = OpenPositionRequest(
        symbol="BTCUSDT",
        entry_price=100.0,
        direction="LONG",
        qty=10.0,
    )
    assert req.symbol == "BTCUSDT"
    assert req.entry_price == 100.0
    assert req.direction == "LONG"
    assert req.qty == 10.0
    assert req.entry_ts is None


def test_symbol_lowercase_is_uppercased():
    from api.positions_birth import OpenPositionRequest

    req = OpenPositionRequest(
        symbol="btcusdt", entry_price=100.0, direction="LONG", qty=10.0,
    )
    assert req.symbol == "BTCUSDT"


def test_symbol_not_in_allowlist_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"not in curated allowlist"):
        OpenPositionRequest(
            symbol="BOGUSCOIN", entry_price=100.0, direction="LONG", qty=10.0,
        )


def test_entry_price_zero_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"entry_price must be > 0"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=0, direction="LONG", qty=10.0,
        )


def test_entry_price_negative_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"entry_price must be > 0"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=-1, direction="LONG", qty=10.0,
        )


def test_direction_required_no_default():
    """F12 / F7: direction must be provided (no default to LONG)."""
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError):
        OpenPositionRequest(symbol="BTCUSDT", entry_price=100.0, qty=10.0)


def test_direction_invalid_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="long", qty=10.0,
        )


def test_qty_required_no_size_usd_fallback():
    """F5: qty is required; no 5-deep fallback chain via size_usd."""
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="LONG", size_usd=1000.0,
        )


def test_qty_zero_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"qty must be > 0"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=0,
        )


def test_qty_negative_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"qty must be > 0"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=-1,
        )


def test_extra_field_tenant_id_in_body_rejected():
    """F6: tenant_id in body must be rejected (extra='forbid')."""
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=10.0,
            tenant_id=99,
        )


def test_extra_arbitrary_field_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=10.0,
            bogus_field="anything",
        )


def test_qty_size_usd_consistent_accepted():
    from api.positions_birth import OpenPositionRequest

    req = OpenPositionRequest(
        symbol="BTCUSDT", entry_price=100.0, direction="LONG",
        qty=10.0, size_usd=1000.0,
    )
    assert req.size_usd == 1000.0


def test_qty_size_usd_inconsistent_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"size_usd"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="LONG",
            qty=10.0, size_usd=500.0,  # 10 * 100 = 1000, not 500
        )


def test_entry_ts_within_window_accepted():
    from api.positions_birth import OpenPositionRequest

    req = OpenPositionRequest(
        symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=10.0,
        entry_ts=_now() - timedelta(hours=1),
    )
    assert req.entry_ts is not None


def test_entry_ts_far_future_rejected():
    """F9: entry_ts more than 60s in the future is rejected."""
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"60s in the future"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=10.0,
            entry_ts=_now() + timedelta(days=30),
        )


def test_entry_ts_too_old_rejected():
    """F9: entry_ts more than 7 days in the past is rejected."""
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"7 days in the past"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=10.0,
            entry_ts=_now() - timedelta(days=10),
        )


def test_entry_ts_within_60s_future_accepted():
    """Small clock skew tolerated."""
    from api.positions_birth import OpenPositionRequest

    req = OpenPositionRequest(
        symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=10.0,
        entry_ts=_now() + timedelta(seconds=30),
    )
    assert req.entry_ts is not None


def test_long_sl_above_entry_rejected():
    """F7: SL/TP relational checks per direction."""
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"LONG.*sl_price.*< entry_price"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=10.0,
            sl_price=110.0,
        )


def test_long_tp_below_entry_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"LONG.*tp_price.*> entry_price"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=10.0,
            tp_price=90.0,
        )


def test_short_sl_below_entry_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"SHORT.*sl_price.*> entry_price"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="SHORT", qty=10.0,
            sl_price=90.0,
        )


def test_short_tp_above_entry_rejected():
    from api.positions_birth import OpenPositionRequest

    with pytest.raises(ValidationError, match=r"SHORT.*tp_price.*< entry_price"):
        OpenPositionRequest(
            symbol="BTCUSDT", entry_price=100.0, direction="SHORT", qty=10.0,
            tp_price=110.0,
        )


def test_full_valid_request_long_with_all_fields():
    from api.positions_birth import OpenPositionRequest

    req = OpenPositionRequest(
        symbol="ETHUSDT",
        entry_price=2000.0,
        direction="LONG",
        qty=0.5,
        size_usd=1000.0,
        scan_id=123,
        sl_price=1900.0,
        tp_price=2200.0,
        atr_entry=50.0,
        be_mult=1.5,
        notes="manual entry from dashboard",
    )
    assert req.scan_id == 123
    assert req.notes == "manual entry from dashboard"
