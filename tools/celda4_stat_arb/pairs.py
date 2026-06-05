"""Formación Engle-Granger de pares cointegrados (spec §4, F2/F3/F4/F5).

Funciones PURAS de la ventana de formación: ninguna consulta toca barras con
`open_time >= formation_end_ms` (spec §4 F2/F3 — anti-survivorship en la capa de
selección). La elegibilidad y los parámetros del par (α, β, μ, σ) se congelan
sobre la formación y NO miran el trading window.

Determinista: la única aleatoriedad (los seeds de las series sintéticas) vive en
los tests. La selección greedy es función pura del db.
"""
from __future__ import annotations

import math
from itertools import combinations

import numpy as np
import statsmodels.api as sm
from statsmodels.tsa.stattools import adfuller

from .constants import (
    ADF_P,
    MAX_PAIRS_PER_SYMBOL,
    MIN_COVERAGE,
    MIN_DOLLAR_VOL_DAILY,
    SIGMA_GUARD,
    TOP_PAIRS,
)

_HOUR_MS = 3_600_000


def expected_bars(start_ms: int, end_ms: int) -> int:
    """Número de barras 1h esperadas en [start_ms, end_ms) sobre la grilla horaria."""
    return (end_ms - start_ms) // _HOUR_MS


def _formation_closes(con, symbol: str, fstart: int, fend: int) -> dict[int, float]:
    """{open_time: close} de las barras de formación (open_time en [fstart, fend)).

    PURO de formación: el `< fend` es estricto — jamás se lee `open_time >= fend`.
    """
    rows = con.execute(
        "SELECT open_time, close FROM perp_klines "
        "WHERE symbol=? AND open_time>=? AND open_time<? ORDER BY open_time",
        (symbol, fstart, fend),
    ).fetchall()
    return {int(t): float(c) for t, c in rows}


def _median_daily_dollar_vol(con, symbol: str, fstart: int, fend: int) -> float | None:
    """Mediana sobre días del dollar-volume diario en [fstart, fend) (spec §4 F2).

    dollar_volume_diario = Σ_barras-del-día (volume × close). Mediana sobre días
    con al menos una barra. None si no hay barras. PURO de formación (`< fend`).
    """
    rows = con.execute(
        "SELECT CAST(open_time / 86400000 AS INTEGER) AS day, "
        "SUM(volume * close) AS dollar_vol "
        "FROM perp_klines WHERE symbol=? AND open_time>=? AND open_time<? "
        "GROUP BY day ORDER BY day",
        (symbol, fstart, fend),
    ).fetchall()
    daily = [float(r[1]) for r in rows if r[1] is not None]
    if not daily:
        return None
    return float(np.median(daily))


def eligible_symbols(con, formation_start_ms: int, formation_end_ms: int) -> dict[str, dict]:
    """Símbolos elegibles sobre SOLO las barras de formación (spec §4 F2/F3).

    Para cada símbolo en `perp_klines`, mirando únicamente barras con
    `formation_start_ms <= open_time < formation_end_ms`:
      (a) cobertura = barras_presentes / esperadas >= MIN_COVERAGE (0.95), y
      (b) mediana del dollar-volume diario >= MIN_DOLLAR_VOL_DAILY ($1M).

    **Función pura de barras < formation_end_ms** — PROHIBIDO referenciar
    disponibilidad o data del trading window (un símbolo que delista 3 días
    después de formar es elegible; su pérdida de cierre forzoso entra al P&L).

    Devuelve {symbol: {"median_daily_dollar_vol": float}} sólo para elegibles.
    """
    exp = expected_bars(formation_start_ms, formation_end_ms)
    if exp <= 0:
        return {}

    symbols = [r[0] for r in con.execute(
        "SELECT DISTINCT symbol FROM perp_klines "
        "WHERE open_time>=? AND open_time<? ORDER BY symbol",
        (formation_start_ms, formation_end_ms),
    ).fetchall()]

    out: dict[str, dict] = {}
    for sym in symbols:
        present = con.execute(
            "SELECT COUNT(*) FROM perp_klines "
            "WHERE symbol=? AND open_time>=? AND open_time<?",
            (sym, formation_start_ms, formation_end_ms),
        ).fetchone()[0]
        if present / exp < MIN_COVERAGE:
            continue
        mdv = _median_daily_dollar_vol(con, sym, formation_start_ms, formation_end_ms)
        if mdv is None or mdv < MIN_DOLLAR_VOL_DAILY:
            continue
        out[sym] = {"median_daily_dollar_vol": mdv}
    return out


def _engle_granger(logx: np.ndarray, logy: np.ndarray) -> tuple[float, float, np.ndarray]:
    """OLS log(Y) = alpha + beta*log(X) (con intercepto). Devuelve (alpha, beta, residuos)."""
    X = sm.add_constant(logx)            # columna de 1s (intercepto) + logx
    model = sm.OLS(logy, X).fit()
    alpha = float(model.params[0])
    beta = float(model.params[1])
    resid = np.asarray(model.resid, dtype=float)
    return alpha, beta, resid


def form_pairs(con, eligible: dict[str, dict], formation_start_ms: int,
               formation_end_ms: int) -> list[dict]:
    """Forma pares cointegrados sobre la formación (spec §4 F4/F5).

    Para cada 2-combinación en orden lexicográfico (X=primero, Y=segundo):
      - alinea sobre la intersección de barras donde AMBOS tienen close;
      - OLS log(Y) = alpha + beta*log(X) (con intercepto); residuos;
      - guard de degeneración: std(residuos) < SIGMA_GUARD → excluido;
      - ADF sobre residuos (regression='c', autolag='AIC') → p-value;
      - candidatos con p < ADF_P, ordenados por p ascendente;
      - selección greedy: en orden de p, se toma el par si NINGUNO de sus dos
        símbolos ya fue usado (MAX_PAIRS_PER_SYMBOL=1), hasta TOP_PAIRS.

    Cada par formado:
      {x, y, alpha, beta, mu (media de residuos, ≈0 con intercepto),
       sigma (std de residuos, ddof=0), adf_p, x_median_dollar_vol,
       y_median_dollar_vol, formation_start_ms, formation_end_ms}.
    """
    # Cargar closes de formación una vez por símbolo elegible.
    closes: dict[str, dict[int, float]] = {
        sym: _formation_closes(con, sym, formation_start_ms, formation_end_ms)
        for sym in eligible
    }

    candidates: list[dict] = []
    for x, y in combinations(sorted(eligible), 2):   # orden lexicográfico
        cx, cy = closes[x], closes[y]
        common = sorted(set(cx) & set(cy))
        if len(common) < 3:
            continue
        logx = np.log(np.array([cx[t] for t in common], dtype=float))
        logy = np.log(np.array([cy[t] for t in common], dtype=float))
        if not (np.all(np.isfinite(logx)) and np.all(np.isfinite(logy))):
            continue

        alpha, beta, resid = _engle_granger(logx, logy)
        sigma = float(np.std(resid, ddof=0))
        if sigma < SIGMA_GUARD:           # guard de degeneración (spec §4 F4)
            continue
        mu = float(np.mean(resid))

        adf_p = float(adfuller(resid, regression="c", autolag="AIC")[1])
        if not (adf_p < ADF_P):
            continue

        candidates.append({
            "x": x, "y": y, "alpha": alpha, "beta": beta, "mu": mu, "sigma": sigma,
            "adf_p": adf_p,
            "x_median_dollar_vol": eligible[x]["median_daily_dollar_vol"],
            "y_median_dollar_vol": eligible[y]["median_daily_dollar_vol"],
            "formation_start_ms": formation_start_ms,
            "formation_end_ms": formation_end_ms,
        })

    # Orden por p ascendente; desempate determinista por (x, y) lexicográfico.
    candidates.sort(key=lambda c: (c["adf_p"], c["x"], c["y"]))

    selected: list[dict] = []
    used: dict[str, int] = {}
    for c in candidates:
        if len(selected) >= TOP_PAIRS:
            break
        if used.get(c["x"], 0) >= MAX_PAIRS_PER_SYMBOL:
            continue
        if used.get(c["y"], 0) >= MAX_PAIRS_PER_SYMBOL:
            continue
        selected.append(c)
        used[c["x"]] = used.get(c["x"], 0) + 1
        used[c["y"]] = used.get(c["y"], 0) + 1
    return selected
