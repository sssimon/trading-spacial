"""Configuración compartida de pytest para el proyecto BTC Scanner."""
import sys
import os
from pathlib import Path

import pytest

# Asegurar que el directorio raíz esté en el path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Asegurar que el directorio tests esté en el path para imports como
# `from _fakes import ...` cuando tests/ es un paquete Python
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)


# ─── Auth test setup (added 2026-04-29 with the JWT auth system) ───────────
#
# The auth middleware enforces JWT cookies on every non-public path. Without
# the bypass below, every existing test that uses TestClient(app) directly
# would 401. We set AUTH_TEST_BYPASS_ROLE=admin before any test runs so all
# 23 pre-existing tests keep working without modification.
#
# The 2 explicit fixtures below (unauthed_client, viewer_client) override
# this for the role/auth tests in tests/test_auth.py.
#
# AUTH_JWT_SECRET must be set or auth/tokens._jwt_secret() raises at import.
# Use a stable test value so tokens we sign in tests are verifiable.

# NOTE: AUTH_TEST_BYPASS_ALLOWED gates the bypass. Without it set to "1"
# AND pytest in sys.modules AND AUTH_TEST_BYPASS_ROLE in {admin,viewer},
# the middleware refuses to skip JWT. See auth/middleware.py:_bypass_role_or_none.
os.environ.setdefault(
    "AUTH_JWT_SECRET",
    "test_secret_64bytes_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
)
os.environ.setdefault("AUTH_TEST_BYPASS_ALLOWED", "1")
os.environ.setdefault("AUTH_TEST_BYPASS_ROLE", "admin")


@pytest.fixture(autouse=True)
def _auth_bypass_default(monkeypatch):
    """Default every test to admin-bypass + test JWT secret.

    Tests that need to verify real auth behavior (test_auth.py) override
    these via monkeypatch in their own fixtures.
    """
    monkeypatch.setenv(
        "AUTH_JWT_SECRET",
        "test_secret_64bytes_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    monkeypatch.setenv("AUTH_TEST_BYPASS_ALLOWED", "1")
    monkeypatch.setenv("AUTH_TEST_BYPASS_ROLE", "admin")
    # First-time-setup env vars: ensure tests start from a clean slate even
    # if the dev's shell exported them. Tests that need specific values set
    # them explicitly via monkeypatch.setenv.
    monkeypatch.delenv("AUTH_INITIAL_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("AUTH_INITIAL_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("AUTH_DISABLE_WEB_SETUP", raising=False)
    # Reset rate limiter between tests so /auth/login + /setup tests don't leak.
    from auth.rate_limit import reset_all_for_tests
    reset_all_for_tests()
    # Reset the in-memory setup token between tests.
    from auth.setup import reset_for_tests as _setup_reset
    _setup_reset()
    yield


# ─── Explicit fixtures for tests/test_auth.py ──────────────────────────────


@pytest.fixture
def unauthed_client(monkeypatch, tmp_path):
    """TestClient with NO bypass — middleware enforces real JWT cookies.

    Uses an isolated DB (tmp_path/signals.db) and bootstraps the auth schema
    so /auth/login can actually look up users.
    """
    from fastapi.testclient import TestClient

    # Isolate signals.db
    db_file = str(tmp_path / "signals.db")
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", db_file)

    # Disable bypass — middleware will demand real tokens
    monkeypatch.delenv("AUTH_TEST_BYPASS_ROLE", raising=False)

    # Initialize schemas in the isolated DB
    from db.schema import init_db
    from db.auth_schema import init_auth_db
    init_db()
    init_auth_db()

    return TestClient(btc_api.app)


@pytest.fixture
def viewer_client(monkeypatch, tmp_path):
    """TestClient where the middleware injects a synthetic viewer user.

    Used to test that admin-only endpoints return 403 for viewers.
    """
    from fastapi.testclient import TestClient

    db_file = str(tmp_path / "signals.db")
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", db_file)

    monkeypatch.setenv("AUTH_TEST_BYPASS_ROLE", "viewer")

    from db.schema import init_db
    from db.auth_schema import init_auth_db
    init_db()
    init_auth_db()

    return TestClient(btc_api.app)


@pytest.fixture
def tmp_ohlcv_db(tmp_path, monkeypatch):
    """Isolated ohlcv.db per test. Points data._storage at a tmp file + fresh schema."""
    from data import _storage
    db_path = tmp_path / "ohlcv.db"
    monkeypatch.setattr(_storage, "DB_PATH", str(db_path))
    # Reset thread-local connection so each test gets a fresh one pointing at tmp_path
    if hasattr(_storage._tls, "conn"):
        _storage._tls.conn.close()
        del _storage._tls.conn
    _storage.init_schema()
    yield db_path
    if hasattr(_storage._tls, "conn"):
        _storage._tls.conn.close()
        del _storage._tls.conn


@pytest.fixture
def fake_provider(monkeypatch):
    """Inject a deterministic FakeProvider as the only provider in data._fetcher."""
    from data import _fetcher
    from _fakes import FakeProvider

    fake = FakeProvider()
    monkeypatch.setattr(_fetcher, "_PROVIDERS", [fake])
    # Reset failover state
    _fetcher._active_idx = 0
    _fetcher._consecutive_failures = 0
    _fetcher._last_probe_ms = 0
    return fake


@pytest.fixture
def fake_providers(monkeypatch):
    """Inject two fake providers to test failover."""
    from data import _fetcher
    from _fakes import FakeProvider

    primary = FakeProvider(name="primary")
    fallback = FakeProvider(name="fallback")
    monkeypatch.setattr(_fetcher, "_PROVIDERS", [primary, fallback])
    _fetcher._active_idx = 0
    _fetcher._consecutive_failures = 0
    _fetcher._last_probe_ms = 0
    return primary, fallback
