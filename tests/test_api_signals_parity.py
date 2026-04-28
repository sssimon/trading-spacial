"""Parity test for /signals endpoints."""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient


BASELINE_PATH = pathlib.Path(__file__).parent / "_baselines" / "signals.json"


def _strip_dynamic(body):
    """Mask wall-clock fields that change between runs."""
    if isinstance(body, dict):
        return {k: ("<MASKED>" if k in {"ts", "timestamp", "last_checked_ts", "now_ts"} else _strip_dynamic(v))
                for k, v in body.items()}
    if isinstance(body, list):
        return [_strip_dynamic(item) for item in body]
    return body


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient with isolated DB seeded with 2 scans."""
    db_path = tmp_path / "test.db"

    import db.connection as dbconn
    monkeypatch.setattr(dbconn, "DB_FILE", str(db_path))

    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(db_path))

    from db.schema import init_db
    init_db()

    from db.connection import get_db
    con = get_db()
    con.execute(
        "INSERT INTO scans (id, ts, symbol, estado, señal, setup, price, lrc_pct, rsi_1h, score, score_label, macro_ok, gatillo, payload) "
        "VALUES (1, '2026-01-15T10:00:00Z', 'BTCUSDT', 'NEUTRAL', 0, 0, 50000.0, 30.0, 45.0, 2, 'standard', 1, 0, '{}')"
    )
    con.execute(
        "INSERT INTO scans (id, ts, symbol, estado, señal, setup, price, lrc_pct, rsi_1h, score, score_label, macro_ok, gatillo, payload) "
        "VALUES (2, '2026-01-15T10:05:00Z', 'BTCUSDT', 'LONG', 1, 0, 50000.0, 20.0, 40.0, 5, 'premium', 1, 1, '{\"sl\": 49000.0, \"tp\": 54000.0}')"
    )
    con.commit()
    con.close()

    from btc_api import app
    return TestClient(app)


def test_signals_match_baseline(client):
    expected = json.loads(BASELINE_PATH.read_text())
    for label, expected_resp in expected.items():
        url = label.split(" ", 1)[1]
        r = client.get(url)
        assert r.status_code == expected_resp["status"], f"status mismatch for {label}: got {r.status_code}"
        actual_body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        assert _strip_dynamic(actual_body) == _strip_dynamic(expected_resp["body"]), f"body mismatch for {label}"
