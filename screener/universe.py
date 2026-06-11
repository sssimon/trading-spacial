"""Enumeración del universo vivo de Binance spot (Vista Valles A §3).

Sólo pares USDT TRADING, excluyendo stablecoins, fiat y apalancados. Las
delistadas NO aparecen (status != TRADING) — correcto: sólo lo comprable
importa para listar candidatas operables (spec §1, nota de survivorship)."""
from __future__ import annotations

import requests

_EXCHANGE_INFO = "https://api.binance.com/api/v3/exchangeInfo"

# Bases que NO son inversión direccional (stablecoins/fiat envueltos).
_STABLE_FIAT_BASES = {
    "USDC", "BUSD", "TUSD", "USDP", "DAI", "FDUSD", "USDD", "EUR", "GBP",
    "AEUR", "EURI", "USDS", "PAX", "SUSD", "GUSD",
}
# Sufijos de tokens apalancados de Binance.
_LEVERAGED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")


def _http_get(url, params=None, timeout=15):
    """Wrapper fino para que los tests mockeen sólo esta llamada."""
    return requests.get(url, params=params, timeout=timeout)


def _is_eligible(s: dict) -> bool:
    if s.get("quoteAsset") != "USDT":
        return False
    if s.get("status") != "TRADING":
        return False
    if s.get("baseAsset") in _STABLE_FIAT_BASES:
        return False
    sym = s.get("symbol", "")
    if any(sym.endswith(suf) for suf in _LEVERAGED_SUFFIXES):
        return False
    return True


def list_live_usdt_spot() -> list[str]:
    """Lista ordenada de símbolos USDT spot vivos y elegibles. Lanza
    requests.RequestException / RuntimeError si exchangeInfo falla (el caller
    decide; sin universo no hay screener)."""
    r = _http_get(_EXCHANGE_INFO)
    if r.status_code != 200:
        raise RuntimeError(f"exchangeInfo HTTP {r.status_code}")
    symbols = r.json().get("symbols", [])
    return sorted(s["symbol"] for s in symbols if _is_eligible(s))
