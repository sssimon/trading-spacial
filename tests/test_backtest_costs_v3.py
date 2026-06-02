# tests/test_backtest_costs_v3.py
"""v3 two-body cost model — see docs/superpowers/specs/2026-06-02-cost-model-v3-design.md."""
import math
import pytest
from backtest_costs import (
    GlobalParams, PUBLISHED_TAKER_FEE_BPS, DEFAULT_TOTAL_COST_CAP_BPS,
)


class TestGlobalParams:
    def test_defaults_match_spec(self):
        g = GlobalParams()
        assert g.Y_impact_constant == 1.5
        assert g.total_cost_cap_bps == 1000.0
        assert g.liquidity_fallback_floor_bps == 100.0
        assert g.v_daily_minutes_per_day == 1440.0

    def test_module_constants(self):
        assert PUBLISHED_TAKER_FEE_BPS == 5.0
        assert DEFAULT_TOTAL_COST_CAP_BPS == 1000.0


class TestComputeTailBps:
    def test_zero_order_zero_tail(self):
        from backtest_costs import compute_tail_bps
        assert compute_tail_bps(
            order_usd=0.0, liquidity_usd_per_min=1_000_000.0,
            sigma_daily_bps=300.0, Y=1.5, v_daily_minutes_per_day=1440.0,
        ) == 0.0

    def test_daily_basis_value(self):
        # order=1000, liq/min=1e6, v_min=1440 -> V_daily=1.44e9
        # participation=1000/1.44e9=6.944e-7; sqrt=8.333e-4
        # tail = 1.5 * 300 * 8.333e-4 = 0.375 bps per fill
        from backtest_costs import compute_tail_bps
        t = compute_tail_bps(
            order_usd=1_000.0, liquidity_usd_per_min=1_000_000.0,
            sigma_daily_bps=300.0, Y=1.5, v_daily_minutes_per_day=1440.0,
        )
        assert t == pytest.approx(0.375, abs=0.001)

    def test_monotonic_in_order(self):
        from backtest_costs import compute_tail_bps
        kw = dict(liquidity_usd_per_min=1_000_000.0, sigma_daily_bps=500.0,
                  Y=1.5, v_daily_minutes_per_day=1440.0)
        assert (compute_tail_bps(order_usd=10_000.0, **kw)
                > compute_tail_bps(order_usd=1_000.0, **kw))

    def test_bad_liquidity_returns_nan(self):
        from backtest_costs import compute_tail_bps
        for bad in (0.0, -5.0, float("nan"), float("inf"), float("-inf")):
            r = compute_tail_bps(
                order_usd=1_000.0, liquidity_usd_per_min=bad,
                sigma_daily_bps=300.0, Y=1.5, v_daily_minutes_per_day=1440.0,
            )
            assert math.isnan(r)

    def test_zero_v_daily_minutes_returns_nan(self):
        from backtest_costs import compute_tail_bps
        assert math.isnan(compute_tail_bps(
            order_usd=1_000.0, liquidity_usd_per_min=1_000_000.0,
            sigma_daily_bps=300.0, Y=1.5, v_daily_minutes_per_day=0.0,
        ))

    def test_negative_participation_clamped(self):
        from backtest_costs import compute_tail_bps
        assert compute_tail_bps(
            order_usd=-1_000.0, liquidity_usd_per_min=1_000_000.0,
            sigma_daily_bps=300.0, Y=1.5, v_daily_minutes_per_day=1440.0,
        ) == 0.0


class TestTierParamsDual:
    def test_v2_positional_still_works(self):
        from backtest_costs import TierParams
        tp = TierParams(5.0, 1423.02, 7.5, 10.0, 2.0)
        assert tp.base_bps == 5.0
        assert tp.size_factor == 1423.02
        assert tp.half_spread_bps == 7.5
        assert tp.fee_bps_per_side == 10.0
        assert tp.funding_rate_bps_per_8h == 2.0
        assert math.isnan(tp.stress_mult)
        assert math.isnan(tp.sigma_daily_bps)

    def test_from_v3_tier_poisons_v2_fields(self):
        from backtest_costs import TierParams
        tp = TierParams.from_v3_tier(
            floor={"half_spread_bps": 1.5, "fee_bps_per_side": 5.0,
                   "funding_rate_bps_per_8h": 1.0, "stress_mult": 1.0},
            impact_tail={"sigma_daily_bps": 300.0},
        )
        assert tp.half_spread_bps == 1.5
        assert tp.fee_bps_per_side == 5.0
        assert tp.funding_rate_bps_per_8h == 1.0
        assert tp.stress_mult == 1.0
        assert tp.sigma_daily_bps == 300.0
        assert math.isnan(tp.base_bps)
        assert math.isnan(tp.size_factor)

    def test_from_v2_flat_poisons_v3_fields(self):
        from backtest_costs import TierParams
        tp = TierParams.from_v2_flat(
            base_bps=5.0, size_factor=1423.02, half_spread_bps=7.5,
            fee_bps_per_side=10.0, funding_rate_bps_per_8h=2.0,
        )
        assert tp.base_bps == 5.0
        assert math.isnan(tp.stress_mult)
        assert math.isnan(tp.sigma_daily_bps)

    def test_v3_params_in_v2_slippage_is_nan_not_zero(self):
        from backtest_costs import TierParams, compute_slippage_bps
        tp = TierParams.from_v3_tier(
            floor={"half_spread_bps": 1.5, "fee_bps_per_side": 5.0,
                   "funding_rate_bps_per_8h": 1.0, "stress_mult": 1.0},
            impact_tail={"sigma_daily_bps": 300.0},
        )
        slip = compute_slippage_bps(
            order_usd=1_000.0, liquidity_usd_per_min=1_000_000.0,
            base_bps=tp.base_bps, size_factor=tp.size_factor, model="v2",
        )
        assert math.isnan(slip)
