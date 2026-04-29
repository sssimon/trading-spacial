"""FastAPI dependencies — get_current_user, require_role, require_csrf.

These read from `request.state.user`, which is populated by AuthMiddleware
(or a test bypass). They are lightweight wrappers; the real enforcement is
in the middleware. Reasons to use these in route handlers:

- Type the `current_user` parameter for static analysis and Swagger UI.
- Apply per-route role gating with `Depends(require_role('admin'))`.
- Enforce CSRF on mutating routes via `Depends(require_csrf)`.
"""
from __future__ import annotations

from typing import Callable

from fastapi import Depends, HTTPException, Request, status

from auth.models import User


def get_current_user(request: Request) -> User:
    """Return the User attached by AuthMiddleware. 401 if missing.

    The middleware will normally have rejected unauthenticated requests
    before this dependency runs. This is the belt-and-suspenders check —
    if anything ever bypasses the middleware (e.g. a misconfigured
    whitelist), the dependency still refuses anonymous access.
    """
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
        )
    return user


def require_role(required: str) -> Callable[[User], User]:
    """Dependency factory: ensure the current user has the given role.

    Usage:
        @router.post("/foo", dependencies=[Depends(require_role("admin"))])
    """

    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role != required:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role '{required}' required",
            )
        return user

    return _dep


def require_csrf(request: Request) -> None:
    """Double-submit-cookie CSRF check.

    GET / HEAD / OPTIONS are skipped (RFC: safe methods).
    `/auth/login` is also skipped (no session yet).
    For everything else we require the X-CSRF-Token header to match the
    csrf_token cookie. Constant-time compare.
    """
    method = request.method.upper()
    if method in ("GET", "HEAD", "OPTIONS"):
        return
    path = request.url.path
    if path.endswith("/auth/login"):
        return

    header = request.headers.get("X-CSRF-Token", "")
    cookie = request.cookies.get("csrf_token", "")
    # Constant-time compare; both must be non-empty to count as a match.
    import hmac

    if not header or not cookie or not hmac.compare_digest(header, cookie):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="csrf token missing or invalid",
        )
