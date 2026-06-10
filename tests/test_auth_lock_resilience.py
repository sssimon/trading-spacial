"""Regression: login must survive SQLite writer-lock contention.

Prod incident 2026-06-10: the background scanner (same process, same
`signals.db`) monopolised the single SQLite writer lock during slow/cold scan
cycles. Login's two `BEGIN IMMEDIATE` writes — `_update_last_login` (cosmetic)
and the refresh-token INSERT (critical) — timed out and raised
`sqlite3.OperationalError: database is locked`, which fell through to a 500
for EVERY tenant. Traceback died at api/auth.py:344 `_update_last_login`.

Fix contract:
- `_update_last_login` is best-effort: a writer-lock timeout must NEVER fail
  authentication (last_login_at is cosmetic).
- the refresh-token write retries on transient "database is locked" so an
  interactive login rides out a scanner write-burst instead of 500-ing.
- a NON-lock DB error is NOT swallowed (real bugs must still surface).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from auth.password import hash_password


def _create_user(client, email, password, role="admin") -> int:
    from db.transaction import transaction

    now = datetime.now(timezone.utc).isoformat()
    with transaction() as con:
        cur = con.execute(
            "INSERT INTO users (email, password_hash, role, is_active, "
            "created_at, password_changed_at) VALUES (?, ?, ?, 1, ?, ?)",
            (email.lower(), hash_password(password), role, now, now),
        )
        return int(cur.lastrowid)


def _login(client, email, password):
    return client.post("/auth/login", json={"email": email, "password": password})


_PW = "correct horse battery staple"


def test_login_survives_transient_writer_lock(unauthed_client, monkeypatch):
    """Scanner holds the writer lock for the last_login write + the first
    refresh-token attempts; login must still succeed (best-effort + retry)."""
    _create_user(unauthed_client, "lock1@example.com", _PW)

    import api.auth

    real_tx = api.auth.transaction
    state = {"n": 0}

    @contextmanager
    def contended_tx(*args, **kwargs):
        state["n"] += 1
        # Calls: 1 = _update_last_login, 2..3 = refresh-token attempts 1..2.
        # All contended; the lock frees by the refresh-token's 3rd attempt.
        if state["n"] <= 3:
            raise sqlite3.OperationalError("database is locked")
        with real_tx(*args, **kwargs) as con:
            yield con

    monkeypatch.setattr(api.auth, "transaction", contended_tx)

    resp = _login(unauthed_client, "lock1@example.com", _PW)
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["email"] == "lock1@example.com"


def test_login_succeeds_when_only_last_login_is_contended(unauthed_client, monkeypatch):
    """Best-effort: a writer-lock timeout on the cosmetic last_login write
    must not fail login; the critical refresh-token write still runs."""
    _create_user(unauthed_client, "lock2@example.com", _PW)

    import api.auth

    real_tx = api.auth.transaction
    state = {"n": 0}

    @contextmanager
    def first_call_locked(*args, **kwargs):
        state["n"] += 1
        if state["n"] == 1:  # only the last_login write
            raise sqlite3.OperationalError("database is locked")
        with real_tx(*args, **kwargs) as con:
            yield con

    monkeypatch.setattr(api.auth, "transaction", first_call_locked)

    resp = _login(unauthed_client, "lock2@example.com", _PW)
    assert resp.status_code == 200, resp.text


def test_login_does_not_swallow_non_lock_db_errors(unauthed_client, monkeypatch):
    """A real (non-lock) DB error must surface, not be hidden by the
    lock-resilience handling."""
    _create_user(unauthed_client, "lock3@example.com", _PW)

    import api.auth

    @contextmanager
    def broken_tx(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: users")
        yield  # pragma: no cover

    monkeypatch.setattr(api.auth, "transaction", broken_tx)

    # The non-lock error must NOT be swallowed — it propagates (surfaces as a
    # 500). TestClient (raise_server_exceptions=True) re-raises it here.
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        _login(unauthed_client, "lock3@example.com", _PW)
