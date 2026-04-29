"""Test suite for the authentication system.

Covers the spec's required scenarios:
1. Login success returns httpOnly cookies + user.
2. Login with unknown email vs wrong password → same error, similar timing.
3. Protected endpoint without cookie → 401.
4. /auth/refresh rotates and revokes the old refresh token.
5. Reusing a rotated refresh token → 401 + revokes the entire family.
6. Rate limit: 6th login attempt within window → 429.
7. CSRF: POST without X-CSRF-Token → 403.
8. require_role('admin') with viewer user → 403.
9. Logout revokes refresh + clears cookies.
10. Change-password requires the current password.
11. Boot fails without AUTH_JWT_SECRET.

The fixtures in conftest.py default tests to bypass-admin mode; this file's
tests use `unauthed_client` and `viewer_client` to exercise the real flows.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from auth.password import hash_password


# ─── Helpers ───────────────────────────────────────────────────────────────


def _create_user_directly(client, email, password, role="viewer"):
    """Use the DB directly (not /auth/register — it doesn't exist)."""
    from db.connection import get_db

    pwd_hash = hash_password(password)
    now = datetime.now(timezone.utc).isoformat()
    con = get_db()
    try:
        cur = con.execute(
            """
            INSERT INTO users
                (email, password_hash, role, is_active, created_at, password_changed_at)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (email.lower(), pwd_hash, role, now, now),
        )
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def _login(client, email, password):
    return client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )


# ─── Tests ──────────────────────────────────────────────────────────────────


def test_login_success_sets_httponly_cookies(unauthed_client):
    user_id = _create_user_directly(
        unauthed_client, "alice@example.com", "correct horse battery staple", role="admin"
    )
    resp = _login(unauthed_client, "alice@example.com", "correct horse battery staple")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["email"] == "alice@example.com"
    assert body["user"]["role"] == "admin"
    assert body["user"]["id"] == user_id

    # All three cookies present
    cookies = resp.cookies
    assert "access_token" in cookies
    assert "refresh_token" in cookies
    assert "csrf_token" in cookies

    # access_token + refresh_token must be httpOnly (in raw Set-Cookie headers)
    set_cookie_headers = resp.headers.get_list("set-cookie") if hasattr(
        resp.headers, "get_list"
    ) else [resp.headers.get("set-cookie", "")]
    raw = " ".join(set_cookie_headers)
    assert "access_token=" in raw and "HttpOnly" in raw
    assert "refresh_token=" in raw

    # The user record was NOT included in any non-cookie header (no token leak).
    assert "Authorization" not in resp.headers


def test_login_unknown_email_same_error_as_wrong_password(unauthed_client):
    _create_user_directly(unauthed_client, "bob@example.com", "long_pass_phrase_x9", role="viewer")

    r1 = _login(unauthed_client, "bob@example.com", "wrong_password_long")
    r2 = _login(unauthed_client, "nobody@example.com", "any_password_here_xx")

    assert r1.status_code == 401
    assert r2.status_code == 401
    assert r1.json()["detail"] == r2.json()["detail"] == "invalid credentials"


def test_login_constant_time_within_150ms(unauthed_client, monkeypatch):
    """Loose timing test: unknown-email path must spend bcrypt time too.

    Disable the rate limiter for this test (we'd hit it after the warmup
    samples and bias the timing), and use median-of-3 to suppress noise
    from CI scheduler jitter. 150ms ceiling is loose by design — we're
    not measuring bcrypt cost, only that BOTH paths perform a bcrypt
    verify. With cost=4 the typical delta is <5ms locally.
    """
    # Effectively disable rate limiting so all samples reach the bcrypt path.
    monkeypatch.setenv("AUTH_LOGIN_MAX_PER_IP", "10000")
    monkeypatch.setenv("AUTH_LOGIN_MAX_PER_EMAIL", "10000")
    from auth.rate_limit import reset_all_for_tests
    reset_all_for_tests()

    _create_user_directly(
        unauthed_client, "carol@example.com", "long_pass_phrase_xx", role="viewer"
    )

    # Warm up — first bcrypt call has lazy-init overhead (dummy hash)
    _login(unauthed_client, "carol@example.com", "wrong_warm_a")
    _login(unauthed_client, "ghost@example.com", "wrong_warm_b")

    def _ms(email, pwd):
        start = time.monotonic()
        _login(unauthed_client, email, pwd)
        return (time.monotonic() - start) * 1000

    # Median of 3 consecutive measurements per path. Interleave to share
    # any sustained noise (e.g. a GC pause or scheduler hiccup).
    real_samples, fake_samples = [], []
    for i in range(3):
        real_samples.append(_ms("carol@example.com", f"wrong_r{i}"))
        fake_samples.append(_ms("ghost@example.com", f"wrong_f{i}"))
    real_samples.sort()
    fake_samples.sort()
    real_median = real_samples[1]
    fake_median = fake_samples[1]
    delta = abs(real_median - fake_median)

    assert delta < 150, (
        f"timing difference {delta:.1f}ms exceeds 150ms bound "
        f"(real={real_samples}, fake={fake_samples})"
    )


def test_protected_endpoint_no_cookie_returns_401(unauthed_client):
    """Any non-public path without auth cookies → 401."""
    resp = unauthed_client.get("/auth/me")
    assert resp.status_code == 401


def test_refresh_with_valid_token_rotates_and_revokes_old(unauthed_client):
    _create_user_directly(unauthed_client, "dan@example.com", "long_pass_phrase_a", role="viewer")
    login_resp = _login(unauthed_client, "dan@example.com", "long_pass_phrase_a")
    assert login_resp.status_code == 200

    old_refresh = login_resp.cookies.get("refresh_token")
    assert old_refresh

    # Send refresh request with old refresh cookie
    resp = unauthed_client.post(
        "/auth/refresh",
        cookies={"refresh_token": old_refresh},
    )
    assert resp.status_code == 200, resp.text
    new_refresh = resp.cookies.get("refresh_token")
    assert new_refresh
    assert new_refresh != old_refresh

    # Old refresh hash should be revoked in DB
    from auth.tokens import _hash_refresh
    from db.connection import get_db

    con = get_db()
    try:
        row = con.execute(
            "SELECT revoked_at FROM refresh_tokens WHERE token_hash = ?",
            (_hash_refresh(old_refresh),),
        ).fetchone()
    finally:
        con.close()
    assert row and row["revoked_at"] is not None


def test_refresh_token_reuse_revokes_family(unauthed_client):
    _create_user_directly(unauthed_client, "eve@example.com", "long_pass_phrase_e", role="viewer")
    login_resp = _login(unauthed_client, "eve@example.com", "long_pass_phrase_e")
    assert login_resp.status_code == 200
    first_refresh = login_resp.cookies.get("refresh_token")

    # Rotate once — get new refresh
    r1 = unauthed_client.post("/auth/refresh", cookies={"refresh_token": first_refresh})
    assert r1.status_code == 200
    second_refresh = r1.cookies.get("refresh_token")

    # Now reuse the FIRST (already-rotated) refresh — theft signal
    r2 = unauthed_client.post("/auth/refresh", cookies={"refresh_token": first_refresh})
    assert r2.status_code == 401
    assert "reused" in r2.json()["detail"].lower()

    # The valid second_refresh should ALSO have been revoked (family kill).
    from auth.tokens import _hash_refresh
    from db.connection import get_db

    con = get_db()
    try:
        row = con.execute(
            "SELECT revoked_at FROM refresh_tokens WHERE token_hash = ?",
            (_hash_refresh(second_refresh),),
        ).fetchone()
    finally:
        con.close()
    assert row and row["revoked_at"] is not None, (
        "family revocation should have killed the live refresh too"
    )


def test_rate_limit_returns_429_after_threshold(unauthed_client, monkeypatch):
    """5 failures + 1 more → 429."""
    monkeypatch.setenv("AUTH_LOGIN_MAX_PER_IP", "5")
    monkeypatch.setenv("AUTH_LOGIN_WINDOW_MINUTES", "15")

    # Fail 5 times against a non-existent email
    for i in range(5):
        r = _login(unauthed_client, "ghost@example.com", f"wrong{i}")
        assert r.status_code == 401

    # 6th attempt should be rate-limited
    r6 = _login(unauthed_client, "ghost@example.com", "wrong6")
    assert r6.status_code == 429
    assert "Retry-After" in r6.headers


def test_csrf_post_without_header_returns_403(unauthed_client):
    """A POST to a non-exempt endpoint without X-CSRF-Token → 403.

    /auth/logout is not exempt and requires CSRF.
    """
    # Login first to get cookies
    _create_user_directly(unauthed_client, "frank@example.com", "long_pass_phrase_f", role="viewer")
    login_resp = _login(unauthed_client, "frank@example.com", "long_pass_phrase_f")
    assert login_resp.status_code == 200

    # POST /auth/logout WITHOUT X-CSRF-Token
    cookies = {
        "access_token": login_resp.cookies.get("access_token"),
        "refresh_token": login_resp.cookies.get("refresh_token"),
        "csrf_token": login_resp.cookies.get("csrf_token"),
    }
    resp = unauthed_client.post("/auth/logout", cookies=cookies)
    assert resp.status_code == 403
    assert "csrf" in resp.json()["detail"].lower()

    # Now WITH the header — should succeed
    resp_ok = unauthed_client.post(
        "/auth/logout",
        cookies=cookies,
        headers={"X-CSRF-Token": cookies["csrf_token"]},
    )
    assert resp_ok.status_code == 200


def test_role_admin_endpoint_returns_403_for_viewer(viewer_client):
    """A viewer hitting an admin endpoint → 403 from require_role."""
    # POST /scan is admin-only. The viewer fixture sets bypass=viewer so
    # CSRF is also bypassed, leaving role-gating as the only gate.
    resp = viewer_client.post("/scan")
    assert resp.status_code == 403
    assert "admin" in resp.json()["detail"].lower()


def test_logout_revokes_refresh(unauthed_client):
    _create_user_directly(unauthed_client, "gina@example.com", "long_pass_phrase_g", role="viewer")
    login_resp = _login(unauthed_client, "gina@example.com", "long_pass_phrase_g")
    refresh = login_resp.cookies.get("refresh_token")

    cookies = dict(login_resp.cookies)
    resp = unauthed_client.post(
        "/auth/logout",
        cookies=cookies,
        headers={"X-CSRF-Token": cookies["csrf_token"]},
    )
    assert resp.status_code == 200

    # Refresh hash should be revoked
    from auth.tokens import _hash_refresh
    from db.connection import get_db

    con = get_db()
    try:
        row = con.execute(
            "SELECT revoked_at FROM refresh_tokens WHERE token_hash = ?",
            (_hash_refresh(refresh),),
        ).fetchone()
    finally:
        con.close()
    assert row and row["revoked_at"] is not None


def test_change_password_requires_current_password(unauthed_client):
    _create_user_directly(unauthed_client, "henry@example.com", "old_long_password_x", role="viewer")
    login_resp = _login(unauthed_client, "henry@example.com", "old_long_password_x")
    cookies = dict(login_resp.cookies)
    headers = {"X-CSRF-Token": cookies["csrf_token"]}

    # Wrong current password
    bad = unauthed_client.post(
        "/auth/change-password",
        cookies=cookies,
        headers=headers,
        json={
            "current_password": "wrong_current_pwd",
            "new_password": "new_long_password_y",
        },
    )
    assert bad.status_code == 400
    assert "current password" in bad.json()["detail"].lower()

    # Correct current password
    ok = unauthed_client.post(
        "/auth/change-password",
        cookies=cookies,
        headers=headers,
        json={
            "current_password": "old_long_password_x",
            "new_password": "new_long_password_y",
        },
    )
    assert ok.status_code == 200

    # Old password no longer works
    r_old = _login(unauthed_client, "henry@example.com", "old_long_password_x")
    assert r_old.status_code == 401

    # New password works
    r_new = _login(unauthed_client, "henry@example.com", "new_long_password_y")
    assert r_new.status_code == 200


def test_boot_fails_without_jwt_secret(monkeypatch):
    """auth.tokens._jwt_secret() must raise without AUTH_JWT_SECRET set."""
    monkeypatch.delenv("AUTH_JWT_SECRET", raising=False)
    # Reset the cached boot-check flag so we re-validate the env var.
    import auth.tokens as t
    monkeypatch.setattr(t, "_BOOT_CHECKED", False)
    with pytest.raises(RuntimeError, match="AUTH_JWT_SECRET"):
        t._jwt_secret()


def test_audit_event_for_failed_login(unauthed_client):
    """A failed login is recorded in auth_events with success=0 and no token leak."""
    _create_user_directly(unauthed_client, "ivan@example.com", "long_pass_phrase_i", role="viewer")
    _login(unauthed_client, "ivan@example.com", "wrong_password_long")

    from db.connection import get_db

    con = get_db()
    try:
        rows = con.execute(
            "SELECT event_type, success, metadata_json FROM auth_events "
            "ORDER BY id DESC LIMIT 5"
        ).fetchall()
    finally:
        con.close()
    assert any(
        r["event_type"] == "login_failed" and r["success"] == 0 for r in rows
    ), f"expected login_failed event in {[dict(r) for r in rows]}"

    # The actual user password must NEVER appear in the metadata blob.
    # The literal word "password" is allowed (e.g. reason="wrong_password"),
    # but the user's real password value is not.
    for r in rows:
        meta = r["metadata_json"] or "{}"
        assert "long_pass_phrase_i" not in meta
        assert "wrong_password_long" not in meta


def test_audit_failure_does_not_break_login(unauthed_client, monkeypatch, capsys):
    """The auth.audit module is failure-tolerant — even if the DB INSERT
    raises, log_auth_event must NOT propagate the error.

    We simulate this by patching get_db inside auth.audit so its connection
    blows up. Login still has to return 200 and the error must surface on
    stderr (so an operator can later notice the audit gap).
    """
    _create_user_directly(unauthed_client, "jane@example.com", "long_pass_phrase_j", role="viewer")

    def _broken_get_db():
        raise RuntimeError("simulated audit DB failure")

    import auth.audit as audit_mod
    monkeypatch.setattr(audit_mod, "get_db", _broken_get_db)

    resp = _login(unauthed_client, "jane@example.com", "long_pass_phrase_j")
    assert resp.status_code == 200, (
        f"login broke when audit DB failed: {resp.status_code} {resp.text}"
    )

    # The audit failure must have been logged to stderr.
    captured = capsys.readouterr()
    assert "auth.audit" in captured.err
    assert "simulated audit DB failure" in captured.err
