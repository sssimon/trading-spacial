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
