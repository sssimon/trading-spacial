from tools.ks_stress_replay.metrics import evaluate_gate


def test_pareto_dominance_is_strong():
    v1 = {"max_dd": -0.30, "total_pnl": 1000.0}
    v2 = {50: {"max_dd": -0.20, "total_pnl": 1100.0}}  # lower DD AND higher PnL
    verdict, slider = evaluate_gate(v1, v2)
    assert verdict == "STRONG" and slider == 50


def test_dd_first_pass_within_pnl_floor():
    v1 = {"max_dd": -0.30, "total_pnl": 1000.0}
    # DD 0.30 -> 0.22 = 8pp absolute reduction; PnL 950 = 95% of v1 (>=90%).
    v2 = {50: {"max_dd": -0.22, "total_pnl": 950.0}}
    verdict, slider = evaluate_gate(v1, v2)
    assert verdict == "PASS" and slider == 50


def test_dd_reduction_too_small_or_pnl_floor_broken_is_fail():
    v1 = {"max_dd": -0.30, "total_pnl": 1000.0}
    v2 = {
        30: {"max_dd": -0.29, "total_pnl": 1000.0},   # only 1pp / 3.3% reduction
        70: {"max_dd": -0.10, "total_pnl": 700.0},    # big DD cut but PnL 70% < 90%
    }
    verdict, slider = evaluate_gate(v1, v2)
    assert verdict == "FAIL" and slider is None


def test_negative_v1_pnl_floor_uses_absolute_band():
    v1 = {"max_dd": -0.40, "total_pnl": -100.0}
    # v2 gives up <=10% of |v1 pnl| (-110 floor) and cuts DD >=3pp.
    v2 = {50: {"max_dd": -0.30, "total_pnl": -105.0}}
    verdict, slider = evaluate_gate(v1, v2)
    assert verdict == "PASS" and slider == 50
