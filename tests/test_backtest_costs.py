"""Unit tests for backtest_costs — realistic transaction cost model (A.0.2, #277).

Covers tier classification, slippage/spread/fee components, calibration loading,
and edge cases (zero volume, missing data). Integration with simulate_strategy
is in test_backtest_with_costs.py.
"""
import json
import math
from pathlib import Path

import pytest


class TestTierForSymbol:
    """Per-spec tier mapping (#277): majors / mid-cap / small-cap."""

    def test_majors_btc_eth(self):
        from backtest_costs import tier_for_symbol

        assert tier_for_symbol("BTCUSDT") == "major"
        assert tier_for_symbol("ETHUSDT") == "major"

    def test_mid_cap_set(self):
        from backtest_costs import tier_for_symbol

        for sym in ("ADAUSDT", "AVAXUSDT", "DOGEUSDT", "UNIUSDT", "XLMUSDT"):
            assert tier_for_symbol(sym) == "mid", f"{sym} should be mid"

    def test_small_cap_set(self):
        from backtest_costs import tier_for_symbol

        for sym in ("PENDLEUSDT", "JUPUSDT", "RUNEUSDT"):
            assert tier_for_symbol(sym) == "small", f"{sym} should be small"

    def test_unknown_symbol_raises(self):
        """Symbols outside the curated 10 must raise — refuses to silently apply
        wrong-tier costs to a symbol the calibration never validated."""
        from backtest_costs import tier_for_symbol, UnknownSymbolError

        with pytest.raises(UnknownSymbolError):
            tier_for_symbol("XRPUSDT")


class TestSlippageBps:
    """v1 linear slippage: bps = base + size_factor * (order_usd / liquidity_per_min)."""

    def test_zero_size_returns_only_base_bps(self):
        """A zero-notional order incurs only the always-on minimum slippage."""
        from backtest_costs import compute_slippage_bps

        bps = compute_slippage_bps(
            order_usd=0.0,
            liquidity_usd_per_min=1_000_000.0,
            base_bps=5.0,
            size_factor=25_000.0,
        )
        assert bps == pytest.approx(5.0)

    def test_doubling_order_size_doubles_size_dependent_term(self):
        """Linear contract: variable component scales linearly with order size."""
        from backtest_costs import compute_slippage_bps

        kwargs = dict(liquidity_usd_per_min=1_000_000.0, base_bps=5.0, size_factor=25_000.0)
        small = compute_slippage_bps(order_usd=1_000.0, **kwargs)
        large = compute_slippage_bps(order_usd=2_000.0, **kwargs)

        # variable = small - 5; large should be 5 + 2 * variable
        var = small - 5.0
        assert large == pytest.approx(5.0 + 2 * var)

    def test_calibration_anchor_majors_01pct_participation_yields_30bps(self):
        """Spec anchor (#277 §1): 0.1% participation should produce ~30 bps total
        slippage on majors when base_bps=5, size_factor=25_000.
        """
        from backtest_costs import compute_slippage_bps

        liquidity_per_min = 1_000_000.0
        order = liquidity_per_min * 0.001  # 0.1% participation = 1_000 USD
        bps = compute_slippage_bps(
            order_usd=order,
            liquidity_usd_per_min=liquidity_per_min,
            base_bps=5.0,
            size_factor=25_000.0,
        )
        # Formula: 5 + 25_000 * (1000/1_000_000) = 5 + 25 = 30
        assert bps == pytest.approx(30.0)

    def test_zero_liquidity_returns_conservative_fallback(self):
        """Zero-volume bar: refuse to divide by zero. Fall back to a conservative
        slippage (default = 100 bps = 1%, deliberately punitive so the strategy
        is penalized for entering when volume is unobservable). Caller may
        override via fallback_bps kwarg."""
        from backtest_costs import compute_slippage_bps

        bps = compute_slippage_bps(
            order_usd=1_000.0,
            liquidity_usd_per_min=0.0,
            base_bps=5.0,
            size_factor=25_000.0,
        )
        assert bps == pytest.approx(100.0)

    def test_zero_liquidity_respects_caller_override(self):
        from backtest_costs import compute_slippage_bps

        bps = compute_slippage_bps(
            order_usd=1_000.0,
            liquidity_usd_per_min=0.0,
            base_bps=5.0,
            size_factor=25_000.0,
            fallback_bps=200.0,
        )
        assert bps == pytest.approx(200.0)

    def test_negative_liquidity_treated_as_zero(self):
        """Defensive: pandas can produce -0.0 or NaN-converted-to-negative under
        unusual rolling. Treat any non-positive liquidity as fallback territory."""
        from backtest_costs import compute_slippage_bps

        bps = compute_slippage_bps(
            order_usd=1_000.0,
            liquidity_usd_per_min=-1.0,
            base_bps=5.0,
            size_factor=25_000.0,
        )
        assert bps == pytest.approx(100.0)

    def test_nan_liquidity_treated_as_zero(self):
        from backtest_costs import compute_slippage_bps

        bps = compute_slippage_bps(
            order_usd=1_000.0,
            liquidity_usd_per_min=float("nan"),
            base_bps=5.0,
            size_factor=25_000.0,
        )
        assert bps == pytest.approx(100.0)


class TestLoadCalibration:
    """Calibration JSON is the source of truth — tests guard against drift."""

    def test_loads_committed_calibration(self):
        """The committed costs_calibration.json must load and expose tier params
        for major / mid / small."""
        from backtest_costs import load_calibration

        cal = load_calibration()
        assert set(cal.tiers.keys()) == {"major", "mid", "small"}
        for tier_name, params in cal.tiers.items():
            assert params.base_bps > 0, f"{tier_name} base_bps must be positive"
            assert params.size_factor > 0, f"{tier_name} size_factor must be positive"
            assert params.half_spread_bps > 0, f"{tier_name} half_spread_bps must be positive"
            assert params.fee_bps_per_side > 0, f"{tier_name} fee_bps_per_side must be positive"

    def test_calibration_covers_all_curated_symbols(self):
        """Every curated symbol must have resolvable params via tier_for_symbol →
        cal.tiers — guards against a curated symbol missing from any tier."""
        from backtest_costs import _TIER_BY_SYMBOL, load_calibration, tier_for_symbol

        cal = load_calibration()
        for sym in _TIER_BY_SYMBOL:
            tier = tier_for_symbol(sym)
            assert tier in cal.tiers, f"Calibration missing tier {tier!r} (needed by {sym})"

    def test_calibration_documents_source_per_param(self):
        """Spec §1: every parameter must cite its source in costs_calibration.json
        ('Not acceptable: a number invented to match desired output')."""
        from backtest_costs import load_calibration

        cal = load_calibration()
        # `sources` is a dict keyed by parameter name with a non-empty string value.
        assert "base_bps" in cal.sources and cal.sources["base_bps"].strip()
        assert "size_factor" in cal.sources and cal.sources["size_factor"].strip()
        assert "half_spread_bps" in cal.sources and cal.sources["half_spread_bps"].strip()
        assert "fee_bps_per_side" in cal.sources and cal.sources["fee_bps_per_side"].strip()

    def test_calibration_includes_sensitivity_note(self):
        """Spec §1: 'sensitivity note (if base_bps doubles, what does it do to
        baseline Calmar — at least one sensitivity bullet)'."""
        from backtest_costs import load_calibration

        cal = load_calibration()
        assert cal.sensitivity_note and len(cal.sensitivity_note) > 0

    def test_calibration_records_v1_model_marker(self):
        """v1 must be tagged 'linear'; v2 plan documented in module docstring."""
        from backtest_costs import load_calibration

        cal = load_calibration()
        assert cal.model == "linear"


class TestComputeTradeCosts:
    """Orchestrator: combine slippage + spread + fee for entry + exit."""

    def _params(self):
        from backtest_costs import TierParams

        return TierParams(
            base_bps=5.0,
            size_factor=25_000.0,
            half_spread_bps=1.5,
            fee_bps_per_side=10.0,
        )

    def test_all_flags_off_returns_zero(self):
        from backtest_costs import compute_trade_costs

        c = compute_trade_costs(
            entry_notional_usd=10_000.0,
            exit_notional_usd=10_000.0,
            entry_liquidity_usd_per_min=1_000_000.0,
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=self._params(),
            enable_slippage=False,
            enable_spread=False,
            enable_fees=False,
        )
        assert c["entry_slippage_bps"] == 0.0
        assert c["exit_slippage_bps"] == 0.0
        assert c["entry_spread_bps"] == 0.0
        assert c["exit_spread_bps"] == 0.0
        assert c["fee_bps"] == 0.0
        assert c["total_cost_bps"] == 0.0

    def test_only_fees_enabled_returns_round_trip_fee(self):
        """fee_bps = 2 × fee_bps_per_side (entry + exit)."""
        from backtest_costs import compute_trade_costs

        c = compute_trade_costs(
            entry_notional_usd=10_000.0,
            exit_notional_usd=10_000.0,
            entry_liquidity_usd_per_min=1_000_000.0,
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=self._params(),
            enable_slippage=False,
            enable_spread=False,
            enable_fees=True,
        )
        assert c["fee_bps"] == pytest.approx(20.0)
        assert c["total_cost_bps"] == pytest.approx(20.0)

    def test_only_spread_enabled_applies_half_spread_per_side(self):
        from backtest_costs import compute_trade_costs

        c = compute_trade_costs(
            entry_notional_usd=10_000.0,
            exit_notional_usd=10_000.0,
            entry_liquidity_usd_per_min=1_000_000.0,
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=self._params(),
            enable_slippage=False,
            enable_spread=True,
            enable_fees=False,
        )
        assert c["entry_spread_bps"] == pytest.approx(1.5)
        assert c["exit_spread_bps"] == pytest.approx(1.5)
        assert c["total_cost_bps"] == pytest.approx(3.0)

    def test_only_slippage_enabled_uses_per_side_liquidity(self):
        from backtest_costs import compute_trade_costs

        # 0.1% participation on entry, 0.2% on exit
        c = compute_trade_costs(
            entry_notional_usd=1_000.0,
            exit_notional_usd=2_000.0,
            entry_liquidity_usd_per_min=1_000_000.0,
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=self._params(),
            enable_slippage=True,
            enable_spread=False,
            enable_fees=False,
        )
        # Entry: 5 + 25_000 * 0.001 = 30
        # Exit:  5 + 25_000 * 0.002 = 55
        assert c["entry_slippage_bps"] == pytest.approx(30.0)
        assert c["exit_slippage_bps"] == pytest.approx(55.0)
        assert c["total_cost_bps"] == pytest.approx(85.0)

    def test_all_flags_on_sums_components(self):
        from backtest_costs import compute_trade_costs

        c = compute_trade_costs(
            entry_notional_usd=1_000.0,
            exit_notional_usd=1_000.0,
            entry_liquidity_usd_per_min=1_000_000.0,
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=self._params(),
            enable_slippage=True,
            enable_spread=True,
            enable_fees=True,
        )
        # Entry: slip 30 + spread 1.5; Exit: same; fee round-trip 20
        # Total: 30 + 30 + 1.5 + 1.5 + 20 = 83
        assert c["total_cost_bps"] == pytest.approx(83.0)

    def test_total_cost_usd_uses_avg_notional(self):
        """total_cost_usd = total_cost_bps × avg_notional / 10_000.

        Average rather than entry-only because cost components are split between
        entry-notional and exit-notional sides; converting per-side bps to USD
        with side-specific notionals would over-attribute exit-side costs to
        the entry stake. Using the average is a v1 simplification — v2 may
        compute side-specific USD costs and sum.
        """
        from backtest_costs import compute_trade_costs

        c = compute_trade_costs(
            entry_notional_usd=10_000.0,
            exit_notional_usd=12_000.0,
            entry_liquidity_usd_per_min=1_000_000.0,
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=self._params(),
            enable_slippage=False,
            enable_spread=False,
            enable_fees=True,
        )
        # fee 20 bps × avg notional 11_000 / 10_000 = 22 USD
        assert c["total_cost_usd"] == pytest.approx(22.0)
