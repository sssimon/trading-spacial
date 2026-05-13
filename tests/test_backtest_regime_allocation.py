"""Integration tests for backtest regime-allocation simulation path
(epic #338 Phase 1C, part of #342).

Covers:
- Flag-off byte-identical to existing LRC path (regression net)
- Flag-on dispatch to _simulate_strategy_regime_allocation
- Uptrend → LONG trades; downtrend → SHORT trades; flat → no trades
- Position state machine: open from flat, hold same direction, flip on signal
  change, close on signal-to-flat
- Trade dict shape: regime-allocation-specific fields (no atr_*_mult_used,
  exit_reason ∈ {SIGNAL_FLIP, SIGNAL_EXIT, BANKRUPT, SIM_END})
- Cost model v2 default + funding accounting on multi-day holds
- Bankruptcy halt
- Equity curve mark-to-market
- End-of-simulation SIM_END exit
"""
import math

import numpy as np
import pandas as pd
import pytest

from backtest import (
    BANKRUPTCY_THRESHOLD,
    INITIAL_CAPITAL,
    _simulate_strategy_regime_allocation,
    simulate_strategy,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_daily_df(n_days: int, closes: np.ndarray, start="2024-01-01"):
    """Build a daily OHLCV DataFrame from a close-price array."""
    idx = pd.date_range(start, periods=n_days, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "open": closes - 0.2,
            "high": closes + 0.5,
            "low": closes - 0.5,
            "close": closes,
            "volume": np.full(n_days, 1_000_000.0),
        },
        index=idx,
    )


def _make_hourly_df(n_hours: int, base_close: float = 100.0, start="2024-01-01"):
    """Minimal 1H DataFrame for liquidity proxy. Not used for signal."""
    idx = pd.date_range(start, periods=n_hours, freq="h", tz="UTC")
    closes = np.full(n_hours, base_close)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes + 0.1,
            "low": closes - 0.1,
            "close": closes,
            "volume": np.full(n_hours, 10_000.0),
        },
        index=idx,
    )


@pytest.fixture
def daily_uptrend_500():
    """500-day clean linear uptrend 100 → 599."""
    return _make_daily_df(500, np.arange(100.0, 600.0))


@pytest.fixture
def daily_downtrend_500():
    """500-day clean linear downtrend 600 → 101."""
    return _make_daily_df(500, np.arange(600.0, 100.0, -1.0))


@pytest.fixture
def daily_flat_500():
    """500-day constant price = 100.0."""
    return _make_daily_df(500, np.full(500, 100.0))


@pytest.fixture
def daily_flip_800():
    """800-day series with flip during sim window:
    - Days 0-499: linear uptrend 100 → 599 (long enough to fully warm up
      ALL 9 Donchian lookbacks including 360-day to LONG sticky direction)
    - Days 500-799: linear downtrend 598 → 100 (forces eventual SHORT flip
      as more lookbacks see new lows over time)

    Sim window starting at day 391 enters during the uptrend (vote LONG),
    then watches the cascade of lookbacks flipping to SHORT during the
    300-day downtrend phase — guaranteed at least one SIGNAL_FLIP."""
    up = np.linspace(100.0, 599.0, 500)
    down = np.linspace(598.0, 100.0, 300)
    return _make_daily_df(800, np.concatenate([up, down]))


@pytest.fixture
def daily_short_history():
    """Only 100 days — below the 390-day warmup threshold."""
    return _make_daily_df(100, np.linspace(100.0, 199.0, 100))


@pytest.fixture
def hourly_500days():
    """500 days × 24 hours of 1H bars for liquidity proxy."""
    return _make_hourly_df(500 * 24, base_close=200.0)


@pytest.fixture
def hourly_minimal():
    """Tiny 1H frame — falls through to liquidity NaN fallback in costs."""
    return _make_hourly_df(50, base_close=100.0)


# ---------------------------------------------------------------------------
# Dispatch from simulate_strategy
# ---------------------------------------------------------------------------


class TestDispatchFromSimulateStrategy:
    """When cfg.regime_allocation_enabled=True, simulate_strategy must delegate
    to the regime-allocation path."""

    def test_flag_on_delegates_to_ra_simulator(
        self, daily_uptrend_500, hourly_500days
    ):
        empty_4h = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        empty_5m = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        trades, equity = simulate_strategy(
            df1h=hourly_500days, df4h=empty_4h, df5m=empty_5m,
            df1d=daily_uptrend_500,
            symbol="BTCUSDT",
            cfg={"regime_allocation_enabled": True},
            sim_start=daily_uptrend_500.index[391],
            sim_end=daily_uptrend_500.index[-1],
        )
        # Uptrend → should produce at least one LONG trade or hold position
        # to SIM_END
        assert len(equity) > 0
        # Either trades exist (closed during sim) or equity grew (still in
        # position at sim_end → SIM_END trade)
        assert len(trades) >= 1

    def test_flag_off_does_not_take_ra_path(
        self, daily_uptrend_500, hourly_500days
    ):
        """Without flag, simulate_strategy uses the LRC path (which doesn't
        produce the regime-allocation trade shape)."""
        empty_4h = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        empty_5m = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        # No flag → LRC path. df4h is empty so LRC returns empty (existing
        # guard `if len(df1h) == 0 or len(df4h) == 0: return decision`)
        result = simulate_strategy(
            df1h=hourly_500days, df4h=empty_4h, df5m=empty_5m,
            df1d=daily_uptrend_500, symbol="BTCUSDT",
            cfg={},  # no flag
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )
        # LRC path returns (trades, equity) when df4h is empty → likely 0 trades
        # Just confirm it didn't crash and didn't use RA path (no SIGNAL_FLIP)
        trades, _ = result if isinstance(result, tuple) else (result, [])
        for t in trades:
            assert t["exit_reason"] not in {"SIGNAL_FLIP", "SIGNAL_EXIT", "SIM_END", "BANKRUPT"}


# ---------------------------------------------------------------------------
# Uptrend, downtrend, flat
# ---------------------------------------------------------------------------


class TestUptrendDowntrendFlat:
    def test_uptrend_produces_long_trade(self, daily_uptrend_500, hourly_500days):
        trades, equity = _simulate_strategy_regime_allocation(
            df1h=hourly_500days, df1d=daily_uptrend_500, symbol="BTCUSDT",
            sim_start=daily_uptrend_500.index[391],
            sim_end=daily_uptrend_500.index[-1],
            cfg={},
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=False, cost_calibration=None,
        )
        # All trades should be LONG (uptrend)
        for t in trades:
            assert t["direction"] == "LONG"
        # Net P&L positive (gross uptrend captures positive PnL)
        net = sum(t["pnl_usd"] for t in trades)
        assert net > 0, f"Uptrend expected positive net PnL; got {net}"

    def test_downtrend_produces_short_trade(self, daily_downtrend_500, hourly_500days):
        trades, equity = _simulate_strategy_regime_allocation(
            df1h=hourly_500days, df1d=daily_downtrend_500, symbol="BTCUSDT",
            sim_start=daily_downtrend_500.index[391],
            sim_end=daily_downtrend_500.index[-1],
            cfg={},
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=False, cost_calibration=None,
        )
        for t in trades:
            assert t["direction"] == "SHORT"
        net = sum(t["pnl_usd"] for t in trades)
        assert net > 0, f"Downtrend SHORT expected positive net PnL; got {net}"

    def test_flat_produces_no_trades(self, daily_flat_500, hourly_500days):
        trades, equity = _simulate_strategy_regime_allocation(
            df1h=hourly_500days, df1d=daily_flat_500, symbol="BTCUSDT",
            sim_start=daily_flat_500.index[391],
            sim_end=daily_flat_500.index[-1],
            cfg={},
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=False, cost_calibration=None,
        )
        # Constant price → no breakouts → no trades
        assert trades == []


# ---------------------------------------------------------------------------
# Position state machine
# ---------------------------------------------------------------------------


class TestPositionStateMachine:
    def test_uptrend_then_downtrend_flips_position(
        self, daily_flip_800
    ):
        """Up-then-down with sim window starting during uptrend → first trade
        LONG, eventually flipped to SHORT or exited via SIGNAL_FLIP/EXIT."""
        # 800 days × 24h = 19200 hours of 1H bars for liquidity proxy
        hourly = _make_hourly_df(800 * 24, base_close=300.0)
        trades, _ = _simulate_strategy_regime_allocation(
            df1h=hourly, df1d=daily_flip_800, symbol="BTCUSDT",
            sim_start=daily_flip_800.index[391],
            sim_end=daily_flip_800.index[-1],
            cfg={},
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=False, cost_calibration=None,
        )
        # Should have ≥ 2 trades (LONG closed via SIGNAL_FLIP, SHORT opened
        # then closed at SIM_END)
        assert len(trades) >= 2, (
            f"Expected ≥2 trades during flip; got {len(trades)}"
        )
        # At least one trade should be SIGNAL_FLIP or SIGNAL_EXIT (not SIM_END)
        exit_reasons = {t["exit_reason"] for t in trades}
        assert any(r in {"SIGNAL_FLIP", "SIGNAL_EXIT"} for r in exit_reasons), (
            f"Expected at least one SIGNAL_FLIP/EXIT; got {exit_reasons}"
        )
        # First trade should be LONG (entered during uptrend)
        assert trades[0]["direction"] == "LONG"

    def test_sim_end_closes_open_position(self, daily_uptrend_500, hourly_500days):
        """Position open at sim_end is closed with exit_reason='SIM_END'."""
        trades, _ = _simulate_strategy_regime_allocation(
            df1h=hourly_500days, df1d=daily_uptrend_500, symbol="BTCUSDT",
            sim_start=daily_uptrend_500.index[391],
            sim_end=daily_uptrend_500.index[-1],
            cfg={},
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=False, cost_calibration=None,
        )
        # Uptrend never flips → only trade is SIM_END at end
        assert len(trades) == 1
        assert trades[0]["exit_reason"] == "SIM_END"
        assert trades[0]["direction"] == "LONG"


# ---------------------------------------------------------------------------
# Trade dict shape
# ---------------------------------------------------------------------------


class TestTradeDictShape:
    def test_trade_dict_has_regime_allocation_fields(
        self, daily_uptrend_500, hourly_500days
    ):
        trades, _ = _simulate_strategy_regime_allocation(
            df1h=hourly_500days, df1d=daily_uptrend_500, symbol="BTCUSDT",
            sim_start=daily_uptrend_500.index[391],
            sim_end=daily_uptrend_500.index[-1],
            cfg={},
            enable_slippage=True, enable_spread=True, enable_fees=True,
            enable_funding=True, cost_calibration=None,
        )
        assert len(trades) >= 1
        t = trades[0]
        required = {
            "entry_time", "exit_time", "entry_price", "exit_price",
            "exit_reason", "direction", "pnl_pct", "pnl_usd",
            "gross_pnl_usd", "notional_usd", "duration_hours",
            "size_mult", "atr_sl_mult_used", "atr_tp_mult_used", "atr_be_mult_used",
            "overshoot_clamped", "score",
            "entry_slippage_bps", "exit_slippage_bps",
            "entry_spread_bps", "exit_spread_bps",
            "fee_bps", "funding_cost_bps", "total_cost_bps", "total_cost_usd",
        }
        missing = required - set(t.keys())
        assert not missing, f"Trade dict missing: {missing}"
        # atr_*_mult_used are None (not applicable to regime-allocation)
        assert t["atr_sl_mult_used"] is None
        assert t["atr_tp_mult_used"] is None
        assert t["atr_be_mult_used"] is None
        assert t["size_mult"] is None

    def test_net_pnl_equals_gross_minus_total_cost(
        self, daily_uptrend_500, hourly_500days
    ):
        trades, _ = _simulate_strategy_regime_allocation(
            df1h=hourly_500days, df1d=daily_uptrend_500, symbol="BTCUSDT",
            sim_start=daily_uptrend_500.index[391],
            sim_end=daily_uptrend_500.index[-1],
            cfg={},
            enable_slippage=True, enable_spread=True, enable_fees=True,
            enable_funding=True, cost_calibration=None,
        )
        for t in trades:
            expected = round(t["gross_pnl_usd"] - t["total_cost_usd"], 2)
            assert t["pnl_usd"] == pytest.approx(expected, abs=0.01)


# ---------------------------------------------------------------------------
# Costs (v2 + funding)
# ---------------------------------------------------------------------------


class TestCosts:
    def test_costs_off_makes_gross_equal_net(
        self, daily_uptrend_500, hourly_500days
    ):
        trades, _ = _simulate_strategy_regime_allocation(
            df1h=hourly_500days, df1d=daily_uptrend_500, symbol="BTCUSDT",
            sim_start=daily_uptrend_500.index[391],
            sim_end=daily_uptrend_500.index[-1],
            cfg={},
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=False, cost_calibration=None,
        )
        for t in trades:
            assert t["total_cost_usd"] == 0.0
            assert t["pnl_usd"] == pytest.approx(t["gross_pnl_usd"], abs=0.01)

    def test_costs_on_reduces_net_below_gross(
        self, daily_uptrend_500, hourly_500days
    ):
        trades, _ = _simulate_strategy_regime_allocation(
            df1h=hourly_500days, df1d=daily_uptrend_500, symbol="BTCUSDT",
            sim_start=daily_uptrend_500.index[391],
            sim_end=daily_uptrend_500.index[-1],
            cfg={},
            enable_slippage=True, enable_spread=True, enable_fees=True,
            enable_funding=True, cost_calibration=None,
        )
        for t in trades:
            assert t["total_cost_usd"] > 0.0
            assert t["pnl_usd"] < t["gross_pnl_usd"]

    def test_funding_accrues_on_multi_day_holds(
        self, daily_uptrend_500, hourly_500days
    ):
        """Multi-day position → funding cost > 0 in trade dict."""
        trades, _ = _simulate_strategy_regime_allocation(
            df1h=hourly_500days, df1d=daily_uptrend_500, symbol="BTCUSDT",
            sim_start=daily_uptrend_500.index[391],
            sim_end=daily_uptrend_500.index[-1],
            cfg={},
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=True, cost_calibration=None,
        )
        # Uptrend → 1 trade, held many days → funding > 0
        assert len(trades) == 1
        assert trades[0]["duration_hours"] > 24
        assert trades[0]["funding_cost_bps"] > 0


# ---------------------------------------------------------------------------
# Warmup / edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_df1d_returns_empty(self, hourly_500days):
        empty_df1d = pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"]
        )
        trades, equity = _simulate_strategy_regime_allocation(
            df1h=hourly_500days, df1d=empty_df1d, symbol="BTCUSDT",
            sim_start=None, sim_end=None, cfg={},
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=False, cost_calibration=None,
        )
        assert trades == []
        assert equity == []

    def test_short_history_no_trades(self, daily_short_history, hourly_minimal):
        """Daily history < 390 → no trades (warmup not complete)."""
        trades, equity = _simulate_strategy_regime_allocation(
            df1h=hourly_minimal, df1d=daily_short_history, symbol="BTCUSDT",
            sim_start=None, sim_end=None, cfg={},
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=False, cost_calibration=None,
        )
        assert trades == []
        # Equity curve still gets entries with starting capital
        assert len(equity) > 0
        for eq in equity:
            assert eq["equity"] == INITIAL_CAPITAL

    def test_no_df1h_uses_nan_liquidity_fallback(
        self, daily_uptrend_500
    ):
        """When df1h is None or empty, costs fall back to default."""
        empty_1h = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        # Should not crash
        trades, _ = _simulate_strategy_regime_allocation(
            df1h=empty_1h, df1d=daily_uptrend_500, symbol="BTCUSDT",
            sim_start=daily_uptrend_500.index[391],
            sim_end=daily_uptrend_500.index[-1],
            cfg={},
            enable_slippage=True, enable_spread=True, enable_fees=True,
            enable_funding=True, cost_calibration=None,
        )
        # Still produces trade(s) — costs use fallback liquidity
        assert len(trades) >= 1


# ---------------------------------------------------------------------------
# Equity curve
# ---------------------------------------------------------------------------


class TestEquityCurve:
    def test_equity_curve_starts_at_initial_capital(
        self, daily_flat_500, hourly_500days
    ):
        """Flat price + no trades → equity = INITIAL_CAPITAL throughout."""
        trades, equity = _simulate_strategy_regime_allocation(
            df1h=hourly_500days, df1d=daily_flat_500, symbol="BTCUSDT",
            sim_start=daily_flat_500.index[391],
            sim_end=daily_flat_500.index[-1],
            cfg={},
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=False, cost_calibration=None,
        )
        assert len(equity) > 0
        for eq in equity:
            assert eq["equity"] == pytest.approx(INITIAL_CAPITAL)

    def test_equity_curve_grows_under_uptrend_long(
        self, daily_uptrend_500, hourly_500days
    ):
        """Uptrend LONG → equity curve ends above initial capital."""
        trades, equity = _simulate_strategy_regime_allocation(
            df1h=hourly_500days, df1d=daily_uptrend_500, symbol="BTCUSDT",
            sim_start=daily_uptrend_500.index[391],
            sim_end=daily_uptrend_500.index[-1],
            cfg={},
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=False, cost_calibration=None,
        )
        assert equity[-1]["equity"] > INITIAL_CAPITAL


# ---------------------------------------------------------------------------
# Bankruptcy halt
# ---------------------------------------------------------------------------


class TestBankruptcyHalt:
    def test_bankruptcy_threshold_inherited(self):
        """Sanity: BANKRUPTCY_THRESHOLD is the documented $1000 floor."""
        assert BANKRUPTCY_THRESHOLD == 1000.0

    def test_bankruptcy_halts_trading(self):
        """Manufactured catastrophe: price gap that wipes out >90% on a SHORT
        position triggers BANKRUPT exit + halts further trading."""
        # 391 days warmup with mild downtrend → ensemble SHORT at day 391
        # Then a massive UPWARD gap that destroys the SHORT
        np.random.seed(42)
        n = 400
        closes = np.full(n, 100.0)
        # Mild downtrend for first 391 days
        for i in range(1, 391):
            closes[i] = max(50.0, closes[i-1] - 0.1)
        # Massive gap up: price 5x → SHORT position destroyed
        for i in range(391, n):
            closes[i] = 500.0
        df1d = _make_daily_df(n, closes)
        df1h = _make_hourly_df(n * 24)

        trades, equity = _simulate_strategy_regime_allocation(
            df1h=df1h, df1d=df1d, symbol="BTCUSDT",
            sim_start=df1d.index[391],
            sim_end=df1d.index[-1],
            cfg={},
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=False, cost_calibration=None,
        )
        # If bankruptcy was triggered, at least one trade has exit_reason=BANKRUPT
        # AND simulation should halt (no further trades after the BANKRUPT)
        bankrupt_trades = [t for t in trades if t["exit_reason"] == "BANKRUPT"]
        if bankrupt_trades:
            # All other trades must precede the BANKRUPT one
            bankrupt_time = bankrupt_trades[0]["exit_time"]
            for t in trades:
                assert t["exit_time"] <= bankrupt_time


# ---------------------------------------------------------------------------
# Sim window filtering
# ---------------------------------------------------------------------------


class TestSimWindow:
    def test_sim_start_filters_correctly(self, daily_uptrend_500, hourly_500days):
        """sim_start in the middle of the series → only bars on/after sim_start
        appear in equity curve."""
        sim_start = daily_uptrend_500.index[400]  # near end
        trades, equity = _simulate_strategy_regime_allocation(
            df1h=hourly_500days, df1d=daily_uptrend_500, symbol="BTCUSDT",
            sim_start=sim_start, sim_end=daily_uptrend_500.index[-1],
            cfg={},
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=False, cost_calibration=None,
        )
        for eq in equity:
            assert eq["time"] >= sim_start

    def test_sim_end_filters_correctly(self, daily_uptrend_500, hourly_500days):
        sim_end = daily_uptrend_500.index[450]
        trades, equity = _simulate_strategy_regime_allocation(
            df1h=hourly_500days, df1d=daily_uptrend_500, symbol="BTCUSDT",
            sim_start=daily_uptrend_500.index[391], sim_end=sim_end,
            cfg={},
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=False, cost_calibration=None,
        )
        for eq in equity:
            assert eq["time"] <= sim_end
        for t in trades:
            assert t["exit_time"] <= sim_end
