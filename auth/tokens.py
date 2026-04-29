"""Token issuance, verification, and rotation.

Access token: HS256 JWT, 15-minute lifetime, in httpOnly cookie.
Refresh token: opaque secrets.token_urlsafe(64), sha256-hashed in DB.

Refresh rotation:
- On /auth/refresh, the presented refresh is verified and revoked, and a new
  pair is issued. The new refresh inherits the same family_id.
- If a *revoked* refresh is presented (i.e. someone is using a token that
  was already rotated), we treat that as theft: revoke the entire family.
  RFC 6819 §5.2.2.3.

JWT secret is read from AUTH_JWT_SECRET. Boot fails if missing.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt

from auth.models import RefreshTokenRecord, User
from db.connection import get_db


# ─── Configuration helpers ──────────────────────────────────────────────────

_BOOT_CHECKED = False


def _jwt_secret() -> str:
    """Return AUTH_JWT_SECRET. Raises a hard error if missing — by design.

    The first time this is called we also do a length sanity check so we
    don't silently accept a default/short secret in any environment.
    """
    global _BOOT_CHECKED
    secret = os.environ.get("AUTH_JWT_SECRET", "")
    if not secret:
        raise RuntimeError(
            "AUTH_JWT_SECRET is not set. Refusing to boot.\n"
            "Generate one with: python -c \"import secrets; "
            "print(secrets.token_urlsafe(64))\"\n"
            "Then put it in .env (see .env.example)."
        )
    if not _BOOT_CHECKED:
        if len(secret) < 32:
            raise RuntimeError(
                f"AUTH_JWT_SECRET is too short ({len(secret)} chars). "
                "Use at least 32 characters; spec recommends 64."
            )
        _BOOT_CHECKED = True
    return secret


def _access_minutes() -> int:
    return int(os.environ.get("AUTH_ACCESS_TOKEN_MINUTES", "15"))


def _refresh_days() -> int:
    return int(os.environ.get("AUTH_REFRESH_TOKEN_DAYS", "7"))


# ─── Access tokens (JWT) ────────────────────────────────────────────────────


def create_access_token(user: User, *, now: Optional[datetime] = None) -> str:
    """Issue a JWT for the given user.

    Claims: sub (user id as str), email, role, iat, exp.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=_access_minutes())).timestamp()),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def verify_access_token(token: str) -> Optional[dict[str, Any]]:
    """Return claims if valid, None otherwise.

    PyJWT raises a variety of exceptions for invalid/expired/malformed
    tokens; we collapse all of them to None so callers can do a single
    falsy check. Specific failure reasons go to the audit trail.
    """
    if not token:
        return None
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


# ─── Refresh tokens (opaque + hashed in DB) ─────────────────────────────────


def _hash_refresh(token: str) -> str:
    """sha256 of the URL-safe token. We never store the plaintext."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_refresh_token(
    user: User,
    *,
    user_agent: Optional[str] = None,
    ip: Optional[str] = None,
    family_id: Optional[str] = None,
    parent_hash: Optional[str] = None,
    now: Optional[datetime] = None,
) -> str:
    """Mint a new refresh token, persist its hash, return the plaintext.

    `family_id` is generated for first-login. On rotation, the caller passes
    the family_id of the parent so the chain can be revoked atomically if
    we detect token reuse.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(64)
    token_hash = _hash_refresh(token)
    fid = family_id or uuid.uuid4().hex
    expires = now + timedelta(days=_refresh_days())

    con = get_db()
    try:
        con.execute(
            """
            INSERT INTO refresh_tokens
                (token_hash, user_id, family_id, parent_hash,
                 expires_at, revoked_at, created_at, user_agent, ip)
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                token_hash,
                user.id,
                fid,
                parent_hash,
                expires.isoformat(),
                now.isoformat(),
                user_agent,
                ip,
            ),
        )
        con.commit()
    finally:
        con.close()
    return token


def lookup_refresh(token: str) -> Optional[RefreshTokenRecord]:
    """Find a refresh token row by hash. Returns None if not found."""
    if not token:
        return None
    h = _hash_refresh(token)
    con = get_db()
    try:
        row = con.execute(
            """
            SELECT id, token_hash, user_id, family_id, parent_hash,
                   expires_at, revoked_at, created_at, user_agent, ip
            FROM refresh_tokens WHERE token_hash = ?
            """,
            (h,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        return None
    return RefreshTokenRecord(
        id=row["id"],
        token_hash=row["token_hash"],
        user_id=row["user_id"],
        family_id=row["family_id"],
        parent_hash=row["parent_hash"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
        created_at=row["created_at"],
        user_agent=row["user_agent"],
        ip=row["ip"],
    )


def revoke_refresh(token_hash: str, *, now: Optional[datetime] = None) -> None:
    """Mark one refresh token as revoked (does not DELETE — audit trail)."""
    if now is None:
        now = datetime.now(timezone.utc)
    con = get_db()
    try:
        con.execute(
            "UPDATE refresh_tokens SET revoked_at = ? "
            "WHERE token_hash = ? AND revoked_at IS NULL",
            (now.isoformat(), token_hash),
        )
        con.commit()
    finally:
        con.close()


def revoke_family(family_id: str, *, now: Optional[datetime] = None) -> int:
    """Mark every still-active token in this family as revoked.

    Returns the count of rows affected. Called on suspected token theft
    (someone re-presents an already-rotated refresh) — RFC 6819 §5.2.2.3.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    con = get_db()
    try:
        cur = con.execute(
            "UPDATE refresh_tokens SET revoked_at = ? "
            "WHERE family_id = ? AND revoked_at IS NULL",
            (now.isoformat(), family_id),
        )
        con.commit()
        return cur.rowcount or 0
    finally:
        con.close()


def revoke_all_for_user(user_id: int, *, now: Optional[datetime] = None) -> int:
    """Revoke every active refresh token belonging to this user.

    Used by password_change (force re-login on all devices).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    con = get_db()
    try:
        cur = con.execute(
            "UPDATE refresh_tokens SET revoked_at = ? "
            "WHERE user_id = ? AND revoked_at IS NULL",
            (now.isoformat(), user_id),
        )
        con.commit()
        return cur.rowcount or 0
    finally:
        con.close()
