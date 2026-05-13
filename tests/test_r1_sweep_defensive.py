"""Defensive-output tests for `tools.r1_signal_exit_sweep`.

Coverage:
  - `_save_json` rejects NaN / Inf payloads (fail-loud rather than emit
    silently-invalid JSON that downstream verdict code might mis-parse).
  - `_run_jobs_parallel` summarizes worker errors at end of sweep (operator
    visibility into mid-sweep failures that would otherwise be buried).
"""
from __future__ import annotations

import json
import math

import pytest

from tools.r1_signal_exit_sweep import _save_json


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
# _summarize_worker_errors — aggregates the `error` field across sweep results
# ─────────────────────────────────────────────────────────────────────────────


def test_summarize_worker_errors_returns_none_on_clean_results():
    """No `error` field on any result → no summary message."""
    from tools.r1_signal_exit_sweep import _summarize_worker_errors

    results = [{"symbol": "BTCUSDT", "n_trades": 5}, {"symbol": "ETHUSDT", "n_trades": 3}]
    assert _summarize_worker_errors(results) is None


def test_summarize_worker_errors_counts_total_and_distinct():
    """Workers emitting `error` → message counts total errors + distinct messages."""
    from tools.r1_signal_exit_sweep import _summarize_worker_errors

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


def test_summarize_worker_errors_includes_distinct_message_text():
    """Operator visibility: distinct error strings appear in the summary."""
    from tools.r1_signal_exit_sweep import _summarize_worker_errors

    results = [
        {"symbol": "BTCUSDT", "error": "ValueError: bad data"},
        {"symbol": "ADAUSDT", "error": "RuntimeError: cutoff drift"},
    ]
    msg = _summarize_worker_errors(results)
    assert "ValueError: bad data" in msg
    assert "RuntimeError: cutoff drift" in msg


def test_summarize_worker_errors_uses_is_not_none_filter_not_truthy():
    """Closes #332 item 4: empty-string `error` field must count, not silently filter.

    Producer `_process_cell` currently only writes `out["error"] = err` when err
    is non-empty (safe in practice), but the truthy filter
    `r.get("error") for r in results if r.get("error")` was contract-fragile —
    if producer ever set `out["error"] = ""` deliberately (e.g., empty error
    flag), the truthy filter would silently swallow it.

    Fix: `is not None` check distinguishes "explicit empty error" from "no key".
    """
    from tools.r1_signal_exit_sweep import _summarize_worker_errors

    results = [
        {"symbol": "BTCUSDT", "error": ""},   # Explicit empty string (key set)
        {"symbol": "ETHUSDT"},                 # No error key at all
    ]
    msg = _summarize_worker_errors(results)
    assert msg is not None, (
        "Empty-string error must be counted (is not None semantics), "
        "not silently filtered by truthy check"
    )
    assert "1 workers errored" in msg


# ─────────────────────────────────────────────────────────────────────────────
# _argmax_cell_per_symbol — deterministic tie-break (issue #332 item 1)
# ─────────────────────────────────────────────────────────────────────────────


def test_argmax_cell_per_symbol_deterministic_tie_break_lower_sl_wins():
    """On net_pnl tie, deterministic tie-break by (-sl, -be, -lrc_thr) tuple key.

    Mirrors `tools/r1_verdict.py:_argmax_cell` behavior. Prevents drift between
    sweep tool's argmax (used for halt diagnostic JSON) and verdict tool's
    argmax (used for §4 classification) when cells tie on net_pnl. Without
    this fix, raw `max(cells, key=lambda c: c["net_pnl"])` falls back to
    insertion-order, which is contract-fragile across parallel worker orderings.
    """
    from tools.r1_signal_exit_sweep import _argmax_cell_per_symbol

    # Two BTC cells with identical net_pnl but different sl values.
    # Tie-break key (net_pnl, -sl, -be, -lrc_thr) on max picks lower sl
    # because max sees larger -sl (-0.5 > -1.0).
    results = [
        {"symbol": "BTCUSDT", "net_pnl": 100.0, "sl": 1.0, "be": 2.0,
         "lrc_thr": 50.0, "n_trades": 15, "exit_reasons": {}},
        {"symbol": "BTCUSDT", "net_pnl": 100.0, "sl": 0.5, "be": 2.0,
         "lrc_thr": 50.0, "n_trades": 15, "exit_reasons": {}},
    ]
    out = _argmax_cell_per_symbol(results)
    assert out["BTCUSDT"]["sl"] == 0.5, (
        f"Expected sl=0.5 (deterministic tie-break: lower sl preferred), "
        f"got sl={out['BTCUSDT']['sl']}"
    )


def test_argmax_cell_per_symbol_tie_break_lower_be_wins_when_sl_tied():
    """When net_pnl and sl tie, tie-break by lower be."""
    from tools.r1_signal_exit_sweep import _argmax_cell_per_symbol

    results = [
        {"symbol": "BTCUSDT", "net_pnl": 100.0, "sl": 1.0, "be": 2.5,
         "lrc_thr": 50.0, "n_trades": 15, "exit_reasons": {}},
        {"symbol": "BTCUSDT", "net_pnl": 100.0, "sl": 1.0, "be": 1.5,
         "lrc_thr": 50.0, "n_trades": 15, "exit_reasons": {}},
    ]
    out = _argmax_cell_per_symbol(results)
    assert out["BTCUSDT"]["be"] == 1.5, (
        f"Expected be=1.5 (tie-break: lower be preferred when sl tied), "
        f"got be={out['BTCUSDT']['be']}"
    )


def test_argmax_cell_per_symbol_tie_break_lower_lrc_thr_wins_when_sl_be_tied():
    """When net_pnl, sl, be all tie, tie-break by lower lrc_thr."""
    from tools.r1_signal_exit_sweep import _argmax_cell_per_symbol

    results = [
        {"symbol": "BTCUSDT", "net_pnl": 100.0, "sl": 1.0, "be": 2.0,
         "lrc_thr": 55.0, "n_trades": 15, "exit_reasons": {}},
        {"symbol": "BTCUSDT", "net_pnl": 100.0, "sl": 1.0, "be": 2.0,
         "lrc_thr": 35.0, "n_trades": 15, "exit_reasons": {}},
    ]
    out = _argmax_cell_per_symbol(results)
    assert out["BTCUSDT"]["lrc_thr"] == 35.0, (
        f"Expected lrc_thr=35.0 (tie-break: lower lrc_thr preferred when sl+be tied), "
        f"got lrc_thr={out['BTCUSDT']['lrc_thr']}"
    )


def test_argmax_cell_per_symbol_higher_net_pnl_wins_over_tie_break():
    """net_pnl dominates: higher net_pnl wins even if sl/be/lrc_thr would prefer the other cell."""
    from tools.r1_signal_exit_sweep import _argmax_cell_per_symbol

    results = [
        {"symbol": "BTCUSDT", "net_pnl": 200.0, "sl": 2.5, "be": 2.5,
         "lrc_thr": 55.0, "n_trades": 15, "exit_reasons": {}},  # higher net_pnl, worse tie-break
        {"symbol": "BTCUSDT", "net_pnl": 100.0, "sl": 0.5, "be": 1.5,
         "lrc_thr": 35.0, "n_trades": 15, "exit_reasons": {}},  # lower net_pnl, better tie-break
    ]
    out = _argmax_cell_per_symbol(results)
    assert out["BTCUSDT"]["net_pnl"] == 200.0, (
        "net_pnl must dominate tie-break fields"
    )
