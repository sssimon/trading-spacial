"""Tests del núcleo puro del régimen de mercado (alt-season). Sin red, sin DB."""
from regime.alt_season import symbol_contribution, MIN_HISTORY_DAYS


def _bars(closes):
    """Barras diarias mínimas desde una lista de cierres (highs/lows = close)."""
    return [{"open_time": i * 86_400_000, "open": c, "high": c, "low": c,
             "close": c, "volume": 1.0, "quote_volume": 1.0}
            for i, c in enumerate(closes)]


def test_contribution_none_si_historia_insuficiente():
    assert symbol_contribution("X", _bars([1.0] * (MIN_HISTORY_DAYS - 1))) is None


def test_contribution_above_sma50_y_ret_30d():
    # 50 cierres planos a 1.0, luego sube a 1.20 al final.
    closes = [1.0] * 49 + [1.20]
    c = symbol_contribution("X", _bars(closes))
    assert c is not None
    assert c["above_sma50"] is True            # 1.20 > media de la ventana
    # ret_30d = (1.20 - close_{t-30}) / close_{t-30}; close_{t-30} = 1.0
    assert abs(c["ret_30d"] - 0.20) < 1e-9


def test_contribution_below_sma50():
    closes = [1.0] * 49 + [0.80]
    c = symbol_contribution("X", _bars(closes))
    assert c["above_sma50"] is False
    assert abs(c["ret_30d"] - (-0.20)) < 1e-9


from regime.alt_season import compose_regime


def _contrib(above, ret):
    return {"above_sma50": above, "ret_30d": ret}


def test_compose_tres_votos_alts():
    # breadth alto (todos sobre sma50), outperf alto, dominancia baja → 3 votos alts.
    contribs = [_contrib(True, 0.30)] * 10       # breadth=1.0, ret alto
    out = compose_regime(contribs, btc_ret_30d=0.0, btc_dominance=0.45, coverage_ratio=1.0)
    assert out["estado"] == "alts"
    assert out["votos"] == {"alts": 3, "neutral": 0, "btc": 0, "vivos": 3}


def test_compose_dos_alts_un_btc_gana_alts():
    contribs = [_contrib(True, 0.30)] * 10       # breadth alts, outperf alts
    out = compose_regime(contribs, btc_ret_30d=0.0, btc_dominance=0.65, coverage_ratio=1.0)  # dom → btc
    assert out["estado"] == "alts"
    assert out["votos"]["vivos"] == 3


def test_compose_empate_es_mixto():
    # breadth alts, dominancia btc, outperf neutral → 1-1-1 → mixto.
    contribs = [_contrib(True, 0.0)] * 10        # breadth=1.0 (alts), outperf=0.0 (neutral)
    out = compose_regime(contribs, btc_ret_30d=0.0, btc_dominance=0.65, coverage_ratio=1.0)
    assert out["estado"] == "mixto"


def test_compose_dominancia_muerta_vota_con_dos():
    contribs = [_contrib(True, 0.30)] * 10       # breadth alts, outperf alts
    out = compose_regime(contribs, btc_ret_30d=0.0, btc_dominance=None, coverage_ratio=1.0)
    assert out["estado"] == "alts"
    assert out["votos"]["vivos"] == 2
    assert out["componentes"]["dominancia_btc"]["estado"] == "muerto"
    assert out["componentes"]["dominancia_btc"]["valor"] is None


def test_compose_un_solo_votante_vivo_es_mixto():
    # outperf muerto (btc_ret None) + dominancia muerta → solo breadth vivo → mixto.
    contribs = [_contrib(True, 0.30)] * 10
    out = compose_regime(contribs, btc_ret_30d=None, btc_dominance=None, coverage_ratio=1.0)
    assert out["estado"] == "mixto"
    assert out["votos"]["vivos"] == 1


def test_compose_cobertura_baja_mata_breadth():
    contribs = [_contrib(True, 0.30)] * 10
    out = compose_regime(contribs, btc_ret_30d=0.0, btc_dominance=0.45, coverage_ratio=0.5)
    assert out["componentes"]["breadth50"]["estado"] == "muerto"
    assert out["componentes"]["breadth50"]["razon"] == "cobertura_baja"
    assert out["componentes"]["breadth50"]["valor"] is not None   # el valor se muestra igual
    assert out["votos"]["vivos"] == 2                              # solo outperf + dominancia


def test_compose_frontera_breadth_060_es_alts():
    # 6 de 10 sobre sma50 → breadth=0.60 → alts (>=).
    contribs = [_contrib(True, 0.0)] * 6 + [_contrib(False, 0.0)] * 4
    out = compose_regime(contribs, btc_ret_30d=0.0, btc_dominance=0.55, coverage_ratio=1.0)
    assert out["componentes"]["breadth50"]["lean"] == "alts"


def test_compose_frontera_breadth_040_es_btc():
    # 4 de 10 sobre sma50 → breadth=0.40 → btc (<=).
    contribs = [_contrib(True, 0.0)] * 4 + [_contrib(False, 0.0)] * 6
    out = compose_regime(contribs, btc_ret_30d=0.0, btc_dominance=0.55, coverage_ratio=1.0)
    assert out["componentes"]["breadth50"]["lean"] == "btc"
    assert abs(out["componentes"]["breadth50"]["valor"] - 0.40) < 1e-9


def test_compose_frontera_outperf_005_es_alts():
    # median(ret_alt - btc_ret) = 0.05 - 0.0 = 0.05 → alts (>=).
    contribs = [_contrib(True, 0.05)] * 5
    out = compose_regime(contribs, btc_ret_30d=0.0, btc_dominance=0.55, coverage_ratio=1.0)
    assert out["componentes"]["outperf_30d"]["lean"] == "alts"
    assert abs(out["componentes"]["outperf_30d"]["valor"] - 0.05) < 1e-9


def test_compose_frontera_outperf_neg005_es_btc():
    # median(ret_alt - btc_ret) = -0.05 - 0.0 = -0.05 → btc (<=).
    contribs = [_contrib(False, -0.05)] * 5
    out = compose_regime(contribs, btc_ret_30d=0.0, btc_dominance=0.55, coverage_ratio=1.0)
    assert out["componentes"]["outperf_30d"]["lean"] == "btc"
    assert abs(out["componentes"]["outperf_30d"]["valor"] - (-0.05)) < 1e-9


def test_compose_frontera_dominancia_050_es_alts():
    # dominancia=0.50 → alts (<=).
    contribs = [_contrib(True, 0.30)] * 5
    out = compose_regime(contribs, btc_ret_30d=0.0, btc_dominance=0.50, coverage_ratio=1.0)
    assert out["componentes"]["dominancia_btc"]["lean"] == "alts"


def test_compose_frontera_dominancia_058_es_btc():
    # dominancia=0.58 → btc (>=).
    contribs = [_contrib(True, 0.30)] * 5
    out = compose_regime(contribs, btc_ret_30d=0.0, btc_dominance=0.58, coverage_ratio=1.0)
    assert out["componentes"]["dominancia_btc"]["lean"] == "btc"


def test_compose_dos_neutral_un_alts_es_mixto():
    # breadth=0.50 (neutral), outperf=0.0 (neutral), dominancia=0.45 (alts)
    # → leans: [neutral, neutral, alts] → neutral domina → mixto.
    contribs = [_contrib(True, 0.0)] * 5 + [_contrib(False, 0.0)] * 5   # breadth=0.50 neutral
    out = compose_regime(contribs, btc_ret_30d=0.0, btc_dominance=0.45, coverage_ratio=1.0)
    assert out["componentes"]["breadth50"]["lean"] == "neutral"
    assert out["componentes"]["outperf_30d"]["lean"] == "neutral"
    assert out["componentes"]["dominancia_btc"]["lean"] == "alts"
    assert out["estado"] == "mixto"


def test_compose_cero_votantes_vivos_es_mixto():
    # alt_contribs=[], btc_ret_30d=None, btc_dominance=None → 0 votantes vivos → mixto.
    out = compose_regime([], btc_ret_30d=None, btc_dominance=None, coverage_ratio=1.0)
    assert out["estado"] == "mixto"
    assert out["votos"]["vivos"] == 0
