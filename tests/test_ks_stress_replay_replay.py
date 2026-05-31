from datetime import datetime, timedelta, timezone
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


class _OrderRecordingOverlay:
    """Records the UTC instant of every close in the order replay processes them.

    Lets us observe chronological ordering independent of pnl commutativity.
    """

    def __init__(self):
        self.close_instants: list[datetime] = []

    def decide(self, symbol, entry_ts):
        return (False, 1.0)

    def record_close(self, symbol, exit_ts, pnl_usd, exit_reason):
        # exit_ts is the ISO string the engine emitted; parse back to a UTC instant.
        self.close_instants.append(
            datetime.fromisoformat(exit_ts).astimezone(timezone.utc)
        )


def test_mixed_tz_offsets_close_in_true_chronological_order():
    """A base stream mixing tz offsets must sort by UTC instant, not by string.

    Two closes at distinct UTC instants are expressed with offsets chosen so
    lexicographic string sort reverses their true chronological order:

        close A: exit at UTC 2022-01-03T01:00:00, written as +02:00 -> "03:00+02:00"
        close B: exit at UTC 2022-01-03T02:00:00, written as +00:00 -> "02:00+00:00"

    String sort: "...02:00+00:00" < "...03:00+02:00" => B before A (WRONG).
    UTC sort:    01:00Z < 02:00Z                     => A before B (CORRECT).
    """
    a_exit = datetime(2022, 1, 3, 3, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    b_exit = datetime(2022, 1, 3, 2, 0, 0, tzinfo=timezone.utc)
    # Sanity: A is truly earlier than B in real time.
    assert a_exit.astimezone(timezone.utc) < b_exit.astimezone(timezone.utc)

    base = {
        "AAA": [{
            "entry_time": datetime(2022, 1, 2, tzinfo=timezone.utc),
            "exit_time": a_exit, "pnl_usd": 10.0, "exit_reason": "TP",
        }],
        "BBB": [{
            "entry_time": datetime(2022, 1, 2, tzinfo=timezone.utc),
            "exit_time": b_exit, "pnl_usd": -10.0, "exit_reason": "SL",
        }],
    }
    overlay = _OrderRecordingOverlay()
    replay(base, overlay, capital_base=1000.0)

    # Closes must be recorded in true chronological (UTC) order: A (01:00Z) then B (02:00Z).
    assert overlay.close_instants == sorted(overlay.close_instants), (
        f"closes recorded out of chronological order: {overlay.close_instants}"
    )
