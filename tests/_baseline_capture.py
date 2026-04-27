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
    """Placeholder capturer for the ohlcv domain. PR1 replaces this with the
    real version that mocks data.market_data.get_klines_live with a fixed
    DataFrame. For PR0c, this just exists to verify the capture pipeline works."""
    resp = client.get("/ohlcv?symbol=BTCUSDT&interval=1h&limit=5")
    return {
        "GET /ohlcv?symbol=BTCUSDT&interval=1h&limit=5": {
            "status": resp.status_code,
            "body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text,
        }
    }


CAPTURERS: dict[str, Callable[[TestClient], dict[str, Any]]] = {
    "ohlcv": _capture_ohlcv,
    # PR1-PR6 register their domain capturers here:
    #   "config":        _capture_config,
    #   "telegram":      _capture_telegram,
    #   "positions":     _capture_positions,
    #   "signals":       _capture_signals,
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

    try:
        # Patch btc_api.DB_FILE before init_db so the temp DB is the target.
        # _resolve_db_file() in db/connection.py honors this patch.
        import btc_api  # noqa: PLC0415
        btc_api.DB_FILE = db_path

        from db.schema import init_db  # noqa: PLC0415
        init_db()

        from db.connection import get_db  # noqa: PLC0415
        con = get_db()
        _seed_minimal(con)
        con.close()

        from btc_api import app  # noqa: PLC0415
        client = TestClient(app)

        result = CAPTURERS[domain](client)
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


if __name__ == "__main__":
    main()
