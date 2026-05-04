"""Tests for the regime threshold pre-holdout re-tune harness."""
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
        # Threshold configs must NOT pass regime_disabled to simulate_strategy.
        assert "regime_disabled" not in captured
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
        assert "regime_disabled" not in captured

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

    def test_io_error_caught_with_io_prefix_and_logged(self, monkeypatch, stub_frames, caplog):
        import sqlite3 as sq
        def fake_simulate(*args, **kwargs):
            raise sq.DatabaseError("db locked")

        monkeypatch.setattr(harness, "_load_frames", lambda sym, cutoff: stub_frames)
        import backtest
        monkeypatch.setattr(backtest, "simulate_strategy", fake_simulate)

        cfg = {"name": "60_40", "bull_above": 60, "bear_below": 40, "disabled": False}
        with caplog.at_level("ERROR"):
            result = harness._run_one_backtest("BTCUSDT", cfg, datetime(2025, 4, 30, tzinfo=timezone.utc),
                                                app_config={})
        assert result["net_pnl"] == 0.0
        assert result["trades"] == 0
        assert result["error"].startswith("io:")
        assert any("I/O failure" in rec.message for rec in caplog.records)

    def test_data_error_caught_with_data_prefix_and_logged(self, monkeypatch, stub_frames, caplog):
        def fake_simulate(*args, **kwargs):
            raise ValueError("bad input")

        monkeypatch.setattr(harness, "_load_frames", lambda sym, cutoff: stub_frames)
        import backtest
        monkeypatch.setattr(backtest, "simulate_strategy", fake_simulate)

        cfg = {"name": "60_40", "bull_above": 60, "bear_below": 40, "disabled": False}
        with caplog.at_level("WARNING"):
            result = harness._run_one_backtest("BTCUSDT", cfg, datetime(2025, 4, 30, tzinfo=timezone.utc),
                                                app_config={})
        assert result["error"].startswith("data:")
        assert any("data/assertion error" in rec.message for rec in caplog.records)

    def test_programming_errors_propagate(self, monkeypatch, stub_frames):
        """AttributeError / KeyError / TypeError must propagate (not be swallowed)."""
        def fake_simulate(*args, **kwargs):
            raise AttributeError("typo")

        monkeypatch.setattr(harness, "_load_frames", lambda sym, cutoff: stub_frames)
        import backtest
        monkeypatch.setattr(backtest, "simulate_strategy", fake_simulate)

        cfg = {"name": "60_40", "bull_above": 60, "bear_below": 40, "disabled": False}
        with pytest.raises(AttributeError):
            harness._run_one_backtest("BTCUSDT", cfg, datetime(2025, 4, 30, tzinfo=timezone.utc),
                                       app_config={})


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
            "regime_thresholds": {"bull_above": 70, "bear_below": 30},
        }

    def test_regime_params_for_60_40_winner(self, tmp_path):
        path = tmp_path / "regime_params.json"
        harness._write_regime_params(str(path), self._make_agg(winner="60_40"))
        payload = json.loads(path.read_text())
        assert payload == {
            "regime_thresholds": {"bull_above": 60, "bear_below": 40},
        }

    def test_regime_params_for_80_20_winner(self, tmp_path):
        path = tmp_path / "regime_params.json"
        harness._write_regime_params(str(path), self._make_agg(winner="80_20"))
        payload = json.loads(path.read_text())
        assert payload == {
            "regime_thresholds": {"bull_above": 80, "bear_below": 20},
        }

    def test_regime_params_for_no_detector_winner(self, tmp_path):
        path = tmp_path / "regime_params.json"
        harness._write_regime_params(str(path), self._make_agg(winner="no_detector"))
        payload = json.loads(path.read_text())
        assert payload == {
            "regime_disabled": True,
        }

    @pytest.mark.parametrize("winner", ["60_40", "70_30", "80_20", "no_detector"])
    def test_regime_params_spec_compliance_keys(self, tmp_path, winner):
        """Spec compliance — decoupled from impl shape:
          - exactly one of {"regime_thresholds", "regime_disabled"} present (XOR)
          - no other top-level keys
        """
        path = tmp_path / f"p_{winner}.json"
        harness._write_regime_params(str(path), self._make_agg(winner=winner))
        payload = json.loads(path.read_text())
        keys = set(payload.keys())
        has_thresholds = "regime_thresholds" in keys
        has_disabled = "regime_disabled" in keys
        assert has_thresholds ^ has_disabled, (
            f"regime_params must have exactly one of regime_thresholds / regime_disabled; "
            f"got keys={keys}"
        )
        assert keys <= {"regime_thresholds", "regime_disabled"}, (
            f"regime_params must have no extra top-level keys; got {keys}"
        )

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
        overrides = {"BTCUSDT": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0}}
        # ran_at_iso varies per call — must be excluded from byte-comparison.
        m1 = harness._build_manifest(agg=agg, cutoff=cutoff, cutoff_ms=1000,
                                      ohlcv_sha="abc", code_commit="def",
                                      ranges={}, runtime_seconds=1.0,
                                      leakage_check="PASS", symbols=["BTC"],
                                      symbol_overrides=overrides)
        m2 = harness._build_manifest(agg=agg, cutoff=cutoff, cutoff_ms=1000,
                                      ohlcv_sha="abc", code_commit="def",
                                      ranges={}, runtime_seconds=1.0,
                                      leakage_check="PASS", symbols=["BTC"],
                                      symbol_overrides=overrides)
        m1.pop("ran_at_iso")
        m2.pop("ran_at_iso")
        # Verify the new symbol_overrides_sha256 field is present and stable
        assert "symbol_overrides_sha256" in m1
        assert m1["symbol_overrides_sha256"] == m2["symbol_overrides_sha256"]
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
        assert params == {"regime_thresholds": {"bull_above": 60, "bear_below": 40}}

    def test_sanity_check_returns_rc_3_and_refuses_canonical_artefacts(self, monkeypatch, tmp_path):
        """When no_detector wins, main() returns 3 AND does NOT write canonical artefacts."""
        def fake_run(symbol, config, cutoff, app_config=None):
            pnl_map = {"60_40": 50.0, "70_30": 30.0, "80_20": 20.0, "no_detector": 200.0}
            return {"symbol": symbol, "config": config["name"],
                    "net_pnl": pnl_map[config["name"]], "trades": 1, "error": None}

        self._common_stubs(monkeypatch, fake_run)

        rc = harness.main(["--max-date", "2025-04-30", "--out-dir", str(tmp_path)])
        assert rc == 3
        # Canonical artefacts must NOT be written.
        assert not (tmp_path / "regime_params.json").exists()
        assert not (tmp_path / "regime_manifest.json").exists()
        assert not (tmp_path / "regime_report.md").exists()
        # Halted summary IS written for post-mortem.
        assert (tmp_path / "halted_summary.json").exists()
        halted = json.loads((tmp_path / "halted_summary.json").read_text())
        assert halted["reason"] == "sanity_check_fired"
        assert halted["agg"]["winner"] == "no_detector"

    def test_errored_cells_return_rc_4_and_dump_sweep_errors(self, monkeypatch, tmp_path):
        """When any cell errors, main() returns 4 and writes sweep_errors.json — does NOT aggregate."""
        def fake_run(symbol, config, cutoff, app_config=None):
            if symbol == "ETHUSDT" and config["name"] == "70_30":
                return {"symbol": symbol, "config": config["name"],
                        "net_pnl": 0.0, "trades": 0, "error": "io:db locked"}
            return {"symbol": symbol, "config": config["name"],
                    "net_pnl": 100.0, "trades": 1, "error": None}

        self._common_stubs(monkeypatch, fake_run)

        rc = harness.main(["--max-date", "2025-04-30", "--out-dir", str(tmp_path)])
        assert rc == 4
        assert (tmp_path / "sweep_errors.json").exists()
        # Canonical artefacts NOT written.
        assert not (tmp_path / "regime_params.json").exists()
        errors = json.loads((tmp_path / "sweep_errors.json").read_text())
        assert len(errors["errored_cells"]) >= 1
        assert any(c["symbol"] == "ETHUSDT" and c["config"] == "70_30"
                   for c in errors["errored_cells"])

    def test_missing_ohlcv_db_returns_rc_2(self, monkeypatch, tmp_path):
        original_exists = os.path.exists
        monkeypatch.setattr(harness.os.path, "exists",
                            lambda p: False if p == harness.OHLCV_DB else original_exists(p))
        rc = harness.main(["--max-date", "2025-04-30", "--out-dir", str(tmp_path)])
        assert rc == 2

    def test_missing_config_hard_errors(self, monkeypatch, tmp_path):
        """_load_config raises FileNotFoundError when config.json is missing."""
        def fake_run(symbol, config, cutoff, app_config=None):
            return {"symbol": symbol, "config": config["name"],
                    "net_pnl": 0.0, "trades": 0, "error": None}

        original_exists = os.path.exists

        def fake_exists(p):
            if p == harness.OHLCV_DB:
                return True
            if p == os.path.join(harness.REPO_ROOT, "config.json"):
                return False
            return original_exists(p)

        monkeypatch.setattr(harness, "_run_one_backtest", fake_run)
        monkeypatch.setattr(harness.os.path, "exists", fake_exists)

        with pytest.raises(FileNotFoundError, match="config.json not found"):
            harness.main(["--max-date", "2025-04-30", "--out-dir", str(tmp_path)])

    def test_stale_canonical_artefacts_removed_on_rerun(self, monkeypatch, tmp_path):
        """Run-1 success → run-2 halt: stale regime_params.json must NOT survive."""
        # Pre-seed run-1 canonical artefacts.
        (tmp_path / "regime_params.json").write_text('{"stale": "from_run1"}')
        (tmp_path / "regime_manifest.json").write_text('{"stale": "from_run1"}')
        (tmp_path / "regime_report.md").write_text("stale run-1 content")
        (tmp_path / "leftover.tmp").write_text("orphan tmp")

        # Run-2 setup: no_detector wins → halt branch.
        def fake_run(symbol, config, cutoff, app_config=None):
            pnl_map = {"60_40": 50.0, "70_30": 30.0, "80_20": 20.0, "no_detector": 200.0}
            return {"symbol": symbol, "config": config["name"],
                    "net_pnl": pnl_map[config["name"]], "trades": 1, "error": None}

        self._common_stubs(monkeypatch, fake_run)

        rc = harness.main(["--max-date", "2025-04-30", "--out-dir", str(tmp_path)])
        assert rc == 3
        # Stale canonical artefacts MUST be cleaned up.
        assert not (tmp_path / "regime_params.json").exists()
        assert not (tmp_path / "regime_manifest.json").exists()
        assert not (tmp_path / "regime_report.md").exists()
        # Orphan tmp files cleaned.
        assert not (tmp_path / "leftover.tmp").exists()
        # Halted summary IS written.
        assert (tmp_path / "halted_summary.json").exists()

    def test_halted_summary_includes_report_md(self, monkeypatch, tmp_path):
        """halted_summary.json must include the rendered report markdown."""
        def fake_run(symbol, config, cutoff, app_config=None):
            pnl_map = {"60_40": 50.0, "70_30": 30.0, "80_20": 20.0, "no_detector": 200.0}
            return {"symbol": symbol, "config": config["name"],
                    "net_pnl": pnl_map[config["name"]], "trades": 1, "error": None}

        self._common_stubs(monkeypatch, fake_run)
        harness.main(["--max-date", "2025-04-30", "--out-dir", str(tmp_path)])
        halted = json.loads((tmp_path / "halted_summary.json").read_text())
        assert "report_md" in halted
        assert "Pre-holdout Regime Threshold Re-tune Report" in halted["report_md"]

    def test_degenerate_zero_pnl_returns_rc_6_and_refuses_canonical_artefacts(
            self, monkeypatch, tmp_path):
        """When all per-config sums ≈ 0, refuse canonical writes; rc=6, halted_summary present."""
        def fake_run(symbol, config, cutoff, app_config=None):
            return {"symbol": symbol, "config": config["name"],
                    "net_pnl": 0.0, "trades": 0, "error": None}

        self._common_stubs(monkeypatch, fake_run)

        rc = harness.main(["--max-date", "2025-04-30", "--out-dir", str(tmp_path)])
        assert rc == 6
        assert not (tmp_path / "regime_params.json").exists()
        assert not (tmp_path / "regime_manifest.json").exists()
        assert not (tmp_path / "regime_report.md").exists()
        assert (tmp_path / "halted_summary.json").exists()
        halted = json.loads((tmp_path / "halted_summary.json").read_text())
        assert halted["reason"] == "degenerate_zero_pnl"
        assert "report_md" in halted

    def test_sanity_takes_priority_over_degenerate(self, monkeypatch, tmp_path):
        """If both flags would fire (no_detector wins AND all sums tiny), sanity wins (rc=3)."""
        # All cells effectively zero except no_detector winning by 1e-12.
        # In practice all_zero check uses 1e-9 threshold, so 1e-12 still triggers degenerate.
        # We engineer a case where degenerate_zero_pnl=True AND winner=no_detector.
        def fake_run(symbol, config, cutoff, app_config=None):
            # All zero across the board → all_zero=True. Sanity check fires when
            # winner == "no_detector"; with all-zero PnL the winner is lex tie-break
            # which is "60_40", NOT no_detector. So this scenario can't actually
            # produce both flags via the realistic path.
            # For the priority test, we instead verify sanity priority by mocking
            # _aggregate_results to return both flags. Skip this engineered case
            # in favor of a direct unit test on the priority order.
            return {"symbol": symbol, "config": config["name"],
                    "net_pnl": 0.0, "trades": 0, "error": None}

        # Mock _aggregate_results to return both flags simultaneously.
        original_aggregate = harness._aggregate_results

        def both_flags_aggregate(cells):
            agg = original_aggregate(cells)
            agg["winner"] = "no_detector"
            agg["decision_flags"]["sanity_check"] = True
            agg["decision_flags"]["degenerate_zero_pnl"] = True
            return agg

        self._common_stubs(monkeypatch, fake_run)
        monkeypatch.setattr(harness, "_aggregate_results", both_flags_aggregate)

        rc = harness.main(["--max-date", "2025-04-30", "--out-dir", str(tmp_path)])
        assert rc == 3, "sanity_check must take priority over degenerate_zero_pnl"
        halted = json.loads((tmp_path / "halted_summary.json").read_text())
        assert halted["reason"] == "sanity_check_fired"

    def test_rc_5_on_canonical_write_failure(self, monkeypatch, tmp_path):
        """Simulate _atomic_write_text raising on canonical writes; verify rc=5 +
        raw_results.json fallback."""
        def fake_run(symbol, config, cutoff, app_config=None):
            return {"symbol": symbol, "config": config["name"],
                    "net_pnl": 100.0, "trades": 1, "error": None}

        self._common_stubs(monkeypatch, fake_run)

        def boom(*args, **kwargs):
            raise OSError("disk full")

        # Force the first canonical write (regime_report.md) to fail.
        monkeypatch.setattr(harness, "_atomic_write_text", boom)

        rc = harness.main(["--max-date", "2025-04-30", "--out-dir", str(tmp_path)])
        assert rc == 5
        # raw_results.json fallback (via _emergency_write) should land somewhere —
        # either at out_dir or in /tmp. Verify out_dir path first; if absent the
        # _emergency_write fallback in /tmp is acceptable per its contract.
        # Here the OSError("disk full") only affects _atomic_write_text; the
        # raw_results _emergency_write call uses _atomic_write_json which is NOT
        # patched, so it should succeed at out_dir.
        assert (tmp_path / "raw_results.json").exists()
        raw = json.loads((tmp_path / "raw_results.json").read_text())
        assert "cells" in raw
        assert "agg" in raw

    def test_degenerate_flag_rendered_in_report(self):
        """_build_report must include degenerate_zero_pnl in the decision flags section."""
        agg = {
            "winner": "60_40", "winner_pnl": 0.0, "runner_up": "70_30",
            "runner_up_pnl": 0.0, "winner_margin_pct": 0.0,
            "per_config_pnl": {"60_40": 0.0, "70_30": 0.0, "80_20": 0.0, "no_detector": 0.0},
            "per_config_trades": {"60_40": 0, "70_30": 0, "80_20": 0, "no_detector": 0},
            "decision_flags": {
                "change_detection": False,
                "sanity_check": False,
                "stability_check": True,
                "degenerate_zero_pnl": True,
            },
        }
        cells = [{"symbol": "BTCUSDT", "config": c, "net_pnl": 0.0, "trades": 0, "error": None}
                 for c in ("60_40", "70_30", "80_20", "no_detector")]
        cutoff = datetime(2025, 4, 30, tzinfo=timezone.utc)
        ranges = {"BTCUSDT": {tf: {"min_ts_iso": "—", "max_ts_iso": "—", "count": 0}
                              for tf in harness.TIMEFRAMES}}
        report = harness._build_report(agg=agg, cells=cells, cutoff=cutoff,
                                        ranges=ranges, runtime_seconds=1.0,
                                        symbols=["BTCUSDT"])
        assert "Degenerate zero-pnl" in report
        assert "lex tie-break" in report


class TestEmergencyWrite:
    def test_emergency_write_falls_back_to_tmp_on_oserror(self, monkeypatch, tmp_path, caplog):
        """When primary atomic write fails, _emergency_write must fall back to /tmp."""
        bad_path = str(tmp_path / "nonexistent_subdir" / "x.json")  # missing parent
        with caplog.at_level("ERROR"):
            harness._emergency_write(bad_path, {"k": "v"}, kind="test_kind")
        # Primary path didn't exist, fallback should have been attempted.
        assert any("Failed to write test_kind" in r.message for r in caplog.records)

    def test_emergency_write_does_not_raise_on_unserializable(self, tmp_path):
        """Non-serializable payloads must not crash the harness."""
        path = str(tmp_path / "out.json")
        # Class instances aren't JSON serializable by default; default=str in
        # the fallback handles them. Primary atomic_write_json will fail; that's
        # the path we're exercising.
        class Foo:
            pass
        # Should not raise, even if the fallback also can't serialize.
        harness._emergency_write(path, {"obj": Foo()}, kind="test_kind")


class TestLoadConfigEmptyOverrides:
    def test_empty_overrides_raises(self, tmp_path, monkeypatch):
        """_load_config raises ValueError when symbol_overrides is empty."""
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({"webhook_url": "x", "symbol_overrides": {}}))
        monkeypatch.setattr(harness, "REPO_ROOT", str(tmp_path))
        with pytest.raises(ValueError, match="symbol_overrides"):
            harness._load_config()

    def test_missing_overrides_key_raises(self, tmp_path, monkeypatch):
        """_load_config raises ValueError when symbol_overrides key absent."""
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({"webhook_url": "x"}))
        monkeypatch.setattr(harness, "REPO_ROOT", str(tmp_path))
        with pytest.raises(ValueError, match="symbol_overrides"):
            harness._load_config()

    def test_non_empty_overrides_passes(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({
            "symbol_overrides": {"BTCUSDT": {"atr_sl_mult": 1.0}}
        }))
        monkeypatch.setattr(harness, "REPO_ROOT", str(tmp_path))
        cfg = harness._load_config()
        assert "BTCUSDT" in cfg["symbol_overrides"]


class TestRegimeKwargErrorPropagates:
    """RegimeKwargError is a programming-error exception; the harness's narrow
    catch (ValueError, AssertionError) must NOT swallow it — it must propagate
    out of _run_one_backtest so the operator sees the bug."""

    def test_regime_kwarg_error_propagates_from_run_one_backtest(self, monkeypatch):
        from backtest import RegimeKwargError
        idx = pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC")
        stub_frames = {
            "df1h": pd.DataFrame({"close": [1.0]*10, "volume": [1.0]*10}, index=idx),
            "df4h": pd.DataFrame({"close": [1.0]*10, "volume": [1.0]*10}, index=idx),
            "df5m": pd.DataFrame({"close": [1.0]*10, "volume": [1.0]*10}, index=idx),
            "df1d": pd.DataFrame({"close": [1.0]*10}, index=idx),
            "df1d_btc": pd.DataFrame({"close": [1.0]*10}, index=idx),
            "df_fng": pd.DataFrame({"fng": [50]*10}, index=idx),
            "df_funding": pd.DataFrame({"rate": [0.0]*10}, index=idx),
        }
        monkeypatch.setattr(harness, "_load_frames", lambda sym, cutoff: stub_frames)

        def fake_simulate(*args, **kwargs):
            raise RegimeKwargError("bad combo")

        import backtest
        monkeypatch.setattr(backtest, "simulate_strategy", fake_simulate)

        cfg = {"name": "60_40", "bull_above": 60, "bear_below": 40, "disabled": False}
        with pytest.raises(RegimeKwargError):
            harness._run_one_backtest("BTCUSDT", cfg, datetime(2025, 4, 30, tzinfo=timezone.utc),
                                       app_config={"symbol_overrides": {"BTCUSDT": {}}})


class TestAggregateZeroPnl:
    def test_degenerate_zero_pnl_flag(self):
        cells = [{"symbol": "X", "config": c, "net_pnl": 0, "trades": 0, "error": None}
                 for c in ("60_40", "70_30", "80_20", "no_detector")]
        agg = harness._aggregate_results(cells)
        assert agg["decision_flags"]["degenerate_zero_pnl"] is True

    def test_degenerate_zero_pnl_flag_inactive_when_any_nonzero(self):
        cells = [{"symbol": "X", "config": c, "net_pnl": pnl, "trades": 1, "error": None}
                 for c, pnl in [("60_40", 100), ("70_30", 50), ("80_20", 30), ("no_detector", 20)]]
        agg = harness._aggregate_results(cells)
        assert agg["decision_flags"]["degenerate_zero_pnl"] is False
