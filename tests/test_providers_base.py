import pytest
from data.providers.base import (
    Bar, ProviderError, ProviderInvalidSymbol,
    ProviderRateLimited, ProviderTemporaryError,
)


class TestBar:
    def test_construction_and_tuple(self):
        b = Bar(
            symbol="BTCUSDT", timeframe="1h", open_time=1000,
            open=100.0, high=110.0, low=95.0, close=105.0, volume=50.0,
            provider="binance", fetched_at=2000,
        )
        assert b.symbol == "BTCUSDT"
        tup = b.as_tuple()
        assert tup == ("BTCUSDT", "1h", 1000, 100.0, 110.0, 95.0, 105.0, 50.0, "binance", 2000)

    def test_frozen(self):
        b = Bar(symbol="X", timeframe="1h", open_time=0, open=1.0, high=1.0, low=1.0, close=1.0,
                volume=0.0, provider="x", fetched_at=0)
        with pytest.raises((AttributeError, Exception)):
            b.symbol = "Y"


class TestExceptionHierarchy:
    def test_all_inherit_from_provider_error(self):
        assert issubclass(ProviderInvalidSymbol, ProviderError)
        assert issubclass(ProviderRateLimited, ProviderError)
        assert issubclass(ProviderTemporaryError, ProviderError)

    def test_raising_and_catching(self):
        with pytest.raises(ProviderError):
            raise ProviderRateLimited("too many requests")
        with pytest.raises(ProviderError):
            raise ProviderTemporaryError("503")
