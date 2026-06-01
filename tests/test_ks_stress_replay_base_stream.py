from datetime import datetime, timezone
from unittest.mock import patch
from tools.ks_stress_replay.base_stream import (
    HOLDOUT_CUTOFF, truncate_at_bankruptcy, flag_bankruptcies,
)


def _tr(entry, reason="TP", pnl=10.0):
    return {
        "entry_time": datetime.fromisoformat(entry).replace(tzinfo=timezone.utc),
        "exit_time": datetime.fromisoformat(entry).replace(tzinfo=timezone.utc),
        "pnl_usd": pnl, "exit_reason": reason,
    }


def test_holdout_cutoff_is_holdout_start():
    assert HOLDOUT_CUTOFF == datetime(2025, 4, 30, tzinfo=timezone.utc)


def test_truncate_at_bankruptcy_drops_post_bankrupt_trades():
    trades = [_tr("2022-01-01"), _tr("2022-01-02", "BANKRUPT", 0.0), _tr("2022-01-03")]
    out = truncate_at_bankruptcy(trades)
    assert len(out) == 2
    assert out[-1]["exit_reason"] == "BANKRUPT"


def test_flag_bankruptcies_lists_affected_symbols():
    stream = {
        "BTCUSDT": [_tr("2022-01-01")],
        "JUPUSDT": [_tr("2022-01-01"), _tr("2022-01-02", "BANKRUPT", 0.0)],
    }
    assert flag_bankruptcies(stream) == ["JUPUSDT"]
