"""Tests del reductor del lifecycle (instrumento, Fase 1). Puro. Spec §5."""
from instrument.lifecycle import LifecycleState, step
from instrument.plan import derive_plan


def _z(tipo, bajo, alto, centro):
    return {"tipo": tipo, "precio_bajo": bajo, "precio_alto": alto,
            "centro": centro, "toques": 3, "confluencia_redondo": []}


def _plan():
    zonas = [_z("soporte", 94, 96, 95),
             _z("resistencia", 104, 106, 105),
             _z("resistencia", 109, 111, 110)]
    return derive_plan(zonas, entry_price=100.0)


def _s0():
    return LifecycleState(plan_id=1)


def test_confirmar_pasa_a_confirmed():
    s = step(_s0(), {"tipo": "PLAN_CONFIRMED", "procedencia": "declarado"}, _plan())
    assert s.fase == "CONFIRMED"


def test_rung_filled_marca_y_resta_size():
    p = _plan()
    s = step(_s0(), {"tipo": "PLAN_CONFIRMED", "procedencia": "observado"}, p)
    s = step(s, {"tipo": "RUNG_FILLED", "order_id": "A", "rung_index": 0,
                 "procedencia": "observado"}, p)
    assert s.fase == "RUNNING"
    assert 0 in s.rungs_llenos
    assert "A" in s.consumed_order_ids
    assert abs(s.size_restante_frac - (1.0 - p.rungs[0].size_frac)) < 1e-9


def test_rung_filled_es_idempotente_por_order_id():
    p = _plan()
    s = step(_s0(), {"tipo": "PLAN_CONFIRMED", "procedencia": "observado"}, p)
    e = {"tipo": "RUNG_FILLED", "order_id": "A", "rung_index": 0, "procedencia": "observado"}
    s1 = step(s, e, p)
    s2 = step(s1, e, p)
    assert s2.size_restante_frac == s1.size_restante_frac
    assert s2.rungs_llenos == s1.rungs_llenos


def test_sl_movido_a_entry_marca_break_even():
    p = _plan()
    s = step(_s0(), {"tipo": "SL_MOVED", "nuevo_sl": p.entry_price,
                     "procedencia": "observado"}, p)
    assert s.be_movido is True


def test_stop_hit_tras_be_cierra_como_be_hit():
    p = _plan()
    s = step(_s0(), {"tipo": "SL_MOVED", "nuevo_sl": p.entry_price, "procedencia": "observado"}, p)
    s = step(s, {"tipo": "STOP_HIT", "procedencia": "observado"}, p)
    assert s.fase == "CLOSED"
    assert s.close_reason == "BE_HIT"


def test_stop_hit_sin_be_cierra_como_sl_hit():
    p = _plan()
    s = step(_s0(), {"tipo": "STOP_HIT", "procedencia": "observado"}, p)
    assert s.close_reason == "SL_HIT"


def test_manual_exit_cierra_fuera_de_plan():
    s = step(_s0(), {"tipo": "MANUAL_EXIT", "procedencia": "declarado"}, _plan())
    assert s.fase == "CLOSED" and s.close_reason == "MANUAL"


def test_eventos_tras_closed_son_noop():
    p = _plan()
    s = step(_s0(), {"tipo": "MANUAL_EXIT", "procedencia": "declarado"}, p)
    s2 = step(s, {"tipo": "RUNG_FILLED", "order_id": "B", "rung_index": 0,
                  "procedencia": "observado"}, p)
    assert s2 == s
