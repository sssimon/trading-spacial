"""API de la Vista Valles (sub-proyecto A). Read-only, NO per-tenant — el
universo de mercado es global (spec §0, §5.3).

Lee la foto regenerable que escribe tools.run_valley_screener. NO computa nada
en el request (el fetch de 200+ símbolos es pesado y vive en el comando).

F3b: GET /valley-eval/{symbol} — A on-demand para UN símbolo arbitrario.
Red fuera de tx, sin caché, read-only. Spec §2."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import requests
from fastapi import APIRouter

from api.levels import BinanceUnavailable, _fetch_daily_bars
from freshness import LiveSnapshot
from screener.valley_filter import classify_liveness, evaluate_symbol

log = logging.getLogger("api.valleys")

router = APIRouter(tags=["valleys"])

_OUTPUT = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       "data", "valley_candidates.json")

FRESCURA_VALLES_SEG = 43200   # 12 horas
FRESCURA_VALLEY_EVAL_SEG = 60   # evaluación viva, computada fresca cada request

_EMPTY = {"generated_at": None,
          "coverage": {"universe": 0, "evaluated": 0, "complete": False},
          "candidates": []}


@router.get("/valley-eval/{symbol}", summary="Evalúa vida + rango de UNA moneda (A on-demand)")
def get_valley_eval(symbol: str) -> dict:
    """A para un símbolo arbitrario: fetch de velas (reusa D.1) + evaluate_symbol
    (puro). Devuelve los hechos si está VIVA y EN RANGO; si no, reporta POR QUÉ
    (razones de liveness — hechos, no juicio de atractivo). no_disponible si la
    red falla. Read-only, red fuera de tx, sin caché. Spec §2."""
    symbol = symbol.upper()[:20]
    now = datetime.now(timezone.utc).isoformat()
    try:
        bars = _fetch_daily_bars(symbol)
    except (requests.RequestException, BinanceUnavailable) as e:
        log.warning("VALLEY_EVAL_NO_DISPONIBLE symbol=%s causa=%s", symbol, e)
        return LiveSnapshot(payload={"symbol": symbol, "estado": "no_disponible"},
                            generated_at=None, umbral_seg=FRESCURA_VALLEY_EVAL_SEG).to_response()
    cand = evaluate_symbol(symbol, bars)
    if cand is None:
        vivo, razones = classify_liveness(bars)
        payload = {"symbol": symbol, "estado": "ok", "candidata": False,
                   "vivo": vivo, "razones_muerte": razones, "generated_at": now}
    else:
        payload = {"symbol": symbol, "estado": "ok", "candidata": True,
                   "generated_at": now, **cand}
    return LiveSnapshot(payload=payload, generated_at=now,
                        umbral_seg=FRESCURA_VALLEY_EVAL_SEG).to_response()


@router.get("/valley-candidates", summary="Candidatas del screener de valles (vivas + en rango)")
def get_valley_candidates() -> dict:
    """Devuelve la foto del screener con su FRESCURA en el contrato. Archivo
    ausente → estado 'muerto' (el screener no ha corrido), distinto de una foto
    vieja → 'rancio'. No más vacío mudo."""
    if not os.path.exists(_OUTPUT):
        return LiveSnapshot(payload=dict(_EMPTY), generated_at=None,
                            umbral_seg=FRESCURA_VALLES_SEG).to_response()
    try:
        with open(_OUTPUT, encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.error("VALLEY_SNAPSHOT_UNREADABLE causa=%s", e)
        snap = dict(_EMPTY)
    return LiveSnapshot(payload=snap, generated_at=snap.get("generated_at"),
                        umbral_seg=FRESCURA_VALLES_SEG).to_response()
