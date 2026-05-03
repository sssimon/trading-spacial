"""End-to-end smoke test for the time-limit barrier across the curated basket.

Runs simulate_strategy across the 10 curated symbols on a fixed train segment
(pre-holdout) using the per-symbol time_limit_hours from config.defaults.json.

Assertions:
- Per symbol (with closed trades): 0% <= TIME_LIMIT_count / closed_count <= 60%
- SL count >= 10% of closed (the time-limit barrier must not starve SL exits)
- TP count >= 3% of closed (TP must remain reachable at the aggregate level)
- 0 exceptions, 0 NaN propagation
- BTC regression pin: TIME_LIMIT_count > 0 AND TIME_LIMIT_count < SL_count

Skipped without cached market data — guards on `data/ohlcv.db`.

Why TP floor is 3% (not higher): the barrier intentionally captures positions
before TP can fire for symbols with tight horizons (the 5h basket). Aggregate
TP behaviour reflects that — tight-horizon symbols dominate the TP-suppression
regime. The 3% floor is a "TP not completely dead" regression net, not a
quality bar.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone

import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OHLCV_DB = os.path.join(REPO_ROOT, "data", "ohlcv.db")
CONFIG_DEFAULTS_PATH = os.path.join(REPO_ROOT, "config.defaults.json")


CURATED_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
    "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
]


@pytest.mark.skipif(
    not os.path.exists(OHLCV_DB), reason="requires cached market data (data/ohlcv.db)",
)
def test_smoke_time_limit_across_curated_symbols(tmp_path, monkeypatch):
    """Per-symbol time-limit smoke: ratios, no exceptions, no NaN, BTC pin."""
    from backtest import simulate_strategy, get_cached_data
    import btc_api

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()

    # Source-of-truth cfg (defaults). Each symbol's time_limit_hours must be
    # present here so a future config refactor cannot silently drop the field.
    with open(CONFIG_DEFAULTS_PATH) as f:
        cfg = json.load(f)

    symbol_overrides = cfg.get("symbol_overrides", {})
    for sym in CURATED_SYMBOLS:
        assert "time_limit_hours" in symbol_overrides.get(sym, {}), (
            f"{sym} must have time_limit_hours in config.defaults.json"
        )

    # 18-month train segment, well before holdout cutoff (2025-04-30).
    sim_start = datetime(2023, 10, 1, tzinfo=timezone.utc)
    sim_end = datetime(2025, 3, 31, tzinfo=timezone.utc)
    data_start = datetime(2022, 1, 1, tzinfo=timezone.utc)

    # BTC daily bars used as global regime anchor — fetched once.
    df1d_btc = get_cached_data("BTCUSDT", "1d", start_date=data_start)
    if df1d_btc.empty:
        pytest.skip("BTCUSDT 1d not cached")

    summary: dict[str, dict] = {}

    for symbol in CURATED_SYMBOLS:
        df1h = get_cached_data(symbol, "1h", start_date=data_start)
        df4h = get_cached_data(symbol, "4h", start_date=data_start)
        df5m = get_cached_data(symbol, "5m", start_date=data_start)
        df1d = get_cached_data(symbol, "1d", start_date=data_start)
        if df1h.empty or df4h.empty or df5m.empty:
            pytest.skip(f"{symbol} market data not cached")

        trades, _equity = simulate_strategy(
            df1h, df4h, df5m, symbol,
            sim_start=sim_start, sim_end=sim_end,
            df1d=df1d, df1d_btc=df1d_btc,
            cfg=cfg, symbol_overrides=symbol_overrides,
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

        assert isinstance(trades, list)

        # No NaN propagation in pnl_usd / pnl_pct — propagated NaN would
        # poison downstream metrics and is a hard regression signal.
        for t in trades:
            assert t["pnl_usd"] is not None and not math.isnan(t["pnl_usd"]), (
                f"NaN pnl_usd for {symbol}: {t}"
            )
            assert t["pnl_pct"] is not None and not math.isnan(t["pnl_pct"]), (
                f"NaN pnl_pct for {symbol}: {t}"
            )

        closed = [t for t in trades if t["exit_reason"] != "OPEN"]
        if not closed:
            summary[symbol] = {"closed": 0, "tl": 0, "sl": 0, "tp": 0}
            continue

        sl_count = sum(1 for t in closed if t["exit_reason"] == "SL")
        tp_count = sum(1 for t in closed if t["exit_reason"] == "TP")
        tl_count = sum(1 for t in closed if t["exit_reason"] == "TIME_LIMIT")

        summary[symbol] = {
            "closed": len(closed),
            "tl": tl_count,
            "sl": sl_count,
            "tp": tp_count,
        }

        # Per-symbol ratio bounds. Lower bound 0% (some tight-TL symbols may
        # rarely trigger if SL/TP fire first); upper bound 60% guards against
        # time-limit dominating exit behavior.
        # Skip the ratio assertion when closed < 30: floor-cap symbols
        # (XLM/PENDLE under the participation cap) intentionally drop into
        # single-digit territory, where the per-symbol ratio is unstable
        # (each trade swings the percentage by 10+ points). The aggregate
        # SL/TP floor below + the BTC pin carry the regression net at low N.
        if len(closed) < 30:
            continue
        tl_ratio = tl_count / len(closed)
        assert 0.0 <= tl_ratio <= 0.60, (
            f"{symbol} TIME_LIMIT ratio out of bounds [0%, 60%]: "
            f"{tl_count}/{len(closed)} = {tl_ratio:.2%}"
        )

    print("\n=== SMOKE TEST PER-SYMBOL SUMMARY ===")
    for sym, s in summary.items():
        if s["closed"] > 0:
            print(
                f"  {sym}: closed={s['closed']:4d} "
                f"SL={s['sl']:3d} ({s['sl']/s['closed']:5.1%}) "
                f"TP={s['tp']:3d} ({s['tp']/s['closed']:5.1%}) "
                f"TL={s['tl']:3d} ({s['tl']/s['closed']:5.1%})"
            )
        else:
            print(f"  {sym}: no closed trades")

    # BTC pin: the 14h time-limit must produce a non-trivial number of TL
    # exits, but must not dominate over SL exits — that would mean the
    # barrier is over-firing and overriding actual stop-loss exits.
    btc = summary.get("BTCUSDT")
    if btc and btc["closed"] > 0:
        assert btc["tl"] > 0, (
            f"BTC pin: expected at least one TIME_LIMIT exit; summary={btc}"
        )
        assert btc["tl"] < btc["sl"], (
            f"BTC pin: TIME_LIMIT must not exceed SL count; got tl={btc['tl']} "
            f"sl={btc['sl']}"
        )

    # Aggregate across all symbols: SL >= 10% of closed, TP >= 3% of closed.
    # See module docstring for why TP is 3%: tight-horizon symbols dominate
    # the TP-suppression regime when the barrier captures positions early.
    total_closed = sum(s["closed"] for s in summary.values())
    total_sl = sum(s["sl"] for s in summary.values())
    total_tp = sum(s["tp"] for s in summary.values())
    if total_closed > 0:
        assert total_sl / total_closed >= 0.10, (
            f"aggregate SL ratio {total_sl}/{total_closed} below 10%"
        )
        assert total_tp / total_closed >= 0.03, (
            f"aggregate TP ratio {total_tp}/{total_closed} below 3%"
        )
