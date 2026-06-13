"""Tests del tipo de frescura (liveness operacional). Puro. Spec §2."""
from datetime import datetime, timedelta, timezone

from freshness import LiveSnapshot, classify_freshness


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hace(horas):
    return (datetime.now(timezone.utc) - timedelta(hours=horas)).isoformat()


def test_muerto_si_nunca_generado():
    assert LiveSnapshot(payload={}, generated_at=None, umbral_seg=3600).estado == "muerto"


def test_fresco_si_reciente():
    assert LiveSnapshot(payload={}, generated_at=_now(), umbral_seg=3600).estado == "fresco"


def test_rancio_si_viejo():
    assert LiveSnapshot(payload={}, generated_at=_hace(2), umbral_seg=3600).estado == "rancio"


def test_no_parseable_es_muerto():
    assert LiveSnapshot(payload={}, generated_at="basura", umbral_seg=3600).estado == "muerto"


def test_to_response_siempre_inyecta_frescura():
    r = LiveSnapshot(payload={"a": 1}, generated_at=None, umbral_seg=3600).to_response()
    assert r["a"] == 1
    assert r["frescura"]["estado"] == "muerto"
    assert r["frescura"]["generated_at"] is None


def test_classify_freshness_atajo():
    assert classify_freshness(_now(), 3600) == "fresco"
    assert classify_freshness(None, 3600) == "muerto"
