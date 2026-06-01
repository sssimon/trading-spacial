import json
from tools.cost_diagnosis.run import write_reports, CORRECTIONS_FOR_REPORT


def test_write_reports_emits_both_files_and_branch(tmp_path):
    per_trade = [{
        "symbol": "AVAXUSDT", "direction": "SHORT", "size_usd": 644.0, "tier": "mid",
        "pnl_usd": 10.0, "entry_ts": "2026-05-10T00:00:00+00:00",
        "exit_ts": "2026-05-10T05:00:00+00:00", "observed_move_pct": 0.5,
        "holding_hours": 5.0, "liq_entry": 50000.0, "liq_exit": 50000.0,
        "liquidity_unobservable": False, "scan_fill_slip_pct": 0.4,
        "costs": {"baseline": 90.0, "daily_basis": 8.0},
    }]
    branch, winning = write_reports(per_trade, str(tmp_path),
                                    corrections=[("baseline", 1.0, 1.0), ("daily_basis", 1440.0, 1.0)])
    assert branch == "RE-ANCHOR" and "daily_basis" in winning
    findings = (tmp_path / "findings.md").read_text(encoding="utf-8")
    assert "RE-ANCHOR" in findings and "over-charge" in findings.lower()
    rows = json.loads((tmp_path / "per_trade.json").read_text(encoding="utf-8"))
    assert rows[0]["symbol"] == "AVAXUSDT"


def test_unobservable_trades_excluded_from_reconcile(tmp_path):
    per_trade = [
        {"symbol": "BTCUSDT", "tier": "major", "pnl_usd": 5.0, "observed_move_pct": 0.5,
         "liquidity_unobservable": True, "scan_fill_slip_pct": None, "costs": {},
         "direction": "SHORT", "size_usd": 644.0, "entry_ts": "x", "exit_ts": "y",
         "holding_hours": 2.0, "liq_entry": float("nan"), "liq_exit": float("nan")},
        {"symbol": "AVAXUSDT", "tier": "mid", "pnl_usd": 10.0, "observed_move_pct": 0.5,
         "liquidity_unobservable": False, "scan_fill_slip_pct": 0.4,
         "costs": {"baseline": 90.0, "daily_basis": 8.0},
         "direction": "SHORT", "size_usd": 644.0, "entry_ts": "a", "exit_ts": "b",
         "holding_hours": 5.0, "liq_entry": 50000.0, "liq_exit": 50000.0},
    ]
    branch, winning = write_reports(per_trade, str(tmp_path),
                                    corrections=[("baseline", 1.0, 1.0), ("daily_basis", 1440.0, 1.0)])
    # only the observable AVAX trade drives the verdict
    assert branch == "RE-ANCHOR"


def test_per_trade_json_has_no_raw_nan(tmp_path):
    # unobservable trade carries NaN liquidity; the written JSON must be strict-valid.
    per_trade = [{
        "symbol": "BTCUSDT", "tier": "major", "pnl_usd": 5.0, "observed_move_pct": 0.5,
        "liquidity_unobservable": True, "scan_fill_slip_pct": None, "costs": {},
        "direction": "SHORT", "size_usd": 644.0, "entry_ts": "x", "exit_ts": "y",
        "holding_hours": 2.0, "liq_entry": float("nan"), "liq_exit": float("nan"),
    }, {
        "symbol": "AVAXUSDT", "tier": "mid", "pnl_usd": 10.0, "observed_move_pct": 0.5,
        "liquidity_unobservable": False, "scan_fill_slip_pct": 0.4,
        "costs": {"baseline": 90.0, "daily_basis": 8.0},
        "direction": "SHORT", "size_usd": 644.0, "entry_ts": "a", "exit_ts": "b",
        "holding_hours": 5.0, "liq_entry": 50000.0, "liq_exit": 50000.0,
    }]
    write_reports(per_trade, str(tmp_path),
                  corrections=[("baseline", 1.0, 1.0), ("daily_basis", 1440.0, 1.0)])
    raw = (tmp_path / "per_trade.json").read_text(encoding="utf-8")
    assert "NaN" not in raw  # strict JSON: no bare NaN token
    import json
    parsed = json.loads(raw)  # must parse
    # the unobservable trade's NaN liquidity became null
    assert parsed[0]["liq_entry"] is None
