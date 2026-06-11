"""Tests de la enumeración del universo vivo (Vista Valles A §3)."""
from unittest.mock import patch

from screener.universe import list_live_usdt_spot, _is_eligible


class TestEligibilidad:
    def test_par_usdt_normal_elegible(self):
        assert _is_eligible({"symbol": "BTCUSDT", "quoteAsset": "USDT",
                             "status": "TRADING", "baseAsset": "BTC"}) is True

    def test_no_usdt_excluido(self):
        assert _is_eligible({"symbol": "BTCETH", "quoteAsset": "ETH",
                             "status": "TRADING", "baseAsset": "BTC"}) is False

    def test_no_trading_excluido(self):
        assert _is_eligible({"symbol": "XYZUSDT", "quoteAsset": "USDT",
                             "status": "BREAK", "baseAsset": "XYZ"}) is False

    def test_stablecoin_excluido(self):
        assert _is_eligible({"symbol": "USDCUSDT", "quoteAsset": "USDT",
                             "status": "TRADING", "baseAsset": "USDC"}) is False

    def test_apalancado_excluido(self):
        assert _is_eligible({"symbol": "BTCUPUSDT", "quoteAsset": "USDT",
                             "status": "TRADING", "baseAsset": "BTCUP"}) is False


class TestListLiveUsdtSpot:
    def test_filtra_y_devuelve_simbolos(self):
        fake = {"symbols": [
            {"symbol": "BTCUSDT", "quoteAsset": "USDT", "status": "TRADING", "baseAsset": "BTC"},
            {"symbol": "USDCUSDT", "quoteAsset": "USDT", "status": "TRADING", "baseAsset": "USDC"},
            {"symbol": "ETHBTC", "quoteAsset": "BTC", "status": "TRADING", "baseAsset": "ETH"},
            {"symbol": "ADAUSDT", "quoteAsset": "USDT", "status": "TRADING", "baseAsset": "ADA"},
        ]}

        class _Resp:
            status_code = 200
            def json(self):
                return fake

        with patch("screener.universe._http_get", return_value=_Resp()):
            out = list_live_usdt_spot()
        assert out == ["ADAUSDT", "BTCUSDT"]  # ordenado, sin stable ni cross
