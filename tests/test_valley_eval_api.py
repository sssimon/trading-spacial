"""Tests del endpoint A on-demand GET /valley-eval/{symbol} (instrumento F3b). Spec §2."""
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.valleys import router
from api.levels import BinanceUnavailable


def _app():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_candidata_true_devuelve_hechos():
    cand = {"symbol": "ADAUSDT", "price": 0.42, "pct_rango": 0.18,
            "semanas_consolidando": 9, "vol_percentil": 0.22,
            "volumen_usd_dia": 3_000_000, "distancia_ath_pct": 0.86, "razones_vida": []}
    with patch("api.valleys._fetch_daily_bars", return_value=[{}] * 130), \
         patch("api.valleys.evaluate_symbol", return_value=cand):
        r = _app().get("/valley-eval/ADAUSDT")
    body = r.json()
    assert body["estado"] == "ok" and body["candidata"] is True
    assert body["pct_rango"] == 0.18


def test_no_candidata_reporta_razones():
    with patch("api.valleys._fetch_daily_bars", return_value=[{}] * 130), \
         patch("api.valleys.evaluate_symbol", return_value=None), \
         patch("api.valleys.classify_liveness", return_value=(False, ["volumen_bajo_piso"])):
        r = _app().get("/valley-eval/XYZUSDT")
    body = r.json()
    assert body["candidata"] is False
    assert body["vivo"] is False
    assert body["razones_muerte"] == ["volumen_bajo_piso"]


def test_red_caida_es_no_disponible_sin_500():
    with patch("api.valleys._fetch_daily_bars", side_effect=BinanceUnavailable("klines HTTP 503")):
        r = _app().get("/valley-eval/ADAUSDT")
    assert r.status_code == 200 and r.json()["estado"] == "no_disponible"
