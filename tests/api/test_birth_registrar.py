"""Unit tests for BirthRegistrar's transactional choreography.

We exercise the class directly (not via HTTP) to cover:
- happy path: INSERT + update_positions_json + structured log with scan_id.
- idempotency replay returns the cached row without re-INSERTing.
- same key with a DIFFERENT body raises DuplicateIdempotencyKeyError (409).
- concurrent same-key opens serialize through one transaction — exactly
  one row created.
- UNIQUE violation on (tenant_id, scan_id) maps to UniqueViolationError
  via SQLite extended error code (not via prose substring match).
- update_positions_json is called AFTER commit (the row is visible).
- update_positions_json failure emits a structured POSITION_SNAPSHOT_STALE
  log.error (Serrano HIGH 5).

Per Voronov 2026-05-26: BirthRegistrar is an op-ligero — validation already
happened upstream (Pydantic + _build_open_request). These tests exercise the
registrar's only owned responsibility: registrar el acto (tx + post-commit +
structured log), no validarlo.
"""
import pytest


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    """Boot a fresh tmp DB with all migrations installed.

    Mirrors the BTC_DB env-var approach used elsewhere in the suite; init_db
    reads that path on import-time of db.transaction's get_db helpers.
    """
    db_path = tmp_path / "br.db"
    # btc_api owns the DB_FILE constant the rest of the stack reads through.
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(db_path))
    # update_positions_json writes to api._paths.DATA_DIR by default —
    # redirect to tmp so the fixture doesn't litter the repo root.
    from api import positions as _pos
    monkeypatch.setattr(
        _pos, "POSITIONS_JSON_FILE",
        str(tmp_path / "positions_summary.json"),
    )
    btc_api.init_db()
    return db_path


def _validated(
    symbol="BTCUSDT",
    scan_id=None,
    idempotency_key=None,
    tenant_id=1,
    qty=10.0,
    entry_price=100.0,
):
    from api.positions_birth import _build_open_request
    body = {
        "symbol": symbol, "entry_price": entry_price,
        "direction": "LONG", "qty": qty,
    }
    if scan_id is not None:
        body["scan_id"] = scan_id
    return _build_open_request(body, tenant_id=tenant_id, idempotency_key=idempotency_key)


def test_happy_path_inserts_row_and_returns_dict(fresh_db):
    from api.positions_birth import BirthRegistrar
    pos = BirthRegistrar.register(_validated())
    assert pos["id"] >= 1
    assert pos["symbol"] == "BTCUSDT"
    assert pos["qty"] == 10.0
    assert pos["tenant_id"] == 1
    assert pos["status"] == "open"


def test_idempotency_replay_returns_cached_without_second_insert(fresh_db):
    """Same key + same body → second call replays the cached row, no
    second INSERT. Exercises the real (now-fingerprinted) IdempotencyCache
    persistence path — the table is real and the fingerprint round-trip is
    end-to-end."""
    from api.positions_birth import BirthRegistrar
    from db.transaction import transaction

    first = BirthRegistrar.register(_validated(idempotency_key="k-1"))
    second = BirthRegistrar.register(_validated(idempotency_key="k-1"))
    assert second["id"] == first["id"]
    with transaction() as con:
        n = con.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    assert n == 1


def test_same_key_different_body_raises_duplicate_idempotency_key_error(fresh_db):
    """Serrano BLOCKER 1: same Idempotency-Key reused with a DIFFERENT body
    must NOT replay the cached row (that would silently return the wrong
    position to the wrong client request). It must raise
    DuplicateIdempotencyKeyError (409) with both fingerprints in the
    detail for operator triage."""
    from api.positions_birth import (
        BirthRegistrar, DuplicateIdempotencyKeyError,
    )
    from db.transaction import transaction

    BirthRegistrar.register(_validated(idempotency_key="k-1", qty=10.0))
    with pytest.raises(DuplicateIdempotencyKeyError) as exc:
        BirthRegistrar.register(_validated(idempotency_key="k-1", qty=20.0))
    assert exc.value.status_code == 409
    assert "existing_body_sha256" in exc.value.detail
    assert "new_body_sha256" in exc.value.detail
    assert exc.value.detail["existing_body_sha256"] != exc.value.detail["new_body_sha256"]

    # And only one row exists — the rejected second call did NOT INSERT.
    with transaction() as con:
        n = con.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    assert n == 1


def test_concurrent_same_key_serializes_to_one_position(fresh_db):
    """Serrano BLOCKER 2 / Aurelius reframe: when the probe and the INSERT
    share one transaction, two concurrent same-key requests must NOT both
    INSERT — they serialize through BEGIN IMMEDIATE and the second one
    sees the cached row.

    Threading model: SQLite serializes write-tx at the file via the
    reserved-writer lock + busy_timeout. We launch N workers each calling
    BirthRegistrar.register with the SAME idempotency key and assert
    exactly one row is created.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from api.positions_birth import BirthRegistrar
    from db.transaction import transaction

    N = 8
    barrier_results: list[dict] = []
    errors: list[BaseException] = []

    def _worker():
        try:
            return BirthRegistrar.register(
                _validated(idempotency_key="k-concurrent")
            )
        except BaseException as e:  # noqa: BLE001
            errors.append(e)
            return None

    with ThreadPoolExecutor(max_workers=N) as ex:
        futures = [ex.submit(_worker) for _ in range(N)]
        for f in as_completed(futures):
            r = f.result()
            if r is not None:
                barrier_results.append(r)

    # If anyone errored, surface that — silent partial failure here is
    # itself a bug in the choreography.
    assert errors == [], f"unexpected worker errors: {errors!r}"
    # Exactly one row total. All workers either INSERTed (one winner) or
    # replayed (everyone else); the winners must agree on the id.
    with transaction() as con:
        n = con.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    assert n == 1, f"expected exactly 1 row, got {n}"
    ids = {r["id"] for r in barrier_results}
    assert ids == {barrier_results[0]["id"]}, (
        f"all returns must share one id: {ids!r}"
    )


def test_duplicate_scan_id_raises_unique_violation_error(fresh_db):
    """The partial UNIQUE index fires sqlite3.IntegrityError with
    SQLITE_CONSTRAINT_UNIQUE (2067) and a message containing
    'positions.scan_id'. The translator must read sqlite_errorcode +
    column list, not match English prose (Serrano BLOCKER 3)."""
    from api.positions_birth import BirthRegistrar, UniqueViolationError

    BirthRegistrar.register(_validated(scan_id=77))
    with pytest.raises(UniqueViolationError) as exc:
        BirthRegistrar.register(_validated(scan_id=77))
    assert exc.value.status_code == 409
    assert exc.value.detail["scan_id"] == 77
    # Triage detail carries the SQLite-reported names/codes.
    assert exc.value.detail.get("sqlite_errorname") == "SQLITE_CONSTRAINT_UNIQUE"
    assert exc.value.detail.get("sqlite_errorcode") == 2067


def test_update_positions_json_invoked_after_commit(fresh_db, monkeypatch):
    """F8 closure: BirthRegistrar must call update_positions_json AFTER commit.
    Detect call ordering by stubbing the JSON helper and verifying the row
    is visible from a fresh transaction at the moment the stub fires."""
    from api.positions_birth import BirthRegistrar
    from db.transaction import transaction
    import api.positions as api_positions

    visible_count = {"value": None}

    def _stub_update():
        with transaction() as con:
            n = con.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        visible_count["value"] = n

    monkeypatch.setattr(api_positions, "update_positions_json", _stub_update)

    BirthRegistrar.register(_validated())
    assert visible_count["value"] == 1


def test_snapshot_staleness_emits_structured_log(fresh_db, monkeypatch, caplog):
    """Serrano HIGH 5: when update_positions_json itself raises, the
    registrar must emit a structured log.error (POSITION_SNAPSHOT_STALE)
    so the staleness is observable. The DB row is durable; the JSON
    snapshot may be stale. We do not re-raise — the contract is
    observability, not transactionality."""
    import logging
    from api.positions_birth import BirthRegistrar
    import api.positions as api_positions

    def _exploding():
        raise OSError("disk full simulation")

    monkeypatch.setattr(api_positions, "update_positions_json", _exploding)
    with caplog.at_level(logging.ERROR, logger="api.positions_birth"):
        pos = BirthRegistrar.register(_validated())
    # The position itself was committed (the disk-full happened AFTER the tx).
    assert pos["id"] >= 1
    # And the staleness is loud.
    assert any(
        "POSITION_SNAPSHOT_STALE" in record.message
        for record in caplog.records
    )


def test_distinct_idempotency_keys_create_distinct_rows(fresh_db):
    """Distinct Idempotency-Keys must produce distinct rows."""
    from api.positions_birth import BirthRegistrar
    from db.transaction import transaction

    a = BirthRegistrar.register(_validated(idempotency_key="k-a"))
    b = BirthRegistrar.register(_validated(idempotency_key="k-b"))
    assert a["id"] != b["id"]
    with transaction() as con:
        n = con.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    assert n == 2


def test_f15_log_line_includes_scan_id(fresh_db, caplog):
    """Serrano MEDIUM 13: the F15 birth log must include scan_id per the
    original plan spec; it was dropped in the prior revision."""
    import logging
    from api.positions_birth import BirthRegistrar
    with caplog.at_level(logging.INFO, logger="api.positions_birth"):
        BirthRegistrar.register(_validated(scan_id=99))
    # The structured log line carries scan_id.
    matched = [
        r for r in caplog.records
        if "POSICION OPENED" in r.message and "scan_id=99" in r.message
    ]
    assert matched, (
        "expected POSICION OPENED log line with scan_id=99; got: "
        f"{[r.message for r in caplog.records]}"
    )
