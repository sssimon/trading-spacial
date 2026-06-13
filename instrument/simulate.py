"""Simulador determinista del plan (instrumento F2) — puro, sin red, sin DB.

Corre el plan derivado en piloto automático sobre velas diarias, generando los
eventos que alimenta la MISMA máquina de estados de F1. Regla de cierre
CONSERVADORA: si una vela toca TP y SL, el SL gana (pesimista, cota inferior
auditable — roster unánime A, spec §1). `resolve_fills` está AISLADO tras una
firma estable para que el swap futuro a intradía sea un cambio de implementación.
Spec §3/§4."""
from __future__ import annotations

from dataclasses import replace

from instrument.lifecycle import LifecycleState, step


def resolve_fills(plan, state: LifecycleState, candle: dict) -> list[dict]:
    """Eventos que dispara una vela diaria dado el estado actual. Puro.

    1. SL primero (pesimista): si el SL está armado y la vela lo toca → STOP_HIT.
    2. Si no: cada rung no-lleno con high ≥ tp_price (orden ascendente) → RUNG_FILLED;
       tras llenarse el rung 0 → SL_MOVED a entry (regla BE del plan).

    Nota: SL_MOVED se agrupa DESPUÉS de todos los RUNG_FILLED de la misma vela; el swap
    a intradía necesitará hilarlo distinto."""
    low = float(candle["low"])
    high = float(candle["high"])

    # sl_actual == 0.0 = SL aún no armado; se omite hasta que el simulador lo siembre.
    if state.sl_actual > 0 and low <= state.sl_actual:
        return [{"tipo": "STOP_HIT", "procedencia": "observado"}]

    events: list[dict] = []
    rung0 = False
    for i, r in enumerate(plan.rungs):
        if i in state.rungs_llenos:
            continue
        if high >= r.tp_price:
            events.append({"tipo": "RUNG_FILLED", "order_id": f"sim-r{i}",
                           "rung_index": i, "procedencia": "observado"})
            if i == 0:
                rung0 = True
    if rung0:
        events.append({"tipo": "SL_MOVED", "nuevo_sl": plan.entry_price,
                       "procedencia": "observado"})
    return events


def simulate_plan(plan, candles: list[dict]) -> tuple[list[dict], LifecycleState]:
    """Recorre las velas diarias desde la entrada; cada vela → resolve_fills → step.
    Para al CLOSED o, si se agotan las velas sin cerrar, emite SIM_END (divergencia
    honesta: el plan habría aguantado más que los datos disponibles). Puro. Spec §4."""
    confirm = {"tipo": "PLAN_CONFIRMED", "procedencia": "observado"}
    state = step(LifecycleState(plan_id=0), confirm, plan)
    # PLAN_CONFIRMED deja sl_actual=0 a propósito (en F1 vivo el SL lo arma el
    # operador); aquí, sin operador en el loop, el simulador siembra el SL del plan.
    state = replace(state, sl_actual=plan.sl_price)
    events: list[dict] = [confirm]

    for candle in candles:
        for e in resolve_fills(plan, state, candle):
            events.append(e)
            state = step(state, e, plan)
            if state.fase == "CLOSED":
                break
        if state.fase == "CLOSED":
            break

    if state.fase != "CLOSED":
        sim_end = {"tipo": "SIM_END", "procedencia": "observado"}
        events.append(sim_end)
        state = step(state, sim_end, plan)
    return events, state
