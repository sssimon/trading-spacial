"""Pass 1: generate the per-symbol base trade stream (no kill switch) over the
pre-holdout window, with a hard holdout cutoff and bankruptcy flagging.

Uses api.config.load_config() (the production layered merge: hardcoded →
config.defaults.json → config.secrets.json → config.json → env) for
symbol_overrides + tuned defaults, _load_frames from tools.regime_retune_pre_holdout
for OHLCV frames, and backtest.simulate_strategy (apply_kill_switch=False).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger("ks_stress_replay.base_stream")

# Holdout starts 2025-04-30T00:00:00Z (.mex/context/decisions.md:39). Bars used
# by this harness are strictly < this cutoff. NON-NEGOTIABLE #3.
HOLDOUT_CUTOFF = datetime(2025, 4, 30, tzinfo=timezone.utc)

CURATED_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
    "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
]


def truncate_at_bankruptcy(trades: list[dict]) -> list[dict]:
    """Drop every trade after the first BANKRUPT marker (inclusive of it).

    The post-#280 simulator emits a BANKRUPT record then keeps processing
    zero-risk trades; including them saturates portfolio DD (caveat #2 in
    .mex/context/decisions.md). Truncating at the first BANKRUPT stops that.
    """
    out: list[dict] = []
    for tr in trades:
        out.append(tr)
        if tr.get("exit_reason") == "BANKRUPT":
            break
    return out


def flag_bankruptcies(stream: dict) -> list[str]:
    """Return sorted symbols whose base stream contains a BANKRUPT trade."""
    flagged = [
        sym for sym, trades in stream.items()
        if any(tr.get("exit_reason") == "BANKRUPT" for tr in trades)
    ]
    return sorted(flagged)


def generate_base_stream(
    symbols: list[str] | None = None,
    cutoff: datetime = HOLDOUT_CUTOFF,
) -> dict:
    """Run simulate_strategy(apply_kill_switch=False) per symbol; return
    dict[symbol -> truncated trade list]. Holdout cutoff enforced.
    """
    if cutoff > HOLDOUT_CUTOFF:
        raise AssertionError(
            f"cutoff {cutoff} exceeds holdout start {HOLDOUT_CUTOFF} — "
            "NON-NEGOTIABLE #3 forbids reading holdout-window frames."
        )
    syms = symbols if symbols is not None else CURATED_SYMBOLS

    from backtest import simulate_strategy
    from tools.regime_retune_pre_holdout import _load_frames
    from api.config import load_config

    app_config = load_config()
    if not app_config.get("symbol_overrides"):
        raise RuntimeError(
            "load_config() returned no symbol_overrides — config.defaults.json "
            "missing or empty. The base stream would run with generic ATR "
            "multipliers, contaminating the v1-vs-v2 comparison."
        )
    overrides = app_config.get("symbol_overrides", {}) or {}

    stream: dict = {}
    for sym in syms:
        frames = _load_frames(sym, cutoff)
        if frames["df1h"].empty or frames["df4h"].empty or frames["df5m"].empty:
            log.warning("empty OHLCV below cutoff for %s — skipping", sym)
            stream[sym] = []
            continue
        trades, _equity = simulate_strategy(
            df1h=frames["df1h"], df4h=frames["df4h"], df5m=frames["df5m"],
            df1d=frames["df1d"], df1d_btc=frames["df1d_btc"],
            df_fng=frames["df_fng"], df_funding=frames["df_funding"],
            symbol=sym, sl_mode="atr", symbol_overrides=overrides,
            cfg=app_config, enable_slippage=True, enable_spread=True,
            enable_fees=True,
        )
        stream[sym] = truncate_at_bankruptcy(trades)
    return stream
