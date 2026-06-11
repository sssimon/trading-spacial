"""Tests del bootstrap de la diferencia de medias (determinista, seed 42).

Unidad de remuestreo = la entrada (= el episodio: una entrada por episodio).
El estadístico = mean(pnl_valle) − mean(pnl_no_valle)."""
from tools.valle_calidad_probe.bootstrap import bootstrap_diff


def test_diferencia_positiva_clara_excluye_cero():
    # valle netamente mejor que no_valle, sin solape → CI debe excluir cero (+).
    # Nota: arrays perfectamente constantes producen distribución bootstrap degenerada
    # (ci_low==ci_high); se usa variación simétrica alrededor de 100/0 para que
    # diff==100.0 exacto y el CI tenga ancho > 0. Espíritu preservado: sin solape.
    valle = [{"pnl_usd": 100.0 + i - 19.5} for i in range(40)]
    no_valle = [{"pnl_usd": float(i) - 19.5} for i in range(40)]
    out = bootstrap_diff(valle, no_valle)
    assert out["diff"] == 100.0
    assert out["ci_low"] > 0.0
    assert out["ci_high"] > out["ci_low"]


def test_grupos_iguales_incluyen_cero():
    valle = [{"pnl_usd": v} for v in [10.0, -10.0] * 20]
    no_valle = [{"pnl_usd": v} for v in [10.0, -10.0] * 20]
    out = bootstrap_diff(valle, no_valle)
    assert out["ci_low"] <= 0.0 <= out["ci_high"]


def test_determinista_misma_seed_mismo_ci():
    valle = [{"pnl_usd": float(i)} for i in range(40)]
    no_valle = [{"pnl_usd": float(i) - 5.0} for i in range(40)]
    a = bootstrap_diff(valle, no_valle)
    b = bootstrap_diff(valle, no_valle)
    assert a == b   # seed fijo → byte-idéntico
