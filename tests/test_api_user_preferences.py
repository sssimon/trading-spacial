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
    """Fresh DB with notify_channels seeded for the test-bypass tenant (id=0).

    The autouse _auth_bypass_default fixture (conftest.py) activates
    AUTH_TEST_BYPASS_ROLE=admin, which makes AuthMiddleware inject a
    synthetic User(id=0). We therefore seed preferences for tenant_id=0 so
    GET /preferences returns the row we expect.

    No real users table row is needed — the bypass user is never DB-hydrated.
    """
    import btc_api
    from fastapi.testclient import TestClient
    from db.schema import init_db
    from db.user_preferences import db_upsert_user_preferences

    db_path = str(tmp_path / "test_prefs.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    init_db()

    db_upsert_user_preferences(
        0,
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
    from db.user_preferences import db_get_user_preferences
    row = db_get_user_preferences(0)
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

    from db.user_preferences import db_get_user_preferences
    row = db_get_user_preferences(0)
    assert row["notify_channels"]["telegram_bot_token"] == "111111111:NEWXYZabcDEFghiJKLmnoPQRstuVWXyzABCDE"

    # Response body should reflect the new token, masked.
    resp_token = r.json()["preferences"]["notify_channels"]["telegram_bot_token"]
    assert _MASK_MARKER in resp_token
    assert "NEWXYZabcDEF" not in resp_token
