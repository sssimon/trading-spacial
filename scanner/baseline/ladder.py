"""Escalera de salida CONGELADA (verbatim del estudio curar_azar / confirm_study).
Aritmética pura: vende fracciones en targets ascendentes si el máximo alto los tocó;
piso -50% si ningún target y el mínimo bajo lo perforó; runner al cierre final.
Producción posee su copia (no importa de data/retune/)."""
from __future__ import annotations

TPS = [0.15, 0.30, 0.50, 0.90]
FRACS = [0.25, 0.25, 0.20, 0.15]
DISASTER = -0.50
HORIZON = 30


def ladder_return(entry: float, hi_max: float, lo_min: float,
                  close_last: float | None) -> float | None:
    """Retorno realizado de la escalera. `hi_max`/`lo_min` = extremos de la ventana
    [entry+1 .. entry+HORIZON]; `close_last` = cierre del último día. None si inválido."""
    if entry is None or entry <= 0 or close_last is None:
        return None
    realized = 0.0
    sold = 0.0
    for tp, fr in zip(TPS, FRACS):
        if hi_max >= entry * (1 + tp):
            realized += fr * tp
            sold += fr
        else:
            break
    if sold == 0.0 and lo_min <= entry * (1 + DISASTER):
        return DISASTER
    runner_frac = 1.0 - sold
    runner_ret = (close_last - entry) / entry
    return realized + runner_frac * runner_ret
