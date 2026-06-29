"""
Calibración del gate de exposición por régimen — prueba de falsación.
Implementación CONGELADA según METODOLOGIA.md (2026-06-23). No cambia la metodología.

Lee data/program_ohlcv.db (anti-survivorship) + btc_dominance.csv (congelado).
NO toca data/holdout/, NO llama open_holdout/simulate_strategy. Período de señales
termina 2025-04-29 (barra antes del holdout 2025-04-30→2026-04-30).
Reusa regime.alt_season.compose_regime para el voto de 3 componentes (fidelidad).
"""
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
