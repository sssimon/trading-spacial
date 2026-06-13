"""API del dossier de due-diligence (C). GET /dossier/{symbol} con caché TTL.

Read-only respecto al estado del usuario, NO per-tenant (el dossier de un
proyecto es global). La generación (Exa + DeepSeek) corre FUERA de toda
transacción (red); solo el upsert de caché va en una tx corta. Spec §3.1, §4.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query

from db.dossiers import db_get_dossier, db_put_dossier
from db.transaction import snapshot_connection, transaction
from freshness import LiveSnapshot
from research.dossier import build_dossier_live

log = logging.getLogger("api.dossier")

router = APIRouter(tags=["dossier"])

_TTL_SECONDS = 7 * 24 * 3600   # 7 días (spec §5)
FRESCURA_DOSSIER_SEG = 7 * 24 * 3600


def _fresh(generated_at: str) -> bool:
    """¿La foto de caché sigue dentro del TTL? Tolera timestamps naive o con
    offset (normaliza a UTC); un valor no parseable cuenta como stale."""
    try:
        ts = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    return age < _TTL_SECONDS


@router.get("/dossier/{symbol}", summary="Dossier de hechos citados de un proyecto")
def get_dossier(symbol: str, refresh: bool = Query(False)) -> dict:
    """Devuelve el dossier del símbolo. Caché-hit fresco → lo sirve; miss o
    `refresh=true` o caché stale → genera (Exa+DeepSeek), cachea y devuelve.
    El dossier NUNCA 500ea por fallo externo: build_dossier_live devuelve un
    Dossier con estado 'no_disponible' si Exa/DeepSeek fallan."""
    symbol = symbol.upper()
    if len(symbol) > 20:
        symbol = symbol[:20]

    if not refresh:
        with snapshot_connection() as con:
            cached = db_get_dossier(con, symbol)
        if cached is not None and _fresh(cached["generated_at"]):
            try:
                hit = json.loads(cached["dossier_json"])
                return LiveSnapshot(payload=hit,
                                    generated_at=hit.get("generated_at"),
                                    umbral_seg=FRESCURA_DOSSIER_SEG).to_response()
            except json.JSONDecodeError:
                log.warning("DOSSIER_CACHE_CORRUPTA symbol=%s — regenerando", symbol)
                # cae a regeneración abajo

    # ── Generación: red FUERA de la tx. ──
    # Race aceptada: dos requests simultáneas del mismo símbolo sin caché ambas
    # generan (doble gasto Exa); el segundo INSERT OR REPLACE gana sin corromper.
    dossier = build_dossier_live(symbol)
    generated_at = datetime.now(timezone.utc).isoformat()
    dossier.generated_at = generated_at
    payload = dossier.model_dump()

    # ── Caché: tx corta, sin I/O. NO cachear los 'no_disponible' (fallo
    #    técnico transitorio — que el próximo intento reintente). ──
    if dossier.estado_general != "no_disponible":
        with transaction() as con:
            db_put_dossier(con, symbol=symbol,
                           dossier_json=json.dumps(payload, ensure_ascii=False),
                           generated_at=generated_at)
    return LiveSnapshot(payload=payload,
                        generated_at=payload.get("generated_at"),
                        umbral_seg=FRESCURA_DOSSIER_SEG).to_response()
