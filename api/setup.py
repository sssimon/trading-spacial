"""First-time setup router: GET /setup, POST /setup, GET /setup/status.

Visibility rules (spec):
- If a user already exists OR system_state has setup_completed_at:
    → 404 on every /setup* path. NEVER reveal that the endpoint existed.
- If no users AND setup not marked AND a setup_token is active:
    → /setup serves the form / accepts the create.

GET /setup/status is the ONE endpoint that's always accessible (gated by
its own rate limiter): returns `{setup_required: bool}`. The frontend
uses it to decide whether to send the user to /setup or /login.

Rate limit: 10 per IP per hour, shared across the three endpoints. Not for
real security (token entropy = 32 bytes urlsafe), just to suppress timing-
scan probing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from auth.audit import log_auth_event
from auth.password import hash_password
from auth.rate_limit import check_setup_allowed
from auth.setup import (
    consume_token,
    token_matches,
    validate_setup_password,
)
from auth.setup_html import render_completed_redirect, render_setup_page
from db.auth_schema import has_any_user, is_setup_completed, mark_setup_completed
from db.connection import get_db


log = logging.getLogger("api.setup")
router = APIRouter(tags=["setup"])


def _client_ip(request: Request) -> str | None:
    xff = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    if xff:
        return xff
    if request.client:
        return request.client.host
    return None


def _setup_done() -> bool:
    """The kill-switch for /setup endpoints. Either user exists OR
    setup_completed_at marked → 404 forever.

    Note: deleting the row in system_state alone is not enough to reactivate
    /setup if there's still a user; you'd also need to delete that user.
    Conversely, if the user gets deleted but setup_completed_at remains,
    /setup stays dead — by design (recovery is via CLI per the README)."""
    return has_any_user() or is_setup_completed()


def _check_rate_limit_or_404(request: Request) -> None:
    """Apply the /setup rate limiter. Returns 404 (not 429) on throttle so
    the throttle is invisible to scanners — they can't tell apart 'not
    allowed' from 'doesn't exist'.

    Rationale: this endpoint must look identical to a legit 404 to anyone
    poking at it. A 429 leaks that the path exists. Spec says rate limit
    is anti-scanning, not anti-DoS.
    """
    ip = _client_ip(request)
    allowed, _ = check_setup_allowed(ip)
    if not allowed:
        raise HTTPException(status_code=404)


@router.get("/setup/status", summary="Public: is first-time setup needed?")
def setup_status(request: Request):
    """Returns {setup_required: bool}. Public, no token needed.

    The frontend hits this at boot to decide which page to show. Failure
    of this endpoint is treated client-side as setup_required=false (the
    real auth gate is the middleware on every other route)."""
    _check_rate_limit_or_404(request)
    return {"setup_required": not _setup_done()}


@router.get("/setup", response_class=HTMLResponse, summary="Setup form (HTML)")
def setup_get(request: Request, token: str = ""):
    """Vanilla HTML form. No-JS friendly. Works in lynx/w3m.

    404 if setup is already done OR token is wrong/missing. The two cases
    look identical to a probe.
    """
    _check_rate_limit_or_404(request)
    if _setup_done():
        raise HTTPException(status_code=404)
    if not token_matches(token):
        raise HTTPException(status_code=404)
    return HTMLResponse(render_setup_page(token=token))


@router.post("/setup", summary="Create the first admin user")
def setup_post(
    request: Request,
    token: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    _check_rate_limit_or_404(request)

    # Same 404 surface as GET — never confirm the endpoint exists.
    if _setup_done():
        raise HTTPException(status_code=404)
    if not token_matches(token):
        raise HTTPException(status_code=404)

    # Server-side validation (mirrors the no-JS HTML form).
    err: str | None = None
    email_clean = (email or "").strip().lower()
    if not email_clean or "@" not in email_clean:
        err = "invalid email"
    elif password != confirm_password:
        err = "passwords do not match"
    else:
        ok, msg = validate_setup_password(password)
        if not ok:
            err = msg

    if err is not None:
        # API clients prefer JSON; humans posting from the HTML form prefer
        # the form re-rendered with the error inline. Detect via Accept.
        accepts = (request.headers.get("Accept") or "").lower()
        if "application/json" in accepts:
            return JSONResponse({"detail": err}, status_code=400)
        return HTMLResponse(
            render_setup_page(token=token, error=err),
            status_code=400,
        )

    # Persist user (admin role) + system_state row + invalidate token.
    pwd_hash = hash_password(password)
    now = datetime.now(timezone.utc).isoformat()
    ip = _client_ip(request)
    ua = (request.headers.get("User-Agent") or "")[:512] or None

    con = get_db()
    try:
        cur = con.execute(
            """
            INSERT INTO users
                (email, password_hash, role, is_active,
                 created_at, password_changed_at)
            VALUES (?, ?, 'admin', 1, ?, ?)
            """,
            (email_clean, pwd_hash, now, now),
        )
        user_id = int(cur.lastrowid or 0)
        con.commit()
    finally:
        con.close()

    mark_setup_completed(ip=ip, method="web")
    consume_token()

    log_auth_event(
        event_type="initial_setup_completed",
        success=True,
        user_id=user_id,
        ip=ip,
        user_agent=ua,
        metadata={"method": "web", "email": email_clean},
    )
    log.info(
        "First-time setup completed via web (user_id=%d, email=%s, ip=%s)",
        user_id, email_clean, ip,
    )

    accepts = (request.headers.get("Accept") or "").lower()
    if "application/json" in accepts:
        return JSONResponse(
            {"ok": True, "user_id": user_id, "redirect": "/login"},
            status_code=201,
        )
    # Vanilla form submitter → meta-refresh to /login.
    return HTMLResponse(render_completed_redirect(), status_code=200)
