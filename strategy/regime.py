"""Market regime detector — composite score (price + sentiment + funding + optional momentum).

Extracted from btc_scanner.py per #225 PR6. Two entry points:
- detect_regime() / get_cached_regime() — global, BTCUSDT-anchored, 24h-TTL cache
- detect_regime_for_symbol(symbol, mode) — per-symbol; modes: global, hybrid, hybrid_momentum

Cache file: data/regime_cache.json. Cache shape: {key: regime_dict} where key is
either "global" or "{mode}:{symbol}" for per-symbol modes.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import pandas as pd
import requests

from data import market_data as md
from infra.http import _rate_limit
from strategy.indicators import calc_adx, calc_rsi, calc_sma

log = logging.getLogger("strategy.regime")

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REGIME_CACHE_FILE = os.path.join(_SCRIPT_DIR, "data", "regime_cache.json")
_REGIME_CACHE_PATH = _REGIME_CACHE_FILE  # canonical alias
_REGIME_TTL_SEC = 86400  # 24 hours


def _load_regime_cache() -> dict:
    """Load regime cache from JSON with soft migration of legacy single-regime shape.

    Legacy shape: {"ts": ..., "regime": ..., "score": ...}  (pre-#152)
    New shape:    {"global": {...}, "hybrid:BTCUSDT": {...}, ...}

    Legacy auto-wraps into {"global": legacy}. Returns {} if file missing or malformed.
    """
    if not os.path.exists(_REGIME_CACHE_PATH):
        return {}
    try:
        with open(_REGIME_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if isinstance(data, dict) and "ts" in data and "regime" in data:
        return {"global": data}
    return data if isinstance(data, dict) else {}


def _save_regime_cache(data: dict) -> None:
    """Persist regime cache to disk."""
    try:
        os.makedirs(os.path.dirname(_REGIME_CACHE_FILE), exist_ok=True)
        with open(_REGIME_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.warning(f"Failed to save regime cache: {e}")


_regime_cache = _load_regime_cache()


def _compute_price_score(df_daily: pd.DataFrame) -> int:
    """Score 0-100 bearish-to-bullish sobre daily bars. Pure function.

    Empieza en 100, resta por condiciones bajistas:
      - Death Cross (SMA50 < SMA200): -40
      - Precio debajo de SMA200: -30
      - Retorno 30d < -10%: -20 ; retorno 30d < 0 pero > -10%: -10

    Returns int clamped to [0, 100]. Devuelve 100 (bullish assumption) si df_daily
    tiene menos de 200 bars (insufficient data para SMA200).

    Spec: docs/superpowers/specs/es/2026-04-20-per-symbol-regime-design.md §5
    """
    if df_daily is None or df_daily.empty or len(df_daily) < 200:
        return 100
    try:
        sma50 = df_daily["close"].rolling(50).mean().iloc[-1]
        sma200 = df_daily["close"].rolling(200).mean().iloc[-1]
        if pd.isna(sma50) or pd.isna(sma200):
            return 100
        price = float(df_daily["close"].iloc[-1])
        score = 100
        if sma50 < sma200:
            score -= 40
        if price < sma200:
            score -= 30
        if len(df_daily) >= 30:
            ret30 = df_daily["close"].iloc[-1] / df_daily["close"].iloc[-30] - 1
            if ret30 < -0.10:
                score -= 20
            elif ret30 < 0:
                score -= 10
        return max(0, min(100, int(score)))
    except Exception:
        return 100


def _compute_fng_score(fng_value: int) -> int:
    """F&G ya es 0-100. Pass-through con clamp."""
    return max(0, min(100, int(fng_value)))


def _compute_funding_score(rate: float) -> int:
    """Rate típicamente entre -0.01 y +0.01.
    Mapping: -0.01 → 0, 0 → 50, +0.01 → 100. Clamp [0,100]."""
    return max(0, min(100, int(50 + rate * 5000)))


def _compute_rsi_score(rsi_1d_last: float) -> int:
    """RSI 0-100. Invertido vs. momentum tradicional porque nuestra estrategia
    es mean-reversion. Oversold (RSI bajo) → bullish. Overbought (RSI alto) → bearish.
    Mapping: RSI=30 → 70, RSI=50 → 50, RSI=70 → 30. Returns 100 - rsi (clamped)."""
    return max(0, min(100, int(100 - rsi_1d_last)))


def _compute_adx_score(adx_1d_last: float) -> int:
    """ADX mide fuerza de trend. Alto ADX = trending = mean-reversion falla.
    Score alto = ranging = strategy-friendly.
    Mapping: ADX<20 → 75 (ranging); ADX 20-30 → 50; ADX ≥30 → 25 (strong trend)."""
    if adx_1d_last < 20:
        return 75
    if adx_1d_last < 30:
        return 50
    return 25


def _regime_cache_key(symbol: str | None, mode: str) -> str:
    """Return cache key: 'global' for legacy mode, '{mode}:{symbol}' otherwise."""
    if mode == "global":
        return "global"
    return f"{mode}:{symbol}"


def _compute_local_regime(
    symbol: str | None,
    mode: str,
    df_daily_sym: pd.DataFrame,
    fng_score: int,
    funding_score: int,
    rsi_score: int = 50,
    adx_score: int = 50,
) -> dict:
    """Compose final regime score per mode. Returns {ts, regime, score, mode, symbol, components}.

    Weights:
      mode='global':           40% price + 30% F&G + 30% funding
      mode='hybrid':           50% price + 25% F&G + 25% funding
      mode='hybrid_momentum':  30% price + 15% RSI + 20% ADX + 20% F&G + 15% funding

    Thresholds: score > 60 = BULL; score < 40 = BEAR; else NEUTRAL.
    """
    price_score = _compute_price_score(df_daily_sym)

    if mode == "global":
        composite = price_score * 0.40 + fng_score * 0.30 + funding_score * 0.30
        components = {"price": price_score, "fng": fng_score, "funding": funding_score}
    elif mode == "hybrid":
        composite = price_score * 0.50 + fng_score * 0.25 + funding_score * 0.25
        components = {"price": price_score, "fng": fng_score, "funding": funding_score}
    elif mode == "hybrid_momentum":
        composite = (price_score * 0.30 + rsi_score * 0.15 + adx_score * 0.20
                     + fng_score * 0.20 + funding_score * 0.15)
        components = {
            "price": price_score, "rsi": rsi_score, "adx": adx_score,
            "fng": fng_score, "funding": funding_score,
        }
    else:
        raise ValueError(f"Unknown regime mode: {mode}")

    if composite > 60:
        regime = "BULL"
    elif composite < 40:
        regime = "BEAR"
    else:
        regime = "NEUTRAL"

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "regime": regime,
        "score": round(composite, 2),
        "mode": mode,
        "symbol": symbol,
        "components": components,
    }


def detect_regime_for_symbol(symbol: str | None, mode: str = "global") -> dict:
    """Public entry. Dispatches by mode; 24h TTL cache.

    mode='global' delegates to get_cached_regime() (legacy path).
    Invalid mode → warning + fallback to 'global'.
    """
    VALID_MODES = {"global", "hybrid", "hybrid_momentum"}
    if mode not in VALID_MODES:
        log.warning(f"Invalid regime mode '{mode}'; falling back to 'global'")
        mode = "global"

    if mode == "global":
        return get_cached_regime()

    # Per-symbol path for hybrid / hybrid_momentum
    key = _regime_cache_key(symbol, mode)
    global _regime_cache
    cached = _regime_cache.get(key)
    if cached and cached.get("ts"):
        try:
            age = (datetime.now(timezone.utc) -
                   datetime.fromisoformat(cached["ts"])).total_seconds()
            if age < 86400:  # 24h TTL
                return cached
        except Exception:
            pass

    # Cache miss — compute
    df_daily = None
    try:
        df_daily = md.get_klines(symbol, "1d", limit=250) if symbol else None
    except Exception as e:
        log.warning(f"detect_regime_for_symbol: md.get_klines failed for {symbol}: {e}")

    fng_score = 50
    funding_score = 50
    rsi_score = 50
    adx_score = 50

    # Fetch F&G and funding via shared HTTP calls (acceptable duplication of detect_regime).
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        if r.ok:
            fng_value = int(r.json()["data"][0]["value"])
            fng_score = _compute_fng_score(fng_value)
    except Exception:
        pass

    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1",
            timeout=10,
        )
        if r.ok and r.json():
            rate = float(r.json()[0]["fundingRate"])
            funding_score = _compute_funding_score(rate)
    except Exception:
        pass

    if mode == "hybrid_momentum" and df_daily is not None and len(df_daily) >= 20:
        try:
            rsi_val = calc_rsi(df_daily["close"], 14).iloc[-1]
            if not pd.isna(rsi_val):
                rsi_score = _compute_rsi_score(rsi_val)
        except Exception:
            pass
        try:
            adx_val = calc_adx(df_daily, 14).iloc[-1]
            if not pd.isna(adx_val):
                adx_score = _compute_adx_score(adx_val)
        except Exception:
            pass

    result = _compute_local_regime(
        symbol, mode, df_daily,
        fng_score, funding_score, rsi_score, adx_score,
    )

    _regime_cache[key] = result
    _save_regime_cache(_regime_cache)
    return result


def detect_regime() -> dict:
    """
    Composite market regime detection. Combines:
      - Price structure (40%): Death Cross + SMA200 position
      - Sentiment (30%): Fear & Greed Index (alternative.me)
      - Market (30%): Binance funding rate

    Returns dict with regime ("BULL"/"BEAR"/"NEUTRAL"), score (0-100), details.
    Score > 70 = BULL, Score < 30 = BEAR, 30-70 = NEUTRAL.
    """
    details = {}
    score_components = []

    # ── 1. Price Structure (40% weight) ──────────────────────────────────────
    price_score = 100  # default bullish
    try:
        df1d = md.get_klines("BTCUSDT", "1d", limit=250)
        if len(df1d) >= 200:
            sma50  = calc_sma(df1d["close"], 50).iloc[-1]
            sma200 = calc_sma(df1d["close"], 200).iloc[-1]
            price  = float(df1d["close"].iloc[-1])
            ret30d = (price / float(df1d["close"].iloc[-30]) - 1) * 100 if len(df1d) >= 30 else 0

            death_cross = bool(sma50 < sma200)
            price_below_sma200 = bool(price < sma200)

            price_score = 100
            if death_cross:
                price_score -= 40
            if price_below_sma200:
                price_score -= 30
            if ret30d < -10:
                price_score -= 20
            elif ret30d < 0:
                price_score -= 10
            price_score = max(0, min(100, price_score))

            details["price"] = {
                "sma50": round(float(sma50), 2),
                "sma200": round(float(sma200), 2),
                "price": round(price, 2),
                "death_cross": death_cross,
                "price_below_sma200": price_below_sma200,
                "ret_30d_pct": round(ret30d, 1),
                "score": price_score,
            }
    except Exception as e:
        log.warning(f"Regime: price structure error: {e}")
        details["price"] = {"error": str(e), "score": price_score}
    score_components.append(("price", price_score, 0.4))

    # ── 2. Sentiment: Fear & Greed Index (30% weight) ────────────────────────
    fng_score = 50  # default neutral
    try:
        _rate_limit()
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        if r.ok:
            data = r.json()
            fng_value = int(data["data"][0]["value"])
            fng_label = data["data"][0]["value_classification"]
            fng_score = fng_value  # 0=extreme fear, 100=extreme greed
            details["sentiment"] = {
                "fear_greed_index": fng_value,
                "classification": fng_label,
                "score": fng_score,
            }
    except Exception as e:
        log.warning(f"Regime: Fear & Greed error: {e}")
        details["sentiment"] = {"error": str(e), "score": fng_score}
    score_components.append(("sentiment", fng_score, 0.3))

    # ── 3. Market: Funding Rate (30% weight) ─────────────────────────────────
    funding_score = 50  # default neutral
    try:
        _rate_limit()
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1",
            timeout=10
        )
        if r.ok:
            data = r.json()
            rate = float(data[0]["fundingRate"])
            # Positive funding = bullish (longs pay shorts), negative = bearish
            # Map: -0.01 → 0, 0 → 50, +0.01 → 100
            funding_score = max(0, min(100, int(50 + rate * 5000)))
            details["funding"] = {
                "rate": rate,
                "rate_pct": round(rate * 100, 4),
                "score": funding_score,
            }
    except Exception as e:
        log.warning(f"Regime: funding rate error: {e}")
        details["funding"] = {"error": str(e), "score": funding_score}
    score_components.append(("funding", funding_score, 0.3))

    # ── Composite Score ──────────────────────────────────────────────────────
    composite = sum(s * w for _, s, w in score_components)
    composite = round(composite, 1)

    if composite > 60:
        regime = "BULL"
    elif composite < 40:
        regime = "BEAR"
    else:
        regime = "NEUTRAL"

    result = {
        "regime": regime,
        "score": composite,
        "details": details,
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    # Update cache (RAM + disk)
    global _regime_cache
    _regime_cache["global"] = result
    _save_regime_cache(_regime_cache)
    log.info(f"Regime Detection: {regime} (score={composite}) "
             f"[price={price_score} fng={fng_score} funding={funding_score}]")

    return result


def get_cached_regime() -> dict:
    """Return cached regime, refreshing if older than TTL.

    _regime_cache is now a composite dict keyed by cache keys.
    The legacy 'global' regime lives at _regime_cache['global'].
    """
    global_entry = _regime_cache.get("global", {})
    if not global_entry or global_entry.get("ts") is None:
        return detect_regime()
    cache_age = (datetime.now(timezone.utc) -
                 datetime.fromisoformat(global_entry["ts"])).total_seconds()
    if cache_age > _REGIME_TTL_SEC:
        return detect_regime()
    return global_entry
