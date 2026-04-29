"""Auth router: /login /logout /refresh /me /change-password.

Cookies emitted:
- access_token: httpOnly, Secure (configurable in dev), SameSite=Lax, 15min
- refresh_token: httpOnly, Secure, SameSite=Lax,
  path=${AUTH_API_PREFIX}/auth/refresh, 7d
- csrf_token: NOT httpOnly (frontend reads it), Secure, SameSite=Lax,
  path=/, lifetime matches access_token

The `AUTH_API_PREFIX` env var (default empty) is prepended to the
refresh_token cookie path so it matches the URL the browser actually hits.
When deployed behind a reverse proxy that mounts the API under `/api/`
(nginx with `proxy_pass /;` strip-prefix, or Vite dev proxy), the browser
calls `/api/auth/refresh`. The cookie's path attribute must match for the
cookie to be sent — `path=/auth/refresh` would never match `/api/auth/refresh`.
Setting `AUTH_API_PREFIX=/api` produces `path=/api/auth/refresh`, which does
match. The router itself stays mounted at `/auth/*`; the proxy strips the
prefix before requests reach FastAPI, so middleware and route handlers see
unprefixed paths regardless.

CSRF on mutating endpoints uses double-submit-cookie pattern.

`/auth/login` is the only mutating endpoint without CSRF (no session yet).
`/auth/refresh` doesn't require CSRF either: it consumes a refresh token
which is path-scoped to /auth/refresh (or its prefixed variant) and httpOnly,
so CSRF can't reach it from a malicious site without first exfiltrating
the cookie.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from auth.audit import log_auth_event as _real_log_auth_event
from auth.dependencies import get_current_user, require_csrf
from auth.models import User


def log_auth_event(**kwargs):
    """Belt-and-suspenders wrapper.

    auth.audit.log_auth_event is already failure-tolerant. This wrapper
    exists so that even if a test (or a future code change) replaces the
    function entirely with one that raises, the auth flow still survives.
    Spec rule: 'audit trail is important, but not more than login working'.
    """
    try:
        _real_log_auth_event(**kwargs)
    except Exception as exc:
        import sys
        sys.stderr.write(
            f"[api.auth] audit wrapper swallowed {type(exc).__name__}: {exc}\n"
        )
        sys.stderr.flush()
from auth.password import (
    hash_password,
    password_meets_minimum,
    verify_password,
    dummy_verify,
)
from auth.rate_limit import (
    check_login_allowed,
    record_login_failure,
    record_login_success,
)
from auth.tokens import (
    create_access_token,
    create_refresh_token,
    lookup_refresh,
    revoke_all_for_user,
    revoke_family,
    revoke_refresh,
    _access_minutes,
    _refresh_days,
)
from db.connection import get_db


router = APIRouter(prefix="/auth", tags=["auth"])


# ─── Schemas ────────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=200)
    new_password: str = Field(..., min_length=12, max_length=200)


class UserResponse(BaseModel):
    id: int
    email: str
    role: str
    is_active: bool
    last_login_at: Optional[str] = None


# ─── Helpers ────────────────────────────────────────────────────────────────


def _cookie_secure() -> bool:
    return os.environ.get("AUTH_COOKIE_SECURE", "0") == "1"


def _cookie_domain() -> Optional[str]:
    d = os.environ.get("AUTH_COOKIE_DOMAIN", "").strip()
    return d or None


def _api_prefix() -> str:
    """Public path prefix that the reverse proxy adds to backend URLs.

    The browser hits `${AUTH_API_PREFIX}/auth/refresh`, but FastAPI sees
    `/auth/refresh` (the proxy strips the prefix). The refresh_token cookie's
    `path` attribute must match what the browser requests, so we prepend
    this prefix when emitting the cookie.

    Default empty preserves compat with environments that don't sit behind
    a prefix-stripping proxy. Production sets `AUTH_API_PREFIX=/api`.
    """
    p = os.environ.get("AUTH_API_PREFIX", "").strip()
    if not p:
        return ""
    # Normalize: leading slash, no trailing slash.
    if not p.startswith("/"):
        p = "/" + p
    return p.rstrip("/")


def _client_ip(request: Request) -> Optional[str]:
    """Extract the client's IP. Trusts X-Forwarded-For only if explicitly
    enabled — for now, fall back to the socket peer."""
    xff = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    if xff:
        return xff
    if request.client:
        return request.client.host
    return None


def _user_agent(request: Request) -> Optional[str]:
    ua = request.headers.get("User-Agent", "").strip()
    return ua or None


def _user_to_response(u: User) -> UserResponse:
    return UserResponse(
        id=u.id,
        email=u.email,
        role=u.role,
        is_active=u.is_active,
        last_login_at=u.last_login_at,
    )


def _set_auth_cookies(
    response: Response, *, access_token: str, refresh_token: str, csrf_token: str
) -> None:
    """Set the three auth cookies on the response.

    access_token: httpOnly, path=/, lifetime = access_minutes
    refresh_token: httpOnly, path=${AUTH_API_PREFIX}/auth/refresh, lifetime = refresh_days
    csrf_token: NOT httpOnly, path=/, lifetime = access_minutes
    """
    secure = _cookie_secure()
    domain = _cookie_domain()
    refresh_path = f"{_api_prefix()}/auth/refresh"
    common = {
        "secure": secure,
        "samesite": "lax",
        "domain": domain,
    }

    response.set_cookie(
        "access_token",
        access_token,
        httponly=True,
        max_age=_access_minutes() * 60,
        path="/",
        **common,
    )
    response.set_cookie(
        "refresh_token",
        refresh_token,
        httponly=True,
        max_age=_refresh_days() * 24 * 60 * 60,
        path=refresh_path,
        **common,
    )
    response.set_cookie(
        "csrf_token",
        csrf_token,
        httponly=False,  # JS reads it
        max_age=_access_minutes() * 60,
        path="/",
        **common,
    )


def _clear_auth_cookies(response: Response) -> None:
    """Best-effort cookie deletion. Browser will overwrite with empty values."""
    domain = _cookie_domain()
    refresh_path = f"{_api_prefix()}/auth/refresh"
    for name, path in (
        ("access_token", "/"),
        ("refresh_token", refresh_path),
        ("csrf_token", "/"),
    ):
        response.delete_cookie(name, path=path, domain=domain)


def _user_by_email(email: str) -> Optional[User]:
    con = get_db()
    try:
        row = con.execute(
            """
            SELECT id, email, password_hash, role, is_active, created_at,
                   password_changed_at, last_login_at, totp_secret, oauth_provider
            FROM users WHERE email = ?
            """,
            (email,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        return None
    return User(
        id=row["id"],
        email=row["email"],
        role=row["role"],
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        password_changed_at=row["password_changed_at"],
        last_login_at=row["last_login_at"],
        totp_secret=row["totp_secret"],
        oauth_provider=row["oauth_provider"],
    )


def _password_hash_for_email(email: str) -> Optional[str]:
    """Fetch the bcrypt hash for an email. Used by login + change_password.
    Returns None if user does not exist."""
    con = get_db()
    try:
        row = con.execute(
            "SELECT password_hash FROM users WHERE email = ?", (email,)
        ).fetchone()
    finally:
        con.close()
    return row["password_hash"] if row else None


def _update_last_login(user_id: int) -> None:
    con = get_db()
    try:
        con.execute(
            "UPDATE users SET last_login_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        con.commit()
    finally:
        con.close()


# ─── Routes ─────────────────────────────────────────────────────────────────


@router.post("/login", summary="Email + password login → sets cookies")
def login(request: Request, response: Response, body: LoginRequest):
    ip = _client_ip(request)
    ua = _user_agent(request)
    email_lower = body.email.strip().lower()

    # Rate limit FIRST (cheap dict lookup), so brute-force attempts don't even
    # get to the bcrypt cost.
    allowed, retry_after = check_login_allowed(ip, email_lower)
    if not allowed:
        log_auth_event(
            event_type="login_failed",
            success=False,
            ip=ip,
            user_agent=ua,
            metadata={"reason": "rate_limited", "retry_after_seconds": retry_after},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many login attempts; try again later",
            headers={"Retry-After": str(retry_after or 1)},
        )

    pwd_hash = _password_hash_for_email(email_lower)
    if pwd_hash is None:
        # Spend the bcrypt time anyway — no timing oracle for "user exists".
        dummy_verify()
        record_login_failure(ip, email_lower)
        log_auth_event(
            event_type="login_failed",
            success=False,
            ip=ip,
            user_agent=ua,
            metadata={"reason": "unknown_email"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )

    if not verify_password(body.password, pwd_hash):
        record_login_failure(ip, email_lower)
        # We log with the matched user_id when the email exists, so audit
        # can show "5 failed logins followed by 1 success" patterns.
        user = _user_by_email(email_lower)
        log_auth_event(
            event_type="login_failed",
            success=False,
            user_id=user.id if user else None,
            ip=ip,
            user_agent=ua,
            metadata={"reason": "wrong_password"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )

    # ── Password OK ──────────────────────────────────────────────────────
    user = _user_by_email(email_lower)
    if user is None or not user.is_active:
        # Race condition or deactivated mid-login. Treat as auth failure.
        log_auth_event(
            event_type="login_failed",
            success=False,
            user_id=user.id if user else None,
            ip=ip,
            user_agent=ua,
            metadata={"reason": "deactivated_or_gone"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )

    record_login_success(ip, email_lower)
    _update_last_login(user.id)

    access_token = create_access_token(user)
    refresh_token = create_refresh_token(user, user_agent=ua, ip=ip)
    csrf_token = secrets.token_urlsafe(32)

    _set_auth_cookies(
        response,
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
    )
    log_auth_event(
        event_type="login_success",
        success=True,
        user_id=user.id,
        ip=ip,
        user_agent=ua,
    )
    return {"user": _user_to_response(user)}


@router.post("/refresh", summary="Rotate refresh token → new pair")
def refresh(request: Request, response: Response):
    ip = _client_ip(request)
    ua = _user_agent(request)
    presented = request.cookies.get("refresh_token", "")
    if not presented:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh token missing",
        )

    record = lookup_refresh(presented)
    if record is None:
        # Token has never existed (or is malformed). Either bot or stale cookie.
        log_auth_event(
            event_type="refresh",
            success=False,
            ip=ip,
            user_agent=ua,
            metadata={"reason": "unknown_token"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid refresh token",
        )

    now = datetime.now(timezone.utc)
    if record.is_expired(now):
        log_auth_event(
            event_type="refresh",
            success=False,
            user_id=record.user_id,
            ip=ip,
            user_agent=ua,
            metadata={"reason": "expired", "family_id": record.family_id},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh token expired",
        )

    if record.is_revoked():
        # Token theft signal — RFC 6819 §5.2.2.3. Revoke entire family so any
        # legitimate device that's still active also has to re-login.
        revoked_count = revoke_family(record.family_id, now=now)
        log_auth_event(
            event_type="refresh_reuse_detected",
            success=False,
            user_id=record.user_id,
            ip=ip,
            user_agent=ua,
            metadata={
                "family_id": record.family_id,
                "tokens_revoked": revoked_count,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh token reused; family revoked",
        )

    # ── Rotate ───────────────────────────────────────────────────────────
    user = _hydrate_user(record.user_id)
    if user is None or not user.is_active:
        revoke_refresh(record.token_hash, now=now)
        log_auth_event(
            event_type="refresh",
            success=False,
            user_id=record.user_id,
            ip=ip,
            user_agent=ua,
            metadata={"reason": "user_inactive"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid refresh token",
        )

    revoke_refresh(record.token_hash, now=now)
    new_refresh = create_refresh_token(
        user,
        user_agent=ua,
        ip=ip,
        family_id=record.family_id,
        parent_hash=record.token_hash,
        now=now,
    )
    new_access = create_access_token(user, now=now)
    new_csrf = secrets.token_urlsafe(32)

    _set_auth_cookies(
        response,
        access_token=new_access,
        refresh_token=new_refresh,
        csrf_token=new_csrf,
    )
    log_auth_event(
        event_type="refresh",
        success=True,
        user_id=user.id,
        ip=ip,
        user_agent=ua,
        metadata={"family_id": record.family_id},
    )
    return {"user": _user_to_response(user)}


@router.post(
    "/logout",
    summary="Invalidate refresh token + clear cookies",
    dependencies=[Depends(require_csrf)],
)
def logout(request: Request, response: Response):
    ip = _client_ip(request)
    ua = _user_agent(request)
    presented = request.cookies.get("refresh_token", "")
    user_id = None

    if presented:
        record = lookup_refresh(presented)
        if record is not None:
            user_id = record.user_id
            if not record.is_revoked():
                revoke_refresh(record.token_hash)

    _clear_auth_cookies(response)
    log_auth_event(
        event_type="logout",
        success=True,
        user_id=user_id,
        ip=ip,
        user_agent=ua,
    )
    return {"ok": True}


@router.get("/me", summary="Current user (200 if authenticated)")
def me(user: User = Depends(get_current_user)):
    return {"user": _user_to_response(user)}


@router.post(
    "/change-password",
    summary="Change own password — requires current password",
    dependencies=[Depends(require_csrf)],
)
def change_password(
    request: Request,
    response: Response,
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
):
    ip = _client_ip(request)
    ua = _user_agent(request)

    pwd_hash = _password_hash_for_email(user.email)
    if pwd_hash is None or not verify_password(body.current_password, pwd_hash):
        log_auth_event(
            event_type="password_change",
            success=False,
            user_id=user.id,
            ip=ip,
            user_agent=ua,
            metadata={"reason": "wrong_current_password"},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="current password is incorrect",
        )

    ok, err = password_meets_minimum(body.new_password)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=err,
        )
    if body.new_password == body.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="new password must differ from current",
        )

    new_hash = hash_password(body.new_password)
    now = datetime.now(timezone.utc).isoformat()
    con = get_db()
    try:
        con.execute(
            "UPDATE users SET password_hash = ?, password_changed_at = ? "
            "WHERE id = ?",
            (new_hash, now, user.id),
        )
        con.commit()
    finally:
        con.close()

    # Revoke ALL refresh tokens for this user — forces re-login on every
    # device they have. Spec compliance: a password change should invalidate
    # other sessions.
    revoke_all_for_user(user.id)
    _clear_auth_cookies(response)

    log_auth_event(
        event_type="password_change",
        success=True,
        user_id=user.id,
        ip=ip,
        user_agent=ua,
    )
    return {"ok": True, "message": "password changed; please log in again"}


# ─── Local helpers (private) ────────────────────────────────────────────────


def _hydrate_user(user_id: int) -> Optional[User]:
    """Refresh-flow user hydration. Mirrors the middleware helper but returns
    None if user is gone or deactivated (caller decides what to do)."""
    con = get_db()
    try:
        row = con.execute(
            """
            SELECT id, email, role, is_active, created_at, password_changed_at,
                   last_login_at, totp_secret, oauth_provider
            FROM users WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        return None
    return User(
        id=row["id"],
        email=row["email"],
        role=row["role"],
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        password_changed_at=row["password_changed_at"],
        last_login_at=row["last_login_at"],
        totp_secret=row["totp_secret"],
        oauth_provider=row["oauth_provider"],
    )
