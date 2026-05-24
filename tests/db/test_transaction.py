"""Contract tests for db.transaction.transaction().

These tests pin down the wrapper's invariants:
- commits on clean exit, rolls back on exception, always closes
- never suppresses exceptions
- yields a configured sqlite3.Connection (dict-row, busy_timeout)
- concurrent transactions serialize under BEGIN IMMEDIATE
- caller violations of the contract are observable, not silent
"""
import sqlite3
import threading

import pytest

from db.transaction import transaction
from db.connection import init_db


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Point the connection resolver at a fresh empty DB for this test."""
    db_path = tmp_path / "test.db"
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(db_path))
    init_db()
    return str(db_path)


def test_commit_on_success(fresh_db):
    with transaction() as con:
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v INT)")
        con.execute("INSERT INTO t (v) VALUES (1)")

    with transaction() as con:
        rows = con.execute("SELECT v FROM t").fetchall()
    assert [r["v"] for r in rows] == [1]


def test_rollback_on_exception(fresh_db):
    with transaction() as con:
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v INT)")

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with transaction() as con:
            con.execute("INSERT INTO t (v) VALUES (42)")
            raise Boom()

    with transaction() as con:
        rows = con.execute("SELECT v FROM t").fetchall()
    assert rows == []


def test_exception_not_suppressed(fresh_db):
    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with transaction():
            raise Boom()


def test_connection_closed_on_success(fresh_db):
    captured = {}
    with transaction() as con:
        captured["con"] = con
        captured["con"].execute("SELECT 1")

    with pytest.raises(sqlite3.ProgrammingError):
        captured["con"].execute("SELECT 1")


def test_connection_closed_on_exception(fresh_db):
    captured = {}

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with transaction() as con:
            captured["con"] = con
            raise Boom()

    with pytest.raises(sqlite3.ProgrammingError):
        captured["con"].execute("SELECT 1")


def test_dict_row_factory(fresh_db):
    with transaction() as con:
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        con.execute("INSERT INTO t (name) VALUES ('alice')")
        row = con.execute("SELECT id, name FROM t").fetchone()

    assert row["id"] == 1
    assert row["name"] == "alice"


def test_concurrent_writers_serialize(fresh_db):
    with transaction() as con:
        con.execute("CREATE TABLE counters (id INTEGER PRIMARY KEY, n INTEGER)")
        con.execute("INSERT INTO counters (id, n) VALUES (1, 0)")

    errors = []

    def increment_n_times(n):
        try:
            for _ in range(n):
                with transaction() as con:
                    row = con.execute(
                        "SELECT n FROM counters WHERE id = 1"
                    ).fetchone()
                    con.execute(
                        "UPDATE counters SET n = ? WHERE id = 1",
                        (row["n"] + 1,),
                    )
        except Exception as e:  # pragma: no cover - assertion below
            errors.append(e)

    threads = [
        threading.Thread(target=increment_n_times, args=(50,)) for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"writer errors: {errors}"
    with transaction() as con:
        final = con.execute("SELECT n FROM counters WHERE id = 1").fetchone()["n"]
    assert final == 200


def test_caller_commit_breaks_observably(fresh_db):
    """An explicit COMMIT inside the body ends the explicit tx; the wrapper's
    final COMMIT then raises. Failure is loud, not silent."""
    with transaction() as con:
        con.execute("CREATE TABLE t (v INT)")
    with pytest.raises(sqlite3.OperationalError):
        with transaction() as con:
            con.execute("INSERT INTO t (v) VALUES (1)")
            con.execute("COMMIT")  # contract violation


def test_caller_close_breaks_observably(fresh_db):
    """Explicit close() in the body makes the wrapper's COMMIT raise."""
    with pytest.raises(sqlite3.ProgrammingError):
        with transaction() as con:
            con.close()  # contract violation
