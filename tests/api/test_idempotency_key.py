"""Tests for IdempotencyCache get/set + 24h TTL + lazy cleanup +
body fingerprint round-trip (Serrano BLOCKER 1).

`get` now returns a dict {"result": payload, "body_sha256": fp} or None;
`set` takes a mandatory `body_sha256` arg. Tests updated for the new
shape — the choreography under test (TTL, lazy cleanup, tenant isolation,
overwrite semantics) is unchanged.
"""
import json
from datetime import datetime, timedelta, timezone
import pytest


_FP = "a" * 64  # canonical placeholder 64-char hex fingerprint
_FP2 = "b" * 64


@pytest.fixture
def fresh_db_con(monkeypatch, tmp_path):
    # This codebase resolves the active DB via btc_api.DB_FILE (see
    # db/connection.py::_resolve_db_file). Per the suite-wide convention
    # (tests/conftest.py + others), point btc_api.DB_FILE at a tmp_path file
    # so init_db() + every transaction() opens against an isolated DB.
    import btc_api
    db_path = tmp_path / "ik.db"
    monkeypatch.setattr(btc_api, "DB_FILE", str(db_path))
    from db.schema import init_db
    init_db()
    from db.transaction import transaction
    with transaction() as con:
        yield con


def test_set_then_get_returns_payload_with_fingerprint(fresh_db_con):
    from api.positions_birth import IdempotencyCache
    payload = {"id": 1, "symbol": "BTCUSDT", "qty": 10.0}
    IdempotencyCache.set(
        fresh_db_con, tenant_id=1, key="k", result=payload, body_sha256=_FP,
    )
    got = IdempotencyCache.get(fresh_db_con, tenant_id=1, key="k")
    assert got == {"result": payload, "body_sha256": _FP}


def test_get_returns_none_for_missing_key(fresh_db_con):
    from api.positions_birth import IdempotencyCache
    assert IdempotencyCache.get(fresh_db_con, tenant_id=1, key="missing") is None


def test_different_tenant_does_not_see_cached_entry(fresh_db_con):
    from api.positions_birth import IdempotencyCache
    payload = {"id": 1}
    IdempotencyCache.set(
        fresh_db_con, tenant_id=1, key="k", result=payload, body_sha256=_FP,
    )
    assert IdempotencyCache.get(fresh_db_con, tenant_id=2, key="k") is None


def test_set_overwrites_existing_entry(fresh_db_con):
    from api.positions_birth import IdempotencyCache
    IdempotencyCache.set(
        fresh_db_con, tenant_id=1, key="k", result={"id": 1}, body_sha256=_FP,
    )
    IdempotencyCache.set(
        fresh_db_con, tenant_id=1, key="k", result={"id": 2}, body_sha256=_FP2,
    )
    got = IdempotencyCache.get(fresh_db_con, tenant_id=1, key="k")
    assert got["result"]["id"] == 2
    assert got["body_sha256"] == _FP2


def test_expired_entry_returns_none_and_is_cleaned_up(fresh_db_con):
    from api.positions_birth import IdempotencyCache

    # Hand-craft an already-expired row.
    expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    created = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    fresh_db_con.execute(
        "INSERT INTO idempotency_keys "
        "(tenant_id, key, result_json, body_sha256, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (1, "k", json.dumps({"id": 99}), _FP, created, expired),
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
    IdempotencyCache.set(
        fresh_db_con, tenant_id=1, key="k", result=payload, body_sha256=_FP,
    )
    got = IdempotencyCache.get(fresh_db_con, tenant_id=1, key="k")
    assert got == {"result": payload, "body_sha256": _FP}


def test_legacy_row_without_fingerprint_returns_none_fp(fresh_db_con):
    """A row written before the body_sha256 column existed (or by an old
    code path) has body_sha256=NULL. get must surface it as None rather
    than raise — the registrar treats a NULL fp as 'no fingerprint, allow
    replay only when keys match'."""
    from api.positions_birth import IdempotencyCache

    # Insert a row with NULL body_sha256 directly.
    now_iso = datetime.now(timezone.utc).isoformat()
    expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    fresh_db_con.execute(
        "INSERT INTO idempotency_keys "
        "(tenant_id, key, result_json, body_sha256, created_at, expires_at) "
        "VALUES (?, ?, ?, NULL, ?, ?)",
        (1, "k-legacy", json.dumps({"id": 42}), now_iso, expires),
    )
    got = IdempotencyCache.get(fresh_db_con, tenant_id=1, key="k-legacy")
    assert got is not None
    assert got["result"] == {"id": 42}
    assert got["body_sha256"] is None


def test_cache_get_unreachable_table_raises_and_logs(fresh_db_con, caplog):
    """Serrano HIGH 2 (post-convergence): when the table is unreachable,
    get must log a structured error AND raise the internal _CacheUnavailable
    marker. Previously this returned None (silent degrade) which opened
    a duplicate-INSERT window at the registrar layer. Failing closed at
    the cache means the registrar can decide whether to surface 503 (when
    Idempotency-Key was supplied) or proceed (when no key was supplied —
    the cache path is bypassed entirely and this raise is never reached)."""
    from api.positions_birth import IdempotencyCache, _CacheUnavailable
    import logging

    # Drop the table to force OperationalError.
    fresh_db_con.execute("DROP TABLE idempotency_keys")
    with caplog.at_level(logging.ERROR, logger="api.positions_birth"):
        with pytest.raises(_CacheUnavailable):
            IdempotencyCache.get(fresh_db_con, tenant_id=1, key="k")
    assert any(
        "IDEMPOTENCY_CACHE_UNREACHABLE" in record.message
        for record in caplog.records
    )


def test_cache_set_unreachable_table_raises_and_logs(fresh_db_con, caplog):
    """Mirror of the get-side fail-closed contract (Serrano HIGH 2). The
    enclosing tx in BirthRegistrar.register has not committed at the
    moment set is called; raising rolls back the position INSERT in the
    same tx, so no orphan row survives."""
    from api.positions_birth import IdempotencyCache, _CacheUnavailable
    import logging

    fresh_db_con.execute("DROP TABLE idempotency_keys")
    with caplog.at_level(logging.ERROR, logger="api.positions_birth"):
        with pytest.raises(_CacheUnavailable):
            IdempotencyCache.set(
                fresh_db_con, tenant_id=1, key="k",
                result={"id": 1}, body_sha256=_FP,
            )
    assert any(
        "IDEMPOTENCY_CACHE_UNREACHABLE" in record.message
        for record in caplog.records
    )
