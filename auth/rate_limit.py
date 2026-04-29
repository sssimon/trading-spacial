"""In-memory rate limiter for /auth/login.

Design choices (per spec):
- 5 attempts / IP / 15 min, 10 attempts / email / 15 min (configurable via env).
- Failed attempts only count. Successes reset the counters for that IP+email.
- Cleanup is opportunistic: each call prunes entries older than the window.
- 2-5 users → no need for Redis; a dict + threading.Lock is enough.
- Process restart resets state. Acceptable: a brute-forcer hitting reboot
  windows still hits the limiter on the next attempt (rate from network is
  the real bottleneck, not memory).

Returns:
- check_login_allowed(ip, email) → (allowed: bool, retry_after_seconds: int|None)
- record_login_failure(ip, email) → None
- record_login_success(ip, email) → None  (resets counters for this pair)
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple


def _max_per_ip() -> int:
    return int(os.environ.get("AUTH_LOGIN_MAX_PER_IP", "5"))


def _max_per_email() -> int:
    return int(os.environ.get("AUTH_LOGIN_MAX_PER_EMAIL", "10"))


def _window_seconds() -> int:
    return int(os.environ.get("AUTH_LOGIN_WINDOW_MINUTES", "15")) * 60


_lock = threading.Lock()
# Per-key: deque of timestamps (monotonic float seconds).
_ip_attempts: Dict[str, Deque[float]] = {}
_email_attempts: Dict[str, Deque[float]] = {}


def _prune(deque_obj: Deque[float], cutoff: float) -> None:
    while deque_obj and deque_obj[0] < cutoff:
        deque_obj.popleft()


def _now() -> float:
    # time.monotonic() is immune to system clock changes — correct for rate
    # limiting. We never serialise these timestamps to disk.
    return time.monotonic()


def check_login_allowed(
    ip: Optional[str], email: Optional[str]
) -> Tuple[bool, Optional[int]]:
    """Return (allowed, retry_after_seconds).

    None ip/email is treated permissively (no key to track) — this is
    test-friendly. Real requests always have an IP.
    """
    now = _now()
    cutoff = now - _window_seconds()
    max_ip = _max_per_ip()
    max_email = _max_per_email()

    with _lock:
        oldest = None
        if ip:
            d = _ip_attempts.setdefault(ip, deque())
            _prune(d, cutoff)
            if len(d) >= max_ip:
                oldest = d[0]
        if email:
            e = email.strip().lower()
            d = _email_attempts.setdefault(e, deque())
            _prune(d, cutoff)
            if len(d) >= max_email:
                oldest_e = d[0]
                oldest = oldest_e if oldest is None else min(oldest, oldest_e)

        if oldest is not None:
            retry = max(1, int((oldest + _window_seconds()) - now))
            return False, retry
        return True, None


def record_login_failure(ip: Optional[str], email: Optional[str]) -> None:
    """Append a timestamp to the per-IP and per-email deques."""
    now = _now()
    with _lock:
        if ip:
            _ip_attempts.setdefault(ip, deque()).append(now)
        if email:
            _email_attempts.setdefault(email.strip().lower(), deque()).append(now)


def record_login_success(ip: Optional[str], email: Optional[str]) -> None:
    """Reset counters for this IP and this email on a successful login.

    A genuine user might fail a few times before remembering the right
    password. Once they succeed, we forgive prior failures so they don't
    trip the limit on the next legitimate logout/login cycle.
    """
    with _lock:
        if ip and ip in _ip_attempts:
            _ip_attempts[ip].clear()
        if email:
            e = email.strip().lower()
            if e in _email_attempts:
                _email_attempts[e].clear()


def reset_all_for_tests() -> None:
    """Test helper. NEVER call from production code paths."""
    with _lock:
        _ip_attempts.clear()
        _email_attempts.clear()
        _setup_ip_attempts.clear()


# ─── /setup rate limit (added with first-time setup) ────────────────────────
#
# Independent bucket from login. Spec: 10 per IP per hour. Not for security
# (token entropy already covers that) but to suppress timing-scan probing
# of /setup and /setup/status. Both endpoints share the bucket.

_SETUP_WINDOW_SECONDS = 60 * 60  # 1 hour
_SETUP_MAX_PER_IP = 10
_setup_ip_attempts: Dict[str, Deque[float]] = {}


def check_setup_allowed(ip: Optional[str]) -> Tuple[bool, Optional[int]]:
    """Same shape as check_login_allowed: returns (allowed, retry_after_s)."""
    now = _now()
    cutoff = now - _SETUP_WINDOW_SECONDS

    with _lock:
        if not ip:
            return True, None
        d = _setup_ip_attempts.setdefault(ip, deque())
        _prune(d, cutoff)
        if len(d) >= _SETUP_MAX_PER_IP:
            retry = max(1, int((d[0] + _SETUP_WINDOW_SECONDS) - now))
            return False, retry
        d.append(now)
        return True, None
