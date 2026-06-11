"""API de la Vista Valles (sub-proyecto A). Read-only, NO per-tenant — el
universo de mercado es global (spec §0, §5.3).

Lee la foto regenerable que escribe tools.run_valley_screener. NO computa nada
en el request (el fetch de 200+ símbolos es pesado y vive en el comando)."""
from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter

log = logging.getLogger("api.valleys")

router = APIRouter(tags=["valleys"])

_OUTPUT = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       "data", "valley_candidates.json")

_EMPTY = {"generated_at": None,
          "coverage": {"universe": 0, "evaluated": 0, "complete": False},
          "candidates": []}


@router.get("/valley-candidates", summary="Candidatas del screener de valles (vivas + en rango)")
def get_valley_candidates() -> dict:
    """Devuelve la foto del screener. Si aún no se ha corrido el comando, la
    respuesta es vacía con complete=False (no 500) — la UI muestra 'sin foto'."""
    if not os.path.exists(_OUTPUT):
        return dict(_EMPTY)
    try:
        with open(_OUTPUT, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.error("VALLEY_SNAPSHOT_UNREADABLE causa=%s", e)
        return dict(_EMPTY)
