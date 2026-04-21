"""Thread-safe in-memory token bucket.

Shared across the process. For multi-process workers a DB-backed
alternative would be needed, but the scanner runs single-process today."""
from __future__ import annotations

import threading
import time


class TokenBucket:
    def __init__(self, capacity: int, refill_per_sec: float):
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, n: float = 1.0) -> bool:
        """Try to acquire n tokens. Returns True if granted, False if not."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_sec)
            self._last_refill = now
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False


# Module-level registry: one bucket per channel name.
_buckets: dict[str, TokenBucket] = {}
_registry_lock = threading.Lock()


def bucket_for(channel_name: str, capacity: int = 20, refill_per_sec: float | None = None) -> TokenBucket:
    """Get-or-create the token bucket for a channel.

    Default: capacity=20, refill_per_sec=capacity/60 (i.e. 20 req/min steady state)."""
    refill = refill_per_sec if refill_per_sec is not None else capacity / 60.0
    with _registry_lock:
        if channel_name not in _buckets:
            _buckets[channel_name] = TokenBucket(capacity, refill)
        return _buckets[channel_name]


def reset_all_for_tests() -> None:
    """Test helper. Clears the registry so each test starts fresh."""
    with _registry_lock:
        _buckets.clear()
