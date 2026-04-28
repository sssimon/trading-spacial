"""Yang-Zhang annualized volatility — diagnostic utility (extracted from btc_scanner.py per #225).

NOT applied to position sizing. The vol-normalized sizing idea of #125 was
found to regress P&L in comparative backtest: the per-symbol atr_sl_mult/tp
tuning from epic #121 (735 sims) already adapts to volatility structurally.
Function kept available for telemetry / future dashboards.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


TARGET_VOL_ANNUAL = 0.15   # reference target (not currently applied)
VOL_LOOKBACK_DAYS = 30


def annualized_vol_yang_zhang(df_daily: pd.DataFrame) -> float:
    """Yang-Zhang annualized vol over daily bars (diagnostic utility).

    Not wired into position sizing. Returns TARGET_VOL_ANNUAL when fewer
    than 5 bars are available.
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
