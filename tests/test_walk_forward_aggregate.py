"""Tests for `walk_forward.run_walk_forward` and `aggregate_run_stats`.

Covers commit 5 of #276 — the orchestrator loop that drives
`tune_window` → `evaluate_window` over a list of folds, and the
aggregator that summarises a `WalkForwardRun` into cross-window stats
(OOS/IS, CV, best/worst, counts).

Test isolation contract:
  * `auto_tune.optimize_symbol` and `auto_tune.run_backtest_with_params`
    are NEVER invoked here. The orchestrator tests monkeypatch
    `walk_forward.tune_window` and `walk_forward.evaluate_window`
    directly with fakes; the aggregator tests build window reports as
    hand-rolled fixtures. The locked holdout snapshot is never read —
    no fold's `test_end` is allowed to exceed a `holdout_start`
    boundary in any fixture.

Invariants exercised:
  1. The orchestrator iterates the window list in order, calls
     `tune_window` then `evaluate_window` per window, and passes the
     tune output verbatim into the eval.
  2. An empty window list returns an empty run without raising.
  3. A per-window failure in tune or eval is captured on that window's
     report under `"error"`; subsequent windows still run.
  4. The orchestrator attaches `is_pnl_by_symbol` to each window's
     report so the aggregator can compute a real OOS/IS ratio.
  5. `aggregate_run_stats` computes OOS/IS on `net_pnl`, CV across the
     six ratio/return metrics, best/worst window on `sharpe_ratio`
     (with `total_return_pct` fallback), and counters
     (n_windows, n_windows_with_trades, n_skipped, n_errored,
     total_trades).
  6. Degenerate cases — empty run, single-window CV, all-windows
     errored, near-zero IS — produce structured `None` with an
     explicit `reason` rather than fabricated numbers.
"""

from __future__ import annotations

from datetime import date

import pytest

from walk_forward import (
    WalkForwardRun,
    Window,
    aggregate_run_stats,
    run_walk_forward,
)


# --------------------------------------------------------------------------- #
# Helpers — Window + report fixtures
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


def _three_windows() -> list[Window]:
    return [
        Window(
            index=i,
            train_start=date(2023, 1, 1),
            train_end=date(2024, 1 + i, 1),
            test_start=date(2024, 1 + i, 1),
            test_end=date(2024, 4 + i, 1),
            warmup_gap_days=0,
        )
        for i in range(3)
    ]


def _sym_entry(
    n_trades: int = 1,
    net_pnl: float = 100.0,
    sharpe: float = 1.5,
    pf: float = 2.0,
    dd: float = -5.0,
    wr: float = 60.0,
    tr_pct: float = 5.0,
    error: str | None = None,
) -> dict:
    return {
        "n_trades": n_trades,
        "metrics": {
            "total_trades": n_trades,
            "net_pnl": net_pnl,
            "profit_factor": pf,
            "sharpe_ratio": sharpe,
            "max_drawdown_pct": dd,
            "win_rate": wr,
            "total_return_pct": tr_pct,
        },
        "regime_tag": None,
        "error": error,
    }


def _window_report(
    index: int,
    results: dict | None = None,
    skipped: list | None = None,
    is_pnl_by_symbol: dict | None = None,
    error: dict | None = None,
) -> dict:
    r: dict = {
        "window_index": index,
        "train_range": {"start": "2023-01-01", "end": "2024-01-01"},
        "test_range": {"start": "2024-01-01", "end": "2024-04-01"},
        "params": {sym: {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5}
                   for sym in (results or {}).keys()},
        "results": results or {},
        "skipped": skipped or [],
    }
    if is_pnl_by_symbol is not None:
        r["is_pnl_by_symbol"] = is_pnl_by_symbol
    if error is not None:
        r["error"] = error
    return r


# --------------------------------------------------------------------------- #
# Orchestrator — Invariant 1: iterates in order, passes tuned -> eval
# --------------------------------------------------------------------------- #


def test_orchestrator_iterates_windows_in_order(monkeypatch):
    """tune_window and evaluate_window must be called once per window,
    in the same order as the input list, and the tune output must reach
    evaluate_window verbatim."""
    import walk_forward as wf

    calls: list[tuple[str, int, object]] = []

    def fake_tune(window, config):
        out = {
            "window_index": window.index,
            "train_end": window.train_end.isoformat(),
            "results": {"BTCUSDT": {
                "current_params": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5},
                "current_val_pnl": 50.0 + window.index,
                "proposed_params": None,
                "proposal_detail": None,
                "recommendation": "KEEP",
            }},
        }
        calls.append(("tune", window.index, out))
        return out

    def fake_eval(window, tuned, config, regime_data=None):
        calls.append(("eval", window.index, tuned))
        return _window_report(
            window.index,
            results={"BTCUSDT": _sym_entry(net_pnl=100.0 + window.index)},
        )

    monkeypatch.setattr(wf, "tune_window", fake_tune)
    monkeypatch.setattr(wf, "evaluate_window", fake_eval)

    windows = _three_windows()
    run = run_walk_forward(windows, app_config={"symbol_overrides": {}})

    # Call ordering: tune(0), eval(0), tune(1), eval(1), tune(2), eval(2).
    phases = [(p, i) for p, i, _ in calls]
    assert phases == [
        ("tune", 0), ("eval", 0),
        ("tune", 1), ("eval", 1),
        ("tune", 2), ("eval", 2),
    ]

    # The tuned dict reaching eval(i) must be identity-equal to the
    # object tune(i) returned — no munging in between.
    tune_outputs = [obj for p, _, obj in calls if p == "tune"]
    eval_inputs = [obj for p, _, obj in calls if p == "eval"]
    for tout, ein in zip(tune_outputs, eval_inputs):
        assert tout is ein, "tune output must reach eval verbatim"

    # Run shape: 3 windows, 3 reports, indices preserved.
    assert isinstance(run, WalkForwardRun)
    assert len(run.window_reports) == 3
    assert [r["window_index"] for r in run.window_reports] == [0, 1, 2]


def test_orchestrator_attaches_is_pnl_block(monkeypatch):
    """The orchestrator must attach `is_pnl_by_symbol` to every
    successful report so the aggregator can compute a real OOS/IS."""
    import walk_forward as wf

    def fake_tune(window, config):
        # CHANGE → proposal_detail.val_pnl is the IS for the symbol.
        return {
            "results": {
                "BTCUSDT": {
                    "current_params": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5},
                    "current_val_pnl": 10.0,
                    "proposed_params": {"atr_sl_mult": 1.2, "atr_tp_mult": 5.0, "atr_be_mult": 1.8},
                    "proposal_detail": {"val_pnl": 77.0, "val_pf": 1.7},
                    "recommendation": "CHANGE",
                },
                "ETHUSDT": {
                    "current_params": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5},
                    "current_val_pnl": -5.0,
                    "proposed_params": None,
                    "proposal_detail": None,
                    "recommendation": "KEEP",
                },
            },
        }

    def fake_eval(window, tuned, config, regime_data=None):
        # Both symbols reached the runner; both get embedded in params.
        return _window_report(
            window.index,
            results={
                "BTCUSDT": _sym_entry(net_pnl=110.0),
                "ETHUSDT": _sym_entry(net_pnl=-3.0),
            },
        )

    monkeypatch.setattr(wf, "tune_window", fake_tune)
    monkeypatch.setattr(wf, "evaluate_window", fake_eval)

    run = run_walk_forward([_make_window(0)], app_config={"symbol_overrides": {}})

    assert len(run.window_reports) == 1
    block = run.window_reports[0]["is_pnl_by_symbol"]
    # CHANGE → val_pnl of proposal_detail. KEEP → current_val_pnl.
    assert block == {"BTCUSDT": 77.0, "ETHUSDT": -5.0}


# --------------------------------------------------------------------------- #
# Orchestrator — Invariant 2: empty window list
# --------------------------------------------------------------------------- #


def test_orchestrator_empty_windows_yields_empty_run(monkeypatch):
    import walk_forward as wf

    # The fakes must not be invoked at all. If they are, the test fails
    # via the explicit pytest.fail() — easier to read than a call counter.
    def boom_tune(*a, **kw):
        pytest.fail("tune_window must not be called on empty window list")

    def boom_eval(*a, **kw):
        pytest.fail("evaluate_window must not be called on empty window list")

    monkeypatch.setattr(wf, "tune_window", boom_tune)
    monkeypatch.setattr(wf, "evaluate_window", boom_eval)

    run = run_walk_forward([], app_config={})
    assert isinstance(run, WalkForwardRun)
    assert run.windows == []
    assert run.window_reports == []


# --------------------------------------------------------------------------- #
# Orchestrator — Invariant 3: best-effort across folds on per-window error
# --------------------------------------------------------------------------- #


def test_orchestrator_continues_after_tune_failure(monkeypatch):
    """If `tune_window` raises on a fold, the orchestrator records the
    error on that fold's report and continues with the rest."""
    import walk_forward as wf

    def fake_tune(window, config):
        if window.index == 1:
            raise RuntimeError("synthetic tune failure on window 1")
        return {"results": {"BTCUSDT": {
            "current_params": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5},
            "current_val_pnl": 50.0,
            "proposed_params": None,
            "proposal_detail": None,
            "recommendation": "KEEP",
        }}}

    eval_called_on: list[int] = []

    def fake_eval(window, tuned, config, regime_data=None):
        eval_called_on.append(window.index)
        return _window_report(
            window.index,
            results={"BTCUSDT": _sym_entry(net_pnl=100.0)},
        )

    monkeypatch.setattr(wf, "tune_window", fake_tune)
    monkeypatch.setattr(wf, "evaluate_window", fake_eval)

    run = run_walk_forward(_three_windows(), app_config={})

    # evaluate_window must NOT be called for the failed-tune fold.
    assert eval_called_on == [0, 2]

    # Report shape: failed fold carries `error`, others do not.
    assert "error" in run.window_reports[1]
    assert run.window_reports[1]["error"]["phase"] == "tune"
    assert run.window_reports[1]["error"]["type"] == "RuntimeError"
    assert "synthetic tune failure" in run.window_reports[1]["error"]["message"]
    assert "error" not in run.window_reports[0]
    assert "error" not in run.window_reports[2]
    # Successful folds carry the IS-pnl block; errored fold does not.
    assert "is_pnl_by_symbol" in run.window_reports[0]
    assert "is_pnl_by_symbol" not in run.window_reports[1]


def test_orchestrator_continues_after_eval_failure(monkeypatch):
    """If `evaluate_window` raises, the fold gets an error report with
    phase=='evaluate' and the loop continues."""
    import walk_forward as wf

    def fake_tune(window, config):
        return {"results": {"BTCUSDT": {
            "current_params": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5},
            "current_val_pnl": 50.0,
            "proposed_params": None,
            "proposal_detail": None,
            "recommendation": "KEEP",
        }}}

    def fake_eval(window, tuned, config, regime_data=None):
        if window.index == 0:
            raise ValueError("synthetic eval failure on window 0")
        return _window_report(
            window.index,
            results={"BTCUSDT": _sym_entry(net_pnl=100.0)},
        )

    monkeypatch.setattr(wf, "tune_window", fake_tune)
    monkeypatch.setattr(wf, "evaluate_window", fake_eval)

    run = run_walk_forward(_three_windows(), app_config={})

    assert "error" in run.window_reports[0]
    assert run.window_reports[0]["error"]["phase"] == "evaluate"
    assert run.window_reports[0]["error"]["type"] == "ValueError"
    # The other two folds completed normally.
    assert "error" not in run.window_reports[1]
    assert "error" not in run.window_reports[2]


# --------------------------------------------------------------------------- #
# Aggregator — counts, CV, OOS/IS, best/worst
# --------------------------------------------------------------------------- #


def test_aggregate_counts_basic():
    """Three windows, two with trades, one with zero trades, plus an
    error fold → aggregated counters reflect each bucket."""
    reports = [
        _window_report(
            0,
            results={"BTCUSDT": _sym_entry(n_trades=3, net_pnl=120.0)},
            is_pnl_by_symbol={"BTCUSDT": 80.0},
        ),
        _window_report(
            1,
            results={"BTCUSDT": _sym_entry(n_trades=0, net_pnl=0.0, sharpe=0.0)},
            is_pnl_by_symbol={"BTCUSDT": 5.0},
        ),
        _window_report(
            2,
            results={"BTCUSDT": _sym_entry(n_trades=5, net_pnl=200.0)},
            is_pnl_by_symbol={"BTCUSDT": 150.0},
        ),
        _window_report(
            3,
            error={"phase": "tune", "type": "RuntimeError", "message": "x"},
        ),
    ]
    run = WalkForwardRun(windows=[_make_window(i) for i in range(4)], window_reports=reports)
    stats = aggregate_run_stats(run)

    assert stats["n_windows"] == 4
    assert stats["n_windows_with_trades"] == 2
    assert stats["n_windows_errored"] == 1
    assert stats["total_trades"] == 8


def test_aggregate_oos_is_ratio_on_net_pnl():
    """OOS/IS = sum(OOS net_pnl) / sum(IS val_pnl) across paired
    (symbol, window) cells."""
    reports = [
        _window_report(
            0,
            results={
                "BTCUSDT": _sym_entry(net_pnl=120.0),
                "ETHUSDT": _sym_entry(net_pnl=80.0),
            },
            is_pnl_by_symbol={"BTCUSDT": 100.0, "ETHUSDT": 50.0},
        ),
        _window_report(
            1,
            results={"BTCUSDT": _sym_entry(net_pnl=50.0)},
            is_pnl_by_symbol={"BTCUSDT": 50.0},
        ),
    ]
    run = WalkForwardRun(windows=[_make_window(0), _make_window(1)], window_reports=reports)
    stats = aggregate_run_stats(run)

    # OOS total = 120 + 80 + 50 = 250
    # IS total  = 100 + 50 + 50 = 200
    # ratio     = 250 / 200 = 1.25
    assert stats["oos_is_ratio"]["metric"] == "net_pnl"
    assert stats["oos_is_ratio"]["value"] == pytest.approx(1.25)
    assert stats["oos_is_ratio"]["n"] == 3


def test_aggregate_oos_is_ratio_missing_block_degrades():
    """If reports lack `is_pnl_by_symbol`, the ratio must come back
    `None` with a reason — not fabricated."""
    reports = [
        _window_report(0, results={"BTCUSDT": _sym_entry(net_pnl=100.0)}),
        _window_report(1, results={"BTCUSDT": _sym_entry(net_pnl=50.0)}),
    ]
    run = WalkForwardRun(windows=[_make_window(0), _make_window(1)], window_reports=reports)
    stats = aggregate_run_stats(run)

    assert stats["oos_is_ratio"]["value"] is None
    assert stats["oos_is_ratio"]["reason"] == "is_pnl_not_on_report"


def test_aggregate_oos_is_ratio_near_zero_is_degrades():
    """A near-zero IS total must surface `None` with reason rather than
    blow up to infinity."""
    reports = [
        _window_report(
            0,
            results={"BTCUSDT": _sym_entry(net_pnl=50.0)},
            is_pnl_by_symbol={"BTCUSDT": 0.0},
        ),
        _window_report(
            1,
            results={"BTCUSDT": _sym_entry(net_pnl=30.0)},
            is_pnl_by_symbol={"BTCUSDT": 0.0},
        ),
    ]
    run = WalkForwardRun(windows=[_make_window(0), _make_window(1)], window_reports=reports)
    stats = aggregate_run_stats(run)

    assert stats["oos_is_ratio"]["value"] is None
    assert stats["oos_is_ratio"]["reason"] == "is_pnl_near_zero"


def test_aggregate_cv_across_windows():
    """CV = std/mean across (window, symbol) cells per metric."""
    # Three windows, one symbol, Sharpe values 1.0, 2.0, 3.0
    # mean = 2.0, population stdev = sqrt((1+0+1)/3) = sqrt(2/3) ≈ 0.8165
    # CV ≈ 0.4082
    reports = [
        _window_report(
            i,
            results={"BTCUSDT": _sym_entry(sharpe=float(i + 1))},
            is_pnl_by_symbol={"BTCUSDT": 50.0},
        )
        for i in range(3)
    ]
    run = WalkForwardRun(windows=[_make_window(i) for i in range(3)], window_reports=reports)
    stats = aggregate_run_stats(run)

    cv_sharpe = stats["cv"]["sharpe_ratio"]
    assert cv_sharpe["n"] == 3
    assert cv_sharpe["value"] == pytest.approx((2.0 / 3.0) ** 0.5 / 2.0, rel=1e-6)


def test_aggregate_cv_single_window_is_undefined():
    """One data point → CV is None with reason 'single_sample'."""
    reports = [
        _window_report(
            0,
            results={"BTCUSDT": _sym_entry(sharpe=1.5)},
            is_pnl_by_symbol={"BTCUSDT": 50.0},
        ),
    ]
    run = WalkForwardRun(windows=[_make_window(0)], window_reports=reports)
    stats = aggregate_run_stats(run)

    assert stats["cv"]["sharpe_ratio"]["value"] is None
    assert stats["cv"]["sharpe_ratio"]["reason"] == "single_sample"


def test_aggregate_cv_zero_mean_is_undefined():
    """Mean near zero → CV is None with reason 'mean_near_zero'."""
    reports = [
        _window_report(
            0,
            results={"BTCUSDT": _sym_entry(sharpe=1.0)},
            is_pnl_by_symbol={"BTCUSDT": 50.0},
        ),
        _window_report(
            1,
            results={"BTCUSDT": _sym_entry(sharpe=-1.0)},
            is_pnl_by_symbol={"BTCUSDT": 50.0},
        ),
    ]
    run = WalkForwardRun(windows=[_make_window(0), _make_window(1)], window_reports=reports)
    stats = aggregate_run_stats(run)

    assert stats["cv"]["sharpe_ratio"]["value"] is None
    assert stats["cv"]["sharpe_ratio"]["reason"] == "mean_near_zero"


def test_aggregate_best_worst_window_on_sharpe():
    """Best/worst indexed on Sharpe (default metric)."""
    reports = [
        _window_report(
            0,
            results={"BTCUSDT": _sym_entry(sharpe=0.5)},
            is_pnl_by_symbol={"BTCUSDT": 50.0},
        ),
        _window_report(
            1,
            results={"BTCUSDT": _sym_entry(sharpe=2.5)},
            is_pnl_by_symbol={"BTCUSDT": 50.0},
        ),
        _window_report(
            2,
            results={"BTCUSDT": _sym_entry(sharpe=-1.0)},
            is_pnl_by_symbol={"BTCUSDT": 50.0},
        ),
    ]
    run = WalkForwardRun(windows=[_make_window(i) for i in range(3)], window_reports=reports)
    stats = aggregate_run_stats(run)

    assert stats["best_window"] == {"index": 1, "metric": "sharpe_ratio", "value": 2.5}
    assert stats["worst_window"] == {"index": 2, "metric": "sharpe_ratio", "value": -1.0}


def test_aggregate_best_worst_falls_back_to_total_return_when_no_sharpe():
    """If reports carry total_return_pct but no sharpe_ratio, the
    ranking falls back to total_return_pct."""
    reports = []
    for i, tr in enumerate((1.0, 5.0, -2.0)):
        entry = _sym_entry(tr_pct=tr)
        del entry["metrics"]["sharpe_ratio"]  # remove sharpe entirely
        reports.append(_window_report(
            i,
            results={"BTCUSDT": entry},
            is_pnl_by_symbol={"BTCUSDT": 50.0},
        ))
    run = WalkForwardRun(windows=[_make_window(i) for i in range(3)], window_reports=reports)
    stats = aggregate_run_stats(run)

    assert stats["best_window"]["metric"] == "total_return_pct"
    assert stats["best_window"]["index"] == 1
    assert stats["worst_window"]["index"] == 2


def test_aggregate_empty_run():
    """An empty run produces all-zero counters and None ratios with
    explicit reasons — not a crash."""
    run = WalkForwardRun(windows=[], window_reports=[])
    stats = aggregate_run_stats(run)

    assert stats["n_windows"] == 0
    assert stats["n_windows_with_trades"] == 0
    assert stats["n_windows_errored"] == 0
    assert stats["total_trades"] == 0
    assert stats["oos_is_ratio"]["value"] is None
    assert stats["best_window"] is None
    assert stats["worst_window"] is None
    for key in (
        "net_pnl", "profit_factor", "sharpe_ratio",
        "max_drawdown_pct", "win_rate", "total_return_pct",
    ):
        assert stats["cv"][key]["value"] is None
        assert stats["cv"][key]["reason"] == "no_samples"


def test_aggregate_all_windows_errored():
    """When every fold is an error report, OOS/IS comes back with the
    paired-zero reason and best/worst are None."""
    reports = [
        _window_report(0, error={"phase": "tune", "type": "RuntimeError", "message": "a"}),
        _window_report(1, error={"phase": "evaluate", "type": "ValueError", "message": "b"}),
    ]
    run = WalkForwardRun(windows=[_make_window(0), _make_window(1)], window_reports=reports)
    stats = aggregate_run_stats(run)

    assert stats["n_windows"] == 2
    assert stats["n_windows_errored"] == 2
    assert stats["n_windows_with_trades"] == 0
    assert stats["total_trades"] == 0
    assert stats["best_window"] is None
    assert stats["worst_window"] is None
    assert stats["oos_is_ratio"]["value"] is None


# --------------------------------------------------------------------------- #
# End-to-end: orchestrator output feeds aggregator without ceremony
# --------------------------------------------------------------------------- #


def test_run_and_aggregate_end_to_end(monkeypatch):
    """Drive the orchestrator on three folds with deterministic fakes,
    then hand the resulting `WalkForwardRun` to the aggregator. This
    is the contract the harness will exercise in production: build
    windows → run → aggregate."""
    import walk_forward as wf

    def fake_tune(window, config):
        # Deterministic IS pnl per window — distinct values so the
        # paired OOS/IS arithmetic is unambiguous.
        return {
            "results": {"BTCUSDT": {
                "current_params": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5},
                "current_val_pnl": 100.0 + window.index * 10.0,
                "proposed_params": None,
                "proposal_detail": None,
                "recommendation": "KEEP",
            }},
        }

    def fake_eval(window, tuned, config, regime_data=None):
        return _window_report(
            window.index,
            results={"BTCUSDT": _sym_entry(
                n_trades=2,
                net_pnl=200.0 + window.index * 10.0,
                sharpe=float(window.index + 1),
            )},
        )

    monkeypatch.setattr(wf, "tune_window", fake_tune)
    monkeypatch.setattr(wf, "evaluate_window", fake_eval)

    run = run_walk_forward(_three_windows(), app_config={})
    stats = aggregate_run_stats(run)

    # IS totals: 100 + 110 + 120 = 330
    # OOS totals: 200 + 210 + 220 = 630
    # Ratio: 630 / 330 ≈ 1.909
    assert stats["oos_is_ratio"]["value"] == pytest.approx(630.0 / 330.0, rel=1e-6)
    assert stats["total_trades"] == 6
    assert stats["n_windows_with_trades"] == 3
    assert stats["best_window"]["index"] == 2
    assert stats["worst_window"]["index"] == 0
