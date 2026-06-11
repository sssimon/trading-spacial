"""Tests del orquestador (gates puros + candado holdout).

NO toca el panel real ni la calibración v3 real — el objetivo es la lógica de
gate/poder/dictamen, no los números de mercado."""
from tools.valle_calidad_probe.run import evaluate_verdict


def test_gate_underpowered_si_pocos_episodios():
    # 5 entradas valle (< MIN_EPISODES_VALLE=30) → UNDERPOWERED.
    valle = [{"pnl_usd": 50.0} for _ in range(5)]
    no_valle = [{"pnl_usd": 0.0} for _ in range(40)]
    v = evaluate_verdict(valle, no_valle)
    assert v["verdict"] == "UNDERPOWERED"
    assert v["n_episodios_valle"] == 5


def test_gate_pass_si_diferencia_positiva_excluye_cero():
    valle = [{"pnl_usd": 100.0 + i - 19.5} for i in range(40)]
    no_valle = [{"pnl_usd": float(i) - 19.5} for i in range(40)]
    v = evaluate_verdict(valle, no_valle)
    assert v["verdict"] == "PASS"
    assert v["ci_low"] > 0.0


def test_gate_fail_si_ci_incluye_cero():
    valle = [{"pnl_usd": x} for x in ([10.0, -10.0] * 20)]
    no_valle = [{"pnl_usd": x} for x in ([10.0, -10.0] * 20)]
    v = evaluate_verdict(valle, no_valle)
    assert v["verdict"] == "FAIL"


def test_no_importa_holdout():
    # Candado #322: el módulo run no debe referenciar holdout en absoluto.
    import tools.valle_calidad_probe.run as runmod
    import inspect
    src = inspect.getsource(runmod)
    assert "holdout" not in src.lower()
    assert "open_holdout" not in src
