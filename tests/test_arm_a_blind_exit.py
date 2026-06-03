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

def test_wilder_atr_uses_gap_component():
    # Alternating bars so the TR is driven by |high - prev_close| (the gap branch),
    # not just high-low. Odd cur (prev close 11): TR=1. Even cur (prev close 10):
    # TR=max(12-11, |12-10|=2, |11-10|)=2. 22 pairs alternate 1,2 -> mean ATR=1.5.
    bars = []
    for i in range(23):
        if i % 2 == 0:
            bars.append({"high": 12.0, "low": 11.0, "close": 11.0})
        else:
            bars.append({"high": 11.0, "low": 10.0, "close": 10.0})
    assert exit_rules.wilder_atr(bars, period=22) == pytest.approx(1.5, abs=1e-9)


def _bar(t, o, h, l, c):
    return {"open_time": t, "open": o, "high": h, "low": l, "close": c}

def test_chandelier_long_trails_up_and_stops():
    atr = 1.0
    path = [
        _bar(0,   100, 101, 99,  100),
        _bar(300, 100, 110, 100, 109),   # peak 110 -> stop = 110 - 3*1 = 107
        _bar(600, 109, 109, 106, 108),   # low 106 <= 107 -> exit at 107
    ]
    px, ts, cap = exit_rules.simulate_chandelier(
        path, "LONG", entry_price=100.0, atr=atr, fill="pessimistic")
    assert px == pytest.approx(107.0)
    assert ts == 600
    assert cap is False

def test_chandelier_short_mirrors():
    atr = 1.0
    path = [
        _bar(0,   100, 101, 99,  100),
        _bar(300, 100, 100, 90,  91),    # trough 90 -> stop = 90 + 3 = 93
        _bar(600, 91,  94,  91,  92),    # high 94 >= 93 -> exit at 93
    ]
    px, ts, cap = exit_rules.simulate_chandelier(
        path, "SHORT", entry_price=100.0, atr=atr, fill="pessimistic")
    assert px == pytest.approx(93.0)
    assert cap is False

def test_chandelier_pessimistic_vs_optimistic_same_bar():
    atr = 1.0
    path = [
        _bar(0,   100, 101, 99,  100),
        _bar(300, 100, 108, 104, 105),
    ]
    px_p, _, _ = exit_rules.simulate_chandelier(path, "LONG", 100.0, atr, fill="pessimistic")
    px_o, _, _ = exit_rules.simulate_chandelier(path, "LONG", 100.0, atr, fill="optimistic")
    assert px_p == pytest.approx(105.0)
    assert px_o == pytest.approx(105.0)
    assert px_p <= px_o + 1e-9

def test_chandelier_hits_cap_when_never_stopped():
    # monotone tiny uptrend that never retraces 3*ATR; path spans 250h but the cap is
    # 200h, so the rule falls through and exits at the LAST bar WITHIN the cap window.
    atr = 1.0
    from tools.arm_a_blind_exit.constants import MAX_HOLD_H
    path = [_bar(i * 300_000, 100 + i * 0.01, 100 + i * 0.01 + 0.005,
                 100 + i * 0.01 - 0.001, 100 + i * 0.01) for i in range(3000)]
    px, ts, cap = exit_rules.simulate_chandelier(path, "LONG", 100.0, atr, fill="pessimistic")
    cap_ms = path[0]["open_time"] + int(MAX_HOLD_H * 3600 * 1000)
    within = [b for b in path if b["open_time"] <= cap_ms]
    assert cap is True
    assert px == pytest.approx(within[-1]["close"])
    assert ts == within[-1]["open_time"]


def test_giveback_long_exits_after_retrace():
    # bar0 high=entry (no favorable move yet, stop stays unarmed); bar1 sets peak 110
    # with low 108 (above giveback 106.2 so it does not trigger same-bar); bar2 retraces.
    path = [
        _bar(0,   100, 100, 99,  100),   # high == entry -> no favorable move
        _bar(300, 100, 110, 108, 109),   # peak 110, fav move = 10, giveback stop = 106.2
        _bar(600, 109, 109, 105, 106),   # low 105 <= 106.2 -> exit at 106.2
    ]
    px, ts, cap = exit_rules.simulate_giveback(path, "LONG", entry_price=100.0, fill="pessimistic")
    assert px == pytest.approx(106.2)
    assert cap is False


def test_giveback_short_exits_after_retrace():
    # SHORT mirror: favorable = price down. bar0 low=entry (unarmed); bar1 trough 90
    # (move 10, giveback stop 90 + 0.38*10 = 93.8), high 100 not tested same-bar under
    # pessimistic; bar2 high 94 >= 93.8 -> exit at 93.8.
    path = [
        _bar(0,   100, 100, 100, 100),   # low == entry -> no favorable move
        _bar(300, 100, 100, 90,  91),    # trough 90, giveback stop = 93.8
        _bar(600, 91,  94,  91,  92),    # high 94 >= 93.8 -> exit at 93.8
    ]
    px, ts, cap = exit_rules.simulate_giveback(path, "SHORT", entry_price=100.0, fill="pessimistic")
    assert px == pytest.approx(93.8)
    assert cap is False


def test_giveback_never_favorable_rides_to_cap():
    # price never exceeds entry -> no favorable move -> giveback stop never arms -> cap.
    path = [_bar(i * 300_000, 100, 100, 99.8, 99.9) for i in range(5)]
    px, ts, cap = exit_rules.simulate_giveback(path, "LONG", 100.0, fill="pessimistic")
    assert cap is True
    assert px == pytest.approx(99.9)          # close of the last bar within cap
    assert ts == 4 * 300_000


from tools.arm_a_blind_exit import evaluate

def test_gross_pnl_long_and_short():
    assert evaluate.gross_pnl(qty=2.0, entry=100.0, exit=110.0, direction="LONG") == pytest.approx(20.0)
    assert evaluate.gross_pnl(qty=2.0, entry=100.0, exit=110.0, direction="SHORT") == pytest.approx(-20.0)
    assert evaluate.gross_pnl(qty=2.0, entry=100.0, exit=90.0,  direction="SHORT") == pytest.approx(20.0)

def test_liquidity_proxy_formula():
    bars = [{"open_time": i * 3_600_000, "close": 100.0, "volume": 60.0} for i in range(200)]
    series = evaluate.liquidity_series(bars)
    assert series[-1][1] == pytest.approx(100.0)
    assert evaluate.liquidity_at(series, ts_ms=200 * 3_600_000) == pytest.approx(100.0)

def test_v3_recost_is_positive_and_uses_v3():
    cost = evaluate.recost_v3(
        symbol="BTCUSDT", entry_notional=10_000.0, exit_notional=10_000.0,
        entry_liq=5_000_000.0, exit_liq=5_000_000.0, holding_hours=24.0)
    assert cost > 0.0
    assert cost < 10_000.0
