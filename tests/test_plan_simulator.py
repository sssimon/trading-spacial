"""Tests del arnés del simulador F2 (instrumento). check_parity es puro. Spec §5."""
import pytest

from instrument.plan import derive_plan
from instrument.lifecycle import LifecycleState
from tools.plan_simulator import check_parity


def _z(tipo, bajo, alto, centro):
    return {"tipo": tipo, "precio_bajo": bajo, "precio_alto": alto,
            "centro": centro, "toques": 3, "confluencia_redondo": []}


def _plan():
    zonas = [_z("soporte", 94, 96, 95), _z("resistencia", 104, 106, 105)]
    return derive_plan(zonas, entry_price=100.0)


def test_parity_ambos_sl():
    st = LifecycleState(plan_id=0, fase="CLOSED", close_reason="SL_HIT")
    assert check_parity(st, {"exit_reason": "SL_HIT"}, _plan())["parity"] is True


def test_parity_real_tp_sim_toco_rung():
    st = LifecycleState(plan_id=0, fase="CLOSED", close_reason="SIM_END",
                        rungs_llenos=frozenset({0}))
    assert check_parity(st, {"exit_reason": "TP_HIT"}, _plan())["parity"] is True


def test_divergencia_real_tp_sim_sl():
    st = LifecycleState(plan_id=0, fase="CLOSED", close_reason="SL_HIT")
    r = check_parity(st, {"exit_reason": "TP_HIT"}, _plan())
    assert r["parity"] is False
    assert "TP" in r["motivo"] and "SL" in r["motivo"]


@pytest.mark.network
def test_forward_candles_devuelve_velas():
    from tools.plan_simulator import _forward_candles
    candles = _forward_candles("BTCUSDT", "2025-01-01T00:00:00+00:00",
                               "2025-02-01T00:00:00+00:00")
    assert len(candles) > 5
    assert all({"open", "high", "low", "close"} <= set(c) for c in candles)


def test_manual_real_es_no_aplica():
    st = LifecycleState(plan_id=0, fase="CLOSED", close_reason="SIM_END")
    r = check_parity(st, {"exit_reason": "MANUAL"}, _plan())
    assert r["parity"] is None
    assert "fuera de plan" in r["motivo"]


def test_time_limit_real_es_no_aplica():
    st = LifecycleState(plan_id=0, fase="CLOSED", close_reason="SL_HIT")
    r = check_parity(st, {"exit_reason": "TIME_LIMIT_HIT"}, _plan())
    assert r["parity"] is None


def test_real_sl_sim_sin_sl_es_divergencia():
    # real SL pero el sim no cerró por SL (cerró por SIM_END sin rungs) → divergencia
    st = LifecycleState(plan_id=0, fase="CLOSED", close_reason="SIM_END",
                        rungs_llenos=frozenset())
    r = check_parity(st, {"exit_reason": "SL_HIT"}, _plan())
    assert r["parity"] is False


def test_exit_reason_vacio_es_no_aplica():
    st = LifecycleState(plan_id=0, fase="CLOSED", close_reason="SIM_END")
    r = check_parity(st, {"exit_reason": None}, _plan())
    assert r["parity"] is None
