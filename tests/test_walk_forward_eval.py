"""Tests for `walk_forward.evaluate_window`.

This module covers commit 4 of #276 — running the strategy on the
fold's **test range** with the params produced by `tune_window`.

The real `auto_tune.run_backtest_with_params` loads OHLCV from disk,
runs the full bar-by-bar simulator, and takes seconds-to-minutes per
call. It is **always mocked here**: a unit test that lets it through
would block CI and would also break the test isolation contract.

Invariants exercised:

  1. `run_backtest_with_params` is called with `sim_start == test_start`
     and `sim_end == test_end`. The TRAIN range is never passed to the
     evaluation runner (the tuner already consumed it). The locked
     snapshot is never reached because `compute_windows` bounds
     `test_end <= holdout_start` (commit 1 contract).
  2. Per-symbol params come from the tune output: `proposed_params`
     when `recommendation == "CHANGE"`, `current_params` otherwise.
  3. The returned report shape carries `window_index`, `train_range`,
     `test_range`, `params`, `results[symbol].{n_trades, metrics, error}`,
     `skipped`, and the window-level `regime_tag` / `per_regime` (None here
     because these eval tests pass no `regime_data`; populated path is
     covered in `test_walk_forward_regime.py`).
  4. Degenerate test range (`test_start >= test_end`) returns the
     envelope with no runner calls.
  5. A symbol whose tune dict carries no usable params is recorded in
     `skipped`; the runner is not called for it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from walk_forward import Window, evaluate_window


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_window(
    test_start: date = date(2025, 4, 1),
    test_end: date = date(2025, 7, 1),
    train_start: date = date(2023, 1, 1),
    train_end: date = date(2025, 4, 1),
    index: int = 0,
) -> Window:
    """Build a Window with non-degenerate boundaries for evaluation tests."""
    return Window(
        index=index,
        train_start=train_start,
        train_end=train_end,
        test_start=test_start,
        test_end=test_end,
        warmup_gap_days=0,
    )


class _FakeAutoTune:
    """Stand-in for the `auto_tune` module.

    Records every `run_backtest_with_params` invocation so tests can
    assert on argument shape, call count, and per-symbol routing. The
    fake returns canned (trades, metrics) so the evaluator's projection
    logic can be exercised end-to-end.
    """

    def __init__(self, per_symbol_outputs: dict | None = None):
        # Per-symbol override: {symbol: (trades, metrics)}. Falls back
        # to a default 1-trade success metric when a symbol is not
        # named.
        self._outputs = per_symbol_outputs or {}
        self.calls: list[dict] = []

    def _default_output(self) -> tuple[list[dict], dict]:
        trades = [{"pnl_usd": 10.0, "exit_reason": "TP"}]
        metrics = {
            "total_trades": 1,
            "net_pnl": 10.0,
            "profit_factor": 1.5,
            "sharpe_ratio": 0.8,
            "max_drawdown_pct": -2.0,
            "win_rate": 100.0,
            "total_return_pct": 0.1,
            # Extra key that should NOT appear in the projection.
            "score_tiers": {},
        }
        return trades, metrics

    def run_backtest_with_params(
        self,
        symbol,
        params,
        sim_start,
        sim_end,
        *,
        cutoff=None,
        app_config=None,
    ):
        self.calls.append(
            {
                "symbol": symbol,
                "params": dict(params),
                "sim_start": sim_start,
                "sim_end": sim_end,
                "cutoff": cutoff,
                "app_config_id": id(app_config) if app_config is not None else None,
            }
        )
        if symbol in self._outputs:
            return self._outputs[symbol]
        return self._default_output()


@pytest.fixture
def patch_auto_tune(monkeypatch):
    """Inject `_FakeAutoTune` in place of the real module.

    `evaluate_window` imports `auto_tune` lazily inside the function,
    so we patch via `sys.modules` exactly like the tune-window tests.
    """

    def _install(per_symbol_outputs: dict | None = None) -> _FakeAutoTune:
        fake = _FakeAutoTune(per_symbol_outputs)
        import sys

        monkeypatch.setitem(sys.modules, "auto_tune", fake)
        return fake

    return _install


def _tune_result_change(symbol: str, sl: float, tp: float, be: float) -> dict:
    """Build a tune dict that recommends CHANGE → proposed_params used."""
    return {
        "symbol": symbol,
        "current_params": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5},
        "current_val_pnl": 0,
        "proposed_params": {"atr_sl_mult": sl, "atr_tp_mult": tp, "atr_be_mult": be},
        "proposal_detail": {"val_pnl": 100, "val_pf": 1.8},
        "recommendation": "CHANGE",
    }


def _tune_result_keep(symbol: str, sl: float = 1.0, tp: float = 4.0, be: float = 1.5) -> dict:
    """Build a tune dict that recommends KEEP → current_params used."""
    return {
        "symbol": symbol,
        "current_params": {"atr_sl_mult": sl, "atr_tp_mult": tp, "atr_be_mult": be},
        "current_val_pnl": 0,
        "proposed_params": None,
        "proposal_detail": None,
        "recommendation": "KEEP",
    }


# --------------------------------------------------------------------------- #
# Invariant 1: runner sees test range, NOT train range
# --------------------------------------------------------------------------- #


def test_runner_receives_test_range_not_train_range(patch_auto_tune):
    """The single most important assertion in this module.

    The evaluation runner must be called with `sim_start == test_start`
    and `sim_end == test_end`. If a future refactor accidentally passes
    `train_start`/`train_end` (or any cutoff that clips below
    `test_start`), every per-window report becomes meaningless. This
    test bites first.
    """
    fake = patch_auto_tune()
    window = _make_window(
        train_start=date(2023, 1, 1),
        train_end=date(2025, 4, 1),
        test_start=date(2025, 4, 1),
        test_end=date(2025, 7, 1),
    )
    tuned = {
        "window_index": 0,
        "train_end": "2025-04-01",
        "results": {"BTCUSDT": _tune_result_change("BTCUSDT", 1.2, 5.0, 1.8)},
    }

    evaluate_window(window, tuned, config={"symbol_overrides": {}})

    assert len(fake.calls) == 1, "exactly one runner call per active symbol"
    call = fake.calls[0]

    expected_start = datetime(2025, 4, 1, tzinfo=timezone.utc)
    expected_end = datetime(2025, 7, 1, tzinfo=timezone.utc)
    assert call["sim_start"] == expected_start, (
        f"sim_start must equal test_start at UTC midnight; "
        f"got {call['sim_start']!r}"
    )
    assert call["sim_end"] == expected_end, (
        f"sim_end must equal test_end at UTC midnight; "
        f"got {call['sim_end']!r}"
    )
    # Neither train boundary may ever surface on the evaluation call.
    train_start_dt = datetime(2023, 1, 1, tzinfo=timezone.utc)
    train_end_dt = datetime(2025, 4, 1, tzinfo=timezone.utc)
    assert call["sim_start"] != train_start_dt
    # train_end == test_start in the anchored adjacency case, so the
    # negative-equality check on sim_end is the load-bearing one:
    assert call["sim_end"] != train_end_dt


def test_runner_receives_no_cutoff(patch_auto_tune):
    """`cutoff` is a tune-side concept; evaluation must NOT pass it.

    Passing a `cutoff <= test_end` would strip the very bars we want to
    evaluate. The evaluator must let the runner see the full test
    range.
    """
    fake = patch_auto_tune()
    window = _make_window()
    tuned = {"results": {"BTCUSDT": _tune_result_keep("BTCUSDT")}}

    evaluate_window(window, tuned, config={"symbol_overrides": {}})

    assert fake.calls[0]["cutoff"] is None, (
        "evaluation runner must be called WITHOUT a cutoff so the test "
        "range is fully visible to the simulator"
    )


# --------------------------------------------------------------------------- #
# Invariant 2: params source — CHANGE uses proposed, KEEP uses current
# --------------------------------------------------------------------------- #


def test_change_recommendation_evaluates_proposed_params(patch_auto_tune):
    fake = patch_auto_tune()
    window = _make_window()
    tuned = {
        "results": {
            "BTCUSDT": _tune_result_change("BTCUSDT", sl=2.0, tp=6.0, be=2.5),
        },
    }

    report = evaluate_window(window, tuned, config={"symbol_overrides": {}})

    assert fake.calls[0]["params"] == {
        "atr_sl_mult": 2.0,
        "atr_tp_mult": 6.0,
        "atr_be_mult": 2.5,
    }
    assert report["params"]["BTCUSDT"]["atr_sl_mult"] == 2.0


def test_keep_recommendation_evaluates_current_params(patch_auto_tune):
    fake = patch_auto_tune()
    window = _make_window()
    tuned = {
        "results": {
            "ETHUSDT": _tune_result_keep("ETHUSDT", sl=1.1, tp=4.4, be=1.6),
        },
    }

    report = evaluate_window(window, tuned, config={"symbol_overrides": {}})

    assert fake.calls[0]["params"] == {
        "atr_sl_mult": 1.1,
        "atr_tp_mult": 4.4,
        "atr_be_mult": 1.6,
    }
    assert report["params"]["ETHUSDT"]["atr_sl_mult"] == 1.1


def test_multi_symbol_routes_params_per_symbol(patch_auto_tune):
    """Each symbol carries its own param set into its own runner call."""
    fake = patch_auto_tune()
    window = _make_window()
    tuned = {
        "results": {
            "BTCUSDT": _tune_result_change("BTCUSDT", 2.0, 6.0, 2.5),
            "ETHUSDT": _tune_result_keep("ETHUSDT", 1.0, 4.0, 1.5),
            "SOLUSDT": _tune_result_change("SOLUSDT", 1.5, 5.5, 2.0),
        },
    }

    evaluate_window(window, tuned, config={"symbol_overrides": {}})

    by_symbol = {c["symbol"]: c["params"] for c in fake.calls}
    assert by_symbol["BTCUSDT"]["atr_sl_mult"] == 2.0
    assert by_symbol["ETHUSDT"]["atr_sl_mult"] == 1.0
    assert by_symbol["SOLUSDT"]["atr_sl_mult"] == 1.5


# --------------------------------------------------------------------------- #
# Invariant 3: report structure
# --------------------------------------------------------------------------- #


def test_report_has_declared_shape(patch_auto_tune):
    patch_auto_tune()
    window = _make_window(index=7)
    tuned = {"results": {"BTCUSDT": _tune_result_change("BTCUSDT", 1.2, 5.0, 1.8)}}

    report = evaluate_window(window, tuned, config={"symbol_overrides": {}})

    # Top-level keys.
    for key in (
        "window_index", "train_range", "test_range", "params", "results",
        "skipped", "regime_tag", "per_regime",
    ):
        assert key in report, f"missing top-level key: {key}"

    assert report["window_index"] == 7
    assert report["train_range"] == {"start": "2023-01-01", "end": "2025-04-01"}
    assert report["test_range"] == {"start": "2025-04-01", "end": "2025-07-01"}

    # regime_tag / per_regime are window-level (#536) and stay None when no
    # regime_data is supplied — these eval tests never pass it.
    assert report["regime_tag"] is None
    assert report["per_regime"] is None

    # Per-symbol entry — the old per-symbol `regime_tag` placeholder is retired.
    entry = report["results"]["BTCUSDT"]
    for key in ("n_trades", "metrics", "error"):
        assert key in entry, f"missing per-symbol key: {key}"
    assert "regime_tag" not in entry, "regime_tag is now window-level, not per-symbol"

    assert entry["n_trades"] == 1
    assert entry["error"] is None


def test_metrics_projection_keeps_only_report_keys(patch_auto_tune):
    """The projection must drop unrelated metric keys (e.g. score_tiers)
    and surface the seven canonical ones when present."""
    patch_auto_tune()
    window = _make_window()
    tuned = {"results": {"BTCUSDT": _tune_result_change("BTCUSDT", 1.2, 5.0, 1.8)}}

    report = evaluate_window(window, tuned, config={"symbol_overrides": {}})
    metrics = report["results"]["BTCUSDT"]["metrics"]

    expected_keys = {
        "total_trades",
        "net_pnl",
        "profit_factor",
        "sharpe_ratio",
        "max_drawdown_pct",
        "win_rate",
        "total_return_pct",
    }
    assert set(metrics.keys()) == expected_keys
    # The extra key the fake returned must not surface.
    assert "score_tiers" not in metrics


def test_runner_error_sentinel_surfaces_in_report(patch_auto_tune):
    """When the runner returns the `{"error": ..., ...}` sentinel (no
    trades or no data), the report must capture the error string and
    `n_trades == 0`."""
    sentinel = (
        [],
        {"error": "No trades", "total_trades": 0, "net_pnl": 0, "profit_factor": 0},
    )
    patch_auto_tune({"BTCUSDT": sentinel})
    window = _make_window()
    tuned = {"results": {"BTCUSDT": _tune_result_change("BTCUSDT", 1.2, 5.0, 1.8)}}

    report = evaluate_window(window, tuned, config={"symbol_overrides": {}})
    entry = report["results"]["BTCUSDT"]

    assert entry["error"] == "No trades"
    assert entry["n_trades"] == 0


# --------------------------------------------------------------------------- #
# Invariant 4: degenerate test range — no runner calls
# --------------------------------------------------------------------------- #


def test_empty_test_range_skips_runner(patch_auto_tune):
    """When `test_start >= test_end`, the function returns the envelope
    without invoking the runner. Defensive — `compute_windows` does not
    emit such folds today, but a hand-rolled `Window` could."""
    fake = patch_auto_tune()
    window = Window(
        index=3,
        train_start=date(2023, 1, 1),
        train_end=date(2025, 7, 1),
        test_start=date(2025, 7, 1),
        test_end=date(2025, 7, 1),  # equal → empty range
        warmup_gap_days=0,
    )
    tuned = {"results": {"BTCUSDT": _tune_result_change("BTCUSDT", 1.2, 5.0, 1.8)}}

    report = evaluate_window(window, tuned, config={"symbol_overrides": {}})

    assert fake.calls == [], "empty test range must not invoke the runner"
    assert report["window_index"] == 3
    assert report["results"] == {}
    assert report["skipped"] == []
    # Range metadata still reflects the (degenerate) input.
    assert report["test_range"]["start"] == report["test_range"]["end"]


def test_inverted_test_range_skips_runner(patch_auto_tune):
    """`test_start > test_end` is also treated as degenerate. No runner
    call; empty results."""
    fake = patch_auto_tune()
    window = Window(
        index=0,
        train_start=date(2023, 1, 1),
        train_end=date(2025, 7, 1),
        test_start=date(2025, 8, 1),
        test_end=date(2025, 7, 1),
        warmup_gap_days=0,
    )
    tuned = {"results": {"BTCUSDT": _tune_result_change("BTCUSDT", 1.2, 5.0, 1.8)}}

    report = evaluate_window(window, tuned, config={"symbol_overrides": {}})

    assert fake.calls == []
    assert report["results"] == {}


# --------------------------------------------------------------------------- #
# Invariant 5: unusable params land in `skipped`
# --------------------------------------------------------------------------- #


def test_symbol_with_no_usable_params_is_skipped(patch_auto_tune):
    """A tune result with neither proposed nor current params (ERROR
    path with empty current) must be recorded in `skipped`, NOT
    silently dropped, NOT invoked on the runner."""
    fake = patch_auto_tune()
    window = _make_window()
    tuned = {
        "results": {
            "BTCUSDT": _tune_result_change("BTCUSDT", 1.2, 5.0, 1.8),
            "BROKEN":  {
                "symbol": "BROKEN",
                "current_params": None,  # corrupt
                "proposed_params": None,
                "recommendation": "ERROR",
            },
        },
    }

    report = evaluate_window(window, tuned, config={"symbol_overrides": {}})

    # Only BTCUSDT reaches the runner.
    assert [c["symbol"] for c in fake.calls] == ["BTCUSDT"]
    # BROKEN lands in skipped with a reason.
    assert report["skipped"] == [
        {"symbol": "BROKEN", "reason": "no_usable_params"},
    ]
    # And BROKEN does NOT show up in results.
    assert "BROKEN" not in report["results"]


def test_no_data_recommendation_falls_back_to_current(patch_auto_tune):
    """`recommendation == "NO_DATA"` still carries current_params; the
    evaluator must fall back to those rather than skip."""
    fake = patch_auto_tune()
    window = _make_window()
    tuned = {
        "results": {
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "current_params": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5},
                "current_val_pnl": 0,
                "proposed_params": None,
                "proposal_detail": None,
                "recommendation": "NO_DATA",
            },
        },
    }

    report = evaluate_window(window, tuned, config={"symbol_overrides": {}})

    assert len(fake.calls) == 1
    assert fake.calls[0]["params"]["atr_sl_mult"] == 1.0
    assert "BTCUSDT" in report["results"]
    assert report["skipped"] == []


# --------------------------------------------------------------------------- #
# Empty tune result envelope
# --------------------------------------------------------------------------- #


def test_empty_tune_results_yields_empty_report(patch_auto_tune):
    fake = patch_auto_tune()
    window = _make_window()
    tuned = {"window_index": 0, "train_end": "2025-04-01", "results": {}}

    report = evaluate_window(window, tuned, config={"symbol_overrides": {}})

    assert fake.calls == []
    assert report["results"] == {}
    assert report["skipped"] == []
