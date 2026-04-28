"""Parity test for /positions endpoints."""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient


BASELINE_PATH = pathlib.Path(__file__).parent / "_baselines" / "positions.json"


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient with isolated DB seeded with one open position."""
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
        "VALUES (2, '2026-01-15T10:05:00Z', 'BTCUSDT', 'LONG', 1, 0, 50000.0, 20.0, 40.0, 5, 'premium', 1, 1, '{}')"
    )
    con.execute(
        "INSERT INTO positions (id, scan_id, symbol, direction, status, entry_price, entry_ts, sl_price, tp_price, size_usd, qty) "
        "VALUES (1, 2, 'BTCUSDT', 'LONG', 'open', 50000.0, '2026-01-15T10:05:00Z', 49000.0, 54000.0, 100.0, 0.002)"
    )
    con.commit()
    con.close()

    # Test config (api_key="test-key")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"api_key": "test-key"}))
    monkeypatch.setattr(btc_api, "CONFIG_FILE", str(cfg_path), raising=False)
    import api.config as _ac
    monkeypatch.setattr(_ac, "CONFIG_FILE", str(cfg_path), raising=False)
    monkeypatch.setattr(btc_api, "DEFAULTS_FILE", str(tmp_path / "no_def.json"), raising=False)
    monkeypatch.setattr(_ac, "DEFAULTS_FILE", str(tmp_path / "no_def.json"), raising=False)
    monkeypatch.setattr(btc_api, "SECRETS_FILE", str(tmp_path / "no_sec.json"), raising=False)
    monkeypatch.setattr(_ac, "SECRETS_FILE", str(tmp_path / "no_sec.json"), raising=False)

    from btc_api import app
    return TestClient(app)


_DYNAMIC_TS_KEYS = {"entry_ts", "exit_ts", "updated_at"}


def _strip_dynamic(obj):
    """Recursively remove keys that contain dynamic timestamps from dicts."""
    if isinstance(obj, dict):
        return {k: _strip_dynamic(v) for k, v in obj.items() if k not in _DYNAMIC_TS_KEYS}
    if isinstance(obj, list):
        return [_strip_dynamic(item) for item in obj]
    return obj


def test_positions_match_baseline(client):
    expected = json.loads(BASELINE_PATH.read_text())
    for label, expected_resp in expected.items():
        parts = label.split(" ", 2)
        method, url = parts[0], parts[1]
        is_auth = "(auth)" in label and "no auth" not in label
        body = None

        if "POST /positions (auth)" in label:
            body = {"symbol": "ETHUSDT", "entry_price": 3000.0,
                    "sl_price": 2900.0, "tp_price": 3300.0,
                    "size_usd": 100.0, "qty": 0.033, "direction": "LONG"}
        elif "POST /positions (no auth)" in label:
            body = {"symbol": "ETHUSDT", "entry_price": 3000.0}
        elif "PUT /positions/1" in label:
            body = {"notes": "test note"}

        # PUT always requires auth. POST: only when "(auth)" label, not "(no auth)".
        needs_auth = is_auth or method == "PUT"
        headers = {"X-API-Key": "test-key"} if needs_auth else {}
        if method == "GET":
            r = client.get(url)
        elif method == "POST":
            r = client.post(url, json=body, headers=headers)
        elif method == "PUT":
            r = client.put(url, json=body, headers=headers)
        else:
            pytest.fail(f"Unexpected method {method}")

        assert r.status_code == expected_resp["status"], f"status mismatch for {label}: got {r.status_code} expected {expected_resp['status']}"
        actual_body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        # Strip dynamic timestamp fields before comparing (entry_ts uses datetime.now()).
        assert _strip_dynamic(actual_body) == _strip_dynamic(expected_resp["body"]), f"body mismatch for {label}"
