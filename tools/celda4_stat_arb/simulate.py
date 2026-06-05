"""Señal + ejecución + costos del walk-forward de pares (spec §4).

Mecánica congelada del spec §4:
  - Señal: z(t) = (log(y_t) - alpha - beta*log(x_t) - mu) / sigma con (alpha,
    beta, mu, sigma) de la formación congelados.
  - Entrada: |z| >= Z_ENTRY (2.0), una posición por par, sin re-entrada tras
    stop dentro del window. long_spread (long Y, short X) si z <= -2;
    short_spread si z >= +2. Fill al close de la barra SIGUIENTE (lag 1).
  - Unidades congeladas al fill de entrada; dollar-neutral $10k/pierna.
  - Salida: cruce de z=0 → fill barra siguiente. Stop |z| >= Z_STOP (3.0) →
    fill barra siguiente, marcado (sin re-entrada). Cierre forzoso a fin de
    window → fill al último close (SIN lag). Delisting (una pierna sin más
    barras) → cierre forzoso en la última barra disponible de esa pierna.
  - P&L: price_pnl (Δprecio por pierna × units × lado) + funding_net
    (settlements en (entry_fill, exit_fill], aproximación mark=close §3 F1) −
    costos (v3w por pierna por fill, 4 fills; tier por el volumen de SU
    formación; delisting forced-close → small).

HARD BOUNDARY (spec §3 NV-A): ninguna consulta toca open_time ni funding_time_ms
>= STUDY_END. `assert_within_study_bounds` lo enforcea en simulate (el único que
abre ventanas de trading); pairs no lo llama: sus ventanas de formación preceden
estructuralmente a trading_end <= STUDY_END vía trading_windows().
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from .constants import (
    FORMATION_DAYS,
    NOTIONAL_PER_LEG,
    STUDY_END,
    STUDY_START,
    TRADING_DAYS,
    Z_ENTRY,
    Z_EXIT,
    Z_STOP,
)
from .costs import tier_for_volume, v3w_fill_cost

_HOUR_MS = 3_600_000
_DAY_MS = 86_400_000


def _to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


_STUDY_END_MS = _to_ms(STUDY_END)


def assert_within_study_bounds(ts_ms: int) -> None:
    """Hard boundary del estudio (spec §3 NV-A): jamás se mira timestamp >= STUDY_END.

    Falla (AssertionError) si `ts_ms >= STUDY_END` epoch ms. Lo usa simulate (el
    único módulo que abre ventanas de trading) para garantizar que ninguna
    consulta cruce la frontera del holdout en TIEMPO (aunque el db contenga data
    posterior). pairs no lo llama: sus formaciones preceden estructuralmente a
    trading_end <= STUDY_END vía trading_windows().
    """
    assert ts_ms < _STUDY_END_MS, (
        f"study boundary violated: timestamp {ts_ms} >= STUDY_END "
        f"({STUDY_END} = {_STUDY_END_MS}); ningún módulo lee data >= STUDY_END (spec §3 NV-A)"
    )


def trading_windows() -> list[tuple[int, int, int, int]]:
    """Ventanas walk-forward (formation_start, formation_end, trading_start, trading_end).

    Rolling, 30 días de trading sin solape; la primera ventana de trading empieza
    en STUDY_START + 180 días; la formación de cada ventana son los 180 días
    inmediatamente anteriores a su trading_start.

    LECTURA CONSERVADORA (declarada): sólo se usan ventanas de trading de 30 días
    COMPLETAS que caben enteras antes de STUDY_END. Si los últimos < 30 días no
    completan una ventana, NO se incluyen (en vez de truncar) — la lectura más
    fiel del spec "ventanas sin solape 30d" sin inventar una ventana parcial.
    """
    study_start = _to_ms(STUDY_START)
    span = TRADING_DAYS * _DAY_MS
    formation_span = FORMATION_DAYS * _DAY_MS

    wins: list[tuple[int, int, int, int]] = []
    trading_start = study_start + formation_span
    while trading_start + span <= _STUDY_END_MS:    # sólo ventanas 30d completas
        formation_start = trading_start - formation_span
        formation_end = trading_start
        trading_end = trading_start + span
        wins.append((formation_start, formation_end, trading_start, trading_end))
        trading_start = trading_end                 # sin solape (rolling)
    return wins


def _bars(con, symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
    """[(open_time, close)] de `symbol` en [start_ms, end_ms), orden ascendente."""
    rows = con.execute(
        "SELECT open_time, close FROM perp_klines "
        "WHERE symbol=? AND open_time>=? AND open_time<? ORDER BY open_time",
        (symbol, start_ms, end_ms),
    ).fetchall()
    return [(int(t), float(c)) for t, c in rows]


def _funding(con, symbol: str, lo_ms: int, hi_ms: int) -> list[tuple[int, float]]:
    """Settlements de `symbol` en (lo_ms, hi_ms] (boundary F13), orden ascendente."""
    rows = con.execute(
        "SELECT funding_time_ms, funding_rate FROM perp_funding "
        "WHERE symbol=? AND funding_time_ms>? AND funding_time_ms<=? "
        "ORDER BY funding_time_ms",
        (symbol, lo_ms, hi_ms),
    ).fetchall()
    return [(int(t), float(r)) for t, r in rows]


def _z(close_y: float, close_x: float, pair: dict) -> float:
    return (math.log(close_y) - pair["alpha"] - pair["beta"] * math.log(close_x)
            - pair["mu"]) / pair["sigma"]


def _close_at_or_before(bars: list[tuple[int, float]], ts_ms: int) -> float:
    """Close de la barra que CONTIENE la hora ts_ms: última barra con open_time <= ts_ms.

    Aproximación de mark del funding (spec §3 F1): el settlement cae en la hora de
    una barra; su close es el mark aproximado. NaN si no hay barra previa.
    """
    last = float("nan")
    for ot, c in bars:
        if ot <= ts_ms:
            last = c
        else:
            break
    return last


def _funding_net(con, pair: dict, *, entry_fill_time: int, exit_fill_time: int,
                 side_y: int, side_x: int, units_y: float, units_x: float) -> float:
    """Funding neto del par sobre (entry_fill_time, exit_fill_time] (spec §4 F13).

    Convención de signo (spec §3-bis/§4): una pierna LONG paga funding positivo
    (funding pnl = -rate*mark*units); una pierna SHORT lo recibe (+rate*mark*units).
    side_leg ∈ {+1 long, -1 short}; el pnl de cada settlement de la pierna es
    `(-side_leg) * rate * mark * units`. Mark = close de la barra que contiene la
    hora del settlement (aproximación §3 F1).
    """
    total = 0.0
    # Una sola pasada de barras por pierna para marcar los settlements.
    for symbol, side, units in ((pair["y"], side_y, units_y), (pair["x"], side_x, units_x)):
        settlements = _funding(con, symbol, entry_fill_time, exit_fill_time)
        if not settlements:
            continue
        # Barras de la pierna hasta el último settlement (para el mark = close).
        last_t = settlements[-1][0]
        bars = _bars(con, symbol, entry_fill_time - _HOUR_MS, last_t + _HOUR_MS)
        for t, rate in settlements:
            mark = _close_at_or_before(bars, t)
            if math.isnan(mark):
                continue
            total += (-side) * rate * mark * units
    return total


def simulate_window(con, pairs: list[dict], trading_start_ms: int, trading_end_ms: int,
                    cutoffs: dict, calibration) -> list[dict]:
    """Simula un trading window para cada par formado. Devuelve dicts de posición.

    Camina las barras 1h del window (sólo barras con close de AMBAS piernas).
    Mecánica del spec §4 (ver docstring del módulo). Una posición a la vez por
    par; sin re-entrada tras stop en el mismo window.

    Posición:
      {pair: "X/Y", window_start_ms, entry_time_ms, exit_time_ms,
       exit_reason: "exit_z"|"stop"|"window_end"|"delisting",
       side: "long_spread"|"short_spread",
       gross: price_pnl, funding: funding_net, costs, net}
    """
    assert_within_study_bounds(trading_end_ms - 1)   # ninguna barra >= STUDY_END

    positions: list[dict] = []
    for pair in pairs:
        positions.extend(
            _simulate_pair(con, pair, trading_start_ms, trading_end_ms, cutoffs, calibration)
        )
    return positions


def _simulate_pair(con, pair: dict, trading_start_ms: int, trading_end_ms: int,
                   cutoffs: dict, calibration) -> list[dict]:
    x, y = pair["x"], pair["y"]
    bars_x = dict(_bars(con, x, trading_start_ms, trading_end_ms))
    bars_y = dict(_bars(con, y, trading_start_ms, trading_end_ms))
    common = sorted(set(bars_x) & set(bars_y))
    if len(common) < 2:
        return []

    # Tier de cada pierna por su volumen de SU formación (spec §4 F6).
    tier_x = tier_for_volume(pair["x_median_dollar_vol"], cutoffs)
    tier_y = tier_for_volume(pair["y_median_dollar_vol"], cutoffs)

    # Último tiempo donde AMBAS piernas tienen barra (para detectar delisting).
    last_common = common[-1]
    last_x = max(bars_x)
    last_y = max(bars_y)

    open_pos = None       # dict de la posición abierta, o None
    stopped = False       # tras un stop, no re-entrada en este window
    result: list[dict] = []

    for i, t in enumerate(common):
        cx, cy = bars_x[t], bars_y[t]
        z = _z(cy, cx, pair)

        if open_pos is None:
            if stopped:
                continue
            if abs(z) >= Z_ENTRY:
                # Señal en barra t → fill al close de la barra SIGUIENTE (lag 1).
                if i + 1 >= len(common):
                    continue                    # sin barra siguiente → no entrada
                ft = common[i + 1]
                side = "long_spread" if z <= -Z_ENTRY else "short_spread"
                open_pos = {
                    "side": side,
                    "entry_fill_time": ft,
                    "entry_x": bars_x[ft],
                    "entry_y": bars_y[ft],
                }
        else:
            # Posición abierta: stop tiene prioridad sobre salida.
            exit_signal = None
            if abs(z) >= Z_STOP:
                exit_signal = "stop"
            elif _crossed_zero(open_pos, z):
                exit_signal = "exit_z"

            if exit_signal is not None:
                if i + 1 < len(common):
                    xt = common[i + 1]
                    result.append(_close_position(
                        con, pair, open_pos, exit_signal, xt,
                        bars_x[xt], bars_y[xt], trading_start_ms,
                        tier_x, tier_y, cutoffs, calibration))
                    if exit_signal == "stop":
                        stopped = True
                    open_pos = None
                # Si no hay barra siguiente, el cierre forzoso de window_end lo resuelve.

    # Cierre forzoso al fin del window / por delisting (posición aún abierta).
    if open_pos is not None:
        # Forzoso en la última barra donde AMBAS piernas existen (sin lag).
        xt = last_common
        # Delisting si alguna pierna dejó de tener barras antes del fin del window
        # (su última barra cae > 1 barra antes de trading_end). Si ambas llegan al
        # final, es cierre forzoso ordinario de fin de window.
        reason = "delisting" if _is_delisting(last_x, last_y, trading_end_ms) else "window_end"
        result.append(_close_position(
            con, pair, open_pos, reason, xt,
            bars_x[xt], bars_y[xt], trading_start_ms,
            tier_x, tier_y, cutoffs, calibration))
    return result


def _crossed_zero(open_pos: dict, z: float) -> bool:
    """z cruzó 0 respecto al lado de entrada (cambio de signo o toca 0)."""
    if open_pos["side"] == "long_spread":   # entró con z<=-2, sale cuando z>=0
        return z >= Z_EXIT
    return z <= Z_EXIT                        # short_spread: entró z>=+2, sale z<=0


def _is_delisting(last_x: int, last_y: int, trading_end_ms: int) -> bool:
    """True si alguna pierna delistó dentro del window (su última barra cae antes
    del fin del window con margen de una barra)."""
    end_threshold = trading_end_ms - _HOUR_MS
    return last_x < end_threshold or last_y < end_threshold


def _close_position(con, pair: dict, open_pos: dict, reason: str, exit_time: int,
                    exit_x: float, exit_y: float, window_start_ms: int,
                    tier_x: str, tier_y: str, cutoffs: dict, calibration,
                    forced: bool = False) -> dict:
    side = open_pos["side"]
    entry_fill = open_pos["entry_fill_time"]
    entry_x, entry_y = open_pos["entry_x"], open_pos["entry_y"]

    # Unidades congeladas al fill de entrada, dollar-neutral $10k/pierna.
    units_y = NOTIONAL_PER_LEG / entry_y
    units_x = NOTIONAL_PER_LEG / entry_x

    side_y = +1 if side == "long_spread" else -1   # long_spread: long Y
    side_x = -1 if side == "long_spread" else +1   # long_spread: short X

    price_pnl = (side_y * units_y * (exit_y - entry_y)
                 + side_x * units_x * (exit_x - entry_x))

    funding_net = _funding_net(
        con, pair, entry_fill_time=entry_fill, exit_fill_time=exit_time,
        side_y=side_y, side_x=side_x, units_y=units_y, units_x=units_x)

    # Costos: v3w por pierna por fill (2 piernas × 2 fills = 4 fills).
    # Delisting → ambas piernas forzosas a small (spec §4 F6).
    forced_close = (reason == "delisting")
    cost_y_entry = v3w_fill_cost(
        units_y * entry_y, tier_y, calibration,
        median_daily_dollar_vol=pair["y_median_dollar_vol"])
    cost_x_entry = v3w_fill_cost(
        units_x * entry_x, tier_x, calibration,
        median_daily_dollar_vol=pair["x_median_dollar_vol"])
    cost_y_exit = v3w_fill_cost(
        units_y * exit_y, tier_y, calibration,
        median_daily_dollar_vol=pair["y_median_dollar_vol"], forced_close=forced_close)
    cost_x_exit = v3w_fill_cost(
        units_x * exit_x, tier_x, calibration,
        median_daily_dollar_vol=pair["x_median_dollar_vol"], forced_close=forced_close)
    costs = cost_y_entry + cost_x_entry + cost_y_exit + cost_x_exit

    net = price_pnl + funding_net - costs
    return {
        "pair": f"{pair['x']}/{pair['y']}",
        "window_start_ms": window_start_ms,
        "entry_time_ms": entry_fill,
        "exit_time_ms": exit_time,
        "exit_reason": reason,
        "side": side,
        "gross": price_pnl,
        "funding": funding_net,
        "costs": costs,
        "net": net,
    }
