"""Tests del endpoint GET /alt-season (régimen de mercado, Valles)."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.alt_season import router


def _app():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _snap(generated_at, estado="alts"):
    return {"generated_at": generated_at,
            "coverage": {"universe": 3, "evaluated": 3, "complete": True},
            "dominancia_fetch": {"ok": True, "fetched_at": generated_at, "source": "coingecko/global"},
            "regime": {"estado": estado, "componentes": {}, "n_alts_evaluadas": 2,
                       "votos": {"alts": 3, "neutral": 0, "btc": 0, "vivos": 3}}}


def test_foto_fresca_es_fresca_y_trae_estado(tmp_path):
    ahora = datetime.now(timezone.utc).isoformat()
    p = tmp_path / "alt_season.json"
    p.write_text(json.dumps(_snap(ahora)), encoding="utf-8")
    with patch("api.alt_season._OUTPUT", str(p)):
        r = _app().get("/alt-season")
    assert r.status_code == 200
    body = r.json()
    assert body["frescura"]["estado"] == "fresco"
    assert body["regime"]["estado"] == "alts"


def test_foto_vieja_es_rancia(tmp_path):
    viejo = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    p = tmp_path / "alt_season.json"
    p.write_text(json.dumps(_snap(viejo)), encoding="utf-8")
    with patch("api.alt_season._OUTPUT", str(p)):
        r = _app().get("/alt-season")
    assert r.json()["frescura"]["estado"] == "rancio"


def test_foto_ausente_es_muerto_no_vacio_mudo(tmp_path):
    with patch("api.alt_season._OUTPUT", str(tmp_path / "nope.json")):
        r = _app().get("/alt-season")
    assert r.status_code == 200
    assert r.json()["frescura"]["estado"] == "muerto"


def test_payload_sin_lenguaje_de_veredicto_ni_per_simbolo(tmp_path):
    ahora = datetime.now(timezone.utc).isoformat()
    p = tmp_path / "alt_season.json"
    p.write_text(json.dumps(_snap(ahora)), encoding="utf-8")
    with patch("api.alt_season._OUTPUT", str(p)):
        body = _app().get("/alt-season").json()
    blob = json.dumps(body, ensure_ascii=False).lower()
    for prohibido in ("comprar", "vender", "subirá", "entra", "señal de compra",
                      "mandan", "manda", "fuertes", "débil", "débiles", "symbol"):
        assert prohibido not in blob, f"lenguaje prohibido en el payload: {prohibido}"
