"""API del plan vivo (instrumento F3a) — gate (derive/confirm) + vista (pull).

PULL-ONLY: ningún endpoint emite push ni instrucción. Read-only sobre positions;
escribe solo lifecycle_states. La red (D.1) corre fuera de tx. Spec §4/§6/§9."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query

from auth.dependencies import get_current_tenant_id
from screener.sr_levels import detect_levels
from instrument.plan import derive_plan
from instrument.lifecycle import LifecycleState
from db.lifecycle_states import db_put_state, db_get_active_state, plan_from_json
from db.transaction import snapshot_connection, transaction

log = logging.getLogger("api.plan")
router = APIRouter(tags=["plan"])


def _zonas_now(symbol: str) -> list[dict]:
    """Zonas de D.1 con velas diarias hasta ahora (red, fuera de tx). Aislado
    para mockear; reutiliza el fetch del endpoint de niveles."""
    from api.levels import _fetch_daily_bars
    return detect_levels(_fetch_daily_bars(symbol))


def _plan_payload(plan) -> dict:
    return {"entry": plan.entry_price,
            "sl_plan": plan.sl_price,
            "rungs": [{"tp_price": r.tp_price, "size_frac": r.size_frac} for r in plan.rungs],
            "runner_frac": plan.runner_frac,
            "entry_zone": plan.entry_zone}


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
    zonas = _zonas_now(symbol.upper())
    return _plan_payload(derive_plan(zonas, entry_price))


@router.post("/plan/confirm", summary="Confirma el plan revisado → crea la fila viva")
def confirm(payload: dict, tenant_id: int = Depends(get_current_tenant_id)) -> dict:
    """El operador confirma en frío. Crea la fila lifecycle_states. La red (D.1)
    corre FUERA de la tx corta del insert."""
    symbol = str(payload["symbol"]).upper()
    entry_price = float(payload["entry_price"])
    position_id = payload.get("position_id")

    zonas = _zonas_now(symbol)
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
    symbol = symbol.upper()
    with snapshot_connection() as con:
        con.row_factory = sqlite3.Row
        row = db_get_active_state(con, tenant_id=tenant_id, symbol=symbol)
    if row is None:
        return {"symbol": symbol, "estado_vivo": None}
    plan = plan_from_json(row["plan_json"])
    rungs_llenos = json.loads(row["rungs_llenos_json"])
    hechos = construir_hechos(rungs_llenos=rungs_llenos, be_movido=bool(row["be_movido"]),
                              estado_vivo=row["estado_vivo"], sl_actual=row["sl_actual"],
                              sl_plan=plan.sl_price)
    return {
        "symbol": symbol, "estado_vivo": row["estado_vivo"],
        "plan": _plan_payload(plan),
        "realidad": {"fase": row["fase"], "rungs_llenos": rungs_llenos,
                     "sl_actual": row["sl_actual"], "be_movido": bool(row["be_movido"]),
                     "size_restante_frac": row["size_restante_frac"]},
        "hechos": hechos,
    }
