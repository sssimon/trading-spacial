"""Password hashing + verification + constant-time dummy.

bcrypt cost factor is read from AUTH_BCRYPT_ROUNDS (default 12, spec minimum).
The dummy_verify() helper exists so that login attempts against unknown emails
take the same wall-clock time as attempts against real ones — preventing
timing-based account enumeration.
"""
from __future__ import annotations

import os

from passlib.context import CryptContext


def _rounds() -> int:
    """Return the bcrypt cost factor.

    Reads AUTH_BCRYPT_ROUNDS from env (default "12"), parses it, and clamps
    it to a minimum of 12 — the project's spec floor. The clamp is a
    hardening invariant, NOT a forgotten cleanup: even if an attacker can
    inject env vars (or someone misconfigures a deploy with `=4`), the
    bcrypt cost cannot drop below 12. To raise the cost, set the env var
    to a higher value (e.g. 14 for paranoid setups).
    """
    raw = os.environ.get("AUTH_BCRYPT_ROUNDS", "12")
    try:
        n = int(raw)
    except ValueError:
        n = 12
    return max(12, n)  # hardening floor — see docstring


_pwd_context = CryptContext(schemes=["bcrypt"], bcrypt__rounds=_rounds(), deprecated="auto")

# Lazy: dummy_verify needs a hash whose bcrypt cost matches the current
# context, otherwise the no-user code path takes very different time than
# the real-user path (e.g. cost=12 dummy vs cost=4 real-user hash in tests
# = 64× wall-clock difference). Computed on first call, cached.
_DUMMY_HASH: str | None = None


def _ensure_dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = _pwd_context.hash("dummy_password_for_timing_uniformity")
    return _DUMMY_HASH


def hash_password(plain: str) -> str:
    """Return a bcrypt hash. Caller MUST validate non-empty.

    bcrypt has a 72-byte limit; we intentionally do NOT silently truncate.
    The CLI / API layer rejects passwords > 72 bytes with a clear error.
    """
    if not isinstance(plain, str) or not plain:
        raise ValueError("password must be a non-empty string")
    if len(plain.encode("utf-8")) > 72:
        raise ValueError("password must be ≤ 72 bytes (bcrypt limit)")
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time verify. Returns False on any error (malformed hash etc)."""
    if not plain or not hashed:
        return False
    try:
        return _pwd_context.verify(plain, hashed)
    except Exception:
        return False


def dummy_verify() -> None:
    """Run a real bcrypt verify against a throwaway hash.

    Used in the login path when the email is unknown, so total wall-clock
    time matches the real-user path. The dummy hash is generated with the
    same bcrypt cost as the live context, so timing matches.
    """
    try:
        _pwd_context.verify("dummy_password_for_timing_uniformity", _ensure_dummy_hash())
    except Exception:
        pass


def password_meets_minimum(plain: str) -> tuple[bool, str]:
    """Minimal policy check. Returns (ok, error_msg_if_not_ok).

    Spec didn't pin a strength policy. We enforce: ≥12 chars, ≤72 bytes,
    not whitespace-only. Stronger policies (zxcvbn etc) can be layered later.
    """
    if not isinstance(plain, str):
        return False, "password must be a string"
    if len(plain) < 12:
        return False, "password must be at least 12 characters"
    if len(plain.encode("utf-8")) > 72:
        return False, "password must be ≤ 72 bytes (bcrypt limit)"
    if not plain.strip():
        return False, "password cannot be whitespace-only"
    return True, ""
