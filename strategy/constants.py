"""Shared trading constants — single source of truth for indicator periods,
score tiers, and LRC zone thresholds. Importable by btc_scanner, strategy/core,
strategy/sizing without circular dependencies (this module imports nothing).

Created in PR0 to eliminate the triplication that existed pre-2026-04-27 in
btc_scanner.py:67-73,412-422 / strategy/core.py:39-56 / strategy/sizing.py:8-9.
"""
from __future__ import annotations

# Indicator periods
LRC_PERIOD = 100
LRC_STDEV = 2.0
RSI_PERIOD = 14
BB_PERIOD = 20
BB_STDEV = 2.0
VOL_PERIOD = 20
ATR_PERIOD = 14

# ATR multiplier defaults (used when symbol_overrides has no per-symbol value)
ATR_SL_MULT_DEFAULT = 1.0
ATR_TP_MULT_DEFAULT = 4.0
ATR_BE_MULT_DEFAULT = 1.5

# LRC zone thresholds (entry windows)
LRC_LONG_MAX = 25.0   # LRC% ≤ 25 → LONG entry zone
LRC_SHORT_MIN = 75.0  # LRC% ≥ 75 → SHORT entry zone (gated by regime=BEAR)

# Score tier thresholds (Spot V6, 0-9 scale)
SCORE_MIN_HALF = 0    # below this → don't enter
SCORE_STANDARD = 2    # 0-1 = 0.5x size, 2-3 = 1.0x, ≥4 = 1.5x
SCORE_PREMIUM = 4
