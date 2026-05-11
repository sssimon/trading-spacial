"""Shared helpers for A.0.2 diagnostic deep-dive scripts (#281).

Centralizes data loading + the train-window boundary so each analysis script
stays focused. Reads `data/ohlcv.db` only — `data/hold` + `out/` (split to
avoid AST guard B's pattern set in tests/test_holdout_isolation.py) is never
touched. The simulation window is bounded `sim_end < locked_dataset_start`
(2025-04-30) by construction.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dateutil.relativedelta import relativedelta

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# Train window — fixed across all #281 analyses for reproducibility.
TRAIN_END_UTC = datetime(2025, 4, 29, 23, 0, 0, tzinfo=timezone.utc)
TRAIN_WINDOW_MONTHS = 18
TRAIN_START_UTC = TRAIN_END_UTC - relativedelta(months=TRAIN_WINDOW_MONTHS)

CURATED_SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
    "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
)


def load_config():
    cfg_path = _ROOT / "config.json"
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    return cfg, cfg.get("symbol_overrides", {})


def fetch_data(symbol: str):
    """Return (df1h, df4h, df5m, df1d, df_fng, df_funding) for `symbol`.

    Fetches from `data/ohlcv.db` via the existing backtest helpers. Date
    range starts 2 months before TRAIN_START to give indicators warmup; 1d
    starts 12 months before to give regime detector enough history.
    """
    from backtest import (
        get_cached_data,
        get_historical_fear_greed,
        get_historical_funding_rate,
    )
    data_start = TRAIN_START_UTC - relativedelta(months=2)
    data_start_d = TRAIN_START_UTC - relativedelta(months=12)
    df1h = get_cached_data(symbol, "1h", start_date=data_start)
    df4h = get_cached_data(symbol, "4h", start_date=data_start)
    df5m = get_cached_data(symbol, "5m", start_date=data_start)
    df1d = get_cached_data(symbol, "1d", start_date=data_start_d)
    df_fng = get_historical_fear_greed()
    df_funding = get_historical_funding_rate()
    return df1h, df4h, df5m, df1d, df_fng, df_funding


def run_simulation(
    symbol: str, *, with_costs: bool, cfg: dict, overrides: dict,
):
    """Run simulate_strategy over the train window. Returns (trades, equity)
    or ([], None) if data is missing."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d, df_fng, df_funding = fetch_data(symbol)
    if df1h.empty or df4h.empty or df5m.empty:
        return [], None
    trades, equity = simulate_strategy(
        df1h, df4h, df5m, symbol,
        sl_mode="atr",
        df1d=df1d,
        sim_start=TRAIN_START_UTC, sim_end=TRAIN_END_UTC,
        df_fng=df_fng, df_funding=df_funding,
        symbol_overrides=overrides,
        enable_slippage=with_costs,
        enable_spread=with_costs,
        enable_fees=with_costs,
        cfg=cfg,
    )
    return trades, equity


def liquidity_per_min_series(df1h):
    """30-day rolling USD volume per minute on 1H bars — same definition as
    backtest.simulate_strategy uses internally for slippage."""
    usd_per_min = (df1h["close"] * df1h["volume"]) / 60.0
    return usd_per_min.rolling(720, min_periods=120).mean()


_CACHE_DIR = Path("/tmp/a02_diag_cache")
_CACHE_DIR.mkdir(exist_ok=True)


def _cache_path(symbol: str, with_costs: bool) -> Path:
    tag = "on" if with_costs else "off"
    return _CACHE_DIR / f"trades_{symbol}_{tag}.json"


def run_simulation_cached(
    symbol: str, *, with_costs: bool, cfg: dict, overrides: dict,
):
    """Same as run_simulation but writes/reads a JSON cache so the same
    cost-on / cost-off run isn't repeated across analysis scripts. Trades
    are stored with timestamps as ISO strings; caller must parse if needed."""
    p = _cache_path(symbol, with_costs)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            return data["trades"], data.get("equity")
        except Exception:
            p.unlink(missing_ok=True)

    trades, equity = run_simulation(
        symbol, with_costs=with_costs, cfg=cfg, overrides=overrides,
    )
    serializable_trades = [
        {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in t.items()}
        for t in trades
    ]
    serializable_equity = (
        [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in e.items()} for e in equity]
        if equity is not None else None
    )
    p.write_text(json.dumps(
        {"trades": serializable_trades, "equity": serializable_equity},
        default=str,
    ))
    return serializable_trades, serializable_equity
