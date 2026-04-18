import pytest
import os
import json
import tempfile
from datetime import datetime, timezone
from auto_tune import (
    calculate_periods, generate_combos, should_recommend, GRID,
    generate_report, write_config_proposed, apply_config,
    load_config, get_current_params, build_telegram_message,
)


class TestCalculatePeriods:
    def test_periods_from_fixed_date(self):
        today = datetime(2026, 4, 18, tzinfo=timezone.utc)
        train_start, train_end, val_start, val_end = calculate_periods(today)
        assert train_start.year == 2025
        assert train_start.month == 1
        assert train_end.year == 2026
        assert train_end.month == 1
        assert val_start == train_end
        assert val_end == today

    def test_periods_lengths(self):
        today = datetime(2026, 4, 18, tzinfo=timezone.utc)
        train_start, train_end, val_start, val_end = calculate_periods(today)
        train_days = (train_end - train_start).days
        val_days = (val_end - val_start).days
        assert 350 <= train_days <= 370
        assert 85 <= val_days <= 95


class TestGridCombos:
    def test_combo_count(self):
        combos = generate_combos()
        assert len(combos) == 105

    def test_combo_keys(self):
        combos = generate_combos()
        for combo in combos:
            assert "atr_sl_mult" in combo
            assert "atr_tp_mult" in combo
            assert "atr_be_mult" in combo


class TestShouldRecommend:
    def test_rejects_below_improvement(self):
        assert should_recommend(10000, 11000, 60, 1.2) is False  # +10% < 15%

    def test_accepts_above_improvement(self):
        assert should_recommend(10000, 12000, 60, 1.2) is True  # +20%

    def test_rejects_insufficient_trades(self):
        assert should_recommend(10000, 12000, 30, 1.2) is False  # 30 < 50

    def test_accepts_sufficient_trades(self):
        assert should_recommend(10000, 12000, 55, 1.2) is True

    def test_rejects_low_pf(self):
        assert should_recommend(10000, 12000, 60, 1.05) is False  # PF < 1.1

    def test_accepts_good_pf(self):
        assert should_recommend(10000, 12000, 60, 1.15) is True

    def test_rejects_negative_with_bad_pf(self):
        assert should_recommend(-5000, -4000, 60, 0.9) is False

    def test_handles_zero_current(self):
        assert should_recommend(0, 2000, 60, 1.2) is True


class TestGenerateReport:
    def test_report_has_summary(self):
        results = [
            {"symbol": "BTCUSDT", "recommendation": "KEEP",
             "current_params": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5},
             "current_val_pnl": 3000, "proposed_params": None, "proposal_detail": None},
            {"symbol": "DOGEUSDT", "recommendation": "CHANGE",
             "current_params": {"atr_sl_mult": 0.7, "atr_tp_mult": 4.0, "atr_be_mult": 1.5},
             "current_val_pnl": 4000,
             "proposed_params": {"atr_sl_mult": 0.5, "atr_tp_mult": 3.0, "atr_be_mult": 2.0},
             "proposal_detail": {"val_pnl": 5200, "val_pf": 1.28, "train_pnl": 9800,
                                 "val_trades": 25, "total_trades": 87, "improvement_pct": 30.0,
                                 "params": {"atr_sl_mult": 0.5, "atr_tp_mult": 3.0, "atr_be_mult": 2.0}}},
        ]
        report = generate_report(results, elapsed_seconds=120)
        assert "Auto-Tune Report" in report
        assert "DOGEUSDT" in report
        assert "BTCUSDT" in report

    def test_report_no_changes(self):
        results = [
            {"symbol": "BTCUSDT", "recommendation": "KEEP",
             "current_params": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5},
             "current_val_pnl": 3000, "proposed_params": None, "proposal_detail": None},
        ]
        report = generate_report(results, elapsed_seconds=60)
        assert "0" in report


class TestConfigProposed:
    def test_no_file_when_no_changes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            results = [{"recommendation": "KEEP"}]
            path = write_config_proposed(results, {}, output_dir=tmpdir)
            assert path is None

    def test_creates_file_when_changes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            results = [{
                "symbol": "DOGEUSDT", "recommendation": "CHANGE",
                "proposed_params": {"atr_sl_mult": 0.5, "atr_tp_mult": 3.0, "atr_be_mult": 2.0},
            }]
            config = {"symbol_overrides": {"DOGEUSDT": {"atr_sl_mult": 0.7}}}
            path = write_config_proposed(results, config, output_dir=tmpdir)
            assert path is not None
            with open(path) as f:
                proposed = json.load(f)
            assert proposed["symbol_overrides"]["DOGEUSDT"]["atr_sl_mult"] == 0.5


class TestApplyConfig:
    def test_apply_creates_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.json")
            proposed_path = os.path.join(tmpdir, "config_proposed.json")

            original = {"symbol_overrides": {"BTCUSDT": {"atr_sl_mult": 1.0}}}
            proposed = {"symbol_overrides": {"BTCUSDT": {"atr_sl_mult": 0.7}}}

            with open(config_path, "w") as f:
                json.dump(original, f)
            with open(proposed_path, "w") as f:
                json.dump(proposed, f)

            backup_path = apply_config(config_path, proposed_path, confirm=True)
            assert backup_path is not None
            assert os.path.exists(backup_path)

            with open(config_path) as f:
                updated = json.load(f)
            assert updated["symbol_overrides"]["BTCUSDT"]["atr_sl_mult"] == 0.7

            with open(backup_path) as f:
                backup = json.load(f)
            assert backup["symbol_overrides"]["BTCUSDT"]["atr_sl_mult"] == 1.0


class TestGetCurrentParams:
    def test_defaults_when_no_overrides(self):
        params = get_current_params("BTCUSDT", {})
        assert params == {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5}

    def test_reads_from_overrides(self):
        config = {"symbol_overrides": {"BTCUSDT": {"atr_sl_mult": 0.7, "atr_tp_mult": 3.0}}}
        params = get_current_params("BTCUSDT", config)
        assert params["atr_sl_mult"] == 0.7
        assert params["atr_tp_mult"] == 3.0
        assert params["atr_be_mult"] == 1.5  # default

    def test_handles_false_override(self):
        config = {"symbol_overrides": {"BTCUSDT": False}}
        params = get_current_params("BTCUSDT", config)
        assert params == {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5}


class TestBuildTelegramMessage:
    def test_message_with_changes(self):
        results = [{
            "symbol": "DOGEUSDT", "recommendation": "CHANGE",
            "proposed_params": {"atr_sl_mult": 0.5, "atr_tp_mult": 3.0, "atr_be_mult": 2.0},
            "proposal_detail": {"improvement_pct": 30.0},
        }]
        msg = build_telegram_message(results)
        assert "1 changes recommended" in msg
        assert "DOGEUSDT" in msg

    def test_message_no_changes(self):
        results = [{"symbol": "BTCUSDT", "recommendation": "KEEP"}]
        msg = build_telegram_message(results)
        assert "0 changes recommended" in msg
        assert "No parameter changes needed" in msg
