"""Tests del arnés de falsación (instrumento, Fase 1). La pieza pura
reproduce_position se testea sin red ni DB. Spec §6."""
import pytest

from tools.lifecycle_falsifier import reproduce_position


def _z(tipo, bajo, alto, centro):
    return {"tipo": tipo, "precio_bajo": bajo, "precio_alto": alto,
            "centro": centro, "toques": 3, "confluencia_redondo": []}


def _zonas():
    return [_z("soporte", 94, 96, 95), _z("resistencia", 104, 106, 105),
            _z("resistencia", 109, 111, 110)]


def test_reproduce_cierre_en_tp_dentro_de_tolerancia():
    pos = {"symbol": "BTCUSDT", "entry_price": 100.0,
           "entry_ts": "2026-01-01T00:00:00+00:00", "exit_ts": "2026-01-03T00:00:00+00:00",
           "exit_price": 105.0, "exit_reason": "TP_HIT", "tenant_id": 2, "id": 7}
    res = reproduce_position(pos, _zonas())
    assert res["reproduced"] is True
    assert res["conduct"]["rungs_honrados"] >= 1
    assert res["conduct"]["cierre_en_plan"] is True


def test_reproduce_cierre_en_sl():
    pos = {"symbol": "BTCUSDT", "entry_price": 100.0,
           "entry_ts": "2026-01-01T00:00:00+00:00", "exit_ts": "2026-01-02T00:00:00+00:00",
           "exit_price": 93.06, "exit_reason": "SL_HIT", "tenant_id": 2, "id": 8}
    res = reproduce_position(pos, _zonas())
    assert res["reproduced"] is True
    assert res["conduct"]["close_reason"] == "SL_HIT"


def test_exit_fuera_de_todo_no_reproducible():
    pos = {"symbol": "BTCUSDT", "entry_price": 100.0,
           "entry_ts": "2026-01-01T00:00:00+00:00", "exit_ts": "2026-01-02T00:00:00+00:00",
           "exit_price": 102.3, "exit_reason": "MANUAL", "tenant_id": 2, "id": 9}
    res = reproduce_position(pos, _zonas())
    assert res["reproduced"] is False
    assert res["conduct"]["cierre_en_plan"] is False


def test_sin_zonas_no_reproducible():
    pos = {"symbol": "XYZUSDT", "entry_price": 100.0,
           "entry_ts": "2026-01-01T00:00:00+00:00", "exit_ts": "2026-01-02T00:00:00+00:00",
           "exit_price": 100.0, "exit_reason": "MANUAL", "tenant_id": 2, "id": 10}
    res = reproduce_position(pos, [])
    assert res["reproduced"] is False


@pytest.mark.network
def test_bars_as_of_devuelve_velas_diarias():
    from tools.lifecycle_falsifier import _bars_as_of
    bars = _bars_as_of("BTCUSDT", "2026-01-01T00:00:00+00:00")
    assert len(bars) > 100
    assert all("high" in b and "low" in b for b in bars)
