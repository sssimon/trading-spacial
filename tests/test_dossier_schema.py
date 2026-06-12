"""Tests del schema del dossier (estricto, sin campo de opinión). Spec §2."""
import pytest
from pydantic import ValidationError

from research.schemas import Dossier, MiembroEquipo, Canal, Cita, Hito


def test_dossier_minimo_valido():
    d = Dossier(symbol="ADAUSDT", estado_general="opaco")
    assert d.symbol == "ADAUSDT"
    assert d.equipo == []
    assert d.equipo_identificado is False
    assert d.no_encontrado_en == []


def test_estado_general_solo_acepta_los_tres_valores():
    for ok in ("rastreable", "opaco", "no_disponible"):
        Dossier(symbol="X", estado_general=ok)
    with pytest.raises(ValidationError):
        Dossier(symbol="X", estado_general="prometedor")   # no es un hallazgo válido


def test_schema_no_tiene_campo_de_opinion():
    # Frontera de Voronov: ningún campo de opinión/potencial/score/recomendación.
    campos = set(Dossier.model_fields)
    prohibidos = {"veredicto", "opinion", "potencial", "score", "recomendacion",
                  "rating", "calidad", "prediccion"}
    assert campos.isdisjoint(prohibidos)


def test_extra_forbid_rechaza_campos_no_declarados():
    with pytest.raises(ValidationError):
        Dossier(symbol="X", estado_general="opaco", veredicto="bueno")  # extra → rechazado


def test_miembro_equipo_y_canal_y_cita_y_hito():
    m = MiembroEquipo(nombre="Charles Hoskinson", rol="CEO",
                      enlaces=["https://x.com/IOHK_Charles"], fuente="https://iohk.io")
    assert m.rol == "CEO"
    c = Canal(url="https://cardano.org", activo="si", fuente="https://cardano.org")
    assert c.activo == "si"
    cita = Cita(valor="2026-05-01", fuente="https://github.com/...")
    assert cita.fuente.startswith("https://")
    h = Hito(descripcion="Mainnet launch", fecha="2017-09", fuente="https://...")
    assert h.descripcion
