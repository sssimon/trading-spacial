import os
import pytest
from tools.arm_a_blind_exit import population
from tools.arm_a_blind_exit.constants import KEEP_SYMBOLS, PAPA_DB, OHLCV_DB
from tools.arm_a_blind_exit import exit_rules

papa_missing = not os.path.exists(PAPA_DB)

@pytest.mark.skipif(papa_missing, reason="papá's DB not present on this machine")
def test_population_is_the_frozen_27():
    pos, dropped = population.load_population(PAPA_DB, OHLCV_DB)
    assert len(pos) == 27
    assert all(p["symbol"] in KEEP_SYMBOLS for p in pos)
    reasons = sorted(p["exit_reason"] for p in pos)
    assert reasons.count("MANUAL") == 23
    assert reasons.count("SL_HIT") == 4
    for p in pos:
        assert p["qty"] is not None
        assert p["direction"] in ("LONG", "SHORT")
        assert p["entry_ts"] and p["exit_ts"]
    assert sum(d["n"] for d in dropped) == 16


def test_wilder_atr_known_series():
    # 23 bars; TR is constant 2.0 (each bar high-low=2, no gaps) -> ATR=2.0
    bars = [{"high": 12.0, "low": 10.0, "close": 11.0} for _ in range(23)]
    atr = exit_rules.wilder_atr(bars, period=22)
    assert atr == pytest.approx(2.0, abs=1e-9)

def test_wilder_atr_needs_enough_bars():
    bars = [{"high": 1.0, "low": 0.0, "close": 0.5} for _ in range(10)]
    with pytest.raises(ValueError):
        exit_rules.wilder_atr(bars, period=22)
