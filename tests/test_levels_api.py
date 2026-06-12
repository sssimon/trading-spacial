"""Tests del endpoint GET /levels/{symbol}. Fetch mockeado: sin red real. Spec §4."""
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.levels import router


def _app():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _bars():
    bars = [{"open_time": 0, "open": 100, "high": 100, "low": 100,
             "close": 100, "volume": 1, "quote_volume": 1} for _ in range(40)]
    for i in (5, 20):
        bars[i] = {**bars[i], "high": 110}
    for i in (12, 30):
        bars[i] = {**bars[i], "low": 90}
    return bars


def test_payload_ok():
    with patch("api.levels._fetch_daily_bars", return_value=_bars()), \
         patch("api.levels._fetch_live_price", return_value=100.0):
        r = _app().get("/levels/BTCUSDT")
    assert r.status_code == 200
    body = r.json()
    assert body["estado"] == "ok"
    assert body["price_live"] == 100.0
    assert {z["tipo"] for z in body["zonas"]} == {"resistencia", "soporte"}
    assert "ubicacion" in body


def test_binance_caido_es_no_disponible_sin_500():
    with patch("api.levels._fetch_daily_bars", side_effect=RuntimeError("klines HTTP 503")):
        r = _app().get("/levels/BTCUSDT")
    assert r.status_code == 200
    assert r.json()["estado"] == "no_disponible"
    assert r.json()["zonas"] == []
    assert r.json()["price_live"] is None


def test_symbol_invalido_es_no_disponible():
    with patch("api.levels._fetch_daily_bars", side_effect=RuntimeError("klines HTTP 400")):
        r = _app().get("/levels/NOPEUSDT")
    assert r.json()["estado"] == "no_disponible"


def test_endpoint_no_toca_db():
    import inspect
    import api.levels
    src = inspect.getsource(api.levels)
    assert "transaction" not in src
    assert "snapshot_connection" not in src


def test_router_registrado_en_la_app():
    import btc_api
    rutas = {getattr(r, "path", None) for r in btc_api.app.routes}
    assert "/levels/{symbol}" in rutas
