"""API del régimen de mercado "¿es alt-season?" (Valles). Read-only, NO per-tenant —
el universo de mercado es global.

Lee la foto que escribe tools.run_valley_screener (misma pasada del screener, owner
de frescura = screener_loop). NO computa nada en el request. Eje MERCADO: hecho, no
veredicto per-símbolo."""
from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter

from api.valleys import FRESCURA_VALLES_SEG   # mismo writer/loop → misma semántica
from freshness import LiveSnapshot

log = logging.getLogger("api.alt_season")

router = APIRouter(tags=["alt-season"])

_OUTPUT = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       "data", "alt_season.json")

_EMPTY = {
    "generated_at": None,
    "coverage": {"universe": 0, "evaluated": 0, "complete": False},
    "dominancia_fetch": {"ok": False, "fetched_at": None, "source": "coingecko/global"},
    "regime": {"estado": "mixto", "componentes": {},
               "votos": {"alts": 0, "neutral": 0, "btc": 0, "vivos": 0},
               "n_alts_evaluadas": 0},
}


@router.get("/alt-season",
            summary="Régimen de mercado ¿es alt-season? (hecho de mercado, no veredicto)")
def get_alt_season() -> dict:
    """Devuelve la foto del régimen con su FRESCURA en el contrato. Archivo ausente →
    'muerto' (el screener no ha corrido), distinto de una foto vieja → 'rancio'.
    'fresco' = el cálculo es reciente, NO que la afirmación de mercado siga vigente."""
    if not os.path.exists(_OUTPUT):
        return LiveSnapshot(payload=dict(_EMPTY), generated_at=None,
                            umbral_seg=FRESCURA_VALLES_SEG).to_response()
    try:
        with open(_OUTPUT, encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.error("ALT_SEASON_SNAPSHOT_UNREADABLE causa=%s", e)
        snap = dict(_EMPTY)
    return LiveSnapshot(payload=snap, generated_at=snap.get("generated_at"),
                        umbral_seg=FRESCURA_VALLES_SEG).to_response()
