"""Smoke test: the scanner thread can boot and execute one cycle without
crashing, given a seeded DB. This catches re-export regressions (PR7-style
issues where a function moved to api/* but scanner_loop still imports the
old btc_api name).

Runs in <2 seconds with mocked HTTP / market data.
"""
from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock

import pandas as pd
import pytest


@pytest.fixture
def isolated_db_and_mocks(monkeypatch):
    """Spin up a temp DB with init_db, mock outbound network, return db_path."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    # Patch btc_api.DB_FILE so _resolve_db_file() returns our temp path.
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)

    from db.schema import init_db
    init_db()

    # Mock outbound HTTP / Telegram / market data so no network is hit
    monkeypatch.setattr("requests.post", MagicMock(return_value=MagicMock(status_code=200, ok=True, text="{}")))
    monkeypatch.setattr("requests.get", MagicMock(return_value=MagicMock(status_code=200, ok=True, json=lambda: [])))

    # Provide a minimal OHLCV DataFrame so scan() doesn't fail on data fetch
    fake_df = pd.DataFrame({
        "open_time": [1736899200000 + i * 3_600_000 for i in range(200)],
        "open":   [50000.0 + i * 10 for i in range(200)],
        "high":   [50100.0 + i * 10 for i in range(200)],
        "low":    [49900.0 + i * 10 for i in range(200)],
        "close":  [50050.0 + i * 10 for i in range(200)],
        "volume": [10.0] * 200,
    })
    monkeypatch.setattr("data.market_data.get_klines", lambda *a, **kw: fake_df.copy())
    monkeypatch.setattr("data.market_data.get_klines_live", lambda *a, **kw: fake_df.copy())

    yield db_path

    if os.path.exists(db_path):
        os.remove(db_path)


def test_scan_executes_without_crashing(isolated_db_and_mocks):
    """A single scan() call on a seeded DB with mocked I/O must complete."""
    from btc_scanner import scan
    try:
        scan(symbol="BTCUSDT")
    except Exception as e:
        pytest.fail(f"scan() raised: {e!r}")
