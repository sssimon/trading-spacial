"""Orquestador del screener de valles (Vista Valles A §5.2).

ÚNICO lugar con I/O de red: enumera el universo vivo, baja klines diarias
frescas, aplica el cálculo puro (screener.valley_filter) y escribe una foto
regenerable a data/valley_candidates.json. Un símbolo que falla se OMITE con
su razón (fallo parcial no corrompe el resultado, spec §6); la cobertura se
reporta con honestidad (complete=False si faltó alguno).

Uso: python -m tools.run_valley_screener
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

import requests

from screener.universe import list_live_usdt_spot
from screener.valley_filter import evaluate_symbol, order_neutral

log = logging.getLogger("tools.run_valley_screener")

_KLINES_URL = "https://api.binance.com/api/v3/klines"
_HISTORY_DAYS = 400   # cubre la ventana de percentil (365) + margen
_OUTPUT = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       "data", "valley_candidates.json")


def _http_get(url, params=None, timeout=15):
    return requests.get(url, params=params, timeout=timeout)


def _fetch_daily_klines(symbol: str, *, limit: int = _HISTORY_DAYS) -> list[list]:
    """Filas crudas de klines 1d de Binance (público). Lanza en error de red /
    HTTP no-200 para que el caller cuente el símbolo como no evaluado."""
    r = _http_get(_KLINES_URL, params={"symbol": symbol, "interval": "1d", "limit": limit})
    if r.status_code in (429, 418):
        raise RuntimeError(f"rate banned HTTP {r.status_code}")
    if r.status_code != 200:
        raise RuntimeError(f"klines HTTP {r.status_code}")
    return r.json()


def _rows_to_bars(rows: list[list]) -> list[dict]:
    """Filas crudas de Binance → barras del contrato puro (índices 0,1,2,3,4,5,7)."""
    return [
        {"open_time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "volume": float(r[5]),
         "quote_volume": float(r[7])}
        for r in rows
    ]


def build_snapshot(*, pause_s: float = 0.0) -> dict:
    """Construye la foto del screener. Devuelve el dict serializable (no
    escribe a disco — eso lo hace main, para que los tests no toquen el FS)."""
    universo = list_live_usdt_spot()
    candidatas: list[dict] = []
    evaluadas = 0
    for sym in universo:
        try:
            rows = _fetch_daily_klines(sym)
        except (requests.RequestException, RuntimeError) as e:
            log.warning("SCREENER_SYMBOL_SKIPPED symbol=%s causa=%s", sym, e)
            continue
        evaluadas += 1
        cand = evaluate_symbol(sym, _rows_to_bars(rows))
        if cand is not None:
            candidatas.append(cand)
        if pause_s:
            time.sleep(pause_s)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coverage": {
            "universe": len(universo),
            "evaluated": evaluadas,
            "complete": evaluadas == len(universo),
        },
        "candidates": order_neutral(candidatas),
    }


def regenerate(*, pause_s: float = 0.05) -> dict:
    """build_snapshot + escribe el JSON. Usado por main() y por screener_loop."""
    snap = build_snapshot(pause_s=pause_s)
    os.makedirs(os.path.dirname(_OUTPUT), exist_ok=True)
    with open(_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2, ensure_ascii=False)
    return snap


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    snap = regenerate()
    cov = snap["coverage"]
    print(f"valley_candidates.json: {len(snap['candidates'])} candidatas; "
          f"cobertura {cov['evaluated']}/{cov['universe']} "
          f"({'completa' if cov['complete'] else 'INCOMPLETA'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
