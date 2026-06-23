# tests/test_exposure_gate.py
from regime.exposure_gate import evaluar_gate, umbral_version, GateDecision

_ON = {"regime_gate": {"enabled": True, "umbral_overrides": {}}}
_OFF = {"regime_gate": {"enabled": False, "umbral_overrides": {}}}

def _gate(estado, frescura, votos, es_alt, cfg=_ON):
    return evaluar_gate(estado, frescura, votos, es_alt, cfg)

def test_btc_fresco_alt_enabled_suprime():
    assert _gate("btc", "fresco", 3, True).nivel == "suprime"

def test_btc_rancio_pasa_failopen():
    d = _gate("btc", "rancio", 3, True)
    assert d.nivel == "pasa" and d.enforced is False

def test_btc_muerto_pasa_failopen():
    assert _gate("btc", "muerto", 3, True).nivel == "pasa"

def test_btc_disabled_pasa():
    d = _gate("btc", "fresco", 3, True, cfg=_OFF)
    assert d.nivel == "pasa" and d.enforced is False

def test_btc_no_alt_pasa():
    assert _gate("btc", "fresco", 3, False).nivel == "pasa"  # BTC nunca se gatea

def test_alts_pasa():
    assert _gate("alts", "fresco", 3, True).nivel == "pasa"

def test_mixto_empate_atenua():
    assert _gate("mixto", "fresco", 3, True).nivel == "atenua"  # votos>=2 = empate genuino

def test_mixto_datos_degradados_pasa():
    assert _gate("mixto", "fresco", 1, True).nivel == "pasa"   # votos<2 = ausencia de señal

def test_estado_inesperado_pasa():
    assert _gate("ZZZ", "fresco", 3, True).nivel == "pasa"

def test_decision_carries_context():
    d = _gate("btc", "fresco", 3, True)
    assert d.estado_regimen == "btc" and d.es_alt is True and d.regime_frescura == "fresco"
    assert isinstance(d.umbral_version, str) and len(d.umbral_version) >= 6

def test_umbral_version_changes_with_overrides():
    base = umbral_version({"regime_gate": {"umbral_overrides": {}}})
    moved = umbral_version({"regime_gate": {"umbral_overrides": {"BREADTH_ALT": 0.7}}})
    assert base != moved

def test_cfg_vacio_failopen():
    d = evaluar_gate("btc", "fresco", 3, True, {})
    assert d.nivel == "pasa" and d.enforced is False
