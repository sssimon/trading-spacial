"""Positions DB layer — CRUD queries.

Extracted from btc_api.py:379-465 in PR4 of the api+db refactor (2026-04-27).
_calc_pnl lives here (pure math, no I/O) and is re-exported by api/positions.py.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from db.connection import get_db

log = logging.getLogger("db.positions")


def _calc_pnl(direction: str, entry: float, exit_p: float, qty: float):
    if direction == 'LONG':
        pnl_usd = (exit_p - entry) * qty
        pnl_pct = ((exit_p - entry) / entry) * 100
    else:
        pnl_usd = (entry - exit_p) * qty
        pnl_pct = ((entry - exit_p) / entry) * 100
    return round(pnl_usd, 4), round(pnl_pct, 4)


def db_create_position(data: dict) -> dict:
    con = get_db()
    entry = float(data["entry_price"])
    qty   = float(data.get("qty") or (float(data.get("size_usd", 0) or 0) / entry if entry else 0))
    ts    = data.get("entry_ts") or datetime.now(timezone.utc).isoformat()
    cur = con.execute("""
        INSERT INTO positions
            (scan_id, symbol, direction, status, entry_price, entry_ts,
             sl_price, tp_price, size_usd, qty, atr_entry, be_mult, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        data.get("scan_id"),
        data["symbol"].upper(),
        data.get("direction", "LONG").upper(),
        "open",
        entry,
        ts,
        data.get("sl_price"),
        data.get("tp_price"),
        data.get("size_usd"),
        qty,
        data.get("atr_entry"),
        data.get("be_mult"),
        data.get("notes", ""),
    ))
    pos_id = cur.lastrowid
    con.commit()
    row = con.execute("SELECT * FROM positions WHERE id=?", (pos_id,)).fetchone()
    con.close()
    return dict(row)


def db_get_positions(status: Optional[str] = None) -> list:
    con = get_db()
    if status and status != "all":
        rows = con.execute(
            "SELECT * FROM positions WHERE status=? ORDER BY id DESC", (status,)
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM positions ORDER BY id DESC"
        ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def db_close_position(pos_id: int, exit_price: float, exit_reason: str) -> Optional[dict]:
    con = get_db()
    row = con.execute("SELECT * FROM positions WHERE id=?", (pos_id,)).fetchone()
    if not row:
        con.close()
        return None
    pos = dict(row)
    qty = pos.get("qty") or 0
    pnl_usd, pnl_pct = _calc_pnl(pos["direction"], pos["entry_price"], exit_price, qty)
    exit_ts = datetime.now(timezone.utc).isoformat()
    con.execute("""
        UPDATE positions
        SET status=?, exit_price=?, exit_ts=?, exit_reason=?, pnl_usd=?, pnl_pct=?
        WHERE id=?
    """, ("closed", exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct, pos_id))
    con.commit()
    row = con.execute("SELECT * FROM positions WHERE id=?", (pos_id,)).fetchone()
    con.close()
    closed = dict(row)
    # Kill switch #138: trigger health evaluation for this symbol.
    try:
        from health import trigger_health_evaluation  # noqa: PLC0415
        from api.config import load_config  # noqa: PLC0415
        trigger_health_evaluation(pos["symbol"], load_config())
    except Exception as e:
        log.warning("health trigger skipped for position close: %s", e)
    return closed


def db_update_position(pos_id: int, data: dict) -> Optional[dict]:
    allowed = {"sl_price", "tp_price", "size_usd", "qty", "notes", "entry_price", "atr_entry", "be_mult"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return None
    con = get_db()
    sets = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [pos_id]
    con.execute(f"UPDATE positions SET {sets} WHERE id=?", vals)
    con.commit()
    row = con.execute("SELECT * FROM positions WHERE id=?", (pos_id,)).fetchone()
    con.close()
    return dict(row) if row else None
