# tests/_fixtures/capture_baseline.py
"""One-shot to regenerate tests/_baselines/scan_btcusdt.json.

Run:
    pytest tests/_fixtures/capture_baseline.py::test_capture_btcusdt -s

DO NOT run unless you intentionally want to reset the baseline.
See tests/_baselines/README.md.
"""
from __future__ import annotations
import json
from pathlib import Path

from btc_scanner import scan
from tests._fixtures.scanner_frozen import frozen_scan  # noqa: F401

_BASELINE = Path(__file__).resolve().parent.parent / "_baselines" / "scan_btcusdt.json"


def _normalize(obj):
    """Convert any non-JSON-native types (e.g. numpy) to native Python."""
    import numpy as np

    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize(x) for x in obj]
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    return obj


def test_capture_btcusdt(frozen_scan):
    rep = scan("BTCUSDT")
    rep_norm = _normalize(rep)
    _BASELINE.parent.mkdir(parents=True, exist_ok=True)
    _BASELINE.write_text(json.dumps(rep_norm, indent=2, sort_keys=True))
    print(f"\nwrote {_BASELINE}")
