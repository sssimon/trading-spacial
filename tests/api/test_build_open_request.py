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


# ---------------- Runtime órgano de rechazo on tenant_id (Serrano HIGH 6) ----------------


def _ok_body():
    return {
        "symbol": "BTCUSDT", "entry_price": 100.0,
        "direction": "LONG", "qty": 10.0,
    }


def test_factory_rejects_string_tenant_id():
    """Regla de coherencia: the type annotation `tenant_id: int` is only
    enforced when the factory rejects non-conforming inputs at runtime."""
    from api.positions_birth import _build_open_request, BodyValidationError
    with pytest.raises(BodyValidationError) as exc:
        _build_open_request(_ok_body(), tenant_id="1", idempotency_key=None)
    assert exc.value.status_code == 422
    assert exc.value.detail["field"] == "tenant_id"


def test_factory_rejects_none_tenant_id():
    from api.positions_birth import _build_open_request, BodyValidationError
    with pytest.raises(BodyValidationError):
        _build_open_request(_ok_body(), tenant_id=None, idempotency_key=None)


def test_factory_rejects_zero_tenant_id():
    from api.positions_birth import _build_open_request, BodyValidationError
    with pytest.raises(BodyValidationError):
        _build_open_request(_ok_body(), tenant_id=0, idempotency_key=None)


def test_factory_rejects_negative_tenant_id():
    from api.positions_birth import _build_open_request, BodyValidationError
    with pytest.raises(BodyValidationError):
        _build_open_request(_ok_body(), tenant_id=-1, idempotency_key=None)


def test_factory_rejects_boolean_tenant_id():
    """`True` is an int in Python (isinstance(True, int) is True) — explicitly
    reject so a caller cannot smuggle bool past the gate."""
    from api.positions_birth import _build_open_request, BodyValidationError
    with pytest.raises(BodyValidationError):
        _build_open_request(_ok_body(), tenant_id=True, idempotency_key=None)


def test_factory_rejects_float_tenant_id():
    from api.positions_birth import _build_open_request, BodyValidationError
    with pytest.raises(BodyValidationError):
        _build_open_request(_ok_body(), tenant_id=1.5, idempotency_key=None)


def test_factory_rejects_non_string_idempotency_key():
    from api.positions_birth import _build_open_request, BodyValidationError
    with pytest.raises(BodyValidationError) as exc:
        _build_open_request(_ok_body(), tenant_id=1, idempotency_key=123)
    assert exc.value.detail["field"] == "idempotency_key"


# ---------------- StaleEntryTsError reclassification (Serrano MEDIUM 5) ----------------


def _body_with_entry_ts(ts_iso: str) -> dict:
    return {
        "symbol": "BTCUSDT", "entry_price": 100.0,
        "direction": "LONG", "qty": 10.0, "entry_ts": ts_iso,
    }


def test_factory_raises_stale_entry_ts_for_far_future():
    """An entry_ts beyond now+60s surfaces as StaleEntryTsError, not as
    generic BodyValidationError. The typed name makes the failure
    actionable for clients without inspecting Pydantic error prose."""
    from datetime import datetime, timedelta, timezone
    from api.positions_birth import _build_open_request, StaleEntryTsError

    far_future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    with pytest.raises(StaleEntryTsError) as exc:
        _build_open_request(
            _body_with_entry_ts(far_future),
            tenant_id=1, idempotency_key=None,
        )
    assert exc.value.status_code == 422


def test_factory_raises_stale_entry_ts_for_far_past():
    """Symmetric: entry_ts older than now-7d also routes to StaleEntryTsError."""
    from datetime import datetime, timedelta, timezone
    from api.positions_birth import _build_open_request, StaleEntryTsError

    far_past = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    with pytest.raises(StaleEntryTsError):
        _build_open_request(
            _body_with_entry_ts(far_past),
            tenant_id=1, idempotency_key=None,
        )


def test_factory_other_pydantic_failures_stay_under_body_validation_error():
    """A non-entry_ts validation failure (e.g., negative entry_price) must
    NOT be reclassified — it stays under generic BodyValidationError."""
    from api.positions_birth import (
        _build_open_request, BodyValidationError, StaleEntryTsError,
    )
    body = {
        "symbol": "BTCUSDT", "entry_price": -1, "direction": "LONG", "qty": 10.0,
    }
    with pytest.raises(BodyValidationError) as exc:
        _build_open_request(body, tenant_id=1, idempotency_key=None)
    # Confirm it isn't accidentally the more specific class.
    assert not isinstance(exc.value, StaleEntryTsError)
