"""Selection-world provenance — see docs/superpowers/specs/2026-06-02-cost-model-provenance-design.md."""
import pytest


class TestCalibrationIdentity:
    def test_active_cost_model_id_shape(self):
        from backtest_costs import active_cost_model_id
        active_model, cal_hash = active_cost_model_id()
        assert active_model == "v3"               # main costs_calibration.json
        assert isinstance(cal_hash, str) and len(cal_hash) == 64   # sha256 hex

    def test_identity_hash_ignores_prose_changes(self):
        from backtest_costs import load_calibration, calibration_identity_hash
        import dataclasses
        cal = load_calibration()
        h1 = calibration_identity_hash(cal)
        cal2 = dataclasses.replace(cal, sources={"x": "different prose"},
                                   sensitivity_note="totally different", model="reworded")
        assert calibration_identity_hash(cal2) == h1

    def test_identity_hash_changes_on_selector_change(self):
        from backtest_costs import load_calibration, calibration_identity_hash
        import dataclasses
        cal = load_calibration()
        h1 = calibration_identity_hash(cal)
        cal2 = dataclasses.replace(cal, global_=dataclasses.replace(cal.global_, Y_impact_constant=99.0))
        assert calibration_identity_hash(cal2) != h1

    def test_v2_sibling_hash_differs_from_v3(self):
        from backtest_costs import load_calibration, calibration_identity_hash
        v3 = calibration_identity_hash(load_calibration())
        v2 = calibration_identity_hash(load_calibration(path="costs_calibration.v2.json"))
        assert v2 != v3

    def test_identity_hash_covers_all_dataclass_fields(self):
        # Structural guard: if a future selector field is added to TierParams or
        # GlobalParams, calibration_identity_hash must capture it. This test fails
        # (forcing an update to the payload) rather than letting two calibrations
        # that differ only in the new field collide silently.
        import dataclasses
        from backtest_costs import TierParams, GlobalParams
        tier_fields = {f.name for f in dataclasses.fields(TierParams)}
        global_fields = {f.name for f in dataclasses.fields(GlobalParams)}
        # The payload-covered field sets (keep in sync with calibration_identity_hash):
        covered_tier = {"base_bps", "size_factor", "half_spread_bps", "fee_bps_per_side",
                        "funding_rate_bps_per_8h", "stress_mult", "sigma_daily_bps"}
        covered_global = {"Y_impact_constant", "total_cost_cap_bps",
                          "liquidity_fallback_floor_bps", "v_daily_minutes_per_day"}
        assert tier_fields == covered_tier, (
            f"TierParams fields changed: {tier_fields ^ covered_tier} — update "
            "calibration_identity_hash payload + this guard")
        assert global_fields == covered_global, (
            f"GlobalParams fields changed: {global_fields ^ covered_global} — update "
            "calibration_identity_hash payload + this guard")

    def test_identity_hash_changes_on_tier_change(self):
        from backtest_costs import load_calibration, calibration_identity_hash
        import dataclasses
        cal = load_calibration()
        h1 = calibration_identity_hash(cal)
        cal3 = dataclasses.replace(cal, tiers={
            **cal.tiers, "major": dataclasses.replace(cal.tiers["major"], stress_mult=99.0)})
        assert calibration_identity_hash(cal3) != h1
