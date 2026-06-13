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


# ── Nuevos tests de frescura (Task 2) ─────────────────────────────────────────

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import FastAPI

from api.valleys import router as _valleys_router


def _veapp():
    app = FastAPI()
    app.include_router(_valleys_router)
    return TestClient(app)


def test_valles_sin_foto_es_muerto_no_vacio_mudo(tmp_path):
    with patch("api.valleys._OUTPUT", str(tmp_path / "nope.json")):
        r = _veapp().get("/valley-candidates")
    assert r.status_code == 200
    assert r.json()["frescura"]["estado"] == "muerto"


def test_valles_foto_vieja_es_rancia(tmp_path):
    viejo = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    p = tmp_path / "foto.json"
    p.write_text(json.dumps({"generated_at": viejo,
                             "coverage": {"universe": 1, "evaluated": 1, "complete": True},
                             "candidates": []}), encoding="utf-8")
    with patch("api.valleys._OUTPUT", str(p)):
        r = _veapp().get("/valley-candidates")
    assert r.json()["frescura"]["estado"] == "rancio"
