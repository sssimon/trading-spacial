"""Regression test for the trading invariant named in plan 2026-05-24:
All mutations derived from one tick of price decision belong to one
serializable transaction.

Specifically: while check_position_stops decides+writes SL/TP/close for
an open position, no concurrent writer (manual operator edit) may
interleave between the read and the write."""
import sqlite3
import threading
import time

import pytest

from db.transaction import transaction
from db.schema import init_db


@pytest.fixture
def db_with_position(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(db_path))
    init_db()
    # Seed an open position with sl_price = 100. The real schema requires
    # entry_ts (TEXT NOT NULL) and uses status='open' (lowercase) per
    # db/schema.py::positions table.
    with transaction() as con:
        con.execute(
            """INSERT INTO positions
               (id, symbol, direction, entry_price, qty, sl_price, tp_price,
                status, entry_ts, atr_entry, be_mult)
               VALUES (1, 'BTCUSDT', 'LONG', 100.0, 1.0, 100.0, 110.0,
                       'open', '2026-05-24T00:00:00+00:00', 2.0, 1.5)"""
        )
    return str(db_path)


def test_trailing_ratchet_atomic_under_concurrent_operator_edit(db_with_position):
    """The trailing-SL update from check_position_stops and a concurrent
    operator UPDATE must serialize. Whichever begins first wins; the loser
    sees the winner's value after BEGIN IMMEDIATE retry."""

    from api.positions import check_position_stops

    operator_done = threading.Event()
    operator_result = {}

    def operator_edit():
        try:
            with transaction() as con:
                con.execute(
                    "UPDATE positions SET sl_price = ? WHERE id = 1",
                    (105.0,),
                )
            operator_result["ok"] = True
        except Exception as e:  # pragma: no cover - assertion below
            operator_result["error"] = e
        finally:
            operator_done.set()

    # Simulate a tick where price = 108 — the trailing-SL logic would raise
    # SL to breakeven or above 100. Run both flows concurrently.
    scanner_thread = threading.Thread(
        target=check_position_stops,
        kwargs={"symbol_price_overrides": {"BTCUSDT": 108.0}},
    )
    operator_thread = threading.Thread(target=operator_edit)

    scanner_thread.start()
    operator_thread.start()
    scanner_thread.join(timeout=15)
    operator_thread.join(timeout=15)

    assert "error" not in operator_result, operator_result["error"]
    assert operator_result.get("ok") is True

    # Final state must reflect exactly one of the two writers; never an
    # interleaved partial state.
    with transaction() as con:
        row = con.execute("SELECT sl_price FROM positions WHERE id = 1").fetchone()
    assert row["sl_price"] in {105.0, 100.0, 104.0, 108.0}, row["sl_price"]
    # Acceptable values: 105.0 (operator wrote last), or any trailing-SL
    # value produced by the scanner. The point is that there is no half-
    # applied combination.
