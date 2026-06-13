"""Tracker en vivo del instrumento (F3a) — puro, sin red, sin DB.

Deriva eventos de transición de los snapshots de observed_orders + qty y avanza
la máquina de F1. HONESTO sobre la resolución: un TP que desaparece pudo llenarse
(qty baja) o cancelarse (qty igual); la ambigüedad no se inventa. Idempotente por
order_id. Spec §5. NO emite alertas, NO instruye — solo deriva hechos."""
from __future__ import annotations

from instrument.conduct import compute_conduct
from instrument.lifecycle import LifecycleState, step

_TOL = 0.005   # 0.5%: proximidad de precio observado ↔ nivel del plan
_EPS = 1e-9


def _close(a: float, b: float) -> bool:
    return b != 0 and abs(a - b) / abs(b) <= _TOL


def detect_transitions(plan, state: LifecycleState, prev_observed: list[dict],
                       curr_observed: list[dict], prev_qty: float,
                       curr_qty: float) -> list[dict]:
    """Snapshots de observed_orders (prev/curr) + qty → eventos para step(). Puro."""
    proc = "observado"
    events: list[dict] = []
    curr_ids = {o["order_id"] for o in curr_observed}
    qty_dropped = curr_qty < prev_qty - _EPS

    if qty_dropped:
        for o in prev_observed:
            oid = str(o["order_id"])
            if o.get("kind") != "TP" or o["order_id"] in curr_ids:
                continue
            if oid in state.consumed_order_ids:
                continue
            for i, r in enumerate(plan.rungs):
                if i in state.rungs_llenos:
                    continue
                if _close(o["price"], r.tp_price):
                    events.append({"tipo": "RUNG_FILLED", "order_id": oid,
                                   "rung_index": i, "procedencia": proc})
                    break

    prev_sl = next((o for o in prev_observed if o.get("kind") == "SL"), None)
    curr_sl = next((o for o in curr_observed if o.get("kind") == "SL"), None)
    if prev_sl and curr_sl and not _close(prev_sl["price"], curr_sl["price"]):
        events.append({"tipo": "SL_MOVED", "nuevo_sl": float(curr_sl["price"]),
                       "procedencia": proc})

    if curr_qty <= 1e-8:
        events.append({"tipo": "STOP_HIT", "procedencia": proc})

    return events


def advance_live(plan, state: LifecycleState, prev_observed: list[dict],
                 curr_observed: list[dict], prev_qty: float,
                 curr_qty: float) -> tuple[LifecycleState, list[dict]]:
    """Detecta transiciones y avanza la máquina. Devuelve (estado_nuevo, eventos).
    Puro: compone detect_transitions + step de F1. Spec §6."""
    events = detect_transitions(plan, state, prev_observed, curr_observed,
                                prev_qty, curr_qty)
    for e in events:
        state = step(state, e, plan)
        if state.fase == "CLOSED":
            break
    return state, events


def finalize_conduct(plan, events: list[dict], final_state, *, entry_price: float,
                     entry_ts: str, exit_ts: str) -> dict:
    """Conducta al cierre con el libro de fills vivo (la comparación que F2
    difirió). Campo por campo contra el plan, procedencia 'observado'. NO PnL.
    Spec §7."""
    return compute_conduct(plan, events, final_state, entry_price=entry_price,
                           entry_ts=entry_ts, exit_ts=exit_ts, procedencia="observado")
