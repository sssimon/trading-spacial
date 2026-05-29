"""Tests for the per-window regime composite (#536, completes #276).

This module covers the regime layer added on top of commit 4's
`evaluate_window`:

  - `_classify_regime_score`   — score → BULL/BEAR/NEUTRAL (thresholds
    mirror `strategy.regime._compute_local_regime`: >60 BULL, <40 BEAR).
  - `_build_window_regime_series` — per-day composite (40% price + 30%
    F&G + 30% funding, mode='global' weights) over the test window,
    built from already-loaded NON-HOLDOUT historical frames.
  - `_window_regime_tag`        — Residual 1: the window's `regime_tag`
    via the agreed rule (Paso 0 → C, "composite_mean"): classify the
    mean of the daily composite.
  - `_per_regime_breakdown`     — Residual 2: bucket the window's trades
    by the composite regime active on each trade's entry day (same
    taxonomy as the window tag — agreed in #536).

The pure helpers take frames as arguments and read no data source, so
they are unit-testable without network or disk and cannot touch the
locked holdout.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from walk_forward import (
    Window,
    evaluate_window,
    _build_window_regime_series,
    _classify_regime_score,
    _per_regime_breakdown,
    _window_regime_tag,
)


# --------------------------------------------------------------------------- #
# Synthetic historical frames (NON-HOLDOUT — built in memory)
# --------------------------------------------------------------------------- #


def _btc_daily(start: date, n_days: int, *, direction: str) -> pd.DataFrame:
    """Daily BTC OHLCV-ish frame with a `close` column and a DatetimeIndex.

    `direction`:
      - "bull": monotonically rising close → price_score near 100.
      - "bear": monotonically falling close → death cross + below SMA200
        + negative 30d return → low price_score.
    """
    idx = pd.date_range(pd.Timestamp(start), periods=n_days, freq="D")
    if direction == "bull":
        close = [100.0 + i for i in range(n_days)]
    elif direction == "bear":
        close = [100.0 + n_days - i for i in range(n_days)]
    else:
        raise ValueError(direction)
    return pd.DataFrame({"close": close}, index=idx)


def _fng(start: date, n_days: int, value: int) -> pd.DataFrame:
    """Daily Fear & Greed frame: index='date', column 'fng' (0-100)."""
    idx = pd.date_range(pd.Timestamp(start), periods=n_days, freq="D")
    return pd.DataFrame({"fng": [value] * n_days}, index=idx)


def _funding(start: date, n_days: int, rate: float) -> pd.DataFrame:
    """8h funding-rate frame: index='time', column 'rate'."""
    idx = pd.date_range(pd.Timestamp(start), periods=n_days * 3, freq="8h")
    return pd.DataFrame({"rate": [rate] * (n_days * 3)}, index=idx)


# --------------------------------------------------------------------------- #
# _classify_regime_score — thresholds mirror production (>60 BULL, <40 BEAR)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "score,expected",
    [
        (90.0, "BULL"),
        (61.0, "BULL"),
        (60.0, "NEUTRAL"),   # strict >: 60 is NOT bull
        (50.0, "NEUTRAL"),
        (40.0, "NEUTRAL"),   # strict <: 40 is NOT bear
        (39.0, "BEAR"),
        (5.0, "BEAR"),
    ],
)
def test_classify_regime_score_thresholds(score, expected):
    assert _classify_regime_score(score) == expected


# --------------------------------------------------------------------------- #
# _build_window_regime_series — composite math + windowing
# --------------------------------------------------------------------------- #


def test_series_covers_only_the_test_window_left_inclusive():
    btc = _btc_daily(date(2024, 1, 1), 400, direction="bull")
    fng = _fng(date(2024, 1, 1), 400, 70)
    fund = _funding(date(2024, 1, 1), 400, 0.004)

    series = _build_window_regime_series(
        btc, fng, fund, date(2025, 1, 1), date(2025, 1, 11)
    )

    # [2025-01-01, 2025-01-11) → exactly 10 days.
    assert len(series) == 10
    assert series.index.min() == pd.Timestamp(2025, 1, 1)
    assert series.index.max() == pd.Timestamp(2025, 1, 10)
    for col in ("price", "fng", "funding", "composite"):
        assert col in series.columns


def test_composite_is_global_weighted_sum_of_components():
    btc = _btc_daily(date(2024, 1, 1), 400, direction="bull")
    fng = _fng(date(2024, 1, 1), 400, 70)
    fund = _funding(date(2024, 1, 1), 400, 0.004)  # score 50 + 0.004*5000 = 70

    series = _build_window_regime_series(
        btc, fng, fund, date(2025, 1, 1), date(2025, 1, 11)
    )

    for _, row in series.iterrows():
        expected = row["price"] * 0.40 + row["fng"] * 0.30 + row["funding"] * 0.30
        assert row["composite"] == pytest.approx(expected, abs=1e-9)
    # F&G pass-through and funding mapping land as expected.
    assert (series["fng"] == 70).all()
    assert (series["funding"] == 70).all()  # _compute_funding_score(0.004)


def test_bull_setup_scores_above_bull_threshold():
    btc = _btc_daily(date(2024, 1, 1), 400, direction="bull")
    fng = _fng(date(2024, 1, 1), 400, 80)
    fund = _funding(date(2024, 1, 1), 400, 0.005)

    series = _build_window_regime_series(
        btc, fng, fund, date(2025, 1, 1), date(2025, 2, 1)
    )
    assert (series["composite"] > 60).all()


def test_bear_setup_scores_below_bear_threshold():
    btc = _btc_daily(date(2024, 1, 1), 400, direction="bear")
    fng = _fng(date(2024, 1, 1), 400, 15)
    fund = _funding(date(2024, 1, 1), 400, -0.005)

    series = _build_window_regime_series(
        btc, fng, fund, date(2025, 1, 1), date(2025, 2, 1)
    )
    assert (series["composite"] < 40).all()


def test_missing_funding_defaults_to_neutral_fifty():
    """Funding history that does not cover the window → neutral 50 (mirrors
    production `detect_regime` funding-error fallback)."""
    btc = _btc_daily(date(2024, 1, 1), 400, direction="bull")
    fng = _fng(date(2024, 1, 1), 400, 70)
    fund = pd.DataFrame({"rate": []}, index=pd.DatetimeIndex([], name="time"))

    series = _build_window_regime_series(
        btc, fng, fund, date(2025, 1, 1), date(2025, 1, 6)
    )
    assert (series["funding"] == 50).all()


def test_empty_window_returns_empty_series():
    btc = _btc_daily(date(2024, 1, 1), 400, direction="bull")
    fng = _fng(date(2024, 1, 1), 400, 70)
    fund = _funding(date(2024, 1, 1), 400, 0.0)

    series = _build_window_regime_series(
        btc, fng, fund, date(2025, 1, 1), date(2025, 1, 1)  # empty range
    )
    assert series.empty


# --------------------------------------------------------------------------- #
# _window_regime_tag — Residual 1 (Paso 0 → C, "composite_mean")
# --------------------------------------------------------------------------- #


def _series(composite_values, price=80, fng=70, funding=60, start=date(2025, 1, 1)):
    idx = pd.date_range(pd.Timestamp(start), periods=len(composite_values), freq="D")
    return pd.DataFrame(
        {
            "price": [price] * len(composite_values),
            "fng": [fng] * len(composite_values),
            "funding": [funding] * len(composite_values),
            "composite": list(composite_values),
        },
        index=idx,
    )


def test_window_regime_tag_classifies_mean_composite_bull():
    tag = _window_regime_tag(_series([70, 80, 90]))
    assert tag["regime"] == "BULL"
    assert tag["score"] == pytest.approx(80.0)
    assert tag["method"] == "composite_mean"
    assert tag["n_days"] == 3
    assert set(tag["components"]) == {"price", "fng", "funding"}


def test_window_regime_tag_neutral_when_mean_is_between():
    tag = _window_regime_tag(_series([40, 50, 60]))
    assert tag["regime"] == "NEUTRAL"
    assert tag["score"] == pytest.approx(50.0)


def test_window_regime_tag_none_for_empty_series():
    assert _window_regime_tag(None) is None
    assert _window_regime_tag(_series([])) is None


# --------------------------------------------------------------------------- #
# _per_regime_breakdown — Residual 2 (composite taxonomy, agreed in #536)
# --------------------------------------------------------------------------- #


def _trade(entry: datetime, pnl_usd: float, pnl_pct: float, exit_reason: str = "TP") -> dict:
    return {
        "entry_time": entry,
        "pnl_usd": pnl_usd,
        "pnl_pct": pnl_pct,
        "exit_reason": exit_reason,
    }


def test_per_regime_buckets_trades_by_entry_day_composite():
    # Day 1 BULL (90), day 2 BEAR (20), day 3 NEUTRAL (50).
    series = _series([90, 20, 50], start=date(2025, 1, 1))
    trades = [
        _trade(datetime(2025, 1, 1, 12), 10.0, 1.0),   # → BULL
        _trade(datetime(2025, 1, 2, 9), -5.0, -0.5),   # → BEAR
        _trade(datetime(2025, 1, 3, 18), 2.0, 0.2),    # → NEUTRAL
    ]
    out = _per_regime_breakdown(trades, series)

    assert out["BULL"]["trades"] == 1
    assert out["BULL"]["total_pnl_usd"] == pytest.approx(10.0)
    assert out["BULL"]["win_rate"] == pytest.approx(100.0)
    assert out["BEAR"]["trades"] == 1
    assert out["BEAR"]["total_pnl_usd"] == pytest.approx(-5.0)
    assert out["BEAR"]["win_rate"] == pytest.approx(0.0)
    assert out["NEUTRAL"]["trades"] == 1


def test_per_regime_skips_open_trades():
    series = _series([90, 90], start=date(2025, 1, 1))
    trades = [
        _trade(datetime(2025, 1, 1, 12), 10.0, 1.0, exit_reason="TP"),
        _trade(datetime(2025, 1, 2, 12), 0.0, 0.0, exit_reason="OPEN"),
    ]
    out = _per_regime_breakdown(trades, series)
    assert out["BULL"]["trades"] == 1  # OPEN trade not counted


def test_per_regime_all_buckets_present_when_empty():
    series = _series([90, 90])
    out = _per_regime_breakdown([], series)
    for regime in ("BULL", "BEAR", "NEUTRAL"):
        assert out[regime]["trades"] == 0
        assert out[regime]["total_pnl_usd"] == 0


def test_per_regime_none_without_series():
    assert _per_regime_breakdown([_trade(datetime(2025, 1, 1), 1.0, 0.1)], None) is None


# --------------------------------------------------------------------------- #
# Integration: evaluate_window wires regime_tag + per_regime when regime_data
# is supplied; stays None (backward-compatible) when it is not.
# --------------------------------------------------------------------------- #


class _FakeAutoTune:
    """Minimal stand-in returning trades with the fields the regime layer
    needs (entry_time, pnl_usd, pnl_pct, exit_reason)."""

    def __init__(self, trades):
        self._trades = trades
        self.calls = []

    def run_backtest_with_params(self, symbol, params, sim_start, sim_end, *, cutoff=None, app_config=None):
        self.calls.append(symbol)
        metrics = {"total_trades": len(self._trades), "net_pnl": 5.0}
        return self._trades, metrics


@pytest.fixture
def patch_auto_tune(monkeypatch):
    def _install(trades):
        import sys
        fake = _FakeAutoTune(trades)
        monkeypatch.setitem(sys.modules, "auto_tune", fake)
        return fake
    return _install


def _eval_window() -> Window:
    return Window(
        index=0,
        train_start=date(2024, 1, 1),
        train_end=date(2025, 1, 1),
        test_start=date(2025, 1, 1),
        test_end=date(2025, 2, 1),
        warmup_gap_days=0,
    )


def _tuned_keep(symbol="BTCUSDT") -> dict:
    return {
        "results": {
            symbol: {
                "symbol": symbol,
                "current_params": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5},
                "current_val_pnl": 0,
                "proposed_params": None,
                "proposal_detail": None,
                "recommendation": "KEEP",
            }
        }
    }


def _regime_data(direction="bull", fng=80, rate=0.005) -> dict:
    return {
        "btc_daily": _btc_daily(date(2024, 1, 1), 500, direction=direction),
        "fng_df": _fng(date(2024, 1, 1), 500, fng),
        "funding_df": _funding(date(2024, 1, 1), 500, rate),
    }


def test_evaluate_window_populates_top_level_regime_tag_and_per_regime(patch_auto_tune):
    patch_auto_tune([
        _trade(datetime(2025, 1, 5, 12), 10.0, 1.0),
        _trade(datetime(2025, 1, 20, 12), -3.0, -0.3),
    ])
    report = evaluate_window(
        _eval_window(), _tuned_keep(), config={"symbol_overrides": {}},
        regime_data=_regime_data(direction="bull"),
    )

    assert isinstance(report["regime_tag"], dict)
    assert report["regime_tag"]["regime"] == "BULL"
    assert report["regime_tag"]["method"] == "composite_mean"
    assert isinstance(report["per_regime"], dict)
    assert set(report["per_regime"]) == {"BULL", "BEAR", "NEUTRAL"}
    # Both trades fall in the bull window → bucketed under BULL.
    assert report["per_regime"]["BULL"]["trades"] == 2


def test_per_symbol_regime_tag_is_retired(patch_auto_tune):
    """The old per-symbol `regime_tag` placeholder is gone; regime lives at
    the window level now."""
    patch_auto_tune([_trade(datetime(2025, 1, 5, 12), 10.0, 1.0)])
    report = evaluate_window(
        _eval_window(), _tuned_keep(), config={"symbol_overrides": {}},
        regime_data=_regime_data(),
    )
    entry = report["results"]["BTCUSDT"]
    assert "regime_tag" not in entry
    assert set(entry) == {"n_trades", "metrics", "error"}


def test_no_regime_data_leaves_regime_fields_none(patch_auto_tune):
    """Backward-compatible: without regime_data the harness performs no I/O
    and leaves regime_tag / per_regime as None."""
    patch_auto_tune([_trade(datetime(2025, 1, 5, 12), 10.0, 1.0)])
    report = evaluate_window(
        _eval_window(), _tuned_keep(), config={"symbol_overrides": {}},
    )
    assert report["regime_tag"] is None
    assert report["per_regime"] is None


def test_empty_test_range_carries_none_regime_fields(patch_auto_tune):
    patch_auto_tune([])
    window = Window(
        index=1, train_start=date(2024, 1, 1), train_end=date(2025, 1, 1),
        test_start=date(2025, 1, 1), test_end=date(2025, 1, 1),  # degenerate
        warmup_gap_days=0,
    )
    report = evaluate_window(
        window, _tuned_keep(), config={"symbol_overrides": {}},
        regime_data=_regime_data(),
    )
    assert report["regime_tag"] is None
    assert report["per_regime"] is None
