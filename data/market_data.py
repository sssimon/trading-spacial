"""Market Data Layer — public API.

All functions in this module are the only supported entrypoints.
Underscore-prefixed modules are private implementation.
"""
from datetime import datetime, timezone
from typing import Iterable
import logging

import pandas as pd

from data import _storage, _fetcher, metrics
from data.timeframes import TIMEFRAMES, delta_ms, last_closed_bar_time


log = logging.getLogger("data.market")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _ensure_schema_once():
    _storage.init_schema()


def get_klines(
    symbol: str,
    timeframe: str,
    limit: int,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Last `limit` CLOSED bars for (symbol, timeframe). Never includes in-progress bar.

    Serves from cache; fetches incremental bars when stale. Column schema:
    ['open_time', 'open', 'high', 'low', 'close', 'volume', 'provider', 'fetched_at'].
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe: {timeframe}")
    if limit <= 0:
        raise ValueError("limit must be positive")
    _ensure_schema_once()

    expected_max = last_closed_bar_time(timeframe, _utcnow())
    cached_max = _storage.max_open_time(symbol, timeframe)
    cached_count = _storage.count_tail(symbol, timeframe, expected_max, limit)
    sufficient = (
        cached_max is not None
        and cached_max >= expected_max
        and cached_count >= limit
    )
    if force_refresh:
        # Bypass ensure_fresh's double-checked cache-hit return.
        delta = delta_ms(timeframe)
        start_ms = expected_max - (limit - 1) * delta
        _fetcher._backfill_range(symbol, timeframe, start_ms, expected_max)
    elif not sufficient:
        _fetcher.ensure_fresh(symbol, timeframe, limit, cached_max, expected_max)
    else:
        metrics.inc("cache_hits_total", labels={"tf": timeframe})

    return _storage.tail(symbol, timeframe, limit)


def get_klines_live(
    symbol: str,
    timeframe: str,
    limit: int,
) -> pd.DataFrame:
    """Last `limit` bars INCLUDING the in-progress bar. Bypasses cache fully.

    Only legitimate consumer: /ohlcv endpoint for animated chart. Does NOT persist.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe: {timeframe}")
    if limit <= 0:
        raise ValueError("limit must be positive")

    d = delta_ms(timeframe)
    now_ms = int(_utcnow().timestamp() * 1000)
    # Current (in-progress) bar open_time:
    current_open_time = (now_ms // d) * d
    start_ms = current_open_time - (limit - 1) * d
    end_ms = current_open_time

    bars = _fetcher.fetch_with_failover(symbol, timeframe, start_ms, end_ms)
    return _bars_to_df(bars)


def _bars_to_df(bars) -> pd.DataFrame:
    cols = ["open_time", "open", "high", "low", "close", "volume", "provider", "fetched_at"]
    return pd.DataFrame(
        [(b.open_time, b.open, b.high, b.low, b.close, b.volume, b.provider, b.fetched_at) for b in bars],
        columns=cols,
    )


def get_klines_range(
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """Closed bars with open_time in [start, end] inclusive (clamped to last closed bar).

    Auto-detects gaps in the cache and backfills only what's missing.
    Raises AllProvidersFailedError if a gap cannot be filled.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe: {timeframe}")
    _ensure_schema_once()

    d = delta_ms(timeframe)
    start_ms = _to_ms(start)
    end_ms = last_closed_bar_time(timeframe, end)

    # Clamp start to known first bar
    earliest = _storage.first_bar_ms(symbol, timeframe)
    if earliest is not None and start_ms < earliest:
        start_ms = earliest

    if start_ms > end_ms:
        return _storage.range_(symbol, timeframe, start_ms, end_ms)

    expected_count = (end_ms - start_ms) // d + 1
    min_t, max_t, count = _storage.range_stats(symbol, timeframe, start_ms, end_ms)

    if count == expected_count:
        return _storage.range_(symbol, timeframe, start_ms, end_ms)

    if count == 0:
        _fetcher._backfill_range(symbol, timeframe, start_ms, end_ms)
    else:
        if min_t > start_ms:
            _fetcher._backfill_range(symbol, timeframe, start_ms, min_t - d)
        if max_t < end_ms:
            _fetcher._backfill_range(symbol, timeframe, max_t + d, end_ms)
        # Re-check; run internal gap fill if still short
        _, _, count2 = _storage.range_stats(symbol, timeframe, start_ms, end_ms)
        if count2 < expected_count:
            _fetcher._fill_internal_gaps(symbol, timeframe, start_ms, end_ms)

    return _storage.range_(symbol, timeframe, start_ms, end_ms)


from concurrent.futures import ThreadPoolExecutor, as_completed


MAX_PARALLEL_FETCH = 5


def prefetch(
    symbols: Iterable[str],
    timeframes: Iterable[str],
    limit: int = 210,
) -> None:
    """Batch-prefetch cache entries for all (symbol, timeframe) combinations in parallel.

    Internal workers call get_klines, so all freshness/locking semantics are preserved.
    Per-(sym, tf) failures are logged and recorded as metrics but do NOT abort the batch.
    """
    tasks = [(s, tf) for s in symbols for tf in timeframes]
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_FETCH) as ex:
        futures = {ex.submit(get_klines, s, tf, limit): (s, tf) for s, tf in tasks}
        for fut in as_completed(futures):
            s, tf = futures[fut]
            try:
                fut.result()
            except Exception as e:
                log.warning("Prefetch failed for %s/%s: %s", s, tf, e)
                metrics.inc("prefetch_errors_total", labels={"symbol": s, "tf": tf})


def backfill(
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime | None = None,
) -> int:
    """Explicit bulk historical fetch + persist. Idempotent, resumable, pre-listing-aware."""
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe: {timeframe}")
    _ensure_schema_once()

    end = end or _utcnow()
    end_ms = last_closed_bar_time(timeframe, end)
    start_ms = _to_ms(start)
    return _fetcher._backfill_range(symbol, timeframe, start_ms, end_ms)


def repair(
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime | None = None,
) -> int:
    """Force re-fetch + overwrite of a range. Use when data anomaly is detected.

    Internally reuses _backfill_range; INSERT OR REPLACE semantics overwrite existing bars.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe: {timeframe}")
    _ensure_schema_once()

    end = end or _utcnow()
    end_ms = last_closed_bar_time(timeframe, end)
    start_ms = _to_ms(start)

    metrics.inc("repairs_requested_total", labels={"symbol": symbol, "tf": timeframe})
    before_count = _storage.range_stats(symbol, timeframe, start_ms, end_ms)[2]
    persisted = _fetcher._backfill_range(symbol, timeframe, start_ms, end_ms)
    after_count = _storage.range_stats(symbol, timeframe, start_ms, end_ms)[2]
    metrics.inc("bars_overwritten_total", max(0, persisted - (after_count - before_count)),
                labels={"symbol": symbol, "tf": timeframe})
    return persisted


def get_stats() -> dict:
    """Snapshot of market data metrics. Exposed via /status endpoint integration."""
    return metrics.get_stats()
