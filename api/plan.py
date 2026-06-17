"""API del plan vivo (instrumento F3a) — gate (derive/confirm) + vista (pull).

PULL-ONLY: ningún endpoint emite push ni instrucción. Read-only sobre positions;
escribe solo lifecycle_states. La red (D.1) corre fuera de tx. Spec §4/§6/§9."""
from __future__ import annotations

import json
import logging
import requests
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.deps import verify_api_key
from api.levels import BinanceUnavailable
from auth.dependencies import get_current_tenant_id
from screener.sr_levels import detect_levels
from instrument.plan import derive_plan
from instrument.lifecycle import LifecycleState
from db.lifecycle_states import db_put_state, db_get_active_state, plan_from_json
from db.conduct_episodes import db_get_latest_episode
from db.transaction import snapshot_connection, transaction
from freshness import LiveSnapshot

log = logging.getLogger("api.plan")
router = APIRouter(tags=["plan"])

PLAN_FRESCURA_UMBRAL_SEG = 900.0


class PlanConfirmRequest(BaseModel):
    symbol: str
    entry_price: float
    position_id: int | None = None


def _zonas_now(symbol: str) -> list[dict]:
    """Zonas de D.1 con velas diarias hasta ahora (red, fuera de tx). Aislado
    para mockear; reutiliza el fetch del endpoint de niveles."""
    from api.levels import _fetch_daily_bars
    return detect_levels(_fetch_daily_bars(symbol))


def _zona_meta(z: dict | None) -> dict | None:
    """Extrae los campos visuales de una zona (paredes). Devuelve None si no hay zona."""
    if z is None:
        return None
    return {
        "centro": z["centro"],
        "precio_bajo": z["precio_bajo"],
        "precio_alto": z["precio_alto"],
        "toques": z["toques"],
    }


def _plan_payload(plan) -> dict:
    return {
        "entry": plan.entry_price,
        "sl_plan": plan.sl_price,
        "sl_piso": _zona_meta(plan.sl_zona),  # soporte inmediato que fija el SL (Task A1)
        "rungs": [
            {"tp_price": r.tp_price, "size_frac": r.size_frac, "zona": _zona_meta(r.zona_origen)}
            for r in plan.rungs
        ],
        "runner_frac": plan.runner_frac,
        "entry_zone": plan.entry_zone,
    }


def construir_hechos(*, rungs_llenos: list, be_movido: bool, estado_vivo: str,
                     sl_actual, sl_plan) -> list[str]:
    """HECHOS de 𝓕ₜ — lo que ES verdad. NUNCA instrucciones (Axiom-0, spec §0).
    Sin imperativos: el instrumento queda fuera del término que mide."""
    hechos: list[str] = []
    if estado_vivo == "incierto":
        hechos.append("transición sin confirmar — revisá en Binance")
    for i in sorted(rungs_llenos):
        hechos.append(f"TP{i + 1} se llenó")
    if be_movido:
        hechos.append("tu SL está en break-even")
    elif sl_actual is not None and sl_plan is not None:
        if sl_actual <= sl_plan * (1 + 1e-9):
            hechos.append("tu SL sigue debajo de la zona")
        else:
            hechos.append("tu SL está por encima del nivel del plan")
    return hechos


@router.get("/plan/derive/{symbol}", summary="Deriva el plan desde D.1 (NO persiste)")
def derive(symbol: str, entry_price: float = Query(...)) -> dict:
    """El operador revisa el plan antes de confirmarlo. NO escribe nada."""
    symbol = symbol.upper()[:20]
    try:
        zonas = _zonas_now(symbol)
    except (requests.RequestException, BinanceUnavailable, RuntimeError) as e:
        log.warning("PLAN_DERIVE_NO_DISPONIBLE symbol=%s causa=%s", symbol, e)
        raise HTTPException(status_code=503, detail="Binance no disponible — reintentá")
    return _plan_payload(derive_plan(zonas, entry_price))


@router.post("/plan/confirm", dependencies=[Depends(verify_api_key)],  # TODO(auth-cleanup): añadir require_role("admin") cuando se barra el resto de writes
             summary="Confirma el plan revisado → crea la fila viva")
def confirm(req: PlanConfirmRequest, tenant_id: int = Depends(get_current_tenant_id)) -> dict:
    """El operador confirma en frío. Crea la fila lifecycle_states. La red (D.1)
    corre FUERA de la tx corta del insert."""
    symbol = req.symbol.upper()[:20]
    entry_price = req.entry_price
    position_id = req.position_id

    if position_id is not None:
        with snapshot_connection() as con:
            r = con.execute("SELECT origin FROM positions WHERE id=? AND tenant_id=?",
                            (position_id, tenant_id)).fetchone()
        if r is not None and r[0] == "AUTO_DERIVED":
            raise HTTPException(status_code=422,
                                detail="posición AUTO_DERIVED no admite plan de conducta (BNC-12)")

    try:
        zonas = _zonas_now(symbol)
    except (requests.RequestException, BinanceUnavailable, RuntimeError) as e:
        log.warning("PLAN_DERIVE_NO_DISPONIBLE symbol=%s causa=%s", symbol, e)
        raise HTTPException(status_code=503, detail="Binance no disponible — reintentá")

    plan = derive_plan(zonas, entry_price)
    state = LifecycleState(plan_id=0, fase="CONFIRMED", sl_actual=plan.sl_price)
    now = datetime.now(timezone.utc).isoformat()

    with transaction() as con:
        db_put_state(con, position_id=position_id, symbol=symbol, tenant_id=tenant_id,
                     estado_vivo="activo", plan=plan, state=state, entry_price=entry_price,
                     qty_original=None, events=[], prev_observed=[], prev_qty=None,
                     confirmed_at=now, updated_at=now)
    return {"symbol": symbol, "estado_vivo": "activo", "plan": _plan_payload(plan)}


@router.get("/plan/{symbol}", summary="Estado vivo del plan (pull, solo hechos)")
def vista(symbol: str, tenant_id: int = Depends(get_current_tenant_id)) -> dict:
    """PULL: el estado vivo. Hechos, nunca instrucciones. Sin plan activo →
    estado_vivo None (la UI muestra 'sin plan')."""
    symbol = symbol.upper()[:20]
    with snapshot_connection() as con:
        row = db_get_active_state(con, tenant_id=tenant_id, symbol=symbol)
    if row is None:
        return {"symbol": symbol, "estado_vivo": None}
    plan = plan_from_json(row["plan_json"])
    rungs_llenos = json.loads(row["rungs_llenos_json"])
    hechos = construir_hechos(rungs_llenos=rungs_llenos, be_movido=bool(row["be_movido"]),
                              estado_vivo=row["estado_vivo"], sl_actual=row["sl_actual"],
                              sl_plan=plan.sl_price)
    payload = {
        "symbol": symbol, "estado_vivo": row["estado_vivo"],
        "plan": _plan_payload(plan),
        "realidad": {"fase": row["fase"], "rungs_llenos": rungs_llenos,
                     "sl_actual": row["sl_actual"], "be_movido": bool(row["be_movido"]),
                     "size_restante_frac": row["size_restante_frac"]},
        "hechos": hechos,
    }
    return LiveSnapshot(
        payload=payload,
        generated_at=row["updated_at"],
        umbral_seg=PLAN_FRESCURA_UMBRAL_SEG,
    ).to_response()


# ── Task A3: conducta del último cierre (sin PnL) ────────────────────────────

# Mapeo: campo real en conduct_episodes → etiqueta en tuteo venezolano.
# rungs_honrados es int (conteo de peldaños llenados) — truthy cuando > 0.
# adherencia_be puede ser None (inaplicable si TP1 no se llenó) — se trata como falsy.
_CONDUCT_FIELDS: list[tuple[str, str]] = [
    ("entry_en_zona",  "Entraste en la zona"),
    ("sl_respetado",   "Respetaste el stop"),
    ("adherencia_be",  "Moviste a break-even"),
    ("rungs_honrados", "Honraste los peldaños"),
    ("cierre_en_plan", "Cerraste según el plan"),
]


@router.get("/plan/{symbol}/conducta", summary="Conducta del último cierre (sin PnL)")
def conducta(symbol: str, tenant_id: int = Depends(get_current_tenant_id)) -> dict:
    """Lee el EpisodioDeConducción del último cierre para tenant+symbol.
    Hechos de conducta únicamente — NUNCA PnL. Sin episodio → estado_vivo None."""
    symbol = symbol.upper()[:20]
    with snapshot_connection() as con:
        ep = db_get_latest_episode(con, tenant_id=tenant_id, symbol=symbol)
    if ep is None:
        return {"symbol": symbol, "estado_vivo": None}

    campos: list[dict] = []
    all_bool_ok = True
    for field, label in _CONDUCT_FIELDS:
        val = ep.get(field)
        ok = "si" if val else "no"
        if ok == "no":
            all_bool_ok = False
        campos.append({"k": label, "ok": ok})

    hold_hours = ep.get("hold_hours")
    if hold_hours is not None:
        campos.append({"k": "Cuánto aguantaste", "ok": "dato", "v": f"{round(hold_hours)} h"})

    titular = (
        "Honraste el plan que aprobaste."
        if all_bool_ok
        else "Esta vez te saliste del plan. Sin reproche — solo el espejo."
    )

    return {
        "symbol": symbol,
        "estado_vivo": "cerrado",
        "titular": titular,
        "campos": campos,
    }
