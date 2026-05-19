"""Positions DB layer — CRUD queries.

Extracted from btc_api.py:379-465 in PR0 of the api+db refactor (2026-04-27).
_calc_pnl lives here (pure math, no I/O) and is re-exported by api/positions.py.

## Multi-tenancy (B.5 #258 — 2026-05-15)

All public functions accept an optional `tenant_id: int | None = None` param:
- `None` (default) — legacy behavior; no tenant filter. Used by internal
  callers like btc_scanner.py that operate system-wide.
- `int` — enforce tenant ownership. Reads filter `WHERE tenant_id = ?`;
  writes inject tenant_id; ownership checks gate mutations.

The API layer (api/positions.py) ALWAYS passes tenant_id from JWT via
`Depends(get_current_tenant_id)`. Never read tenant_id from request params,
headers, or body — that's the threat surface closed by B.5.

Pre-reg: docs/superpowers/plans/2026-05-15-multi-tenant-b5-api-enforcement-pre-reg.md
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


def db_create_position(data: dict, tenant_id: Optional[int] = None) -> dict:
    """Create position. If tenant_id provided, persisted on the row.

    Per B.5: API callers always pass tenant_id from JWT; internal/legacy
    callers may pass None (row inserted with tenant_id NULL).
    """
    con = get_db()
    entry = float(data["entry_price"])
    qty   = float(data.get("qty") or (float(data.get("size_usd", 0) or 0) / entry if entry else 0))
    ts    = data.get("entry_ts") or datetime.now(timezone.utc).isoformat()
    cur = con.execute("""
        INSERT INTO positions
            (scan_id, symbol, direction, status, entry_price, entry_ts,
             sl_price, tp_price, size_usd, qty, atr_entry, be_mult, notes,
             tenant_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
        tenant_id,  # NULL if not provided — legacy behavior
    ))
    pos_id = cur.lastrowid
    con.commit()
    row = con.execute("SELECT * FROM positions WHERE id=?", (pos_id,)).fetchone()
    con.close()
    return dict(row)


def db_last_exit_ts(symbol: str, tenant_id: Optional[int] = None) -> Optional[datetime]:
    """Return last exit_ts (UTC, tz-aware) for symbol's closed positions, or None.

    Per B.5: when tenant_id is None (default), returns the most recent exit
    across ALL users — correct semantic for scanner cooldown (system-wide).
    When tenant_id is int, filters to that user's exits only.
    """
    con = get_db()
    if tenant_id is None:
        row = con.execute(
            "SELECT exit_ts FROM positions "
            "WHERE symbol=? AND status='closed' AND exit_ts IS NOT NULL "
            "ORDER BY exit_ts DESC LIMIT 1",
            (symbol.upper(),),
        ).fetchone()
    else:
        row = con.execute(
            "SELECT exit_ts FROM positions "
            "WHERE symbol=? AND status='closed' AND exit_ts IS NOT NULL "
            "AND tenant_id=? "
            "ORDER BY exit_ts DESC LIMIT 1",
            (symbol.upper(), tenant_id),
        ).fetchone()
    con.close()
    if not row or not row[0]:
        return None
    try:
        dt = datetime.fromisoformat(row[0])
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def db_get_positions(
    status: Optional[str] = None,
    tenant_id: Optional[int] = None,
    since: Optional[str] = None,
) -> list:
    """List positions, optionally filtered by status, tenant_id, and since.

    Per B.5: when tenant_id is None (default), returns all rows (legacy).
    When tenant_id is int, filters strict to that tenant.

    `since`: ISO 8601 string. When provided, filters to rows with
    `exit_ts >= since` for status='closed' (the typical use — windowed
    historial) or `entry_ts >= since` otherwise. Pushed into SQL so the
    caller doesn't load the full history into Python just to discard it
    (PR #403 review issue 3).
    """
    con = get_db()
    clauses: list[str] = []
    params: list = []
    if status and status != "all":
        clauses.append("status=?")
        params.append(status)
    if tenant_id is not None:
        clauses.append("tenant_id=?")
        params.append(tenant_id)
    if since is not None:
        # Use exit_ts when filtering closed trades (the historial case);
        # otherwise entry_ts. Both columns are ISO 8601 strings so a
        # lexicographic compare is correct.
        ts_col = "exit_ts" if status == "closed" else "entry_ts"
        clauses.append(f"{ts_col} >= ?")
        params.append(since)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = con.execute(
        f"SELECT * FROM positions{where} ORDER BY id DESC", params,
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def db_close_position(
    pos_id: int,
    exit_price: float,
    exit_reason: str,
    tenant_id: Optional[int] = None,
) -> Optional[dict]:
    """Close position by id. Per B.5: ownership-enforced when tenant_id given.

    If tenant_id is provided and the position does NOT belong to that tenant,
    returns None (IDOR protection — caller sees same behavior as 'not found').
    """
    con = get_db()
    if tenant_id is None:
        row = con.execute(
            "SELECT * FROM positions WHERE id=?", (pos_id,),
        ).fetchone()
    else:
        row = con.execute(
            "SELECT * FROM positions WHERE id=? AND tenant_id=?",
            (pos_id, tenant_id),
        ).fetchone()
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


def db_update_position(
    pos_id: int,
    data: dict,
    tenant_id: Optional[int] = None,
) -> Optional[dict]:
    """Update position fields. Per B.5: ownership-enforced when tenant_id given.

    Returns None if position not found OR (when tenant_id provided) the
    position does not belong to that tenant.
    """
    allowed = {"sl_price", "tp_price", "size_usd", "qty", "notes", "entry_price", "atr_entry", "be_mult"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return None
    con = get_db()
    # Ownership pre-check when tenant_id provided
    if tenant_id is not None:
        owner_row = con.execute(
            "SELECT id FROM positions WHERE id=? AND tenant_id=?",
            (pos_id, tenant_id),
        ).fetchone()
        if not owner_row:
            con.close()
            return None
    sets = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [pos_id]
    con.execute(f"UPDATE positions SET {sets} WHERE id=?", vals)
    con.commit()
    row = con.execute("SELECT * FROM positions WHERE id=?", (pos_id,)).fetchone()
    con.close()
    return dict(row) if row else None
