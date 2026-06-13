"""Tests del domicilio lifecycle_states (instrumento F3a). Spec §3."""
import sqlite3

from instrument.plan import derive_plan
from instrument.lifecycle import LifecycleState
from db.lifecycle_states import (
    plan_to_json, plan_from_json, state_to_row, state_from_row,
    db_put_state, db_get_active_state,
)


def _z(tipo, bajo, alto, centro):
    return {"tipo": tipo, "precio_bajo": bajo, "precio_alto": alto,
            "centro": centro, "toques": 3, "confluencia_redondo": []}


def _plan():
    return derive_plan([_z("soporte", 94, 96, 95), _z("resistencia", 104, 106, 105)],
                       entry_price=100.0)


def test_plan_json_roundtrip():
    p = _plan()
    p2 = plan_from_json(plan_to_json(p))
    assert p2.entry_price == p.entry_price
    assert [r.tp_price for r in p2.rungs] == [r.tp_price for r in p.rungs]
    assert p2.sl_price == p.sl_price and p2.runner_frac == p.runner_frac


def test_state_row_roundtrip_preserva_frozensets():
    s = LifecycleState(plan_id=0, fase="RUNNING",
                       rungs_llenos=frozenset({0}), consumed_order_ids=frozenset({"11"}),
                       sl_actual=99.0, be_movido=True, size_restante_frac=0.5)
    s2 = state_from_row(state_to_row(s))
    assert s2.rungs_llenos == frozenset({0})
    assert s2.consumed_order_ids == frozenset({"11"})
    assert s2.be_movido is True and s2.fase == "RUNNING"


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE lifecycle_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT, position_id INTEGER,
            symbol TEXT NOT NULL, tenant_id INTEGER NOT NULL,
            estado_vivo TEXT NOT NULL CHECK (estado_vivo IN ('activo','cerrado','incierto')),
            plan_json TEXT NOT NULL, entry_price REAL NOT NULL, qty_original REAL,
            fase TEXT NOT NULL, rungs_llenos_json TEXT NOT NULL,
            consumed_orders_json TEXT NOT NULL, sl_actual REAL, be_movido INTEGER NOT NULL,
            size_restante_frac REAL, events_json TEXT NOT NULL DEFAULT '[]',
            prev_observed_json TEXT, prev_qty REAL,
            confirmed_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE (tenant_id, symbol, confirmed_at))""")
    return con


def test_put_y_get_active():
    con = _con()
    p = _plan()
    s = LifecycleState(plan_id=0, fase="CONFIRMED", sl_actual=p.sl_price)
    db_put_state(con, position_id=7, symbol="BTCUSDT", tenant_id=2, estado_vivo="activo",
                 plan=p, state=s, entry_price=100.0, qty_original=1.0, events=[],
                 prev_observed=[], prev_qty=1.0, confirmed_at="2026-06-13T00:00:00+00:00",
                 updated_at="2026-06-13T00:00:00+00:00")
    row = db_get_active_state(con, tenant_id=2, symbol="BTCUSDT")
    assert row is not None and row["symbol"] == "BTCUSDT" and row["estado_vivo"] == "activo"
