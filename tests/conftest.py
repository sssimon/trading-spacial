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
