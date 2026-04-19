"""Provider adapter contract: Bar dataclass, exceptions, Protocol.

Adding a new provider = implement ProviderAdapter Protocol in a new module and
register it in data._fetcher._PROVIDERS.
"""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Bar:
    """Normalized OHLCV bar, provider-agnostic."""
    symbol: str
    timeframe: str
    open_time: int    # ms UTC
    open: float
    high: float
    low: float
    close: float
    volume: float
    provider: str
    fetched_at: int   # ms UTC

    def as_tuple(self) -> tuple:
        """Serialization for SQLite INSERT."""
        return (
            self.symbol, self.timeframe, self.open_time,
            self.open, self.high, self.low, self.close, self.volume,
            self.provider, self.fetched_at,
        )


class ProviderError(Exception):
    """Base of all provider errors. May propagate to consumers."""


class ProviderInvalidSymbol(ProviderError):
    """Symbol does not exist on this provider. FATAL — no failover triggered."""


class ProviderRateLimited(ProviderError):
    """Rate limit hit. Triggers failover threshold counter."""


class ProviderTemporaryError(ProviderError):
    """5xx, timeout, DNS. Triggers failover threshold counter."""


class AllProvidersFailedError(ProviderError):
    """Every provider in the registry returned an error."""


class ProviderAdapter(Protocol):
    name: str
    rate_limit_per_min: int

    def fetch_klines(
        self, symbol: str, timeframe: str, start_ms: int, end_ms: int
    ) -> list[Bar]:
        """Fetch bars with open_time in [start_ms, end_ms] (inclusive both).

        Returns list ordered by open_time ascending. Empty list means
        no bars exist in that range (e.g., pre-listing).
        """
        ...

    def is_healthy(self) -> bool:
        """Cheap probe used for recovery logic after failover."""
        ...
