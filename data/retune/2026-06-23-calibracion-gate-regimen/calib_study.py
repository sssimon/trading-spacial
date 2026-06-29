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
