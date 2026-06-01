import math
from tools.cost_diagnosis.recompute import model_cost_bps, CORRECTIONS


def test_baseline_major_matches_hand_calc():
    # BTC tier=major: base_bps=2, size_factor=885.44, half_spread=1.5, fee_per_side=10.
    # size=644, liq=2_000_000/min => participation=644/2e6=3.22e-4, sqrt=0.017944.
    # slip/fill = 2 + 885.44*0.017944 = 17.886; round-trip slip = 35.77.
    # spread round-trip = 3.0; fee round-trip = 20.0; funding (hold<8h) = 0.
    # total ~ 58.77 bps.
    bps = model_cost_bps("BTCUSDT", 644.0, 2_000_000.0, 2_000_000.0, 5.0)
    assert math.isclose(bps, 58.77, abs_tol=0.5)


def test_daily_basis_reduces_slippage():
    base = model_cost_bps("AVAXUSDT", 644.0, 50_000.0, 50_000.0, 3.0)
    daily = model_cost_bps("AVAXUSDT", 644.0, 50_000.0, 50_000.0, 3.0, liq_mult=1440.0)
    assert daily < base


def test_size_factor_divisor_reduces_slippage():
    base = model_cost_bps("RUNEUSDT", 644.0, 30_000.0, 30_000.0, 3.0)
    div = model_cost_bps("RUNEUSDT", 644.0, 30_000.0, 30_000.0, 3.0, sf_div=37.95)
    assert div < base


def test_corrections_table_shape():
    names = [c[0] for c in CORRECTIONS]
    assert names[0] == "baseline"
    assert "daily_basis" in names and "sf_div_37.95" in names and "both_37.95" in names
