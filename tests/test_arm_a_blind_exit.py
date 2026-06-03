import os
import pytest
from tools.arm_a_blind_exit import population
from tools.arm_a_blind_exit.constants import KEEP_SYMBOLS, PAPA_DB, OHLCV_DB

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
