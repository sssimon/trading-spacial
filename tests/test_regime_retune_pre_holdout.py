"""Tests for the A.4-1.5 regime threshold pre-holdout re-tune harness."""
import json
import os
from datetime import datetime, timezone

import pandas as pd
import pytest

from tools import regime_retune_pre_holdout as harness


class TestGrid:
    def test_grid_has_exactly_4_configs(self):
        assert len(harness.GRID) == 4

    def test_grid_locked_by_historical_record(self):
        # Per spec D9 §2.10 + commit bf581f1 body
        assert harness.GRID == [
            {"name": "60_40",       "bull_above": 60,   "bear_below": 40,   "disabled": False},
            {"name": "70_30",       "bull_above": 70,   "bear_below": 30,   "disabled": False},
            {"name": "80_20",       "bull_above": 80,   "bear_below": 20,   "disabled": False},
            {"name": "no_detector", "bull_above": None, "bear_below": None, "disabled": True},
        ]


class TestSliceBelowCutoff:
    def test_slice_strict_less_than(self):
        idx = pd.date_range("2025-04-29 23:55", periods=4, freq="5min", tz="UTC")
        df = pd.DataFrame({"close": [1, 2, 3, 4]}, index=idx)
        cutoff = datetime(2025, 4, 30, tzinfo=timezone.utc)
        sliced = harness._slice_below_cutoff(df, cutoff)
        assert len(sliced) == 1
        assert sliced.index[0] == pd.Timestamp("2025-04-29 23:55", tz="UTC")

    def test_slice_empty_input(self):
        df = pd.DataFrame({"close": []})
        cutoff = datetime(2025, 4, 30, tzinfo=timezone.utc)
        out = harness._slice_below_cutoff(df, cutoff)
        assert out.empty

    def test_slice_all_after_cutoff_returns_empty(self):
        idx = pd.date_range("2025-04-30 00:00", periods=2, freq="5min", tz="UTC")
        df = pd.DataFrame({"close": [1, 2]}, index=idx)
        cutoff = datetime(2025, 4, 30, tzinfo=timezone.utc)
        out = harness._slice_below_cutoff(df, cutoff)
        assert out.empty

    def test_slice_naive_index_with_aware_cutoff(self):
        # Index without timezone (some loaders strip tz); cutoff has tz.
        idx = pd.date_range("2025-04-29 12:00", periods=3, freq="1h")
        df = pd.DataFrame({"close": [1, 2, 3]}, index=idx)
        cutoff = datetime(2025, 4, 30, tzinfo=timezone.utc)
        out = harness._slice_below_cutoff(df, cutoff)
        assert len(out) == 3  # all bars before cutoff


class TestVerifyNoLeakage:
    def test_pass_when_all_below_cutoff(self):
        ranges = {
            "BTCUSDT": {"1h": {"max_ts_ms": 1000, "min_ts_ms": 100, "count": 10}},
        }
        assert harness._verify_no_leakage(ranges, cutoff_ms=2000) == "PASS"

    def test_raises_when_leakage_present(self):
        ranges = {
            "BTCUSDT": {"1h": {"max_ts_ms": 3000, "min_ts_ms": 100, "count": 10}},
        }
        with pytest.raises(AssertionError, match="no-leakage violation"):
            harness._verify_no_leakage(ranges, cutoff_ms=2000)

    def test_pass_when_count_zero(self):
        ranges = {
            "BTCUSDT": {"1h": {"max_ts_ms": None, "min_ts_ms": None, "count": 0}},
        }
        assert harness._verify_no_leakage(ranges, cutoff_ms=2000) == "PASS"


class TestRunOneBacktest:
    @pytest.fixture
    def stub_frames(self):
        # Non-empty frames so the early-empty guard doesn't short-circuit.
        idx = pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC")
        return {
            "df1h": pd.DataFrame({"close": [1.0]*10, "volume": [1.0]*10}, index=idx),
            "df4h": pd.DataFrame({"close": [1.0]*10, "volume": [1.0]*10}, index=idx),
            "df5m": pd.DataFrame({"close": [1.0]*10, "volume": [1.0]*10}, index=idx),
            "df1d": pd.DataFrame({"close": [1.0]*10}, index=idx),
            "df1d_btc": pd.DataFrame({"close": [1.0]*10}, index=idx),
            "df_fng": pd.DataFrame({"fng": [50]*10}, index=idx),
            "df_funding": pd.DataFrame({"rate": [0.0]*10}, index=idx),
        }

    def test_disabled_config_passes_regime_disabled_true(self, monkeypatch, stub_frames):
        captured = {}

        def fake_simulate(*args, **kwargs):
            captured.update(kwargs)
            return [], None  # (trades, equity)

        monkeypatch.setattr(harness, "_load_frames", lambda sym, cutoff: stub_frames)
        # simulate_strategy is imported inside _run_one_backtest, so patch backtest module.
        import backtest
        monkeypatch.setattr(backtest, "simulate_strategy", fake_simulate)

        cfg = {"name": "no_detector", "bull_above": None, "bear_below": None, "disabled": True}
        result = harness._run_one_backtest("BTCUSDT", cfg, datetime(2025, 4, 30, tzinfo=timezone.utc),
                                            app_config={"symbol_overrides": {}})
        assert result["net_pnl"] == 0.0
        assert result["trades"] == 0
        assert captured.get("regime_disabled") is True
        assert "regime_thresholds" not in captured

    def test_60_40_config_passes_regime_thresholds_60_40(self, monkeypatch, stub_frames):
        captured = {}

        def fake_simulate(*args, **kwargs):
            captured.update(kwargs)
            return [{"pnl_usd": 100.0}, {"pnl_usd": -25.5}], None

        monkeypatch.setattr(harness, "_load_frames", lambda sym, cutoff: stub_frames)
        import backtest
        monkeypatch.setattr(backtest, "simulate_strategy", fake_simulate)

        cfg = {"name": "60_40", "bull_above": 60, "bear_below": 40, "disabled": False}
        result = harness._run_one_backtest("BTCUSDT", cfg, datetime(2025, 4, 30, tzinfo=timezone.utc),
                                            app_config={"symbol_overrides": {}})
        assert result["net_pnl"] == 74.5
        assert result["trades"] == 2
        assert captured.get("regime_disabled") is False or "regime_disabled" not in captured
        assert captured.get("regime_thresholds") == (60, 40)

    def test_70_30_config_forwards_thresholds(self, monkeypatch, stub_frames):
        captured = {}

        def fake_simulate(*args, **kwargs):
            captured.update(kwargs)
            return [], None

        monkeypatch.setattr(harness, "_load_frames", lambda sym, cutoff: stub_frames)
        import backtest
        monkeypatch.setattr(backtest, "simulate_strategy", fake_simulate)

        cfg = {"name": "70_30", "bull_above": 70, "bear_below": 30, "disabled": False}
        harness._run_one_backtest("BTCUSDT", cfg, datetime(2025, 4, 30, tzinfo=timezone.utc),
                                   app_config={"symbol_overrides": {}})
        assert captured.get("regime_thresholds") == (70, 30)

    def test_empty_frames_short_circuit(self, monkeypatch):
        empty_frames = {
            "df1h": pd.DataFrame({"close": []}),
            "df4h": pd.DataFrame({"close": []}),
            "df5m": pd.DataFrame({"close": []}),
            "df1d": pd.DataFrame({"close": []}),
            "df1d_btc": pd.DataFrame({"close": []}),
            "df_fng": pd.DataFrame({"fng": []}),
            "df_funding": pd.DataFrame({"rate": []}),
        }
        monkeypatch.setattr(harness, "_load_frames", lambda sym, cutoff: empty_frames)

        cfg = {"name": "60_40", "bull_above": 60, "bear_below": 40, "disabled": False}
        result = harness._run_one_backtest("BTCUSDT", cfg, datetime(2025, 4, 30, tzinfo=timezone.utc),
                                            app_config={})
        assert result["net_pnl"] == 0.0
        assert result["trades"] == 0
        assert result["error"] == "empty_ohlcv_below_cutoff"

    def test_simulate_exception_caught_and_logged(self, monkeypatch, stub_frames):
        def fake_simulate(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(harness, "_load_frames", lambda sym, cutoff: stub_frames)
        import backtest
        monkeypatch.setattr(backtest, "simulate_strategy", fake_simulate)

        cfg = {"name": "60_40", "bull_above": 60, "bear_below": 40, "disabled": False}
        result = harness._run_one_backtest("BTCUSDT", cfg, datetime(2025, 4, 30, tzinfo=timezone.utc),
                                            app_config={})
        assert result["net_pnl"] == 0.0
        assert result["trades"] == 0
        assert "boom" in result["error"]


class TestAggregate:
    def test_aggregate_picks_winner(self):
        cells = [
            {"symbol": "BTC", "config": "60_40", "net_pnl": 100.0, "trades": 5, "error": None},
            {"symbol": "ETH", "config": "60_40", "net_pnl": 50.0,  "trades": 3, "error": None},
            {"symbol": "BTC", "config": "70_30", "net_pnl": 120.0, "trades": 4, "error": None},
            {"symbol": "ETH", "config": "70_30", "net_pnl": 60.0,  "trades": 3, "error": None},
            {"symbol": "BTC", "config": "80_20", "net_pnl": 80.0,  "trades": 2, "error": None},
            {"symbol": "ETH", "config": "80_20", "net_pnl": 40.0,  "trades": 2, "error": None},
            {"symbol": "BTC", "config": "no_detector", "net_pnl": 70.0, "trades": 6, "error": None},
            {"symbol": "ETH", "config": "no_detector", "net_pnl": 30.0, "trades": 4, "error": None},
        ]
        agg = harness._aggregate_results(cells)
        assert agg["per_config_pnl"]["60_40"]       == 150.0
        assert agg["per_config_pnl"]["70_30"]       == 180.0
        assert agg["per_config_pnl"]["80_20"]       == 120.0
        assert agg["per_config_pnl"]["no_detector"] == 100.0
        assert agg["winner"] == "70_30"
        assert agg["runner_up"] == "60_40"
        assert abs(agg["winner_margin_pct"] - (30.0 / 180.0 * 100)) < 1e-6

    def test_decision_flag_change_detection(self):
        cells = [{"symbol": "X", "config": c, "net_pnl": pnl, "trades": 1, "error": None}
                 for c, pnl in [("60_40", 50), ("70_30", 100), ("80_20", 30), ("no_detector", 20)]]
        agg = harness._aggregate_results(cells)
        assert agg["decision_flags"]["change_detection"] is True
        assert agg["winner"] == "70_30"

    def test_decision_flag_sanity_check_fires_on_no_detector_winner(self):
        cells = [{"symbol": "X", "config": c, "net_pnl": pnl, "trades": 1, "error": None}
                 for c, pnl in [("60_40", 50), ("70_30", 30), ("80_20", 20), ("no_detector", 200)]]
        agg = harness._aggregate_results(cells)
        assert agg["decision_flags"]["sanity_check"] is True
        assert agg["winner"] == "no_detector"

    def test_decision_flag_stability_check_fires_within_5_pct(self):
        # winner=180, runner_up=178 → margin = 2/180 = 1.11% < 5%
        cells = [{"symbol": "X", "config": c, "net_pnl": pnl, "trades": 1, "error": None}
                 for c, pnl in [("60_40", 178), ("70_30", 180), ("80_20", 100), ("no_detector", 50)]]
        agg = harness._aggregate_results(cells)
        assert agg["decision_flags"]["stability_check"] is True

    def test_decision_flag_stability_check_inactive_when_margin_large(self):
        cells = [{"symbol": "X", "config": c, "net_pnl": pnl, "trades": 1, "error": None}
                 for c, pnl in [("60_40", 100), ("70_30", 200), ("80_20", 50), ("no_detector", 30)]]
        agg = harness._aggregate_results(cells)
        assert agg["decision_flags"]["stability_check"] is False

    def test_60_40_winner_no_change_flag(self):
        cells = [{"symbol": "X", "config": c, "net_pnl": pnl, "trades": 1, "error": None}
                 for c, pnl in [("60_40", 200), ("70_30", 100), ("80_20", 50), ("no_detector", 30)]]
        agg = harness._aggregate_results(cells)
        assert agg["decision_flags"]["change_detection"] is False
        assert agg["winner"] == "60_40"

    def test_tie_break_on_lex_order(self):
        # All equal → winner is lex-first ("60_40")
        cells = [{"symbol": "X", "config": c, "net_pnl": 100, "trades": 1, "error": None}
                 for c in ("60_40", "70_30", "80_20", "no_detector")]
        agg = harness._aggregate_results(cells)
        assert agg["winner"] == "60_40"

    def test_zero_winner_pnl_yields_zero_margin(self):
        cells = [{"symbol": "X", "config": c, "net_pnl": 0, "trades": 0, "error": None}
                 for c in ("60_40", "70_30", "80_20", "no_detector")]
        agg = harness._aggregate_results(cells)
        assert agg["winner_margin_pct"] == 0.0


class TestArtefactWriters:
    def _make_agg(self, winner="70_30", winner_pnl=180.0):
        return {
            "winner": winner,
            "winner_pnl": winner_pnl,
            "runner_up": "60_40",
            "runner_up_pnl": 150.0,
            "winner_margin_pct": (winner_pnl - 150.0) / abs(winner_pnl) * 100 if winner_pnl else 0.0,
            "per_config_pnl": {"60_40": 150.0, "70_30": 180.0, "80_20": 120.0, "no_detector": 100.0},
            "per_config_trades": {"60_40": 8, "70_30": 7, "80_20": 4, "no_detector": 10},
            "decision_flags": {"change_detection": True, "sanity_check": False, "stability_check": False},
        }

    def test_regime_params_for_threshold_winner(self, tmp_path):
        path = tmp_path / "regime_params.json"
        harness._write_regime_params(str(path), self._make_agg(winner="70_30"))
        payload = json.loads(path.read_text())
        assert payload == {
            "format_version": 1,
            "regime_thresholds": {"bull_above": 70, "bear_below": 30},
        }

    def test_regime_params_for_60_40_winner(self, tmp_path):
        path = tmp_path / "regime_params.json"
        harness._write_regime_params(str(path), self._make_agg(winner="60_40"))
        payload = json.loads(path.read_text())
        assert payload == {
            "format_version": 1,
            "regime_thresholds": {"bull_above": 60, "bear_below": 40},
        }

    def test_regime_params_for_80_20_winner(self, tmp_path):
        path = tmp_path / "regime_params.json"
        harness._write_regime_params(str(path), self._make_agg(winner="80_20"))
        payload = json.loads(path.read_text())
        assert payload == {
            "format_version": 1,
            "regime_thresholds": {"bull_above": 80, "bear_below": 20},
        }

    def test_regime_params_for_no_detector_winner(self, tmp_path):
        path = tmp_path / "regime_params.json"
        harness._write_regime_params(str(path), self._make_agg(winner="no_detector"))
        payload = json.loads(path.read_text())
        assert payload == {
            "format_version": 1,
            "regime_disabled": True,
        }

    def test_regime_params_byte_deterministic_across_runs(self, tmp_path):
        agg = self._make_agg(winner="60_40")
        path1 = tmp_path / "p1.json"
        path2 = tmp_path / "p2.json"
        harness._write_regime_params(str(path1), agg)
        harness._write_regime_params(str(path2), agg)
        assert path1.read_bytes() == path2.read_bytes()

    def test_manifest_byte_deterministic_across_runs(self, tmp_path):
        agg = self._make_agg(winner="60_40")
        cutoff = datetime(2025, 4, 30, tzinfo=timezone.utc)
        # ran_at_iso varies per call — must be excluded from byte-comparison.
        m1 = harness._build_manifest(agg=agg, cutoff=cutoff, cutoff_ms=1000,
                                      ohlcv_sha="abc", code_commit="def",
                                      ranges={}, runtime_seconds=1.0,
                                      leakage_check="PASS", symbols=["BTC"])
        m2 = harness._build_manifest(agg=agg, cutoff=cutoff, cutoff_ms=1000,
                                      ohlcv_sha="abc", code_commit="def",
                                      ranges={}, runtime_seconds=1.0,
                                      leakage_check="PASS", symbols=["BTC"])
        # Strip ran_at_iso for the comparison
        m1.pop("ran_at_iso")
        m2.pop("ran_at_iso")
        path1 = tmp_path / "m1.json"
        path2 = tmp_path / "m2.json"
        harness._atomic_write_json(str(path1), m1)
        harness._atomic_write_json(str(path2), m2)
        assert path1.read_bytes() == path2.read_bytes()

    def test_report_renders_winner_marker_and_flags(self, tmp_path):
        agg = self._make_agg(winner="70_30")
        cells = [
            {"symbol": "BTCUSDT", "config": "60_40", "net_pnl": 100.0, "trades": 4, "error": None},
            {"symbol": "BTCUSDT", "config": "70_30", "net_pnl": 120.0, "trades": 3, "error": None},
            {"symbol": "BTCUSDT", "config": "80_20", "net_pnl": 80.0, "trades": 2, "error": None},
            {"symbol": "BTCUSDT", "config": "no_detector", "net_pnl": 70.0, "trades": 5, "error": None},
        ]
        cutoff = datetime(2025, 4, 30, tzinfo=timezone.utc)
        ranges = {"BTCUSDT": {tf: {"min_ts_iso": "2023-01-01", "max_ts_iso": "2025-04-29",
                                     "count": 100} for tf in harness.TIMEFRAMES}}
        report = harness._build_report(agg=agg, cells=cells, cutoff=cutoff,
                                        ranges=ranges, runtime_seconds=42.0, symbols=["BTCUSDT"])
        assert "**winner**" in report
        assert "70_30" in report
        assert "CHANGE detection" in report
        assert "Sanity check" in report
        assert "Stability check" in report
        assert "BTCUSDT" in report
        assert "JUPUSDT" in report  # caveat mentioned


class TestCLI:
    def test_max_date_required(self):
        with pytest.raises(SystemExit):
            harness.main([])

    def _common_stubs(self, monkeypatch, fake_run):
        """Stub the slow / IO-bound parts of main() so tests can exercise control flow."""
        original_exists = os.path.exists  # capture before patching

        monkeypatch.setattr(harness, "_run_one_backtest", fake_run)
        monkeypatch.setattr(harness, "_per_symbol_data_ranges",
                            lambda *a, **kw: {sym: {} for sym in harness._get_symbols()})
        monkeypatch.setattr(harness, "_verify_no_leakage", lambda *a, **kw: "PASS")
        monkeypatch.setattr(harness, "_sha256_file", lambda *a, **kw: "deadbeef")
        monkeypatch.setattr(harness, "_resolve_git_commit", lambda: "abcdef0")
        monkeypatch.setattr(harness, "_load_config", lambda: {"symbol_overrides": {}})
        # Force the OHLCV_DB existence check to pass without reaching the filesystem.
        monkeypatch.setattr(harness.os.path, "exists",
                            lambda p: True if p == harness.OHLCV_DB else original_exists(p))

    def test_full_run_with_stubs(self, monkeypatch, tmp_path):
        """End-to-end CLI run with stubbed _run_one_backtest. 60_40 wins."""
        def fake_run(symbol, config, cutoff, app_config=None):
            pnl_map = {"60_40": 200.0, "70_30": 100.0, "80_20": 50.0, "no_detector": 30.0}
            return {"symbol": symbol, "config": config["name"],
                    "net_pnl": pnl_map[config["name"]], "trades": 1, "error": None}

        self._common_stubs(monkeypatch, fake_run)

        rc = harness.main(["--max-date", "2025-04-30", "--out-dir", str(tmp_path)])
        assert rc == 0
        assert (tmp_path / "regime_params.json").exists()
        assert (tmp_path / "regime_manifest.json").exists()
        assert (tmp_path / "regime_report.md").exists()

        params = json.loads((tmp_path / "regime_params.json").read_text())
        assert params == {"format_version": 1,
                          "regime_thresholds": {"bull_above": 60, "bear_below": 40}}

    def test_sanity_check_returns_rc_3(self, monkeypatch, tmp_path):
        """When no_detector wins, main() returns 3 (HALT signal)."""
        def fake_run(symbol, config, cutoff, app_config=None):
            pnl_map = {"60_40": 50.0, "70_30": 30.0, "80_20": 20.0, "no_detector": 200.0}
            return {"symbol": symbol, "config": config["name"],
                    "net_pnl": pnl_map[config["name"]], "trades": 1, "error": None}

        self._common_stubs(monkeypatch, fake_run)

        rc = harness.main(["--max-date", "2025-04-30", "--out-dir", str(tmp_path)])
        assert rc == 3

    def test_missing_ohlcv_db_returns_rc_2(self, monkeypatch, tmp_path):
        original_exists = os.path.exists
        monkeypatch.setattr(harness.os.path, "exists",
                            lambda p: False if p == harness.OHLCV_DB else original_exists(p))
        rc = harness.main(["--max-date", "2025-04-30", "--out-dir", str(tmp_path)])
        assert rc == 2
