"""Tests for `db.schema._set_wal_mode_idempotent_with_retry` (#495 defense-in-depth).

The root-cause fix is in `scanner.runtime.stop_managed_threads()` which
removes the orphan-writer source. This layer is the belt-and-suspenders
guard for the residual case where some other writer still holds the lock
when `init_db()` runs.

Contract:
  - if DB is already in WAL mode → skip the PRAGMA assignment entirely
  - if PRAGMA fails with `database is locked` → retry up to 3 times with backoff
  - other OperationalErrors → propagate immediately (no retry)
"""
from __future__ import annotations

import os
import sqlite3
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "wal_test.db"
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(db_path))
    return str(db_path)


def test_skip_pragma_when_already_in_wal_mode(monkeypatch):
    """When the probe `PRAGMA journal_mode` (no assignment) returns 'wal',
    the helper must NOT issue `PRAGMA journal_mode=WAL`. This is the
    steady-state path on every boot after the first."""
    from db import schema

    issued_sql: list[str] = []

    class FakeCon:
        def execute(self, sql, *a, **kw):
            issued_sql.append(sql)
            sql_upper = sql.upper().replace(" ", "")
            if sql_upper == "PRAGMAJOURNAL_MODE":
                # Already in WAL — the skip path should fire.
                return MagicMock(fetchone=lambda: ("wal",))
            return MagicMock()

        def close(self):
            pass

    monkeypatch.setattr(schema, "_open_configured_connection", lambda: FakeCon())
    schema._set_wal_mode_idempotent_with_retry()

    upper_sql = [s.upper().replace(" ", "") for s in issued_sql]
    assert "PRAGMABUSY_TIMEOUT=5000" in upper_sql, (
        f"busy_timeout pragma should still fire; saw: {issued_sql}"
    )
    assert "PRAGMAJOURNAL_MODE" in upper_sql, (
        f"idempotency probe must run; saw: {issued_sql}"
    )
    assert "PRAGMAJOURNAL_MODE=WAL" not in upper_sql, (
        f"WAL assignment must be skipped when already in WAL; saw: {issued_sql}"
    )


def test_retries_on_database_locked(tmp_path, monkeypatch):
    """When the WAL pragma raises `database is locked`, the helper retries
    up to 3 times with backoff before giving up."""
    from db import schema

    call_count = {"n": 0}

    class FakeCon:
        def execute(self, sql, *a, **kw):
            sql_upper = sql.upper().replace(" ", "")
            if "JOURNAL_MODE=WAL" in sql_upper:
                call_count["n"] += 1
                if call_count["n"] < 3:
                    raise sqlite3.OperationalError("database is locked")
                # succeeds on attempt 3
                return MagicMock(fetchone=lambda: ("wal",))
            if sql_upper == "PRAGMABUSY_TIMEOUT=5000":
                return MagicMock()
            if sql_upper == "PRAGMAJOURNAL_MODE":
                # not in WAL yet → return a non-WAL mode so the helper
                # proceeds to the assignment.
                return MagicMock(fetchone=lambda: ("delete",))
            return MagicMock()

        def close(self):
            pass

    monkeypatch.setattr(
        schema, "_open_configured_connection", lambda: FakeCon(),
    )
    # Speed up the test by zeroing the backoffs.
    monkeypatch.setattr(schema, "time", _ZeroSleepTime())

    schema._set_wal_mode_idempotent_with_retry()
    assert call_count["n"] == 3, (
        f"expected 3 WAL-assignment attempts, got {call_count['n']}"
    )


def test_raises_after_exhausting_retries(tmp_path, monkeypatch):
    """If all retry attempts fail with `database is locked`, the original
    exception propagates so the caller (lifespan) sees the failure."""
    from db import schema

    attempts = {"n": 0}

    class FakeCon:
        def execute(self, sql, *a, **kw):
            sql_upper = sql.upper().replace(" ", "")
            if "JOURNAL_MODE=WAL" in sql_upper:
                attempts["n"] += 1
                raise sqlite3.OperationalError("database is locked")
            if sql_upper == "PRAGMAJOURNAL_MODE":
                return MagicMock(fetchone=lambda: ("delete",))
            return MagicMock()

        def close(self):
            pass

    monkeypatch.setattr(
        schema, "_open_configured_connection", lambda: FakeCon(),
    )
    monkeypatch.setattr(schema, "time", _ZeroSleepTime())

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        schema._set_wal_mode_idempotent_with_retry()
    # 1 immediate + 3 backoff retries = 4 total attempts
    assert attempts["n"] == 4, (
        f"expected 4 attempts (1 + 3 retries), got {attempts['n']}"
    )


def test_non_lock_operational_errors_do_not_retry(monkeypatch):
    """A non-lock OperationalError (e.g., 'no such table') is not retryable
    and must propagate immediately."""
    from db import schema

    attempts = {"n": 0}

    class FakeCon:
        def execute(self, sql, *a, **kw):
            sql_upper = sql.upper().replace(" ", "")
            if "JOURNAL_MODE=WAL" in sql_upper:
                attempts["n"] += 1
                raise sqlite3.OperationalError("disk I/O error")
            if sql_upper == "PRAGMAJOURNAL_MODE":
                return MagicMock(fetchone=lambda: ("delete",))
            return MagicMock()

        def close(self):
            pass

    monkeypatch.setattr(
        schema, "_open_configured_connection", lambda: FakeCon(),
    )
    monkeypatch.setattr(schema, "time", _ZeroSleepTime())

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        schema._set_wal_mode_idempotent_with_retry()
    assert attempts["n"] == 1, (
        f"non-lock error must NOT trigger retry; got {attempts['n']} attempts"
    )


class _ZeroSleepTime:
    """`time` module shim that no-ops sleep — keeps the retry tests fast.
    `db.schema` imports `time` at module level, so monkeypatching
    `schema.time` replaces the binding the helper actually uses."""
    def sleep(self, _seconds):  # noqa: D401 — shim
        return None
