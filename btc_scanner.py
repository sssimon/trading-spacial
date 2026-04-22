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

# ── Parámetros de indicadores ──────────────────────────────────────────────
LRC_PERIOD     = 100
LRC_STDEV      = 2.0
RSI_PERIOD     = 14
BB_PERIOD      = 20
BB_STDEV       = 2.0
VOL_PERIOD     = 20
ATR_PERIOD     = 14
ATR_SL_MULT    = 1.0    # SL = entry - 1.0x ATR (optimizado para mean-reversion)
ATR_TP_MULT    = 4.0    # TP = entry + 4.0x ATR (ratio 4:1, adaptativo)
ATR_BE_MULT    = 1.5    # Mover SL a breakeven cuando profit >= 1.5x ATR

# ── Yang-Zhang vol estimator (diagnostic utility only — NOT applied to sizing) ──
# The vol-normalized sizing idea of #125 was found to regress P&L in comparative
# backtest: the per-symbol atr_sl_mult/tp tuning from epic #121 (735 sims) already
# adapts to volatility structurally. Multiplying a flat vol_mult on top shrinks
# the effective risk of the highest-validated symbols (DOGE, BTC, RUNE) and
# undoes the gains. Function kept available for telemetry / future dashboards.
TARGET_VOL_ANNUAL = 0.15   # reference target (not currently applied)
VOL_LOOKBACK_DAYS = 30


def annualized_vol_yang_zhang(df_daily: pd.DataFrame) -> float:
    """Yang-Zhang annualized vol over daily bars (diagnostic utility).

    Not wired into position sizing — see note above. Returns TARGET_VOL_ANNUAL
    when fewer than 5 bars are available.
    """
    if len(df_daily) < 5:
        return TARGET_VOL_ANNUAL
    o = df_daily["open"].astype(float)
    h = df_daily["high"].astype(float)
    l = df_daily["low"].astype(float)
    c = df_daily["close"].astype(float)
    log_ho = np.log(h / o)
    log_lo = np.log(l / o)
    log_co = np.log(c / o)
    log_oc_prev = np.log(o / c.shift(1)).dropna()
    n = len(df_daily) - 1
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    sigma_on = log_oc_prev.var(ddof=1) if len(log_oc_prev) >= 2 else 0.0
    sigma_oc = log_co.var(ddof=1)
    sigma_rs = (log_ho * (log_ho - log_co) + log_lo * (log_lo - log_co)).mean()
    var_daily = max(sigma_on + k * sigma_oc + (1 - k) * sigma_rs, 1e-10)
    return float(np.sqrt(var_daily * 365))


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


def _classify_tune_result(count: int, profit_factor: float | None) -> str:
    """Classify a (symbol, direction) tuning result into one of three tiers.

    Used by scripts/apply_tune_to_config.py to decide whether to commit a
    dedicated triplet, fall back to a single-triplet per-symbol, or disable
    the direction entirely.

    Returns one of: "dedicated", "fallback", "disabled".

    Rules (from spec §6):
        N ≥ 30 AND PF ≥ 1.3   → "dedicated"
        N ≥ 30 AND 1.0 ≤ PF < 1.3 → "fallback"
        N < 30 OR PF < 1.0    → "disabled"
        PF = inf (no losses)  → "dedicated" if N ≥ 30
        PF is None or NaN     → "disabled" (insufficient info)
    """
    if count == 0 or profit_factor is None:
        return "disabled"
    try:
        pf = float(profit_factor)
    except (TypeError, ValueError):
        return "disabled"
    if np.isnan(pf):
        return "disabled"
    if count < 30:
        return "disabled"
    if pf < 1.0:
        return "disabled"
    if pf < 1.3:
        return "fallback"
    return "dedicated"  # pf ≥ 1.3 (including inf)


def resolve_direction_params(
    overrides: dict | None,
    symbol: str,
    direction: str,
) -> dict | None:
    """Resolve {atr_sl_mult, atr_tp_mult, atr_be_mult} for (symbol, direction).

    Returns None if the direction is disabled for that symbol (via `"short": null`).
    Precedence: direction block (long/short) > flat dict > global defaults.
    Case insensitive on direction.

    Spec: See spec §6
    """
    defaults = {
        "atr_sl_mult": ATR_SL_MULT,
        "atr_tp_mult": ATR_TP_MULT,
        "atr_be_mult": ATR_BE_MULT,
    }

    if direction is None:
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

    # dir_block absent (sentinel) or wrong non-None type (e.g. string) — use flat or defaults
    return {
        "atr_sl_mult": entry.get("atr_sl_mult", defaults["atr_sl_mult"]),
        "atr_tp_mult": entry.get("atr_tp_mult", defaults["atr_tp_mult"]),
        "atr_be_mult": entry.get("atr_be_mult", defaults["atr_be_mult"]),
    }


# ── Parámetros de la estrategia Spot 1H ────────────────────────────────────
LRC_LONG_MAX   = 25.0     # LRC% ≤ 25  →  zona de entrada
LRC_SHORT_MIN  = 75.0     # LRC% >= 75  →  zona de entrada SHORT
SL_PCT         = 2.0      # Stop Loss  2.0%
TP_PCT         = 4.0      # Take Profit 4.0%
COOLDOWN_H     = 6        # Horas mínimas entre trades

# ── Score (Spot V6) ────────────────────────────────────────────────────────
# 0–1 pts → sizing 50%  |  2–3 → sizing normal  |  ≥4 → sizing +50%
SCORE_MIN_HALF  = 0       # Mínimo para entrar (sizing reducido)
SCORE_STANDARD  = 2
SCORE_PREMIUM   = 4

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
#  INDICADORES
# ─────────────────────────────────────────────────────────────────────────────

def calc_lrc(close: pd.Series, period=100, k=2.0):
    """
    Canal de Regresión Lineal.
    Retorna: lrc_pct (0-100), upper, lower, mid
    lrc_pct ≤ 25  →  zona LONG (cuartil inferior del canal)
    """
    if len(close) < period:
        return None, None, None, None
    y    = close.iloc[-period:].values
    x    = np.arange(period)
    m, b = np.polyfit(x, y, 1)
    reg  = m * x + b
    std  = np.std(y - reg)
    upper = reg[-1] + k * std
    lower = reg[-1] - k * std
    mid   = reg[-1]
    price = close.iloc[-1]
    if abs(upper - lower) < 1e-10:
        lrc_pct = 50.0
    else:
        lrc_pct = (price - lower) / (upper - lower) * 100
        lrc_pct = max(0.0, min(100.0, lrc_pct))
    return round(lrc_pct, 2), round(upper, 2), round(lower, 2), round(mid, 2)


def calc_rsi(close: pd.Series, period=14):
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def calc_bb(close: pd.Series, period=20, k=2.0):
    sma = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    return sma + k * std, sma, sma - k * std   # upper, mid, lower


def calc_sma(close: pd.Series, period: int):
    return close.rolling(period).mean()


def calc_atr(df: pd.DataFrame, period=14) -> pd.Series:
    """Average True Range — mide la volatilidad real del mercado."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def calc_adx(df: pd.DataFrame, period=14) -> pd.Series:
    """
    Average Directional Index — mide la fuerza de la tendencia (no su dirección).
    ADX < 25  →  mercado lateral/ranging  (apto para mean-reversion)
    ADX >= 25 →  mercado en tendencia     (evitar mean-reversion)

    Pasos:
      1. +DM / -DM desde highs/lows
      2. Suavizar +DM, -DM y TR con EMA (periodo)
      3. +DI = smoothed +DM / ATR * 100
      4. -DI = smoothed -DM / ATR * 100
      5. DX  = |+DI - -DI| / (+DI + -DI) * 100
      6. ADX = EMA de DX (periodo)
    """
    high  = df["high"]
    low   = df["low"]
    close = df["close"]

    # True Range
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    # +DM y -DM
    up_move   = high.diff()
    down_move = (-low).diff()   # equivale a low.shift(1) - low

    plus_dm  = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_dm_s  = pd.Series(plus_dm,  index=df.index)
    minus_dm_s = pd.Series(minus_dm, index=df.index)

    # Suavizado con EMA (Wilder: alpha = 1/period)
    alpha = 1.0 / period
    atr_smooth    = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_dm_smooth  = plus_dm_s.ewm(alpha=alpha, adjust=False).mean()
    minus_dm_smooth = minus_dm_s.ewm(alpha=alpha, adjust=False).mean()

    # +DI y -DI
    plus_di  = (plus_dm_smooth  / atr_smooth.replace(0, np.nan)) * 100
    minus_di = (minus_dm_smooth / atr_smooth.replace(0, np.nan)) * 100

    # DX
    di_sum  = plus_di + minus_di
    di_diff = (plus_di - minus_di).abs()
    dx = (di_diff / di_sum.replace(0, np.nan)) * 100

    # ADX = EMA de DX
    adx = dx.ewm(alpha=alpha, adjust=False).mean()
    return adx


def detect_bull_engulfing(df: pd.DataFrame):
    """
    BullEngulfing: vela anterior bajista completamente engullida por vela alcista.
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
    """
    BearEngulfing: vela anterior alcista completamente engullida por vela bajista.
    Si está activo → NO entrar SHORT (exclusion para shorts).
    """
    if len(df) < 2:
        return False
    p, c = df.iloc[-2], df.iloc[-1]
    return bool(p["close"] > p["open"]          # anterior alcista
               and c["close"] < c["open"]      # actual bajista
               and c["open"]  >= p["close"]    # abre >= cierre anterior
               and c["close"] <= p["open"])    # cierra <= open anterior


def calc_cvd_delta(df: pd.DataFrame, n=3):
    """Proxy CVD: volumen taker buy − sell últimas n barras.

    Data layer bars carry only OHLCV (no taker-side metadata), so
    approximate taker_buy_base from the bar's close position within
    its high-low range, same heuristic the old bybit adapter used.
    """
    if "taker_buy_base" in df.columns:
        taker_buy = df["taker_buy_base"]
    else:
        hl = (df["high"] - df["low"]).replace(0, 1e-9)
        bullish = df["close"] >= df["open"]
        taker_buy = pd.Series(
            np.where(
                bullish,
                df["volume"] * (df["close"] - df["low"]) / hl,
                df["volume"] * (df["high"] - df["close"]) / hl,
            ),
            index=df.index,
        )
    buy  = taker_buy.tail(n)
    sell = (df["volume"] - taker_buy).tail(n)
    return float((buy - sell).sum())


def detect_rsi_divergence(close: pd.Series, rsi: pd.Series, window=72):
    """
    Detecta divergencias entre precio y RSI.
    - Alcista (Bullish): Precio hace mínimo más bajo, RSI hace mínimo más alto.
    - Bajista (Bearish): Precio hace máximo más alto, RSI hace máximo más bajo.
    Ventana default: 72 barras (3 días en 1H).
    Usa extremos locales de 5 puntos para filtrar ruido.
    """
    if len(close) < window:
        return {"bull": False, "bear": False}
    
    p = close.iloc[-window:].values
    r = rsi.iloc[-window:].values
    
    # 1. Buscar Mínimos Locales (para Bullish)
    # i < i-2, i-1, i+1, i+2
    mins = [i for i in range(2, window - 2)
            if p[i] < p[i-1] and p[i] < p[i-2] and p[i] < p[i+1] and p[i] < p[i+2]]
    
    bull_div = False
    if len(mins) >= 2:
        a, b = mins[-2], mins[-1]
        # Precio baja + RSI sube
        bull_div = bool(p[b] < p[a] and r[b] > r[a])

    # 2. Buscar Máximos Locales (para Bearish)
    # i > i-2, i-1, i+1, i+2
    maxs = [i for i in range(2, window - 2)
            if p[i] > p[i-1] and p[i] > p[i-2] and p[i] > p[i+1] and p[i] > p[i+2]]
    
    bear_div = False
    if len(maxs) >= 2:
        a, b = maxs[-2], maxs[-1]
        # Precio sube + RSI baja
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


# ─────────────────────────────────────────────────────────────────────────────
#  GATILLO 5M
# ─────────────────────────────────────────────────────────────────────────────

def check_trigger_5m(df5: pd.DataFrame):
    """
    Evalúa si la última vela de 5M activa el gatillo de entrada.

    Gatillo ACTIVO cuando se cumplen las dos condiciones:
      1. Vela 5M cierra alcista (close > open)  →  primera señal de reversión
      2. RSI 5M está recuperando  (RSI actual > RSI vela anterior)
    Ambas confirman que la presión vendedora en la zona baja del 1H está cediendo.
    """
    if len(df5) < 3:
        return False, {}

    rsi5        = calc_rsi(df5["close"], RSI_PERIOD)
    cur         = df5.iloc[-1]
    prev        = df5.iloc[-2]

    bullish_candle  = bool(cur["close"] > cur["open"])
    rsi_recovering  = bool(rsi5.iloc[-1] > rsi5.iloc[-2])

    # El gatillo requiere las dos condiciones
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
    """
    Evalúa si la última vela de 5M activa el gatillo de entrada SHORT.

    Gatillo SHORT ACTIVO cuando:
      1. Vela 5M cierra bajista (close < open)
      2. RSI 5M está cayendo (RSI actual < RSI vela anterior)
    """
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

def metrics_inc_direction_disabled(symbol: str, direction: str) -> None:
    """Increment the direction_disabled_skips_total metric."""
    try:
        from data import metrics
        metrics.inc("direction_disabled_skips_total",
                    labels={"symbol": symbol, "direction": direction})
    except Exception:
        pass  # metrics optional — don't crash scan on metric failure


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

    regime = regime_data.get("regime", "BULL")
    regime = "LONG" if regime == "BULL" else "SHORT" if regime == "BEAR" else "LONG"

    # ── Indicadores 1H (señal) ────────────────────────────────────────────────
    lrc_pct, lrc_up, lrc_dn, lrc_mid = calc_lrc(df1h["close"], LRC_PERIOD, LRC_STDEV)

    rsi1h     = calc_rsi(df1h["close"], RSI_PERIOD)
    cur_rsi1h = round(rsi1h.iloc[-1], 2)

    bb_up1h, _, bb_dn1h = calc_bb(df1h["close"], BB_PERIOD, BB_STDEV)

    sma10_1h  = calc_sma(df1h["close"], 10).iloc[-1]
    sma20_1h  = calc_sma(df1h["close"], 20).iloc[-1]

    vol_avg1h = df1h["volume"].rolling(VOL_PERIOD).mean().iloc[-1]
    vol_1h    = df1h["volume"].iloc[-1]

    bull_eng  = detect_bull_engulfing(df1h)
    cvd_1h    = calc_cvd_delta(df1h, n=3)
    
    # Divergencias RSI (1H)
    rsi_divs  = detect_rsi_divergence(df1h["close"], rsi1h, window=72)
    bull_div  = rsi_divs["bull"]
    bear_div  = rsi_divs["bear"]

    # ADX 1H (filtro de tendencia)
    adx_1h    = calc_adx(df1h, 14)
    cur_adx   = round(float(adx_1h.iloc[-1]), 2) if not pd.isna(adx_1h.iloc[-1]) else 0

    # ── Indicadores 4H (macro) ────────────────────────────────────────────────
    sma100_4h      = calc_sma(df4h["close"], 100).iloc[-1]
    price_above_4h = bool(price > sma100_4h)

    # ── Condición Primaria (1H) ───────────────────────────────────────────────
    in_long_zone  = lrc_pct is not None and lrc_pct <= LRC_LONG_MAX
    in_short_zone = lrc_pct is not None and lrc_pct >= LRC_SHORT_MIN

    # ── Contexto macro 4H ─────────────────────────────────────────────────────
    macro_long  = price_above_4h    # precio por encima de SMA100 en 4H
    macro_short = not price_above_4h  # precio por debajo de SMA100 en 4H

    # Bear engulfing (exclusion para SHORT)
    bear_eng = detect_bear_engulfing(df1h)

    # ── Condiciones de Exclusión (Spot V6) ────────────────────────────────────
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

    blocks_long = []
    if bull_eng:
        blocks_long.append("E1: BullEngulfing activo — posible micro-techo")
    if bear_div:
        blocks_long.append("E6: Divergencia bajista RSI (1H) — agotamiento alcista")

    blocks_short = []
    if bear_eng:
        blocks_short.append("E1S: BearEngulfing activo — posible micro-suelo")
    if bull_div:
        blocks_short.append("E6S: Divergencia alcista RSI (1H) — agotamiento bajista")

    # ── Determinar direccion activa (gateado por regime detector) ───────────
    # LONG: cuando regime = BULL o NEUTRAL y precio en zona baja del canal
    # SHORT: cuando regime = BEAR y precio en zona alta del canal
    # Backtest full cycle 2022-2026 valido: +241% con LONG+SHORT regime-gated
    direction = None
    if in_long_zone and regime in ("LONG", "NEUTRAL"):
        direction = "LONG"
    elif in_short_zone and regime == "SHORT":
        direction = "SHORT"

    # ── Score de Confirmaciones 1H ────────────────────────────────────────────
    score = 0
    conf  = {}

    def add(key, pts, passed, extra=None):
        nonlocal score
        pts_earned = pts if passed else 0
        score += pts_earned
        entry = {"pass": passed, "pts": pts_earned, "max_pts": pts}
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
        add("C4_BB_Superior",         1, price >= bb_up1h.iloc[-1],
            {"bb_upper_1h": round(bb_up1h.iloc[-1], 2)})
        add("C5_Volumen",             1, bool(vol_1h >= vol_avg1h),
            {"vol_ratio": round(vol_1h / vol_avg1h, 2)})
        add("C6_CVD_Delta_Negativo",  1, cvd_1h < 0,
            {"cvd_delta": round(cvd_1h, 4)})
        add("C7_SMA10_menor_SMA20",   1, sma10_1h < sma20_1h,
            {"sma10": round(sma10_1h, 2), "sma20": round(sma20_1h, 2)})
    else:
        # Score LONG (original)
        add("C1_RSI_Sobreventa",      2, cur_rsi1h < 40,
            {"rsi_1h": cur_rsi1h})
        add("C2_Divergencia_Alcista", 2, bull_div)
        dist_sup = abs(price - lrc_dn) / price * 100 if lrc_dn else 999
        add("C3_Soporte_Cercano",     1, dist_sup <= 1.5,
            {"dist_soporte_pct": round(dist_sup, 2)})
        add("C4_BB_Inferior",         1, price <= bb_dn1h.iloc[-1],
            {"bb_lower_1h": round(bb_dn1h.iloc[-1], 2)})
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

    # ── Gatillo 5M ────────────────────────────────────────────────────────────
    if direction == "SHORT":
        trigger_active, trigger_details = check_trigger_5m_short(df5)
    else:
        trigger_active, trigger_details = check_trigger_5m(df5)

    # ── Sizing informativo ────────────────────────────────────────────────────
    atr_val    = float(calc_atr(df1h, ATR_PERIOD).iloc[-1])
    capital    = 1000.0
    risk_usd   = capital * 0.01
    # Kill switch #138 PR 3: halve risk for REDUCED symbols.
    try:
        from health import apply_reduce_factor
        risk_usd = apply_reduce_factor(risk_usd, symbol, _cfg)
    except Exception as e:
        log.warning("scan: reduce-factor lookup failed for %s: %s", symbol, e)

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

    # ── Veredicto ─────────────────────────────────────────────────────────────
    blocks = blocks_long if direction == "LONG" else blocks_short if direction == "SHORT" else []
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
