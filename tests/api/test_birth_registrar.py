"""Unit tests for BirthRegistrar's transactional choreography.

We exercise the class directly (not via HTTP) to cover:
- happy path: INSERT + update_positions_json + structured log.
- idempotency replay returns the cached row without re-INSERTing.
- UNIQUE violation on (tenant_id, scan_id) maps to UniqueViolationError.
- update_positions_json is called AFTER commit (the row is visible).

Per Voronov 2026-05-26: BirthRegistrar is an op-ligero — validation already
happened upstream (Pydantic + _build_open_request). These tests exercise the
registrar's only owned responsibility: registrar el acto (tx + post-commit +
structured log), no validarlo.

Note on the Idempotency-Key persistence: Task 15 ships a stub IdempotencyCache
(no real `idempotency_keys` table yet — Task 16 wires it). The idempotency
test below uses a monkeypatched in-memory cache so the choreography under test
is "if the cache says hit, do not re-INSERT" rather than relying on the
yet-unbuilt persistence layer.
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


def _validated(symbol="BTCUSDT", scan_id=None, idempotency_key=None, tenant_id=1):
    from api.positions_birth import _build_open_request
    body = {
        "symbol": symbol, "entry_price": 100.0,
        "direction": "LONG", "qty": 10.0,
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


def test_idempotency_replay_returns_cached_without_second_insert(fresh_db, monkeypatch):
    """Task 15 stub: IdempotencyCache.get returns None (no real table yet).
    To test the choreography ('if cache hits, do not re-INSERT'), we swap the
    cache for an in-memory dict-backed double — what Task 16 will deliver via
    SQL when the idempotency_keys table lands."""
    from api.positions_birth import BirthRegistrar
    import api.positions_birth as _birth
    from db.transaction import transaction

    _mem: dict[tuple[int, str], dict] = {}

    class _MemCache:
        @staticmethod
        def get(con, tenant_id, key):
            return _mem.get((tenant_id, key))

        @staticmethod
        def set(con, tenant_id, key, result):
            _mem[(tenant_id, key)] = result

    monkeypatch.setattr(_birth, "IdempotencyCache", _MemCache)

    first = BirthRegistrar.register(_validated(idempotency_key="k-1"))
    second = BirthRegistrar.register(_validated(idempotency_key="k-1"))
    assert second["id"] == first["id"]
    with transaction() as con:
        n = con.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    assert n == 1


def test_duplicate_scan_id_raises_unique_violation_error(fresh_db):
    from api.positions_birth import BirthRegistrar, UniqueViolationError

    BirthRegistrar.register(_validated(scan_id=77))
    with pytest.raises(UniqueViolationError) as exc:
        BirthRegistrar.register(_validated(scan_id=77))
    assert exc.value.status_code == 409
    assert exc.value.detail["scan_id"] == 77


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


def test_distinct_idempotency_keys_create_distinct_rows(fresh_db, monkeypatch):
    """Distinct Idempotency-Keys must produce distinct rows. Uses the same
    in-memory cache double as the replay test so we exercise the choreography
    even before Task 16 lights up the persistence."""
    from api.positions_birth import BirthRegistrar
    import api.positions_birth as _birth
    from db.transaction import transaction

    _mem: dict[tuple[int, str], dict] = {}

    class _MemCache:
        @staticmethod
        def get(con, tenant_id, key):
            return _mem.get((tenant_id, key))

        @staticmethod
        def set(con, tenant_id, key, result):
            _mem[(tenant_id, key)] = result

    monkeypatch.setattr(_birth, "IdempotencyCache", _MemCache)

    a = BirthRegistrar.register(_validated(idempotency_key="k-a"))
    b = BirthRegistrar.register(_validated(idempotency_key="k-b"))
    assert a["id"] != b["id"]
    with transaction() as con:
        n = con.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    assert n == 2
