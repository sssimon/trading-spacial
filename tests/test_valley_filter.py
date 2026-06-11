"""Tests del cálculo puro del screener de valles (Vista Valles A).

Spec: docs/superpowers/specs/es/2026-06-11-screener-valles-filtro-vida-design.md §7.
Cero red, cero DB — todo sobre barras diarias sintéticas.
"""
from screener.valley_filter import classify_liveness


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
