import json
import pytest
from tools.cost_diagnosis.live_trades import load_live_trades, LiveTrade


def _write(tmp_path, rows):
    p = tmp_path / "live.json"
    p.write_text(json.dumps(rows), encoding="utf-8")
    return str(p)


def test_loads_and_parses_rows(tmp_path):
    rows = [{
        "id": 1, "symbol": "AVAXUSDT", "direction": "SHORT", "size_usd": 644.0,
        "qty": 30.0, "entry_price": 21.5, "entry_ts": "2026-05-21T21:21:40+00:00",
        "exit_price": 21.0, "exit_ts": "2026-05-22T03:00:00+00:00", "pnl_usd": 12.3,
        "pnl_pct": 0.9, "scan_id": 99, "scan_price": 21.6, "scan_ts": "2026-05-21T21:00:00+00:00",
    }]
    trades = load_live_trades(_write(tmp_path, rows))
    assert len(trades) == 1
    t = trades[0]
    assert isinstance(t, LiveTrade)
    assert t.symbol == "AVAXUSDT" and t.size_usd == 644.0 and t.scan_price == 21.6


def test_missing_required_field_raises(tmp_path):
    rows = [{"id": 2, "symbol": "BTCUSDT", "direction": "SHORT"}]  # missing prices etc.
    with pytest.raises(ValueError, match="missing"):
        load_live_trades(_write(tmp_path, rows))


def test_null_scan_price_is_allowed(tmp_path):
    rows = [{
        "id": 3, "symbol": "BTCUSDT", "direction": "SHORT", "size_usd": 644.0,
        "qty": 0.01, "entry_price": 60000.0, "entry_ts": "2026-05-21T00:00:00+00:00",
        "exit_price": 60100.0, "exit_ts": "2026-05-21T02:00:00+00:00", "pnl_usd": -1.2,
        "pnl_pct": -0.1, "scan_id": None, "scan_price": None, "scan_ts": None,
    }]
    trades = load_live_trades(_write(tmp_path, rows))
    assert trades[0].scan_price is None
