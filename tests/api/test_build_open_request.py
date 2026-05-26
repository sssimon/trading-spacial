"""Tests for _build_open_request + ValidatedOpenRequest sentinel protection.

Mirrors the OwnershipValidatedSnapshot pattern from C2: the type is only a
guarantee if a runtime órgano de rechazo refuses the wrong sentinel.
"""
import pytest


def test_factory_returns_validated_request():
    from api.positions_birth import _build_open_request, ValidatedOpenRequest

    body = {
        "symbol": "BTCUSDT", "entry_price": 100.0,
        "direction": "LONG", "qty": 10.0,
    }
    v = _build_open_request(body, tenant_id=1, idempotency_key=None)
    assert isinstance(v, ValidatedOpenRequest)
    assert v.tenant_id == 1
    assert v.idempotency_key is None
    assert v.payload.symbol == "BTCUSDT"


def test_cannot_construct_validated_request_directly_with_wrong_sentinel():
    """Per Regla de coherencia: ValidatedOpenRequest must reject construction
    with anything other than the module-private sentinel."""
    from api.positions_birth import (
        ValidatedOpenRequest,
        OpenPositionRequest,
    )

    payload = OpenPositionRequest(
        symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=10.0,
    )
    with pytest.raises(TypeError, match=r"runtime órgano de rechazo"):
        ValidatedOpenRequest(
            payload=payload,
            tenant_id=1,
            idempotency_key=None,
            _sentinel=object(),
        )


def test_cannot_construct_validated_request_with_none_sentinel():
    from api.positions_birth import (
        ValidatedOpenRequest,
        OpenPositionRequest,
    )

    payload = OpenPositionRequest(
        symbol="BTCUSDT", entry_price=100.0, direction="LONG", qty=10.0,
    )
    with pytest.raises(TypeError, match=r"runtime órgano de rechazo"):
        ValidatedOpenRequest(
            payload=payload, tenant_id=1, idempotency_key=None, _sentinel=None,
        )


def test_factory_raises_body_validation_error_on_pydantic_failure():
    from api.positions_birth import _build_open_request, BodyValidationError

    body = {"symbol": "BTCUSDT", "entry_price": -1, "direction": "LONG", "qty": 10.0}
    with pytest.raises(BodyValidationError) as exc:
        _build_open_request(body, tenant_id=1, idempotency_key=None)
    assert exc.value.status_code == 422
    assert isinstance(exc.value.detail, list)  # pydantic errors() list


def test_factory_raises_body_validation_error_on_extra_field():
    from api.positions_birth import _build_open_request, BodyValidationError

    body = {
        "symbol": "BTCUSDT", "entry_price": 100.0, "direction": "LONG",
        "qty": 10.0, "tenant_id": 99,
    }
    with pytest.raises(BodyValidationError):
        _build_open_request(body, tenant_id=1, idempotency_key=None)


def test_factory_carries_jwt_tenant_id_not_body_tenant_id():
    """Even if a body smuggled tenant_id (it can't, F6) — the factory's
    contract is that tenant_id comes from JWT alone. We only test the
    happy path: the carried tenant_id equals the JWT-supplied value."""
    from api.positions_birth import _build_open_request

    body = {
        "symbol": "BTCUSDT", "entry_price": 100.0,
        "direction": "LONG", "qty": 10.0,
    }
    v = _build_open_request(body, tenant_id=42, idempotency_key="abc")
    assert v.tenant_id == 42
    assert v.idempotency_key == "abc"


def test_factory_carries_idempotency_key_when_supplied():
    from api.positions_birth import _build_open_request

    body = {
        "symbol": "BTCUSDT", "entry_price": 100.0,
        "direction": "LONG", "qty": 10.0,
    }
    v = _build_open_request(body, tenant_id=1, idempotency_key="req-uuid-xyz")
    assert v.idempotency_key == "req-uuid-xyz"
