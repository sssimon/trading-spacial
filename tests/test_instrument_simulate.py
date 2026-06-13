"""Tests del simulador determinista (instrumento F2). Puro: sin red, sin DB. Spec §3/§4."""
from instrument.plan import derive_plan
from instrument.lifecycle import LifecycleState
from instrument.simulate import resolve_fills, simulate_plan


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


def test_doble_toque_sl_primero():
    p = _plan()
    candle = {"open": 100, "high": 106, "low": 90, "close": 95}
    evs = resolve_fills(p, _armed(p), candle)
    assert [e["tipo"] for e in evs] == ["STOP_HIT"]


def test_solo_tp1_dispara_rung_y_be():
    p = _plan()
    candle = {"open": 100, "high": 106, "low": 99, "close": 105}
    evs = resolve_fills(p, _armed(p), candle)
    tipos = [e["tipo"] for e in evs]
    assert evs[0]["tipo"] == "RUNG_FILLED" and evs[0]["rung_index"] == 0
    assert "SL_MOVED" in tipos
    assert next(e for e in evs if e["tipo"] == "SL_MOVED")["nuevo_sl"] == p.entry_price


def test_ambos_rungs_en_una_vela_en_orden():
    p = _plan()
    candle = {"open": 100, "high": 112, "low": 99, "close": 111}
    rungs = [e for e in resolve_fills(p, _armed(p), candle) if e["tipo"] == "RUNG_FILLED"]
    assert [e["rung_index"] for e in rungs] == [0, 1]


def test_nada_dispara_lista_vacia():
    p = _plan()
    candle = {"open": 100, "high": 101, "low": 99, "close": 100}
    assert resolve_fills(p, _armed(p), candle) == []


def test_rung_ya_lleno_no_se_reemite():
    p = _plan()
    candle = {"open": 100, "high": 106, "low": 99, "close": 105}
    evs = resolve_fills(p, _armed(p, rungs_llenos=frozenset({0})), candle)
    assert not any(e["tipo"] == "RUNG_FILLED" and e["rung_index"] == 0 for e in evs)


def test_simula_tp1_luego_be_cierra_be_hit():
    p = _plan()
    candles = [
        {"open": 100, "high": 106, "low": 99, "close": 105},
        {"open": 105, "high": 107, "low": 99, "close": 100},
    ]
    events, st = simulate_plan(p, candles)
    assert st.fase == "CLOSED" and st.close_reason == "BE_HIT"
    assert events[0]["tipo"] == "PLAN_CONFIRMED"


def test_simula_solo_cae_cierra_sl_hit():
    p = _plan()
    candles = [{"open": 100, "high": 101, "low": 90, "close": 92}]
    _, st = simulate_plan(p, candles)
    assert st.close_reason == "SL_HIT"


def test_simula_sube_toda_la_escalera_sim_end():
    p = _plan()
    candles = [{"open": 100, "high": 112, "low": 99, "close": 111}]
    _, st = simulate_plan(p, candles)
    assert st.rungs_llenos == frozenset({0, 1})
    assert st.close_reason == "SIM_END"


def test_simula_nunca_toca_nada_sim_end():
    p = _plan()
    candles = [{"open": 100, "high": 101, "low": 99, "close": 100} for _ in range(3)]
    _, st = simulate_plan(p, candles)
    assert st.close_reason == "SIM_END"


def test_simula_lista_vacia_es_sim_end_inmediato():
    p = _plan()
    events, st = simulate_plan(p, [])
    assert st.fase == "CLOSED" and st.close_reason == "SIM_END"
    assert events == [{"tipo": "PLAN_CONFIRMED", "procedencia": "observado"},
                      {"tipo": "SIM_END", "procedencia": "observado"}]
