"""Tests del endpoint GET /dossier/{symbol} (caché TTL, no per-tenant). Spec §4/§5."""
import os
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dossier import router, _TTL_SECONDS


def _app():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _fake_dossier(symbol):
    from research.schemas import Dossier
    return Dossier(symbol=symbol, estado_general="opaco", no_encontrado_en=["equipo"])


def test_genera_y_cachea_en_miss(monkeypatch, tmp_path):
    # DB fresca + sin caché → genera (build_dossier_live mockeado) y devuelve.
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(tmp_path / "d.db"))
    os.environ["MIGRATE_QTY_ALLOW_BULK_QUARANTINE"] = "1"
    try:
        from db.schema import init_db
        init_db()
    finally:
        os.environ.pop("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", None)
    with patch("api.dossier.build_dossier_live", side_effect=_fake_dossier) as gen:
        r = _app().get("/dossier/ADAUSDT")
    assert r.status_code == 200
    assert r.json()["estado_general"] == "opaco"
    assert gen.call_count == 1
    # Segunda llamada: caché-hit, NO regenera.
    with patch("api.dossier.build_dossier_live", side_effect=_fake_dossier) as gen2:
        r2 = _app().get("/dossier/ADAUSDT")
    assert r2.status_code == 200
    assert gen2.call_count == 0   # servido desde caché


def test_refresh_fuerza_regeneracion(monkeypatch, tmp_path):
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(tmp_path / "d.db"))
    os.environ["MIGRATE_QTY_ALLOW_BULK_QUARANTINE"] = "1"
    try:
        from db.schema import init_db
        init_db()
    finally:
        os.environ.pop("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", None)
    with patch("api.dossier.build_dossier_live", side_effect=_fake_dossier):
        _app().get("/dossier/ADAUSDT")
    with patch("api.dossier.build_dossier_live", side_effect=_fake_dossier) as gen:
        r = _app().get("/dossier/ADAUSDT?refresh=true")
    assert r.status_code == 200
    assert gen.call_count == 1   # refresh ignora la caché


def test_ttl_es_siete_dias():
    assert _TTL_SECONDS == 7 * 24 * 3600


def test_no_disponible_no_se_cachea_y_reintenta(monkeypatch, tmp_path):
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(tmp_path / "d.db"))
    from db.schema import init_db
    init_db()
    from research.schemas import Dossier
    def _nd(symbol):
        return Dossier(symbol=symbol, estado_general="no_disponible")
    with patch("api.dossier.build_dossier_live", side_effect=_nd) as g1:
        r1 = _app().get("/dossier/ADAUSDT")
    assert r1.status_code == 200
    assert r1.json()["estado_general"] == "no_disponible"
    assert g1.call_count == 1
    # Segunda llamada: como no se cacheó, DEBE regenerar (reintento).
    with patch("api.dossier.build_dossier_live", side_effect=_nd) as g2:
        _app().get("/dossier/ADAUSDT")
    assert g2.call_count == 1   # no servido desde caché — reintenta
