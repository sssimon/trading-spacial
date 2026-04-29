"""Auth-related DB schema.

Tables:
- users: account, password hash, role, optional 2FA/oauth slots for future
- refresh_tokens: rotation chain with family_id for theft detection
- auth_events: audit trail (login_success / login_failed / logout / refresh /
  password_change / role_change). NEVER stores password or token plaintext.

init_auth_db() is idempotent (CREATE TABLE IF NOT EXISTS) and is invoked
from btc_api.lifespan() right after init_db().
"""
from __future__ import annotations

import logging

from db.connection import get_db

log = logging.getLogger("db.auth_schema")


def init_auth_db() -> None:
    """Create auth tables if missing. Safe to call repeatedly."""
    con = get_db()
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                email               TEXT NOT NULL UNIQUE,
                password_hash       TEXT NOT NULL,
                role                TEXT NOT NULL DEFAULT 'viewer'
                                          CHECK (role IN ('admin', 'viewer')),
                is_active           INTEGER NOT NULL DEFAULT 1,
                totp_secret         TEXT,
                oauth_provider      TEXT,
                created_at          TEXT NOT NULL,
                last_login_at       TEXT,
                password_changed_at TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash   TEXT NOT NULL UNIQUE,
                user_id      INTEGER NOT NULL REFERENCES users(id),
                family_id    TEXT NOT NULL,
                parent_hash  TEXT,
                expires_at   TEXT NOT NULL,
                revoked_at   TEXT,
                created_at   TEXT NOT NULL,
                user_agent   TEXT,
                ip           TEXT
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_refresh_user "
            "ON refresh_tokens(user_id, revoked_at)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_refresh_family "
            "ON refresh_tokens(family_id)"
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER REFERENCES users(id),
                event_type    TEXT NOT NULL,
                ip            TEXT,
                user_agent    TEXT,
                ts            TEXT NOT NULL,
                success       INTEGER NOT NULL,
                metadata_json TEXT
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_events_user_ts "
            "ON auth_events(user_id, ts DESC)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_events_ts "
            "ON auth_events(ts DESC)"
        )
        con.commit()
    finally:
        con.close()


def has_any_user() -> bool:
    """True if at least one user exists. Used by app boot to print a hint
    when the DB is fresh and nobody has run scripts/create_user.py yet."""
    con = get_db()
    try:
        row = con.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        return row is not None
    finally:
        con.close()


# ─── system_state (added 2026-04-29 with first-time setup) ─────────────────
#
# Single-row-per-key bag for app-level flags. The "setup_completed_at" key
# is the gate that determines whether the /setup endpoint exists at all.
# We use a generic key/value table (rather than a one-off table for setup)
# so future flags (first_run_telemetry_opt_in, etc.) live in the same
# place without schema churn.


def init_system_state() -> None:
    """Idempotent — create system_state if missing."""
    con = get_db()
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS system_state (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        con.commit()
    finally:
        con.close()


def is_setup_completed() -> bool:
    """True if setup_completed_at row exists.

    We deliberately do NOT also infer "completed" from has_any_user(): the
    spec is explicit that if the admin row gets deleted accidentally, the
    system stays inaccessible via web. Recovery requires CLI or a manual
    DELETE on this row (documented in README).
    """
    con = get_db()
    try:
        row = con.execute(
            "SELECT 1 FROM system_state WHERE key = 'setup_completed_at'"
        ).fetchone()
        return row is not None
    finally:
        con.close()


def mark_setup_completed(*, ip: str | None, method: str) -> None:
    """Persist that initial setup ran. method ∈ {web, cli, env_vars}.

    Stored as two separate rows so a SELECT * shows both fields. Uses
    INSERT OR REPLACE so a manual rerun (after deleting the rows for
    recovery) doesn't blow up.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    con = get_db()
    try:
        con.execute(
            "INSERT OR REPLACE INTO system_state(key, value, updated_at) "
            "VALUES ('setup_completed_at', ?, ?)",
            (now, now),
        )
        con.execute(
            "INSERT OR REPLACE INTO system_state(key, value, updated_at) "
            "VALUES ('setup_completed_ip', ?, ?)",
            (ip or "", now),
        )
        con.execute(
            "INSERT OR REPLACE INTO system_state(key, value, updated_at) "
            "VALUES ('setup_completed_method', ?, ?)",
            (method, now),
        )
        con.commit()
    finally:
        con.close()
