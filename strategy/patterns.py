"""Candle/indicator pattern detectors used by scan() (extracted from btc_scanner.py per #225).

Pure functions, no I/O. Imports only from strategy.constants and strategy.indicators.
"""
from __future__ import annotations

import pandas as pd

from strategy.constants import (
    RSI_PERIOD, SCORE_MIN_HALF, SCORE_STANDARD, SCORE_PREMIUM,
)
from strategy.indicators import calc_rsi


def detect_bull_engulfing(df: pd.DataFrame):
    """BullEngulfing: vela anterior bajista completamente engullida por vela alcista.

    Si está activo → NO entrar (E1).
    """
    if len(df) < 2:
        return False
    p, c = df.iloc[-2], df.iloc[-1]
    return (p["close"] < p["open"]          # anterior bajista
            and c["close"] > c["open"]      # actual alcista
            and c["open"]  <= p["close"]    # abre ≤ cierre anterior
            and c["close"] >= p["open"])    # cierra ≥ open anterior


def detect_bear_engulfing(df: pd.DataFrame):
    """BearEngulfing: vela anterior alcista completamente engullida por vela bajista.

    Si está activo → NO entrar SHORT (exclusion para shorts).
    """
    if len(df) < 2:
        return False
    p, c = df.iloc[-2], df.iloc[-1]
    return bool(p["close"] > p["open"]          # anterior alcista
               and c["close"] < c["open"]       # actual bajista
               and c["open"]  >= p["close"]     # abre >= cierre anterior
               and c["close"] <= p["open"])     # cierra <= open anterior


def detect_rsi_divergence(close: pd.Series, rsi: pd.Series, window=72):
    """Detecta divergencias entre precio y RSI.

    - Alcista (Bullish): Precio hace mínimo más bajo, RSI hace mínimo más alto.
    - Bajista (Bearish): Precio hace máximo más alto, RSI hace máximo más bajo.
    Ventana default: 72 barras (3 días en 1H).
    Usa extremos locales de 5 puntos para filtrar ruido.
    """
    if len(close) < window:
        return {"bull": False, "bear": False}

    p = close.iloc[-window:].values
    r = rsi.iloc[-window:].values

    mins = [i for i in range(2, window - 2)
            if p[i] < p[i-1] and p[i] < p[i-2] and p[i] < p[i+1] and p[i] < p[i+2]]

    bull_div = False
    if len(mins) >= 2:
        a, b = mins[-2], mins[-1]
        bull_div = bool(p[b] < p[a] and r[b] > r[a])

    maxs = [i for i in range(2, window - 2)
            if p[i] > p[i-1] and p[i] > p[i-2] and p[i] > p[i+1] and p[i] > p[i+2]]

    bear_div = False
    if len(maxs) >= 2:
        a, b = maxs[-2], maxs[-1]
        bear_div = bool(p[b] > p[a] and r[b] < r[a])

    return {"bull": bull_div, "bear": bear_div}


def score_label(score):
    """Etiqueta de calidad según puntuación Spot V6."""
    if score >= SCORE_PREMIUM:
        return "PREMIUM ⭐⭐⭐ (sizing 150%)"
    elif score >= SCORE_STANDARD:
        return "ESTÁNDAR ⭐⭐ (sizing 100%)"
    elif score >= SCORE_MIN_HALF:
        return "MÍNIMA ⭐ (sizing 50%)"
    return "INSUFICIENTE"


def check_trigger_5m(df5: pd.DataFrame):
    """Evalúa si la última vela de 5M activa el gatillo de entrada (LONG)."""
    if len(df5) < 3:
        return False, {}

    rsi5        = calc_rsi(df5["close"], RSI_PERIOD)
    cur         = df5.iloc[-1]

    bullish_candle  = bool(cur["close"] > cur["open"])
    rsi_recovering  = bool(rsi5.iloc[-1] > rsi5.iloc[-2])

    trigger_active = bullish_candle and rsi_recovering

    details = {
        "vela_5m_alcista":    bullish_candle,
        "rsi_5m_recuperando": rsi_recovering,
        "rsi_5m_actual":      round(rsi5.iloc[-1], 2),
        "rsi_5m_anterior":    round(rsi5.iloc[-2], 2),
        "close_5m":           round(cur["close"], 2),
        "open_5m":            round(cur["open"], 2),
    }
    return trigger_active, details


def check_trigger_5m_short(df5: pd.DataFrame):
    """Evalúa si la última vela de 5M activa el gatillo de entrada SHORT."""
    if len(df5) < 3:
        return False, {}

    rsi5        = calc_rsi(df5["close"], RSI_PERIOD)
    cur         = df5.iloc[-1]

    bearish_candle  = bool(cur["close"] < cur["open"])
    rsi_falling     = bool(rsi5.iloc[-1] < rsi5.iloc[-2])

    trigger_active = bearish_candle and rsi_falling

    details = {
        "vela_5m_bajista":    bearish_candle,
        "rsi_5m_cayendo":     rsi_falling,
        "rsi_5m_actual":      round(rsi5.iloc[-1], 2),
        "rsi_5m_anterior":    round(rsi5.iloc[-2], 2),
        "close_5m":           round(cur["close"], 2),
        "open_5m":            round(cur["open"], 2),
    }
    return trigger_active, details
