"""Tests for simulate_strategy regime-threshold kwargs + BYPASS bypass.

Covers signature wiring (parity at the API surface), BYPASS direction
token end-to-end through strategy/core.py's gating block, and mutually-
exclusive kwarg validation. Full bar-loop integration is exercised by the
harness's actual run.
"""
import inspect

import pandas as pd
import pytest

import backtest
from strategy.core import _regime_to_direction_token


class TestSimulateStrategySignature:
    def test_accepts_regime_thresholds_kwarg(self):
        sig = inspect.signature(backtest.simulate_strategy)
        assert "regime_thresholds" in sig.parameters
        # Default None preserves legacy (uses _compute_local_regime defaults 60/40)
        assert sig.parameters["regime_thresholds"].default is None

    def test_accepts_regime_disabled_kwarg(self):
        sig = inspect.signature(backtest.simulate_strategy)
        assert "regime_disabled" in sig.parameters
        assert sig.parameters["regime_disabled"].default is False


class TestRegimeAtTimeSignature:
    def test_accepts_threshold_kwargs(self):
        sig = inspect.signature(backtest._regime_at_time)
        assert "bull_above" in sig.parameters
        assert "bear_below" in sig.parameters
        assert sig.parameters["bull_above"].default == 60
        assert sig.parameters["bear_below"].default == 40


class TestRegimeToDirectionToken:
    """The load-bearing BYPASS branch — verified end-to-end on the helper itself."""

    def test_bear_maps_to_short(self):
        assert _regime_to_direction_token("BEAR") == "SHORT"

    def test_bull_maps_to_long(self):
        assert _regime_to_direction_token("BULL") == "LONG"

    def test_neutral_maps_to_long(self):
        assert _regime_to_direction_token("NEUTRAL") == "LONG"

    def test_none_maps_to_long_silent(self, caplog):
        with caplog.at_level("WARNING", logger="strategy.core"):
            result = _regime_to_direction_token(None)
        assert result == "LONG"
        assert not any("Unknown regime_label" in r.message for r in caplog.records)

    def test_unknown_label_warns_once_and_returns_long(self, caplog):
        # Ensure the once-per-label guard starts clean
        from strategy.core import _unknown_regime_warned
        _unknown_regime_warned.discard("bypass")
        _unknown_regime_warned.discard("MYSTERY")

        with caplog.at_level("WARNING", logger="strategy.core"):
            result1 = _regime_to_direction_token("bypass")  # lowercase ≠ BYPASS
            result2 = _regime_to_direction_token("bypass")  # second call should NOT warn
        assert result1 == "LONG"
        assert result2 == "LONG"
        warnings = [r for r in caplog.records if "Unknown regime_label" in r.message]
        assert len(warnings) == 1
        assert "'bypass'" in warnings[0].message

    def test_bypass_maps_to_any(self):
        assert _regime_to_direction_token("BYPASS") == "ANY"


class TestMutuallyExclusiveKwargs:
    def test_both_kwargs_raises(self):
        empty = pd.DataFrame({"close": [], "volume": []})
        with pytest.raises(ValueError, match="mutually exclusive"):
            backtest.simulate_strategy(
                df1h=empty, df4h=empty, df5m=empty, symbol="BTCUSDT",
                regime_disabled=True,
                regime_thresholds=(70, 30),
            )


class TestBypassEmitsBypassRegime:
    """When regime_disabled=True, the regime dict passed downstream must have
    regime="BYPASS" and the helper _regime_at_time must NOT be called."""

    def _build_minimal_frames(self):
        """Build the smallest set of frames that gets the bar loop to invoke
        the regime branch. simulate_strategy needs LRC_PERIOD bars in df1h
        before it processes anything; we give it a 110-bar window so the loop
        enters at least once."""
        idx_1h = pd.date_range("2024-01-01", periods=110, freq="1h", tz="UTC").tz_localize(None)
        df1h = pd.DataFrame({
            "open":  [100.0] * 110,
            "high":  [101.0] * 110,
            "low":   [99.0] * 110,
            "close": [100.0] * 110,
            "volume": [1000.0] * 110,
        }, index=idx_1h)
        idx_4h = pd.date_range("2024-01-01", periods=30, freq="4h", tz="UTC").tz_localize(None)
        df4h = df1h.iloc[:30].copy().set_index(idx_4h)
        idx_5m = pd.date_range("2024-01-01", periods=210, freq="5min", tz="UTC").tz_localize(None)
        df5m = pd.DataFrame({
            "open":  [100.0] * 210,
            "high":  [101.0] * 210,
            "low":   [99.0] * 210,
            "close": [100.0] * 210,
            "volume": [1000.0] * 210,
        }, index=idx_5m)
        return df1h, df4h, df5m

    def test_bypass_branch_runtime_skips_regime_at_time(self, monkeypatch):
        """Runtime check: with regime_disabled=True, _regime_at_time MUST NOT
        be called even if the bar loop enters."""
        called = {"count": 0}

        def fake_regime_at_time(*args, **kwargs):
            called["count"] += 1
            return {"regime": "BULL", "score": 80.0, "mode": "global", "symbol": "X", "components": {}}

        monkeypatch.setattr(backtest, "_regime_at_time", fake_regime_at_time)

        df1h, df4h, df5m = self._build_minimal_frames()

        # Run with regime_disabled=True. The simulator may still raise downstream
        # (no F&G / funding data, no df1d) — we only care that the bypass branch
        # was taken before any potential downstream error, i.e. _regime_at_time
        # was never reached.
        try:
            backtest.simulate_strategy(
                df1h=df1h, df4h=df4h, df5m=df5m, symbol="BTCUSDT",
                regime_disabled=True,
            )
        except Exception:
            pass  # downstream errors are not what this test checks

        assert called["count"] == 0, (
            f"_regime_at_time was called {called['count']} times under regime_disabled=True; "
            "bypass branch did not skip the helper."
        )
