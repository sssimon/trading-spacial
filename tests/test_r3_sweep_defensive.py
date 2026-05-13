"""Defensive-output tests for `tools.r3_trend_pullback_sweep`.

Mirrors `tests/test_r1_sweep_defensive.py` patterns for R3:
  - `_save_json` rejects NaN / Inf payloads.
  - `_summarize_worker_errors` uses `is not None` filter (#332 item 4 hardened).
  - `_emit_worker_error_summary` writes to stderr (#332 item 5 hardened).
  - End-to-end wiring test for `_run_jobs_parallel` → `_emit_worker_error_summary`.
"""
from __future__ import annotations

import json
import math

import pytest

from tools.r3_trend_pullback_sweep import _save_json


# ─────────────────────────────────────────────────────────────────────────────
# _save_json — fail-loud on inf / NaN
# ─────────────────────────────────────────────────────────────────────────────


def test_save_json_rejects_nan(tmp_path):
    """NaN must raise rather than serialize to invalid JSON."""
    payload = {"net_pnl": math.nan}
    with pytest.raises(ValueError):
        _save_json(tmp_path / "out.json", payload)


def test_save_json_rejects_inf(tmp_path):
    """Infinity must raise rather than serialize to invalid JSON."""
    payload = {"net_pnl": math.inf}
    with pytest.raises(ValueError):
        _save_json(tmp_path / "out.json", payload)


def test_save_json_rejects_negative_inf_inside_nested_list(tmp_path):
    """allow_nan=False applies to nested structures too."""
    payload = {"results": [{"value": float("-inf")}]}
    with pytest.raises(ValueError):
        _save_json(tmp_path / "out.json", payload)


def test_save_json_writes_normal_payload(tmp_path):
    """Happy path: finite numbers serialize and round-trip cleanly."""
    payload = {"net_pnl": -1234.56, "n_trades": 42, "label": "ok"}
    target = tmp_path / "out.json"
    _save_json(target, payload)
    assert json.loads(target.read_text()) == payload


# ─────────────────────────────────────────────────────────────────────────────
# _summarize_worker_errors — is_not_none filter (#332 item 4 hardened pattern)
# ─────────────────────────────────────────────────────────────────────────────


def test_summarize_worker_errors_returns_none_on_clean_results():
    """No `error` field on any result → no summary message."""
    from tools.r3_trend_pullback_sweep import _summarize_worker_errors

    results = [{"symbol": "BTCUSDT", "n_trades": 5}, {"symbol": "ETHUSDT", "n_trades": 3}]
    assert _summarize_worker_errors(results) is None


def test_summarize_worker_errors_counts_total_and_distinct():
    """Workers emitting `error` → message counts total errors + distinct messages."""
    from tools.r3_trend_pullback_sweep import _summarize_worker_errors

    results = [
        {"symbol": "BTCUSDT", "error": "ValueError: bad data"},
        {"symbol": "ETHUSDT", "error": "ValueError: bad data"},
        {"symbol": "ADAUSDT", "error": "RuntimeError: cutoff drift"},
        {"symbol": "AVAXUSDT", "n_trades": 5},
    ]
    msg = _summarize_worker_errors(results)
    assert msg is not None
    assert "3 workers errored" in msg
    assert "2 distinct" in msg


def test_summarize_worker_errors_uses_is_not_none_filter_not_truthy():
    """Closes #332 item 4: empty-string `error` field must count, not silently filter.

    Mirrors R1 sweep hardened pattern. Producer `_process_cell` currently only
    writes the field when non-empty (safe in practice), but `is not None`
    filter hardens the contract against future producer changes.
    """
    from tools.r3_trend_pullback_sweep import _summarize_worker_errors

    results = [
        {"symbol": "BTCUSDT", "error": ""},   # Explicit empty string
        {"symbol": "ETHUSDT"},                 # No error key at all
    ]
    msg = _summarize_worker_errors(results)
    assert msg is not None, (
        "Empty-string error must be counted (is not None semantics), "
        "not silently filtered by truthy check"
    )
    assert "1 workers errored" in msg


# ─────────────────────────────────────────────────────────────────────────────
# _emit_worker_error_summary — stderr wiring (#332 item 5 hardened pattern)
# ─────────────────────────────────────────────────────────────────────────────


def test_emit_worker_error_summary_writes_to_stderr_when_errors_present(capsys):
    """Errors → stderr emission. Mirrors R1 wiring test."""
    from tools.r3_trend_pullback_sweep import _emit_worker_error_summary

    results = [
        {"symbol": "BTCUSDT", "error": "ValueError: bad data"},
        {"symbol": "ETHUSDT", "error": "RuntimeError: cutoff drift"},
    ]
    _emit_worker_error_summary(results)
    captured = capsys.readouterr()
    assert "2 workers errored" in captured.err
    assert "ValueError: bad data" in captured.err
    assert "RuntimeError: cutoff drift" in captured.err
    assert captured.out == "", "Summary must go to stderr, not stdout"


def test_emit_worker_error_summary_silent_on_clean_results(capsys):
    """No errors → no stderr emission (avoids noise)."""
    from tools.r3_trend_pullback_sweep import _emit_worker_error_summary

    results = [
        {"symbol": "BTCUSDT", "n_trades": 5},
        {"symbol": "ETHUSDT", "n_trades": 3},
    ]
    _emit_worker_error_summary(results)
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_run_jobs_parallel_writes_error_summary_to_stderr_end_to_end(
    capsys, monkeypatch
):
    """End-to-end wiring test: _run_jobs_parallel → _emit_worker_error_summary.

    Mirrors R1 end-to-end wiring test (#332 item 5 hardened pattern).
    Monkeypatches `Pool` with a `FakePool` to return synthetic results
    deterministically; asserts error summary reaches `captured.err`.
    """
    from tools import r3_trend_pullback_sweep

    synthetic_results = [
        {"symbol": "BTCUSDT", "error": "ValueError: synthetic worker failure",
         "n_trades": 0, "sl": 1.0, "be": 1.5, "pullback_distance": 0.5,
         "exit_reasons": {}},
        {"symbol": "ETHUSDT", "n_trades": 5,
         "sl": 1.0, "be": 1.5, "pullback_distance": 0.5,
         "exit_reasons": {}, "net_pnl": 10.0},
    ]

    class _FakePool:
        """Minimal Pool replacement: context manager + map returning synthetic results."""

        def __init__(self, n_workers):
            self.n_workers = n_workers

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def map(self, fn, jobs):
            return synthetic_results

    monkeypatch.setattr(r3_trend_pullback_sweep, "Pool", _FakePool)

    jobs = [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}]
    results = r3_trend_pullback_sweep._run_jobs_parallel(jobs, workers=2, label="e2e_test")

    captured = capsys.readouterr()
    assert "1 workers errored" in captured.err, (
        "Expected error summary in stderr — wiring "
        "_run_jobs_parallel → _emit_worker_error_summary may be broken"
    )
    assert "ValueError: synthetic worker failure" in captured.err
    assert results == synthetic_results


# ─────────────────────────────────────────────────────────────────────────────
# _argmax_cell_per_symbol — tuple tie-break (#332 item 1 hardened pattern)
# ─────────────────────────────────────────────────────────────────────────────


def test_argmax_cell_per_symbol_deterministic_tie_break_lower_sl_wins():
    """On net_pnl tie, deterministic tie-break by (-sl, -be, -pullback_distance).

    Mirrors R1 hardened pattern (#332 item 1) applied to R3 with
    pullback_distance instead of lrc_thr.
    """
    from tools.r3_trend_pullback_sweep import _argmax_cell_per_symbol

    results = [
        {"symbol": "BTCUSDT", "net_pnl": 100.0, "sl": 1.0, "be": 2.0,
         "pullback_distance": 0.5, "n_trades": 15, "exit_reasons": {}},
        {"symbol": "BTCUSDT", "net_pnl": 100.0, "sl": 0.5, "be": 2.0,
         "pullback_distance": 0.5, "n_trades": 15, "exit_reasons": {}},
    ]
    out = _argmax_cell_per_symbol(results)
    assert out["BTCUSDT"]["sl"] == 0.5


def test_argmax_cell_per_symbol_tie_break_lower_be_wins_when_sl_tied():
    """When net_pnl and sl tie, tie-break by lower be."""
    from tools.r3_trend_pullback_sweep import _argmax_cell_per_symbol

    results = [
        {"symbol": "BTCUSDT", "net_pnl": 100.0, "sl": 1.0, "be": 2.5,
         "pullback_distance": 0.5, "n_trades": 15, "exit_reasons": {}},
        {"symbol": "BTCUSDT", "net_pnl": 100.0, "sl": 1.0, "be": 1.5,
         "pullback_distance": 0.5, "n_trades": 15, "exit_reasons": {}},
    ]
    out = _argmax_cell_per_symbol(results)
    assert out["BTCUSDT"]["be"] == 1.5


def test_argmax_cell_per_symbol_tie_break_lower_pullback_wins_when_sl_be_tied():
    """When net_pnl, sl, be all tie, tie-break by lower pullback_distance.

    R3-specific: pullback_distance replaces lrc_thr as the 4th tie-break key.
    """
    from tools.r3_trend_pullback_sweep import _argmax_cell_per_symbol

    results = [
        {"symbol": "BTCUSDT", "net_pnl": 100.0, "sl": 1.0, "be": 2.0,
         "pullback_distance": 0.7, "n_trades": 15, "exit_reasons": {}},
        {"symbol": "BTCUSDT", "net_pnl": 100.0, "sl": 1.0, "be": 2.0,
         "pullback_distance": 0.3, "n_trades": 15, "exit_reasons": {}},
    ]
    out = _argmax_cell_per_symbol(results)
    assert out["BTCUSDT"]["pullback_distance"] == 0.3


def test_argmax_cell_per_symbol_tiebreak_deterministic_across_input_orders():
    """Permutation test: all input orderings yield same deterministic winner.

    Mirrors R1 sweep polish-1 test (24 permutations of 4 engineered-tied cells).
    """
    from itertools import permutations

    from tools.r3_trend_pullback_sweep import _argmax_cell_per_symbol

    cells = [
        {"symbol": "BTCUSDT", "net_pnl": 100.0, "sl": 1.0, "be": 2.0,
         "pullback_distance": 0.7, "n_trades": 15, "exit_reasons": {}},
        {"symbol": "BTCUSDT", "net_pnl": 100.0, "sl": 1.0, "be": 2.0,
         "pullback_distance": 0.3, "n_trades": 15, "exit_reasons": {}},
        {"symbol": "BTCUSDT", "net_pnl": 100.0, "sl": 1.0, "be": 1.5,
         "pullback_distance": 0.3, "n_trades": 15, "exit_reasons": {}},
        {"symbol": "BTCUSDT", "net_pnl": 100.0, "sl": 0.5, "be": 1.5,
         "pullback_distance": 0.3, "n_trades": 15, "exit_reasons": {}},
    ]

    expected = {"sl": 0.5, "be": 1.5, "pullback_distance": 0.3}
    for perm in permutations(cells):
        out = _argmax_cell_per_symbol(list(perm))
        winner = out["BTCUSDT"]
        for k, v in expected.items():
            assert winner[k] == v, (
                f"Permutation order {[c['sl'] for c in perm]}: "
                f"expected {k}={v}, got {k}={winner[k]}"
            )
