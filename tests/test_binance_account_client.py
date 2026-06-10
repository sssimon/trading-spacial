# tests/test_binance_account_client.py
import hashlib
import hmac
from unittest.mock import patch
import pytest


def test_signature_is_hmac_sha256_of_query():
    from data.providers.binance_account import _sign
    sig = _sign("secretkey", "symbol=BTCUSDT&timestamp=123")
    expected = hmac.new(b"secretkey", b"symbol=BTCUSDT&timestamp=123", hashlib.sha256).hexdigest()
    assert sig == expected


def test_get_spot_account_parses_free_plus_locked():
    from data.providers.binance_account import BinanceAccountClient

    class FakeResp:
        status_code = 200
        def json(self):
            return {"balances": [
                {"asset": "BTC", "free": "0.5", "locked": "0.1"},
                {"asset": "ETH", "free": "2.0", "locked": "0.0"},
                {"asset": "DUST", "free": "0.0", "locked": "0.0"},
            ]}

    client = BinanceAccountClient(api_key="k", secret="s", server_time_offset_ms=0)
    with patch("data.providers.binance_account._signed_get", return_value=FakeResp()):
        balances = client.get_spot_balances()
    assert balances == {"BTC": 0.6, "ETH": 2.0}  # free+locked; zero-balance dropped


def test_minus_2015_raises_auth_error():
    from data.providers.binance_account import BinanceAccountClient, BinanceAuthError

    class FakeResp:
        status_code = 401
        def json(self):
            return {"code": -2015, "msg": "Invalid API-key, IP, or permissions for action."}
        text = "{}"

    client = BinanceAccountClient(api_key="k", secret="s", server_time_offset_ms=0)
    with patch("data.providers.binance_account._signed_get", return_value=FakeResp()):
        with pytest.raises(BinanceAuthError):
            client.get_spot_balances()


def test_probe_trading_disabled_when_order_test_returns_minus_2015():
    from data.providers.binance_account import BinanceAccountClient

    class FakeResp:
        status_code = 401
        def json(self):
            return {"code": -2015, "msg": "..."}
        text = "{}"

    client = BinanceAccountClient(api_key="k", secret="s", server_time_offset_ms=0)
    with patch("data.providers.binance_account._signed_get", return_value=FakeResp()):
        assert client.probe_trading_disabled() is True  # -2015 → no trading → OK


def test_probe_trading_enabled_when_order_test_succeeds():
    from data.providers.binance_account import BinanceAccountClient

    class FakeResp:
        status_code = 200
        def json(self):
            return {}  # order/test success ⇒ trading ENABLED ⇒ key debe rechazarse
        text = "{}"

    client = BinanceAccountClient(api_key="k", secret="s", server_time_offset_ms=0)
    with patch("data.providers.binance_account._signed_get", return_value=FakeResp()):
        assert client.probe_trading_disabled() is False
