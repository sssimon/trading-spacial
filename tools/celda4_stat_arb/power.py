"""Gate de poder de la celda 4 (spec §5/§6, F10/NV-B).

Corre y ESCRIBE `power.json` ANTES que `evaluate.py` (orden forzado del spec §5:
kill → poder → Gate A → LOO; ningún paso lee el resultado de uno posterior).
`evaluate.py` se NIEGA a correr si `power.json` no existe en su output_dir.

Regla pre-registrada (spec §5 NV-B, IRREVOCABLE): si
    MDE_annualized > POWER_MULT × T_FLOOR_v3w_annual
el estudio muere como N-INSUFICIENTE sin verdict. El ancla es auto-referente a
COSTOS ("el estudio debe poder ver un edge que importe — cómodamente encima de
sus propios costos"); NO ancla en efectos publicados (in-sample lavado por cita)
ni en el cardinal cross-mundo de carry (Voronov V2).

Determinismo: usa un numpy Generator DEDICADO sembrado con `SEED` (constants).
`evaluate.py` usa el MISMO seed independientemente para su propio bootstrap; los
dos generadores son objetos distintos sembrados igual → cada uno es determinista
por separado y no comparten estado (no hay acoplamiento de orden de consumo).

Unidades (documentadas por cantidad):
  - net por posición: $ (denominado sobre 2×NOTIONAL_PER_LEG desplegado).
  - MDE_dollars: $ — semi-ancho esperado del CI95 del estimador pooled (suma de
    nets por window), vía bootstrap por window.
  - median_cost_rate: adimensional (fracción del notional desplegado por
    round-trip) = costs / (2×NOTIONAL_PER_LEG).
  - median_holding_hours: horas (exit_time_ms − entry_time_ms en horas).
  - t_floor_annual: adimensional anualizado = median_cost_rate × (8760 /
    median_holding_hours) — el costo round-trip amortizado a tasa anual si se
    rotara la posición continuamente sobre el horizonte de holding observado.
  - MDE_annualized: adimensional anualizado = (MDE_dollars / (2×NOTIONAL_PER_LEG))
    × (8760 / median_holding_hours) — el MDE $ llevado a la MISMA moneda y
    horizonte que t_floor_annual (fracción del notional desplegado, anualizada
    por el mismo holding). Ambos lados quedan dimensionalmente consistentes.
"""
from __future__ import annotations

import json
import os
import statistics

import numpy as np

from .constants import BOOTSTRAP_N, NOTIONAL_PER_LEG, POWER_MULT, SEED

_HOURS_PER_YEAR = 8760.0
_DEPLOYED_NOTIONAL = 2.0 * NOTIONAL_PER_LEG
_HOUR_MS = 3_600_000


def _window_nets(positions: list[dict]) -> list[float]:
    """Suma de `net` por window (agrupado por window_start_ms), orden por window."""
    by_window: dict[int, float] = {}
    for p in positions:
        by_window[p["window_start_ms"]] = by_window.get(p["window_start_ms"], 0.0) + p["net"]
    return [by_window[k] for k in sorted(by_window)]


def compute_power(positions: list[dict], output_dir: str) -> dict:
    """Computa el gate de poder y ESCRIBE power.json ANTES de devolver (spec §5).

    Insumos (ambos outputs de la corrida; la REGLA y el MULTIPLICADOR están
    congelados en constants.py):
      - MDE_annualized: semi-ancho esperado del CI95 del estimador pooled (suma de
        nets por window), por bootstrap por window con BOOTSTRAP_N resamples y un
        Generator DEDICADO sembrado con SEED; llevado a fracción del notional
        desplegado y anualizado por la mediana del holding.
      - T_FLOOR_v3w_annual: mediana sobre posiciones de (costs / (2×NOTIONAL)),
        anualizada por la mediana del holding en horas.

    power_ok = MDE_annualized <= POWER_MULT × T_FLOOR_v3w_annual.

    Devuelve (y persiste en power.json) un dict con todas las cantidades y sus
    unidades implícitas (ver docstring del módulo). Escribe el artefacto ANTES de
    retornar — `evaluate.py` se niega a correr sin él (orden forzado F10).
    """
    n = len(positions)
    window_nets = _window_nets(positions)

    # MDE en $: semi-ancho esperado del CI95 del estimador pooled (suma de nets
    # por window) por bootstrap por window. Generator DEDICADO sembrado con SEED.
    rng = np.random.default_rng(SEED)
    if window_nets:
        arr = np.asarray(window_nets, dtype=float)
        idx = rng.integers(0, len(arr), size=(BOOTSTRAP_N, len(arr)))
        boot_sums = arr[idx].sum(axis=1)
        ci_lo = float(np.percentile(boot_sums, 2.5))
        ci_hi = float(np.percentile(boot_sums, 97.5))
        mde_dollars = (ci_hi - ci_lo) / 2.0
    else:
        ci_lo = ci_hi = 0.0
        mde_dollars = 0.0

    # T_FLOOR_v3w: mediana del costo round-trip / notional desplegado, anualizada
    # por la mediana del holding observado.
    if positions:
        cost_rates = [p["costs"] / _DEPLOYED_NOTIONAL for p in positions]
        holding_hours = [
            max((p["exit_time_ms"] - p["entry_time_ms"]) / _HOUR_MS, 0.0)
            for p in positions
        ]
        median_cost_rate = float(statistics.median(cost_rates))
        median_holding_hours = float(statistics.median(holding_hours))
    else:
        median_cost_rate = 0.0
        median_holding_hours = 0.0

    if median_holding_hours > 0.0:
        annualizer = _HOURS_PER_YEAR / median_holding_hours
        t_floor_annual = median_cost_rate * annualizer
        mde_annualized = (mde_dollars / _DEPLOYED_NOTIONAL) * annualizer
    else:
        # Sin holding observable no hay anualización definida; power gate no puede
        # afirmarse → power_ok False (conservador: el estudio no demostró poder).
        t_floor_annual = 0.0
        mde_annualized = float("inf") if mde_dollars > 0.0 else 0.0

    threshold = POWER_MULT * t_floor_annual
    power_ok = bool(mde_annualized <= threshold) if median_holding_hours > 0.0 else False

    result = {
        "n_positions": n,
        "n_windows": len(window_nets),
        "deployed_notional": _DEPLOYED_NOTIONAL,
        "mde_dollars": mde_dollars,
        "mde_pooled_ci": {"lo": ci_lo, "hi": ci_hi},
        "mde_annualized": mde_annualized,
        "median_cost_rate": median_cost_rate,
        "median_holding_hours": median_holding_hours,
        "t_floor_v3w_annual": t_floor_annual,
        "power_mult": POWER_MULT,
        "threshold": threshold,
        "power_ok": power_ok,
        "seed": SEED,
        "bootstrap_n": BOOTSTRAP_N,
    }

    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "power.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    return result
