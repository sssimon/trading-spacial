#!/usr/bin/env python3
"""CLI tool: reset a user's password.

Usage:
    python scripts/reset_password.py
    python scripts/reset_password.py --email user@example.com

Effects:
- Replaces the password hash for the matching user.
- Updates password_changed_at to now.
- Revokes EVERY active refresh token for that user (logout on all devices).
- Logs an auth_events row of type 'password_reset_via_cli'.

There is intentionally no /auth/forgot-password web endpoint. Recovery
requires shell access to the server. This is the security model — see
README "First-time setup" section.
"""
from __future__ import annotations

import argparse
import getpass
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from auth.audit import log_auth_event  # noqa: E402
from auth.password import hash_password  # noqa: E402
from auth.setup import validate_setup_password  # noqa: E402
from auth.tokens import revoke_all_for_user  # noqa: E402
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


def _find_user(email: str) -> tuple[int, bool] | None:
    con = get_db()
    try:
        row = con.execute(
            "SELECT id, is_active FROM users WHERE email = ?", (email,)
        ).fetchone()
    finally:
        con.close()
    if not row:
        return None
    return int(row["id"]), bool(row["is_active"])


def _prompt_new_password() -> str:
    while True:
        pwd1 = getpass.getpass("New password (min 12 chars, letter+digit): ")
        ok, err = validate_setup_password(pwd1)
        if not ok:
            print(f"rejected: {err}", file=sys.stderr)
            continue
        pwd2 = getpass.getpass("Confirm new password: ")
        if pwd1 != pwd2:
            print("passwords do not match; try again", file=sys.stderr)
            continue
        return pwd1


def _update_password(user_id: int, pwd_hash: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    con = get_db()
    try:
        con.execute(
            "UPDATE users SET password_hash = ?, password_changed_at = ? "
            "WHERE id = ?",
            (pwd_hash, now, user_id),
        )
        con.commit()
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset a user's password")
    parser.add_argument("--email", default=None, help="User email")
    args = parser.parse_args()

    init_auth_db()
    email = _prompt_email(args.email)
    found = _find_user(email)
    if found is None:
        raise SystemExit(f"no such user: {email}")
    user_id, is_active = found
    if not is_active:
        print(f"WARNING: user {email} is marked inactive (is_active=0).",
              file=sys.stderr)

    new_pwd = _prompt_new_password()
    pwd_hash = hash_password(new_pwd)
    _update_password(user_id, pwd_hash)
    revoked = revoke_all_for_user(user_id)

    log_auth_event(
        event_type="password_reset_via_cli",
        success=True,
        user_id=user_id,
        metadata={"email": email, "tokens_revoked": revoked},
    )

    print(
        f"✓ password reset for user_id={user_id} email={email}; "
        f"{revoked} refresh token(s) revoked."
    )


if __name__ == "__main__":
    main()
