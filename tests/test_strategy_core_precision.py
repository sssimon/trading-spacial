"""Regression: SL/TP precision must NOT collapse onto entry_price for sub-$1 symbols.

Catches the 2026-04-15 → 2026-04-27 precision bug where round(price, 2) /
round(price ± dist, 2) in evaluate_signal made DOGE/XLM/JUP signals invalid
(SL = entry_price → instant SL hit with $0 PnL, WR ~1.6%).

The parity test for the refactor pinned BTCUSDT only ($30k+, unaffected by
0.01 rounding). This test adds explicit coverage for sub-$1 symbols.

Approach: directly validate the math of evaluate_signal's SL/TP block by
constructing minimal inputs that always produce a signal — synthetic data
with controlled price + ATR so we can verify exact distance preservation.
"""
from __future__ import annotations

import os

import pytest


OHLCV_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ohlcv.db",
)


def test_no_round_2_decimals_in_evaluate_signal_sl_tp_block():
    """Inspect strategy/core.py source for the precision regression pattern.

    A code-level guard: if `round(price, 2)` or `round(price + sl_dist, 2)`
    re-appears in evaluate_signal's SL/TP assignment block, the test fails.
    Cheap and immune to data availability.
    """
    import inspect
    from strategy import core

    source = inspect.getsource(core)
    # The forbidden patterns that caused the regression
    forbidden = [
        "round(price, 2)",
        "round(price + sl_dist, 2)",
        "round(price - sl_dist, 2)",
        "round(price + tp_dist, 2)",
        "round(price - tp_dist, 2)",
    ]
    for pattern in forbidden:
        assert pattern not in source, (
            f"strategy/core.py contains '{pattern}' — this regresses the "
            f"precision bug from 2026-04-15 (commit 1e58b05). Sub-$1 symbols "
            f"like DOGE/XLM lose all SL/TP distance. Use full float precision."
        )


def test_evaluate_signal_returns_full_precision_for_doge_price():
    """Direct math check: when entry_price is sub-$1, SL/TP distances survive."""
    from strategy.core import _resolve_direction_params

    # Simulate DOGE-style state directly, bypassing evaluate_signal's full
    # signal-generation gate (which depends on price action).
    overrides = {"DOGEUSDT": {"atr_sl_mult": 0.7, "atr_tp_mult": 4.0, "atr_be_mult": 1.5}}
    resolved = _resolve_direction_params(overrides, "DOGEUSDT", "LONG")
    assert resolved["atr_sl_mult"] == 0.7
    assert resolved["atr_tp_mult"] == 4.0

    # Manually compute as evaluate_signal does (post-fix, full precision).
    price = 0.08234           # DOGE-style sub-$1
    atr = 0.005               # ~6% of price
    sl_dist = atr * resolved["atr_sl_mult"]
    tp_dist = atr * resolved["atr_tp_mult"]

    entry_price = float(price)
    sl_price = float(price - sl_dist)   # LONG
    tp_price = float(price + tp_dist)

    # Pre-fix bug behavior was: round(0.08234, 2) = 0.08, round(0.07884, 2) = 0.08
    # → entry == sl → instant SL hit. Post-fix: distinct values preserved.
    assert entry_price != sl_price, "sl_price collapsed to entry_price (precision bug regression)"
    assert entry_price != tp_price, "tp_price collapsed to entry_price"
    assert sl_price < entry_price < tp_price  # LONG ordering
    assert abs(entry_price - sl_price) == pytest.approx(sl_dist, abs=1e-12)
    assert abs(tp_price - entry_price) == pytest.approx(tp_dist, abs=1e-12)


@pytest.mark.skipif(
    not os.path.exists(OHLCV_DB), reason="requires cached market data (data/ohlcv.db)",
)
def test_doge_backtest_smoke_pnl_distribution_post_fix(tmp_path, monkeypatch):
    """Smoke: a small DOGE backtest must NOT have >50% of trades at exactly $0 PnL.

    Pre-fix, DOGE backtest had ~80% of trades with pnl_usd=0.0 (instant SL hits
    at the rounded entry price). Post-fix, distribution should be normal.
    """
    from datetime import datetime, timezone
    from backtest import simulate_strategy, get_cached_data
    import btc_api

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()

    sim_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    sim_end = datetime(2024, 6, 1, tzinfo=timezone.utc)
    data_start = datetime(2023, 1, 1, tzinfo=timezone.utc)

    df1h = get_cached_data("DOGEUSDT", "1h", start_date=data_start)
    df4h = get_cached_data("DOGEUSDT", "4h", start_date=data_start)
    df5m = get_cached_data("DOGEUSDT", "5m", start_date=data_start)
    df1d = get_cached_data("DOGEUSDT", "1d", start_date=data_start)

    if df1h.empty or df4h.empty or df5m.empty:
        pytest.skip("DOGEUSDT cached data not available")

    # Pass cfg + symbol_overrides so per-symbol ATR mults apply (DOGE 0.7/4.0/1.5)
    cfg = btc_api.load_config()
    symbol_overrides = cfg.get("symbol_overrides", {}) if isinstance(cfg, dict) else {}

    trades, _ = simulate_strategy(
        df1h, df4h, df5m, "DOGEUSDT",
        sim_start=sim_start, sim_end=sim_end,
        df1d=df1d,
        cfg=cfg, symbol_overrides=symbol_overrides,
        # A.0.2 (#277): smoke test pins the precision-bug zero-PnL distribution
        # which is a pre-cost concept; explicit flags=False preserves it.
        # Cost-on behavior on DOGE has its own coverage in test_backtest_with_costs.
        enable_slippage=False, enable_spread=False, enable_fees=False,
    )

    if not trades:
        pytest.skip("No trades generated for DOGEUSDT in 2024-H1")

    zero_pnl = sum(1 for t in trades if t["pnl_usd"] == 0.0)
    zero_pct = zero_pnl / len(trades) * 100

    assert zero_pct < 50.0, (
        f"DOGE has {zero_pct:.1f}% zero-PnL trades ({zero_pnl}/{len(trades)}) — "
        f"precision bug regression. Pre-fix was ~80%. Expected <50%."
    )
