"""GET /baseline — yardstick descriptivo del baseline cured-random (anti-veredicto).
Lee el estado persistido por el freshness owner y lo envuelve en LiveSnapshot. NO
surfacea picks individuales (se leerían como señal); solo el agregado distribucional."""
from __future__ import annotations

from fastapi import APIRouter

from freshness import LiveSnapshot
from scanner.baseline.store import load

baseline_router = APIRouter()
_UMBRAL_SEG = 26 * 3600  # ~26h: un tick diario sano queda fresco; owner muerto -> muerto

_NOTA = ("Yardstick descriptivo del azar curado (entrada random + escalera + freno de "
         "drawdown). Mide contra qué compararte; no es una señal ni te dice qué comprar.")


def get_baseline() -> dict:
    ensemble, generated_at = load()
    payload = ensemble.snapshot() if ensemble is not None else {
        "mediana": None, "banda_p10": None, "banda_p90": None,
        "n_seeds": 0, "tier_mediana": None, "last_date": None,
    }
    payload["nota"] = _NOTA
    return LiveSnapshot(payload=payload, generated_at=generated_at,
                        umbral_seg=_UMBRAL_SEG).to_response()


@baseline_router.get("/baseline", summary="Baseline cured-random (yardstick descriptivo)")
def baseline_endpoint() -> dict:
    return get_baseline()
