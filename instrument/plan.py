"""Derivación del plan del instrumento (Fase 1) — puro, sin red, sin DB.

Dado las zonas de D.1 (screener.sr_levels.detect_levels) + el precio de entrada,
produce un Plan inmutable: SL bajo el soporte, escalera de TPs en las resistencias,
runner OPEN TARGET, regla break-even tras TP1. El plan es DISCIPLINADO, no afirma
rentabilidad (spec §1, §4). Hermano de screener/sr_levels.py."""
from __future__ import annotations

from dataclasses import dataclass

# ── Constantes de arranque (calibrables, spec §4) ───────────────────────────
MAX_RUNGS = 4
SL_MARGIN_PCT = 0.01               # colchón bajo el borde del soporte
RUNNER_ON = True                   # reserva la fracción OPEN TARGET
SIZE_SCHEDULE = [0.50, 0.20, 0.15, 0.10]  # front-loaded; el resto va al runner
RUNNER_FRAC = 0.05


@dataclass(frozen=True)
class Rung:
    tp_price: float
    size_frac: float
    zona_origen: dict


@dataclass(frozen=True)
class Plan:
    entry_price: float
    entry_zone: dict | None
    sl_price: float
    rungs: tuple   # tuple[Rung, ...], ascendente por tp_price — inmutable (BNC)
    runner_frac: float


def derive_plan(zonas: list[dict], entry_price: float, *,
                runner_on: bool = RUNNER_ON) -> Plan:
    """Deriva el Plan desde las zonas de D.1. Las resistencias sobre el entry son
    los TPs (cap MAX_RUNGS); el soporte inmediato fija el SL; los tamaños van
    front-loaded con TP1 ≥ 50%; el runner queda abierto (spec §4)."""
    resistencias = sorted(
        [z for z in zonas if z["tipo"] == "resistencia" and z["centro"] > entry_price],
        key=lambda z: z["centro"],
    )[:MAX_RUNGS]

    soportes_abajo = [z for z in zonas
                      if z["tipo"] == "soporte" and z["precio_alto"] < entry_price]
    soporte = max(soportes_abajo, key=lambda z: z["centro"]) if soportes_abajo else None

    entry_zone = next(
        (z for z in zonas if z["tipo"] == "soporte"
         and z["precio_bajo"] <= entry_price <= z["precio_alto"]),
        None,
    )

    base = soporte["precio_bajo"] if soporte is not None else entry_price
    sl_price = base * (1 - SL_MARGIN_PCT)

    runner = RUNNER_FRAC if runner_on else 0.0
    n = len(resistencias)
    if n == 0:
        return Plan(entry_price=entry_price, entry_zone=entry_zone, sl_price=sl_price,
                    rungs=(), runner_frac=(1.0 if runner_on else 0.0))

    fracs = SIZE_SCHEDULE[:n]
    total = sum(fracs)
    scaled = [f / total * (1 - runner) for f in fracs]
    # Garantía front-loaded: TP1 nunca baja de 0.50 por aritmética float
    if scaled and scaled[0] < 0.50:
        deficit = 0.50 - scaled[0]
        scaled[0] = 0.50
        # Redistribuir el déficit quitándolo del resto (pro-rata)
        rest_sum = sum(scaled[1:])
        if rest_sum > 0:
            scaled[1:] = [s - deficit * s / rest_sum for s in scaled[1:]]
        else:
            # Absorber déficit del runner si no hay rungs secundarios
            runner = max(0.0, runner - deficit)
    rungs = tuple(Rung(tp_price=z["centro"], size_frac=s, zona_origen=z)
                  for z, s in zip(resistencias, scaled))
    return Plan(entry_price=entry_price, entry_zone=entry_zone, sl_price=sl_price,
                rungs=rungs, runner_frac=runner)
