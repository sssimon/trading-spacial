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
