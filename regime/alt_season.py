"""Régimen de mercado "¿es alt-season?" — cálculo PURO (sin red, sin DB).

Espejo de screener/valley_filter.py. Exhibe un HECHO de mercado (no per-símbolo,
no veredicto): la inclinación del mercado por voto de 3 componentes
(breadth + outperformance alt-vs-BTC + dominancia BTC), con los componentes
visibles. Eje MERCADO, distinto de classify_liveness (eje símbolo).

Contrato de barras: list[dict] diarias ascendentes con claves
{open_time, open, high, low, close, volume, quote_volume}.
"""
from __future__ import annotations

from statistics import mean, median

SMA_FAST = 50
RET_WINDOW_DAYS = 30
MIN_HISTORY_DAYS = 50   # cuello de botella = SMA50 (ret_30d sólo necesita 31)

# Umbrales de lean (PROVISIONALES, sin calibrar contra el panel 2020-2025).
BREADTH_ALT = 0.60
BREADTH_BEAR = 0.40
OUTPERF_ALT = 0.05
OUTPERF_BEAR = -0.05
DOM_ALT = 0.50
DOM_BTC = 0.58

# Gobierno de evidencia.
COVERAGE_MIN = 0.70
MIN_LIVE_VOTERS = 2


def effective_thresholds(overrides: dict | None) -> dict:
    """Umbrales efectivos: constantes de módulo pisadas por `overrides` (calibración
    sin deploy). Claves fijas — única fuente para compose_regime y umbral_version."""
    base = {
        "BREADTH_ALT": BREADTH_ALT, "BREADTH_BEAR": BREADTH_BEAR,
        "OUTPERF_ALT": OUTPERF_ALT, "OUTPERF_BEAR": OUTPERF_BEAR,
        "DOM_ALT": DOM_ALT, "DOM_BTC": DOM_BTC,
        "COVERAGE_MIN": COVERAGE_MIN, "MIN_LIVE_VOTERS": MIN_LIVE_VOTERS,
    }
    if overrides:
        for k, v in overrides.items():
            if k in base:
                base[k] = v
    return base


def symbol_contribution(symbol: str, bars: list[dict]) -> dict | None:
    """Contribución de UN símbolo al régimen. None si len(bars) < MIN_HISTORY_DAYS."""
    if len(bars) < MIN_HISTORY_DAYS:
        return None
    closes = [float(b["close"]) for b in bars]
    sma50 = mean(closes[-SMA_FAST:])
    close_t = closes[-1]
    close_30 = closes[-(RET_WINDOW_DAYS + 1)]
    ret_30d = (close_t - close_30) / close_30 if close_30 else 0.0
    return {"above_sma50": close_t > sma50, "ret_30d": ret_30d}


def _lean_higher_alt(value: float, alt_thr: float, bear_thr: float) -> str:
    """alts si value ≥ alt_thr ; btc si value ≤ bear_thr ; si no neutral."""
    if value >= alt_thr:
        return "alts"
    if value <= bear_thr:
        return "btc"
    return "neutral"


def _lean_lower_alt(value: float, alt_thr: float, bear_thr: float) -> str:
    """Dominancia: MENOR = alts. alts si value ≤ alt_thr ; btc si value ≥ bear_thr."""
    if value <= alt_thr:
        return "alts"
    if value >= bear_thr:
        return "btc"
    return "neutral"


def compose_regime(alt_contribs: list[dict], btc_ret_30d: float | None,
                   btc_dominance: float | None, coverage_ratio: float,
                   thresholds: dict | None = None) -> dict:
    """Compone el estado de régimen por voto de 3 componentes. Hecho de mercado,
    cero campo per-símbolo, cero valencia. Ver spec §Núcleo.
    Si thresholds is None usa effective_thresholds(None) (comportamiento por defecto)."""
    t = thresholds if thresholds is not None else effective_thresholds(None)
    voters: list[str] = []
    componentes: dict[str, dict] = {}

    # 1. breadth50 — vota sólo si la cobertura alcanza el piso.
    breadth50 = (mean(1.0 if c["above_sma50"] else 0.0 for c in alt_contribs)
                 if alt_contribs else None)
    if breadth50 is not None and coverage_ratio >= t["COVERAGE_MIN"]:
        lean = _lean_higher_alt(breadth50, t["BREADTH_ALT"], t["BREADTH_BEAR"])
        componentes["breadth50"] = {"valor": breadth50, "lean": lean,
                                    "estado": "fresco", "n": len(alt_contribs)}
        voters.append(lean)
    else:
        componentes["breadth50"] = {
            "valor": breadth50, "lean": None, "estado": "muerto",
            "n": len(alt_contribs),
            "razon": "cobertura_baja" if breadth50 is not None else "sin_datos"}

    # 2. outperf_30d — mediana de (ret alt - ret BTC). Muerto si BTC no evaluable.
    if alt_contribs and btc_ret_30d is not None:
        outperf = median(c["ret_30d"] - btc_ret_30d for c in alt_contribs)
        lean = _lean_higher_alt(outperf, t["OUTPERF_ALT"], t["OUTPERF_BEAR"])
        componentes["outperf_30d"] = {"valor": outperf, "lean": lean, "estado": "fresco"}
        voters.append(lean)
    else:
        componentes["outperf_30d"] = {"valor": None, "lean": None, "estado": "muerto"}

    # 3. dominancia_btc — muerta si la llamada a CoinGecko falló.
    if btc_dominance is not None:
        lean = _lean_lower_alt(btc_dominance, t["DOM_ALT"], t["DOM_BTC"])
        componentes["dominancia_btc"] = {"valor": btc_dominance, "lean": lean,
                                         "estado": "fresco"}
        voters.append(lean)
    else:
        componentes["dominancia_btc"] = {"valor": None, "lean": None, "estado": "muerto"}

    n_alts = voters.count("alts")
    n_btc = voters.count("btc")
    n_neutral = voters.count("neutral")
    n_live = len(voters)
    if n_live < t["MIN_LIVE_VOTERS"]:
        estado = "mixto"
    elif n_alts > n_btc and n_alts > n_neutral:
        estado = "alts"
    elif n_btc > n_alts and n_btc > n_neutral:
        estado = "btc"
    else:
        estado = "mixto"

    return {
        "estado": estado,
        "componentes": componentes,
        "votos": {"alts": n_alts, "neutral": n_neutral, "btc": n_btc, "vivos": n_live},
        "n_alts_evaluadas": len(alt_contribs),
    }
