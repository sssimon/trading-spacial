"""Cliente Binance AUTENTICADO read-only spot (firmado HMAC-SHA256).

SEPARADO del adapter público de klines (data/providers/binance.py): NO comparte
el failover a Bybit (un endpoint de cuenta no es fungible — Bybit no tiene la
cuenta del tenant). Solo lectura: get_spot_balances + la sonda probe_trading_disabled
(usa /api/v3/order/test, que valida SIN colocar). Cero métodos que coloquen órdenes.

Spec: docs/superpowers/specs/es/2026-06-10-conexion-binance-solo-lectura-spec.md §3, §2.4.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse

import requests

BASE_URL = "https://api.binance.com"
RECV_WINDOW_MS = 5000


class BinanceAuthError(Exception):
    """-2015: API-key/IP/permiso inválido (Binance no los distingue)."""


class BinanceClockSkew(Exception):
    """-1021: timestamp fuera de recvWindow (reloj desfasado)."""


class BinanceRateBanned(Exception):
    """-1003 / 418 / 429: ban temporal por weight."""


def _http_get(url, params=None, headers=None, timeout=10):
    """Wrapper fino para que los tests mockeen solo esta llamada."""
    return requests.get(url, params=params, headers=headers, timeout=timeout)


def _http_post(url, params=None, headers=None, timeout=10):
    return requests.post(url, params=params, headers=headers, timeout=timeout)


def _sign(secret: str, query_string: str) -> str:
    return hmac.new(secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()


def _signed_get(api_key, secret, path, params, offset_ms, method="GET"):
    """Firma y envía un request a un endpoint USER_DATA/TRADE. NO loguea la secret."""
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000) + offset_ms
    p["recvWindow"] = RECV_WINDOW_MS
    qs = urllib.parse.urlencode(p)
    qs = qs + "&signature=" + _sign(secret, qs)
    url = BASE_URL + path + "?" + qs
    headers = {"X-MBX-APIKEY": api_key}
    if method == "POST":
        return _http_post(url, headers=headers, timeout=10)
    return _http_get(url, headers=headers, timeout=10)


def get_server_time_offset_ms() -> int:
    """offset = serverTime - localTime, para no fallar -1021 por clock skew."""
    r = _http_get(BASE_URL + "/api/v3/time", timeout=5)
    server_ms = int(r.json()["serverTime"])
    return server_ms - int(time.time() * 1000)


def _raise_for_error_code(resp):
    """Mapea códigos firmados de Binance a excepciones tipadas. La secret NUNCA
    entra al mensaje de la excepción (solo el code + msg de Binance)."""
    try:
        body = resp.json()
    except Exception:
        body = {}
    code = body.get("code")
    if code == -2015:
        raise BinanceAuthError("-2015: " + body.get("msg", ""))
    if code == -1021:
        raise BinanceClockSkew("-1021: " + body.get("msg", ""))
    if code == -1003 or resp.status_code in (418, 429):
        raise BinanceRateBanned("rate banned: HTTP " + str(resp.status_code))
    if resp.status_code != 200:
        raise RuntimeError("binance account HTTP " + str(resp.status_code) + ": code=" + str(code))


class BinanceAccountClient:
    def __init__(self, *, api_key: str, secret: str, server_time_offset_ms: int = 0):
        self._api_key = api_key
        self._secret = secret
        self._offset = server_time_offset_ms

    def get_spot_balances(self) -> dict:
        """{asset: free+locked} para balances > 0. Lee /api/v3/account (USER_DATA)."""
        resp = _signed_get(self._api_key, self._secret, "/api/v3/account", {}, self._offset)
        _raise_for_error_code(resp)
        out = {}
        for b in resp.json().get("balances", []):
            total = float(b["free"]) + float(b["locked"])
            if total > 0:
                out[b["asset"]] = total
        return out

    def probe_trading_disabled(self) -> bool:
        """True si la key NO puede operar (lo que queremos para read-only).

        Usa /api/v3/order/test con una orden bien-formada: si Binance la rechaza
        por permiso (-2015) => trading deshabilitado => True. Si devuelve exito ({})
        => trading HABILITADO => False (la key debe rechazarse). order/test NO
        coloca ninguna orden (valida y descarta)."""
        params = {
            "symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT",
            "timeInForce": "GTC", "quantity": "0.00001", "price": "1",
        }
        resp = _signed_get(self._api_key, self._secret, "/api/v3/order/test",
                           params, self._offset, method="POST")
        try:
            code = resp.json().get("code")
        except Exception:
            code = None
        if code == -2015:
            return True   # sin permiso de trading -> correcto
        if resp.status_code == 200:
            return False  # order/test aceptado -> la key SI puede operar
        # Otro error (p.ej. parametro) -- no concluyente; fail-closed a "no validado".
        raise RuntimeError("order/test no concluyente: HTTP " + str(resp.status_code) + " code=" + str(code))
