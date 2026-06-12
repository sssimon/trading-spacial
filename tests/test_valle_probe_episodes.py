"""Tests de la detección de episodios (puro, sintético).

Un episodio = run contiguo de días del mismo estado en_rango. La entrada es el
PRIMER día de cada episodio. Reusa measure_consolidation del screener A."""
from tools.valle_calidad_probe.episodes import detect_episodes


def _bar(t, close, *, high=None, low=None):
    high = high if high is not None else close * 1.005
    low = low if low is not None else close * 0.995
    return {"open_time": t, "open": close, "high": high, "low": low,
            "close": close, "volume": 1000.0, "quote_volume": 1_000_000.0}


def test_serie_en_rango_da_un_episodio_valle():
    # 150 días oscilando ±3% → en rango todo el tiempo evaluable → 1 episodio valle.
    bars = []
    for i in range(150):
        c = 1.0 + (0.03 if i % 2 else -0.03)
        bars.append(_bar(i * 86_400_000, c, high=c * 1.005, low=c * 0.995))
    eps = detect_episodes(bars)
    valles = [e for e in eps if e["tipo"] == "valle"]
    assert len(valles) == 1
    # La entrada cae en el primer día EVALUABLE (>= ventana de consolidación).
    assert valles[0]["entry_idx"] >= 84


def test_transicion_genera_episodios_separados():
    # Primero en rango (±3%), luego tendencia fuerte (sube 1→2) → valle, luego no_valle.
    bars = []
    for i in range(120):
        c = 1.0 + (0.03 if i % 2 else -0.03)
        bars.append(_bar(i * 86_400_000, c, high=c * 1.005, low=c * 0.995))
    for j in range(120, 240):
        c = 1.0 + (j - 119) / 60.0     # tendencia ancha → en_rango False
        bars.append(_bar(j * 86_400_000, c))
    eps = detect_episodes(bars)
    tipos = [e["tipo"] for e in eps]
    assert "valle" in tipos and "no_valle" in tipos
    # Episodios contiguos no se repiten consecutivamente del mismo tipo.
    for a, b in zip(tipos, tipos[1:]):
        assert a != b


def test_serie_corta_sin_historia_no_da_episodios():
    bars = [_bar(i * 86_400_000, 1.0) for i in range(50)]  # < ventana
    assert detect_episodes(bars) == []
