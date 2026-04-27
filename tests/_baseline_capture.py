"""Capture HTTP response baselines from btc_api.py for parity testing.

Usage:
    python -m tests._baseline_capture <domain> > tests/_baselines/<domain>.json

Where <domain> is one of the registered keys in CAPTURERS.

The script:
1. Spins up a TestClient against the current btc_api.app.
2. Seeds a temp DB with deterministic fixtures.
3. Issues a fixed set of HTTP requests per domain.
4. Dumps {request_label: {status: int, body: <json>}} to stdout.

Determinism requirements: fixtures use fixed timestamps, fixed scan IDs,
and seed=42 for any randomness. The baseline is committed to git and is
NOT regenerated except when the response format intentionally changes.

Each domain PR (PR1-PR6) extends this file by adding a _capture_<domain>
function and registering it in CAPTURERS.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any, Callable

from fastapi.testclient import TestClient


def _seed_minimal(con) -> None:
    """Insert minimal fixtures shared across domains: 2 scans, 1 position."""
    con.execute(
        "INSERT INTO scans (id, ts, symbol, estado, señal, setup, price, lrc_pct, rsi_1h, score, score_label, macro_ok, gatillo, payload) "
        "VALUES (1, '2026-01-15T10:00:00Z', 'BTCUSDT', 'NEUTRAL', 0, 0, 50000.0, 30.0, 45.0, 2, 'standard', 1, 0, '{}')"
    )
    con.execute(
        "INSERT INTO scans (id, ts, symbol, estado, señal, setup, price, lrc_pct, rsi_1h, score, score_label, macro_ok, gatillo, payload) "
        "VALUES (2, '2026-01-15T10:05:00Z', 'BTCUSDT', 'LONG', 1, 0, 50000.0, 20.0, 40.0, 5, 'premium', 1, 1, '{\"sl\": 49000.0, \"tp\": 54000.0}')"
    )
    con.execute(
        "INSERT INTO positions (id, scan_id, symbol, direction, status, entry_price, entry_ts, sl_price, tp_price, size_usd, qty) "
        "VALUES (1, 2, 'BTCUSDT', 'LONG', 'open', 50000.0, '2026-01-15T10:05:00Z', 49000.0, 54000.0, 100.0, 0.002)"
    )
    con.commit()


def _capture_ohlcv(client: TestClient) -> dict[str, Any]:
    """Capture /ohlcv with mocked fetcher returning a fixed DataFrame."""
    import pandas as pd  # noqa: PLC0415
    from unittest.mock import patch  # noqa: PLC0415

    fixed_df = pd.DataFrame({
        "open_time": [1736899200000 + i * 3_600_000 for i in range(5)],
        "open":      [50000.0, 50100.0, 50050.0, 50200.0, 50300.0],
        "high":      [50500.0, 50400.0, 50300.0, 50500.0, 50600.0],
        "low":       [49800.0, 49900.0, 49850.0, 50000.0, 50100.0],
        "close":     [50100.0, 50050.0, 50200.0, 50300.0, 50400.0],
        "volume":    [10.0, 12.0, 8.0, 15.0, 11.0],
    })
    empty_df = pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])

    out: dict[str, Any] = {}
    with patch("data.market_data.get_klines_live", return_value=fixed_df):
        for url in [
            "/ohlcv?symbol=BTCUSDT&interval=1h&limit=5",
            "/ohlcv?symbol=ETHUSDT&interval=4h&limit=5",
        ]:
            resp = client.get(url)
            out[f"GET {url}"] = {
                "status": resp.status_code,
                "body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text,
            }
    # Invalid interval — should 400 without hitting the fetcher
    resp = client.get("/ohlcv?symbol=BTCUSDT&interval=invalid&limit=5")
    out["GET /ohlcv?symbol=BTCUSDT&interval=invalid&limit=5"] = {
        "status": resp.status_code,
        "body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text,
    }
    # Empty DataFrame — should return empty arrays
    with patch("data.market_data.get_klines_live", return_value=empty_df):
        resp = client.get("/ohlcv?symbol=BTCUSDT&interval=1h&limit=5")
        out["GET /ohlcv?symbol=BTCUSDT&interval=1h&limit=5 (empty)"] = {
            "status": resp.status_code,
            "body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text,
        }

    return out


def _capture_config(client: TestClient) -> dict[str, Any]:
    """Capture /config GET (with secrets stripped) and POST scenarios.

    Uses isolated config files written into the seeding step's temp dir.
    Tests must monkeypatch CONFIG_FILE, DEFAULTS_FILE, SECRETS_FILE so the
    routes don't read the real production files.
    """
    out: dict[str, Any] = {}

    # GET /config without auth — should still 200 if api_key not set, or 401 if set
    # Since the seeded test config sets api_key="test-key", GET requires auth header
    r = client.get("/config", headers={"X-API-Key": "test-key"})
    out["GET /config (auth)"] = {"status": r.status_code, "body": r.json()}

    # GET /config without auth header — expect 401
    r = client.get("/config")
    out["GET /config (no auth)"] = {"status": r.status_code, "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text}

    # POST without auth → 401
    r = client.post("/config", json={"signal_filters": {"min_score": 5}})
    out["POST /config (no auth)"] = {"status": r.status_code, "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text}

    # POST with auth + valid update
    r = client.post("/config",
                    json={"signal_filters": {"min_score": 5}},
                    headers={"X-API-Key": "test-key"})
    out["POST /config (auth, valid)"] = {"status": r.status_code, "body": r.json()}

    return out


def _capture_positions(client: TestClient) -> dict[str, Any]:
    """Capture /positions endpoints with seeded position data."""
    out: dict[str, Any] = {}

    # GET /positions?status=all (uses _seed_minimal's seeded position id=1)
    r = client.get("/positions?status=all")
    out["GET /positions?status=all"] = {"status": r.status_code, "body": r.json()}

    r = client.get("/positions?status=open")
    out["GET /positions?status=open"] = {"status": r.status_code, "body": r.json()}

    r = client.get("/positions?status=closed")
    out["GET /positions?status=closed"] = {"status": r.status_code, "body": r.json()}

    # POST without auth → 401
    r = client.post("/positions", json={"symbol": "ETHUSDT", "entry_price": 3000.0})
    out["POST /positions (no auth)"] = {"status": r.status_code, "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text}

    # POST with auth → 200 + new position
    r = client.post("/positions",
                    json={"symbol": "ETHUSDT", "entry_price": 3000.0,
                          "sl_price": 2900.0, "tp_price": 3300.0,
                          "size_usd": 100.0, "qty": 0.033, "direction": "LONG"},
                    headers={"X-API-Key": "test-key"})
    out["POST /positions (auth)"] = {"status": r.status_code, "body": r.json()}

    # PUT /positions/1 — set notes
    r = client.put("/positions/1",
                   json={"notes": "test note"},
                   headers={"X-API-Key": "test-key"})
    out["PUT /positions/1"] = {"status": r.status_code, "body": r.json()}

    return out


def _capture_signals(client: TestClient) -> dict[str, Any]:
    """Capture /signals endpoints with seeded scan data."""
    out: dict[str, Any] = {}
    for url in [
        "/signals",
        "/signals?limit=10",
        "/signals?only_signals=true",
        "/signals?since_hours=24",
        "/signals/latest",
        "/signals/latest?symbol=BTCUSDT",
        "/signals/latest/message",
        "/signals/performance",
        "/signals/2",  # by ID — uses seeded scan_id=2
    ]:
        r = client.get(url)
        out[f"GET {url}"] = {
            "status": r.status_code,
            "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text,
        }
    return out


CAPTURERS: dict[str, Callable[[TestClient], dict[str, Any]]] = {
    "ohlcv": _capture_ohlcv,
    "config": _capture_config,
    "positions": _capture_positions,
    "signals": _capture_signals,
    # PR1-PR6 register their domain capturers here:
    #   "telegram":      _capture_telegram,
    #   "kill_switch":   _capture_kill_switch,
    #   "health":        _capture_health,
    #   "tune":          _capture_tune,
    #   "notifications": _capture_notifications,
}


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in CAPTURERS:
        print(
            f"Usage: python -m tests._baseline_capture <{ '|'.join(sorted(CAPTURERS.keys())) }>",
            file=sys.stderr,
        )
        sys.exit(1)

    domain = sys.argv[1]

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    config_dir = tempfile.mkdtemp(prefix="config_test_")

    try:
        # Patch btc_api.DB_FILE before init_db so the temp DB is the target.
        # _resolve_db_file() in db/connection.py honors this patch.
        import btc_api as _ba  # noqa: PLC0415
        _ba.DB_FILE = db_path

        # Test config (only used for the "config" domain capturer)
        import json as _json  # noqa: PLC0415
        config_path = os.path.join(config_dir, "config.json")
        _test_cfg = {
            "api_key": "test-key",
            "webhook_url": "http://test.local/hook",
            "telegram_chat_id": "test-chat",
            "telegram_bot_token": "test-token",
            "signal_filters": {"min_score": 4, "require_macro_ok": False, "notify_setup": False},
            "scan_interval_sec": 300,
            "num_symbols": 20,
            "proxy": "",
            "auto_approve_tune": True,
        }
        with open(config_path, "w") as f:
            _json.dump(_test_cfg, f)

        # Patch the config file path. Both btc_api (legacy) and api.config (new)
        # read CONFIG_FILE at call time via load_config; patch both to ensure the
        # routes see the test config regardless of which path resolves first.
        _ba.CONFIG_FILE = config_path
        _ba.DEFAULTS_FILE = "/tmp/_nonexistent_defaults.json"  # force fallback to hardcoded
        _ba.SECRETS_FILE = "/tmp/_nonexistent_secrets.json"

        import api.config as _ac  # noqa: PLC0415
        _ac.CONFIG_FILE = config_path
        _ac.DEFAULTS_FILE = "/tmp/_nonexistent_defaults.json"
        _ac.SECRETS_FILE = "/tmp/_nonexistent_secrets.json"

        from db.schema import init_db  # noqa: PLC0415
        init_db()

        from db.connection import get_db  # noqa: PLC0415
        con = get_db()
        _seed_minimal(con)
        con.close()

        from btc_api import app  # noqa: PLC0415
        client = TestClient(app)

        result = CAPTURERS[domain](client)
        print(_json.dumps(result, indent=2, sort_keys=True, default=str))
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
        import shutil as _shutil  # noqa: PLC0415
        _shutil.rmtree(config_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
