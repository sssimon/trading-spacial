"""Test doubles: FakeProvider implements ProviderAdapter deterministically."""
import time
from typing import Any


class FakeProvider:
    """Deterministic provider for tests. Records calls. Responds from pre-seeded data."""

    def __init__(self, name: str = "fake"):
        self.name = name
        self.rate_limit_per_min = 100_000
        self.calls: list[tuple[str, str, int, int]] = []
        self.bars_by_key: dict[tuple[str, str], list] = {}
        self.raise_by_key: dict[tuple[str, str], Exception] = {}
        self.healthy: bool = True

    def set_bars(self, symbol: str, timeframe: str, bars: list):
        """Seed with a list of Bar instances ordered by open_time ascending."""
        self.bars_by_key[(symbol, timeframe)] = bars

    def set_error(self, symbol: str, timeframe: str, exc: Exception):
        self.raise_by_key[(symbol, timeframe)] = exc

    def clear_errors(self):
        self.raise_by_key.clear()

    def fetch_klines(self, symbol: str, timeframe: str, start_ms: int, end_ms: int):
        self.calls.append((symbol, timeframe, start_ms, end_ms))
        if (symbol, timeframe) in self.raise_by_key:
            raise self.raise_by_key[(symbol, timeframe)]
        all_bars = self.bars_by_key.get((symbol, timeframe), [])
        return [b for b in all_bars if start_ms <= b.open_time <= end_ms]

    def is_healthy(self) -> bool:
        return self.healthy


def make_bar(symbol: str, timeframe: str, open_time: int, price: float = 100.0, **overrides):
    """Factory for test Bar instances. Imports locally so tests can run before Bar exists."""
    from data.providers.base import Bar
    defaults = dict(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        open=price,
        high=price * 1.01,
        low=price * 0.99,
        close=price,
        volume=1000.0,
        provider="fake",
        fetched_at=int(time.time() * 1000),
    )
    defaults.update(overrides)
    return Bar(**defaults)
