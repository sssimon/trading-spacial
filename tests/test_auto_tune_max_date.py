"""Tests for the ``--max-date`` cutoff path in auto_tune (epic A.4-1, #250).

Covers the slicing helper, seed initializer, the new CLI flag, and the
``cutoff`` propagation through ``run_backtest_with_params``. Tests that
require a populated ``data/ohlcv.db`` are skipped when the file is
absent (matching the project convention from
``tests/test_backtest_refactor_parity.py``).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

import auto_tune

OHLCV_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "ohlcv.db",
)


@pytest.fixture
def fixed_cutoff():
    return datetime(2025, 4, 30, tzinfo=timezone.utc)


def _ohlcv_df(timestamps: list[datetime]) -> pd.DataFrame:
    """Build a minimal OHLCV-shaped frame for slicing tests."""
    naive = [t.replace(tzinfo=None) if t.tzinfo else t for t in timestamps]
    idx = pd.DatetimeIndex(naive, name="ts")
    return pd.DataFrame(
        {
            "open": [100.0] * len(timestamps),
            "high": [101.0] * len(timestamps),
            "low": [99.0] * len(timestamps),
            "close": [100.5] * len(timestamps),
            "volume": [1.0] * len(timestamps),
        },
        index=idx,
    )


class TestSliceBelowCutoff:
    def test_returns_empty_unchanged(self, fixed_cutoff):
        df = pd.DataFrame()
        out = auto_tune._slice_below_cutoff(df, fixed_cutoff.replace(tzinfo=None), "BTC", "df1h")
        assert out.empty

    def test_returns_none_unchanged(self, fixed_cutoff):
        out = auto_tune._slice_below_cutoff(None, fixed_cutoff.replace(tzinfo=None), "BTC", "df1h")
        assert out is None

    def test_drops_bars_at_or_after_cutoff(self, fixed_cutoff):
        timestamps = [
            datetime(2025, 4, 28),
            datetime(2025, 4, 29),
            datetime(2025, 4, 30),  # exactly at cutoff — must drop
            datetime(2025, 5, 1),   # after cutoff — must drop
        ]
        df = _ohlcv_df(timestamps)
        sliced = auto_tune._slice_below_cutoff(df, fixed_cutoff.replace(tzinfo=None), "BTC", "df1h")
        assert len(sliced) == 2
        assert sliced.index.max() == datetime(2025, 4, 29)

    def test_keeps_all_when_all_below_cutoff(self, fixed_cutoff):
        timestamps = [datetime(2025, 4, 28), datetime(2025, 4, 29)]
        df = _ohlcv_df(timestamps)
        sliced = auto_tune._slice_below_cutoff(df, fixed_cutoff.replace(tzinfo=None), "BTC", "df1h")
        assert len(sliced) == 2

    def test_returns_df_unchanged_when_index_not_datetime(self, fixed_cutoff):
        df = pd.DataFrame({"a": [1, 2, 3]}, index=[10, 20, 30])
        out = auto_tune._slice_below_cutoff(df, fixed_cutoff.replace(tzinfo=None), "BTC", "df1h")
        # Non-datetime index → no slicing, return as-is.
        assert out is df


class TestInitializeSeed:
    def test_uses_config_seed(self):
        seed = auto_tune.initialize_seed({"auto_tune": {"seed": 1234}})
        assert seed == 1234
        # Verify both RNGs were actually seeded
        a = random.random()
        b = float(np.random.random())
        # Re-seed and re-draw — must match
        auto_tune.initialize_seed({"auto_tune": {"seed": 1234}})
        assert random.random() == a
        assert float(np.random.random()) == b

    def test_default_seed_is_42(self):
        assert auto_tune.DEFAULT_SEED == 42
        seed = auto_tune.initialize_seed({})
        assert seed == 42

    def test_default_seed_when_subkey_missing(self):
        seed = auto_tune.initialize_seed({"auto_tune": {}})
        assert seed == 42

    def test_seed_is_coerced_to_int(self):
        seed = auto_tune.initialize_seed({"auto_tune": {"seed": "7"}})
        assert seed == 7


class TestMaxDateCli:
    def _build_parser(self):
        # Mirror the argparse construction in auto_tune.main(). Keeping
        # it inline lets the test cover the flag definition directly
        # without invoking the full main() side-effect path.
        parser = argparse.ArgumentParser()
        parser.add_argument("--symbol", type=str)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--max-date", type=str, default=None)
        return parser

    def test_flag_default_is_none(self):
        parser = self._build_parser()
        args = parser.parse_args([])
        assert args.max_date is None

    def test_flag_parses_iso_date(self):
        parser = self._build_parser()
        args = parser.parse_args(["--max-date", "2025-04-30"])
        assert args.max_date == "2025-04-30"

    def test_flag_present_in_real_main_parser(self):
        # Smoke check that auto_tune.main() exposes --max-date. We do it
        # by invoking the script with --help and looking for the flag.
        import subprocess
        script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "auto_tune.py",
        )
        out = subprocess.check_output([sys.executable, script, "--help"], stderr=subprocess.STDOUT)
        assert b"--max-date" in out


class TestRunBacktestCutoff:
    """Verify ``cutoff`` slicing inside ``run_backtest_with_params``.

    These tests monkeypatch the data-loading helpers so they don't need
    a populated ``data/ohlcv.db``. They prove the slicing path is wired
    up; integration tests that hit the real DB are guarded below.
    """

    def test_cutoff_slices_post_cutoff_bars(self, monkeypatch, fixed_cutoff):
        sim_start = datetime(2024, 1, 30, tzinfo=timezone.utc)
        sim_end = datetime(2025, 1, 30, tzinfo=timezone.utc)

        # Build frames with bars on both sides of the cutoff.
        ts_pre = pd.date_range("2024-02-01", "2025-04-29", freq="1h").tolist()
        ts_post = pd.date_range("2025-04-30", "2025-05-15", freq="1h").tolist()
        all_ts = ts_pre + ts_post

        def fake_get_cached_data(symbol, interval, start_date=None):
            return _ohlcv_df(all_ts)

        def fake_fng():
            return _ohlcv_df([datetime(2025, 4, 29), datetime(2025, 5, 2)])

        def fake_funding():
            return _ohlcv_df([datetime(2025, 4, 29), datetime(2025, 5, 2)])

        def fake_simulate(*a, **kw):
            # Capture the frames the simulator received and short-circuit.
            captured["df1h"] = a[0]
            captured["df4h"] = a[1]
            captured["df5m"] = a[2]
            captured["df1d"] = kw.get("df1d")
            captured["df_fng"] = kw.get("df_fng")
            captured["df_funding"] = kw.get("df_funding")
            return [], []

        def fake_metrics(*a, **kw):
            return {"net_pnl": 0, "total_trades": 0, "profit_factor": 0}

        captured: dict = {}
        import backtest as bt
        monkeypatch.setattr(bt, "get_cached_data", fake_get_cached_data)
        monkeypatch.setattr(bt, "get_historical_fear_greed", fake_fng)
        monkeypatch.setattr(bt, "get_historical_funding_rate", fake_funding)
        monkeypatch.setattr(bt, "simulate_strategy", fake_simulate)
        monkeypatch.setattr(bt, "calculate_metrics", fake_metrics)

        params = {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5}
        auto_tune.run_backtest_with_params(
            "BTCUSDT", params, sim_start, sim_end, cutoff=fixed_cutoff,
        )

        cutoff_naive = fixed_cutoff.replace(tzinfo=None)
        for name in ("df1h", "df4h", "df5m", "df1d", "df_fng", "df_funding"):
            df = captured[name]
            assert not df.empty, f"{name} unexpectedly empty"
            assert df.index.max() < cutoff_naive, (
                f"{name} retained a bar at or after cutoff: {df.index.max()}"
            )

    def test_no_cutoff_keeps_post_cutoff_bars(self, monkeypatch):
        sim_start = datetime(2024, 1, 30, tzinfo=timezone.utc)
        sim_end = datetime(2025, 1, 30, tzinfo=timezone.utc)

        ts = pd.date_range("2024-02-01", "2025-05-15", freq="1h").tolist()

        captured: dict = {}

        def fake_get_cached_data(symbol, interval, start_date=None):
            return _ohlcv_df(ts)

        def fake_simulate(*a, **kw):
            captured["df1h"] = a[0]
            return [], []

        def fake_metrics(*a, **kw):
            return {"net_pnl": 0, "total_trades": 0, "profit_factor": 0}

        import backtest as bt
        monkeypatch.setattr(bt, "get_cached_data", fake_get_cached_data)
        monkeypatch.setattr(bt, "get_historical_fear_greed", lambda: pd.DataFrame())
        monkeypatch.setattr(bt, "get_historical_funding_rate", lambda: pd.DataFrame())
        monkeypatch.setattr(bt, "simulate_strategy", fake_simulate)
        monkeypatch.setattr(bt, "calculate_metrics", fake_metrics)

        params = {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5}
        auto_tune.run_backtest_with_params(
            "BTCUSDT", params, sim_start, sim_end, cutoff=None,
        )

        # Without cutoff, the legacy path retains the full frame including
        # post-cutoff bars (no slicing pass).
        assert captured["df1h"].index.max() > datetime(2025, 4, 30)


class TestBuildParamsBlock:
    """``_build_params_block`` must refuse to emit a partial params.json
    when a portfolio symbol has no usable override in the current config.
    Silent None placeholders would make the artefact look like a valid
    drop-in while actually breaking downstream consumers.
    """

    def _result(self, sym: str, recommendation: str = "KEEP", proposed: dict | None = None) -> dict:
        return {
            "symbol": sym,
            "recommendation": recommendation,
            "current_params": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5},
            "current_val_pnl": 0,
            "proposed_params": proposed,
            "proposal_detail": None,
        }

    def test_uses_proposed_when_change(self):
        from tools.retune_pre_holdout import _build_params_block

        results = [self._result("BTC", "CHANGE", {"atr_sl_mult": 1.5, "atr_tp_mult": 5.0, "atr_be_mult": 2.0})]
        out = _build_params_block(results, {"BTC": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5}})
        assert out == {"BTC": {"atr_sl_mult": 1.5, "atr_tp_mult": 5.0, "atr_be_mult": 2.0}}

    def test_preserves_current_when_keep(self):
        from tools.retune_pre_holdout import _build_params_block

        results = [self._result("BTC", "KEEP")]
        current = {"BTC": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5}}
        out = _build_params_block(results, current)
        assert out == current

    def test_preserves_false_disabled_sentinel(self):
        from tools.retune_pre_holdout import _build_params_block

        results = [self._result("BTC", "KEEP")]
        out = _build_params_block(results, {"BTC": False})
        assert out == {"BTC": False}

    def test_raises_when_symbol_missing_from_overrides(self):
        from tools.retune_pre_holdout import _build_params_block

        results = [self._result("BTC", "KEEP")]
        with pytest.raises(ValueError, match="has no flat override entry"):
            _build_params_block(results, {})

    def test_raises_when_override_is_partial(self):
        from tools.retune_pre_holdout import _build_params_block

        results = [self._result("BTC", "KEEP")]
        # Missing atr_be_mult — must refuse, not emit None.
        with pytest.raises(ValueError, match="missing required keys"):
            _build_params_block(results, {"BTC": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0}})

    def test_raises_when_override_is_garbage(self):
        from tools.retune_pre_holdout import _build_params_block

        results = [self._result("BTC", "NO_DATA")]
        with pytest.raises(ValueError, match="has no flat override entry"):
            _build_params_block(results, {"BTC": "not-a-dict"})


class TestParallelOrchestration:
    """Cover the dispatch path of the wrapper. Cross-process determinism
    is verified at the run-twice + diff layer (Phase 3, run 1 vs run 2);
    these tests cover the in-process / orchestration logic that we can
    reach without spawning subprocesses.
    """

    def test_run_optimizations_workers_1_preserves_order(self, monkeypatch):
        from tools import retune_pre_holdout as rph

        captured = []

        def fake_worker(payload):
            sym, _config, _cutoff = payload
            captured.append(sym)
            return {"symbol": sym, "recommendation": "KEEP",
                    "current_params": {}, "current_val_pnl": 0,
                    "proposed_params": None, "proposal_detail": None}

        monkeypatch.setattr(rph, "_optimize_worker", fake_worker)
        cutoff = datetime(2025, 4, 30, tzinfo=timezone.utc)
        symbols = ["BTC", "ETH", "ADA", "AVAX"]
        out = rph._run_optimizations(symbols, {}, cutoff, workers=1)
        assert [r["symbol"] for r in out] == symbols
        assert captured == symbols  # in-process loop preserves input order

    def test_optimize_worker_reseeds_per_call(self, monkeypatch):
        from tools import retune_pre_holdout as rph

        seeded_with = []

        def fake_initialize_seed(config):
            seeded_with.append(int(config.get("auto_tune", {}).get("seed", 42)))
            return seeded_with[-1]

        def fake_optimize_symbol(symbol, config, *, today, cutoff):
            return {"symbol": symbol, "recommendation": "KEEP",
                    "current_params": {}, "current_val_pnl": 0,
                    "proposed_params": None, "proposal_detail": None}

        monkeypatch.setattr(auto_tune, "initialize_seed", fake_initialize_seed)
        monkeypatch.setattr(auto_tune, "optimize_symbol", fake_optimize_symbol)

        cutoff_iso = datetime(2025, 4, 30, tzinfo=timezone.utc).isoformat()
        rph._optimize_worker(("BTC", {"auto_tune": {"seed": 7}}, cutoff_iso))
        rph._optimize_worker(("ETH", {"auto_tune": {"seed": 7}}, cutoff_iso))
        # Each invocation must call initialize_seed independently — the
        # parent's seed call doesn't propagate to children under spawn.
        assert seeded_with == [7, 7]

    def test_optimize_worker_catches_exceptions(self, monkeypatch):
        from tools import retune_pre_holdout as rph

        def fake_optimize_symbol(symbol, config, *, today, cutoff):
            raise RuntimeError("boom")

        def fake_get_current_params(symbol, config):
            return {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5}

        monkeypatch.setattr(auto_tune, "optimize_symbol", fake_optimize_symbol)
        monkeypatch.setattr(auto_tune, "get_current_params", fake_get_current_params)
        # Bypass the seed init to avoid touching real RNG state in the test.
        monkeypatch.setattr(auto_tune, "initialize_seed", lambda cfg: 42)

        cutoff_iso = datetime(2025, 4, 30, tzinfo=timezone.utc).isoformat()
        out = rph._optimize_worker(("BTC", {}, cutoff_iso))
        assert out["symbol"] == "BTC"
        assert out["recommendation"] == "ERROR"
        assert out["error"] == "boom"

    def test_workers_flag_present_in_real_main_parser(self):
        # Smoke check that the wrapper exposes --workers.
        import subprocess
        out = subprocess.check_output(
            [sys.executable, "-m", "tools.retune_pre_holdout", "--help"],
            stderr=subprocess.STDOUT,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        assert b"--workers" in out


class TestArtefactReproducibility:
    """Belt-and-suspenders: the artefact JSON layer must be byte-stable
    across re-runs (sort_keys + indent enforced). This is a structural
    check on the wrapper's writer — the full reproducibility test
    against ``data/ohlcv.db`` is documented in the manifest itself
    (run twice, diff params.json, expect empty).
    """

    def test_json_writer_is_sort_keys_byte_stable(self, tmp_path):
        from tools.retune_pre_holdout import _atomic_write_json

        payload = {
            "z_last": [3, 2, 1],
            "a_first": {"nested_z": 1, "nested_a": 2},
            "middle": "value",
        }
        path_a = tmp_path / "a.json"
        path_b = tmp_path / "b.json"
        _atomic_write_json(str(path_a), payload)
        _atomic_write_json(str(path_b), payload)
        assert path_a.read_bytes() == path_b.read_bytes()

        decoded = json.loads(path_a.read_text())
        # sort_keys at every level — verify by re-encoding canonically.
        assert path_a.read_text().rstrip("\n") == json.dumps(decoded, sort_keys=True, indent=2, ensure_ascii=False)


@pytest.mark.skipif(
    not os.path.exists(OHLCV_DB),
    reason="requires cached market data (data/ohlcv.db)",
)
class TestEndToEndWithRealOhlcv:
    def test_optimize_symbol_with_cutoff_does_not_leak(self):
        """Real data: tune a single symbol with cutoff and verify the
        no-leakage assertion is exercised end-to-end without raising.
        """
        config = auto_tune.load_config()
        cutoff = datetime(2025, 4, 30, tzinfo=timezone.utc)
        result = auto_tune.optimize_symbol(
            "BTCUSDT", config, today=cutoff, cutoff=cutoff,
        )
        # Result shape is the same as the legacy path; recommendation
        # may be CHANGE / KEEP / NO_DATA / ERROR depending on data.
        assert "recommendation" in result
        assert "current_params" in result
