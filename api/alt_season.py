"""API del régimen de mercado "¿es alt-season?" (Valles). Read-only, NO per-tenant —
el universo de mercado es global.

Lee la foto que escribe tools.run_valley_screener (misma pasada del screener, owner
de frescura = screener_loop). NO computa nada en el request. Eje MERCADO: hecho, no
veredicto per-símbolo."""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter

from api.valleys import FRESCURA_VALLES_SEG   # mismo writer/loop → misma semántica
from freshness import LiveSnapshot
from regime.alt_season_read import leer_regimen

log = logging.getLogger("api.alt_season")

router = APIRouter(tags=["alt-season"])

_OUTPUT = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       "data", "alt_season.json")


@router.get("/alt-season",
            summary="Régimen de mercado ¿es alt-season? (hecho de mercado, no veredicto)")
def get_alt_season() -> dict:
    """Devuelve la foto del régimen con su FRESCURA en el contrato. Archivo ausente →
    'muerto' (el screener no ha corrido), distinto de una foto vieja → 'rancio'.
    'fresco' = el cálculo es reciente, NO que la afirmación de mercado siga vigente."""
    rv = leer_regimen(FRESCURA_VALLES_SEG, ruta=_OUTPUT)
    # LiveSnapshot re-computa la frescura con el MISMO umbral, preservando el
    # contrato actual de /alt-season (generated_at vs umbral_seg, nunca campo leído).
    return LiveSnapshot(payload=rv.snapshot, generated_at=rv.generated_at,
                        umbral_seg=FRESCURA_VALLES_SEG).to_response()
