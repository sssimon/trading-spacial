"""Tests for IdempotencyCache get/set + 24h TTL + lazy cleanup."""
import json
from datetime import datetime, timedelta, timezone
import pytest


@pytest.fixture
def fresh_db_con(monkeypatch, tmp_path):
    import os
    db_path = tmp_path / "ik.db"
    monkeypatch.setenv("BTC_DB", str(db_path))
    from db.schema import init_db
    init_db()
    from db.transaction import transaction
    with transaction() as con:
        yield con


def test_set_then_get_returns_payload(fresh_db_con):
    from api.positions_birth import IdempotencyCache
    payload = {"id": 1, "symbol": "BTCUSDT", "qty": 10.0}
    IdempotencyCache.set(fresh_db_con, tenant_id=1, key="k", result=payload)
    got = IdempotencyCache.get(fresh_db_con, tenant_id=1, key="k")
    assert got == payload


def test_get_returns_none_for_missing_key(fresh_db_con):
    from api.positions_birth import IdempotencyCache
    assert IdempotencyCache.get(fresh_db_con, tenant_id=1, key="missing") is None


def test_different_tenant_does_not_see_cached_entry(fresh_db_con):
    from api.positions_birth import IdempotencyCache
    payload = {"id": 1}
    IdempotencyCache.set(fresh_db_con, tenant_id=1, key="k", result=payload)
    assert IdempotencyCache.get(fresh_db_con, tenant_id=2, key="k") is None


def test_set_overwrites_existing_entry(fresh_db_con):
    from api.positions_birth import IdempotencyCache
    IdempotencyCache.set(fresh_db_con, tenant_id=1, key="k", result={"id": 1})
    IdempotencyCache.set(fresh_db_con, tenant_id=1, key="k", result={"id": 2})
    assert IdempotencyCache.get(fresh_db_con, tenant_id=1, key="k")["id"] == 2


def test_expired_entry_returns_none_and_is_cleaned_up(fresh_db_con):
    from api.positions_birth import IdempotencyCache

    # Hand-craft an already-expired row.
    expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    created = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    fresh_db_con.execute(
        "INSERT INTO idempotency_keys (tenant_id, key, result_json, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (1, "k", json.dumps({"id": 99}), created, expired),
    )

    # The get path lazy-deletes expired rows for this (tenant, key).
    assert IdempotencyCache.get(fresh_db_con, tenant_id=1, key="k") is None
    n = fresh_db_con.execute(
        "SELECT COUNT(*) FROM idempotency_keys WHERE tenant_id=1 AND key='k'"
    ).fetchone()[0]
    assert n == 0


def test_unexpired_entry_within_24h_still_returned(fresh_db_con):
    from api.positions_birth import IdempotencyCache
    payload = {"id": 7}
    IdempotencyCache.set(fresh_db_con, tenant_id=1, key="k", result=payload)
    got = IdempotencyCache.get(fresh_db_con, tenant_id=1, key="k")
    assert got == payload
