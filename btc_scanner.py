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
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
    "ADAUSDT","AVAXUSDT","DOGEUSDT","DOTUSDT","MATICUSDT",
    "LINKUSDT","LTCUSDT","UNIUSDT","ATOMUSDT","XLMUSDT",
    "NEARUSDT","FILUSDT","APTUSDT","OPUSDT","ARBUSDT",
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

# ── Parámetros de la estrategia Spot 1H ────────────────────────────────────
LRC_LONG_MAX   = 25.0     # LRC% ≤ 25  →  zona de entrada
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

BINANCE_URLS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api4.binance.com",
]

# Intervalos Bybit  (distintos a Binance)
_BYBIT_INTERVAL = {"1m":"1","3m":"3","5m":"5","15m":"15","30m":"30",
                   "1h":"60","2h":"120","4h":"240","6h":"360","12h":"720",
                   "1d":"D","1w":"W","1M":"M"}

_active_provider = None   # "binance" | "bybit"  — se detecta la primera vez
_provider_lock = threading.Lock()           # protege escrituras a _active_provider
_provider_fail_count = 0                    # llamadas en modo Bybit desde el último intento de Binance
_RECOVERY_INTERVAL = 10                     # cada N llamadas en Bybit, reintentar Binance


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


def _get(url: str, params: dict = None) -> dict:
    """HTTP GET con soporte de proxy y timeout."""
    proxies = _load_proxy()
    r = requests.get(url, params=params, proxies=proxies or None,
                     timeout=12, headers={"User-Agent": "btc-scanner/1.0"})
    r.raise_for_status()
    return r.json()


# ── Binance ───────────────────────────────────────────────────────────────────

def _klines_binance(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    """
    Intenta cada URL mirror de Binance en orden.
    Devuelve DataFrame normalizado o lanza excepción si todos fallan.
    """
    last_err = None
    for base in BINANCE_URLS:
        try:
            data = _get(f"{base}/api/v3/klines",
                        {"symbol": symbol, "interval": interval, "limit": limit})
            df = pd.DataFrame(data, columns=[
                "ts","open","high","low","close","volume",
                "close_time","quote_vol","trades",
                "taker_buy_base","taker_buy_quote","ignore"
            ])
            for c in ["open","high","low","close","volume",
                      "taker_buy_base","taker_buy_quote"]:
                df[c] = df[c].astype(float)
            df["ts"] = pd.to_datetime(df["ts"], unit="ms")
            df.set_index("ts", inplace=True)
            log.debug(f"Binance OK ({base})")
            return df
        except Exception as e:
            log.warning(f"Binance {base} → {type(e).__name__}: {e}")
            last_err = e
    raise last_err


# ── Bybit ─────────────────────────────────────────────────────────────────────

def _klines_bybit(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    """
    Bybit V5 Spot klines → mismo formato de DataFrame que Binance.

    Bybit no provee taker_buy_base directamente en klines;
    se aproxima con el ratio (close-low)/(high-low) × volume
    para velas alcistas, y el inverso para bajistas.
    Esta aproximación es suficiente para el cálculo de CVD delta.
    """
    byt_interval = _BYBIT_INTERVAL.get(interval, interval.replace("m","").replace("h","60"))
    data = _get("https://api.bybit.com/v5/market/kline",
                {"category": "spot", "symbol": symbol,
                 "interval": byt_interval, "limit": limit})
    if data.get("retCode", -1) != 0:
        raise RuntimeError(f"Bybit error: {data.get('retMsg')}")

    rows = data["result"]["list"]          # orden: más reciente primero
    rows = list(reversed(rows))            # → cronológico
    df = pd.DataFrame(rows, columns=[
        "ts","open","high","low","close","volume","turnover"
    ])
    for c in ["open","high","low","close","volume","turnover"]:
        df[c] = df[c].astype(float)
    df["ts"] = pd.to_datetime(df["ts"].astype(float), unit="ms")
    df.set_index("ts", inplace=True)

    # Aproximar taker_buy_base
    hl = (df["high"] - df["low"]).replace(0, 1e-9)
    bullish = df["close"] >= df["open"]
    df["taker_buy_base"] = np.where(
        bullish,
        df["volume"] * (df["close"] - df["low"]) / hl,
        df["volume"] * (df["high"] - df["close"]) / hl,
    )
    df["taker_buy_quote"] = df["taker_buy_base"] * df["close"]
    log.debug("Bybit OK")
    return df


# ── Punto de entrada unificado ────────────────────────────────────────────────

def get_klines(symbol: str, interval: str, limit: int = 210) -> pd.DataFrame:
    """
    Obtiene velas OHLCV con detección automática de proveedor.

    Intenta Binance primero (todos los mirrors). Si todos fallan con
    error de red o HTTP >= 400, cambia a Bybit automáticamente y
    registra el proveedor activo para los siguientes ciclos.

    Cuando el proveedor activo es Bybit, cada _RECOVERY_INTERVAL
    llamadas se reintenta Binance. Si responde, se restaura como
    proveedor principal.
    """
    global _active_provider, _provider_fail_count

    # Si ya sabemos que Bybit funciona, ir directo — pero reintentar
    # Binance periódicamente para recuperar el proveedor preferido.
    if _active_provider == "bybit":
        with _provider_lock:
            _provider_fail_count += 1
            should_retry = (_provider_fail_count >= _RECOVERY_INTERVAL)
            if should_retry:
                _provider_fail_count = 0

        if should_retry:
            try:
                df = _klines_binance(symbol, interval, limit)
                with _provider_lock:
                    _active_provider = "binance"
                log.info("✅ Binance recuperado — volviendo a proveedor principal")
                return df
            except Exception:
                log.debug("Binance sigue sin responder, manteniendo Bybit")

        return _klines_bybit(symbol, interval, limit)

    # Intentar Binance
    try:
        df = _klines_binance(symbol, interval, limit)
        if _active_provider != "binance":
            with _provider_lock:
                _active_provider = "binance"
            log.info("✅ Proveedor de datos: Binance")
        return df
    except Exception as binance_err:
        log.warning(f"⚠️  Todos los mirrors de Binance fallaron ({binance_err}). "
                    f"Cambiando a Bybit…")

    # Fallback a Bybit
    df = _klines_bybit(symbol, interval, limit)
    if _active_provider != "bybit":
        with _provider_lock:
            _active_provider = "bybit"
            _provider_fail_count = 0
        log.info("✅ Proveedor de datos: Bybit (fallback automático)")
    return df


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


def calc_cvd_delta(df: pd.DataFrame, n=3):
    """Proxy CVD: volumen taker buy − sell últimas n barras."""
    buy  = df["taker_buy_base"].tail(n)
    sell = (df["volume"] - df["taker_buy_base"]).tail(n)
    return float((buy - sell).sum())


def detect_rsi_divergence(close: pd.Series, rsi: pd.Series, window=30):
    """
    Divergencia alcista: precio hace mínimo más bajo, RSI hace mínimo más alto.
    Ventana de 30 barras para 1H (≈ 1.25 días).
    """
    if len(close) < window:
        return False
    p = close.iloc[-window:].values
    r = rsi.iloc[-window:].values
    mins = [i for i in range(1, window - 1)
            if p[i] < p[i - 1] and p[i] < p[i + 1]]
    if len(mins) >= 2:
        a, b = mins[-2], mins[-1]
        return bool(p[b] < p[a] and r[b] > r[a])
    return False


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


# ─────────────────────────────────────────────────────────────────────────────
#  SCANNER PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def scan(symbol: str = None):
    symbol = symbol or SYMBOL
    ts  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    rep = {"timestamp": ts, "symbol": symbol, "errors": []}

    # ── Datos de mercado ──────────────────────────────────────────────────────
    df5  = get_klines(symbol, "5m",  limit=210)   # gatillo
    df1h = get_klines(symbol, "1h",  limit=210)   # señal principal
    df4h = get_klines(symbol, "4h",  limit=150)   # contexto macro

    price = df1h["close"].iloc[-1]   # precio de cierre de la última vela 1H

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
    bull_div  = detect_rsi_divergence(df1h["close"], rsi1h, window=30)

    # ── Indicadores 4H (macro) ────────────────────────────────────────────────
    sma100_4h      = calc_sma(df4h["close"], 100).iloc[-1]
    price_above_4h = bool(price > sma100_4h)

    # ── Condición Primaria (1H) ───────────────────────────────────────────────
    in_long_zone = lrc_pct is not None and lrc_pct <= LRC_LONG_MAX

    # ── Contexto macro 4H ─────────────────────────────────────────────────────
    macro_ok = price_above_4h   # precio por encima de SMA100 en 4H

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
    }

    blocks = []
    if bull_eng:
        blocks.append("E1: BullEngulfing activo — posible micro-techo, esperar próxima vela")

    # ── Score de Confirmaciones 1H (Spot V6) ──────────────────────────────────
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

    # C1: RSI 1H < 40
    add("C1_RSI_Sobreventa",      2, cur_rsi1h < 40,
        {"rsi_1h": cur_rsi1h})

    # C2: Divergencia alcista RSI (1H)
    add("C2_Divergencia_Alcista", 2, bull_div)

    # C3: Cerca de soporte (LRC lower como proxy de soporte estructural)
    dist_sup = abs(price - lrc_dn) / price * 100 if lrc_dn else 999
    add("C3_Soporte_Cercano",     1, dist_sup <= 1.5,
        {"dist_soporte_pct": round(dist_sup, 2)})

    # C4: Toque de Banda de Bollinger inferior (1H)
    add("C4_BB_Inferior",         1, price <= bb_dn1h.iloc[-1],
        {"bb_lower_1h": round(bb_dn1h.iloc[-1], 2)})

    # C5: Volumen por encima del promedio (1H)
    add("C5_Volumen",             1, bool(vol_1h >= vol_avg1h),
        {"vol_ratio": round(vol_1h / vol_avg1h, 2)})

    # C6: CVD Delta positivo (compradores netos en últimas 3 barras 1H)
    add("C6_CVD_Delta_Positivo",  1, cvd_1h > 0,
        {"cvd_delta": round(cvd_1h, 4)})

    # C7: SMA10 > SMA20 (tendencia local alcista en 1H)
    add("C7_SMA10_mayor_SMA20",   1, sma10_1h > sma20_1h,
        {"sma10": round(sma10_1h, 2), "sma20": round(sma20_1h, 2)})

    # C8: DXY (manual)
    conf["C8_DXY_Bajando"] = {
        "pass": "MANUAL", "pts": "?", "max_pts": 1,
        "nota": "DXY bajando o lateral → verificar TradingView (DXY < SMA20)",
    }

    # ── Gatillo 5M ────────────────────────────────────────────────────────────
    trigger_active, trigger_details = check_trigger_5m(df5)

    # ── Sizing informativo (1H Spot) ──────────────────────────────────────────
    capital    = 1000.0
    risk_usd   = capital * 0.01
    sl_dist    = price * (SL_PCT / 100)
    qty_btc    = risk_usd / sl_dist
    val_pos    = qty_btc * price
    # Spot: valor posición no puede superar 98% del capital
    if val_pos > capital * 0.98:
        qty_btc = (capital * 0.98) / price
        val_pos  = qty_btc * price

    tp_price   = round(price * (1 + TP_PCT / 100), 2)
    sl_price   = round(price * (1 - SL_PCT / 100), 2)

    # ── Veredicto ─────────────────────────────────────────────────────────────
    if not in_long_zone:
        estado = "⏳ SIN SETUP — LRC% fuera de zona (> 25%)"
        señal  = False
    elif blocks:
        estado = f"🚫 BLOQUEADA — {len(blocks)} condición(es) de exclusión automática"
        señal  = False
    elif not macro_ok:
        estado = "⚠️  SETUP TÉCNICO — Macro 4H adversa (precio < SMA100 4H)"
        señal  = False
    elif not trigger_active:
        estado = "🕐 SETUP VÁLIDO — Esperando gatillo 5M"
        señal  = False
    else:
        # Señal completa
        sl = score_label(score)
        estado = f"✅ SEÑAL + GATILLO CONFIRMADOS — Calidad: {sl}"
        señal  = True

    # ── Consolidar ────────────────────────────────────────────────────────────
    rep.update({
        "estado":         estado,
        "señal_activa":   señal,
        "price":          round(price, 2),
        "lrc_1h": {
            "pct":   lrc_pct,
            "upper": lrc_up,
            "lower": lrc_dn,
            "mid":   lrc_mid,
        },
        "rsi_1h":         cur_rsi1h,
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
            "sl_pct":      f"{SL_PCT}%",
            "tp_pct":      f"{TP_PCT}%",
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
        DIV,
        "  ── SETUP 1H  (señal principal) ──────────────────────────",
        f"  LRC 1H : {rep['lrc_1h']['pct']}%   {'✅ ZONA LONG (≤ 25%)' if rep['lrc_1h']['pct'] and rep['lrc_1h']['pct'] <= 25 else '⏳ Fuera de zona'}",
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
