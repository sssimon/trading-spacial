"""Tests del detector neutral de S/R (D.1). Puro: sin red, sin DB. Spec §3."""
from screener.sr_levels import _pivots


def _bar(h, l):
    return {"open_time": 0, "open": l, "high": h, "low": l,
            "close": (h + l) / 2, "volume": 1, "quote_volume": 1}


def test_pivot_alto_es_maximo_local_estricto():
    highs = [10, 11, 12, 15, 12, 11, 10]   # idx 3 (=15) es el pico
    bars = [_bar(h, 9) for h in highs]
    altos, _ = _pivots(bars, k=2)
    assert altos == [15.0]


def test_pivot_bajo_es_minimo_local_estricto():
    lows = [10, 9, 8, 5, 8, 9, 10]         # idx 3 (=5) es el valle
    bars = [_bar(20, l) for l in lows]
    _, bajos = _pivots(bars, k=2)
    assert bajos == [5.0]


def test_pivot_excluye_ultimas_k_velas():
    highs = [10, 11, 12, 13, 99]
    bars = [_bar(h, 9) for h in highs]
    altos, _ = _pivots(bars, k=2)
    assert 99.0 not in altos


def test_meseta_plana_no_es_pivote():
    highs = [10, 12, 12, 12, 10]
    bars = [_bar(h, 9) for h in highs]
    altos, _ = _pivots(bars, k=1)
    assert altos == []


# ── Task 2: _round_confluence ────────────────────────────────────────────────
from screener.sr_levels import _round_confluence


def test_confluencia_detecta_redondo_en_banda():
    assert _round_confluence(69000.0, 69200.0) == [69000.0]


def test_confluencia_vacia_si_no_hay_redondo():
    assert _round_confluence(69100.0, 69200.0) == []


def test_confluencia_precio_pequeno():
    res = _round_confluence(5.95e-6, 6.05e-6)
    assert any(abs(x - 6.0e-6) < 1e-12 for x in res)
