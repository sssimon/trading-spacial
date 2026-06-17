"""Tests de los endpoints del plan vivo (instrumento F3a). Spec §4/§6."""
import os
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.plan import router, construir_hechos, _plan_payload
from api.deps import verify_api_key
from auth.dependencies import get_current_tenant_id
from instrument.plan import derive_plan


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
    # Guarda la regresión "alguien borró el include_router(plan_router)" de
    # forma determinista, vía inspección de fuente (mismo idioma que
    # test_endpoint_no_toca_db en test_levels_api). NO se lee btc_api.app.routes:
    # ese singleton puede observarse a medio construir según el orden de import
    # del arnés (import re-entrante vía db/connection.py). El comportamiento
    # runtime de los endpoints ya está cubierto por los _app()-tests de arriba.
    import inspect

    import btc_api
    assert "include_router(plan_router)" in inspect.getsource(btc_api)


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


# ── Task A1: metadata de paredes (toques / piso) ──────────────────────────────


def _zonas_paredes():
    # soporte_piso (sl_zona): precio_alto < entry=0.419  → el más cercano abajo
    # soporte_entry (entry_zone): precio_bajo ≤ 0.419 ≤ precio_alto  → contiene el entry
    # Usamos entry_zone independiente del sl_zona para distinguir ambos campos.
    # derive_plan toma sl_zona = max(soportes con precio_alto < entry, key=centro)
    # Con entry=0.419 y soporte_piso.precio_alto=0.398 < 0.419  → sl_zona.centro=0.392
    # Con soporte_entry.precio_bajo=0.410 ≤ 0.419 ≤ 0.425=precio_alto → entry_zone presente
    return [
        {"tipo": "soporte", "precio_bajo": 0.388, "precio_alto": 0.398, "centro": 0.392, "toques": 5, "confluencia_redondo": []},
        {"tipo": "soporte", "precio_bajo": 0.410, "precio_alto": 0.425, "centro": 0.417, "toques": 3, "confluencia_redondo": []},
        {"tipo": "resistencia", "precio_bajo": 0.445, "precio_alto": 0.451, "centro": 0.448, "toques": 2, "confluencia_redondo": []},
        {"tipo": "resistencia", "precio_bajo": 0.470, "precio_alto": 0.478, "centro": 0.474, "toques": 4, "confluencia_redondo": []},
    ]


def test_plan_payload_incluye_metadata_de_paredes():
    plan = derive_plan(_zonas_paredes(), 0.419)
    p = _plan_payload(plan)
    # rungs: cada rung expone su zona_origen
    assert p["rungs"][0]["zona"]["toques"] == 2
    assert p["rungs"][0]["zona"]["centro"] == 0.448
    # sl_piso: el soporte más cercano por debajo del entry (precio_alto < entry)
    # soporte_piso: precio_alto=0.398 < 0.419 → sl_zona.centro=0.392
    assert p["sl_piso"]["centro"] == 0.392
    assert p["sl_piso"]["precio_bajo"] == 0.388
    # campos legacy intactos
    assert p["sl_plan"] == plan.sl_price
    assert p["entry"] == plan.entry_price
    # entry_zone: la zona de soporte que abarca el entry (precio_bajo ≤ entry ≤ precio_alto)
    assert p["entry_zone"]["toques"] == 3


# ── Task A2: LiveSnapshot / frescura en el contrato ──────────────────────────


def test_vista_emite_frescura_en_el_contrato(monkeypatch):
    from datetime import datetime, timezone
    import api.plan as plan_api
    now = datetime.now(timezone.utc).isoformat()
    fake_row = {
        "plan_json": '{"entry_price":0.419,"entry_zone":null,"sl_price":0.385,"rungs":[],"runner_frac":0.05,"sl_zona":null}',
        "rungs_llenos_json": "[]", "be_movido": 0, "estado_vivo": "activo",
        "sl_actual": 0.385, "fase": "CONFIRMED", "size_restante_frac": 1.0, "updated_at": now,
    }
    import contextlib
    monkeypatch.setattr(plan_api, "db_get_active_state", lambda con, **kw: fake_row)
    monkeypatch.setattr(plan_api, "snapshot_connection", lambda: contextlib.nullcontext(None))
    out = plan_api.vista("ADAUSDT", tenant_id=1)
    assert out["frescura"]["estado"] == "fresco"
    assert out["frescura"]["generated_at"] == now
    assert out["estado_vivo"] == "activo"
