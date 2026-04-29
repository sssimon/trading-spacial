"""ASGI middleware — JWT enforcement on every request except whitelist.

Whitelist (always public):
    /health, /auth/login, /auth/refresh, /docs, /openapi.json, /redoc,
    OPTIONS preflight (CORS).

For non-whitelisted paths:
    1. Read the `access_token` cookie.
    2. Verify the JWT.
    3. Hydrate `request.state.user` with a User dataclass.
    4. If verification fails: 401 with body `{"detail": "not authenticated"}`.

Test bypass — TRIPLE GUARD (defense in depth):
    All THREE conditions must hold for the middleware to skip JWT verification:
      1. `AUTH_TEST_BYPASS_ALLOWED=1` env var set
      2. `AUTH_TEST_BYPASS_ROLE` env var set to 'admin' or 'viewer'
      3. `pytest` is in `sys.modules` (we're inside a pytest run)

    Even if a misconfigured deploy copies BOTH env vars from a CI environment
    into production, the third condition (`sys.modules['pytest']`) makes
    activation impossible outside pytest. The variable names are also
    deliberately verbose so they show up in `env | grep AUTH`.

    The autouse conftest fixture sets all three; the two explicit role
    fixtures override AUTH_TEST_BYPASS_ROLE only.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from auth.models import User
from auth.tokens import verify_access_token
from db.connection import get_db


def _bypass_role_or_none() -> str | None:
    """Returns 'admin'/'viewer' if the triple-guarded test bypass applies.

    Defence in depth — accidentally exporting AUTH_TEST_BYPASS_* in
    production still won't enable bypass because pytest isn't loaded there.
    """
    if os.environ.get("AUTH_TEST_BYPASS_ALLOWED", "").strip() != "1":
        return None
    if "pytest" not in sys.modules:
        return None
    role = os.environ.get("AUTH_TEST_BYPASS_ROLE", "").strip()
    if role in ("admin", "viewer"):
        return role
    return None


# Paths that never require auth.
#
# CRITICAL — DO NOT change /health to a prefix match. The /health/* tree
# contains admin-only operations and must stay protected:
#   - /health/symbols          → reads kill-switch tier per symbol
#   - /health/events           → reads transition history
#   - /health/dashboard        → aggregated portfolio health view
#   - /health/reactivate/{sym} → admin-only state change (PAUSED→PROBATION)
#
# A previous version of this whitelist had `/health` prefix-matched, which
# made /health/reactivate/JUP public — bypassing both AuthMiddleware AND
# require_role("admin"). The endpoint then 401'd from get_current_user
# because request.state.user was never set. Caught by tests, fixed
# 2026-04-29. The exact-match here is the fix; do not "simplify" it.
#
# Only the bare `/health` is public — that is the docker/monitoring
# health probe, used by scripts/REINICIAR_SERVICIOS.ps1 and similar.
#
# /docs and /redoc are prefix-matched so /docs/oauth2-redirect etc. work.
# /auth/login and /auth/refresh are entry points: no session yet.
_PUBLIC_PATHS_EXACT: frozenset = frozenset({
    "/health",       # <-- exact match ONLY; see comment above
    "/auth/login",
    "/auth/refresh",
    "/openapi.json",
    "/favicon.ico",
    # First-time setup (added 2026-04-29):
    # - /setup is the bootstrap path; gated internally by both the
    #   "no users" check and a 32-byte token. The middleware can't enforce
    #   auth on it because there is no user yet.
    # - /setup/status is a tiny JSON endpoint the frontend reads to decide
    #   whether to redirect to /setup or /login. It MUST be public so the
    #   frontend can render before login. Both endpoints have their own
    #   rate limiter (10/IP/hour) inside api/setup.py.
    # - After setup completes, the routes return 404 from inside the
    #   handler (system_state has setup_completed_at) — the whitelist
    #   here is a no-op once setup is done, but harmless.
    "/setup",
    "/setup/status",
})
_PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/docs",
    "/redoc",
)


def _is_public(path: str) -> bool:
    if path in _PUBLIC_PATHS_EXACT:
        return True
    for p in _PUBLIC_PATH_PREFIXES:
        if path == p or path.startswith(p + "/"):
            return True
    return False


def _hydrate_user_from_db(user_id: int) -> User | None:
    """Fetch a fresh User row by id. None if user is gone or deactivated.

    We refetch on every request rather than trusting the JWT claims alone,
    so a deactivated user gets locked out within their access-token TTL
    instead of having to wait until the JWT expires. This is one extra
    sqlite read per protected request — acceptable for 2-5 users at one
    request every few seconds.
    """
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
    if not row["is_active"]:
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


def _synthetic_test_user(role: str) -> User:
    """Return a fake User used only when AUTH_TEST_BYPASS_ROLE is set.

    id=0 by convention (no real user has id=0 since AUTOINCREMENT starts
    at 1). Email is unique-ish so audit logs don't collide if tests
    deliberately log events.
    """
    return User(
        id=0,
        email=f"test-{role}@bypass.local",
        role=role,
        is_active=True,
        created_at="1970-01-01T00:00:00+00:00",
        password_changed_at="1970-01-01T00:00:00+00:00",
        last_login_at=None,
    )


class AuthMiddleware(BaseHTTPMiddleware):
    """Enforce JWT cookie auth on all non-public paths."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        # CORS preflight always passes — actual CORS headers are emitted by
        # the CORSMiddleware that wraps us.
        if request.method.upper() == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        if _is_public(path):
            return await call_next(request)

        # ── Test bypass (triple-guarded) ─────────────────────────────
        bypass_role = _bypass_role_or_none()
        if bypass_role is not None:
            request.state.user = _synthetic_test_user(bypass_role)
            return await call_next(request)

        # ── Real JWT path ────────────────────────────────────────────
        access_token = request.cookies.get("access_token", "")
        claims = verify_access_token(access_token) if access_token else None
        if not claims:
            return JSONResponse(
                {"detail": "not authenticated"},
                status_code=401,
            )

        try:
            user_id = int(claims.get("sub", "0"))
        except (TypeError, ValueError):
            return JSONResponse(
                {"detail": "not authenticated"},
                status_code=401,
            )

        user = _hydrate_user_from_db(user_id)
        if user is None:
            return JSONResponse(
                {"detail": "not authenticated"},
                status_code=401,
            )

        request.state.user = user
        return await call_next(request)


# ─── CSRF middleware (double-submit cookie) ────────────────────────────────


_CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Paths exempt from CSRF: login (no session yet), refresh (refresh cookie is
# path-scoped to /auth/refresh and httpOnly — CSRF can't reach it from a
# malicious page without first reading the cookie, which httpOnly forbids).
_CSRF_EXEMPT_PATHS: tuple[str, ...] = (
    "/auth/login",
    "/auth/refresh",
)


class CsrfMiddleware(BaseHTTPMiddleware):
    """Enforce double-submit-cookie CSRF on state-changing requests.

    Rejects with 403 if header X-CSRF-Token is missing or doesn't match the
    csrf_token cookie. Skipped for safe methods, public paths, and the two
    exempt paths above. Test bypass: same env var as AuthMiddleware so the
    autouse fixture doesn't have to override two things separately.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        method = request.method.upper()
        if method in _CSRF_SAFE_METHODS:
            return await call_next(request)

        path = request.url.path
        if _is_public(path) or path in _CSRF_EXEMPT_PATHS:
            return await call_next(request)

        # Test bypass — same triple-guarded check as AuthMiddleware.
        if _bypass_role_or_none() is not None:
            # Skip CSRF in tests by default. Tests that want to verify CSRF
            # behavior unset AUTH_TEST_BYPASS_ROLE (or use unauthed_client).
            if os.environ.get("AUTH_TEST_BYPASS_CSRF", "1") != "0":
                return await call_next(request)

        header = request.headers.get("X-CSRF-Token", "")
        cookie = request.cookies.get("csrf_token", "")
        import hmac

        if not header or not cookie or not hmac.compare_digest(header, cookie):
            return JSONResponse(
                {"detail": "csrf token missing or invalid"},
                status_code=403,
            )

        return await call_next(request)
