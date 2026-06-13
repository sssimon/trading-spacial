"""Tests de compute_conduct (instrumento, Fase 1). Puro. Spec §7.

La conducta es INDEPENDIENTE del PnL: mide adherencia al plan, no si ganó."""
from instrument.plan import derive_plan
from instrument.lifecycle import LifecycleState, step
from instrument.conduct import compute_conduct


def _z(tipo, bajo, alto, centro):
    return {"tipo": tipo, "precio_bajo": bajo, "precio_alto": alto,
            "centro": centro, "toques": 3, "confluencia_redondo": []}


def _plan(entry=100.0):
    zonas = [_z("soporte", 99, 101, 100), _z("resistencia", 104, 106, 105),
             _z("resistencia", 109, 111, 110)]
    return derive_plan(zonas, entry_price=entry)


def _replay(plan, events):
    s = LifecycleState(plan_id=1)
    for e in events:
        s = step(s, e, plan)
    return s


def test_conducta_perfecta_aguanto_y_movio_be():
    p = _plan()
    events = [
        {"tipo": "PLAN_CONFIRMED", "procedencia": "observado"},
        {"tipo": "RUNG_FILLED", "order_id": "A", "rung_index": 0, "procedencia": "observado"},
        {"tipo": "SL_MOVED", "nuevo_sl": p.entry_price, "procedencia": "observado"},
        {"tipo": "RUNG_FILLED", "order_id": "B", "rung_index": 1, "procedencia": "observado"},
        {"tipo": "STOP_HIT", "procedencia": "observado"},
    ]
    fs = _replay(p, events)
    c = compute_conduct(p, events, fs, entry_price=100.0,
                        entry_ts="2026-01-01T00:00:00+00:00",
                        exit_ts="2026-01-03T00:00:00+00:00", procedencia="observado")
    assert c["adherencia_be"] is True
    assert c["rungs_honrados"] == 2
    assert c["cierre_en_plan"] is True
    assert c["sl_respetado"] is True
    assert c["hold_hours"] == 48.0
    assert c["procedencia"] == "observado"


def test_panico_salida_unica_antes_de_tp1():
    p = _plan()
    events = [
        {"tipo": "PLAN_CONFIRMED", "procedencia": "declarado"},
        {"tipo": "MANUAL_EXIT", "procedencia": "declarado"},
    ]
    fs = _replay(p, events)
    c = compute_conduct(p, events, fs, entry_price=100.0,
                        entry_ts="2026-01-01T00:00:00+00:00",
                        exit_ts="2026-01-01T02:00:00+00:00", procedencia="declarado")
    assert c["cierre_en_plan"] is False
    assert c["rungs_honrados"] == 0
    assert c["escalono"] is False
    assert c["adherencia_be"] is None


def test_ensanchar_sl_marca_sl_no_respetado():
    p = _plan()
    events = [
        {"tipo": "PLAN_CONFIRMED", "procedencia": "observado"},
        {"tipo": "SL_MOVED", "nuevo_sl": p.sl_price - 5.0, "procedencia": "observado"},
        {"tipo": "STOP_HIT", "procedencia": "observado"},
    ]
    fs = _replay(p, events)
    c = compute_conduct(p, events, fs, entry_price=100.0,
                        entry_ts="2026-01-01T00:00:00+00:00",
                        exit_ts="2026-01-01T05:00:00+00:00", procedencia="observado")
    assert c["sl_respetado"] is False


def test_entry_fuera_de_zona():
    p = _plan(entry=103.0)
    events = [{"tipo": "PLAN_CONFIRMED", "procedencia": "observado"},
              {"tipo": "MANUAL_EXIT", "procedencia": "observado"}]
    fs = _replay(p, events)
    c = compute_conduct(p, events, fs, entry_price=103.0,
                        entry_ts="2026-01-01T00:00:00+00:00",
                        exit_ts="2026-01-01T01:00:00+00:00", procedencia="observado")
    assert c["entry_en_zona"] is False


def test_sl_hit_sin_rungs_no_es_escalonado():
    # Un STOP_HIT con cero rungs llenos es salida ÚNICA, no escalonada.
    p = _plan()
    events = [
        {"tipo": "PLAN_CONFIRMED", "procedencia": "observado"},
        {"tipo": "STOP_HIT", "procedencia": "observado"},
    ]
    fs = _replay(p, events)
    c = compute_conduct(p, events, fs, entry_price=100.0,
                        entry_ts="2026-01-01T00:00:00+00:00",
                        exit_ts="2026-01-01T06:00:00+00:00", procedencia="observado")
    assert c["cierre_en_plan"] is True      # el SL es parte del plan
    assert c["escalono"] is False           # pero NO escalonó (salida única)
    assert c["rungs_honrados"] == 0
