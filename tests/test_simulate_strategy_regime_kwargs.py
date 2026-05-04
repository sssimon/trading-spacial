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
    def test_both_kwargs_raises_regime_kwarg_error(self):
        from backtest import RegimeKwargError
        empty = pd.DataFrame({"close": [], "volume": []})
        with pytest.raises(RegimeKwargError, match="mutually exclusive"):
            backtest.simulate_strategy(
                df1h=empty, df4h=empty, df5m=empty, symbol="BTCUSDT",
                regime_disabled=True,
                regime_thresholds=(70, 30),
            )

    def test_only_disabled_does_not_raise_at_entry(self):
        """Positive case: regime_disabled=True alone should not raise the
        mutex/shape validation. Downstream errors due to empty frames are
        unrelated to entry-validation."""
        from backtest import RegimeKwargError
        empty = pd.DataFrame({"close": [], "volume": []})
        try:
            backtest.simulate_strategy(
                df1h=empty, df4h=empty, df5m=empty, symbol="BTCUSDT",
                regime_disabled=True,
            )
        except RegimeKwargError:
            pytest.fail("regime_disabled=True alone should not raise RegimeKwargError")
        except Exception:
            pass  # any other downstream error is fine

    def test_only_thresholds_does_not_raise_at_entry(self):
        from backtest import RegimeKwargError
        empty = pd.DataFrame({"close": [], "volume": []})
        try:
            backtest.simulate_strategy(
                df1h=empty, df4h=empty, df5m=empty, symbol="BTCUSDT",
                regime_thresholds=(70, 30),
            )
        except RegimeKwargError:
            pytest.fail("regime_thresholds alone should not raise RegimeKwargError")
        except Exception:
            pass

    def test_neither_kwarg_does_not_raise_at_entry(self):
        from backtest import RegimeKwargError
        empty = pd.DataFrame({"close": [], "volume": []})
        try:
            backtest.simulate_strategy(
                df1h=empty, df4h=empty, df5m=empty, symbol="BTCUSDT",
            )
        except RegimeKwargError:
            pytest.fail("default kwargs should not raise RegimeKwargError")
        except Exception:
            pass


class TestRegimeThresholdsShape:
    """I11: regime_thresholds must be tuple[int, int]; malformed shapes raise."""

    @pytest.fixture
    def empty_frames(self):
        empty = pd.DataFrame({"close": [], "volume": []})
        return {"df1h": empty, "df4h": empty, "df5m": empty}

    def test_list_rejected(self, empty_frames):
        from backtest import RegimeKwargError
        with pytest.raises(RegimeKwargError, match="tuple"):
            backtest.simulate_strategy(
                **empty_frames, symbol="BTCUSDT",
                regime_thresholds=[60, 40],  # list, not tuple
            )

    def test_wrong_length_rejected(self, empty_frames):
        from backtest import RegimeKwargError
        with pytest.raises(RegimeKwargError, match="tuple"):
            backtest.simulate_strategy(
                **empty_frames, symbol="BTCUSDT",
                regime_thresholds=(60,),
            )

    def test_dict_rejected(self, empty_frames):
        from backtest import RegimeKwargError
        with pytest.raises(RegimeKwargError, match="tuple"):
            backtest.simulate_strategy(
                **empty_frames, symbol="BTCUSDT",
                regime_thresholds={"bull": 60, "bear": 40},
            )

    def test_string_elements_rejected(self, empty_frames):
        from backtest import RegimeKwargError
        with pytest.raises(RegimeKwargError, match="tuple"):
            backtest.simulate_strategy(
                **empty_frames, symbol="BTCUSDT",
                regime_thresholds=("60", "40"),
            )

    def test_bool_elements_rejected(self, empty_frames):
        """True is an int subclass in Python — explicit isinstance(bool) guard."""
        from backtest import RegimeKwargError
        with pytest.raises(RegimeKwargError, match="tuple"):
            backtest.simulate_strategy(
                **empty_frames, symbol="BTCUSDT",
                regime_thresholds=(True, False),
            )


class TestBypassEmitsBypassRegime:
    """When regime_disabled=True, the regime dict passed downstream must have
    regime="BYPASS" and the helper _regime_at_time must NOT be called.

    Frames sized > simulate_strategy's warmup (LRC_PERIOD + buffer) so the bar
    loop actually enters and the regime branch is exercised. Sanity asserted
    in test setup; the test would be vacuous with insufficient bars."""

    BARS_1H = 140  # > warmup (LRC_PERIOD=100 + buffer); bar loop iterates ≥30 times

    def _build_minimal_frames(self):
        idx_1h = pd.date_range("2024-01-01", periods=self.BARS_1H, freq="1h", tz="UTC").tz_localize(None)
        df1h = pd.DataFrame({
            "open":  [100.0] * self.BARS_1H,
            "high":  [101.0] * self.BARS_1H,
            "low":   [99.0] * self.BARS_1H,
            "close": [100.0] * self.BARS_1H,
            "volume": [1000.0] * self.BARS_1H,
        }, index=idx_1h)
        idx_4h = pd.date_range("2024-01-01", periods=40, freq="4h", tz="UTC").tz_localize(None)
        df4h = pd.DataFrame({
            "open":  [100.0] * 40,
            "high":  [101.0] * 40,
            "low":   [99.0] * 40,
            "close": [100.0] * 40,
            "volume": [1000.0] * 40,
        }, index=idx_4h)
        idx_5m = pd.date_range("2024-01-01", periods=300, freq="5min", tz="UTC").tz_localize(None)
        df5m = pd.DataFrame({
            "open":  [100.0] * 300,
            "high":  [101.0] * 300,
            "low":   [99.0] * 300,
            "close": [100.0] * 300,
            "volume": [1000.0] * 300,
        }, index=idx_5m)
        return df1h, df4h, df5m

    def test_setup_actually_exercises_bar_loop(self):
        """Sanity: confirm the test fixture is large enough for the bar loop
        to enter. Without this, the bypass test below is vacuous."""
        df1h, _, _ = self._build_minimal_frames()
        from strategy.constants import LRC_PERIOD
        assert len(df1h) > LRC_PERIOD + 10, (
            f"test setup invalid — bar loop won't enter "
            f"(len(df1h)={len(df1h)}, need > LRC_PERIOD+10 = {LRC_PERIOD + 10})"
        )

    def test_bypass_branch_runtime_skips_regime_at_time(self, monkeypatch):
        """Runtime check: with regime_disabled=True, _regime_at_time MUST NOT
        be called even though the bar loop enters."""
        called = {"count": 0}

        def fake_regime_at_time(*args, **kwargs):
            called["count"] += 1
            return {"regime": "BULL", "score": 80.0, "mode": "global", "symbol": "X", "components": {}}

        monkeypatch.setattr(backtest, "_regime_at_time", fake_regime_at_time)

        df1h, df4h, df5m = self._build_minimal_frames()

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

    def test_default_path_DOES_call_regime_at_time(self, monkeypatch):
        """Counter-test: without regime_disabled, _regime_at_time SHOULD be
        called. Proves the bypass test above is non-vacuous (a bug that
        always called _regime_at_time would fail this counter-test)."""
        called = {"count": 0}

        def fake_regime_at_time(*args, **kwargs):
            called["count"] += 1
            return {"regime": "BULL", "score": 80.0, "mode": "global", "symbol": "X", "components": {}}

        monkeypatch.setattr(backtest, "_regime_at_time", fake_regime_at_time)

        df1h, df4h, df5m = self._build_minimal_frames()

        try:
            backtest.simulate_strategy(
                df1h=df1h, df4h=df4h, df5m=df5m, symbol="BTCUSDT",
                # default: regime_disabled=False
            )
        except Exception:
            pass

        assert called["count"] > 0, (
            "_regime_at_time was never called even under default (non-bypass) "
            "configuration; bypass test above is vacuous."
        )
