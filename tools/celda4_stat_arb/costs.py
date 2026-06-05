"""v3w — la moneda de costos de ESTA celda (spec §3-bis).

**v3w es una extensión DECLARADA de v3 con procedencia propia.** v3
(`backtest_costs`) está CERRADO sobre 10 símbolos curados: `tier_for_symbol`
lanza `UnknownSymbolError` fuera de dominio — el código se niega, no aproxima.
Aplicar "v3" al universo ancho de pares sería extrapolación renombrada como
calibración. v3w resuelve esto reasignando el tier POR VOLUMEN (regla declarada,
cortes derivados de los 10 curados) y reusando los parámetros POR TIER de
`costs_calibration.json` SIN modificarlos.

Por lo tanto: **el verdict de esta celda es net-of-v3w, NO net-of-v3.** La
comparación cardinal con el PASS de carry (net-of-v3 en su dominio) queda
**prohibida a nivel de tipo** salvo re-pricing explícito a moneda común (spec
§3-bis, §5).

Reuso de v3 (verificable, sin inventar fórmula):
  - `backtest_costs.load_calibration()` → `Calibration` con `tiers[t]`
    (`TierParams` por tier) y `global_` (`GlobalParams`).
  - El costo de UN fill v3w es el "floor leg" de v3, idéntico a la primera línea
    de `backtest_costs._v3_leg_cost` (backtest_costs.py:446):
        floor_leg_bps = tp.stress_mult * (tp.half_spread_bps + tp.fee_bps_per_side)
    Es el cuerpo size-INDEPENDIENTE de v3 (spread+fee por fill, stress-escalado),
    la cota dominante en el régimen de operación ($10k notional). El "tail"
    Almgren-Chriss de `_v3_leg_cost` depende de `liquidity_usd_per_min` por fill;
    la firma de `v3w_fill_cost` no transporta liquidez (el costo de la celda es
    por-fill sobre tier, no por-orderbook), y v3 documenta el tail como inerte
    (floor-dominado) al notional de operación (costs_calibration.json
    sensitivity_note). NO se inventa fórmula: se reusa exactamente el floor leg.
  - dollar_cost = floor_leg_bps * notional_usd / 10_000  (bps → $, igual que v3:
    total_cost_usd = total_cost_bps * notional / 10_000, backtest_costs.py:547).

NOTA de tier: v3w nombra los tiers "large"/"mid"/"small" (spec); v3 nombra el
tier superior "major". La equivalencia es large ≡ major; el mapeo a los
`TierParams` de la calibración traduce "large" → "major".
"""
from __future__ import annotations

import sqlite3
import statistics
from contextlib import closing
from datetime import datetime, timezone

from backtest_costs import _TIER_BY_SYMBOL, tier_for_symbol

from .constants import V3W_REFERENCE_WINDOW

# v3w tier name → v3 calibration tier key. v3w usa "large"; v3 usa "major".
_V3W_TO_V3_TIER = {"large": "major", "mid": "mid", "small": "small"}
# v3 tier key → v3w tier name (para la derivación: agrupar curados por su tier v3).
_V3_TO_V3W_TIER = {"major": "large", "mid": "mid", "small": "small"}


def _to_ms(date_str: str) -> int:
    """Fecha 'YYYY-MM-DD' (UTC, medianoche) → epoch ms."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _median_daily_dollar_vol(con, symbol: str, start_ms: int, end_ms: int) -> float | None:
    """Mediana sobre días del dollar-volume diario de un símbolo en [start, end).

    dollar_volume_diario = Σ_barras-del-día (volume × close)  (spec §4 / §3-bis).
    La mediana es sobre los días con al menos una barra. Devuelve None si no hay
    barras en la ventana.
    """
    # Agrupa las barras 1h por día UTC (open_time en ms) y suma volume*close.
    rows = con.execute(
        "SELECT CAST(open_time / 86400000 AS INTEGER) AS day, "
        "SUM(volume * close) AS dollar_vol "
        "FROM perp_klines WHERE symbol=? AND open_time>=? AND open_time<? "
        "GROUP BY day ORDER BY day",
        (symbol, start_ms, end_ms),
    ).fetchall()
    daily = [float(r[1]) for r in rows if r[1] is not None]
    if not daily:
        return None
    return statistics.median(daily)


def derive_tier_cutoffs(db_path: str) -> dict:
    """Deriva los cortes de tier de v3w desde los 10 curados de v3 (spec §3-bis).

    Mide la mediana del dollar-volume diario de cada símbolo de
    `backtest_costs._TIER_BY_SYMBOL` sobre `V3W_REFERENCE_WINDOW` en
    `perp_klines`, agrupa por su tier v3 original, y fija los cortes como los
    puntos medios geométricos entre grupos de tier adyacentes:

        cutoff_large = sqrt(min(median_vol grupo large) × max(median_vol grupo mid))
        cutoff_mid   = sqrt(min(median_vol grupo mid)   × max(median_vol grupo small))

    HARD-FAIL (ValueError) si el mapeo de volumen resultante NO reproduce el tier
    v3 original de los 10 curados — es decir, si los rangos de volumen de los
    grupos se solapan de modo que los puntos medios geométricos no los separan
    (NV-tiers: los cortes no pueden ser cardinales inventados; se derivan por
    consistencia con el dominio-hogar y el candado verifica).

    Devuelve {"cutoff_large": float, "cutoff_mid": float,
              "derivation": {symbol: {"median_dollar_vol": float, "v3_tier": str}}}.
    """
    start_ms = _to_ms(V3W_REFERENCE_WINDOW[0])
    end_ms = _to_ms(V3W_REFERENCE_WINDOW[1])

    derivation: dict[str, dict] = {}
    # median_vol por tier v3w ("large"/"mid"/"small")
    by_tier: dict[str, list[float]] = {"large": [], "mid": [], "small": []}

    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as con:
        for symbol in _TIER_BY_SYMBOL:
            mv = _median_daily_dollar_vol(con, symbol, start_ms, end_ms)
            if mv is None:
                raise ValueError(
                    f"derive_tier_cutoffs: no hay barras para {symbol!r} en "
                    f"perp_klines sobre {V3W_REFERENCE_WINDOW}; no se puede derivar "
                    "el corte (los 10 curados deben existir en la ventana de referencia)."
                )
            v3_tier = tier_for_symbol(symbol)            # "major"/"mid"/"small"
            v3w_tier = _V3_TO_V3W_TIER[v3_tier]
            derivation[symbol] = {"median_dollar_vol": mv, "v3_tier": v3_tier}
            by_tier[v3w_tier].append(mv)

    for tier_name, vols in by_tier.items():
        if not vols:
            raise ValueError(
                f"derive_tier_cutoffs: el grupo de tier {tier_name!r} quedó vacío; "
                "los 10 curados deben poblar los 3 tiers (large/mid/small)."
            )

    min_large, max_large = min(by_tier["large"]), max(by_tier["large"])
    min_mid, max_mid = min(by_tier["mid"]), max(by_tier["mid"])
    min_small, max_small = min(by_tier["small"]), max(by_tier["small"])

    # Los grupos deben estar ordenados sin solape: small < mid < large en volumen.
    if not (max_small < min_mid):
        raise ValueError(
            "derive_tier_cutoffs: los rangos de volumen de los grupos small y mid "
            f"se solapan (max(small)={max_small:.4g} >= min(mid)={min_mid:.4g}); "
            "los puntos medios geométricos no pueden separar los tiers. El mapeo "
            "por volumen NO reproduce el tier v3 de los 10 curados (candado §3-bis)."
        )
    if not (max_mid < min_large):
        raise ValueError(
            "derive_tier_cutoffs: los rangos de volumen de los grupos mid y large "
            f"se solapan (max(mid)={max_mid:.4g} >= min(large)={min_large:.4g}); "
            "los puntos medios geométricos no pueden separar los tiers. El mapeo "
            "por volumen NO reproduce el tier v3 de los 10 curados (candado §3-bis)."
        )

    cutoff_large = (min_large * max_mid) ** 0.5
    cutoff_mid = (min_mid * max_small) ** 0.5

    # Candado: el mapeo por volumen DEBE reproducir el tier v3 original de los 10.
    cutoffs = {"cutoff_large": cutoff_large, "cutoff_mid": cutoff_mid}
    for symbol, info in derivation.items():
        mapped = tier_for_volume(info["median_dollar_vol"], cutoffs)
        expected = _V3_TO_V3W_TIER[info["v3_tier"]]
        if mapped != expected:
            raise ValueError(
                f"derive_tier_cutoffs: {symbol!r} mapea a {mapped!r} por volumen "
                f"pero su tier v3 es {expected!r}; la regla de volumen no reproduce "
                "el dominio-hogar (candado §3-bis)."
            )

    return {"cutoff_large": cutoff_large, "cutoff_mid": cutoff_mid, "derivation": derivation}


def tier_for_volume(median_dollar_vol: float, cutoffs: dict) -> str:
    """Asigna tier v3w por mediana de dollar-volume diario (spec §3-bis).

    "large" si >= cutoff_large; "mid" si >= cutoff_mid; "small" en otro caso.
    """
    if median_dollar_vol >= cutoffs["cutoff_large"]:
        return "large"
    if median_dollar_vol >= cutoffs["cutoff_mid"]:
        return "mid"
    return "small"


def v3w_fill_cost(notional_usd: float, tier: str, calibration, *,
                  forced_close: bool = False) -> float:
    """Costo en $ de UN fill taker de `notional_usd` en el tier dado (spec §3-bis/§4 F6).

    Reusa el "floor leg" de v3 (backtest_costs._v3_leg_cost:446), el cuerpo
    size-independiente spread+fee stress-escalado, con los `TierParams` por tier
    de la calibración cargada (sin modificarlos):

        floor_leg_bps = tp.stress_mult * (tp.half_spread_bps + tp.fee_bps_per_side)
        dollar_cost   = floor_leg_bps * notional_usd / 10_000

    `forced_close=True` fuerza el tier "small" (el peor) sin importar `tier`
    (spec §4 F6: cierres forzosos por delisting se cargan al tier small de v3w).

    `tier` es nombre v3w ("large"/"mid"/"small"); se traduce a la clave de
    calibración v3 ("major"/"mid"/"small") vía _V3W_TO_V3_TIER.
    """
    effective = "small" if forced_close else tier
    if effective not in _V3W_TO_V3_TIER:
        raise ValueError(
            f"v3w_fill_cost: tier {effective!r} desconocido; esperaba "
            f"{set(_V3W_TO_V3_TIER)}"
        )
    v3_tier_key = _V3W_TO_V3_TIER[effective]
    tp = calibration.tiers[v3_tier_key]
    floor_leg_bps = tp.stress_mult * (tp.half_spread_bps + tp.fee_bps_per_side)
    return floor_leg_bps * notional_usd / 10_000.0
