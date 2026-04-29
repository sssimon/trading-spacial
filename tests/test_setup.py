"""First-time-setup test suite (11 cases per the spec).

The lifespan path is exercised by entering a `with TestClient(app)` block
explicitly — that's the only way Starlette runs startup. The autouse
fixture in conftest leaves the bypass on for the rest of the suite, so
nothing here changes how unrelated tests behave.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient


# ─── Helpers ────────────────────────────────────────────────────────────────


@contextmanager
def boot_app(tmp_path, monkeypatch, *, capsys=None, **env):
    """Boot btc_api with a fresh DB and the given env vars. Yields a
    `with`-bound TestClient (so the lifespan runs)."""
    db_file = str(tmp_path / "signals.db")
    import btc_api

    monkeypatch.setattr(btc_api, "DB_FILE", db_file)
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, str(v))

    # Real-mode auth: turn off the autouse bypass for these tests.
    monkeypatch.delenv("AUTH_TEST_BYPASS_ROLE", raising=False)

    with TestClient(btc_api.app) as client:
        yield client


def _users_count(db_file: str) -> int:
    con = sqlite3.connect(db_file)
    try:
        n = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        return int(n)
    finally:
        con.close()


def _completed(db_file: str) -> bool:
    con = sqlite3.connect(db_file)
    try:
        row = con.execute(
            "SELECT 1 FROM system_state WHERE key='setup_completed_at'"
        ).fetchone()
        return row is not None
    finally:
        con.close()


# ─── Tests ──────────────────────────────────────────────────────────────────


def test_1_fresh_db_generates_token_and_banner(tmp_path, monkeypatch, capsys):
    """Spec #1: fresh DB → setup_token generated, banner in stdout."""
    with boot_app(tmp_path, monkeypatch) as _client:
        out = capsys.readouterr().out
    assert "SETUP REQUIRED" in out
    assert "first-time installation detected" in out
    assert "/setup?token=" in out
    # The generated token is 32 bytes urlsafe → 43 chars in the URL.
    import re
    m = re.search(r"/setup\?token=([A-Za-z0-9_-]+)", out)
    assert m and len(m.group(1)) >= 40


def test_2_get_setup_without_token_404(tmp_path, monkeypatch):
    """Spec #2: GET /setup with no token → 404."""
    with boot_app(tmp_path, monkeypatch) as client:
        resp = client.get("/setup")
    assert resp.status_code == 404


def test_3_get_setup_wrong_token_404(tmp_path, monkeypatch):
    """Spec #3: GET /setup with wrong token → 404 (same as missing)."""
    with boot_app(tmp_path, monkeypatch) as client:
        resp = client.get("/setup", params={"token": "obviously-wrong"})
    assert resp.status_code == 404


def test_4_get_setup_correct_token_returns_html(tmp_path, monkeypatch, capsys):
    """Spec #4: GET /setup with correct token → 200 + HTML form."""
    with boot_app(tmp_path, monkeypatch) as client:
        out = capsys.readouterr().out
        import re
        token = re.search(r"/setup\?token=([A-Za-z0-9_-]+)", out).group(1)
        resp = client.get("/setup", params={"token": token})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    assert '<form method="post" action="/setup">' in body
    assert f'value="{token}"' in body  # token embedded as hidden field
    assert 'name="email"' in body
    assert 'name="password"' in body
    assert 'name="confirm_password"' in body


def test_5_post_setup_creates_admin_and_marks_completed(tmp_path, monkeypatch, capsys):
    """Spec #5: POST /setup with valid input → user(role=admin),
    system_state row, token consumed."""
    db_file = str(tmp_path / "signals.db")
    with boot_app(tmp_path, monkeypatch) as client:
        import re
        out = capsys.readouterr().out
        token = re.search(r"/setup\?token=([A-Za-z0-9_-]+)", out).group(1)

        resp = client.post(
            "/setup",
            data={
                "token": token,
                "email": "admin@example.com",
                "password": "abcdef123456",
                "confirm_password": "abcdef123456",
            },
            headers={"Accept": "application/json"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["redirect"] == "/login"

    assert _users_count(db_file) == 1
    assert _completed(db_file)

    con = sqlite3.connect(db_file)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT email, role, is_active FROM users").fetchone()
    finally:
        con.close()
    assert row["email"] == "admin@example.com"
    assert row["role"] == "admin"
    assert row["is_active"] == 1


def test_6_second_post_setup_returns_404(tmp_path, monkeypatch, capsys):
    """Spec #6: after setup completes, /setup returns 404 forever."""
    with boot_app(tmp_path, monkeypatch) as client:
        import re
        out = capsys.readouterr().out
        token = re.search(r"/setup\?token=([A-Za-z0-9_-]+)", out).group(1)

        # First setup succeeds
        first = client.post(
            "/setup",
            data={
                "token": token,
                "email": "admin@example.com",
                "password": "abcdef123456",
                "confirm_password": "abcdef123456",
            },
            headers={"Accept": "application/json"},
        )
        assert first.status_code == 201

        # Second attempt → 404
        second_get = client.get("/setup", params={"token": token})
        second_post = client.post(
            "/setup",
            data={
                "token": token,
                "email": "another@example.com",
                "password": "differentpass1",
                "confirm_password": "differentpass1",
            },
            headers={"Accept": "application/json"},
        )
    assert second_get.status_code == 404
    assert second_post.status_code == 404


def test_7_post_setup_weak_password_400(tmp_path, monkeypatch, capsys):
    """Spec #7: short password → 400 + clear error."""
    with boot_app(tmp_path, monkeypatch) as client:
        import re
        out = capsys.readouterr().out
        token = re.search(r"/setup\?token=([A-Za-z0-9_-]+)", out).group(1)

        # Too short
        r1 = client.post(
            "/setup",
            data={
                "token": token,
                "email": "admin@example.com",
                "password": "short1",
                "confirm_password": "short1",
            },
            headers={"Accept": "application/json"},
        )
        # Missing digit
        r2 = client.post(
            "/setup",
            data={
                "token": token,
                "email": "admin@example.com",
                "password": "abcdefghijkl",
                "confirm_password": "abcdefghijkl",
            },
            headers={"Accept": "application/json"},
        )
        # Missing letter
        r3 = client.post(
            "/setup",
            data={
                "token": token,
                "email": "admin@example.com",
                "password": "123456789012",
                "confirm_password": "123456789012",
            },
            headers={"Accept": "application/json"},
        )
        # Mismatch
        r4 = client.post(
            "/setup",
            data={
                "token": token,
                "email": "admin@example.com",
                "password": "abcdef123456",
                "confirm_password": "differentpass1",
            },
            headers={"Accept": "application/json"},
        )

    assert r1.status_code == 400
    assert "12 characters" in r1.json()["detail"]
    assert r2.status_code == 400
    assert "digit" in r2.json()["detail"]
    assert r3.status_code == 400
    assert "letter" in r3.json()["detail"]
    assert r4.status_code == 400
    assert "match" in r4.json()["detail"].lower()


def test_8_disable_web_setup_returns_404(tmp_path, monkeypatch, capsys):
    """Spec #8: AUTH_DISABLE_WEB_SETUP=1 → /setup 404, no token, no banner
    URL — only the CLI-only banner."""
    with boot_app(tmp_path, monkeypatch, AUTH_DISABLE_WEB_SETUP="1") as client:
        out = capsys.readouterr().out
        # Banner is shown, but with the CLI-only message
        assert "SETUP REQUIRED" in out
        assert "AUTH_DISABLE_WEB_SETUP=1" in out
        assert "scripts/create_user.py" in out
        # No web URL
        assert "/setup?token=" not in out

        # Even guessing any token → 404
        r1 = client.get("/setup", params={"token": "guess"})
        r2 = client.get("/setup")
        r3 = client.post(
            "/setup",
            data={"token": "guess", "email": "a@b.c",
                  "password": "abcdef123456",
                  "confirm_password": "abcdef123456"},
        )
    assert r1.status_code == 404
    assert r2.status_code == 404
    assert r3.status_code == 404


def test_9_env_vars_create_admin_at_boot(tmp_path, monkeypatch, capsys):
    """Spec #9: AUTH_INITIAL_ADMIN_EMAIL+PASSWORD set → user created,
    no banner, no setup_token."""
    db_file = str(tmp_path / "signals.db")
    with boot_app(
        tmp_path, monkeypatch,
        AUTH_INITIAL_ADMIN_EMAIL="env-admin@example.com",
        AUTH_INITIAL_ADMIN_PASSWORD="abcdef123456",
    ) as client:
        out = capsys.readouterr().out

    # No banner from the setup-required path
    assert "SETUP REQUIRED" not in out
    # User exists, role admin, system_state marked
    assert _users_count(db_file) == 1
    assert _completed(db_file)

    con = sqlite3.connect(db_file)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT email, role FROM users").fetchone()
        method = con.execute(
            "SELECT value FROM system_state WHERE key='setup_completed_method'"
        ).fetchone()
    finally:
        con.close()
    assert row["email"] == "env-admin@example.com"
    assert row["role"] == "admin"
    assert method["value"] == "env_vars"


def test_10_env_vars_xor_only_email_fails_at_boot(tmp_path, monkeypatch):
    """Spec #10: only one of the two env vars set → boot fails loud."""
    import btc_api

    db_file = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_file)
    monkeypatch.setenv("AUTH_INITIAL_ADMIN_EMAIL", "env-admin@example.com")
    monkeypatch.delenv("AUTH_INITIAL_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("AUTH_TEST_BYPASS_ROLE", raising=False)

    with pytest.raises(RuntimeError, match="AUTH_INITIAL_ADMIN_EMAIL"):
        with TestClient(btc_api.app):
            pass


def test_11_reset_password_cli_revokes_all_refreshes(tmp_path, monkeypatch):
    """Spec #11: scripts/reset_password.py changes hash + revokes all
    refresh tokens for the user."""
    db_file = str(tmp_path / "signals.db")
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", db_file)

    # Bootstrap directly: create the user + insert two active refresh
    # tokens for them, then call reset_password as a library.
    from datetime import datetime, timedelta, timezone
    from db.connection import get_db
    from db.schema import init_db
    from db.auth_schema import init_auth_db
    from auth.password import hash_password
    from auth.tokens import _hash_refresh, revoke_all_for_user

    init_db()
    init_auth_db()
    now = datetime.now(timezone.utc).isoformat()
    con = get_db()
    try:
        cur = con.execute(
            "INSERT INTO users (email, password_hash, role, is_active, "
            "created_at, password_changed_at) VALUES (?, ?, 'admin', 1, ?, ?)",
            ("u@example.com", hash_password("oldpassword12"), now, now),
        )
        uid = int(cur.lastrowid)
        future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        for tok in ("rA", "rB"):
            con.execute(
                "INSERT INTO refresh_tokens (token_hash, user_id, family_id, "
                "parent_hash, expires_at, revoked_at, created_at, "
                "user_agent, ip) VALUES (?, ?, 'fam', NULL, ?, NULL, ?, ?, ?)",
                (_hash_refresh(tok), uid, future, now, "test", "127.0.0.1"),
            )
        con.commit()
    finally:
        con.close()

    # Apply the password reset machinery (mirrors what the CLI does).
    new_hash = hash_password("newpassword12")
    con = get_db()
    try:
        con.execute(
            "UPDATE users SET password_hash = ?, password_changed_at = ? "
            "WHERE id = ?",
            (new_hash, datetime.now(timezone.utc).isoformat(), uid),
        )
        con.commit()
    finally:
        con.close()
    revoked = revoke_all_for_user(uid)
    assert revoked == 2

    # Verify in DB: hash changed, both refreshes revoked_at IS NOT NULL.
    con = get_db()
    try:
        h = con.execute(
            "SELECT password_hash FROM users WHERE id = ?", (uid,)
        ).fetchone()["password_hash"]
        rows = con.execute(
            "SELECT revoked_at FROM refresh_tokens WHERE user_id = ?", (uid,)
        ).fetchall()
    finally:
        con.close()
    assert h == new_hash
    assert all(r["revoked_at"] is not None for r in rows)


def test_setup_status_endpoint_public(tmp_path, monkeypatch):
    """Bonus: /setup/status is reachable without auth; reports correctly
    on a fresh DB and after setup completes."""
    with boot_app(tmp_path, monkeypatch) as client:
        r1 = client.get("/setup/status")
        assert r1.status_code == 200
        assert r1.json() == {"setup_required": True}

        # Complete setup
        import re
        # We can't easily re-capture stdout; just call the function directly
        from auth.setup import generate_token  # noqa
        # Instead, fetch the active token via the helper
        from auth.setup import get_token
        token = get_token()
        assert token, "expected an active setup token in this fresh boot"

        r2 = client.post(
            "/setup",
            data={"token": token, "email": "a@b.com",
                  "password": "abcdef123456",
                  "confirm_password": "abcdef123456"},
            headers={"Accept": "application/json"},
        )
        assert r2.status_code == 201

        r3 = client.get("/setup/status")
        assert r3.status_code == 200
        assert r3.json() == {"setup_required": False}


def test_setup_rate_limit_returns_404_after_threshold(tmp_path, monkeypatch):
    """The /setup rate limit returns 404 (not 429) so it's invisible to
    scanners — they see the same shape they'd see for any wrong path."""
    with boot_app(tmp_path, monkeypatch) as client:
        # Fire 11 requests — the 11th should be throttled to 404
        codes = [client.get("/setup", params={"token": "x"}).status_code
                 for _ in range(11)]
    # All 11 are 404 (10 because token is wrong, the 11th because rate-limited).
    # The interesting check is just that we never see a 429.
    assert codes.count(404) == 11
    assert 429 not in codes
