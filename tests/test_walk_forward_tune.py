"""Tests for `walk_forward.tune_window`.

This module covers the per-window integration into `auto_tune.optimize_symbol`
added in commit 3 of #276. The real optimizer takes minutes per symbol and
hits OHLCV / regime data; both are mocked here so the test runs in
milliseconds and never reaches across the holdout boundary.

Invariants exercised:

  1. `optimize_symbol` is called exactly once per symbol that
     `get_portfolio_symbols` returns.
  2. Each call passes `today=cutoff=<train_end at midnight UTC, tz-aware>`.
  3. Per-symbol returns are aggregated into `results[symbol]` preserving
     the symbol order from `get_portfolio_symbols`.
  4. Empty / single-symbol / multi-symbol portfolios all behave correctly.

The real `auto_tune.optimize_symbol` is NEVER invoked. Any test that lets
it through would take minutes and would also break the holdout safety
contract by touching live OHLCV state.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

import walk_forward
from walk_forward import Window, tune_window


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_window(train_end: date, index: int = 0) -> Window:
    """Build a Window where only `train_end` and `index` matter for tune_window.

    The other fields are filled with consistent placeholders so the
    `frozen=True` dataclass invariants are satisfied.
    """
    return Window(
        index=index,
        train_start=date(2023, 1, 1),
        train_end=train_end,
        test_start=train_end,
        test_end=date(train_end.year, train_end.month, train_end.day),
        warmup_gap_days=0,
    )


class _FakeAutoTune:
    """Stand-in for the `auto_tune` module.

    Records every `optimize_symbol` invocation so tests can assert on
    argument shape, call count, and ordering.
    """

    def __init__(self, symbols: list[str]):
        self._symbols = list(symbols)
        self.calls: list[dict] = []

    def get_portfolio_symbols(self, config: dict) -> list[str]:
        # Return a fresh list so the caller cannot mutate our state.
        return list(self._symbols)

    def optimize_symbol(self, symbol, config, today=None, *, cutoff=None):
        self.calls.append(
            {
                "symbol": symbol,
                "config_id": id(config),
                "today": today,
                "cutoff": cutoff,
            }
        )
        return {
            "symbol": symbol,
            "recommendation": "KEEP_CURRENT",
            "current_params": {"atr_sl_mult": 1.0},
            "current_val_pnl": 0,
            "proposed_params": None,
            "proposal_detail": None,
        }


@pytest.fixture
def patch_auto_tune(monkeypatch):
    """Inject a `_FakeAutoTune` in place of the real module.

    Returns a factory the test can call with the symbol list it wants the
    fake to advertise via `get_portfolio_symbols`.
    """

    def _install(symbols: list[str]) -> _FakeAutoTune:
        fake = _FakeAutoTune(symbols)
        # `tune_window` imports `auto_tune` lazily inside the function body,
        # so we patch the module-level binding via sys.modules.
        import sys

        monkeypatch.setitem(sys.modules, "auto_tune", fake)
        return fake

    return _install


# --------------------------------------------------------------------------- #
# Property 1: one call per symbol, in order
# --------------------------------------------------------------------------- #


def test_calls_optimize_symbol_once_per_portfolio_symbol(patch_auto_tune):
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    fake = patch_auto_tune(symbols)

    window = _make_window(train_end=date(2025, 6, 30))
    config = {"symbol_overrides": {}}

    out = tune_window(window, config)

    assert [c["symbol"] for c in fake.calls] == symbols, (
        "optimize_symbol must be called once per active symbol, in the "
        "order returned by get_portfolio_symbols"
    )
    assert set(out["results"].keys()) == set(symbols)


# --------------------------------------------------------------------------- #
# Property 2: today=cutoff=train_end (UTC midnight, tz-aware)
# --------------------------------------------------------------------------- #


def test_passes_train_end_as_today_and_cutoff(patch_auto_tune):
    fake = patch_auto_tune(["BTCUSDT"])
    train_end = date(2025, 6, 30)
    window = _make_window(train_end=train_end)
    config = {"symbol_overrides": {}}

    tune_window(window, config)

    assert len(fake.calls) == 1
    call = fake.calls[0]
    expected = datetime(2025, 6, 30, tzinfo=timezone.utc)
    assert call["today"] == expected, (
        f"today must equal train_end at UTC midnight; got {call['today']!r}"
    )
    assert call["cutoff"] == expected, (
        f"cutoff must equal train_end at UTC midnight; got {call['cutoff']!r}"
    )
    # Both today and cutoff must be the same object-shape: tz-aware UTC.
    assert call["today"].tzinfo is not None
    assert call["cutoff"].tzinfo is not None


# --------------------------------------------------------------------------- #
# Property 3: per-symbol returns aggregated under `results`
# --------------------------------------------------------------------------- #


def test_aggregates_per_symbol_returns(patch_auto_tune):
    symbols = ["BTCUSDT", "ETHUSDT"]
    patch_auto_tune(symbols)

    window = _make_window(train_end=date(2025, 6, 30), index=4)
    out = tune_window(window, {"symbol_overrides": {}})

    assert out["window_index"] == 4
    assert out["train_end"] == "2025-06-30"
    assert out["cutoff"].startswith("2025-06-30T00:00:00")
    assert set(out["results"]) == set(symbols)
    for sym in symbols:
        r = out["results"][sym]
        assert r["symbol"] == sym
        assert r["recommendation"] == "KEEP_CURRENT"


# --------------------------------------------------------------------------- #
# Property 4: edge cases
# --------------------------------------------------------------------------- #


def test_empty_portfolio_yields_empty_results(patch_auto_tune):
    fake = patch_auto_tune([])  # no active symbols
    window = _make_window(train_end=date(2025, 6, 30))

    out = tune_window(window, {"symbol_overrides": {}})

    assert fake.calls == [], "no symbols → no optimize_symbol calls"
    assert out["results"] == {}
    assert out["window_index"] == 0
    assert out["train_end"] == "2025-06-30"


def test_single_symbol_portfolio(patch_auto_tune):
    fake = patch_auto_tune(["BTCUSDT"])
    window = _make_window(train_end=date(2024, 12, 31))

    out = tune_window(window, {"symbol_overrides": {}})

    assert len(fake.calls) == 1
    assert fake.calls[0]["symbol"] == "BTCUSDT"
    assert list(out["results"]) == ["BTCUSDT"]


def test_multiple_symbols_preserve_order(patch_auto_tune):
    # Non-alphabetical input to make the order-preservation assertion bite.
    symbols = ["SOLUSDT", "BTCUSDT", "ETHUSDT", "AVAXUSDT"]
    fake = patch_auto_tune(symbols)
    window = _make_window(train_end=date(2025, 3, 31))

    tune_window(window, {"symbol_overrides": {}})

    assert [c["symbol"] for c in fake.calls] == symbols


# --------------------------------------------------------------------------- #
# Internal: _train_end_to_cutoff helper
# --------------------------------------------------------------------------- #


def test_train_end_to_cutoff_from_date_is_utc_midnight():
    out = walk_forward._train_end_to_cutoff(date(2025, 6, 30))
    assert out == datetime(2025, 6, 30, tzinfo=timezone.utc)
    assert out.tzinfo is not None


def test_train_end_to_cutoff_from_naive_datetime_attaches_utc():
    naive = datetime(2025, 6, 30, 12, 34, 56)
    out = walk_forward._train_end_to_cutoff(naive)
    assert out.tzinfo is timezone.utc
    # The walltime is preserved; only tz is attached.
    assert out.replace(tzinfo=None) == naive


def test_train_end_to_cutoff_from_aware_datetime_is_passthrough():
    aware = datetime(2025, 6, 30, 12, 34, 56, tzinfo=timezone.utc)
    out = walk_forward._train_end_to_cutoff(aware)
    assert out == aware
