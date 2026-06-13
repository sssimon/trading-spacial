"""Tests de los endpoints del plan vivo (instrumento F3a). Spec §4/§6."""
import os
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.plan import router, construir_hechos
from api.deps import verify_api_key
from auth.dependencies import get_current_tenant_id


def _app():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_tenant_id] = lambda: 2
    app.dependency_overrides[verify_api_key] = lambda: None
    return TestClient(app)


def _zonas():
    return [{"tipo": "soporte", "precio_bajo": 94, "precio_alto": 96, "centro": 95,
             "toques": 3, "confluencia_redondo": []},
            {"tipo": "resistencia", "precio_bajo": 104, "precio_alto": 106, "centro": 105,
             "toques": 3, "confluencia_redondo": []}]


def test_derive_devuelve_plan_sin_persistir():
    with patch("api.plan._zonas_now", return_value=_zonas()):
        r = _app().get("/plan/derive/BTCUSDT?entry_price=100")
    assert r.status_code == 200
    body = r.json()
    assert body["entry"] == 100.0
    assert [rg["tp_price"] for rg in body["rungs"]] == [105.0]
    assert body["sl_plan"] == 94.0 * (1 - 0.01)


def test_confirm_crea_la_fila(monkeypatch, tmp_path):
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(tmp_path / "d.db"))
    os.environ["MIGRATE_QTY_ALLOW_BULK_QUARANTINE"] = "1"
    try:
        from db.schema import init_db
        init_db()
    finally:
        os.environ.pop("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", None)
    with patch("api.plan._zonas_now", return_value=_zonas()):
        r = _app().post("/plan/confirm", json={"symbol": "BTCUSDT", "entry_price": 100.0})
    assert r.status_code == 200 and r.json()["estado_vivo"] == "activo"


def test_hechos_no_contienen_imperativos():
    hechos = construir_hechos(rungs_llenos=[0], be_movido=True, estado_vivo="activo",
                              sl_actual=100.0, sl_plan=93.06)
    texto = " ".join(hechos).lower()
    for imperativo in ("mové", "movete", "cerrá", "vendé", "comprá", "mueve", "cierra"):
        assert imperativo not in texto


def test_hechos_reportan_tp1_y_be():
    hechos = construir_hechos(rungs_llenos=[0], be_movido=True, estado_vivo="activo",
                              sl_actual=100.0, sl_plan=93.06)
    texto = " ".join(hechos).lower()
    assert "tp1" in texto and "break-even" in texto


def test_vista_router_registrado():
    import btc_api
    rutas = {getattr(r, "path", None) for r in btc_api.app.routes}
    assert "/plan/{symbol}" in rutas


def test_vista_sin_plan_activo_devuelve_none(monkeypatch, tmp_path):
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(tmp_path / "d.db"))
    os.environ["MIGRATE_QTY_ALLOW_BULK_QUARANTINE"] = "1"
    try:
        from db.schema import init_db
        init_db()
    finally:
        os.environ.pop("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", None)
    r = _app().get("/plan/NOPEUSDT")
    assert r.status_code == 200 and r.json()["estado_vivo"] is None


def test_confirm_payload_incompleto_es_422():
    r = _app().post("/plan/confirm", json={"symbol": "BTCUSDT"})   # falta entry_price
    assert r.status_code == 422
