"""Tests del lector compartido del snapshot de régimen alt-season.
TDD Task 3 — los 5 casos del brief (2026-06-23)."""
import json
from datetime import datetime, timezone, timedelta
from regime.alt_season_read import leer_regimen, RegimenVivo


def _write(tmp_path, generated_at, estado="btc", vivos=3):
    p = tmp_path / "alt_season.json"
    p.write_text(json.dumps({
        "generated_at": generated_at,
        "regime": {"estado": estado, "votos": {"vivos": vivos}},
    }), encoding="utf-8")
    return str(p)


def test_ausente_es_muerto(tmp_path):
    r = leer_regimen(27000, ruta=str(tmp_path / "no_existe.json"))
    assert r.frescura == "muerto"


def test_corrupto_es_muerto(tmp_path):
    p = tmp_path / "alt_season.json"; p.write_text("{ not json", encoding="utf-8")
    r = leer_regimen(27000, ruta=str(p))
    assert r.frescura == "muerto"


def test_generated_at_viejo_es_rancio(tmp_path):
    viejo = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    r = leer_regimen(27000, ruta=_write(tmp_path, viejo))  # 27000s = 7.5h < 10h
    assert r.frescura == "rancio"


def test_generated_at_reciente_es_fresco(tmp_path):
    reciente = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    r = leer_regimen(27000, ruta=_write(tmp_path, reciente, estado="alts", vivos=2))
    assert r.frescura == "fresco" and r.estado == "alts" and r.votos_vivos == 2


def test_failopen_combinado_con_gate(tmp_path):
    # El test que ATRAPA el fail-open silencioso: clima 'btc' viejo → 'rancio' →
    # el gate NO debe esconder (enforced=False).
    from regime.exposure_gate import evaluar_gate
    viejo = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    r = leer_regimen(27000, ruta=_write(tmp_path, viejo, estado="btc"))
    d = evaluar_gate(r.estado, r.frescura, r.votos_vivos, True,
                     {"regime_gate": {"enabled": True, "umbral_overrides": {}}})
    assert d.enforced is False and d.nivel == "pasa"
