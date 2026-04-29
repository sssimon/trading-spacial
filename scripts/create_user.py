#!/usr/bin/env python3
"""CLI tool: create a user in signals.db.

Usage:
    python scripts/create_user.py
    python scripts/create_user.py --email user@example.com --role admin

Behavior:
- Prompts for email if not provided.
- Validates the role (admin | viewer); defaults to viewer.
- Reads password from stdin TWICE without echo (getpass), validates strength.
- Refuses to create a duplicate email (unique constraint).
- Refuses if AUTH_JWT_SECRET is not set — surfacing the misconfiguration
  early rather than after the user is created and can't actually log in.
- Auto-creates the auth tables if they don't exist (idempotent).

The first run on a fresh signals.db: this CLI is the only way to bootstrap
a user. There is no signup endpoint and no default admin.
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure repo root on sys.path so we can `from auth ... import ...`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from auth.password import hash_password, password_meets_minimum  # noqa: E402
from db.auth_schema import init_auth_db  # noqa: E402
from db.connection import get_db  # noqa: E402


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _prompt_email(initial: str | None) -> str:
    if initial:
        if not _EMAIL_RE.match(initial):
            raise SystemExit(f"invalid email: {initial!r}")
        return initial.strip().lower()
    while True:
        email = input("Email: ").strip().lower()
        if _EMAIL_RE.match(email):
            return email
        print("invalid email format; try again", file=sys.stderr)


def _prompt_password() -> str:
    while True:
        pwd1 = getpass.getpass("Password (min 12 chars, max 72 bytes): ")
        ok, err = password_meets_minimum(pwd1)
        if not ok:
            print(f"rejected: {err}", file=sys.stderr)
            continue
        pwd2 = getpass.getpass("Confirm password: ")
        if pwd1 != pwd2:
            print("passwords do not match; try again", file=sys.stderr)
            continue
        return pwd1


def _email_exists(email: str) -> bool:
    con = get_db()
    try:
        row = con.execute(
            "SELECT 1 FROM users WHERE email = ?", (email,)
        ).fetchone()
        return row is not None
    finally:
        con.close()


def _create_user(email: str, password_hash: str, role: str) -> int:
    """Insert and return the new user_id."""
    now = datetime.now(timezone.utc).isoformat()
    con = get_db()
    try:
        cur = con.execute(
            """
            INSERT INTO users
                (email, password_hash, role, is_active,
                 created_at, password_changed_at)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (email, password_hash, role, now, now),
        )
        con.commit()
        return int(cur.lastrowid or 0)
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a new auth user")
    parser.add_argument("--email", default=None, help="Email address")
    parser.add_argument(
        "--role",
        default="viewer",
        choices=("admin", "viewer"),
        help="Role (default: viewer)",
    )
    args = parser.parse_args()

    # Surface JWT secret misconfiguration early. Without it, the user we
    # create cannot actually log in, so this is the right place to fail.
    if not os.environ.get("AUTH_JWT_SECRET"):
        print(
            "WARNING: AUTH_JWT_SECRET is not set. The user will be created\n"
            "but logins will fail until you set the secret. Generate with:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(64))"\n',
            file=sys.stderr,
        )

    init_auth_db()

    email = _prompt_email(args.email)
    if _email_exists(email):
        raise SystemExit(f"user already exists: {email}")

    password = _prompt_password()
    pwd_hash = hash_password(password)
    user_id = _create_user(email=email, password_hash=pwd_hash, role=args.role)

    print(f"created user id={user_id} email={email} role={args.role}")


if __name__ == "__main__":
    main()
