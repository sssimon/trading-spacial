"""Constantes CONGELADAS del probe valle-calidad (sondeo pre-celda).

Pre-registro: data/retune/2026-06-11-valle-calidad-probe/PREREGISTRO.md
(criterios congelados 2026-06-11, ANTES de correr). Ningún valor de gate /
poder / horizonte se mueve tras ver resultados.

La definición de "valle" NO vive aquí: se importa del screener A
(screener.valley_filter), única fuente de verdad. Esto solo re-exporta los
dos parámetros que el probe necesita citar, atados por test a su origen."""
from __future__ import annotations

from screener.valley_filter import CONSOLIDATION_WINDOW_DAYS, RANGE_BAND_MAX

# ── Ventana del estudio (pre-holdout en tiempo, igual que celda 4) ──────────
STUDY_START = "2021-01-01"
STUDY_END = "2025-04-30"

# ── Estrategia pre-declarada ────────────────────────────────────────────────
HOLD_DAYS = 20                 # H, declarado; sin grid de horizontes
NOTIONAL_USD = 1000.0          # notional fijo → pooling $-weighted

# ── Bootstrap (block por episodio) ──────────────────────────────────────────
BOOTSTRAP_ITERS = 10000
SEED = 42

# ── Poder ───────────────────────────────────────────────────────────────────
MIN_EPISODES_VALLE = 30        # < esto ⟹ UNDERPOWERED / INCONCLUSO

# ── Robustez (reporte, NO gate) ─────────────────────────────────────────────
REGIME_SPLIT = "2023-03-01"    # punto medio temporal; parte las dos mitades

# ── Datos ───────────────────────────────────────────────────────────────────
DB_PATH = "data/program_ohlcv.db"

__all__ = [
    "STUDY_START", "STUDY_END", "HOLD_DAYS", "NOTIONAL_USD",
    "BOOTSTRAP_ITERS", "SEED", "MIN_EPISODES_VALLE", "REGIME_SPLIT", "DB_PATH",
    "CONSOLIDATION_WINDOW_DAYS", "RANGE_BAND_MAX",
]
