"""Gate de exposición por régimen — política PURA (sin red, sin DB). Espejo
estructural de regime/alt_season.py: solo funciones puras, cero I/O.

Decide pasa|atenua|suprime desde el ESTADO del régimen + su FRESCURA. Un hecho
de exposición de mercado, NO un veredicto per-coin. Spec 2026-06-23."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from regime.alt_season import effective_thresholds


@dataclass(frozen=True)
class GateDecision:
    nivel: str            # "pasa" | "atenua" | "suprime"
    estado_regimen: str   # "alts" | "mixto" | "btc"
    es_alt: bool
    regime_frescura: str  # "fresco" | "rancio" | "muerto" (COMPUTADA por el orquestador)
    votos_vivos: int
    razon: str
    enforced: bool        # = cfg.regime_gate.enabled AND regime_frescura == "fresco"
    umbral_version: str


def umbral_version(cfg: dict) -> str:
    """Sello determinista de los umbrales EFECTIVOS (6 de lean + 2 de gobierno de
    evidencia + overrides). Ata cada fila de auditoría a la calibración exacta."""
    overrides = (cfg.get("regime_gate") or {}).get("umbral_overrides") or {}
    eff = effective_thresholds(overrides)
    blob = json.dumps(eff, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def evaluar_gate(estado: str, frescura: str, votos_vivos: int,
                 es_alt: bool, cfg: dict) -> GateDecision:
    """Política graduada. Fail-open sobre clima rancio/muerto y sobre flag off."""
    rg = cfg.get("regime_gate") or {}
    enforced = bool(rg.get("enabled", False)) and frescura == "fresco"
    eff = effective_thresholds(rg.get("umbral_overrides") or {})
    min_live = eff["MIN_LIVE_VOTERS"]

    if not enforced:
        nivel, razon = "pasa", "gate inactivo (flag off o régimen no fresco)"
    elif not es_alt:
        nivel, razon = "pasa", "símbolo no-alt — el gate es sobre exposición a alts"
    elif estado == "alts":
        nivel, razon = "pasa", "régimen 'alts' — el viento acompaña"
    elif estado == "btc":
        nivel, razon = "suprime", "régimen 'btc' — el viento no acompaña a las alts"
    elif estado == "mixto":
        if votos_vivos < min_live:
            nivel, razon = "pasa", "mixto por datos degradados — ausencia de señal, no clima"
        else:
            nivel, razon = "atenua", "régimen 'mixto' — clima ambiguo"
    else:
        nivel, razon = "pasa", f"estado inesperado '{estado}' — fail-open"

    return GateDecision(
        nivel=nivel, estado_regimen=estado, es_alt=es_alt,
        regime_frescura=frescura, votos_vivos=votos_vivos, razon=razon,
        enforced=enforced, umbral_version=umbral_version(cfg),
    )
