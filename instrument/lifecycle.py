"""Máquina de estados del lifecycle (instrumento, Fase 1) — reductor PURO.

step(estado, evento, plan) → estado. Idempotente por order_id (un RUNG_FILLED
repetido es no-op). NO llama a Binance, NO escribe positions.status, NO toca
PositionClosure — el → CLOSED es del estado del PLAN, no del cierre real de la
posición (spec §5). Cada evento lleva procedencia 'observado'|'declarado'."""
from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class LifecycleState:
    plan_id: int
    fase: str = "PLANNED"                       # PLANNED|CONFIRMED|RUNNING|CLOSED
    rungs_llenos: frozenset = field(default_factory=frozenset)
    consumed_order_ids: frozenset = field(default_factory=frozenset)
    sl_actual: float = 0.0
    be_movido: bool = False
    size_restante_frac: float = 1.0
    close_reason: str | None = None


def step(state: LifecycleState, event: dict, plan) -> LifecycleState:
    """Aplica un evento. Estado terminal CLOSED: todo evento es no-op."""
    if state.fase == "CLOSED":
        return state
    tipo = event["tipo"]

    if tipo == "PLAN_CONFIRMED":
        return state if state.fase != "PLANNED" else replace(state, fase="CONFIRMED")

    if tipo == "RUNG_FILLED":
        oid = event["order_id"]
        if oid in state.consumed_order_ids:
            return state   # idempotencia por order_id
        i = event["rung_index"]
        frac = plan.rungs[i].size_frac if 0 <= i < len(plan.rungs) else 0.0
        return replace(
            state, fase="RUNNING",
            rungs_llenos=state.rungs_llenos | {i},
            consumed_order_ids=state.consumed_order_ids | {oid},
            size_restante_frac=max(0.0, state.size_restante_frac - frac),
        )

    if tipo == "SL_MOVED":
        nuevo = event["nuevo_sl"]
        return replace(state, sl_actual=nuevo,
                       be_movido=state.be_movido or (nuevo == plan.entry_price))

    if tipo == "STOP_HIT":
        return replace(state, fase="CLOSED",
                       close_reason="BE_HIT" if state.be_movido else "SL_HIT")

    if tipo == "MANUAL_EXIT":
        return replace(state, fase="CLOSED", close_reason="MANUAL")

    if tipo == "POSITION_GONE":
        return replace(state, fase="CLOSED", close_reason="RECONCILED")

    if tipo == "SIM_END":
        return replace(state, fase="CLOSED", close_reason="SIM_END")

    return state   # evento desconocido: ignorado
