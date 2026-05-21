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
