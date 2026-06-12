"""Tests de derive_plan (instrumento, Fase 1). Puro: sin red, sin DB. Spec §4."""
from instrument.plan import derive_plan, Plan, Rung


def _z(tipo, bajo, alto, centro):
    return {"tipo": tipo, "precio_bajo": bajo, "precio_alto": alto,
            "centro": centro, "toques": 3, "confluencia_redondo": []}


def _zonas_4_resistencias():
    return [
        _z("soporte", 94, 96, 95),
        _z("resistencia", 104, 106, 105),
        _z("resistencia", 109, 111, 110),
        _z("resistencia", 114, 116, 115),
        _z("resistencia", 119, 121, 120),
    ]


def test_sl_bajo_el_soporte_con_margen():
    p = derive_plan(_zonas_4_resistencias(), entry_price=100.0)
    assert p.sl_price == 94.0 * (1 - 0.01)


def test_escalera_son_las_resistencias_ascendentes_cap_4():
    p = derive_plan(_zonas_4_resistencias(), entry_price=100.0)
    assert [r.tp_price for r in p.rungs] == [105.0, 110.0, 115.0, 120.0]


def test_tamanos_frontloaded_tp1_min_50_y_suman_uno_con_runner():
    p = derive_plan(_zonas_4_resistencias(), entry_price=100.0)
    assert p.rungs[0].size_frac >= 0.50
    total = sum(r.size_frac for r in p.rungs) + p.runner_frac
    assert abs(total - 1.0) < 1e-9


def test_menos_de_4_resistencias_trunca_y_renormaliza():
    zonas = [_z("soporte", 94, 96, 95),
             _z("resistencia", 104, 106, 105),
             _z("resistencia", 109, 111, 110)]
    p = derive_plan(zonas, entry_price=100.0)
    assert len(p.rungs) == 2
    total = sum(r.size_frac for r in p.rungs) + p.runner_frac
    assert abs(total - 1.0) < 1e-9
    assert p.rungs[0].size_frac >= 0.50


def test_runner_desactivado_reparte_todo_en_la_escalera():
    p = derive_plan(_zonas_4_resistencias(), entry_price=100.0, runner_on=False)
    assert p.runner_frac == 0.0
    assert abs(sum(r.size_frac for r in p.rungs) - 1.0) < 1e-9


def test_entry_zone_es_el_soporte_que_contiene_al_entry():
    zonas = [_z("soporte", 99, 101, 100), _z("resistencia", 104, 106, 105)]
    p = derive_plan(zonas, entry_price=100.0)
    assert p.entry_zone is not None
    assert p.entry_zone["centro"] == 100.0


def test_sin_resistencias_todo_es_runner():
    zonas = [_z("soporte", 94, 96, 95)]
    p = derive_plan(zonas, entry_price=100.0)
    assert p.rungs == []
    assert p.runner_frac == 1.0
