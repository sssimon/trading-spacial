"""First-time setup token — in-memory only, single process lifetime.

Spec rules:
- Token is generated at app boot if no users exist AND setup not marked.
- Stored in a module-level variable; never persisted.
- Process restart → new token (the previous one is dead).
- Cleared after successful POST /setup.

The module also exposes the password policy used by both the web and CLI
setup paths so they validate identically.
"""
from __future__ import annotations

import re
import secrets
from threading import Lock
from typing import Optional

# ── In-memory token (process-local) ─────────────────────────────────────────

_lock = Lock()
_token: Optional[str] = None


def generate_token() -> str:
    """Generate and stash a fresh setup token. Returns the plaintext.

    Called from btc_api.lifespan() only when:
      - No users exist
      - setup_completed_at not in system_state
      - AUTH_DISABLE_WEB_SETUP != "1"
      - AUTH_INITIAL_ADMIN_EMAIL/PASSWORD not both provided
    """
    global _token
    with _lock:
        _token = secrets.token_urlsafe(32)
        return _token


def get_token() -> Optional[str]:
    """Return the active token (or None)."""
    with _lock:
        return _token


def consume_token() -> None:
    """Invalidate the token. Called after successful POST /setup."""
    global _token
    with _lock:
        _token = None


def reset_for_tests() -> None:
    """Test helper. NEVER call from production code paths."""
    global _token
    with _lock:
        _token = None


def token_matches(presented: Optional[str]) -> bool:
    """Constant-time compare of a presented token against the in-memory one.

    Returns False if no token is active OR no token was presented.
    """
    import hmac

    with _lock:
        active = _token
    if not active or not presented:
        return False
    return hmac.compare_digest(active, presented)


# ── Password policy (shared by /setup and the CLI) ─────────────────────────

_HAS_LETTER = re.compile(r"[A-Za-z]")
_HAS_DIGIT = re.compile(r"[0-9]")


def validate_setup_password(plain: str) -> tuple[bool, str]:
    """Spec rules: ≥12 chars, ≥1 letter, ≥1 digit, ≤72 bytes (bcrypt limit).

    Returns (ok, error_message_if_not_ok).
    """
    if not isinstance(plain, str):
        return False, "password must be a string"
    if len(plain) < 12:
        return False, "password must be at least 12 characters"
    if len(plain.encode("utf-8")) > 72:
        return False, "password must be ≤ 72 bytes (bcrypt limit)"
    if not _HAS_LETTER.search(plain):
        return False, "password must contain at least one letter"
    if not _HAS_DIGIT.search(plain):
        return False, "password must contain at least one digit"
    if not plain.strip():
        return False, "password cannot be whitespace-only"
    return True, ""
