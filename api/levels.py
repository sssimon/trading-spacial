"""API del detector neutral de S/R (D.1). GET /levels/{symbol}.

Read-only, NO per-tenant (los niveles de un símbolo son globales). Trae velas
diarias + precio vivo de Binance (red FUERA de toda tx), corre el detector puro
y ubica el precio. NUNCA cachea, NUNCA toca DB — el precio es vivo, se computa
fresco cada request. Fallo externo → 'no_disponible' sin 500. Spec §4."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests
from fastapi import APIRouter

from freshness import LiveSnapshot
from screener.sr_levels import LOOKBACK_DAYS, detect_levels, locate_price

log = logging.getLogger("api.levels")

router = APIRouter(tags=["levels"])

FRESCURA_LEVELS_SEG = 60  # precio es vivo/fresco cada request

_KLINES_URL = "https://api.binance.com/api/v3/klines"
_PRICE_URL = "https://api.binance.com/api/v3/ticker/price"


class BinanceUnavailable(Exception):
    """Fallo EXTERNO de Binance (red, rate-ban, HTTP no-200, payload inesperado).
    Distinto de un bug interno: solo esto mapea a 'no_disponible'."""


def _http_get(url, params=None, timeout=15):
    return requests.get(url, params=params, timeout=timeout)


def _fetch_daily_bars(symbol: str) -> list[dict]:
    """Velas 1d del contrato puro (índices 0,1,2,3,4,5,7). Lanza BinanceUnavailable en no-200."""
    r = _http_get(_KLINES_URL,
                  params={"symbol": symbol, "interval": "1d", "limit": LOOKBACK_DAYS})
    if r.status_code in (429, 418):
        raise BinanceUnavailable(f"rate banned HTTP {r.status_code}")
    if r.status_code != 200:
        raise BinanceUnavailable(f"klines HTTP {r.status_code}")
    return [
        {"open_time": int(x[0]), "open": float(x[1]), "high": float(x[2]),
         "low": float(x[3]), "close": float(x[4]), "volume": float(x[5]),
         "quote_volume": float(x[7])}
        for x in r.json()
    ]


def _fetch_live_price(symbol: str) -> float:
    """Precio spot vivo (/ticker/price). Lanza BinanceUnavailable en cualquier
    fallo externo: rate-ban, HTTP no-200, o payload sin 'price' numérico."""
    r = _http_get(_PRICE_URL, params={"symbol": symbol})
    if r.status_code in (429, 418):
        raise BinanceUnavailable(f"rate banned HTTP {r.status_code}")
    if r.status_code != 200:
        raise BinanceUnavailable(f"price HTTP {r.status_code}")
    try:
        return float(r.json()["price"])
    except (KeyError, TypeError, ValueError) as e:
        raise BinanceUnavailable(f"price payload inesperado: {e}") from e


def _no_disponible(symbol: str) -> dict:
    payload = {"symbol": symbol, "estado": "no_disponible",
               "price_live": None, "zonas": [],
               "ubicacion": {"dentro_de": None, "techo": None, "piso": None}}
    return LiveSnapshot(payload=payload, generated_at=None,
                        umbral_seg=FRESCURA_LEVELS_SEG).to_response()


@router.get("/levels/{symbol}", summary="Niveles S/R neutrales + ubicación del precio vivo")
def get_levels(symbol: str) -> dict:
    """Detecta zonas S/R desde velas diarias y ubica el precio vivo. NUNCA 500ea
    por fallo externo (Binance caído / símbolo inválido) → 'no_disponible'."""
    symbol = symbol.upper()[:20]
    try:
        bars = _fetch_daily_bars(symbol)
        price = _fetch_live_price(symbol)
    except (requests.RequestException, BinanceUnavailable) as e:
        log.warning("LEVELS_NO_DISPONIBLE symbol=%s causa=%s", symbol, e)
        return _no_disponible(symbol)

    zonas = detect_levels(bars)
    generated_at = datetime.now(timezone.utc).isoformat()
    payload = {"symbol": symbol, "estado": "ok",
               "generated_at": generated_at,
               "price_live": price, "zonas": zonas,
               "ubicacion": locate_price(price, zonas)}
    return LiveSnapshot(payload=payload, generated_at=generated_at,
                        umbral_seg=FRESCURA_LEVELS_SEG).to_response()
