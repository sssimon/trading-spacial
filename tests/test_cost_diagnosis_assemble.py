import math
import pandas as pd
from tools.cost_diagnosis.live_trades import LiveTrade
from tools.cost_diagnosis.assemble import assemble_per_trade


def _liq(symbol_value):
    idx = pd.date_range("2026-05-01", periods=300, freq="1h", tz="UTC")
    # constant series so liquidity_at is deterministic
    return pd.Series([symbol_value] * 300, index=idx)


def test_assembles_expected_fields():
    t = LiveTrade(
        symbol="AVAXUSDT", direction="SHORT", size_usd=644.0,
        entry_price=20.0, entry_ts="2026-05-10T00:00:00+00:00",
        exit_price=19.0, exit_ts="2026-05-10T05:00:00+00:00", pnl_usd=12.0,
        scan_price=20.1, scan_ts="2026-05-09T23:00:00+00:00",
    )
    liq_map = {"AVAXUSDT": _liq(50_000.0)}
    rows = assemble_per_trade([t], liq_map)
    r = rows[0]
    assert r["tier"] == "mid"
    # observed move = |19-20|/20 * 100 = 5.0%
    assert math.isclose(r["observed_move_pct"], 5.0, rel_tol=1e-9)
    assert math.isclose(r["holding_hours"], 5.0, rel_tol=1e-9)
    # scan-vs-fill slippage = |20.0-20.1|/20.1 * 100
    assert math.isclose(r["scan_fill_slip_pct"], abs(20.0 - 20.1) / 20.1 * 100, rel_tol=1e-9)
    assert "baseline" in r["costs"] and "daily_basis" in r["costs"]
    assert r["costs"]["daily_basis"] < r["costs"]["baseline"]


def test_nan_liquidity_marks_trade_unobservable():
    t = LiveTrade(
        symbol="BTCUSDT", direction="SHORT", size_usd=644.0,
        entry_price=60000.0, entry_ts="2020-01-01T00:00:00+00:00",  # before series
        exit_price=59000.0, exit_ts="2020-01-01T02:00:00+00:00", pnl_usd=9.0,
        scan_price=None, scan_ts=None,
    )
    rows = assemble_per_trade([t], {"BTCUSDT": _liq(1_000_000.0)})
    assert rows[0]["liquidity_unobservable"] is True
