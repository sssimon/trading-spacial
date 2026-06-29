"""
Calibración del gate de exposición por régimen — prueba de falsación.
Implementación CONGELADA según METODOLOGIA.md (2026-06-23). No cambia la metodología.

Lee data/program_ohlcv.db (anti-survivorship) + btc_dominance.csv (congelado).
NO toca data/holdout/, NO llama open_holdout/simulate_strategy. Período de señales
termina 2025-04-29 (barra antes del holdout 2025-04-30→2026-04-30).
Reusa regime.alt_season.compose_regime para el voto de 3 componentes (fidelidad).
"""
import sqlite3
import sys
from pathlib import Path
import pandas as pd
import numpy as np

HERE = Path(__file__).resolve().parent
# raíz del repo: .../data/retune/<dir>/ → subir 3
REPO_ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

SIGNAL_START = pd.Timestamp("2021-01-01", tz="UTC")
SIGNAL_END = pd.Timestamp("2025-04-29", tz="UTC")   # barra antes del holdout
HOLDOUT_START = pd.Timestamp("2025-04-30", tz="UTC")


_DEFAULT_DB = str(REPO_ROOT / "data" / "program_ohlcv.db")
_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def load_spot_daily(db_path: str, symbols=None) -> dict:
    con = sqlite3.connect(db_path)
    try:
        if symbols is None:
            symbols = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM spot_klines")]
        out = {}
        for sym in symbols:
            rows = con.execute(
                "SELECT open_time, open, high, low, close, volume FROM spot_klines "
                "WHERE symbol=? ORDER BY open_time", (sym,)).fetchall()
            if not rows:
                continue
            df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume"])
            df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            df = df.set_index("ts")
            daily = df.resample("1D").agg(_AGG).dropna(subset=["close"])
            daily["quote_vol"] = daily["volume"] * daily["close"]
            daily.index = daily.index.normalize()
            out[sym] = daily
        return out
    finally:
        con.close()


# Gate de vida
MIN_MEDIAN_QUOTE_VOL_30D = 500_000.0

# Reglas
POS_THRESHOLD = 0.25
RSI_THRESHOLD = 40.0
CONSOL_THRESHOLD = 45.0
VOL_RATIO_THRESHOLD = 0.85

# Forward
TP = 0.20
SL = -0.12
RULE_HOLD_DAYS = 14
WIN15_THRESHOLD = 0.15


def load_btc_dominance(csv_path: str) -> pd.Series:
    """CSV (date,dominance) → Series index=fecha UTC, valor=fracción 0-1.
    Normaliza desde 0-100 si el máximo sugiere porcentaje (>1.5)."""
    df = pd.read_csv(csv_path)
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.normalize()
    s = pd.Series(df["dominance"].astype(float).values, index=df["date"])
    s = s[~s.index.duplicated(keep="last")].sort_index()
    if s.max() > 1.5:
        s = s / 100.0
    s.name = "btc_dominance"
    return s


# ----------------------------------------------------------------------------
# Features ex-ante
# Definición canónica: screener/valley_filter.measure_setup + edge_study.py
# Copiado VERBATIM de data/retune/2026-06-18-setup-edge-multiregimen/edge_study.py
# líneas 203-326. Única adición: ret_30d (forward 30d para outperf de régimen).
# ----------------------------------------------------------------------------
def wilder_rsi(close, period=14):
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = (-delta).clip(lower=0.0)
    # Wilder smoothing == EMA con alpha = 1/period (com = period-1)
    roll_up = up.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    roll_down = down.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = roll_up / roll_down.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # si roll_down==0 (sólo subidas) → RSI 100
    rsi = rsi.where(roll_down != 0.0, 100.0)
    return rsi


def compute_features(df):
    """Añade columnas de features ex-ante (todas con datos ≤ t). Sin lookahead."""
    close = df["close"]
    high = df["high"]
    low = df["low"]
    qv = df["quote_vol"]

    # pos_in_30d_range (ventana de 30 incluye t → es ex-ante: usa datos ≤ t)
    roll_min_low = low.rolling(30, min_periods=30).min()
    roll_max_high = high.rolling(30, min_periods=30).max()
    denom = (roll_max_high - roll_min_low)
    clamp = (1e-9 * close).abs()
    denom_clamped = np.maximum(denom, clamp)
    df["pos_in_30d_range"] = (close - roll_min_low) / denom_clamped

    df["rsi14"] = wilder_rsi(close, 14)

    sma20 = close.rolling(20, min_periods=20).mean()
    sma50 = close.rolling(50, min_periods=50).mean()
    df["sma20"] = sma20
    df["sma50"] = sma50
    df["pct_vs_sma20"] = (close - sma20) / sma20 * 100.0
    df["pct_vs_sma50"] = (close - sma50) / sma50 * 100.0

    median_close_30 = close.rolling(30, min_periods=30).median()
    df["consol_30d"] = (roll_max_high - roll_min_low) / median_close_30 * 100.0

    med_qv_3 = qv.rolling(3, min_periods=3).median()
    med_qv_30 = qv.rolling(30, min_periods=30).median()
    df["vol_ratio"] = med_qv_3 / med_qv_30.replace(0.0, np.nan)

    # --- Gate de vida ---
    df["median_qv_30d"] = med_qv_30
    # no agonizante: mediana últimos 90d ≥ 0.5 × mediana de los 90d previos (90..180 atrás)
    med_qv_90 = qv.rolling(90, min_periods=90).median()
    med_qv_90_prev = med_qv_90.shift(90)
    df["not_dying"] = med_qv_90 >= 0.5 * med_qv_90_prev
    # < 50% velas planas en 90d (vela plana = high == low)
    flat = (high == low).astype(float)
    df["flat_frac_90d"] = flat.rolling(90, min_periods=90).mean()

    alive = (
        (df["median_qv_30d"] >= MIN_MEDIAN_QUOTE_VOL_30D)
        & (df["not_dying"] == True)  # noqa: E712
        & (df["flat_frac_90d"] < 0.5)
    )
    df["alive"] = alive.fillna(False)

    # close > SMA50 para breadth (régimen)
    df["above_sma50"] = (close > sma50)

    # --- Forward (entrada = open en t+1) ---
    entry = df["open"].shift(-1)
    df["entry_next_open"] = entry
    for k in (7, 14, 30):
        # max high en los k días [t+1 .. t+k]
        # rolling max sobre high, alineado a futuro
        fwd_max_high = high.shift(-1).rolling(k, min_periods=1).max().shift(-(k - 1))
        df[f"max_fwd_{k}d"] = (fwd_max_high - entry) / entry

    # rule_return: primero de +20% TP / −12% SL / cierre en t+14
    df["rule_return"] = _compute_rule_return(df)

    # win15
    df["win15"] = df["max_fwd_14d"] >= WIN15_THRESHOLD

    # ret_30d: retorno forward 30d para outperf del régimen
    df["ret_30d"] = (close - close.shift(30)) / close.shift(30)

    return df


def _compute_rule_return(df):
    """rule_return vectorizado-ish: recorre t+1..t+14 buscando TP/SL intrabar, sino cierre t+14.

    Convención de orden intrabar: si en el mismo día se tocan TP y SL (high≥tp y low≤sl),
    asumimos que SL se toca primero (conservador). Entrada = open en t+1.
    """
    n = len(df)
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    entries = df["entry_next_open"].to_numpy()
    out = np.full(n, np.nan)
    for i in range(n):
        entry = entries[i]
        if not np.isfinite(entry) or entry <= 0:
            continue
        tp_price = entry * (1.0 + TP)
        sl_price = entry * (1.0 + SL)
        # ventana t+1 .. t+14
        start = i + 1
        end = min(i + RULE_HOLD_DAYS, n - 1)  # índice del día de cierre (t+14)
        if start > n - 1:
            continue
        resolved = False
        for j in range(start, min(start + RULE_HOLD_DAYS, n)):
            hj = highs[j]
            lj = lows[j]
            # SL primero si ambos se tocan (conservador)
            if lj <= sl_price:
                out[i] = SL
                resolved = True
                break
            if hj >= tp_price:
                out[i] = TP
                resolved = True
                break
        if not resolved:
            # cierre en el último día de la ventana disponible
            close_idx = min(start + RULE_HOLD_DAYS - 1, n - 1)
            out[i] = (closes[close_idx] - entry) / entry
    return out
