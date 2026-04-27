"""Verify simulate_strategy output matches pinned baselines on multiple symbols.

ORIGINAL pin (#186 A6 refactor — Apr 2024):
    symbol=BTCUSDT, sim_start=2024-01-01, sim_end=2024-03-01
    len(trades) == 24, final equity 11021.66, net PnL 1021.66

EXTENDED pins (post-fix/precision-rounding-bug — Apr 2026):
    DOGE/XLM 2024-H1 — sub-$1 symbols where the precision bug
    (round(price, 2) + abs(entry-sl_orig)) silently broke for 12 days
    while the BTC-only parity test passed throughout.

These values are pinned so any future refactor that inadvertently changes
trade-level behavior — especially for sub-$1 symbols — will fail.
"""
import os
from datetime import datetime, timezone

import pytest


OHLCV_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ohlcv.db",
)


@pytest.mark.skipif(
    not os.path.exists(OHLCV_DB), reason="requires cached market data (data/ohlcv.db)",
)
def test_simulate_strategy_parity_without_kill_switch(tmp_path, monkeypatch):
    """With apply_kill_switch=False, refactored simulate_strategy produces the same
    trade count + final equity + net PnL as the pre-refactor baseline on a pinned
    window (BTCUSDT, 2024-01-01 → 2024-03-01).

    This is the *critical* parity test for Epic #186 A6: any drift means the
    rewire changed trade-level semantics on the default path, which was a
    non-negotiable constraint of the refactor.
    """
    from backtest import simulate_strategy, get_cached_data
    import btc_api

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()

    symbol = "BTCUSDT"
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 3, 1, tzinfo=timezone.utc)
    data_start = datetime(2023, 1, 1, tzinfo=timezone.utc)

    df1h = get_cached_data(symbol, "1h", start_date=data_start)
    df4h = get_cached_data(symbol, "4h", start_date=data_start)
    df5m = get_cached_data(symbol, "5m", start_date=data_start)
    df1d = get_cached_data(symbol, "1d", start_date=data_start)

    if df1h.empty or df4h.empty or df5m.empty:
        pytest.skip("BTCUSDT market data not cached in data/ohlcv.db")

    trades, equity = simulate_strategy(
        df1h, df4h, df5m, symbol,
        sim_start=start, sim_end=end,
        df1d=df1d,
    )

    # Pinned from a pre-refactor capture (committed at the start of Task 6).
    # If you're touching this test because the values changed, you've likely
    # broken trade-level parity — re-read the Task 6 constraints before updating.
    EXPECTED_TRADE_COUNT = 24
    EXPECTED_FINAL_EQUITY = 11021.66
    EXPECTED_NET_PNL = 1021.66

    assert len(trades) == EXPECTED_TRADE_COUNT, (
        f"trade count drift: {len(trades)} vs expected {EXPECTED_TRADE_COUNT}"
    )
    assert equity[-1]["equity"] == pytest.approx(EXPECTED_FINAL_EQUITY, rel=1e-4)
    net_pnl = round(sum(t["pnl_usd"] for t in trades), 2)
    assert net_pnl == pytest.approx(EXPECTED_NET_PNL, abs=0.01)


@pytest.mark.skipif(
    not os.path.exists(OHLCV_DB), reason="requires cached market data (data/ohlcv.db)",
)
def test_simulate_strategy_with_simulator_wires_correctly(tmp_path, monkeypatch):
    """With apply_kill_switch=True + shared_simulator, closed trades feed the
    simulator and the tier can evolve mid-backtest.

    This test proves the simulator is WIRED, not a specific pnl number. The
    actual tier depends on config + symbol performance in the window.
    """
    from backtest import simulate_strategy, get_cached_data
    from backtest_kill_switch import KillSwitchSimulator
    import btc_api

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()

    symbol = "BTCUSDT"
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 3, 1, tzinfo=timezone.utc)
    data_start = datetime(2023, 1, 1, tzinfo=timezone.utc)

    df1h = get_cached_data(symbol, "1h", start_date=data_start)
    df4h = get_cached_data(symbol, "4h", start_date=data_start)
    df5m = get_cached_data(symbol, "5m", start_date=data_start)
    df1d = get_cached_data(symbol, "1d", start_date=data_start)

    if df1h.empty or df4h.empty or df5m.empty:
        pytest.skip("BTCUSDT market data not cached in data/ohlcv.db")

    cfg_ks = {
        "kill_switch": {
            "enabled": True,
            "min_trades_for_eval": 3,
            "alert_win_rate_threshold": 0.30,
            "reduce_size_factor": 0.5,
            "pause_months_consecutive": 2,
            "auto_recovery_enabled": True,
        },
    }
    sim = KillSwitchSimulator(cfg_ks)

    trades, _equity = simulate_strategy(
        df1h, df4h, df5m, symbol,
        sim_start=start, sim_end=end,
        df1d=df1d,
        apply_kill_switch=True,
        shared_simulator=sim,
        cfg=cfg_ks,
    )

    # Some trades should have happened on this symbol+window, and each one
    # should have been fed to the simulator.
    assert len(trades) > 0
    assert symbol in sim.states
    assert len(sim.states[symbol].closed_trades) == len(trades)
    # Final tier must be one of the valid states.
    assert sim.get_tier(symbol) in ("NORMAL", "ALERT", "REDUCED", "PAUSED")


# ─────────────────────────────────────────────────────────────────────────────
# Sub-$1 symbol parity (post-fix/precision-rounding-bug — Apr 2026)
#
# These extend the BTC-only baseline above to cover symbols where the
# `round(price, 2)` + `abs(entry - sl_orig)` bugs silently broke trade
# generation for 12 days. With cfg+symbol_overrides passed and the precision
# fixes in place, these are the values produced today.
#
# If a future change drifts these numbers, you've likely re-introduced the
# precision regression OR a different bug specific to sub-$1 price scale.
# ─────────────────────────────────────────────────────────────────────────────

import btc_api as _btc_api  # noqa: E402  — import is here for clarity, not at top


def _no_phantom_profits(trades: list[dict]) -> tuple[int, int]:
    """Count physically-impossible trades: SL exits with positive PnL.

    LONG hitting SL means price went DOWN below entry → PnL must be ≤ 0.
    SHORT hitting SL means price went UP above entry → PnL must be ≤ 0.
    Any trade violating this is a phantom from inverted SL.
    """
    phantom_long = sum(
        1 for t in trades
        if t["direction"] == "LONG" and t["exit_reason"] == "SL" and t["pnl_usd"] > 0
    )
    phantom_short = sum(
        1 for t in trades
        if t["direction"] == "SHORT" and t["exit_reason"] == "SL" and t["pnl_usd"] > 0
    )
    return phantom_long, phantom_short


@pytest.mark.skipif(
    not os.path.exists(OHLCV_DB), reason="requires cached market data (data/ohlcv.db)",
)
def test_simulate_strategy_parity_doge_2024_h1(tmp_path, monkeypatch):
    """DOGE 2024-01-01 → 2024-06-01 pinned post-fix.

    DOGE was where the precision bug hit hardest: pre-fix WR ~1.6% with
    most trades at $0 PnL. Post-fix should produce a stable distribution
    with NO phantom (SL exits with positive PnL are physically impossible).
    """
    from backtest import simulate_strategy, get_cached_data

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(_btc_api, "DB_FILE", db_path)
    _btc_api.init_db()

    symbol = "DOGEUSDT"
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 6, 1, tzinfo=timezone.utc)
    data_start = datetime(2023, 1, 1, tzinfo=timezone.utc)

    df1h = get_cached_data(symbol, "1h", start_date=data_start)
    df4h = get_cached_data(symbol, "4h", start_date=data_start)
    df5m = get_cached_data(symbol, "5m", start_date=data_start)
    df1d = get_cached_data(symbol, "1d", start_date=data_start)
    if df1h.empty or df4h.empty or df5m.empty:
        pytest.skip("DOGEUSDT market data not cached")

    cfg = _btc_api.load_config()
    symbol_overrides = cfg.get("symbol_overrides", {}) if isinstance(cfg, dict) else {}

    trades, equity = simulate_strategy(
        df1h, df4h, df5m, symbol,
        sim_start=start, sim_end=end,
        df1d=df1d,
        cfg=cfg, symbol_overrides=symbol_overrides,
    )

    # Pinned 2026-04-27 with main commit 664e85a (precision fix merged).
    EXPECTED_TRADE_COUNT = 52
    EXPECTED_FINAL_EQUITY = 9711.23
    EXPECTED_NET_PNL = -288.77

    assert len(trades) == EXPECTED_TRADE_COUNT, (
        f"DOGE trade count drift: {len(trades)} vs expected {EXPECTED_TRADE_COUNT}"
    )
    assert equity[-1]["equity"] == pytest.approx(EXPECTED_FINAL_EQUITY, rel=1e-4)
    net_pnl = round(sum(t["pnl_usd"] for t in trades), 2)
    assert net_pnl == pytest.approx(EXPECTED_NET_PNL, abs=0.01)

    # Anti-phantom: no SL exits with positive PnL allowed.
    phantom_long, phantom_short = _no_phantom_profits(trades)
    assert phantom_long == 0, (
        f"DOGE has {phantom_long} LONG SL trades with positive PnL — "
        f"phantom profit regression. Inverted SL bug returned."
    )
    assert phantom_short == 0, (
        f"DOGE has {phantom_short} SHORT SL trades with positive PnL — "
        f"phantom profit regression."
    )


@pytest.mark.skipif(
    not os.path.exists(OHLCV_DB), reason="requires cached market data (data/ohlcv.db)",
)
def test_simulate_strategy_parity_xlm_2024_h1(tmp_path, monkeypatch):
    """XLM 2024-01-01 → 2024-06-01 pinned post-fix.

    XLM was the most extreme phantom case (206% phantom in Apr 18 docs).
    Post-fix it produces real strategy results with NO phantom trades.
    """
    from backtest import simulate_strategy, get_cached_data

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(_btc_api, "DB_FILE", db_path)
    _btc_api.init_db()

    symbol = "XLMUSDT"
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 6, 1, tzinfo=timezone.utc)
    data_start = datetime(2023, 1, 1, tzinfo=timezone.utc)

    df1h = get_cached_data(symbol, "1h", start_date=data_start)
    df4h = get_cached_data(symbol, "4h", start_date=data_start)
    df5m = get_cached_data(symbol, "5m", start_date=data_start)
    df1d = get_cached_data(symbol, "1d", start_date=data_start)
    if df1h.empty or df4h.empty or df5m.empty:
        pytest.skip("XLMUSDT market data not cached")

    cfg = _btc_api.load_config()
    symbol_overrides = cfg.get("symbol_overrides", {}) if isinstance(cfg, dict) else {}

    trades, equity = simulate_strategy(
        df1h, df4h, df5m, symbol,
        sim_start=start, sim_end=end,
        df1d=df1d,
        cfg=cfg, symbol_overrides=symbol_overrides,
    )

    # Pinned 2026-04-27 with main commit 664e85a (precision fix merged).
    EXPECTED_TRADE_COUNT = 37
    EXPECTED_FINAL_EQUITY = 14995.23
    EXPECTED_NET_PNL = 4995.23

    assert len(trades) == EXPECTED_TRADE_COUNT, (
        f"XLM trade count drift: {len(trades)} vs expected {EXPECTED_TRADE_COUNT}"
    )
    assert equity[-1]["equity"] == pytest.approx(EXPECTED_FINAL_EQUITY, rel=1e-4)
    net_pnl = round(sum(t["pnl_usd"] for t in trades), 2)
    assert net_pnl == pytest.approx(EXPECTED_NET_PNL, abs=0.01)

    phantom_long, phantom_short = _no_phantom_profits(trades)
    assert phantom_long == 0, f"XLM has {phantom_long} phantom LONG SL profits"
    assert phantom_short == 0, f"XLM has {phantom_short} phantom SHORT SL profits"
