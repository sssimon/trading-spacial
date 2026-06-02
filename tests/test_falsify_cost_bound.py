"""Unit tests for tools/ks_stress_replay/falsify_cost_bound.py.

Uses synthetic position dicts — CI-runnable without the server DB.
"""
import math
import pytest
from tools.ks_stress_replay.falsify_cost_bound import (
    score_positions, assert_no_sign_inversion, MANDATORY_LOWER_BOUND_BPS,
    EXPECTED_MIN, InsufficientDataError, BoundFalsifiedError,
)


def _pos(symbol, pnl_usd, pnl_pct, size_usd=644.0, liq=1_000_000.0):
    return {"symbol": symbol, "direction": "SHORT", "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct, "size_usd": size_usd, "liquidity_per_min": liq}


class TestFalsifyHarness:
    def test_mandatory_lower_bound_is_external_fee(self):
        # 2 * published taker fee (5.0) = 10.0 RT bps, NOT the model floor.
        assert MANDATORY_LOWER_BOUND_BPS == 10.0

    def test_insufficient_data_aborts(self):
        with pytest.raises(InsufficientDataError):
            assert_no_sign_inversion(score_positions([_pos("AVAXUSDT", 5.0, 0.5)]))

    def test_v3_preserves_winner_sign(self):
        # A winner in price stays a winner after v3 cost (floor-dominated).
        # gross = 20 * 6.0 = 120.0 > NOISE_BAND_USD=5.0 -> symbol is evaluated.
        rows = [_pos("AVAXUSDT", 6.0, 0.5) for _ in range(20)]
        scored = score_positions(rows)
        assert_no_sign_inversion(scored)  # should NOT raise

    def test_inflated_cost_inverts_sign_is_caught(self):
        # Simulate a model that overcharges (inject huge cost) -> inversion -> raises.
        # pnl_usd=0.4 -> gross = 20 * 0.4 = 8.0 > NOISE_BAND_USD=5.0 (evaluated).
        # force_cost_bps=500 -> cost_usd = 500*644/10000 = 32.2 per position.
        # net = 8.0 - 20*32.2 = 8.0 - 644.0 = -636.0 < 0 -> sign inversion caught.
        # (Original spec used pnl_usd=0.2 -> gross=4.0 <= 5.0 noise band -> skipped,
        #  so the test would never raise. Fixed to 0.4 to make gross just above noise.)
        rows = [_pos("AVAXUSDT", 0.4, 0.03) for _ in range(20)]
        scored = score_positions(rows, force_cost_bps=500.0)  # v2-like overcharge
        with pytest.raises(AssertionError, match="sign inversion"):
            assert_no_sign_inversion(scored)

    def test_falsification_error_is_assertionerror(self):
        from tools.ks_stress_replay.falsify_cost_bound import BoundFalsifiedError
        assert issubclass(BoundFalsifiedError, AssertionError)

    def test_nan_pnl_rejected(self):
        with pytest.raises(ValueError, match="non-finite pnl_usd"):
            score_positions([_pos("AVAXUSDT", float("nan"), 0.5)])

    def test_zero_size_rejected(self):
        with pytest.raises(ValueError, match="size_usd"):
            score_positions([_pos("AVAXUSDT", 5.0, 0.5, size_usd=0.0)])

    def test_missing_key_rejected(self):
        with pytest.raises(ValueError, match="missing required keys"):
            score_positions([{"symbol": "AVAXUSDT", "pnl_usd": 5.0}])

    def test_unscored_noise_symbols_reported(self):
        # 20 rows, gross 20*0.1=2.0 < NOISE_BAND 5.0 -> symbol skipped & REPORTED
        rows = [_pos("AVAXUSDT", 0.1, 0.01) for _ in range(20)]
        summary = assert_no_sign_inversion(score_positions(rows))
        assert "AVAXUSDT" in summary["skipped_noise"]
        assert summary["checked"] == []
