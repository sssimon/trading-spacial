"""Simulación del P&L net-of-v3w de una entrada LONG (puro).

LONG el primer día del episodio, hold HOLD_DAYS; si la serie termina antes
(delisting), cierre forzado al último precio con el peor tier (forced_close).
El costo es 2 fills (entrada + salida); se inyecta como callable para testear
sin la calibración real (en producción = tools.celda4_stat_arb.costs.v3w_fill_cost).
Pre-registro §"Estrategia" y §"Costo (net-of-v3w)"."""
from __future__ import annotations

from .constants import HOLD_DAYS, NOTIONAL_USD


def simulate_entry(bars: list[dict], *, entry_idx: int, tipo: str, episode_id: int,
                   median_dollar_vol: float, fill_cost) -> dict:
    """Simula UNA entrada. `fill_cost(notional, median_daily_dollar_vol=...,
    forced_close=...)` devuelve el costo $ de un fill. Devuelve el registro de la
    entrada con gross/net y pnl_usd."""
    entry_price = float(bars[entry_idx]["close"])
    target_idx = entry_idx + HOLD_DAYS
    forced = target_idx > len(bars) - 1
    exit_idx = min(target_idx, len(bars) - 1)
    exit_price = float(bars[exit_idx]["close"])

    gross_ret = (exit_price - entry_price) / entry_price if entry_price else 0.0

    # 2 fills: entrada (tier normal) + salida (forced_close si delisting).
    cost_in = fill_cost(NOTIONAL_USD, median_daily_dollar_vol=median_dollar_vol,
                        forced_close=False)
    cost_out = fill_cost(NOTIONAL_USD, median_daily_dollar_vol=median_dollar_vol,
                         forced_close=forced)
    net_pnl = NOTIONAL_USD * gross_ret - (cost_in + cost_out)

    return {
        "tipo": tipo,
        "episode_id": episode_id,
        "entry_idx": entry_idx,
        "entry_ts": int(bars[entry_idx]["open_time"]),   # para el reporte de robustez por mitades
        "exit_idx": exit_idx,
        "forced_close": forced,
        "gross_ret": gross_ret,
        "net_ret": net_pnl / NOTIONAL_USD,
        "pnl_usd": net_pnl,
    }
