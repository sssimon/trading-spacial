"""Parity test for /ohlcv: response after refactor must match baseline."""
from __future__ import annotations

import json
import pathlib

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch


BASELINE_PATH = pathlib.Path(__file__).parent / "_baselines" / "ohlcv.json"


@pytest.fixture
def fixed_klines_df() -> pd.DataFrame:
    return pd.DataFrame({
        "open_time": [1736899200000 + i * 3_600_000 for i in range(5)],
        "open":      [50000.0, 50100.0, 50050.0, 50200.0, 50300.0],
        "high":      [50500.0, 50400.0, 50300.0, 50500.0, 50600.0],
        "low":       [49800.0, 49900.0, 49850.0, 50000.0, 50100.0],
        "close":     [50100.0, 50050.0, 50200.0, 50300.0, 50400.0],
        "volume":    [10.0, 12.0, 8.0, 15.0, 11.0],
    })


@pytest.fixture
def empty_klines_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])


@pytest.fixture
def client():
    from btc_api import app
    return TestClient(app)


def test_ohlcv_responses_match_baseline(client, fixed_klines_df, empty_klines_df):
    expected = json.loads(BASELINE_PATH.read_text())

    for url_label, expected_resp in expected.items():
        # url_label format: "GET /ohlcv?... [optional suffix]"
        url = url_label.split(" ", 1)[1].split(" ", 1)[0]
        is_empty = "(empty)" in url_label

        if is_empty:
            with patch("data.market_data.get_klines_live", return_value=empty_klines_df):
                actual = client.get(url)
        elif "interval=invalid" in url:
            actual = client.get(url)  # should 400 without touching fetcher
        else:
            with patch("data.market_data.get_klines_live", return_value=fixed_klines_df):
                actual = client.get(url)

        assert actual.status_code == expected_resp["status"], f"status mismatch for {url_label}"
        actual_body = actual.json() if actual.headers.get("content-type", "").startswith("application/json") else actual.text
        assert actual_body == expected_resp["body"], f"body mismatch for {url_label}"
