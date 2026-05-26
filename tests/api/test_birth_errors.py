"""Route-level tests for POST /positions error taxonomy mapping (#473).

The route must:
- map BodyValidationError to HTTP 422 with a structured detail
- map sqlite3.IntegrityError (partial UNIQUE index) to HTTP 409
- NOT collapse unrelated server errors to 500 str(e) — they bubble to
  FastAPI's default 500 handler with a generic message and full logged tb

NOTE (transitional): Task 14 of the plan rewires the route to use
`_build_open_request` + a thin `db_create_position` shim. Task 15 introduces
`BirthRegistrar`. These tests are written against the transitional state —
the shape of the contract is the same (BodyValidationError → 422,
IntegrityError → 409, no `except Exception → 500 str(e)`); only the internal
patch target moves when Task 15 lands.
"""
import pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Boot a FastAPI TestClient against a fresh tmp DB with all migrations.

    Mirrors the dependency-override pattern in tests/test_api.py — register
    the route handlers directly on a fresh FastAPI app so the production
    `dependencies=[verify_api_key, require_role("admin")]` list is bypassed
    (those are factory deps that produce a new instance per route definition,
    so overriding by reference doesn't help). The route LOGIC under test is
    open_position's body validation + birth-path + error taxonomy, not the
    auth wrapper.
    """
    db_path = tmp_path / "birth.db"
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(db_path))
    # Monkeypatch DATA_DIR / POSITIONS_JSON_FILE so update_positions_json
    # writes into the temp tree instead of the repo root.
    from api import positions as _pos
    monkeypatch.setattr(
        _pos, "POSITIONS_JSON_FILE",
        str(tmp_path / "positions_summary.json"),
    )
    btc_api.init_db()
    # init_db() builds positions/scans/etc.; auth tables (users) live in
    # db/auth_schema.py and need a separate bootstrap call.
    from db.auth_schema import init_auth_db
    from db.transaction import transaction
    with transaction() as con:
        init_auth_db(con)

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    with transaction() as con:
        con.execute(
            "INSERT INTO users (id, email, password_hash, role, created_at, "
            "password_changed_at) "
            "VALUES (1, 'samuel@test', 'x', 'admin', ?, ?)",
            (now_iso, now_iso),
        )
        # Capital row is not required by the open_position path (no capital
        # read/write at birth), only at close — skip to keep this fixture
        # narrowly scoped.

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api import positions as _pos
    from auth.dependencies import get_current_tenant_id

    app = FastAPI()
    app.post("/positions")(_pos.open_position)
    app.dependency_overrides[get_current_tenant_id] = lambda: 1
    return TestClient(app)


def test_invalid_body_returns_422(client):
    """Pydantic validation failure (negative entry_price) → 422 BodyValidationError."""
    resp = client.post("/positions", json={
        "symbol": "BTCUSDT", "entry_price": -1, "direction": "LONG", "qty": 10.0,
    })
    assert resp.status_code == 422
    body = resp.json()
    assert "BodyValidationError" in str(body) or "entry_price" in str(body)


def test_extra_field_returns_422(client):
    """tenant_id in body (extra='forbid') → 422 BodyValidationError."""
    resp = client.post("/positions", json={
        "symbol": "BTCUSDT", "entry_price": 100.0, "direction": "LONG",
        "qty": 10.0, "tenant_id": 99,
    })
    assert resp.status_code == 422


def test_unknown_symbol_returns_422(client):
    """Symbol not in allowlist → 422 BodyValidationError."""
    resp = client.post("/positions", json={
        "symbol": "BOGUSCOIN", "entry_price": 100.0, "direction": "LONG", "qty": 10.0,
    })
    assert resp.status_code == 422


def test_duplicate_open_scan_returns_409(client):
    """Two successful opens with the same scan_id — second must 409.

    The partial UNIQUE index `idx_positions_open_scan_unique` fires at the
    schema layer; the route must translate sqlite3.IntegrityError → 409
    instead of bubbling it as 500 str(e).
    """
    first = client.post("/positions", json={
        "symbol": "BTCUSDT", "entry_price": 100.0, "direction": "LONG",
        "qty": 10.0, "scan_id": 99,
    })
    assert first.status_code == 200, first.text
    second = client.post("/positions", json={
        "symbol": "BTCUSDT", "entry_price": 100.0, "direction": "LONG",
        "qty": 10.0, "scan_id": 99,
    })
    assert second.status_code == 409
    body_text = str(second.json())
    assert "scan_id" in body_text or "unique" in body_text.lower() or "conflict" in body_text.lower()


def test_route_does_not_collapse_server_exceptions_to_500_str(client, monkeypatch):
    """If the underlying write raises an unrelated RuntimeError (e.g. disk
    error), the route must NOT catch it as `except Exception → 500 str(e)`.
    It bubbles to FastAPI's default 500 handler with a generic body.

    Patch target is BirthRegistrar.register — Task 15 moved the write path
    into the op-ligero. The contract under test — no `except Exception`
    membrane that leaks str(e) — is invariant across that move.
    """
    from api import positions_birth as _birth

    def _exploding(*args, **kwargs):
        raise RuntimeError("simulated disk failure: file not found")

    monkeypatch.setattr(
        _birth.BirthRegistrar, "register", staticmethod(_exploding),
    )

    # TestClient propagates unhandled exceptions by default. Reach into the
    # underlying httpx transport to disable that so we observe the 500
    # response shape FastAPI generates from its default exception handler
    # (which must NOT leak str(e)).
    from starlette.testclient import TestClient as _StarletteTC  # noqa: PLC0415
    fresh = _StarletteTC(client.app, raise_server_exceptions=False)
    resp = fresh.post("/positions", json={
        "symbol": "BTCUSDT", "entry_price": 100.0, "direction": "LONG", "qty": 10.0,
    })
    assert resp.status_code == 500
    # The detail must NOT be the raw str(e) leaked to the client.
    body_text = resp.text
    assert "simulated disk failure" not in body_text
