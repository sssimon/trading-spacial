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


# ── Task 3: _cluster ─────────────────────────────────────────────────────────
from screener.sr_levels import _cluster


def test_cluster_agrupa_dentro_de_tolerancia():
    zonas = _cluster([100.0, 100.5, 110.0], "soporte", tol_pct=0.0075, min_touches=1)
    assert len(zonas) == 2
    assert zonas[0]["toques"] == 2
    assert zonas[0]["centro"] == 100.25
    assert zonas[0]["tipo"] == "soporte"
    assert zonas[0]["precio_bajo"] == 100.0 and zonas[0]["precio_alto"] == 100.5


def test_cluster_min_touches_filtra_giros_sueltos():
    zonas = _cluster([100.0, 200.0], "resistencia", tol_pct=0.0075, min_touches=2)
    assert zonas == []


def test_centro_es_mediana_no_redondo():
    zonas = _cluster([64800.0, 65100.0, 65400.0], "soporte", tol_pct=0.02, min_touches=1)
    assert zonas[0]["centro"] == 65100.0
    assert 65000.0 in zonas[0]["confluencia_redondo"]


# ── Task 4: locate_price ─────────────────────────────────────────────────────
from screener.sr_levels import locate_price


def _zona(tipo, bajo, alto, centro, toques):
    return {"tipo": tipo, "precio_bajo": bajo, "precio_alto": alto,
            "centro": centro, "toques": toques, "confluencia_redondo": []}


def test_locate_entre_zonas():
    zonas = [_zona("soporte", 64800, 65400, 65100, 3),
             _zona("resistencia", 69000, 69200, 69100, 4)]
    u = locate_price(67230, zonas)
    assert u["dentro_de"] is None
    assert u["techo"]["centro"] == 69100
    assert u["piso"]["centro"] == 65100
    assert u["piso"]["dist_pct"] == round((65100 - 67230) / 67230 * 100, 2)


def test_locate_dentro_de_zona():
    zonas = [_zona("soporte", 64800, 65400, 65100, 3)]
    u = locate_price(65000, zonas)
    assert u["dentro_de"]["centro"] == 65100
    assert u["techo"] is None and u["piso"] is None


def test_locate_sin_zonas():
    assert locate_price(100.0, []) == {"dentro_de": None, "techo": None, "piso": None}


# ── Task 5: detect_levels ────────────────────────────────────────────────────
from screener.sr_levels import detect_levels


def test_detect_levels_clasifica_y_ordena():
    bars = [_bar(100, 100) for _ in range(40)]
    for i in (5, 20):
        bars[i] = _bar(110, 100)
    for i in (12, 30):
        bars[i] = _bar(100, 90)
    zonas = detect_levels(bars)

    assert {z["tipo"] for z in zonas} == {"resistencia", "soporte"}
    res = [z for z in zonas if z["tipo"] == "resistencia"][0]
    sop = [z for z in zonas if z["tipo"] == "soporte"][0]
    assert res["toques"] == 2 and res["centro"] == 110.0
    assert sop["toques"] == 2 and sop["centro"] == 90.0
    assert [z["centro"] for z in zonas] == [90.0, 110.0]
