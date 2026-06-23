"""Lector compartido del snapshot de régimen alt-season. ÚNICO path de lectura
de data/alt_season.json (lo usan la API y el hook del scanner). Computa la
frescura vía freshness.LiveSnapshot — la frescura NO está persistida en el JSON.
Maneja archivo ausente / corrupto → 'muerto' (fail-open). Spec 2026-06-23 §1."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from freshness import LiveSnapshot

log = logging.getLogger("regime.alt_season_read")

_DEFAULT_RUTA = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                             "data", "alt_season.json")

_EMPTY = {
    "generated_at": None,
    "coverage": {"universe": 0, "evaluated": 0, "complete": False},
    "dominancia_fetch": {"ok": False, "fetched_at": None, "source": "coingecko/global"},
    "regime": {"estado": "mixto", "componentes": {},
               "votos": {"alts": 0, "neutral": 0, "btc": 0, "vivos": 0},
               "n_alts_evaluadas": 0},
}


@dataclass(frozen=True)
class RegimenVivo:
    estado: str
    frescura: str          # "fresco" | "rancio" | "muerto"
    votos_vivos: int
    generated_at: str | None
    snapshot: dict


def _leer_snapshot(ruta: str) -> dict:
    """Lee el JSON con guard de ausencia/corrupción → _EMPTY (frescura derivará 'muerto')."""
    if not os.path.exists(ruta):
        return dict(_EMPTY)
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.error("ALT_SEASON_SNAPSHOT_UNREADABLE causa=%s", e)
        return dict(_EMPTY)


def leer_regimen(umbral_seg: float, ruta: str | None = None) -> RegimenVivo:
    """Estado + frescura del régimen. La frescura se COMPUTA (generated_at vs
    umbral_seg), no se lee de un campo. umbral_seg lo pasa el llamador (la API usa
    el de la UI; el gate del scanner usa el suyo, más estricto)."""
    snap = _leer_snapshot(ruta or _DEFAULT_RUTA)
    generated_at = snap.get("generated_at")
    frescura = LiveSnapshot(payload={}, generated_at=generated_at, umbral_seg=umbral_seg).estado
    regime = snap.get("regime") or {}
    estado = regime.get("estado", "mixto")
    votos_vivos = int((regime.get("votos") or {}).get("vivos", 0))
    return RegimenVivo(estado=estado, frescura=frescura, votos_vivos=votos_vivos,
                       generated_at=generated_at, snapshot=snap)
