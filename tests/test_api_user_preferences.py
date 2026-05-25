"""Tests for api/user_preferences.py — masking + /preferences/test endpoint.

Covers:
- _mask_token helper invariants.
- GET /api/preferences masks telegram_bot_token in response.
- PUT /api/preferences preserves masked token, replaces unmasked.
- POST /api/preferences/test: no-creds early-return, happy path, dedup-bypass,
  no-write-to-notifications_sent, isolated per tenant.
"""
from __future__ import annotations

import pytest

from api.user_preferences import _MASK_MARKER


# ── _mask_token helper ──────────────────────────────────────────────


def test_mask_token_real_telegram_token():
    """Real Telegram tokens are ~46 chars (<bot_id>:<35-char-secret>).
    Mask should preserve first 10 + last 4 chars with **** between."""
    from api.user_preferences import _mask_token
    token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz_aBcDeFgH12"
    assert _mask_token(token) == "123456789:****gH12"


def test_mask_token_empty_returns_empty():
    """Empty input → empty output (no masking artifacts on empty)."""
    from api.user_preferences import _mask_token
    assert _mask_token("") == ""


def test_mask_token_short_input_returns_empty():
    """Inputs shorter than 10 chars are treated as garbage and return ""
    (defensive: real tokens are always ≥10 chars; this guard prevents
    leaking partial credentials via mask='****X' for X<10 chars)."""
    from api.user_preferences import _mask_token
    assert _mask_token("123") == ""
    assert _mask_token("123456789") == ""  # exactly 9, still under threshold


# ── GET /api/preferences masking ────────────────────────────────────


@pytest.fixture
def seeded_user_with_telegram(tmp_path, monkeypatch):
    """Fresh DB with notify_channels seeded for the test-bypass tenant (id=99).

    The autouse _auth_bypass_default fixture (conftest.py) activates
    AUTH_TEST_BYPASS_ROLE=admin, which makes AuthMiddleware inject a
    synthetic User(id=99). We therefore seed preferences for tenant_id=99 so
    GET /preferences returns the row we expect. (Bumped from 0 → 99 by
    #446 Task 6 fix — PositionClosure USER mode requires
    caller_tenant_id > 0.)

    No real users table row is needed — the bypass user is never DB-hydrated.
    """
    import btc_api
    from fastapi.testclient import TestClient
    from db.schema import init_db
    from db.transaction import transaction
    from db.user_preferences import db_upsert_user_preferences

    db_path = str(tmp_path / "test_prefs.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    init_db()

    with transaction() as con:
        db_upsert_user_preferences(
            con,
            99,
            notify_channels={
                "telegram_bot_token": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz_aBcDeFgH12",
                "telegram_chat_id":   "987654321",
            },
        )

    return TestClient(btc_api.app)


def test_get_preferences_masks_telegram_bot_token(seeded_user_with_telegram):
    """GET response should return masked token, not plain — defense against
    XSS exfiltration of credentials."""
    client = seeded_user_with_telegram
    r = client.get("/preferences")
    assert r.status_code == 200
    data = r.json()
    masked = data["notify_channels"]["telegram_bot_token"]
    assert masked == "123456789:****gH12"
    assert "ABCdefGHI" not in masked, "secret portion should not leak"


def test_get_preferences_chat_id_not_masked(seeded_user_with_telegram):
    """chat_id is not secret (visible in any getUpdates response); pass through."""
    client = seeded_user_with_telegram
    r = client.get("/preferences")
    assert r.json()["notify_channels"]["telegram_chat_id"] == "987654321"


# ── PUT /api/preferences preserves masked token ──────────────────────


def test_put_preserves_token_when_value_contains_mask_marker(seeded_user_with_telegram):
    """If user submits the masked value (didn't retype), DB token stays unchanged."""
    client = seeded_user_with_telegram
    masked = "123456789:****gH12"
    r = client.put("/preferences", json={
        "notify_channels": {
            "telegram_bot_token": masked,
            "telegram_chat_id":   "111222333",  # changed
        },
    })
    assert r.status_code == 200

    # Re-fetch raw from DB (bypass masking) to verify token preserved
    from db.transaction import transaction
    from db.user_preferences import db_get_user_preferences
    with transaction() as con:
        row = db_get_user_preferences(con, 99)
    nc = row["notify_channels"]
    assert nc["telegram_bot_token"] == "123456789:ABCdefGHIjklMNOpqrsTUVwxyz_aBcDeFgH12"
    assert nc["telegram_chat_id"] == "111222333"

    # Response body should also have masked token (consistency with GET).
    resp_token = r.json()["preferences"]["notify_channels"]["telegram_bot_token"]
    assert _MASK_MARKER in resp_token
    assert "ABCdefGHI" not in resp_token  # secret portion must not leak in response


def test_put_replaces_token_when_value_unmasked(seeded_user_with_telegram):
    """If user submits a plain token (no ****), DB updates to the new value."""
    client = seeded_user_with_telegram
    r = client.put("/preferences", json={
        "notify_channels": {
            "telegram_bot_token": "111111111:NEWXYZabcDEFghiJKLmnoPQRstuVWXyzABCDE",
            "telegram_chat_id":   "987654321",
        },
    })
    assert r.status_code == 200

    from db.transaction import transaction
    from db.user_preferences import db_get_user_preferences
    with transaction() as con:
        row = db_get_user_preferences(con, 99)
    assert row["notify_channels"]["telegram_bot_token"] == "111111111:NEWXYZabcDEFghiJKLmnoPQRstuVWXyzABCDE"

    # Response body should reflect the new token, masked.
    resp_token = r.json()["preferences"]["notify_channels"]["telegram_bot_token"]
    assert _MASK_MARKER in resp_token
    assert "NEWXYZabcDEF" not in resp_token


# ── POST /api/preferences/test ──────────────────────────────────────


def test_test_endpoint_no_channels_returns_no_telegram_configured(tmp_path, monkeypatch):
    """User without notify_channels → {ok: false, reason: 'no_telegram_configured'}."""
    import btc_api
    from fastapi.testclient import TestClient
    from db.auth_schema import init_auth_db
    from db.schema import init_db
    from db.transaction import transaction

    db_path = str(tmp_path / "test_prefs_no_channels.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    init_db()
    with transaction() as con:
        init_auth_db(con)
    # No user row needed: conftest auth bypass uses synthetic User(id=99).

    client = TestClient(btc_api.app)
    r = client.post("/preferences/test")
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": False, "receipts": [], "reason": "no_telegram_configured"}


def test_test_endpoint_only_token_returns_no_telegram_configured(seeded_user_with_telegram, monkeypatch):
    """Token set but chat_id missing → still no_telegram_configured."""
    from db.transaction import transaction
    from db.user_preferences import db_upsert_user_preferences
    # Overwrite the fixture's prefs to remove chat_id
    with transaction() as con:
        db_upsert_user_preferences(con, 99, notify_channels={"telegram_bot_token": "xxx:yyy"})

    client = seeded_user_with_telegram
    r = client.post("/preferences/test")
    assert r.json()["reason"] == "no_telegram_configured"


def test_test_endpoint_with_telegram_routes_correctly(seeded_user_with_telegram, monkeypatch):
    """Happy path: token + chat_id set → TelegramChannel.send called with user_cfg.
    Mock requests.post to avoid hitting real Telegram API."""
    sent_payloads = []
    class _FakeResp:
        ok = True
        status_code = 200
        text = "ok"
    def _fake_post(url, json=None, **kw):
        sent_payloads.append({"url": url, "body": json})
        return _FakeResp()

    import requests
    monkeypatch.setattr(requests, "post", _fake_post)

    client = seeded_user_with_telegram
    r = client.post("/preferences/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["receipts"] == [{"channel": "telegram", "status": "ok", "error": None}]
    assert body["reason"] is None
    # Sent payload uses the USER's bot token + chat_id from notify_channels overlay
    assert len(sent_payloads) == 1
    assert "123456789:ABCdefGHIjklMNOpqrsTUVwxyz_aBcDeFgH12" in sent_payloads[0]["url"]
    assert sent_payloads[0]["body"]["chat_id"] == "987654321"


def test_test_endpoint_two_calls_within_window_both_succeed(seeded_user_with_telegram, monkeypatch):
    """Defense against future dedup window changes: 2 calls in quick succession
    BOTH return ok=true. Bypass of notify() guarantees this regardless of any
    future tightening of signal-type dedup defaults."""
    class _FakeResp:
        ok = True
        status_code = 200
        text = "ok"
    def _fake_post(url, **kw):
        return _FakeResp()
    import requests
    monkeypatch.setattr(requests, "post", _fake_post)

    client = seeded_user_with_telegram
    r1 = client.post("/preferences/test")
    r2 = client.post("/preferences/test")
    assert r1.json()["ok"] is True
    assert r2.json()["ok"] is True


def test_test_endpoint_does_not_write_to_notifications_sent(seeded_user_with_telegram, monkeypatch):
    """Bypass of notify() means no row written to notifications_sent
    (avoids polluting NotificationBell with test pings)."""
    class _FakeResp:
        ok = True
        status_code = 200
        text = "ok"
    def _fake_post(url, **kw):
        return _FakeResp()
    import requests
    monkeypatch.setattr(requests, "post", _fake_post)

    from db.transaction import transaction
    with transaction() as con:
        before = con.execute("SELECT COUNT(*) FROM notifications_sent").fetchone()[0]

    client = seeded_user_with_telegram
    r = client.post("/preferences/test")
    assert r.status_code == 200  # ensures the endpoint actually ran (regression guard)

    with transaction() as con:
        after = con.execute("SELECT COUNT(*) FROM notifications_sent").fetchone()[0]
    assert before == after, "POST /test should NOT write to notifications_sent"
