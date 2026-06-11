# tests/test_binance_account_client.py
import hashlib
import hmac
from unittest.mock import patch, Mock
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


# ─── v0.2: myTrades + exchangeInfo + ticker (Task 3) ──────────────────────────


def test_raise_for_error_code_handles_list_body():
    """Un myTrades exitoso devuelve una LISTA (no un dict con 'code').
    _raise_for_error_code NO debe crashear con .get sobre una lista."""
    from data.providers.binance_account import _raise_for_error_code

    class ListResp:
        status_code = 200
        def json(self):
            return [{"id": 1, "price": "100"}]
    _raise_for_error_code(ListResp())  # no debe lanzar


def test_get_my_trades_paginates_by_from_id():
    from data.providers import binance_account as ba
    from data.providers.binance_account import BinanceAccountClient

    class Resp:
        status_code = 200
        def __init__(self, fills): self._f = fills
        def json(self): return self._f

    page1 = [{"id": 1, "price": "1"}, {"id": 2, "price": "2"}]  # == limit(2) → sigue
    page2 = [{"id": 3, "price": "3"}]                            # < limit → para
    client = BinanceAccountClient(api_key="k", secret="s", server_time_offset_ms=0)
    with patch.object(ba, "_MYTRADES_PAGE_LIMIT", 2), \
         patch("data.providers.binance_account._signed_get",
               side_effect=[Resp(page1), Resp(page2)]) as m:
        fills = client.get_my_trades("BTCUSDT")
    assert [f["id"] for f in fills] == [1, 2, 3]
    # la 2da página pide fromId = max(id de page1) + 1 = 3
    assert m.call_args_list[1].args[3]["fromId"] == 3
    assert m.call_args_list[1].args[3]["symbol"] == "BTCUSDT"


def test_get_my_trades_single_page():
    from data.providers import binance_account as ba
    from data.providers.binance_account import BinanceAccountClient

    class Resp:
        status_code = 200
        def json(self): return [{"id": 1}, {"id": 2}]  # < limit 1000 → una sola llamada
    client = BinanceAccountClient(api_key="k", secret="s", server_time_offset_ms=0)
    with patch("data.providers.binance_account._signed_get", return_value=Resp()) as m:
        fills = client.get_my_trades("ETHUSDT")
    assert len(fills) == 2
    assert m.call_count == 1


def test_get_my_trades_rate_ban_propagates():
    """Un ban mid-paginación PROPAGA (el caller marca ingest_incompleto, no
    persiste ACB truncado — spec F8)."""
    from data.providers.binance_account import BinanceAccountClient, BinanceRateBanned

    class Resp:
        status_code = 429
        def json(self): return {"code": -1003, "msg": "weight"}
    client = BinanceAccountClient(api_key="k", secret="s", server_time_offset_ms=0)
    with patch("data.providers.binance_account._signed_get", return_value=Resp()):
        with pytest.raises(BinanceRateBanned):
            client.get_my_trades("BTCUSDT")


def test_get_exchange_filters_parses_notional_and_lot():
    from data.providers.binance_account import BinanceAccountClient

    class Resp:
        status_code = 200
        def json(self):
            return {"symbols": [{
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "LOT_SIZE", "minQty": "0.00001000", "stepSize": "0.00001000"},
                    {"filterType": "NOTIONAL", "minNotional": "10.00000000"},
                ],
            }]}
    client = BinanceAccountClient(api_key="k", secret="s", server_time_offset_ms=0)
    with patch("data.providers.binance_account._http_get", return_value=Resp()):
        out = client.get_exchange_filters(["BTCUSDT"])
    assert out["BTCUSDT"]["min_notional"] == 10.0
    assert out["BTCUSDT"]["min_qty"] == 0.00001


def test_get_ticker_prices_parses():
    from data.providers.binance_account import BinanceAccountClient

    class Resp:
        status_code = 200
        def json(self):
            return [{"symbol": "BTCUSDT", "price": "64000.5"},
                    {"symbol": "ETHUSDT", "price": "3000.0"}]
    client = BinanceAccountClient(api_key="k", secret="s", server_time_offset_ms=0)
    with patch("data.providers.binance_account._http_get", return_value=Resp()):
        prices = client.get_ticker_prices(["BTCUSDT", "ETHUSDT"])
    assert prices == {"BTCUSDT": 64000.5, "ETHUSDT": 3000.0}


def test_public_methods_empty_symbols_no_call():
    from data.providers.binance_account import BinanceAccountClient
    client = BinanceAccountClient(api_key="k", secret="s", server_time_offset_ms=0)
    with patch("data.providers.binance_account._http_get") as m:
        assert client.get_exchange_filters([]) == {}
        assert client.get_ticker_prices([]) == {}
    m.assert_not_called()


# ─── v0.3: get_open_orders (Task 1) ───────────────────────────────────────────


class TestGetOpenOrders:
    def test_devuelve_lista_cruda_y_firma_el_request(self):
        from data.providers.binance_account import BinanceAccountClient

        captured = {}

        def fake_get(url, params=None, headers=None, timeout=10):
            captured["url"] = url
            captured["headers"] = headers
            resp = Mock()
            resp.status_code = 200
            resp.json.return_value = [
                {"symbol": "BTCUSDT", "orderId": 7, "orderListId": 33,
                 "side": "SELL", "type": "STOP_LOSS_LIMIT",
                 "price": "49000", "stopPrice": "50000",
                 "origQty": "0.5", "executedQty": "0"},
            ]
            return resp

        client = BinanceAccountClient(api_key="K", secret="S")
        with patch("data.providers.binance_account._http_get", side_effect=fake_get):
            orders = client.get_open_orders()

        assert orders[0]["orderId"] == 7
        assert "/api/v3/openOrders" in captured["url"]
        assert "signature=" in captured["url"]          # request firmado
        assert "symbol=" not in captured["url"]         # SIN symbol: toda la cuenta
        assert captured["headers"]["X-MBX-APIKEY"] == "K"

    def test_error_2015_levanta_auth_error(self):
        from data.providers.binance_account import BinanceAccountClient, BinanceAuthError

        def fake_get(url, params=None, headers=None, timeout=10):
            resp = Mock()
            resp.status_code = 400
            resp.json.return_value = {"code": -2015, "msg": "Invalid API-key"}
            return resp

        client = BinanceAccountClient(api_key="K", secret="S")
        with patch("data.providers.binance_account._http_get", side_effect=fake_get):
            with pytest.raises(BinanceAuthError):
                client.get_open_orders()
