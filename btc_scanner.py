#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║   BTC SCANNER — Ultimate Macro & Order Flow V6.0             ║
║   BTCUSDT SPOT  |  Señal 1H  +  Gatillo 5M                  ║
║                                                              ║
║   LÓGICA MULTI-TIMEFRAME:                                    ║
║     4H  →  Contexto macro (SMA100, tendencia)                ║
║     1H  →  Señal principal (LRC ≤ 25%, score C1-C8)         ║
║     5M  →  Gatillo de entrada (vela confirma reversión)      ║
╚══════════════════════════════════════════════════════════════╝

Uso:
    python3 btc_scanner.py            →  bucle continuo (revisa cada 5 min)
    python3 btc_scanner.py --once     →  un solo escaneo y salir
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import time
import os
import sys
import json
import io
import logging
import threading

from data import market_data as md
from strategy.indicators import (
    calc_lrc, calc_rsi, calc_bb, calc_sma, calc_atr, calc_adx, calc_cvd_delta,
)
from strategy.constants import (
    LRC_PERIOD, LRC_STDEV, RSI_PERIOD, BB_PERIOD, BB_STDEV, VOL_PERIOD,
    ATR_PERIOD,
    LRC_LONG_MAX, LRC_SHORT_MIN, SCORE_MIN_HALF, SCORE_STANDARD, SCORE_PREMIUM,
)
# Re-exports for backward compatibility — moved to strategy/direction.py per #225 PR2
from strategy.direction import (  # noqa: F401
    ATR_SL_MULT, ATR_TP_MULT, ATR_BE_MULT,
    resolve_direction_params, metrics_inc_direction_disabled,
)

# Re-exports for backward compatibility — moved to strategy/patterns.py per #225 PR1
from strategy.patterns import (  # noqa: F401
    detect_bull_engulfing, detect_bear_engulfing, detect_rsi_divergence,
    score_label, check_trigger_5m, check_trigger_5m_short,
)

# Re-export for backward compatibility — moved to strategy/tune.py per #225 PR3
from strategy.tune import _classify_tune_result  # noqa: F401

# Re-export for backward compatibility — moved to strategy/vol.py per #225 PR4
from strategy.vol import (  # noqa: F401
    annualized_vol_yang_zhang, TARGET_VOL_ANNUAL, VOL_LOOKBACK_DAYS,
)

# Reconfigure stdout for Windows Unicode support
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except (AttributeError, io.UnsupportedOperation):
    pass

log = logging.getLogger("btc_scanner")

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────
SYMBOL         = "BTCUSDT"   # símbolo por defecto / fallback

# ── Top 20 por capitalización (fallback si CoinGecko no responde) ──────────
STABLECOINS = {
    "USDT","USDC","BUSD","DAI","TUSD","USDP","USDD","GUSD","FRAX",
    "LUSD","FDUSD","PYUSD","SUSD","CRVUSD","USDE","USDS",
}

DEFAULT_SYMBOLS = [
    "BTCUSDT","ETHUSDT","ADAUSDT","AVAXUSDT","DOGEUSDT",
    "UNIUSDT","XLMUSDT","PENDLEUSDT","JUPUSDT","RUNEUSDT",
]

SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
LOG_FILE       = os.path.join(SCRIPT_DIR, "logs", "signals_log.txt")
os.makedirs(os.path.join(SCRIPT_DIR, "logs"), exist_ok=True)

SCAN_INTERVAL  = 300   # 5 minutos = cierre de vela 5M

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
        import requests as _req
        r = _req.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        if r.ok:
            fng_value = int(r.json()["data"][0]["value"])
            fng_score = _compute_fng_score(fng_value)
    except Exception:
        pass

    try:
        import requests as _req
        r = _req.get(
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


# ── Parámetros de la estrategia Spot 1H ────────────────────────────────────
SL_PCT         = 2.0      # Stop Loss  2.0%
TP_PCT         = 4.0      # Take Profit 4.0%
COOLDOWN_H     = 6        # Horas mínimas entre trades

# ── Gatillo 5M ─────────────────────────────────────────────────────────────
# Condiciones que debe cumplir la última vela de 5M para activar la entrada.
# Se activa cuando 1H está en setup válido Y la vela 5M confirma reversión.
TRIGGER_RSI_RECOVERY  = True   # RSI 5M sube respecto a la vela anterior
TRIGGER_BULLISH_CLOSE = True   # Vela 5M cierra alcista (close > open)

# ── Filtro de tendencia ADX ────────────────────────────────────────────────
ADX_THRESHOLD = 25  # ADX < 25 = ranging market (OK for mean-reversion)


# ─────────────────────────────────────────────────────────────────────────────
#  SÍMBOLOS DINÁMICOS  —  Top N por capitalización de mercado
# ─────────────────────────────────────────────────────────────────────────────

def get_top_symbols(n: int = 20, quote: str = "USDT") -> list:
    """
    Obtiene los N primeros criptos por capitalización desde CoinGecko.
    Excluye stablecoins y retorna pares USDT (ej: ["BTCUSDT", "ETHUSDT", ...]).
    En caso de error usa DEFAULT_SYMBOLS.
    """
    try:
        import requests as _req
        proxies = _load_proxy()
        r = _req.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": n * 2,   # buffer para filtrar stablecoins
                "page": 1,
                "sparkline": "false",
            },
            proxies=proxies or None,
            timeout=15,
            headers={"User-Agent": "btc-scanner/1.0"},
        )
        r.raise_for_status()
        symbols = []
        for coin in r.json():
            ticker = coin["symbol"].upper()
            if ticker in STABLECOINS:
                continue
            pair = f"{ticker}{quote}"
            symbols.append(pair)
            if len(symbols) >= n:
                break
        if symbols:
            log.info(f"CoinGecko: top {len(symbols)} símbolos → {symbols[:5]}…")
            return symbols
    except Exception as e:
        log.warning(f"CoinGecko no disponible ({e}). Usando lista por defecto.")
    return DEFAULT_SYMBOLS[:n]


# ─────────────────────────────────────────────────────────────────────────────
#  CAPA DE DATOS  —  Binance (multi-URL + proxy)  →  Bybit (fallback auto)
# ─────────────────────────────────────────────────────────────────────────────
#
#  Orden de intento:
#    1. api.binance.com   (principal)
#    2. api1–api4.binance.com  (mirrors oficiales de Binance)
#    3. api.bybit.com  (si Binance entero falla → mismo par BTCUSDT Spot)
#
#  Proxy: configurar en config.json  →  "proxy": "socks5://127.0.0.1:1080"
#         o variable de entorno  HTTPS_PROXY / HTTP_PROXY

def _load_proxy() -> dict:
    """Lee proxy de config.json o de variables de entorno."""
    cfg_path = os.path.join(SCRIPT_DIR, "config.json")
    proxy_str = ""
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
            proxy_str = cfg.get("proxy", "").strip()
        except Exception:
            pass
    # Variable de entorno tiene prioridad sobre config
    proxy_str = os.environ.get("HTTPS_PROXY",
                os.environ.get("HTTP_PROXY", proxy_str)).strip()
    if proxy_str:
        return {"http": proxy_str, "https": proxy_str}
    return {}


_last_api_call = 0.0
_API_MIN_INTERVAL = 0.1  # 100ms between API calls (max 10/sec, well under limits)
_api_lock = threading.Lock()


def _rate_limit():
    """Enforce minimum interval between API calls to avoid rate-limit bans."""
    global _last_api_call
    with _api_lock:
        now = time.time()
        elapsed = now - _last_api_call
        if elapsed < _API_MIN_INTERVAL:
            time.sleep(_API_MIN_INTERVAL - elapsed)
        _last_api_call = time.time()


# ─────────────────────────────────────────────────────────────────────────────
#  INDICADORES — calc_lrc / calc_rsi / calc_bb / calc_sma / calc_atr / calc_adx
#  moved to strategy/indicators.py (Epic #186 A2). Re-exported at the top of
#  this module for backward compatibility.
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
#  REGIME DETECTOR — Multi-signal, runs once per day, cached
# ─────────────────────────────────────────────────────────────────────────────

_REGIME_CACHE_FILE = os.path.join(SCRIPT_DIR, "data", "regime_cache.json")
_REGIME_CACHE_PATH = _REGIME_CACHE_FILE  # canonical name used by new code (monkeypatchable)
_REGIME_TTL_SEC = 86400  # 24 hours


def _load_regime_cache() -> dict:
    """Load regime cache from JSON with soft migration.

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
        # Legacy format — wrap into new structure
        return {"global": data}
    return data if isinstance(data, dict) else {}


def _save_regime_cache(data: dict):
    """Persist regime cache to disk."""
    try:
        os.makedirs(os.path.dirname(_REGIME_CACHE_FILE), exist_ok=True)
        with open(_REGIME_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.warning(f"Failed to save regime cache: {e}")


_regime_cache = _load_regime_cache()


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


# ─────────────────────────────────────────────────────────────────────────────
#  SCANNER PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def scan(symbol: str = None):
    symbol = symbol or SYMBOL
    ts  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    rep = {"timestamp": ts, "symbol": symbol, "errors": []}

    # ── Datos de mercado ──────────────────────────────────────────────────────
    df5  = md.get_klines(symbol, "5m",  limit=210)   # gatillo
    df1h = md.get_klines(symbol, "1h",  limit=210)   # señal principal
    df4h = md.get_klines(symbol, "4h",  limit=150)   # contexto macro
    price = df1h["close"].iloc[-1]   # precio de cierre de la última vela 1H

    # ── Load config (reused for regime_mode + symbol_overrides) ─────────────
    _cfg_path = os.path.join(SCRIPT_DIR, "config.json")
    _cfg = {}
    if os.path.exists(_cfg_path):
        try:
            with open(_cfg_path) as _f:
                _cfg = json.load(_f)
        except Exception:
            pass

    # Kill switch #138 PR 4: PAUSED symbols do not generate signals. Early-return
    # with a structured report that mimics the "disabled in config" shape used below.
    try:
        from health import get_symbol_state
        _health_state = get_symbol_state(symbol)
    except Exception as e:
        log.warning("scan: health state lookup failed for %s: %s", symbol, e)
        _health_state = "NORMAL"

    # Observability (#187 phase 1): log the v1 decision so the dashboard
    # can visualize + so future shadow mode can compare v2 vs v1 side-by-side.
    # Fail-open: never break the scanner on observability errors.
    try:
        import observability
        _v1_size_factor = {
            "NORMAL": 1.0, "ALERT": 1.0, "REDUCED": 0.5,
            "PAUSED": 0.0, "PROBATION": 0.5,
        }.get(_health_state, 1.0)
        _v1_skip = (_health_state == "PAUSED")
        observability.record_decision(
            symbol=symbol,
            engine="v1",
            per_symbol_tier=_health_state,
            portfolio_tier="NORMAL",          # phase 1: hardcoded; B2 computes real aggregate
            size_factor=_v1_size_factor,
            skip=_v1_skip,
            reasons={"health_state": _health_state},
            scan_id=None,
            slider_value=None,
            velocity_active=False,
        )
    except Exception as _obs_err:
        log.warning("observability.record_decision failed for %s: %s", symbol, _obs_err)

    # Shadow mode for kill switch v2 (#187 B2): compute + log portfolio tier
    # as engine='v2_shadow' alongside the v1 row. No effect on trading.
    try:
        from strategy.kill_switch_v2_shadow import emit_shadow_decision, update_price
        if not df1h.empty:
            update_price(symbol, float(df1h["close"].iloc[-1]))
        # B3: read the global regime cache for regime-aware adjustment.
        # Cache is daily; this is a cheap dict lookup once warm. If cache is
        # empty (first ever run), _regime_score stays None → NEUTRAL default.
        _regime_score = None
        try:
            _cached = get_cached_regime()
            if _cached and isinstance(_cached, dict):
                _score = _cached.get("score")
                if _score is not None:
                    _regime_score = float(_score)
        except Exception as _rs_err:
            log.warning(
                "kill_switch_v2_shadow: regime score lookup failed for %s: %s "
                "— falling back to NEUTRAL default (no regime adjustment applied)",
                symbol, _rs_err, exc_info=True,
            )
        emit_shadow_decision(
            symbol=symbol,
            cfg=_cfg if _cfg else {},
            regime_score=_regime_score,
        )
    except Exception as _shadow_err:
        log.warning("kill_switch_v2_shadow emission failed for %s: %s", symbol, _shadow_err)

    if _health_state == "PAUSED":
        rep.update({
            "estado": f"🛑 {symbol} PAUSED por kill switch (#138) — reactivar manualmente",
            "señal_activa": False,
            "direction": None,
            "price": round(float(price), 2),
            "health_state": "PAUSED",
        })
        return rep

    # ── Régimen de mercado (compuesto, cacheado por detect_regime()) ──────────
    _regime_mode = _cfg.get("regime_mode", "global")
    if _regime_mode not in ("global", "hybrid", "hybrid_momentum"):
        log.warning(f"Invalid regime_mode='{_regime_mode}' in config; falling back to 'global'")
        _regime_mode = "global"

    if _regime_mode == "global":
        regime_data = get_cached_regime()
    else:
        regime_data = detect_regime_for_symbol(symbol, _regime_mode)

    # ── PURE DECISION KERNEL (#186 A5) ────────────────────────────────────────
    # Delegate the indicators → direction → score → SL/TP → classification chain
    # to strategy.core.evaluate_signal. This function is a pure mirror of the
    # block we used to have inline; it returns a SignalDecision whose fields we
    # map onto the legacy report shape below. Heavy I/O (data fetch, config,
    # regime detect, observability, health-state lookup) stays in scan().
    from strategy.core import evaluate_signal
    from strategy.sizing import compute_size  # wired per A5 spec; not yet
                                              # mapped onto riesgo_usd (see note below).

    # The pure kernel consumes a `regime` dict; pass the raw detector output so
    # evaluate_signal can read the "regime" key (BULL/BEAR/NEUTRAL).
    decision = evaluate_signal(
        df1h, df4h, df5, df1h,   # df1d slot: legacy scan doesn't fetch 1d,
                                 # so pass df1h for shape-compat (unused by core).
        symbol=symbol,
        cfg=_cfg,
        regime=regime_data,
        health_state=_health_state,
        now=datetime.now(timezone.utc),
    )

    # Legacy token: "LONG" | "SHORT" | None (distinct from the kernel's "NONE").
    direction = None if decision.direction == "NONE" else decision.direction

    # Pull indicators out of the decision for the report dict. These are the
    # same values evaluate_signal just computed — no recomputation.
    ind = decision.indicators
    lrc_pct   = ind.get("lrc_pct")
    lrc_up    = ind.get("lrc_upper")
    lrc_dn    = ind.get("lrc_lower")
    lrc_mid   = ind.get("lrc_mid")
    cur_rsi1h = ind.get("rsi_1h")
    bb_up1h_last = ind.get("bb_upper_1h")
    bb_dn1h_last = ind.get("bb_lower_1h")
    sma10_1h  = ind.get("sma10_1h")
    sma20_1h  = ind.get("sma20_1h")
    vol_1h    = ind.get("vol_1h")
    vol_avg1h = ind.get("vol_avg_1h")
    cvd_1h    = ind.get("cvd_1h")
    cur_adx   = ind.get("adx_1h")
    atr_val   = ind.get("atr_1h")
    sma100_4h = ind.get("sma100_4h")
    price_above_4h = ind.get("price_above_sma100_4h")
    bull_div  = ind.get("bull_div_1h")
    bear_div  = ind.get("bear_div_1h")

    # Engulfings are not in the indicators dict (they're boolean conditions,
    # not numeric indicators). Recompute here — cheap, read-only from df1h.
    bull_eng  = detect_bull_engulfing(df1h)
    bear_eng  = detect_bear_engulfing(df1h)

    # ── Condiciones de Exclusión (Spot V6) — legacy shape preserved ───────────
    excl = {
        "E1_BullEngulfing": {
            "activo": bull_eng,
            "nota":   "Vela alcista que cubre la anterior — entrada en micro-techo",
        },
        "E2_Noticias_Macro": {
            "activo": "VERIFICAR_MANUAL",
            "nota":   "Revisar ForexFactory / TradingView Calendar (±30 min)",
        },
        "E3_RachaPerdedora": {
            "activo": "VERIFICAR_MANUAL",
            "nota":   "¿3 o más trades perdedores consecutivos? Pausa 24h",
        },
        "E4_Capital_Min": {
            "activo": "VERIFICAR_MANUAL",
            "nota":   "Capital disponible > $100 (o > 10% del capital inicial)",
        },
        "E5_Cooldown": {
            "activo": "VERIFICAR_MANUAL",
            "nota":   f"¿Han pasado ≥ {COOLDOWN_H}h desde el último trade?",
        },
        "E6_Divergencia_Bajista": {
            "activo": bear_div,
            "nota":   "Precio sube + RSI baja (1H) — peligro de reversión bajista",
        },
        "E7_Tendencia_Fuerte": {
            "activo": "INFORMATIVO",
            "nota":   f"ADX={cur_adx} (>={ADX_THRESHOLD} = tendencia fuerte). Indicador informativo, no bloquea.",
        },
    }

    # ── Score + confirmations (report-layer derivation from decision.indicators)
    # The pure kernel exposes `decision.score` (0 when direction==NONE), but the
    # legacy report always computed the LONG scoring block when direction was
    # unset — callers + tests pin that shape. We reproduce it here without
    # recomputing any indicators: the `add()` closure just reads from the
    # already-computed `ind` dict.
    score = 0
    conf  = {}

    def add(key, pts, passed, extra=None):
        nonlocal score
        pts_earned = pts if passed else 0
        score += pts_earned
        entry = {"pass": bool(passed), "pts": pts_earned, "max_pts": pts}
        if extra:
            entry.update(extra)
        conf[key] = entry

    if direction == "SHORT":
        # Score SHORT (invertido)
        add("C1_RSI_Sobrecompra",     2, cur_rsi1h > 60,
            {"rsi_1h": cur_rsi1h})
        add("C2_Divergencia_Bajista", 2, bear_div)
        dist_res = abs(price - lrc_up) / price * 100 if lrc_up else 999
        add("C3_Resistencia_Cercana", 1, dist_res <= 1.5,
            {"dist_resistencia_pct": round(dist_res, 2)})
        add("C4_BB_Superior",         1, bb_up1h_last is not None and price >= bb_up1h_last,
            {"bb_upper_1h": round(bb_up1h_last, 2) if bb_up1h_last is not None else None})
        add("C5_Volumen",             1, bool(vol_1h >= vol_avg1h),
            {"vol_ratio": round(vol_1h / vol_avg1h, 2)})
        add("C6_CVD_Delta_Negativo",  1, cvd_1h < 0,
            {"cvd_delta": round(cvd_1h, 4)})
        add("C7_SMA10_menor_SMA20",   1, sma10_1h < sma20_1h,
            {"sma10": round(sma10_1h, 2), "sma20": round(sma20_1h, 2)})
    else:
        # Score LONG (original — also the default when direction is None for
        # legacy report parity).
        add("C1_RSI_Sobreventa",      2, cur_rsi1h < 40,
            {"rsi_1h": cur_rsi1h})
        add("C2_Divergencia_Alcista", 2, bull_div)
        dist_sup = abs(price - lrc_dn) / price * 100 if lrc_dn else 999
        add("C3_Soporte_Cercano",     1, dist_sup <= 1.5,
            {"dist_soporte_pct": round(dist_sup, 2)})
        add("C4_BB_Inferior",         1, bb_dn1h_last is not None and price <= bb_dn1h_last,
            {"bb_lower_1h": round(bb_dn1h_last, 2) if bb_dn1h_last is not None else None})
        add("C5_Volumen",             1, bool(vol_1h >= vol_avg1h),
            {"vol_ratio": round(vol_1h / vol_avg1h, 2)})
        add("C6_CVD_Delta_Positivo",  1, cvd_1h > 0,
            {"cvd_delta": round(cvd_1h, 4)})
        add("C7_SMA10_mayor_SMA20",   1, sma10_1h > sma20_1h,
            {"sma10": round(sma10_1h, 2), "sma20": round(sma20_1h, 2)})

    conf["C8_DXY"] = {
        "pass": "MANUAL", "pts": "?", "max_pts": 1,
        "nota": "DXY verificar TradingView (DXY < SMA20 para LONG, > SMA20 para SHORT)",
    }

    # ── Gatillo 5M (kept outside evaluate_signal because the report needs
    # the detailed `trigger_details` dict, which the pure kernel does not
    # expose — it returns only the boolean in decision.reasons). ──────────────
    if direction == "SHORT":
        trigger_active, trigger_details = check_trigger_5m_short(df5)
    else:
        trigger_active, trigger_details = check_trigger_5m(df5)

    # ── Sizing informativo ────────────────────────────────────────────────────
    # Kept at the legacy 1% fixed formula: the existing downstream contract
    # (tests, frontend "riesgo_usd" field, notification templates) pins this.
    # The pure `compute_size` below layers a score-multiplier on top, which is
    # the right model for future v2 sizing — we wire the call in (per #186 A5
    # spec) and pass its result through the decision.reasons for observability
    # / follow-up mapping in a separate task.
    capital    = 1000.0
    risk_usd   = capital * 0.01
    # Kill switch #138 PR 3: halve risk for REDUCED symbols.
    try:
        from health import apply_reduce_factor
        risk_usd = apply_reduce_factor(risk_usd, symbol, _cfg)
    except Exception as e:
        log.warning("scan: reduce-factor lookup failed for %s: %s", symbol, e)

    # compute_size is wired per #186 A5 but NOT mapped onto riesgo_usd to
    # preserve the legacy report contract. Value is recorded in decision.reasons
    # for future migration (epic #187 v2 sizing).
    try:
        _pure_size_usd = compute_size(
            score=int(decision.score),
            health_tier=_health_state,
            capital=capital,
            cfg=_cfg,
        )
        decision.reasons.setdefault("pure_size_usd", _pure_size_usd)
    except Exception as _sz_err:
        log.warning("scan: compute_size(score=%s, tier=%s) failed: %s",
                    decision.score, _health_state, _sz_err)

    # Per-symbol ATR overrides from config (reuse _cfg loaded above)
    _sym_overrides = _cfg.get("symbol_overrides", {})
    _so = _sym_overrides.get(symbol, {})
    if _so is False:
        # Symbol disabled — no signal
        rep.update({"estado": f"⛔ {symbol} deshabilitado en config", "señal_activa": False,
                    "direction": None, "price": round(price, 2)})
        return rep
    resolved = resolve_direction_params(_sym_overrides, symbol, direction)
    if resolved is None:
        # Direction disabled for this symbol (spec §5 form 3).
        metrics_inc_direction_disabled(symbol, direction)
        rep.update({
            "estado": f"⛔ {direction} deshabilitado para {symbol}",
            "señal_activa": False,
            "direction": direction,
            "direction_disabled": True,
            "price": round(price, 2),
        })
        return rep
    _sl_m = resolved["atr_sl_mult"]
    _tp_m = resolved["atr_tp_mult"]
    _be_m = resolved["atr_be_mult"]

    # SL/TP: use the scan-resolved ATR multipliers (honors the legacy
    # monkeypatchable resolve_direction_params). decision.sl_price / tp_price
    # were computed by the pure kernel using its own resolver copy — those
    # values agree whenever overrides aren't monkeypatched, but we recompute
    # here to preserve the legacy branch structure under test.
    sl_dist    = atr_val * _sl_m
    tp_dist    = atr_val * _tp_m

    if direction == "SHORT":
        sl_price   = round(price + sl_dist, 2)   # SL arriba para SHORT
        tp_price   = round(price - tp_dist, 2)   # TP abajo para SHORT
    else:
        sl_price   = round(price - sl_dist, 2)   # SL abajo para LONG
        tp_price   = round(price + tp_dist, 2)   # TP arriba para LONG

    sl_pct_val = round(sl_dist / price * 100, 2)
    tp_pct_val = round(tp_dist / price * 100, 2)

    qty_btc    = risk_usd / sl_dist
    val_pos    = qty_btc * price
    if val_pos > capital * 0.98:
        qty_btc = (capital * 0.98) / price
        val_pos  = qty_btc * price

    # ── Veredicto (estado string + señal flag) ────────────────────────────────
    # Reproduce the legacy branch structure:
    #   - direction is None        → SIN SETUP
    #   - blocks_auto present      → BLOQUEADA
    #   - macro_4h adversa         → SETUP pero macro mala
    #   - sin gatillo 5M           → SETUP válido, esperando gatillo
    #   - todo OK                  → SEÑAL CONFIRMADA
    # evaluate_signal produces a parallel estado string in decision.estado, but
    # the legacy scan() estado format has slightly different macro phrasing —
    # we keep the legacy template here to preserve exact-string parity.
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

    macro_long  = price_above_4h
    macro_short = not price_above_4h
    blocks   = blocks_long if direction == "LONG" else blocks_short if direction == "SHORT" else []
    macro_ok = macro_long if direction == "LONG" else macro_short if direction == "SHORT" else False

    if direction is None:
        estado = "⏳ SIN SETUP — LRC% fuera de zona (25%-75%)"
        señal  = False
    elif blocks:
        estado = f"🚫 BLOQUEADA {direction} — {len(blocks)} exclusión(es) automática"
        señal  = False
    elif not macro_ok:
        macro_desc = "precio < SMA100 4H" if direction == "LONG" else "precio > SMA100 4H"
        estado = f"⚠️  SETUP {direction} — Macro 4H adversa ({macro_desc})"
        señal  = False
    elif not trigger_active:
        estado = f"🕐 SETUP {direction} VÁLIDO — Esperando gatillo 5M"
        señal  = False
    else:
        sl = score_label(score)
        estado = f"✅ SEÑAL {direction} + GATILLO CONFIRMADOS — Calidad: {sl}"
        señal  = True

    # ── Consolidar ────────────────────────────────────────────────────────────
    rep.update({
        "estado":         estado,
        "señal_activa":   señal,
        "direction":      direction,
        "regime":         regime_data.get("regime"),
        "regime_score":   regime_data.get("score"),
        "regime_details": regime_data.get("details"),
        "price":          round(price, 2),
        "lrc_1h": {
            "pct":   lrc_pct,
            "upper": lrc_up,
            "lower": lrc_dn,
            "mid":   lrc_mid,
        },
        "rsi_1h":         cur_rsi1h,
        "adx_1h":         cur_adx,
        "macro_4h": {
            "sma100":       round(sma100_4h, 2),
            "price_above":  price_above_4h,
        },
        "score":          score,
        "score_label":    score_label(score),
        "confirmations":  conf,
        "exclusions":     excl,
        "blocks_auto":    blocks,
        "gatillo_5m":     trigger_details,
        "gatillo_activo": trigger_active,
        "sizing_1h": {
            "capital_usd": capital,
            "riesgo_usd":  round(risk_usd, 2),
            "atr_1h":      round(atr_val, 2),
            "atr_sl_mult": _sl_m,
            "atr_tp_mult": _tp_m,
            "atr_be_mult": _be_m,
            "sl_mode":     "atr",
            "sl_pct":      f"{sl_pct_val}%",
            "tp_pct":      f"{tp_pct_val}%",
            "sl_precio":   sl_price,
            "tp_precio":   tp_price,
            "qty_btc":     round(qty_btc, 6),
            "valor_pos":   round(val_pos, 2),
            "pct_capital": round(val_pos / capital * 100, 1),
        },
    })
    # Convertir tipos numpy a tipos Python nativos para serialización JSON
    import numpy as np
    def clean_dict(d):
        if isinstance(d, dict):
            for k, v in list(d.items()):
                if isinstance(v, np.bool_):
                    d[k] = bool(v)
                elif isinstance(v, np.integer):
                    d[k] = int(v)
                elif isinstance(v, np.floating):
                    d[k] = float(v)
                elif isinstance(v, dict):
                    clean_dict(v)
                elif isinstance(v, list):
                    for i, item in enumerate(v):
                        if isinstance(item, np.bool_):
                            v[i] = bool(item)
                        elif isinstance(item, np.integer):
                            v[i] = int(item)
                        elif isinstance(item, np.floating):
                            v[i] = float(item)
                        elif isinstance(item, dict):
                            clean_dict(item)
        return d
    clean_dict(rep)
    return rep


# ─────────────────────────────────────────────────────────────────────────────
#  FORMATO DE SALIDA
# ─────────────────────────────────────────────────────────────────────────────

def fmt(rep):
    SEP = "=" * 65
    DIV = "─" * 65

    ok  = lambda b: ("✅" if b is True else ("❌" if b is False else "❓"))

    lines = [
        SEP,
        f"  CRYPTO SCANNER  1H+5M  |  {rep.get('symbol','?')}  |  {rep['timestamp']}",
        SEP,
        f"  💰 PRECIO (cierre 1H) : ${rep['price']:,.2f}",
        f"  📡 ESTADO             : {rep['estado']}",
        f"  📐 DIRECCION          : {rep.get('direction') or 'N/A'}",
        DIV,
        "  ── SETUP 1H  (señal principal) ──────────────────────────",
        f"  LRC 1H : {rep['lrc_1h']['pct']}%   {'✅ ZONA LONG (≤ 25%)' if rep['lrc_1h']['pct'] and rep['lrc_1h']['pct'] <= 25 else '🔴 ZONA SHORT (≥ 75%)' if rep['lrc_1h']['pct'] and rep['lrc_1h']['pct'] >= 75 else '⏳ Fuera de zona'}",
        f"  Upper  : ${rep['lrc_1h']['upper']}   |   Mid : ${rep['lrc_1h']['mid']}   |   Lower : ${rep['lrc_1h']['lower']}",
        f"  RSI 1H : {rep['rsi_1h']}  {'✅ Sobreventa' if rep['rsi_1h'] < 40 else ''}",
        DIV,
        "  ── CONTEXTO MACRO 4H ────────────────────────────────────",
        f"  SMA100 4H        : ${rep['macro_4h']['sma100']}",
        f"  Precio > SMA100  : {ok(rep['macro_4h']['price_above'])}  "
        f"({'alcista ✅' if rep['macro_4h']['price_above'] else 'bajista ⚠️ — solo operar si hay confluencia fuerte'})",
        DIV,
        f"  ── SCORE 1H : {rep['score']}/9  ({rep['score_label']}) ──────────────────",
    ]

    for k, v in rep.get("confirmations", {}).items():
        passed  = v.get("pass")
        sym     = ok(passed) if isinstance(passed, bool) else "❓"
        pts     = v.get("pts", 0)
        extras  = {ek: ev for ek, ev in v.items()
                   if ek not in ("pass", "pts", "max_pts", "nota")}
        nota    = f"\n      → {v['nota']}" if "nota" in v else ""
        xs      = ("  " + str(extras)) if extras else ""
        lines.append(f"    {sym} {k:<30} {pts}pts{xs}{nota}")

    lines += [DIV, "  ── GATILLO 5M  (precisión de entrada) ───────────────────"]
    gat = rep.get("gatillo_5m", {})
    g_ok  = lambda b: "✅" if b else "❌"
    lines += [
        f"    {g_ok(gat.get('vela_5m_alcista'))}  Vela 5M alcista (close > open)"
        f"  →  open ${gat.get('open_5m')} / close ${gat.get('close_5m')}",
        f"    {g_ok(gat.get('rsi_5m_recuperando'))}  RSI 5M recuperando"
        f"  →  {gat.get('rsi_5m_anterior')} → {gat.get('rsi_5m_actual')}",
        f"    {'✅ GATILLO ACTIVO' if rep.get('gatillo_activo') else '🕐 Gatillo inactivo — esperar próxima vela 5M'}",
    ]

    lines += [DIV, "  ── BLOQUEOS AUTOMÁTICOS ─────────────────────────────────"]
    if rep["blocks_auto"]:
        for b in rep["blocks_auto"]:
            lines.append(f"    🚫 {b}")
    else:
        lines.append("    ✅ Ningún bloqueo automático activo")

    lines += [DIV, "  ── VERIFICAR MANUALMENTE ANTES DE ENTRAR ─────────────────"]
    for k, v in rep.get("exclusions", {}).items():
        if isinstance(v, dict) and v.get("activo") == "VERIFICAR_MANUAL":
            lines.append(f"    📋 {k}: {v.get('nota','')}")

    lines += [DIV, "  ── SIZING  (ejemplo $1,000 capital) ──────────────────────"]
    sz = rep["sizing_1h"]
    lines += [
        f"    Riesgo 1%        : ${sz['riesgo_usd']}",
        f"    SL / TP          : {sz['sl_pct']} / {sz['tp_pct']}   →   R:R 2:1",
        f"    Precio SL        : ${sz['sl_precio']}",
        f"    Precio TP        : ${sz['tp_precio']}",
        f"    Cantidad BTC     : {sz['qty_btc']} BTC",
        f"    Valor posición   : ${sz['valor_pos']}  ({sz['pct_capital']}% del capital)",
    ]

    # Nota de sizing según score
    score = rep['score']
    if score >= SCORE_PREMIUM:
        lines.append(f"    💡 Score ≥ 4 → Puedes usar sizing +50% (riesgo hasta 1.5%)")
    elif score < SCORE_STANDARD:
        lines.append(f"    ⚠️  Score < 2 → Usar sizing 50% (riesgo 0.5%)")

    if rep.get("errors"):
        lines += [DIV, "  ADVERTENCIAS"]
        for e in rep["errors"]:
            lines.append(f"    ⚠️  {e}")

    lines.append(SEP)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────────────────────────────────────

def save_log(rep, full_text):
    estado = rep.get("estado", "")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if rep.get("señal_activa"):
            # Señal completa + gatillo → log completo + archivo individual
            f.write(full_text + "\n\n")
            ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            score  = rep.get("score", 0)
            sl     = rep.get("score_label", "")[:3].upper()
            sig_path = os.path.join(SCRIPT_DIR,
                                    f"SIGNAL_LONG_SCORE{score}_{ts_str}.txt")
            with open(sig_path, "w", encoding="utf-8") as sf:
                sf.write(full_text)
            print(f"\n  ⚡ ¡SEÑAL GUARDADA! → {sig_path}")
        elif "SETUP VÁLIDO" in estado:
            # Setup válido pero sin gatillo → guardar en log con marca
            f.write(f"[{rep['timestamp']}] 🕐 SETUP VÁLIDO SIN GATILLO | "
                    f"${rep.get('price','?')} | LRC%: {rep.get('lrc_1h',{}).get('pct','?')} | "
                    f"Score: {rep.get('score', 0)}\n")
        else:
            # Sin setup → solo una línea de resumen
            f.write(f"[{rep['timestamp']}] {estado[:50]} | "
                    f"${rep.get('price','?')} | "
                    f"LRC%: {rep.get('lrc_1h',{}).get('pct','?')}\n")


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    once    = "--once" in sys.argv
    # Si se pasa un símbolo como argumento, solo escanea ese
    sym_arg = next((a for a in sys.argv[1:] if a != "--once"), None)

    print(f"\n{'='*65}")
    print(f"  CRYPTO SCANNER  |  Señal 1H + Gatillo 5M  |  Top 20 pares")
    print(f"  Log: {LOG_FILE}")
    if not once:
        print(f"  Revisa cada {SCAN_INTERVAL}s  |  Ctrl+C para detener")
    print(f"{'='*65}\n")

    while True:
        symbols = [sym_arg] if sym_arg else get_top_symbols(20)
        # Warm cache in parallel so subsequent per-symbol get_klines are cache-hits
        try:
            md.prefetch(symbols, ["5m", "1h", "4h"], limit=210)
        except Exception as e:
            log.warning("prefetch batch failed: %s", e)
        try:
            for sym in symbols:
                try:
                    rep  = scan(sym)
                    text = fmt(rep)
                    print(text)
                    save_log(rep, text)
                except Exception as e:
                    print(f"\n  ❌ Error en {sym}: {e}\n")
                    with open(LOG_FILE, "a") as f:
                        f.write(f"[{datetime.now(timezone.utc)}] ERROR {sym}: {e}\n")
        except KeyboardInterrupt:
            print("\n\n  ⛔ Scanner detenido.\n")
            break

        if once:
            break

        print(f"\n  ⏳ Próximo ciclo en {SCAN_INTERVAL}s (Ctrl+C para detener)...\n")
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
