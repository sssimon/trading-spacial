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
        assert tp.half_spread_bps == 7.5
        assert tp.fee_bps_per_side == 10.0
        assert tp.funding_rate_bps_per_8h == 2.0

    def test_from_v2_flat_funding_defaults_zero(self):
        from backtest_costs import TierParams
        tp = TierParams.from_v2_flat(
            base_bps=5.0, size_factor=1.0, half_spread_bps=1.0, fee_bps_per_side=1.0)
        assert tp.funding_rate_bps_per_8h == 0.0

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


class TestComputeTradeCostsV3:
    def _v3_mid(self):
        from backtest_costs import TierParams
        return TierParams.from_v3_tier(
            floor={"half_spread_bps": 4.0, "fee_bps_per_side": 5.0,
                   "funding_rate_bps_per_8h": 2.0, "stress_mult": 1.0},
            impact_tail={"sigma_daily_bps": 500.0},
        )

    def test_floor_dominates_at_operating_size(self):
        from backtest_costs import compute_trade_costs, GlobalParams
        c = compute_trade_costs(
            entry_notional_usd=644.0, exit_notional_usd=644.0,
            entry_liquidity_usd_per_min=1_000_000.0,
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=self._v3_mid(), model="v3",
            global_params=GlobalParams(), holding_hours=5.3,
        )
        assert c["floor_bps"] == pytest.approx(18.0, abs=0.01)
        assert c["tail_bps"] < c["floor_bps"]
        assert c["total_cost_bps"] == pytest.approx(c["floor_bps"] + c["tail_bps"], abs=1e-6)
        assert c["cap_hit"] is False

    def test_funding_charged_only_past_8h(self):
        from backtest_costs import compute_trade_costs, GlobalParams
        c = compute_trade_costs(
            entry_notional_usd=644.0, exit_notional_usd=644.0,
            entry_liquidity_usd_per_min=1_000_000.0,
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=self._v3_mid(), model="v3",
            global_params=GlobalParams(), holding_hours=24.0,
        )
        assert c["funding_cost_bps"] == pytest.approx(6.0)
        assert c["floor_bps"] == pytest.approx(24.0, abs=0.01)

    def test_total_cap_binds(self):
        from backtest_costs import TierParams, compute_trade_costs, GlobalParams
        small = TierParams.from_v3_tier(
            floor={"half_spread_bps": 10.0, "fee_bps_per_side": 5.0,
                   "funding_rate_bps_per_8h": 5.0, "stress_mult": 1.0},
            impact_tail={"sigma_daily_bps": 800.0},
        )
        c = compute_trade_costs(
            entry_notional_usd=5_000_000.0, exit_notional_usd=5_000_000.0,
            entry_liquidity_usd_per_min=10_000.0,
            exit_liquidity_usd_per_min=10_000.0,
            tier_params=small, model="v3", global_params=GlobalParams(),
        )
        assert c["total_cost_bps"] == pytest.approx(1000.0)
        assert c["cap_hit"] is True

    def test_leg_fallback_composes_above_floor(self):
        from backtest_costs import compute_trade_costs, GlobalParams
        c = compute_trade_costs(
            entry_notional_usd=644.0, exit_notional_usd=644.0,
            entry_liquidity_usd_per_min=float("nan"),
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=self._v3_mid(), model="v3", global_params=GlobalParams(),
        )
        # entry leg dead -> fallback max(1*(4+5)=9, 100)=100 (excess 91 in slippage)
        # exit leg live: floor 9 + tiny tail ~0.50 ; spread/fee reported separately
        assert c["total_cost_bps"] == pytest.approx(109.50, abs=0.05)
        assert c["fallback_hit"] is True
        # dict components must reconstruct the total in the fallback case
        recon = (c["entry_slippage_bps"] + c["exit_slippage_bps"]
                 + c["entry_spread_bps"] + c["exit_spread_bps"]
                 + c["fee_bps"] + c["funding_cost_bps"])
        assert recon == pytest.approx(c["total_cost_bps"], abs=1e-6)

    def test_default_model_is_still_v2(self):
        from backtest_costs import compute_trade_costs, TierParams
        c = compute_trade_costs(
            entry_notional_usd=1_000.0, exit_notional_usd=1_000.0,
            entry_liquidity_usd_per_min=1_000_000.0,
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=TierParams(5.0, 1423.02, 7.5, 10.0, 2.0),
            enable_spread=False, enable_fees=False, enable_funding=False,
        )
        assert c["entry_slippage_bps"] == pytest.approx(50.0, abs=0.1)

    def test_unknown_model_raises_in_trade_costs(self):
        from backtest_costs import compute_trade_costs, TierParams
        with pytest.raises(ValueError, match="Unknown cost model"):
            compute_trade_costs(
                entry_notional_usd=1_000.0, exit_notional_usd=1_000.0,
                entry_liquidity_usd_per_min=1e6, exit_liquidity_usd_per_min=1e6,
                tier_params=TierParams(5.0, 1423.02, 7.5, 10.0, 2.0),
                model="bogus",
            )

    def test_v2_params_in_v3_raises(self):
        from backtest_costs import TierParams, compute_trade_costs, GlobalParams
        v2tp = TierParams(5.0, 1423.02, 7.5, 10.0, 2.0)  # stress_mult/sigma are NaN
        with pytest.raises(ValueError, match="finite stress_mult"):
            compute_trade_costs(
                entry_notional_usd=644.0, exit_notional_usd=644.0,
                entry_liquidity_usd_per_min=1e6, exit_liquidity_usd_per_min=1e6,
                tier_params=v2tp, model="v3", global_params=GlobalParams())

    def test_v3_enable_slippage_false_omits_tail_and_fallback(self):
        from backtest_costs import compute_trade_costs, GlobalParams
        # Even with dead entry liquidity, enable_slippage=False -> spread+fee+funding only.
        c = compute_trade_costs(
            entry_notional_usd=644.0, exit_notional_usd=644.0,
            entry_liquidity_usd_per_min=float("nan"),
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=self._v3_mid(), model="v3", global_params=GlobalParams(),
            enable_slippage=False, holding_hours=0.0,
        )
        assert c["tail_bps"] == 0.0
        assert c["total_cost_bps"] == pytest.approx(18.0, abs=0.01)  # spread+fee only


class TestVersionAwareLoader:
    def test_v2_sibling_loads_flat(self):
        from backtest_costs import load_calibration
        cal = load_calibration(path="costs_calibration.v2.json")
        assert cal.version == 2
        assert cal.active_model == "v2"   # absent in v2 JSON -> defaults to "v2"
        mid = cal.tiers["mid"]
        assert mid.size_factor == pytest.approx(1423.02)  # v2 field present
        assert math.isnan(mid.stress_mult)                # v3 field poisoned

    def test_v3_fixture_loads_nested(self, tmp_path):
        import json
        from backtest_costs import load_calibration
        v3 = {
            "version": 3, "model": "two-body", "active_model": "v3",
            "global": {"Y_impact_constant": 1.5, "total_cost_cap_bps": 1000.0,
                       "liquidity_fallback_floor_bps": 100.0,
                       "v_daily_minutes_per_day": 1440},
            "tiers": {"mid": {"symbols": ["ADAUSDT"],
                "floor": {"half_spread_bps": 4.0, "fee_bps_per_side": 5.0,
                          "funding_rate_bps_per_8h": 2.0, "stress_mult": 1.0},
                "impact_tail": {"sigma_daily_bps": 500.0}}},
            "sources": {}, "sensitivity_note": "x",
        }
        p = tmp_path / "v3.json"
        p.write_text(json.dumps(v3))
        cal = load_calibration(path=str(p))
        assert cal.version == 3
        assert cal.active_model == "v3"
        assert cal.global_.Y_impact_constant == 1.5
        mid = cal.tiers["mid"]
        assert mid.sigma_daily_bps == 500.0
        assert math.isnan(mid.size_factor)  # v2 field poisoned

    def test_missing_version_key_raises_with_path(self, tmp_path):
        import json
        from backtest_costs import load_calibration
        p = tmp_path / "noversion.json"
        p.write_text(json.dumps({"model": "x", "tiers": {}, "sources": {},
                                 "sensitivity_note": "y"}))
        with pytest.raises(KeyError, match="missing the required 'version'"):
            load_calibration(path=str(p))

    def test_v3_missing_global_block_raises(self, tmp_path):
        import json
        from backtest_costs import load_calibration
        p = tmp_path / "noglobal.json"
        p.write_text(json.dumps({"version": 3, "model": "x", "active_model": "v3",
                                 "tiers": {}, "sources": {}, "sensitivity_note": "y"}))
        with pytest.raises(KeyError, match="missing the required 'global'"):
            load_calibration(path=str(p))


class TestBacktestPathsUseV3:
    def test_loaded_main_calibration_is_v3(self):
        from backtest_costs import load_calibration
        cal = load_calibration()
        assert cal.version == 3
        assert cal.active_model == "v3"
        assert cal.global_ is not None and cal.global_.Y_impact_constant == 1.5
        assert cal.tiers["major"].half_spread_bps == 1.5
        assert cal.tiers["major"].fee_bps_per_side == 5.0
        assert cal.tiers["mid"].sigma_daily_bps == 500.0

    def test_floor_rt_values(self):
        from backtest_costs import load_calibration
        cal = load_calibration()
        def floor_rt(t):
            return cal.tiers[t].stress_mult * (2*cal.tiers[t].half_spread_bps
                                               + 2*cal.tiers[t].fee_bps_per_side)
        assert floor_rt("major") == pytest.approx(13.0)
        assert floor_rt("mid") == pytest.approx(18.0)
        assert floor_rt("small") == pytest.approx(30.0)
