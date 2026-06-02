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
