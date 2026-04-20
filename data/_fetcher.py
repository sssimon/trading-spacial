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


def _maybe_probe_primary_recovery() -> None:
    """If we're on a fallback, probe primary health periodically; revert on success."""
    global _active_idx, _last_probe_ms
    with _state_lock:
        if _active_idx == 0:
            return
        now_ms = int(time.time() * 1000)
        if now_ms - _last_probe_ms < RECOVERY_PROBE_INTERVAL_MS:
            return
        _last_probe_ms = now_ms
        primary_to_probe = _PROVIDERS[0]

    healthy = False
    try:
        healthy = primary_to_probe.is_healthy()
    except Exception:
        pass
    if healthy:
        with _state_lock:
            _active_idx = 0
        metrics.inc("provider_recoveries_total", labels={"provider": primary_to_probe.name})
        log.info("Primary provider %s recovered — reverting active", primary_to_probe.name)


def fetch_with_failover(symbol: str, timeframe: str, start_ms: int, end_ms: int) -> list[Bar]:
    """Try providers in priority order (sticky). On failure thresholds, switch active."""
    global _active_idx, _consecutive_failures

    _maybe_probe_primary_recovery()

    with _state_lock:
        ordering = list(range(_active_idx, len(_PROVIDERS))) + list(range(_active_idx))
        primary_name = _PROVIDERS[ordering[0]].name

    for position, idx in enumerate(ordering):
        provider = _PROVIDERS[idx]
        try:
            _rate_limiter.acquire(provider.name, provider.rate_limit_per_min)
            t0 = time.time()
            bars = provider.fetch_klines(symbol, timeframe, start_ms, end_ms)
            latency_ms = int((time.time() - t0) * 1000)
            metrics.observe("fetch_latency_ms", latency_ms, labels={"provider": provider.name})
            metrics.inc("fetches_total", labels={"provider": provider.name, "tf": timeframe})
            if position == 0:
                # Only reset on primary success so fallback coverage still
                # accumulates consecutive primary failures across calls.
                with _state_lock:
                    _consecutive_failures = 0
            else:
                metrics.inc(
                    "fallback_fetches_total",
                    labels={"from": primary_name, "to": provider.name},
                )
            return bars
        except ProviderInvalidSymbol:
            raise
        except (ProviderRateLimited, ProviderTemporaryError) as e:
            metrics.inc(
                "provider_errors_total",
                labels={"provider": provider.name, "kind": type(e).__name__},
            )
            log.warning("%s failed (%s): %s", provider.name, type(e).__name__, e)
            if position == 0:
                with _state_lock:
                    _consecutive_failures += 1
                    if _consecutive_failures >= FAILOVER_THRESHOLD:
                        new_idx = (idx + 1) % len(_PROVIDERS)
                        metrics.inc(
                            "provider_switches_total",
                            labels={"from": provider.name, "to": _PROVIDERS[new_idx].name},
                        )
                        log.warning(
                            "Switching active provider %s → %s after %d consecutive failures",
                            provider.name, _PROVIDERS[new_idx].name, _consecutive_failures,
                        )
                        _active_idx = new_idx
                        _consecutive_failures = 0
            continue

    raise AllProvidersFailedError(
        f"All providers failed for {symbol} {timeframe} [{start_ms}, {end_ms}]"
    )


CHUNK_SIZE = 1000


def ensure_fresh(
    symbol: str, timeframe: str, limit: int,
    cached_max: int | None, expected_max: int,
) -> None:
    """Fetch incremental bars if cache is stale, using double-checked locking."""
    lock = _get_or_create_lock(symbol, timeframe)
    with lock:
        # Re-check cache inside lock — another thread may have just filled it
        new_cached_max = _storage.max_open_time(symbol, timeframe)
        new_count = _storage.count_tail(symbol, timeframe, expected_max, limit)
        if (
            new_cached_max is not None
            and new_cached_max >= expected_max
            and new_count >= limit
        ):
            metrics.inc("double_checked_hits_total")
            return

        delta = delta_ms(timeframe)
        if new_cached_max is None:
            start_ms = expected_max - (limit - 1) * delta
        else:
            start_ms = new_cached_max + delta
        end_ms = expected_max

        if start_ms > end_ms:
            return

        bars = fetch_with_failover(symbol, timeframe, start_ms, end_ms)
        if bars:
            _storage.upsert_many(bars)


def _backfill_range(symbol: str, timeframe: str, start_ms: int, end_ms: int) -> int:
    """Bulk fetch + persist of [start_ms, end_ms] inclusive in CHUNK_SIZE-bar chunks."""
    delta = delta_ms(timeframe)
    earliest = _storage.first_bar_ms(symbol, timeframe)
    if earliest is not None:
        start_ms = max(start_ms, earliest)
    if start_ms > end_ms:
        return 0

    cur = start_ms
    total = 0
    estimated = max(1, (end_ms - start_ms) // delta + 1)
    while cur <= end_ms:
        chunk_end = min(cur + (CHUNK_SIZE - 1) * delta, end_ms)
        bars = fetch_with_failover(symbol, timeframe, cur, chunk_end)
        if not bars:
            # Empty response — mark pre-listing and stop
            _storage.set_first_bar_ms(symbol, timeframe, chunk_end + delta)
            break
        persisted = _storage.upsert_many(bars)
        total += persisted
        cur = bars[-1].open_time + delta
        if total > 0 and total % 1000 == 0:
            log.info(
                "Backfill %s %s: %d/%d (%.1f%%)",
                symbol, timeframe, total, estimated, total / estimated * 100.0,
            )
    metrics.inc("backfill_bars_total", total, labels={"symbol": symbol, "tf": timeframe})
    return total


def _fill_internal_gaps(symbol: str, timeframe: str, start_ms: int, end_ms: int) -> int:
    """Detect and fill holes inside [start_ms, end_ms] inclusive."""
    delta = delta_ms(timeframe)
    existing = set(_storage.times_in_range(symbol, timeframe, start_ms, end_ms))
    total = 0
    gap_start = None
    cur = start_ms
    while cur <= end_ms:
        if cur not in existing:
            if gap_start is None:
                gap_start = cur
        else:
            if gap_start is not None:
                total += _backfill_range(symbol, timeframe, gap_start, cur - delta)
                gap_start = None
        cur += delta
    if gap_start is not None:
        total += _backfill_range(symbol, timeframe, gap_start, end_ms)
    return total
