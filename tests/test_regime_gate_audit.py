"""TDD: Task 4 — tabla regime_gate_audit + batch insert.

DB isolation: fixture tmp_db local (mirrors test_agent_breaker.py pattern):
  monkeypatch.setattr(btc_api, "DB_FILE", tmp_path / "signals.db")
  btc_api.init_db()
tenant_id es NULLABLE (decisión de mercado global).
Batch: una sola transacción, no-op si lista vacía.
"""
from __future__ import annotations

import pytest
from db.regime_gate_audit import registrar_decisiones, _query_all


def _fila(**kw):
    base = dict(
        motor="valles",
        symbol="ADAUSDT",
        estado_regimen="btc",
        nivel="suprime",
        es_alt=True,
        regime_frescura="fresco",
        votos_vivos=3,
        enforced=True,
        umbral_version="abc123def456",
        tenant_id=None,
    )
    base.update(kw)
    return base


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    yield db_path


def test_registra_batch(tmp_db):
    n = registrar_decisiones([_fila(), _fila(symbol="DOGEUSDT")])
    assert n == 2
    rows = _query_all()
    assert len(rows) == 2
    assert rows[0]["tenant_id"] is None          # universo global
    assert rows[0]["umbral_version"] == "abc123def456"
    assert rows[0]["enforced"] == 1              # bool → int


def test_batch_vacio_no_escribe(tmp_db):
    assert registrar_decisiones([]) == 0
    assert _query_all() == []
