"""Tests del orquestador del dossier (Exa + extracción inyectadas).

Cubre: extracción al esquema, candado anti-alucinación, opaco vs no_disponible,
prohibición de opinión en el prompt. Spec §3."""
import pytest

from research.dossier import build_dossier, EXTRACTION_PROMPT
from research.exa_client import ExaUnavailable


def _exa_ok(query):
    # Mismo set de URLs para todos los dominios; el candado las usa como ancla.
    return [
        {"title": "Cardano", "url": "https://cardano.org", "text": "CEO Charles Hoskinson"},
        {"title": "GitHub", "url": "https://github.com/input-output-hk", "text": "último commit 2026-05"},
    ]


def test_extrae_al_esquema_con_estado_rastreable():
    def extract(content, prompt):
        return {
            "equipo": [{"nombre": "Charles Hoskinson", "rol": "CEO",
                        "enlaces": [], "fuente": "https://cardano.org"}],
            "equipo_identificado": True,
            "presencia": {"sitio_web": {"url": "https://cardano.org", "activo": "si",
                                        "fuente": "https://cardano.org"}},
            "actividad": {"ultimo_commit_github": {"valor": "2026-05",
                          "fuente": "https://github.com/input-output-hk"}},
            "financiacion": [], "hitos": [],
        }
    d = build_dossier("ADAUSDT", exa_search=_exa_ok, extract_fn=extract)
    assert d.estado_general == "rastreable"
    assert d.equipo[0].nombre == "Charles Hoskinson"
    assert d.symbol == "ADAUSDT"


def test_candado_anti_alucinacion_descarta_cita_inventada():
    def extract(content, prompt):
        return {
            "equipo": [
                {"nombre": "Real", "fuente": "https://cardano.org"},          # ✓ en el set
                {"nombre": "Inventado", "fuente": "https://fake-no-existe.xyz"},  # ✗ alucinada
            ],
            "equipo_identificado": True, "presencia": {}, "actividad": {},
            "financiacion": [], "hitos": [],
        }
    d = build_dossier("ADAUSDT", exa_search=_exa_ok, extract_fn=extract)
    nombres = [m.nombre for m in d.equipo]
    assert "Real" in nombres
    assert "Inventado" not in nombres   # cita fuera del set de Exa → descartada


def test_exa_caido_es_no_disponible_no_opaco():
    def exa_falla(query):
        raise ExaUnavailable("rate banned")
    d = build_dossier("ADAUSDT", exa_search=exa_falla, extract_fn=lambda c, p: {})
    assert d.estado_general == "no_disponible"   # fallo técnico, NO opaco


def test_exa_vacio_es_opaco_legitimo():
    def exa_vacio(query):
        return []   # buscó y no encontró
    def extract(content, prompt):
        return {"equipo": [], "equipo_identificado": False, "presencia": {},
                "actividad": {}, "financiacion": [], "hitos": []}
    d = build_dossier("XYZUSDT", exa_search=exa_vacio, extract_fn=extract)
    assert d.estado_general == "opaco"
    assert d.no_encontrado_en   # lista de qué se buscó y no apareció


def test_extraccion_invalida_es_no_disponible():
    def extract(content, prompt):
        raise RuntimeError("deepseek timeout")
    d = build_dossier("ADAUSDT", exa_search=_exa_ok, extract_fn=extract)
    assert d.estado_general == "no_disponible"


def test_prompt_prohibe_opinar():
    p = EXTRACTION_PROMPT.lower()
    assert "prohibido" in p
    for verbo in ("opinar", "evaluar", "recomendar", "predecir", "calificar"):
        assert verbo in p


def test_extract_malformado_anclado_es_no_disponible():
    # Hecho anclado (fuente en el set) pero estructuralmente inválido (sin 'nombre').
    def extract(content, prompt):
        return {"equipo": [{"fuente": "https://cardano.org"}],  # falta 'nombre' → ValidationError
                "presencia": {}, "actividad": {}, "financiacion": [], "hitos": []}
    d = build_dossier("ADAUSDT", exa_search=_exa_ok, extract_fn=extract)
    assert d.estado_general == "no_disponible"


def test_extract_no_dict_es_no_disponible():
    def extract(content, prompt):
        return ["esto no es un dict"]   # crudo.get(...) → AttributeError
    d = build_dossier("ADAUSDT", exa_search=_exa_ok, extract_fn=extract)
    assert d.estado_general == "no_disponible"


def test_prompt_incluye_el_esquema_objetivo():
    # Regresión: DeepSeek necesita las claves EXACTAS del esquema en el prompt, o
    # inventa su propia estructura (devolvió {"hechos": ...} → todo opaco en prod).
    p = EXTRACTION_PROMPT
    for clave in ("equipo", "equipo_identificado", "presencia", "actividad",
                  "financiacion", "hitos"):
        assert f'"{clave}"' in p, f"el prompt debe especificar la clave {clave!r}"
