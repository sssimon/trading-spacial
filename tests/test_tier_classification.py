import math
import pytest
from btc_scanner import _classify_tune_result


class TestClassifyTuneResult:
    def test_dedicated_solid(self):
        assert _classify_tune_result(100, 1.50) == "dedicated"

    def test_dedicated_exact_boundary(self):
        assert _classify_tune_result(30, 1.30) == "dedicated"

    def test_fallback_pf_just_below_dedicated(self):
        assert _classify_tune_result(30, 1.29999) == "fallback"

    def test_fallback_pf_at_lower_edge(self):
        assert _classify_tune_result(30, 1.00) == "fallback"

    def test_fallback_count_plenty(self):
        assert _classify_tune_result(500, 1.15) == "fallback"

    def test_disabled_by_count_below_threshold(self):
        assert _classify_tune_result(29, 1.50) == "disabled"

    def test_disabled_by_pf_below_1(self):
        assert _classify_tune_result(100, 0.95) == "disabled"

    def test_disabled_by_both(self):
        assert _classify_tune_result(5, 0.5) == "disabled"

    def test_pf_infinity_with_enough_samples_is_dedicated(self):
        assert _classify_tune_result(50, math.inf) == "dedicated"

    def test_pf_none_is_disabled(self):
        assert _classify_tune_result(50, None) == "disabled"

    def test_count_zero_is_disabled(self):
        assert _classify_tune_result(0, 1.50) == "disabled"

    def test_pf_nan_is_disabled(self):
        assert _classify_tune_result(50, float("nan")) == "disabled"
