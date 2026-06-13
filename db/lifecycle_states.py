"""Domicilio del estado vivo del plan (instrumento F3a) — serialización + SQL.

Serializa Plan/LifecycleState ↔ JSON (frozensets ↔ listas) y persiste una fila
por plan activo. Helpers SQL puros (reciben `con`). Spec §3."""
from __future__ import annotations

import json
import sqlite3

from instrument.plan import Plan, Rung
from instrument.lifecycle import LifecycleState


def plan_to_json(plan: Plan) -> str:
    return json.dumps({
        "entry_price": plan.entry_price, "entry_zone": plan.entry_zone,
        "sl_price": plan.sl_price, "runner_frac": plan.runner_frac,
        "rungs": [{"tp_price": r.tp_price, "size_frac": r.size_frac,
                   "zona_origen": r.zona_origen} for r in plan.rungs],
    })


def plan_from_json(s: str) -> Plan:
    d = json.loads(s)
    rungs = tuple(Rung(tp_price=r["tp_price"], size_frac=r["size_frac"],
                       zona_origen=r["zona_origen"]) for r in d["rungs"])
    return Plan(entry_price=d["entry_price"], entry_zone=d["entry_zone"],
                sl_price=d["sl_price"], rungs=rungs, runner_frac=d["runner_frac"])


def state_to_row(state: LifecycleState) -> dict:
    return {
        "fase": state.fase,
        "rungs_llenos_json": json.dumps(sorted(state.rungs_llenos)),
        "consumed_orders_json": json.dumps(sorted(state.consumed_order_ids)),
        "sl_actual": state.sl_actual, "be_movido": int(state.be_movido),
        "size_restante_frac": state.size_restante_frac,
    }


def state_from_row(row) -> LifecycleState:
    def g(key):
        return row[key]
    return LifecycleState(
        plan_id=0, fase=g("fase"),
        rungs_llenos=frozenset(json.loads(g("rungs_llenos_json"))),
        consumed_order_ids=frozenset(json.loads(g("consumed_orders_json"))),
        sl_actual=g("sl_actual"), be_movido=bool(g("be_movido")),
        size_restante_frac=g("size_restante_frac"),
    )


def db_put_state(con: sqlite3.Connection, *, position_id, symbol, tenant_id, estado_vivo,
                 plan, state, entry_price, qty_original, events, prev_observed, prev_qty,
                 confirmed_at, updated_at) -> None:
    r = state_to_row(state)
    con.execute(
        """INSERT INTO lifecycle_states
           (position_id, symbol, tenant_id, estado_vivo, plan_json, entry_price,
            qty_original, fase, rungs_llenos_json, consumed_orders_json, sl_actual,
            be_movido, size_restante_frac, events_json, prev_observed_json, prev_qty,
            confirmed_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (position_id, symbol, tenant_id, estado_vivo, plan_to_json(plan), entry_price,
         qty_original, r["fase"], r["rungs_llenos_json"], r["consumed_orders_json"],
         r["sl_actual"], r["be_movido"], r["size_restante_frac"], json.dumps(events),
         json.dumps(prev_observed), prev_qty, confirmed_at, updated_at))


def db_get_active_state(con: sqlite3.Connection, *, tenant_id: int, symbol: str):
    cur = con.execute(
        "SELECT * FROM lifecycle_states WHERE tenant_id=? AND symbol=? "
        "AND estado_vivo IN ('activo','incierto') ORDER BY confirmed_at DESC LIMIT 1",
        (tenant_id, symbol))
    return cur.fetchone()


def db_list_active(con: sqlite3.Connection, *, tenant_id: int) -> list:
    cur = con.execute(
        "SELECT * FROM lifecycle_states WHERE tenant_id=? AND estado_vivo IN ('activo','incierto')",
        (tenant_id,))
    return cur.fetchall()


def db_update_state(con: sqlite3.Connection, *, row_id, estado_vivo, state, events,
                    prev_observed, prev_qty, updated_at) -> None:
    r = state_to_row(state)
    con.execute(
        """UPDATE lifecycle_states SET estado_vivo=?, fase=?, rungs_llenos_json=?,
               consumed_orders_json=?, sl_actual=?, be_movido=?, size_restante_frac=?,
               events_json=?, prev_observed_json=?, prev_qty=?, updated_at=?
           WHERE id=?""",
        (estado_vivo, r["fase"], r["rungs_llenos_json"], r["consumed_orders_json"],
         r["sl_actual"], r["be_movido"], r["size_restante_frac"], json.dumps(events),
         json.dumps(prev_observed), prev_qty, updated_at, row_id))
