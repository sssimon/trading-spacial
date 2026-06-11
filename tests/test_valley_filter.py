"""Tests del cálculo puro del screener de valles (Vista Valles A).

Spec: docs/superpowers/specs/es/2026-06-11-screener-valles-filtro-vida-design.md §7.
Cero red, cero DB — todo sobre barras diarias sintéticas.
"""
from screener.valley_filter import classify_liveness, measure_consolidation, evaluate_symbol, order_neutral, liquidity_value


def _bar(t, close, quote_vol, *, high=None, low=None):
    """Barra diaria sintética. high/low por defecto = ±0.5% de close (vela viva)."""
    high = high if high is not None else close * 1.005
    low = low if low is not None else close * 0.995
    return {"open_time": t, "open": close, "high": high, "low": low,
            "close": close, "volume": quote_vol / close, "quote_volume": quote_vol}


def _serie(n, close=1.0, quote_vol=1_000_000.0, **kw):
    """n barras diarias consecutivas (1 día = 86_400_000 ms)."""
    return [_bar(i * 86_400_000, close, quote_vol, **kw) for i in range(n)]


class TestClassifyLiveness:
    def test_moneda_viva_pasa(self):
        vivo, razones = classify_liveness(_serie(200, quote_vol=2_000_000.0))
        assert vivo is True
        assert razones == []

    def test_volumen_bajo_piso_excluye(self):
        vivo, razones = classify_liveness(_serie(200, quote_vol=100_000.0))
        assert vivo is False
        assert "volumen_bajo_piso" in razones

    def test_historia_insuficiente_excluye(self):
        vivo, razones = classify_liveness(_serie(50, quote_vol=2_000_000.0))
        assert vivo is False
        assert "historia_insuficiente" in razones

    def test_volumen_agonizante_excluye(self):
        viejo = [_bar(i * 86_400_000, 1.0, 3_000_000.0) for i in range(90)]
        nuevo = [_bar((90 + i) * 86_400_000, 1.0, 800_000.0) for i in range(90)]
        vivo, razones = classify_liveness(viejo + nuevo)
        assert vivo is False
        assert "volumen_agonizante" in razones

    def test_velas_planas_excluye(self):
        planas = [_bar(i * 86_400_000, 1.0, 2_000_000.0, high=1.0, low=1.0) for i in range(200)]
        vivo, razones = classify_liveness(planas)
        assert vivo is False
        assert "velas_planas" in razones

    def test_descansa_con_vida_vs_agoniza(self):
        estable = _serie(200, quote_vol=700_000.0)  # bajo pero constante y > piso
        vivo, razones = classify_liveness(estable)
        assert vivo is True, f"volumen bajo-estable debe vivir, razones={razones}"


class TestMeasureConsolidation:
    def test_valle_en_rango_estrecho(self):
        # 120 días oscilando dentro de ±3% de 1.0 → en rango.
        bars = []
        for i in range(120):
            c = 1.0 + (0.03 if i % 2 else -0.03)  # ±3%
            bars.append(_bar(i * 86_400_000, c, 1_000_000.0,
                             high=c * 1.005, low=c * 0.995))
        out = measure_consolidation(bars)
        assert out["en_rango"] is True
        assert out["pct_rango"] < 0.25
        assert out["semanas"] >= 1

    def test_tendencia_no_esta_en_rango(self):
        # Precio subiendo de 1.0 a 2.0 → NO en rango (rango ancho).
        bars = [_bar(i * 86_400_000, 1.0 + i / 120.0, 1_000_000.0) for i in range(120)]
        out = measure_consolidation(bars)
        assert out["en_rango"] is False
        assert out["pct_rango"] > 0.25

    def test_reporta_metricas_aunque_no_este_en_rango(self):
        bars = [_bar(i * 86_400_000, 1.0 + i / 60.0, 1_000_000.0) for i in range(120)]
        out = measure_consolidation(bars)
        assert set(out.keys()) == {"en_rango", "pct_rango", "semanas", "vol_percentil"}


class TestEvaluateYorden:
    def test_evaluate_viva_en_rango_es_candidata(self):
        bars = []
        for i in range(150):
            c = 1.0 + (0.03 if i % 2 else -0.03)
            bars.append(_bar(i * 86_400_000, c, 2_000_000.0,
                             high=c * 1.005, low=c * 0.995))
        cand = evaluate_symbol("XYZUSDT", bars)
        assert cand is not None
        assert cand["symbol"] == "XYZUSDT"
        assert set(cand.keys()) >= {
            "symbol", "price", "pct_rango", "semanas_consolidando",
            "volumen_usd_dia", "distancia_ath_pct", "razones_vida"}
        assert cand["razones_vida"] == []

    def test_evaluate_muerta_devuelve_none(self):
        cand = evaluate_symbol("DEADUSDT", _serie(200, quote_vol=50_000.0))
        assert cand is None  # volumen bajo piso ⟹ no candidata

    def test_evaluate_viva_pero_no_en_rango_devuelve_none(self):
        bars = [_bar(i * 86_400_000, 1.0 + i / 100.0, 2_000_000.0) for i in range(150)]
        cand = evaluate_symbol("TRENDUSDT", bars)
        assert cand is None  # viva pero en tendencia, no es valle

    def test_orden_neutral_por_liquidez_desc(self):
        a = {"symbol": "AUSDT", "volumen_usd_dia": 1_000_000.0}
        b = {"symbol": "BUSDT", "volumen_usd_dia": 5_000_000.0}
        c = {"symbol": "CUSDT", "volumen_usd_dia": 2_000_000.0}
        ordenado = order_neutral([a, b, c])
        assert [x["symbol"] for x in ordenado] == ["BUSDT", "CUSDT", "AUSDT"]

    def test_liquidity_value_es_mediana_volumen(self):
        bars = _serie(60, quote_vol=1_500_000.0)
        assert liquidity_value(bars) == 1_500_000.0
