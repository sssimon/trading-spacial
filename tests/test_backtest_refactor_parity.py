"""Verify simulate_strategy output matches pre-refactor for apply_kill_switch=False (#186 A6).

Pre-refactor baseline was captured with:
    symbol=BTCUSDT, sim_start=2024-01-01, sim_end=2024-03-01
    len(trades) == 24
    equity[-1]["equity"] == 11021.66  (rel tol 1e-4)
    sum(pnl_usd for t in trades) == 1021.66

These values are pinned here so any future refactor that inadvertently changes
trade-level behavior on the `apply_kill_switch=False` path will fail this test.
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
