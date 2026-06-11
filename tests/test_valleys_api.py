"""Tests del endpoint GET /valley-candidates (Vista Valles A §5.3)."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.valleys import router


def _app():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_devuelve_la_foto_cuando_existe(tmp_path, monkeypatch):
    foto = {"generated_at": "2026-06-11T00:00:00+00:00",
            "coverage": {"universe": 2, "evaluated": 2, "complete": True},
            "candidates": [{"symbol": "XYZUSDT", "price": 1.0, "volumen_usd_dia": 2_000_000.0}]}
    p = tmp_path / "valley_candidates.json"
    p.write_text(json.dumps(foto), encoding="utf-8")
    monkeypatch.setattr("api.valleys._OUTPUT", str(p))

    r = _app().get("/valley-candidates")
    assert r.status_code == 200
    body = r.json()
    assert body["coverage"]["complete"] is True
    assert body["candidates"][0]["symbol"] == "XYZUSDT"


def test_foto_ausente_devuelve_vacio_no_500(tmp_path, monkeypatch):
    monkeypatch.setattr("api.valleys._OUTPUT", str(tmp_path / "no_existe.json"))
    r = _app().get("/valley-candidates")
    assert r.status_code == 200
    body = r.json()
    assert body["candidates"] == []
    assert body["coverage"]["complete"] is False
    assert body["generated_at"] is None
