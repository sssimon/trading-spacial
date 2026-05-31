from datetime import datetime, timezone
from tools.ks_stress_replay.overlays import NoneOverlay
from tools.ks_stress_replay.replay import replay


def _trade(entry, exit_, pnl, reason="TP"):
    return {
        "entry_time": datetime.fromisoformat(entry).replace(tzinfo=timezone.utc),
        "exit_time": datetime.fromisoformat(exit_).replace(tzinfo=timezone.utc),
        "pnl_usd": pnl, "exit_reason": reason,
    }


def test_none_overlay_realizes_all_pnl_and_tracks_dd():
    base = {
        "BTCUSDT": [
            _trade("2022-01-01T00:00:00", "2022-01-02T00:00:00", 100.0),
            _trade("2022-01-03T00:00:00", "2022-01-04T00:00:00", -300.0, "SL"),
        ],
    }
    res = replay(base, NoneOverlay(), capital_base=1000.0)
    assert res["total_pnl"] == -200.0
    assert res["final_equity"] == 800.0
    # peak 1100 after first close, trough 800 => DD = (800-1100)/1100
    assert abs(res["max_dd"] - ((800.0 - 1100.0) / 1100.0)) < 1e-9
    assert res["taken"] == 2 and res["skipped"] == 0


def test_closes_processed_in_timestamp_order_across_symbols():
    # ETH closes between BTC's entry and close => interleaving matters.
    base = {
        "BTCUSDT": [_trade("2022-01-01T00:00:00", "2022-01-10T00:00:00", 50.0)],
        "ETHUSDT": [_trade("2022-01-02T00:00:00", "2022-01-03T00:00:00", -100.0, "SL")],
    }
    res = replay(base, NoneOverlay(), capital_base=1000.0)
    # Equity path: -100 (ETH close on 01-03) then +50 (BTC close on 01-10).
    # Trough at 900 after ETH close => max_dd = (900-1000)/1000 = -0.1
    assert abs(res["max_dd"] - (-0.1)) < 1e-9
    assert res["final_equity"] == 950.0
