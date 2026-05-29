"""Tests for `walk_forward.run_walk_forward(..., ci_mode=True)` and
`frozen_params_for_window`.

Commit 6 of #276 adds a CI escape hatch: instead of driving
`auto_tune.optimize_symbol` (minutes per symbol, full grid) per
window, the orchestrator can be asked to use the ATR multipliers
already in `app_config["symbol_overrides"]` as frozen params. This
keeps the orchestrator → evaluate_window contract intact while
making the loop cheap enough for CI smoke runs.

Invariants exercised:

  1. `ci_mode=True` skips `tune_window` entirely — the real
     `auto_tune.optimize_symbol` must NEVER be reached.
  2. The frozen params come verbatim from
     `symbol_overrides[symbol]` and feed downstream
     `evaluate_window` (and `_build_is_pnl_block`) without shape
     drift.
  3. Symbols whose override lacks the three ATR keys are skipped:
     they have no frozen params to use.
  4. `ci_mode` defaults to `False`; production behaviour is
     unchanged when the flag is absent.
  5. `frozen_params_for_window` returns the `tune_window`-shaped
     dict downstream callers expect.

The real `auto_tune.optimize_symbol` and `run_backtest_with_params`
are NEVER invoked. Fakes for `get_portfolio_symbols` are installed
via `sys.modules`, mirroring the pattern in
`tests/test_walk_forward_tune.py`.
"""

from __future__ import annotations

import sys
from datetime import date

import pytest

import walk_forward
from walk_forward import (
    Window,
    frozen_params_for_window,
    run_walk_forward,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_window(index: int = 0) -> Window:
    return Window(
        index=index,
        train_start=date(2023, 1, 1),
        train_end=date(2024, 1, 1),
        test_start=date(2024, 1, 1),
        test_end=date(2024, 4, 1),
        warmup_gap_days=0,
    )


class _FakeAutoTune:
    """Minimal stand-in for the `auto_tune` module.

    Records every `optimize_symbol` invocation so a test can assert it
    was NEVER reached. Provides `get_portfolio_symbols` so
    `frozen_params_for_window` can resolve the active symbol list.
    """

    def __init__(self, symbols: list[str]):
        self._symbols = list(symbols)
        self.optimize_calls: list[dict] = []

    def get_portfolio_symbols(self, config: dict) -> list[str]:
        return list(self._symbols)

    def optimize_symbol(self, symbol, config, today=None, *, cutoff=None):
        # If this lands in ci_mode something is very wrong.
        self.optimize_calls.append(
            {"symbol": symbol, "today": today, "cutoff": cutoff}
        )
        raise AssertionError(
            f"optimize_symbol must not be called in ci_mode (symbol={symbol!r})"
        )


@pytest.fixture
def patch_auto_tune(monkeypatch):
    def _install(symbols: list[str]) -> _FakeAutoTune:
        fake = _FakeAutoTune(symbols)
        monkeypatch.setitem(sys.modules, "auto_tune", fake)
        return fake

    return _install


# --------------------------------------------------------------------------- #
# Property 1: ci_mode skips tune_window entirely
# --------------------------------------------------------------------------- #


def test_skips_tune_window(patch_auto_tune, monkeypatch):
    """When `ci_mode=True`, `tune_window` must never be called."""
    symbols = ["BTCUSDT", "ETHUSDT"]
    fake = patch_auto_tune(symbols)

    overrides = {
        "BTCUSDT": {"atr_sl_mult": 1.5, "atr_tp_mult": 3.0, "atr_be_mult": 1.0},
        "ETHUSDT": {"atr_sl_mult": 1.4, "atr_tp_mult": 2.8, "atr_be_mult": 0.9},
    }
    app_config = {"symbol_overrides": overrides}

    tune_calls: list = []

    def boom_tune(window, config):  # pragma: no cover — must not be reached
        tune_calls.append(window.index)
        raise AssertionError("tune_window must not be called in ci_mode")

    monkeypatch.setattr(walk_forward, "tune_window", boom_tune)

    # Stub evaluate_window so we don't try to actually run a backtest.
    def fake_eval(window, tuned, config):
        return {
            "window_index": window.index,
            "train_range": {
                "start": window.train_start.isoformat(),
                "end": window.train_end.isoformat(),
            },
            "test_range": {
                "start": window.test_start.isoformat(),
                "end": window.test_end.isoformat(),
            },
            "params": dict(tuned["results"].get(s, {}).get("current_params", {})
                           for s in []),  # not load-bearing for this test
            "results": {},
            "skipped": [],
        }

    monkeypatch.setattr(walk_forward, "evaluate_window", fake_eval)

    run = run_walk_forward(
        [_make_window(0), _make_window(1)],
        app_config=app_config,
        ci_mode=True,
    )

    assert tune_calls == [], "tune_window must not be invoked in ci_mode"
    assert fake.optimize_calls == [], (
        "optimize_symbol must not be invoked in ci_mode"
    )
    assert len(run.window_reports) == 2


# --------------------------------------------------------------------------- #
# Property 2: frozen params come from symbol_overrides and feed evaluate
# --------------------------------------------------------------------------- #


def test_uses_config_overrides(patch_auto_tune, monkeypatch):
    """The params evaluate_window receives must be the frozen overrides."""
    symbols = ["BTCUSDT", "ETHUSDT"]
    patch_auto_tune(symbols)

    overrides = {
        "BTCUSDT": {"atr_sl_mult": 1.5, "atr_tp_mult": 3.0, "atr_be_mult": 1.0},
        "ETHUSDT": {"atr_sl_mult": 1.4, "atr_tp_mult": 2.8, "atr_be_mult": 0.9},
    }
    app_config = {"symbol_overrides": overrides}

    seen_tuned: list[dict] = []

    def fake_eval(window, tuned, config):
        seen_tuned.append(tuned)
        # Mirror the evaluate_window envelope.
        params = {
            sym: {k: v for k, v in r["current_params"].items()}
            for sym, r in tuned["results"].items()
        }
        return {
            "window_index": window.index,
            "train_range": {
                "start": window.train_start.isoformat(),
                "end": window.train_end.isoformat(),
            },
            "test_range": {
                "start": window.test_start.isoformat(),
                "end": window.test_end.isoformat(),
            },
            "params": params,
            "results": {},
            "skipped": [],
        }

    monkeypatch.setattr(walk_forward, "evaluate_window", fake_eval)

    run = run_walk_forward(
        [_make_window(0)],
        app_config=app_config,
        ci_mode=True,
    )

    assert len(seen_tuned) == 1
    tuned = seen_tuned[0]
    assert set(tuned["results"].keys()) == {"BTCUSDT", "ETHUSDT"}
    assert tuned["results"]["BTCUSDT"]["current_params"] == overrides["BTCUSDT"]
    assert tuned["results"]["ETHUSDT"]["current_params"] == overrides["ETHUSDT"]
    assert tuned["results"]["BTCUSDT"]["recommendation"] == "KEEP_CURRENT"
    assert tuned["results"]["BTCUSDT"]["proposed_params"] is None
    # The report's `params` block — what would be evaluated — must match.
    report = run.window_reports[0]
    assert report["params"]["BTCUSDT"] == overrides["BTCUSDT"]
    assert report["params"]["ETHUSDT"] == overrides["ETHUSDT"]


# --------------------------------------------------------------------------- #
# Property 3: symbols missing ATR overrides are skipped
# --------------------------------------------------------------------------- #


def test_skips_symbol_without_overrides(patch_auto_tune, monkeypatch):
    """If `symbol_overrides[sym]` lacks the ATR keys, the symbol is dropped."""
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    patch_auto_tune(symbols)

    overrides = {
        # Full override — kept.
        "BTCUSDT": {"atr_sl_mult": 1.5, "atr_tp_mult": 3.0, "atr_be_mult": 1.0},
        # Partial — missing atr_be_mult — dropped.
        "ETHUSDT": {"atr_sl_mult": 1.4, "atr_tp_mult": 2.8},
        # SOLUSDT not present in overrides at all — dropped.
    }
    app_config = {"symbol_overrides": overrides}

    window = _make_window(0)
    out = frozen_params_for_window(window, app_config)

    assert set(out["results"].keys()) == {"BTCUSDT"}, (
        "only symbols whose overrides carry the three ATR keys must be kept"
    )
    assert out["results"]["BTCUSDT"]["current_params"] == overrides["BTCUSDT"]


# --------------------------------------------------------------------------- #
# Property 4: ci_mode defaults to False (production behaviour unchanged)
# --------------------------------------------------------------------------- #


def test_default_off(patch_auto_tune, monkeypatch):
    """`ci_mode` defaults to False; `tune_window` is the path taken."""
    symbols = ["BTCUSDT"]
    patch_auto_tune(symbols)

    overrides = {
        "BTCUSDT": {"atr_sl_mult": 1.5, "atr_tp_mult": 3.0, "atr_be_mult": 1.0},
    }
    app_config = {"symbol_overrides": overrides}

    tune_calls: list[int] = []

    def fake_tune(window, config):
        tune_calls.append(window.index)
        return {
            "window_index": window.index,
            "train_end": window.train_end.isoformat(),
            "cutoff": window.train_end.isoformat(),
            "results": {
                "BTCUSDT": {
                    "symbol": "BTCUSDT",
                    "recommendation": "KEEP_CURRENT",
                    "current_params": overrides["BTCUSDT"],
                    "current_val_pnl": 0.0,
                    "proposed_params": None,
                    "proposal_detail": None,
                }
            },
        }

    def fake_eval(window, tuned, config):
        return {
            "window_index": window.index,
            "train_range": {
                "start": window.train_start.isoformat(),
                "end": window.train_end.isoformat(),
            },
            "test_range": {
                "start": window.test_start.isoformat(),
                "end": window.test_end.isoformat(),
            },
            "params": {"BTCUSDT": overrides["BTCUSDT"]},
            "results": {},
            "skipped": [],
        }

    monkeypatch.setattr(walk_forward, "tune_window", fake_tune)
    monkeypatch.setattr(walk_forward, "evaluate_window", fake_eval)

    # Note: no ci_mode kwarg.
    run = run_walk_forward([_make_window(0)], app_config=app_config)

    assert tune_calls == [0], (
        "by default ci_mode=False — tune_window must drive the loop"
    )
    assert len(run.window_reports) == 1


# --------------------------------------------------------------------------- #
# Property 5: frozen_params_for_window output shape matches tune_window
# --------------------------------------------------------------------------- #


def test_frozen_params_shape(patch_auto_tune):
    """`frozen_params_for_window` returns a tune_window-shaped envelope.

    Required keys at the top level: window_index, train_end, cutoff,
    results. Each per-symbol entry carries the keys downstream
    consumers (`evaluate_window._select_params_for_eval`,
    `_extract_is_pnl`) expect: recommendation, current_params,
    proposed_params, proposal_detail, current_val_pnl.
    """
    symbols = ["BTCUSDT"]
    patch_auto_tune(symbols)

    overrides = {
        "BTCUSDT": {"atr_sl_mult": 1.5, "atr_tp_mult": 3.0, "atr_be_mult": 1.0},
    }
    app_config = {"symbol_overrides": overrides}

    window = _make_window(index=7)
    out = frozen_params_for_window(window, app_config)

    assert set(out.keys()) == {"window_index", "train_end", "cutoff", "results"}
    assert out["window_index"] == 7
    assert out["train_end"] == window.train_end.isoformat()
    assert out["cutoff"].startswith(window.train_end.isoformat())

    sym_entry = out["results"]["BTCUSDT"]
    expected_keys = {
        "symbol",
        "recommendation",
        "current_params",
        "current_val_pnl",
        "proposed_params",
        "proposal_detail",
    }
    assert set(sym_entry.keys()) == expected_keys
    assert sym_entry["recommendation"] == "KEEP_CURRENT"
    assert sym_entry["current_params"] == overrides["BTCUSDT"]
    assert sym_entry["proposed_params"] is None
    assert sym_entry["proposal_detail"] is None
    assert sym_entry["current_val_pnl"] == 0.0
