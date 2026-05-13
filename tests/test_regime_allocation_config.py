"""Config integration tests for regime-allocation (epic #338 Phase 1D, part of #342).

Verifies that:
- `config.defaults.json` contains the `regime_allocation` block with the
  expected schema and locked default values per §8 of epic spec
- Dispatch in `evaluate_signal` and `simulate_strategy` accepts BOTH the
  nested `cfg.regime_allocation.enabled` (production shape) AND the flat
  `cfg.regime_allocation_enabled` (testing convenience)
- Param resolution in `_simulate_strategy_regime_allocation` prefers nested
  values over top-level (so production config.defaults.json is the source of
  truth) but tolerates either shape
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


_DEFAULTS_PATH = (
    Path(__file__).resolve().parent.parent / "config.defaults.json"
)


# ---------------------------------------------------------------------------
# Schema: config.defaults.json has regime_allocation block with locked defaults
# ---------------------------------------------------------------------------


class TestConfigDefaultsSchema:
    def _load(self) -> dict:
        with _DEFAULTS_PATH.open() as f:
            return json.load(f)

    def test_regime_allocation_block_present(self):
        cfg = self._load()
        assert "regime_allocation" in cfg
        assert isinstance(cfg["regime_allocation"], dict)

    def test_enabled_defaults_to_false(self):
        """Production default is OFF — opt-in flag. Strategy class is not
        yet validated (Phase 2-6 pending)."""
        cfg = self._load()
        assert cfg["regime_allocation"]["enabled"] is False

    def test_portfolio_vol_target_locked_30pct(self):
        """§8.3 locked 30% annualized."""
        cfg = self._load()
        assert cfg["regime_allocation"]["portfolio_vol_target"] == 0.30

    def test_max_position_pct_locked_20pct(self):
        """§4.3 hard cap per-symbol = 20% of capital."""
        cfg = self._load()
        assert cfg["regime_allocation"]["max_position_pct"] == 0.20

    def test_min_position_usd_locked_50(self):
        """Binance perp min order = $50."""
        cfg = self._load()
        assert cfg["regime_allocation"]["min_position_usd"] == 50.0

    def test_max_leverage_locked_2x(self):
        """§8.6 leverage cap = 2x (sum |positions| ≤ 2 × capital)."""
        cfg = self._load()
        assert cfg["regime_allocation"]["max_leverage"] == 2.0


# ---------------------------------------------------------------------------
# Dispatch: nested cfg.regime_allocation.enabled enables the path
# ---------------------------------------------------------------------------


def _make_daily_df(n_days: int, closes: np.ndarray, start="2024-01-01"):
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


def _make_hourly_df(n_hours: int, start="2024-01-01"):
    idx = pd.date_range(start, periods=n_hours, freq="h", tz="UTC")
    closes = np.full(n_hours, 200.0)
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
def daily_uptrend():
    return _make_daily_df(500, np.arange(100.0, 600.0))


@pytest.fixture
def hourly_500days():
    return _make_hourly_df(500 * 24)


class TestNestedConfigDispatch:
    """evaluate_signal accepts nested `cfg.regime_allocation.enabled`."""

    def test_evaluate_signal_nested_true_takes_ra_path(self, daily_uptrend):
        from strategy.core import evaluate_signal

        decision = evaluate_signal(
            df1h=pd.DataFrame(columns=["open", "high", "low", "close", "volume"]),
            df4h=pd.DataFrame(columns=["open", "high", "low", "close", "volume"]),
            df5m=pd.DataFrame(columns=["open", "high", "low", "close", "volume"]),
            df1d=daily_uptrend,
            symbol="BTCUSDT",
            cfg={"regime_allocation": {"enabled": True}},
            regime={"regime": "BULL"},
        )
        # Confirm RA path took it (regime_allocation_vote present)
        assert "regime_allocation_vote" in decision.indicators
        assert decision.direction == "LONG"

    def test_evaluate_signal_nested_false_takes_lrc_path(self, daily_uptrend, hourly_500days):
        from strategy.core import evaluate_signal

        empty_4h = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        empty_5m = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        decision = evaluate_signal(
            df1h=hourly_500days,
            df4h=hourly_500days.iloc[::4].copy(),  # fake 4H from 1H
            df5m=empty_5m,
            df1d=daily_uptrend,
            symbol="BTCUSDT",
            cfg={"regime_allocation": {"enabled": False}},
            regime={"regime": "BYPASS"},
        )
        # LRC path → no regime_allocation_vote
        assert "regime_allocation_vote" not in decision.indicators
        assert "lrc_pct" in decision.indicators

    def test_simulate_strategy_nested_true_dispatches(self, daily_uptrend, hourly_500days):
        from backtest import simulate_strategy

        empty_4h = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        empty_5m = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        trades, equity = simulate_strategy(
            df1h=hourly_500days, df4h=empty_4h, df5m=empty_5m,
            df1d=daily_uptrend, symbol="BTCUSDT",
            sim_start=daily_uptrend.index[391],
            sim_end=daily_uptrend.index[-1],
            cfg={"regime_allocation": {"enabled": True}},
        )
        # RA path produces trades shape
        assert len(trades) >= 1
        for t in trades:
            # Regime-allocation exit reasons
            assert t["exit_reason"] in {"SIGNAL_FLIP", "SIGNAL_EXIT", "BANKRUPT", "SIM_END"}
            # No atr_*_mult_used (RA shape)
            assert t["atr_sl_mult_used"] is None

    def test_flat_flag_still_works_for_backward_compat(self, daily_uptrend, hourly_500days):
        """Test convenience: `cfg.regime_allocation_enabled` (flat) still works
        even though production config uses nested shape."""
        from backtest import simulate_strategy

        empty_4h = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        empty_5m = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        trades, equity = simulate_strategy(
            df1h=hourly_500days, df4h=empty_4h, df5m=empty_5m,
            df1d=daily_uptrend, symbol="BTCUSDT",
            sim_start=daily_uptrend.index[391],
            sim_end=daily_uptrend.index[-1],
            cfg={"regime_allocation_enabled": True},  # flat — backward compat
        )
        assert len(trades) >= 1


# ---------------------------------------------------------------------------
# Param resolution: nested params used when present
# ---------------------------------------------------------------------------


class TestParamResolution:
    """`_simulate_strategy_regime_allocation` reads from nested block when
    present, falls back to top-level keys otherwise."""

    def test_nested_min_position_usd_used(self, daily_uptrend, hourly_500days):
        """Passing nested min_position_usd changes when a trade fires.

        Uses min_position_usd's cliff behavior: when the computed notional
        falls below the threshold, the position is skipped entirely (size=0,
        no trade). Setting min very high → no trades; setting min low →
        trades fire. Cleaner cliff behavior than portfolio_vol_target which
        can interact with caps.
        """
        from backtest import simulate_strategy

        empty_4h = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        empty_5m = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        # min_position_usd ridiculously high → no trades fire (all positions
        # below threshold). Confirms the nested param is being read.
        trades_high_min, _ = simulate_strategy(
            df1h=hourly_500days, df4h=empty_4h, df5m=empty_5m,
            df1d=daily_uptrend, symbol="BTCUSDT",
            sim_start=daily_uptrend.index[391], sim_end=daily_uptrend.index[-1],
            cfg={
                "regime_allocation": {
                    "enabled": True,
                    "min_position_usd": 1_000_000.0,  # impossibly high
                }
            },
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=False,
        )
        # min_position_usd default (50) → trades fire normally
        trades_default_min, _ = simulate_strategy(
            df1h=hourly_500days, df4h=empty_4h, df5m=empty_5m,
            df1d=daily_uptrend, symbol="BTCUSDT",
            sim_start=daily_uptrend.index[391], sim_end=daily_uptrend.index[-1],
            cfg={"regime_allocation": {"enabled": True}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=False,
        )
        # High-min config → no trades fire (all below threshold)
        assert trades_high_min == [], (
            f"Expected zero trades with min_position_usd=$1M; got {len(trades_high_min)}"
        )
        # Default-min config → trades fire normally
        assert len(trades_default_min) >= 1

    def test_top_level_param_used_when_no_nested_block(self, daily_uptrend, hourly_500days):
        """Without nested `regime_allocation` block, top-level keys are read."""
        from backtest import simulate_strategy

        empty_4h = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        empty_5m = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        trades, _ = simulate_strategy(
            df1h=hourly_500days, df4h=empty_4h, df5m=empty_5m,
            df1d=daily_uptrend, symbol="BTCUSDT",
            sim_start=daily_uptrend.index[391], sim_end=daily_uptrend.index[-1],
            cfg={
                "regime_allocation_enabled": True,
                "portfolio_vol_target": 0.40,  # top-level value
            },
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=False,
        )
        assert trades  # should run successfully with top-level params

    def test_defaults_when_no_overrides(self, daily_uptrend, hourly_500days):
        """No nested, no top-level → falls back to module defaults
        (30% vol, 20% per-symbol cap, $50 min)."""
        from backtest import simulate_strategy

        empty_4h = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        empty_5m = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        trades, _ = simulate_strategy(
            df1h=hourly_500days, df4h=empty_4h, df5m=empty_5m,
            df1d=daily_uptrend, symbol="BTCUSDT",
            sim_start=daily_uptrend.index[391], sim_end=daily_uptrend.index[-1],
            cfg={"regime_allocation_enabled": True},  # ONLY the flag, no params
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=False,
        )
        assert trades  # should run with defaults
