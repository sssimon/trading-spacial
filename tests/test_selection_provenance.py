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
