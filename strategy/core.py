"""Pure decision logic — the shared kernel between scanner and backtest (#186 A1).

This module exposes `evaluate_signal(...)`: a PURE function that takes market
data (OHLCV dataframes) and state (cfg, regime, health tier) and returns a
`SignalDecision` describing the trading decision. No I/O, no global mutation,
no network, no DB. Same inputs → same outputs.

Callers (`btc_scanner.scan`, `backtest.simulate_strategy`) handle I/O around
this pure kernel: fetching data, loading config, persisting results, publishing
notifications.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from strategy.indicators import (
    calc_adx,
    calc_atr,
    calc_bb,
    calc_cvd_delta,
    calc_lrc,
    calc_rsi,
    calc_sma,
)

# Imported lazily inside evaluate_signal to avoid circular imports:
#   btc_scanner imports strategy.indicators; we re-import its helpers here.
# Keeping these imports at call-time preserves isolation of the pure module
# should btc_scanner ever depend on strategy.core in the future.

# Strategy parameters — kept in sync with btc_scanner constants. Duplicated
# intentionally to keep `strategy/` self-contained (pure function with no
# dependency on btc_scanner's module state). The indicator periods and zone
# thresholds never change at runtime.
LRC_PERIOD = 100
LRC_STDEV = 2.0
RSI_PERIOD = 14
BB_PERIOD = 20
BB_STDEV = 2.0
VOL_PERIOD = 20
ATR_PERIOD = 14
ATR_SL_MULT_DEFAULT = 1.0
ATR_TP_MULT_DEFAULT = 4.0
ATR_BE_MULT_DEFAULT = 1.5

LRC_LONG_MAX = 25.0
LRC_SHORT_MIN = 75.0

# Score tier thresholds (Spot V6, 0-9 scale)
SCORE_MIN_HALF = 0
SCORE_STANDARD = 2
SCORE_PREMIUM = 4


# ─────────────────────────────────────────────────────────────────────────────
#  Pure candlestick / divergence helpers (mirror btc_scanner; no I/O, no state)
#  Kept here instead of cross-importing to avoid coupling strategy/ to btc_scanner.
# ─────────────────────────────────────────────────────────────────────────────


def _detect_bull_engulfing(df: pd.DataFrame) -> bool:
    """Bullish engulfing on the last two bars.

    Matches `btc_scanner.detect_bull_engulfing` exactly.
    """
    if len(df) < 2:
        return False
    p, c = df.iloc[-2], df.iloc[-1]
    return bool(p["close"] < p["open"]
                and c["close"] > c["open"]
                and c["open"] <= p["close"]
                and c["close"] >= p["open"])


def _detect_bear_engulfing(df: pd.DataFrame) -> bool:
    """Bearish engulfing on the last two bars.

    Matches `btc_scanner.detect_bear_engulfing` exactly.
    """
    if len(df) < 2:
        return False
    p, c = df.iloc[-2], df.iloc[-1]
    return bool(p["close"] > p["open"]
                and c["close"] < c["open"]
                and c["open"] >= p["close"]
                and c["close"] <= p["open"])


def _detect_rsi_divergence(close: pd.Series, rsi: pd.Series, window: int = 72) -> dict:
    """Detect bullish / bearish RSI divergence over the given window.

    Matches `btc_scanner.detect_rsi_divergence` exactly (5-point local extrema).
    """
    if len(close) < window:
        return {"bull": False, "bear": False}

    p = close.iloc[-window:].values
    r = rsi.iloc[-window:].values

    mins = [i for i in range(2, window - 2)
            if p[i] < p[i - 1] and p[i] < p[i - 2]
            and p[i] < p[i + 1] and p[i] < p[i + 2]]
    bull_div = False
    if len(mins) >= 2:
        a, b = mins[-2], mins[-1]
        bull_div = bool(p[b] < p[a] and r[b] > r[a])

    maxs = [i for i in range(2, window - 2)
            if p[i] > p[i - 1] and p[i] > p[i - 2]
            and p[i] > p[i + 1] and p[i] > p[i + 2]]
    bear_div = False
    if len(maxs) >= 2:
        a, b = maxs[-2], maxs[-1]
        bear_div = bool(p[b] > p[a] and r[b] < r[a])

    return {"bull": bull_div, "bear": bear_div}


def _score_label(score: int) -> str:
    """Short tier label — dashboard-friendly, Spanish-neutral.

    Note: `btc_scanner.score_label` returns a long human-readable string like
    "PREMIUM ⭐⭐⭐ (sizing 150%)". That long string is a presentation concern
    of the scanner's report; the pure kernel exposes the tier token only.
    """
    if score >= SCORE_PREMIUM:
        return "PREMIUM"
    if score >= SCORE_STANDARD:
        return "STANDARD"
    if score >= SCORE_MIN_HALF:
        return "MINIMA"
    return "INSUFICIENTE"


def _regime_to_direction_token(regime_label: str | None) -> str:
    """Map regime label → direction token used by scan().

    Mirrors the logic in `btc_scanner.scan()`:
        `regime = "LONG" if regime == "BULL" else "SHORT" if regime == "BEAR" else "LONG"`
    i.e. both BULL and NEUTRAL/unknown allow LONG; only BEAR enables SHORT.
    """
    if regime_label == "BEAR":
        return "SHORT"
    # BULL, NEUTRAL, missing, or unknown all fall back to LONG-enabled
    return "LONG"


def _resolve_direction_params(
    overrides: dict | None,
    symbol: str,
    direction: str,
) -> dict | None:
    """Resolve {atr_sl_mult, atr_tp_mult, atr_be_mult} for (symbol, direction).

    Mirrors `btc_scanner.resolve_direction_params` byte-for-byte. Pulled into
    strategy/core to keep the pure kernel self-contained. See spec §6.

    Returns None if the direction is disabled for the symbol (`"short": null`).
    """
    defaults = {
        "atr_sl_mult": ATR_SL_MULT_DEFAULT,
        "atr_tp_mult": ATR_TP_MULT_DEFAULT,
        "atr_be_mult": ATR_BE_MULT_DEFAULT,
    }

    if direction is None or direction == "NONE":
        return defaults
    if not isinstance(overrides, dict):
        return defaults

    entry = overrides.get(symbol, {})
    if not isinstance(entry, dict):
        return defaults

    sentinel = object()
    dir_key = direction.lower()
    dir_block = entry.get(dir_key, sentinel)

    if dir_block is None:
        return None  # direction disabled

    if isinstance(dir_block, dict):
        return {
            "atr_sl_mult": dir_block.get("atr_sl_mult",
                                          entry.get("atr_sl_mult", defaults["atr_sl_mult"])),
            "atr_tp_mult": dir_block.get("atr_tp_mult",
                                          entry.get("atr_tp_mult", defaults["atr_tp_mult"])),
            "atr_be_mult": dir_block.get("atr_be_mult",
                                          entry.get("atr_be_mult", defaults["atr_be_mult"])),
        }

    return {
        "atr_sl_mult": entry.get("atr_sl_mult", defaults["atr_sl_mult"]),
        "atr_tp_mult": entry.get("atr_tp_mult", defaults["atr_tp_mult"]),
        "atr_be_mult": entry.get("atr_be_mult", defaults["atr_be_mult"]),
    }


def _check_trigger_5m_long(df5: pd.DataFrame) -> bool:
    """5-minute bullish trigger: bullish candle AND RSI recovering.

    Mirrors `btc_scanner.check_trigger_5m` (returns only the boolean).
    """
    if len(df5) < 3:
        return False
    rsi5 = calc_rsi(df5["close"], RSI_PERIOD)
    cur = df5.iloc[-1]
    bullish_candle = bool(cur["close"] > cur["open"])
    rsi_recovering = bool(rsi5.iloc[-1] > rsi5.iloc[-2])
    return bullish_candle and rsi_recovering


def _check_trigger_5m_short(df5: pd.DataFrame) -> bool:
    """5-minute bearish trigger: bearish candle AND RSI falling.

    Mirrors `btc_scanner.check_trigger_5m_short` (returns only the boolean).
    """
    if len(df5) < 3:
        return False
    rsi5 = calc_rsi(df5["close"], RSI_PERIOD)
    cur = df5.iloc[-1]
    bearish_candle = bool(cur["close"] < cur["open"])
    rsi_falling = bool(rsi5.iloc[-1] < rsi5.iloc[-2])
    return bearish_candle and rsi_falling


@dataclass
class SignalDecision:
    """Return shape of `evaluate_signal()`.

    All fields are Python primitives or simple containers — no numpy scalars,
    no pandas objects. Safe to serialize / compare / dataclass-replace.
    """

    # Core decision
    direction: str = "NONE"          # "LONG" | "SHORT" | "NONE"
    score: int = 0                    # 0-9
    score_label: str = ""             # "MINIMA" | "STANDARD" | "PREMIUM"
    is_signal: bool = False
    is_setup: bool = False

    # Entry/exit prices (None when direction == "NONE")
    entry_price: float | None = None
    sl_price: float | None = None
    tp_price: float | None = None

    # Diagnostics — populated incrementally as evaluate_signal runs.
    reasons: dict[str, Any] = field(default_factory=dict)
    indicators: dict[str, Any] = field(default_factory=dict)
    estado: str = ""                  # human-readable Spanish status


def evaluate_signal(
    df1h: pd.DataFrame,
    df4h: pd.DataFrame,
    df5m: pd.DataFrame,
    df1d: pd.DataFrame,
    symbol: str,
    cfg: dict[str, Any],
    regime: dict[str, Any],
    health_state: str = "NORMAL",
    now: datetime | None = None,
) -> SignalDecision:
    """Pure decision from market data + state.

    Args:
        df1h: 1-hour OHLCV bars (primary signal timeframe).
        df4h: 4-hour OHLCV bars (macro context).
        df5m: 5-minute OHLCV bars (entry trigger).
        df1d: 1-day OHLCV bars (regime context — optional / may be unused).
        symbol: Symbol being evaluated (e.g. "BTCUSDT"). Used for per-symbol
            override resolution in `cfg["symbol_overrides"]`.
        cfg: Config dict (typically the merged `load_config()` result). Reads
            `symbol_overrides` for ATR multipliers.
        regime: Regime detector output shape:
            `{"regime": "BULL"|"BEAR"|"NEUTRAL", "score": float, "details": {}}`
        health_state: Kill-switch tier for this symbol. Currently PAUSED short-
            circuits to NONE; other tiers affect size (handled by caller).
        now: Timestamp context (not currently used inside the pure function;
            reserved for future time-aware checks).

    Returns:
        `SignalDecision` with decision fields populated. Never raises on empty
        data — returns a NONE decision instead.
    """
    decision = SignalDecision()

    # Guard: not enough bars to compute anything useful.
    if len(df1h) == 0 or len(df4h) == 0:
        return decision

    # ── Indicators on 1H (primary signal timeframe) ────────────────────────
    price = float(df1h["close"].iloc[-1])
    lrc_pct, lrc_up, lrc_dn, lrc_mid = calc_lrc(df1h["close"], LRC_PERIOD, LRC_STDEV)

    rsi1h_series = calc_rsi(df1h["close"], RSI_PERIOD)
    cur_rsi1h = round(float(rsi1h_series.iloc[-1]), 2)

    bb_up1h_series, _, bb_dn1h_series = calc_bb(df1h["close"], BB_PERIOD, BB_STDEV)
    bb_up1h = float(bb_up1h_series.iloc[-1]) if not pd.isna(bb_up1h_series.iloc[-1]) else None
    bb_dn1h = float(bb_dn1h_series.iloc[-1]) if not pd.isna(bb_dn1h_series.iloc[-1]) else None

    sma10_1h = float(calc_sma(df1h["close"], 10).iloc[-1])
    sma20_1h = float(calc_sma(df1h["close"], 20).iloc[-1])

    vol_avg1h = float(df1h["volume"].rolling(VOL_PERIOD).mean().iloc[-1])
    vol_1h = float(df1h["volume"].iloc[-1])

    cvd_1h = calc_cvd_delta(df1h, n=3)

    adx_1h_series = calc_adx(df1h, 14)
    cur_adx = (
        round(float(adx_1h_series.iloc[-1]), 2)
        if not pd.isna(adx_1h_series.iloc[-1])
        else 0.0
    )

    atr_val = float(calc_atr(df1h, ATR_PERIOD).iloc[-1])

    # ── Indicators on 4H (macro context) ───────────────────────────────────
    sma100_4h = float(calc_sma(df4h["close"], 100).iloc[-1])
    price_above_4h = bool(price > sma100_4h)

    # RSI divergences on 1H
    rsi_divs = _detect_rsi_divergence(df1h["close"], rsi1h_series, window=72)
    bull_div = rsi_divs["bull"]
    bear_div = rsi_divs["bear"]

    # Populate diagnostics
    decision.indicators = {
        "price": price,
        "lrc_pct": lrc_pct,
        "lrc_upper": lrc_up,
        "lrc_lower": lrc_dn,
        "lrc_mid": lrc_mid,
        "rsi_1h": cur_rsi1h,
        "bb_upper_1h": bb_up1h,
        "bb_lower_1h": bb_dn1h,
        "sma10_1h": sma10_1h,
        "sma20_1h": sma20_1h,
        "vol_1h": vol_1h,
        "vol_avg_1h": vol_avg1h,
        "cvd_1h": cvd_1h,
        "adx_1h": cur_adx,
        "atr_1h": atr_val,
        "sma100_4h": sma100_4h,
        "price_above_sma100_4h": price_above_4h,
        "bull_div_1h": bull_div,
        "bear_div_1h": bear_div,
    }

    # ── Zone + regime → direction ──────────────────────────────────────────
    in_long_zone = lrc_pct is not None and lrc_pct <= LRC_LONG_MAX
    in_short_zone = lrc_pct is not None and lrc_pct >= LRC_SHORT_MIN

    regime_label = (regime or {}).get("regime")
    regime_token = _regime_to_direction_token(regime_label)

    # LONG when in low zone AND regime is LONG or NEUTRAL (mapped to LONG).
    # SHORT only when in high zone AND regime is BEAR → SHORT.
    # Everything else → NONE (middle band, or mismatched zone/regime pair).
    if in_long_zone and regime_token in ("LONG", "NEUTRAL"):
        direction = "LONG"
    elif in_short_zone and regime_token == "SHORT":
        direction = "SHORT"
    else:
        direction = "NONE"

    decision.direction = direction

    # ── Exclusion / block detection (engulfings + divergences) ─────────────
    bull_eng = _detect_bull_engulfing(df1h)
    bear_eng = _detect_bear_engulfing(df1h)
    blocks_long: list[str] = []
    if bull_eng:
        blocks_long.append("E1: BullEngulfing activo — posible micro-techo")
    if bear_div:
        blocks_long.append("E6: Divergencia bajista RSI (1H) — agotamiento alcista")
    blocks_short: list[str] = []
    if bear_eng:
        blocks_short.append("E1S: BearEngulfing activo — posible micro-suelo")
    if bull_div:
        blocks_short.append("E6S: Divergencia alcista RSI (1H) — agotamiento bajista")

    # ── Score (Spot V6 0-9) — mirrors btc_scanner.scan() C1-C7 ─────────────
    score = 0
    if direction == "SHORT":
        # C1: RSI overbought
        if cur_rsi1h > 60:
            score += 2
        # C2: bearish divergence
        if bear_div:
            score += 2
        # C3: close to upper LRC band
        dist_res = abs(price - lrc_up) / price * 100 if lrc_up else 999
        if dist_res <= 1.5:
            score += 1
        # C4: price at/above upper Bollinger
        if bb_up1h is not None and price >= bb_up1h:
            score += 1
        # C5: volume ≥ average
        if bool(vol_1h >= vol_avg1h):
            score += 1
        # C6: negative CVD delta
        if cvd_1h < 0:
            score += 1
        # C7: 10-SMA below 20-SMA (bearish crossover)
        if sma10_1h < sma20_1h:
            score += 1
    elif direction == "LONG":
        # C1: RSI oversold
        if cur_rsi1h < 40:
            score += 2
        # C2: bullish divergence
        if bull_div:
            score += 2
        # C3: close to lower LRC band
        dist_sup = abs(price - lrc_dn) / price * 100 if lrc_dn else 999
        if dist_sup <= 1.5:
            score += 1
        # C4: price at/below lower Bollinger
        if bb_dn1h is not None and price <= bb_dn1h:
            score += 1
        # C5: volume ≥ average
        if bool(vol_1h >= vol_avg1h):
            score += 1
        # C6: positive CVD delta
        if cvd_1h > 0:
            score += 1
        # C7: 10-SMA above 20-SMA (bullish crossover)
        if sma10_1h > sma20_1h:
            score += 1
    # direction == "NONE" → score stays 0 (matches scan: no confirmations added)

    decision.score = int(score)
    decision.score_label = _score_label(score)

    # ── Symbol / direction gating via config overrides ─────────────────────
    sym_overrides = (cfg or {}).get("symbol_overrides", {}) if isinstance(cfg, dict) else {}
    so_entry = sym_overrides.get(symbol, {}) if isinstance(sym_overrides, dict) else {}

    if so_entry is False:
        # Symbol disabled in config — same shape as scan() early return.
        decision.direction = "NONE"
        decision.score = 0
        decision.score_label = _score_label(0)
        decision.is_signal = False
        decision.is_setup = False
        decision.estado = f"\u26d4 {symbol} deshabilitado en config"
        decision.reasons = {"symbol_disabled": True}
        return decision

    # If we picked a direction, check it's not disabled for this symbol.
    if direction != "NONE":
        resolved = _resolve_direction_params(sym_overrides, symbol, direction)
        if resolved is None:
            # Direction disabled for this (symbol, direction) pair.
            decision.is_signal = False
            decision.is_setup = False
            decision.estado = f"\u26d4 {direction} deshabilitado para {symbol}"
            decision.reasons = {
                "direction_disabled": True,
                "direction": direction,
            }
            return decision
    else:
        resolved = _resolve_direction_params(sym_overrides, symbol, direction)

    sl_mult = resolved["atr_sl_mult"]
    tp_mult = resolved["atr_tp_mult"]
    be_mult = resolved["atr_be_mult"]

    # ── SL / TP prices (ATR-based) ─────────────────────────────────────────
    sl_dist = atr_val * sl_mult
    tp_dist = atr_val * tp_mult

    if direction == "NONE":
        entry_price = None
        sl_price = None
        tp_price = None
    else:
        # Full float precision — sub-$1 symbols (DOGE/XLM) lose all SL/TP fidelity
        # if rounded to 2 decimals (SL collapses onto entry_price). Display layers
        # downstream format for human reading; computation stays exact.
        entry_price = float(price)
        if direction == "SHORT":
            sl_price = float(price + sl_dist)   # SL above for SHORT
            tp_price = float(price - tp_dist)   # TP below for SHORT
        else:  # LONG
            sl_price = float(price - sl_dist)
            tp_price = float(price + tp_dist)

    decision.entry_price = entry_price
    decision.sl_price = sl_price
    decision.tp_price = tp_price

    # ── Macro check & 5M trigger ───────────────────────────────────────────
    macro_ok = (
        price_above_4h if direction == "LONG"
        else (not price_above_4h) if direction == "SHORT"
        else False
    )
    if direction == "SHORT":
        trigger_active = _check_trigger_5m_short(df5m)
    elif direction == "LONG":
        trigger_active = _check_trigger_5m_long(df5m)
    else:
        trigger_active = False

    blocks = blocks_long if direction == "LONG" else blocks_short if direction == "SHORT" else []

    # ── Veredicto (Spanish human-readable estado) ──────────────────────────
    if direction == "NONE":
        estado = "\u23f3 SIN SETUP \u2014 LRC% fuera de zona (25%-75%)"
        is_signal = False
        is_setup = False
    elif blocks:
        estado = f"\U0001f6ab BLOQUEADA {direction} \u2014 {len(blocks)} exclusi\u00f3n(es) autom\u00e1tica"
        is_signal = False
        is_setup = False
    elif not macro_ok:
        macro_desc = "precio < SMA100 4H" if direction == "LONG" else "precio > SMA100 4H"
        estado = f"\u26a0\ufe0f  SETUP {direction} \u2014 Macro 4H adversa ({macro_desc})"
        is_signal = False
        is_setup = False
    elif not trigger_active:
        estado = f"\U0001f550 SETUP {direction} V\u00c1LIDO \u2014 Esperando gatillo 5M"
        is_signal = False
        is_setup = True
    else:
        estado = f"\u2705 SE\u00d1AL {direction} + GATILLO CONFIRMADOS \u2014 Calidad: {_score_label(score)}"
        is_signal = True
        is_setup = True

    decision.is_signal = is_signal
    decision.is_setup = is_setup
    decision.estado = estado
    decision.reasons = {
        "blocks": blocks,
        "macro_ok": macro_ok,
        "trigger_active": trigger_active,
        "atr_sl_mult": sl_mult,
        "atr_tp_mult": tp_mult,
        "atr_be_mult": be_mult,
    }

    return decision
