# tests/test_binance_account_client.py
import hashlib
import hmac
from unittest.mock import patch
import pytest
import requests


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


def test_transport_error_scrubs_signature_from_exception():
    # BNC-2 §2.4 #4: la firma per-request (la URL lleva &signature=<hmac>) NUNCA
    # debe filtrarse al mensaje/traceback cuando requests lanza un error de red.
    from data.providers.binance_account import BinanceAccountClient, BinanceTransportError

    leaky = requests.ConnectionError(
        "HTTPSConnectionPool(host='api.binance.com'): Max retries exceeded with "
        "url: /api/v3/account?timestamp=1&recvWindow=5000&signature=DEADBEEFSIGNATURE"
    )
    client = BinanceAccountClient(api_key="k", secret="THE_SECRET", server_time_offset_ms=0)
    with patch("data.providers.binance_account._http_get", side_effect=leaky):
        with pytest.raises(BinanceTransportError) as ei:
            client.get_spot_balances()
    msg = str(ei.value)
    assert "DEADBEEFSIGNATURE" not in msg
    assert "signature" not in msg.lower()
    assert "THE_SECRET" not in msg
    # `from None` debe suprimir la excepción original (que sí lleva la firma).
    assert ei.value.__cause__ is None
    assert ei.value.__suppress_context__ is True


def test_minus_1021_raises_clock_skew():
    from data.providers.binance_account import BinanceAccountClient, BinanceClockSkew

    class FakeResp:
        status_code = 400
        def json(self):
            return {"code": -1021, "msg": "Timestamp for this request is outside of the recvWindow."}

    client = BinanceAccountClient(api_key="k", secret="s", server_time_offset_ms=0)
    with patch("data.providers.binance_account._signed_get", return_value=FakeResp()):
        with pytest.raises(BinanceClockSkew):
            client.get_spot_balances()


def test_rate_banned_maps_to_typed_error():
    from data.providers.binance_account import BinanceAccountClient, BinanceRateBanned

    class FakeResp:
        status_code = 429
        def json(self):
            return {"code": -1003, "msg": "Too much request weight used."}

    client = BinanceAccountClient(api_key="k", secret="s", server_time_offset_ms=0)
    with patch("data.providers.binance_account._signed_get", return_value=FakeResp()):
        with pytest.raises(BinanceRateBanned):
            client.get_spot_balances()
