"""Domicilio del estado vivo del plan (instrumento F3a) — serialización + SQL.

Serializa Plan/LifecycleState ↔ JSON (frozensets ↔ listas) y persiste una fila
por plan activo. Helpers SQL puros (reciben `con`). Spec §3."""
from __future__ import annotations

import json
import sqlite3

from instrument.plan import Plan, Rung
from instrument.lifecycle import LifecycleState

# Columnas explícitas (mismo patrón que db/conduct_episodes.py _COLS).
# Incluye close_reason para que los callers reciban dicts independientes
# del row_factory configurado en la conexión.
_COLS = ("id, position_id, symbol, tenant_id, estado_vivo, plan_json, "
         "entry_price, qty_original, fase, rungs_llenos_json, "
         "consumed_orders_json, sl_actual, be_movido, size_restante_frac, "
         "close_reason, events_json, prev_observed_json, prev_qty, "
         "confirmed_at, updated_at")


def plan_to_json(plan: Plan) -> str:
    return json.dumps({
        "entry_price": plan.entry_price, "entry_zone": plan.entry_zone,
        "sl_price": plan.sl_price, "runner_frac": plan.runner_frac,
        "rungs": [{"tp_price": r.tp_price, "size_frac": r.size_frac,
                   "zona_origen": r.zona_origen} for r in plan.rungs],
    })


def plan_from_json(s: str) -> Plan:
    d = json.loads(s)
    try:
        rungs = tuple(Rung(tp_price=r["tp_price"], size_frac=r["size_frac"],
                           zona_origen=r["zona_origen"]) for r in d["rungs"])
        return Plan(entry_price=d["entry_price"], entry_zone=d["entry_zone"],
                    sl_price=d["sl_price"], rungs=rungs, runner_frac=d["runner_frac"])
    except KeyError as e:
        raise ValueError(f"plan_from_json: campo faltante {e} en el plan almacenado") from e


def state_to_row(state: LifecycleState) -> dict:
    return {
        "fase": state.fase,
        "rungs_llenos_json": json.dumps(sorted(state.rungs_llenos)),
        "consumed_orders_json": json.dumps(sorted(state.consumed_order_ids)),
        "sl_actual": state.sl_actual, "be_movido": int(state.be_movido),
        "size_restante_frac": state.size_restante_frac,
        "close_reason": state.close_reason,
    }


def state_from_row(row: dict) -> LifecycleState:
    return LifecycleState(
        plan_id=0, fase=row["fase"],
        rungs_llenos=frozenset(json.loads(row["rungs_llenos_json"])),
        consumed_order_ids=frozenset(json.loads(row["consumed_orders_json"])),
        sl_actual=row["sl_actual"], be_movido=bool(row["be_movido"]),
        size_restante_frac=row["size_restante_frac"],
        close_reason=row["close_reason"],
    )


def db_put_state(con: sqlite3.Connection, *, position_id, symbol, tenant_id, estado_vivo,
                 plan, state, entry_price, qty_original, events, prev_observed, prev_qty,
                 confirmed_at, updated_at) -> None:
    r = state_to_row(state)
    # Supersede cualquier fila activa/incierta previa para el mismo (tenant, symbol)
    # de modo que db_list_active nunca devuelva dos filas para el mismo símbolo.
    con.execute(
        "UPDATE lifecycle_states SET estado_vivo='cerrado', updated_at=? "
        "WHERE tenant_id=? AND symbol=? AND estado_vivo IN ('activo','incierto')",
        (updated_at, tenant_id, symbol))
    con.execute(
        """INSERT INTO lifecycle_states
           (position_id, symbol, tenant_id, estado_vivo, plan_json, entry_price,
            qty_original, fase, rungs_llenos_json, consumed_orders_json, sl_actual,
            be_movido, size_restante_frac, close_reason, events_json,
            prev_observed_json, prev_qty, confirmed_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (position_id, symbol, tenant_id, estado_vivo, plan_to_json(plan), entry_price,
         qty_original, r["fase"], r["rungs_llenos_json"], r["consumed_orders_json"],
         r["sl_actual"], r["be_movido"], r["size_restante_frac"], r["close_reason"],
         json.dumps(events), json.dumps(prev_observed), prev_qty, confirmed_at, updated_at))


def db_get_active_state(con: sqlite3.Connection, *, tenant_id: int, symbol: str):
    cur = con.execute(
        f"SELECT {_COLS} FROM lifecycle_states WHERE tenant_id=? AND symbol=? "
        "AND estado_vivo IN ('activo','incierto') ORDER BY confirmed_at DESC LIMIT 1",
        (tenant_id, symbol))
    cols = [c[0] for c in cur.description]
    row = cur.fetchone()
    return dict(zip(cols, row)) if row else None


def db_list_active(con: sqlite3.Connection, *, tenant_id: int) -> list[dict]:
    cur = con.execute(
        f"SELECT {_COLS} FROM lifecycle_states "
        "WHERE tenant_id=? AND estado_vivo IN ('activo','incierto')",
        (tenant_id,))
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def db_update_state(con: sqlite3.Connection, *, row_id, estado_vivo, state, events,
                    prev_observed, prev_qty, updated_at) -> None:
    r = state_to_row(state)
    con.execute(
        """UPDATE lifecycle_states SET estado_vivo=?, fase=?, rungs_llenos_json=?,
               consumed_orders_json=?, sl_actual=?, be_movido=?, size_restante_frac=?,
               close_reason=?, events_json=?, prev_observed_json=?, prev_qty=?,
               updated_at=?
           WHERE id=?""",
        (estado_vivo, r["fase"], r["rungs_llenos_json"], r["consumed_orders_json"],
         r["sl_actual"], r["be_movido"], r["size_restante_frac"], r["close_reason"],
         json.dumps(events), json.dumps(prev_observed), prev_qty, updated_at, row_id))
