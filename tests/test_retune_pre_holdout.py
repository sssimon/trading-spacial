"""Tests for the pre-holdout ATR re-tune harness (tools/retune_pre_holdout.py).

Focus (#278 Part 1): a trial-registry durability failure inside the per-symbol
worker must NOT be swallowed into an ``ERROR`` result. ``auto_tune.optimize_symbol``
calls ``run_backtest_with_params(..., trial_source="auto_tune")``, which claims /
finalizes trials in ``db/trials.py``; those raise ``sqlite3.OperationalError`` only
after the bounded retry budget is exhausted. Swallowing it would silently
under-count N and emit a partial artefact, so the worker must re-raise — aborting
the whole run loudly (in the pool path this surfaces via ``ex.map`` re-raising in
the parent). Non-DB per-symbol errors must still degrade gracefully.
"""
import sqlite3
from datetime import datetime, timezone

import pytest

from tools import retune_pre_holdout as harness


CUTOFF = datetime(2025, 4, 30, tzinfo=timezone.utc)


def _seed_stubs(monkeypatch):
    """Neutralise the side-effecting helpers so tests exercise control flow only."""
    monkeypatch.setattr(harness.auto_tune, "initialize_seed", lambda config: 0)
    monkeypatch.setattr(
        harness.auto_tune,
        "get_current_params",
        lambda sym, config: {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.0},
    )


class TestOptimizeWorkerDbDurability:
    def test_operational_error_propagates_not_swallowed(self, monkeypatch):
        """sqlite3.OperationalError from optimize_symbol must propagate, not become an ERROR dict."""
        _seed_stubs(monkeypatch)

        def boom(symbol, config, today=None, cutoff=None):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(harness.auto_tune, "optimize_symbol", boom)

        payload = ("BTCUSDT", {}, CUTOFF.isoformat())
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            harness._optimize_worker(payload)

    def test_operational_error_not_converted_to_error_result(self, monkeypatch):
        """Belt-and-suspenders: confirm no ERROR result is returned in place of the raise."""
        _seed_stubs(monkeypatch)

        def boom(symbol, config, today=None, cutoff=None):
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(harness.auto_tune, "optimize_symbol", boom)

        result = None
        try:
            result = harness._optimize_worker(("ETHUSDT", {}, CUTOFF.isoformat()))
        except sqlite3.OperationalError:
            pass
        assert result is None, "OperationalError was swallowed into a returned result"

    def test_propagates_through_in_process_caller(self, monkeypatch):
        """_run_optimizations(workers=1) (in-process path) must let the OperationalError surface.

        The workers>1 ProcessPoolExecutor path re-raises identically via ex.map; we
        exercise the in-process branch here because spawning real children in a unit
        test is unnecessary to prove the swallow is gone (pickle round-trip of the
        exception type is verified separately).
        """
        _seed_stubs(monkeypatch)

        def boom(symbol, config, today=None, cutoff=None):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(harness.auto_tune, "optimize_symbol", boom)

        with pytest.raises(sqlite3.OperationalError):
            harness._run_optimizations(["BTCUSDT", "ETHUSDT"], {}, CUTOFF, workers=1)


class TestOptimizeWorkerGracefulDegradation:
    def test_non_db_exception_still_becomes_error_result(self, monkeypatch):
        """Positive control: a non-DB per-symbol error must still degrade to an ERROR result."""
        _seed_stubs(monkeypatch)

        def boom(symbol, config, today=None, cutoff=None):
            raise ValueError("bad params for this symbol")

        monkeypatch.setattr(harness.auto_tune, "optimize_symbol", boom)

        result = harness._optimize_worker(("BTCUSDT", {}, CUTOFF.isoformat()))
        assert result["symbol"] == "BTCUSDT"
        assert result["recommendation"] == "ERROR"
        assert "bad params" in result["error"]

    def test_success_passthrough(self, monkeypatch):
        """A successful optimize_symbol result is returned verbatim."""
        _seed_stubs(monkeypatch)
        sentinel = {"symbol": "BTCUSDT", "recommendation": "KEEP"}
        monkeypatch.setattr(
            harness.auto_tune, "optimize_symbol",
            lambda symbol, config, today=None, cutoff=None: sentinel,
        )
        assert harness._optimize_worker(("BTCUSDT", {}, CUTOFF.isoformat())) is sentinel
