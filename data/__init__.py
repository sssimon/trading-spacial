"""Market Data Layer — unified OHLCV cache + fetch for all modules.

Public entrypoints:
    get_klines(symbol, timeframe, limit, force_refresh=False) -> DataFrame
    get_klines_range(symbol, timeframe, start, end)          -> DataFrame
    get_klines_live(symbol, timeframe, limit)                -> DataFrame
    prefetch(symbols, timeframes, limit=210)                 -> None
    backfill(symbol, timeframe, start, end=None)             -> int
    repair(symbol, timeframe, start, end=None)               -> int

Utilities:
    get_stats()                                              -> dict
    last_closed_bar_time(timeframe, now=None)                -> int ms

See docs/superpowers/specs/en/2026-04-18-market-data-layer-design.md
"""
from data.market_data import (
    get_klines,
    get_klines_range,
    get_klines_live,
    prefetch,
    backfill,
    repair,
    get_stats,
)
from data.timeframes import last_closed_bar_time

__all__ = [
    "get_klines", "get_klines_range", "get_klines_live",
    "prefetch", "backfill", "repair",
    "get_stats", "last_closed_bar_time",
]
