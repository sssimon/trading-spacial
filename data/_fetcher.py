"""Fetcher: orchestrates providers with failover, dedup, rate limiting."""
import threading
import time
import logging

from data.providers.base import (
    ProviderAdapter, ProviderError, ProviderInvalidSymbol,
    ProviderRateLimited, ProviderTemporaryError, AllProvidersFailedError, Bar,
)
from data.providers.binance import BinanceAdapter
from data.providers.bybit import BybitAdapter
from data import metrics, _storage
from data.timeframes import delta_ms, last_closed_bar_time


log = logging.getLogger("data.market")


# ─── Provider registry ──────────────────────────────────────────────────────
_PROVIDERS: list[ProviderAdapter] = [BinanceAdapter(), BybitAdapter()]


# ─── Failover state (module-level, guarded) ─────────────────────────────────
_state_lock = threading.Lock()
_active_idx: int = 0
_consecutive_failures: int = 0
_last_probe_ms: int = 0

FAILOVER_THRESHOLD = 3
RECOVERY_PROBE_INTERVAL_MS = 300_000   # 5 minutes


# ─── Per-(symbol, timeframe) lock registry ──────────────────────────────────
_fetch_locks: dict[tuple[str, str], threading.Lock] = {}
_registry_guard = threading.Lock()


def _get_or_create_lock(symbol: str, timeframe: str) -> threading.Lock:
    """Return per-(symbol, timeframe) lock for in-process fetch dedup."""
    key = (symbol, timeframe)
    with _registry_guard:
        return _fetch_locks.setdefault(key, threading.Lock())


# ─── Rate limiter (minimal token bucket; compatible with existing project API) ──
class _RateLimiter:
    """Per-key token bucket. If the existing project rate limiter is available,
    substitute it here. This simple version refills tokens proportionally by
    elapsed time and blocks with a short sleep when empty."""

    def __init__(self):
        self._lock = threading.Lock()
        self._tokens: dict[str, float] = {}
        self._last_refill: dict[str, float] = {}

    def acquire(self, key: str, limit_per_min: int) -> None:
        while True:
            with self._lock:
                now = time.time()
                refill_rate = limit_per_min / 60.0  # tokens per second
                last = self._last_refill.get(key, now)
                self._tokens[key] = min(
                    limit_per_min,
                    self._tokens.get(key, limit_per_min) + (now - last) * refill_rate,
                )
                self._last_refill[key] = now
                if self._tokens[key] >= 1.0:
                    self._tokens[key] -= 1.0
                    return
                deficit = 1.0 - self._tokens[key]
                sleep_for = deficit / refill_rate
            time.sleep(min(sleep_for, 1.0))


_rate_limiter = _RateLimiter()
