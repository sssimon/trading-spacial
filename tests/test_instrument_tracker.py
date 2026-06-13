"""Tests del detector/tracker en vivo (instrumento F3a). Puro: sin red, sin DB. Spec §5."""
from instrument.plan import derive_plan
from instrument.lifecycle import LifecycleState
from instrument.tracker import detect_transitions


def _z(tipo, bajo, alto, centro):
    return {"tipo": tipo, "precio_bajo": bajo, "precio_alto": alto,
            "centro": centro, "toques": 3, "confluencia_redondo": []}


def _plan():
    zonas = [_z("soporte", 94, 96, 95),
             _z("resistencia", 104, 106, 105),
             _z("resistencia", 109, 111, 110)]
    return derive_plan(zonas, entry_price=100.0)


def _armed(p, **kw):
    return LifecycleState(plan_id=0, fase="CONFIRMED", sl_actual=p.sl_price, **kw)


def _tp(price, oid, qty=1.0):
    return {"kind": "TP", "price": price, "qty": qty, "order_id": oid}


def _sl(price, oid, qty=1.0):
    return {"kind": "SL", "price": price, "qty": qty, "order_id": oid}


def test_tp_desaparece_con_caida_de_qty_es_rung_filled():
    p = _plan()
    prev = [_tp(105, 11), _sl(p.sl_price, 99)]
    curr = [_sl(p.sl_price, 99)]
    evs = detect_transitions(p, _armed(p), prev, curr, prev_qty=1.0, curr_qty=0.5)
    rung = [e for e in evs if e["tipo"] == "RUNG_FILLED"]
    assert len(rung) == 1 and rung[0]["rung_index"] == 0 and rung[0]["order_id"] == "11"


def test_tp_desaparece_sin_caida_de_qty_es_cancelacion():
    p = _plan()
    prev = [_tp(105, 11), _sl(p.sl_price, 99)]
    curr = [_sl(p.sl_price, 99)]
    evs = detect_transitions(p, _armed(p), prev, curr, prev_qty=1.0, curr_qty=1.0)
    assert not any(e["tipo"] == "RUNG_FILLED" for e in evs)


def test_rung_idempotente_por_order_id():
    p = _plan()
    prev = [_tp(105, 11)]
    curr = []
    st = _armed(p, consumed_order_ids=frozenset({"11"}))
    evs = detect_transitions(p, st, prev, curr, prev_qty=1.0, curr_qty=0.5)
    assert not any(e["tipo"] == "RUNG_FILLED" for e in evs)


def test_sl_cambia_a_entry_es_sl_moved():
    p = _plan()
    prev = [_sl(p.sl_price, 99)]
    curr = [_sl(p.entry_price, 99)]
    evs = detect_transitions(p, _armed(p), prev, curr, prev_qty=1.0, curr_qty=1.0)
    sl = [e for e in evs if e["tipo"] == "SL_MOVED"]
    assert len(sl) == 1 and sl[0]["nuevo_sl"] == p.entry_price


def test_qty_cero_es_stop_hit():
    p = _plan()
    evs = detect_transitions(p, _armed(p), [_sl(p.sl_price, 99)], [], prev_qty=1.0, curr_qty=0.0)
    assert any(e["tipo"] == "STOP_HIT" for e in evs)


# ── Task 2: advance_live ────────────────────────────────────────────────────
from instrument.tracker import advance_live


def test_advance_live_aplica_rung_y_avanza():
    p = _plan()
    prev = [_tp(105, 11), _sl(p.sl_price, 99)]
    curr = [_sl(p.sl_price, 99)]
    new, events = advance_live(p, _armed(p), prev, curr, prev_qty=1.0, curr_qty=0.5)
    assert 0 in new.rungs_llenos
    assert "11" in new.consumed_order_ids
    assert any(e["tipo"] == "RUNG_FILLED" for e in events)
    assert new.fase == "RUNNING"


def test_advance_live_cierre_lleva_a_closed():
    p = _plan()
    new, events = advance_live(p, _armed(p), [_sl(p.sl_price, 99)], [],
                               prev_qty=1.0, curr_qty=0.0)
    assert new.fase == "CLOSED"
    assert new.close_reason in ("SL_HIT", "BE_HIT")


def test_advance_live_sin_cambios_no_avanza():
    p = _plan()
    obs = [_sl(p.sl_price, 99)]
    new, events = advance_live(p, _armed(p), obs, obs, prev_qty=1.0, curr_qty=1.0)
    assert events == []
    assert new.fase == "CONFIRMED"


# ── Task 6: finalize_conduct ────────────────────────────────────────────────
from instrument.tracker import finalize_conduct


def test_finalize_conduct_al_cierre():
    p = _plan()
    events = [
        {"tipo": "RUNG_FILLED", "order_id": "11", "rung_index": 0, "procedencia": "observado"},
        {"tipo": "SL_MOVED", "nuevo_sl": p.entry_price, "procedencia": "observado"},
        {"tipo": "STOP_HIT", "procedencia": "observado"},
    ]
    from instrument.lifecycle import LifecycleState, step
    st = LifecycleState(plan_id=0, fase="CONFIRMED", sl_actual=p.sl_price)
    for e in events:
        st = step(st, e, p)
    c = finalize_conduct(p, events, st, entry_price=100.0,
                         entry_ts="2026-06-10T00:00:00+00:00",
                         exit_ts="2026-06-12T00:00:00+00:00")
    assert c["adherencia_be"] is True
    assert c["rungs_honrados"] == 1
    assert c["procedencia"] == "observado"
    assert "hold_hours" in c
