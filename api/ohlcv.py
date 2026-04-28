"""OHLCV route — returns candle data for the frontend chart.

Extracted from btc_api.py in PR1 of the api+db refactor (2026-04-27).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from data import market_data as md

router = APIRouter(tags=["ohlcv"])

_VALID_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
}


@router.get("/ohlcv", summary="Velas OHLCV para graficar")
def get_ohlcv(
    symbol:   str = Query("BTCUSDT", description="Par de trading (ej: ETHUSDT)"),
    interval: str = Query("1h",      description="Intervalo: 5m,15m,1h,4h,1d"),
    limit:    int = Query(300,       ge=1, le=1000, description="Número de velas"),
):
    """Retorna datos OHLCV listos para lightweight-charts (timestamps en segundos UTC).
    Usa md.get_klines_live() — incluye la barra en curso para el gráfico animado."""
    if interval not in _VALID_INTERVALS:
        raise HTTPException(status_code=400, detail=f"Intervalo invalido: {interval}")
    try:
        df = md.get_klines_live(symbol.upper(), interval, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error obteniendo OHLCV: {e}")

    if df.empty:
        return {"symbol": symbol.upper(), "interval": interval, "candles": [], "volumes": []}

    candles, volumes = [], []
    for _, row in df.iterrows():
        ts = int(row["open_time"]) // 1000  # ms → seconds for lightweight-charts
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        v = float(row["volume"])
        candles.append({"time": ts, "open": o, "high": h, "low": l, "close": c})
        volumes.append({
            "time":  ts,
            "value": v,
            "color": "rgba(34,197,94,0.35)" if c >= o else "rgba(239,68,68,0.35)",
        })

    return {"symbol": symbol.upper(), "interval": interval, "candles": candles, "volumes": volumes}
