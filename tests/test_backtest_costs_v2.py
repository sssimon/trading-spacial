"""v2 sqrt-participation cost model tests (epic #338 Phase 0, closes #340).

Covers:
- Anchor parity v1 ↔ v2 at 0.1% participation per tier (calibration design invariant)
- Small-participation asymmetry: v2 charges more than v1 below anchor (correct per academic)
- Large-participation asymmetry: v2 charges less than v1 above anchor (sub-linear bound)
- Extreme participation cap (EXTREME_PARTICIPATION_CAP_BPS = 500)
- Funding-rate accounting (per-tier conservative, 8h intervals, floor semantics)
- DOGE -$30K forensic case: v1 catastrophic vs v2 mitigated
- Integration smoke: compute_trade_costs with v2 default + funding
"""
import math

import pytest

from backtest_costs import (
    EXTREME_PARTICIPATION_CAP_BPS,
    TierParams,
    compute_funding_cost_bps,
    compute_slippage_bps,
    compute_trade_costs,
    load_calibration,
)


# ---------------------------------------------------------------------------
# Anchor parity — v1 and v2 must produce identical slippage at 0.1% per tier.
# This is the calibration design invariant. If it fails, costs_calibration.json
# has drifted from the formula in backtest_costs.py.
# ---------------------------------------------------------------------------


class TestAnchorParity:
    """At 0.1% participation, v1 and v2 must produce identical total slippage.

    Formula: target = base_bps + size_factor_v1 * 0.001 (v1 linear at anchor)
             target = base_bps + size_factor_v2 * sqrt(0.001) (v2 sqrt at anchor)
    Calibration: size_factor_v2 = size_factor_v1 / sqrt(0.001 / 0.001) × ... ⇒
                 size_factor_v2 = (target - base) / sqrt(0.001)
                                = size_factor_v1 * 0.001 / sqrt(0.001)
                                = size_factor_v1 * sqrt(0.001)
                                ≈ size_factor_v1 * 0.03162
    """

    ANCHOR_PARTICIPATION = 0.001  # 0.1%
    LIQUIDITY = 1_000_000.0

    @pytest.mark.parametrize(
        "tier_name,base_bps,size_factor_v1,target_total_bps",
        [
            ("major", 2.0, 28_000.0, 30.0),   # 2 + 28000 * 0.001 = 30
            ("mid", 5.0, 45_000.0, 50.0),     # 5 + 45000 * 0.001 = 50
            ("small", 10.0, 65_000.0, 75.0),  # 10 + 65000 * 0.001 = 75
        ],
    )
    def test_v1_anchor_matches_target(self, tier_name, base_bps, size_factor_v1, target_total_bps):
        """v1 at anchor reproduces the calibration design point."""
        order = self.LIQUIDITY * self.ANCHOR_PARTICIPATION
        bps_v1 = compute_slippage_bps(
            order_usd=order,
            liquidity_usd_per_min=self.LIQUIDITY,
            base_bps=base_bps,
            size_factor=size_factor_v1,
            model="v1",
        )
        assert bps_v1 == pytest.approx(target_total_bps, abs=0.01), (
            f"{tier_name} v1 at anchor: expected {target_total_bps}, got {bps_v1}"
        )

    @pytest.mark.parametrize(
        "tier_name,base_bps,size_factor_v2,target_total_bps",
        [
            ("major", 2.0, 885.44, 30.0),
            ("mid", 5.0, 1423.02, 50.0),
            ("small", 10.0, 2055.59, 75.0),
        ],
    )
    def test_v2_anchor_matches_target(self, tier_name, base_bps, size_factor_v2, target_total_bps):
        """v2 at anchor reproduces the calibration design point (anchor parity)."""
        order = self.LIQUIDITY * self.ANCHOR_PARTICIPATION
        bps_v2 = compute_slippage_bps(
            order_usd=order,
            liquidity_usd_per_min=self.LIQUIDITY,
            base_bps=base_bps,
            size_factor=size_factor_v2,
            model="v2",
        )
        assert bps_v2 == pytest.approx(target_total_bps, abs=0.05), (
            f"{tier_name} v2 at anchor: expected {target_total_bps}, got {bps_v2}"
        )

    def test_committed_calibration_v2_anchor_parity_with_v1_baseline(self):
        """The committed costs_calibration.json v2 size_factors must produce the
        same total slippage as the v1 baseline at 0.1% participation. Anchors:
        major=30, mid=50, small=75.

        This is the integration test that asserts the JSON is in sync with the
        formula — if anyone changes either size_factor or the formula without
        re-deriving the other, this test catches it.
        """
        cal = load_calibration()
        order = self.LIQUIDITY * self.ANCHOR_PARTICIPATION

        expected_anchors = {"major": 30.0, "mid": 50.0, "small": 75.0}
        for tier_name, tier_params in cal.tiers.items():
            bps = compute_slippage_bps(
                order_usd=order,
                liquidity_usd_per_min=self.LIQUIDITY,
                base_bps=tier_params.base_bps,
                size_factor=tier_params.size_factor,
                model="v2",
            )
            assert bps == pytest.approx(expected_anchors[tier_name], abs=0.1), (
                f"{tier_name} committed v2 calibration drifts from anchor "
                f"target {expected_anchors[tier_name]}: got {bps}"
            )


# ---------------------------------------------------------------------------
# Non-anchor asymmetry — academic correctness of sqrt vs linear.
# Below anchor (small orders): v2 > v1 (small orders aren't free).
# Above anchor (large orders): v2 < v1 (sqrt is sub-linear vs catastrophic linear).
# ---------------------------------------------------------------------------


class TestNonAnchorAsymmetry:
    """v2 corrects v1 on both ends of the participation curve."""

    LIQUIDITY = 1_000_000.0
    # Use major tier numbers for clarity
    BASE_BPS = 2.0
    SF_V1 = 28_000.0
    SF_V2 = 885.44

    def test_small_participation_v2_charges_more_than_v1(self):
        """At 0.01% participation (10× smaller than anchor), v2 charges more
        than v1. This corrects the 'small orders are free' bias of v1."""
        small_participation = 0.0001  # 10x below anchor
        order = self.LIQUIDITY * small_participation

        bps_v1 = compute_slippage_bps(
            order_usd=order, liquidity_usd_per_min=self.LIQUIDITY,
            base_bps=self.BASE_BPS, size_factor=self.SF_V1, model="v1",
        )
        bps_v2 = compute_slippage_bps(
            order_usd=order, liquidity_usd_per_min=self.LIQUIDITY,
            base_bps=self.BASE_BPS, size_factor=self.SF_V2, model="v2",
        )
        # v1: 2 + 28000 × 0.0001 = 4.8
        # v2: 2 + 885.44 × sqrt(0.0001) = 2 + 885.44 × 0.01 = 10.85
        assert bps_v2 > bps_v1, (
            f"v2 ({bps_v2}) should charge MORE than v1 ({bps_v1}) at 0.01% participation"
        )
        # Tighter assertion: ratio v2/v1 ≈ 2.26 at this participation
        assert bps_v2 / bps_v1 > 1.5

    def test_large_participation_v2_charges_less_than_v1(self):
        """At 1% participation (10× larger than anchor), v2 charges less than
        v1 (sqrt sub-linearity bounds the catastrophic single-trade exposure)."""
        large_participation = 0.01  # 10x above anchor
        order = self.LIQUIDITY * large_participation

        bps_v1 = compute_slippage_bps(
            order_usd=order, liquidity_usd_per_min=self.LIQUIDITY,
            base_bps=self.BASE_BPS, size_factor=self.SF_V1, model="v1",
        )
        bps_v2 = compute_slippage_bps(
            order_usd=order, liquidity_usd_per_min=self.LIQUIDITY,
            base_bps=self.BASE_BPS, size_factor=self.SF_V2, model="v2",
        )
        # v1: 2 + 28000 × 0.01 = 282 (catastrophic, but not yet at extreme cap)
        # v2: 2 + 885.44 × sqrt(0.01) = 2 + 88.54 = 90.5
        assert bps_v2 < bps_v1, (
            f"v2 ({bps_v2}) should charge LESS than v1 ({bps_v1}) at 1% participation"
        )
        assert bps_v1 / bps_v2 > 2.0  # v2 is at least 2× cheaper at high participation

    def test_at_exact_anchor_v1_equals_v2_within_tolerance(self):
        """Re-iteration as direct comparison (the calibration design point)."""
        anchor = 0.001
        order = self.LIQUIDITY * anchor

        bps_v1 = compute_slippage_bps(
            order_usd=order, liquidity_usd_per_min=self.LIQUIDITY,
            base_bps=self.BASE_BPS, size_factor=self.SF_V1, model="v1",
        )
        bps_v2 = compute_slippage_bps(
            order_usd=order, liquidity_usd_per_min=self.LIQUIDITY,
            base_bps=self.BASE_BPS, size_factor=self.SF_V2, model="v2",
        )
        assert bps_v1 == pytest.approx(bps_v2, abs=0.05)


# ---------------------------------------------------------------------------
# Extreme participation cap — protects backtests from non-physical slippage.
# Real execution would refuse a fill at >5% adverse price.
# ---------------------------------------------------------------------------


class TestExtremeParticipationCap:
    """EXTREME_PARTICIPATION_CAP_BPS bounds v2 single-fill cost."""

    def test_cap_value_is_500_bps(self):
        """Sanity: the documented cap is 500 bps (5%)."""
        assert EXTREME_PARTICIPATION_CAP_BPS == 500.0

    def test_extreme_participation_capped_under_v2(self):
        """A trade at 100x participation (catastrophic notional vs liquidity)
        produces capped slippage under v2, not unbounded."""
        # 100% participation = order equals entire 1-minute liquidity
        order = 1_000_000.0
        liquidity = 1_000_000.0

        bps_v2 = compute_slippage_bps(
            order_usd=order,
            liquidity_usd_per_min=liquidity,
            base_bps=10.0,
            size_factor=2055.59,
            model="v2",
        )
        # v2 raw formula: 10 + 2055.59 × sqrt(1.0) = 10 + 2055.59 = 2065.59
        # But cap kicks in at 500
        assert bps_v2 == pytest.approx(500.0, abs=0.01), (
            f"Expected cap at 500 bps; got {bps_v2}"
        )

    def test_just_below_cap_returns_raw_value(self):
        """At a participation level where raw formula gives ~450 bps, no cap
        applied (returns raw value)."""
        # Need: base + size_factor × sqrt(p) ≈ 450
        # With small tier: 10 + 2055.59 × sqrt(p) = 450 → sqrt(p) = 0.214 → p ≈ 0.0458
        liquidity = 1_000_000.0
        order = liquidity * 0.0458

        bps_v2 = compute_slippage_bps(
            order_usd=order,
            liquidity_usd_per_min=liquidity,
            base_bps=10.0,
            size_factor=2055.59,
            model="v2",
        )
        # Approximately 450, NOT capped at 500
        assert bps_v2 < EXTREME_PARTICIPATION_CAP_BPS
        assert bps_v2 == pytest.approx(450.0, rel=0.05)

    def test_v1_not_capped_for_parity_with_legacy_behavior(self):
        """v1 explicitly does NOT apply the extreme participation cap. Legacy
        behavior preserved for parity testing; new code should use v2 default."""
        # At 1% participation with major tier: v1 = 2 + 28000*0.01 = 282 bps
        # Same level v1 with mid: 5 + 45000*0.01 = 455
        # Same level v1 with small: 10 + 65000*0.01 = 660 (would be capped if v2!)
        order = 10_000.0
        liquidity = 1_000_000.0
        bps_v1 = compute_slippage_bps(
            order_usd=order, liquidity_usd_per_min=liquidity,
            base_bps=10.0, size_factor=65_000.0, model="v1",
        )
        assert bps_v1 == pytest.approx(660.0, abs=0.01)
        assert bps_v1 > EXTREME_PARTICIPATION_CAP_BPS  # v1 exceeds the v2 cap


# ---------------------------------------------------------------------------
# Funding-rate accounting — required because epic #338 §8.5 locked SHORT
# bidirectional which requires perps.
# ---------------------------------------------------------------------------


class TestFundingRate:
    """Per-tier conservative funding accounting at 8h intervals."""

    def test_zero_holding_hours_returns_zero(self):
        """Trade closed within same funding interval pays no funding."""
        bps = compute_funding_cost_bps(holding_hours=0.0, funding_rate_bps_per_8h=5.0)
        assert bps == 0.0

    def test_seven_hours_pays_zero_intervals(self):
        """Floor semantics: 7h held < 1 funding interval."""
        bps = compute_funding_cost_bps(holding_hours=7.99, funding_rate_bps_per_8h=5.0)
        assert bps == 0.0

    def test_eight_hours_pays_one_interval(self):
        """Exactly at the funding interval boundary."""
        bps = compute_funding_cost_bps(holding_hours=8.0, funding_rate_bps_per_8h=5.0)
        assert bps == pytest.approx(5.0)

    def test_twenty_four_hours_pays_three_intervals(self):
        """24h ≡ 3 × 8h funding intervals."""
        bps = compute_funding_cost_bps(holding_hours=24.0, funding_rate_bps_per_8h=5.0)
        assert bps == pytest.approx(15.0)

    def test_seventy_two_hours_pays_nine_intervals(self):
        """3 days = 9 funding intervals."""
        bps = compute_funding_cost_bps(holding_hours=72.0, funding_rate_bps_per_8h=2.0)
        assert bps == pytest.approx(18.0)

    def test_negative_rate_taken_as_absolute_value(self):
        """Conservative=True: always charge abs(rate) regardless of sign.
        Real funding can be positive (longs pay) or negative (shorts pay), but
        the v2 backtest baseline assumes worst-case (always pay)."""
        bps = compute_funding_cost_bps(holding_hours=8.0, funding_rate_bps_per_8h=-3.0)
        assert bps == pytest.approx(3.0)

    def test_negative_holding_hours_returns_zero(self):
        """Defensive: nonsensical input doesn't propagate negative cost."""
        bps = compute_funding_cost_bps(holding_hours=-5.0, funding_rate_bps_per_8h=5.0)
        assert bps == 0.0

    def test_nonfinite_holding_hours_returns_zero(self):
        """NaN/inf treated as zero (defensive)."""
        bps_nan = compute_funding_cost_bps(holding_hours=float("nan"), funding_rate_bps_per_8h=5.0)
        bps_inf = compute_funding_cost_bps(holding_hours=float("inf"), funding_rate_bps_per_8h=5.0)
        assert bps_nan == 0.0
        assert bps_inf == 0.0

    def test_direction_aware_mode_not_yet_implemented(self):
        """conservative=False raises until per-bar funding rate is integrated
        in a future version (Phase 1+ or v3)."""
        with pytest.raises(NotImplementedError):
            compute_funding_cost_bps(holding_hours=24.0, funding_rate_bps_per_8h=3.0, conservative=False)


# ---------------------------------------------------------------------------
# DOGE -$30K forensic case — v1 catastrophic vs v2 mitigated.
# Source: R3 audit §4 H8 (`docs/superpowers/specs/es/2026-05-11-strategy-structural-audit.md`).
# v1 produced -$30,489 single-trade cost; v2 must significantly mitigate.
# ---------------------------------------------------------------------------


class TestDogeForensicCase:
    """Reproducer of the H8 audit finding: DOGE single trade at sl=0.7 lost
    $30K on a single bar of catastrophic liquidity drain.

    Mechanics (from audit §4 H8):
    - Tier mid: base=5, size_factor_v1=45000 (v1) or 1423.02 (v2)
    - Bar liquidity proxy: ~$100/min (anomalously thin)
    - Notional under R-multiple sizing with tight SL: ~$21,000 (2× capital)
    - participation = 21000/100 = 210 (21000% — extreme)
    """
    ORDER_USD = 21_000.0
    LIQUIDITY_USD_PER_MIN = 100.0  # the catastrophically thin bar
    BASE_BPS = 5.0  # mid tier
    SF_V1 = 45_000.0
    SF_V2 = 1_423.02

    def test_v1_reproduces_catastrophic_slippage(self):
        """Under v1 linear: slippage_bps = 5 + 45000 × 210 = 9,450,005 bps.
        Not capped → catastrophic cost. Reproduces audit H8."""
        bps_v1 = compute_slippage_bps(
            order_usd=self.ORDER_USD,
            liquidity_usd_per_min=self.LIQUIDITY_USD_PER_MIN,
            base_bps=self.BASE_BPS,
            size_factor=self.SF_V1,
            model="v1",
        )
        # Expected: 5 + 45000 × 210 = 9,450,005
        assert bps_v1 == pytest.approx(9_450_005.0, abs=10.0)
        # Cost in USD: 9.45M bps × 21000 / 10000 = ~$19.8M (per-side; doubles round trip)
        # This is what produced -$30K NET after K-cap (gross was ~-$1500, cost ~-$28500)
        # The audit reports per-bar cost approximation; the function only computes
        # per-fill slippage which can be unboundedly catastrophic under v1.
        cost_usd_per_fill = bps_v1 * self.ORDER_USD / 10_000.0
        assert cost_usd_per_fill > 10_000_000.0  # > $10M cost from one fill

    def test_v2_mitigates_to_extreme_cap(self):
        """Under v2 sqrt + cap: same trade saturates the 500 bps cap, NOT
        the unbounded v1 catastrophe. Audit-grade evidence that v2 fixes H8."""
        bps_v2 = compute_slippage_bps(
            order_usd=self.ORDER_USD,
            liquidity_usd_per_min=self.LIQUIDITY_USD_PER_MIN,
            base_bps=self.BASE_BPS,
            size_factor=self.SF_V2,
            model="v2",
        )
        # v2 raw: 5 + 1423.02 × sqrt(210) = 5 + 1423.02 × 14.49 = 20,625 bps
        # Cap kicks in at 500 → final 500 bps
        assert bps_v2 == pytest.approx(EXTREME_PARTICIPATION_CAP_BPS, abs=0.01)
        cost_usd_per_fill = bps_v2 * self.ORDER_USD / 10_000.0
        # Capped cost: 500 bps × $21000 / 10000 = $1050 per fill
        assert cost_usd_per_fill == pytest.approx(1_050.0, abs=1.0)

    def test_v2_mitigation_ratio_at_least_1000x(self):
        """Cross-check: v2 reduces single-fill cost on this catastrophic bar by
        at least 1000× vs v1. Audit-grade evidence."""
        bps_v1 = compute_slippage_bps(
            order_usd=self.ORDER_USD, liquidity_usd_per_min=self.LIQUIDITY_USD_PER_MIN,
            base_bps=self.BASE_BPS, size_factor=self.SF_V1, model="v1",
        )
        bps_v2 = compute_slippage_bps(
            order_usd=self.ORDER_USD, liquidity_usd_per_min=self.LIQUIDITY_USD_PER_MIN,
            base_bps=self.BASE_BPS, size_factor=self.SF_V2, model="v2",
        )
        ratio = bps_v1 / bps_v2
        assert ratio > 1_000.0, f"v2 should mitigate v1 by ≥1000×; got {ratio:.0f}×"


# ---------------------------------------------------------------------------
# Integration: compute_trade_costs with v2 default + funding
# ---------------------------------------------------------------------------


class TestComputeTradeCostsV2Integration:
    """End-to-end: compute_trade_costs uses v2 by default and accounts for funding."""

    def _params_mid(self):
        return TierParams(
            base_bps=5.0,
            size_factor=1_423.02,
            half_spread_bps=7.5,
            fee_bps_per_side=10.0,
            funding_rate_bps_per_8h=2.0,
        )

    def test_v2_is_the_default_model_for_compute_trade_costs(self):
        """No `model` kwarg → uses v2."""
        c = compute_trade_costs(
            entry_notional_usd=1_000.0,
            exit_notional_usd=1_000.0,
            entry_liquidity_usd_per_min=1_000_000.0,
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=self._params_mid(),
            enable_slippage=True,
            enable_spread=False,
            enable_fees=False,
            enable_funding=False,
        )
        # v2 at 0.1% mid anchor: 5 + 1423.02 × sqrt(0.001) = 5 + 45 = 50 bps
        assert c["entry_slippage_bps"] == pytest.approx(50.0, abs=0.1)
        assert c["exit_slippage_bps"] == pytest.approx(50.0, abs=0.1)

    def test_funding_field_present_in_output_dict(self):
        """funding_cost_bps must be a key in the result dict (even if 0)."""
        c = compute_trade_costs(
            entry_notional_usd=1_000.0,
            exit_notional_usd=1_000.0,
            entry_liquidity_usd_per_min=1_000_000.0,
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=self._params_mid(),
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=False,
        )
        assert "funding_cost_bps" in c
        assert c["funding_cost_bps"] == 0.0

    def test_24h_holding_period_funding_charged(self):
        """24h holding → 3 funding intervals × 2 bps = 6 bps funding cost on mid."""
        c = compute_trade_costs(
            entry_notional_usd=1_000.0,
            exit_notional_usd=1_000.0,
            entry_liquidity_usd_per_min=1_000_000.0,
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=self._params_mid(),
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=True,
            holding_hours=24.0,
        )
        assert c["funding_cost_bps"] == pytest.approx(6.0)
        assert c["total_cost_bps"] == pytest.approx(6.0)

    def test_funding_disabled_returns_zero_funding(self):
        """enable_funding=False overrides positive holding_hours."""
        c = compute_trade_costs(
            entry_notional_usd=1_000.0,
            exit_notional_usd=1_000.0,
            entry_liquidity_usd_per_min=1_000_000.0,
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=self._params_mid(),
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=False,
            holding_hours=24.0,
        )
        assert c["funding_cost_bps"] == 0.0

    def test_zero_holding_hours_no_funding_even_when_enabled(self):
        """enable_funding=True but holding_hours=0 → no funding (within-interval trade)."""
        c = compute_trade_costs(
            entry_notional_usd=1_000.0,
            exit_notional_usd=1_000.0,
            entry_liquidity_usd_per_min=1_000_000.0,
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=self._params_mid(),
            enable_slippage=False, enable_spread=False, enable_fees=False,
            enable_funding=True,
            holding_hours=0.0,
        )
        assert c["funding_cost_bps"] == 0.0

    def test_all_flags_on_v2_with_funding_sums_correctly(self):
        """End-to-end roundtrip with all v2 components active."""
        c = compute_trade_costs(
            entry_notional_usd=1_000.0,
            exit_notional_usd=1_000.0,
            entry_liquidity_usd_per_min=1_000_000.0,
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=self._params_mid(),
            enable_slippage=True,
            enable_spread=True,
            enable_fees=True,
            enable_funding=True,
            holding_hours=16.0,  # 2 funding intervals
        )
        # Entry slip ≈ 50, exit slip ≈ 50 (v2 at 0.1% mid anchor)
        # Entry spread 7.5, exit spread 7.5
        # Round-trip fee: 20 bps
        # Funding: 2 intervals × 2 bps = 4 bps
        # Total: 50 + 50 + 7.5 + 7.5 + 20 + 4 = 139 bps
        expected_total = 50.0 + 50.0 + 7.5 + 7.5 + 20.0 + 4.0
        assert c["total_cost_bps"] == pytest.approx(expected_total, abs=0.5)


# ---------------------------------------------------------------------------
# Defensive: unknown model raises
# ---------------------------------------------------------------------------


class TestModelDispatch:
    def test_unknown_model_raises(self):
        """Anti-typo guard: invalid model string raises rather than silently
        defaulting."""
        with pytest.raises(ValueError, match="Unknown cost model"):
            compute_slippage_bps(
                order_usd=1_000.0,
                liquidity_usd_per_min=1_000_000.0,
                base_bps=5.0,
                size_factor=25_000.0,
                model="v3",  # noqa
            )

    def test_v1_and_v2_both_accepted(self):
        """Both legitimate values dispatch without error."""
        for m in ("v1", "v2"):
            compute_slippage_bps(
                order_usd=1_000.0,
                liquidity_usd_per_min=1_000_000.0,
                base_bps=5.0,
                size_factor=1_000.0,
                model=m,
            )
