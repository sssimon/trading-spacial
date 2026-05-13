"""Tests for strategy.vol_targeting (epic #338 Phase 1A, closes #342 in part).

Covers realized-volatility annualization, vol-targeted position sizing with
hard caps, leverage-cap proportional scaling, and the end-to-end portfolio
orchestrator. All TDD-developed.
"""
import math

import numpy as np
import pandas as pd
import pytest

from strategy.vol_targeting import (
    DEFAULT_MAX_LEVERAGE,
    DEFAULT_MAX_POSITION_PCT,
    DEFAULT_MIN_POSITION_USD,
    DEFAULT_PORTFOLIO_VOL_TARGET,
    DEFAULT_VOL_WINDOW_DAYS,
    TRADING_DAYS_PER_YEAR,
    apply_leverage_cap,
    compute_portfolio_positions,
    compute_position_size,
    compute_realized_vol_annualized,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_locked_defaults_match_epic_spec(self):
        """§8.3 locked 30% vol target; §8.6 locked 2x leverage; §4.3 locked
        20% per-symbol cap + $50 min order."""
        assert DEFAULT_PORTFOLIO_VOL_TARGET == 0.30
        assert DEFAULT_MAX_LEVERAGE == 2.0
        assert DEFAULT_MAX_POSITION_PCT == 0.20
        assert DEFAULT_MIN_POSITION_USD == 50.0
        assert DEFAULT_VOL_WINDOW_DAYS == 30
        assert TRADING_DAYS_PER_YEAR == 365


# ---------------------------------------------------------------------------
# compute_realized_vol_annualized
# ---------------------------------------------------------------------------


class TestComputeRealizedVolAnnualized:
    def test_zero_vol_returns_nan(self):
        """Constant returns (std = 0) cannot be sized against → NaN."""
        returns = pd.Series([0.0] * 30)
        vol = compute_realized_vol_annualized(returns)
        assert math.isnan(vol)

    def test_constant_1pct_daily_std(self):
        """30 returns alternating ±1% give std ≈ 1% (sample, ddof=1), annualized.

        Sample std (ddof=1) of [+0.01,-0.01,...] over n obs = 0.01 × sqrt(n/(n-1)).
        For n=30: 0.01 × sqrt(30/29) ≈ 0.01017. Annualized: × sqrt(365) ≈ 0.1943.
        """
        returns = pd.Series([0.01, -0.01] * 15)
        vol = compute_realized_vol_annualized(returns)
        expected = 0.01 * math.sqrt(30 / 29) * math.sqrt(365)
        assert vol == pytest.approx(expected, abs=1e-4)

    def test_insufficient_data_returns_nan(self):
        """Fewer returns than window → NaN."""
        returns = pd.Series([0.01] * 10)
        vol = compute_realized_vol_annualized(returns, window=30)
        assert math.isnan(vol)

    def test_uses_only_last_window_returns(self):
        """Earlier returns (beyond window) must be ignored."""
        # First 100 bars high-vol (5%), last 30 bars low-vol (alternating ±1%)
        np.random.seed(42)
        old = np.random.normal(0, 0.05, 100)
        new = np.array([0.01, -0.01] * 15)
        returns = pd.Series(np.concatenate([old, new]))
        vol = compute_realized_vol_annualized(returns, window=30)
        # Should reflect last 30 (alt ±0.01), not full 130
        expected = 0.01 * math.sqrt(30 / 29) * math.sqrt(365)
        assert vol == pytest.approx(expected, abs=1e-3)

    def test_nan_returns_dropped(self):
        """NaN returns in the window are dropped before computing std."""
        returns = pd.Series([0.01, -0.01] * 14 + [float("nan"), float("nan")])
        vol = compute_realized_vol_annualized(returns, window=30)
        # 28 valid + 2 nan dropped → sample std on 28 obs of ±0.01 = 0.01 × sqrt(28/27)
        assert math.isfinite(vol)
        expected = 0.01 * math.sqrt(28 / 27) * math.sqrt(365)
        assert vol == pytest.approx(expected, abs=1e-3)

    def test_too_few_valid_after_dropna_returns_nan(self):
        """If NaN-stripped window has fewer than 2 returns → NaN."""
        returns = pd.Series([float("nan")] * 30)
        vol = compute_realized_vol_annualized(returns, window=30)
        assert math.isnan(vol)

    def test_window_lt_2_raises(self):
        """std requires ≥ 2 obs."""
        with pytest.raises(ValueError, match="window must be"):
            compute_realized_vol_annualized(pd.Series([0.01, 0.02]), window=1)

    def test_high_vol_50pct_annualized(self):
        """30 returns of std 2.6% → annualized ≈ 50%.

        Sample std (ddof=1) over n=30 alt ±0.026 = 0.026 × sqrt(30/29).
        Annualized × sqrt(365) ≈ 0.5052.
        """
        returns = pd.Series([0.026, -0.026] * 15)
        vol = compute_realized_vol_annualized(returns)
        expected = 0.026 * math.sqrt(30 / 29) * math.sqrt(365)
        assert vol == pytest.approx(expected, abs=1e-3)


# ---------------------------------------------------------------------------
# compute_position_size
# ---------------------------------------------------------------------------


class TestComputePositionSize:
    def test_direction_zero_returns_zero(self):
        size = compute_position_size(
            capital_usd=10_000.0, direction=0,
            target_vol_per_symbol=0.05, realized_vol_annualized=0.50,
        )
        assert size == 0.0

    def test_long_basic_math(self):
        """capital=$10k, target=5%, realized=50% → notional = 10000*0.05/0.50 = $1000."""
        size = compute_position_size(
            capital_usd=10_000.0, direction=1,
            target_vol_per_symbol=0.05, realized_vol_annualized=0.50,
            max_position_pct=1.0,  # no cap
            min_position_usd=0.0,  # no floor
        )
        assert size == pytest.approx(1_000.0)

    def test_short_returns_negative(self):
        """SHORT direction returns negative signed notional."""
        size = compute_position_size(
            capital_usd=10_000.0, direction=-1,
            target_vol_per_symbol=0.05, realized_vol_annualized=0.50,
            max_position_pct=1.0, min_position_usd=0.0,
        )
        assert size == pytest.approx(-1_000.0)

    def test_max_position_pct_caps_notional(self):
        """If vol-targeted size would exceed 20% of capital, hard cap applies."""
        # Tiny vol → huge implied size
        size = compute_position_size(
            capital_usd=10_000.0, direction=1,
            target_vol_per_symbol=0.05, realized_vol_annualized=0.10,  # raw = $5000 = 50%
            max_position_pct=0.20,  # cap at $2000
            min_position_usd=0.0,
        )
        assert size == pytest.approx(2_000.0)

    def test_min_position_usd_skips_tiny_trade(self):
        """If sizing produces a notional < $50, return 0 (skip the trade)."""
        # Huge vol → tiny implied size
        size = compute_position_size(
            capital_usd=10_000.0, direction=1,
            target_vol_per_symbol=0.05, realized_vol_annualized=20.0,  # raw = $25
            max_position_pct=1.0, min_position_usd=50.0,
        )
        assert size == 0.0

    def test_nan_realized_vol_returns_zero(self):
        """Can't size without a valid vol estimate."""
        size = compute_position_size(
            capital_usd=10_000.0, direction=1,
            target_vol_per_symbol=0.05, realized_vol_annualized=float("nan"),
        )
        assert size == 0.0

    def test_zero_realized_vol_returns_zero(self):
        """Division-by-zero guard."""
        size = compute_position_size(
            capital_usd=10_000.0, direction=1,
            target_vol_per_symbol=0.05, realized_vol_annualized=0.0,
        )
        assert size == 0.0

    def test_negative_realized_vol_returns_zero(self):
        """Defensive: nonsensical vol → no position."""
        size = compute_position_size(
            capital_usd=10_000.0, direction=1,
            target_vol_per_symbol=0.05, realized_vol_annualized=-0.10,
        )
        assert size == 0.0

    def test_zero_capital_returns_zero(self):
        size = compute_position_size(
            capital_usd=0.0, direction=1,
            target_vol_per_symbol=0.05, realized_vol_annualized=0.50,
        )
        assert size == 0.0

    def test_invalid_direction_raises(self):
        """Anti-typo: only -1, 0, +1 accepted."""
        with pytest.raises(ValueError, match="direction must be in"):
            compute_position_size(
                capital_usd=10_000.0, direction=2,  # noqa
                target_vol_per_symbol=0.05, realized_vol_annualized=0.50,
            )

    def test_zero_target_vol_returns_zero(self):
        size = compute_position_size(
            capital_usd=10_000.0, direction=1,
            target_vol_per_symbol=0.0, realized_vol_annualized=0.50,
        )
        assert size == 0.0


# ---------------------------------------------------------------------------
# apply_leverage_cap
# ---------------------------------------------------------------------------


class TestApplyLeverageCap:
    def test_within_cap_returns_unchanged(self):
        """Sum |positions| ≤ cap → no scaling."""
        positions = {"BTC": 5000.0, "ETH": -3000.0}
        # Sum = $8000 < 2 × $10000 = $20000 cap
        out = apply_leverage_cap(positions, capital_usd=10_000.0, max_leverage=2.0)
        assert out == {"BTC": 5000.0, "ETH": -3000.0}

    def test_at_cap_returns_unchanged(self):
        """At the cap exactly (boundary) → no scaling."""
        positions = {"BTC": 12_000.0, "ETH": -8_000.0}
        # Sum = $20000 = cap
        out = apply_leverage_cap(positions, capital_usd=10_000.0, max_leverage=2.0)
        assert out == {"BTC": 12_000.0, "ETH": -8_000.0}

    def test_over_cap_scaled_proportionally(self):
        """Sum $25k > $20k cap → scale by 0.8 across all positions."""
        positions = {"BTC": 15_000.0, "ETH": -10_000.0}
        out = apply_leverage_cap(positions, capital_usd=10_000.0, max_leverage=2.0)
        # Scale factor = 20000 / 25000 = 0.8
        assert out["BTC"] == pytest.approx(12_000.0)
        assert out["ETH"] == pytest.approx(-8_000.0)
        total = sum(abs(p) for p in out.values())
        assert total == pytest.approx(20_000.0)

    def test_signs_preserved(self):
        """LONG positions stay LONG, SHORT positions stay SHORT after scaling."""
        positions = {"A": 30_000.0, "B": -20_000.0, "C": 10_000.0}
        out = apply_leverage_cap(positions, capital_usd=10_000.0, max_leverage=2.0)
        assert out["A"] > 0
        assert out["B"] < 0
        assert out["C"] > 0

    def test_empty_returns_empty(self):
        out = apply_leverage_cap({}, capital_usd=10_000.0, max_leverage=2.0)
        assert out == {}

    def test_all_zero_positions_returns_unchanged(self):
        """No notional to scale → return as-is (no division by zero)."""
        positions = {"BTC": 0.0, "ETH": 0.0}
        out = apply_leverage_cap(positions, capital_usd=10_000.0, max_leverage=2.0)
        assert out == positions

    def test_single_position_capped(self):
        """One symbol with notional > cap → scaled to exactly cap."""
        positions = {"BTC": 100_000.0}
        out = apply_leverage_cap(positions, capital_usd=10_000.0, max_leverage=2.0)
        assert out["BTC"] == pytest.approx(20_000.0)

    def test_negative_capital_raises(self):
        with pytest.raises(ValueError, match="capital_usd"):
            apply_leverage_cap({"A": 100.0}, capital_usd=-10.0)

    def test_zero_capital_raises(self):
        with pytest.raises(ValueError, match="capital_usd"):
            apply_leverage_cap({"A": 100.0}, capital_usd=0.0)

    def test_negative_leverage_raises(self):
        with pytest.raises(ValueError, match="max_leverage"):
            apply_leverage_cap({"A": 100.0}, capital_usd=10_000.0, max_leverage=-1.0)


# ---------------------------------------------------------------------------
# compute_portfolio_positions — end-to-end orchestrator
# ---------------------------------------------------------------------------


class TestComputePortfolioPositions:
    def test_all_zero_directions_returns_empty(self):
        """No active symbols → no positions."""
        out = compute_portfolio_positions(
            capital_usd=10_000.0,
            directions={"BTC": 0, "ETH": 0, "ADA": 0},
            realized_vols={"BTC": 0.50, "ETH": 0.60, "ADA": 1.0},
        )
        assert out == {}

    def test_single_active_symbol_gets_full_vol_budget(self):
        """One active symbol → target_per_symbol = portfolio_vol_target / 1."""
        out = compute_portfolio_positions(
            capital_usd=10_000.0,
            directions={"BTC": 1, "ETH": 0},
            realized_vols={"BTC": 0.30, "ETH": 0.60},
            max_position_pct=1.0,  # disable per-symbol cap
        )
        # target = 0.30 / 1 = 0.30; size = 10000 × 0.30 / 0.30 = $10000
        # But default max_position_pct = 1.0 (since we passed it explicitly), no cap
        # Leverage cap: sum |positions| = $10000 ≤ $20000 → no scale
        assert "BTC" in out
        assert out["BTC"] == pytest.approx(10_000.0, rel=0.01)
        assert "ETH" not in out  # flat

    def test_three_active_long_symbols(self):
        """3 active LONG symbols, each with realized vol = portfolio_target → each
        gets equal share of vol budget."""
        out = compute_portfolio_positions(
            capital_usd=10_000.0,
            directions={"BTC": 1, "ETH": 1, "ADA": 1, "DOGE": 0},
            realized_vols={"BTC": 0.30, "ETH": 0.30, "ADA": 0.30, "DOGE": 0.50},
            portfolio_vol_target=0.30,
            max_position_pct=1.0,
        )
        # target_per_symbol = 0.30 / 3 = 0.10
        # size_each = 10000 × 0.10 / 0.30 ≈ $3333
        for sym in ("BTC", "ETH", "ADA"):
            assert out[sym] == pytest.approx(3_333.33, rel=0.01)
        assert "DOGE" not in out

    def test_short_returns_negative_position(self):
        """SHORT direction yields negative signed position USD."""
        out = compute_portfolio_positions(
            capital_usd=10_000.0,
            directions={"BTC": -1},
            realized_vols={"BTC": 0.30},
            max_position_pct=1.0,
        )
        assert out["BTC"] < 0
        assert out["BTC"] == pytest.approx(-10_000.0, rel=0.01)

    def test_leverage_cap_kicks_in_with_many_symbols(self):
        """5 LONG + 5 SHORT, each ≈ 100% of capital pre-cap, gets scaled to 2x total."""
        # Set realized vols equal to target so raw_per_symbol = capital
        out = compute_portfolio_positions(
            capital_usd=10_000.0,
            directions={
                "S1": 1, "S2": 1, "S3": 1, "S4": 1, "S5": 1,
                "S6": -1, "S7": -1, "S8": -1, "S9": -1, "S10": -1,
            },
            realized_vols={f"S{i}": 0.03 for i in range(1, 11)},  # target/10 each
            portfolio_vol_target=0.30,
            max_position_pct=1.0,  # so per-symbol cap doesn't bite
            max_leverage=2.0,
        )
        total = sum(abs(p) for p in out.values())
        assert total == pytest.approx(20_000.0, rel=0.001)  # exactly leverage cap

    def test_per_symbol_cap_bites_before_leverage_cap(self):
        """If each symbol's individual cap triggers, leverage cap is downstream."""
        # 10 symbols, each with very low vol → each would be ~100% of capital
        # Per-symbol cap = 20% → each = $2000, total = $20000 = leverage cap (just at)
        out = compute_portfolio_positions(
            capital_usd=10_000.0,
            directions={f"S{i}": 1 for i in range(1, 11)},
            realized_vols={f"S{i}": 0.03 for i in range(1, 11)},
            portfolio_vol_target=0.30,
            max_position_pct=0.20,
            max_leverage=2.0,
        )
        # Each symbol capped at $2000; total = $20000 = leverage cap exactly
        for sym in out:
            assert out[sym] == pytest.approx(2_000.0, rel=0.01)

    def test_missing_realized_vol_skips_symbol(self):
        """If a symbol has no vol entry, it gets size 0 and is excluded."""
        out = compute_portfolio_positions(
            capital_usd=10_000.0,
            directions={"BTC": 1, "ETH": 1},
            realized_vols={"BTC": 0.30},  # ETH missing
            max_position_pct=1.0,
        )
        assert "BTC" in out
        assert "ETH" not in out

    def test_below_min_threshold_excluded(self):
        """Symbol whose vol-targeted size falls below $50 is excluded."""
        out = compute_portfolio_positions(
            capital_usd=10_000.0,
            directions={"BTC": 1},
            realized_vols={"BTC": 100.0},  # crazy high vol → tiny size
            portfolio_vol_target=0.30,
            min_position_usd=50.0,
        )
        # size = 10000 × 0.30 / 100 = $30 < $50 → excluded
        assert out == {}

    def test_defaults_used_when_not_specified(self):
        """Calling without explicit params uses locked defaults."""
        out = compute_portfolio_positions(
            capital_usd=10_000.0,
            directions={"BTC": 1},
            realized_vols={"BTC": 0.30},
        )
        # With defaults: target=0.30/1=0.30; raw=$10000; max_pct=0.20 → cap=$2000
        assert out["BTC"] == pytest.approx(2_000.0, rel=0.01)
