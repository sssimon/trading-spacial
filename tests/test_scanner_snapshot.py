# tests/test_scanner_snapshot.py
"""End-to-end snapshot regression for scan() during the #225 refactor.

If this fails, STOP. Either the refactor introduced a behavior change (most
likely) or the snapshot needs an intentional regen (rare; see
tests/_baselines/README.md).
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from btc_scanner import scan
from tests._fixtures.scanner_frozen import frozen_scan  # noqa: F401
from tests._fixtures.capture_baseline import _normalize

_BASELINE = Path(__file__).resolve().parent / "_baselines" / "scan_btcusdt.json"


def test_scan_btcusdt_matches_baseline(frozen_scan):
    rep = _normalize(scan("BTCUSDT"))
    expected = json.loads(_BASELINE.read_text())
    assert rep == expected, (
        "scan('BTCUSDT') drifted from tests/_baselines/scan_btcusdt.json. "
        "Investigate before regenerating."
    )
