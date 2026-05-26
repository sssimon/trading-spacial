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
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from api.positions_birth import ValidatedOpenRequest

log = logging.getLogger("db.positions")


def _calc_pnl(direction: str, entry: float, exit_p: float, qty: float):
    if direction == 'LONG':
        pnl_usd = (exit_p - entry) * qty
        pnl_pct = ((exit_p - entry) / entry) * 100
    else:
        pnl_usd = (entry - exit_p) * qty
        pnl_pct = ((entry - exit_p) / entry) * 100
    return round(pnl_usd, 4), round(pnl_pct, 4)


def db_create_position_sql(
    con: sqlite3.Connection,
    request: "ValidatedOpenRequest",
) -> dict:
    """Thin SQL INSERT for a Position birth. NO defensive membranes.

    Per Cluster D (Voronov 2026-05-26): the Pydantic boundary
    (`OpenPositionRequest`) + sentinel factory (`_build_open_request`)
    already validated every field. The 5-deep `qty` fallback chain and the
    `data.get(...)` membranes are GONE — they were #471 F5 ("código de
    revisor" trying to compensate for an upstream contract that did not
    exist). The only knowledge this function needs is the SQL shape.

    Mirrors the contract of `db_close_position_sql`: pure SQL, no transaction,
    no side-effects beyond the INSERT. Caller (BirthRegistrar) owns the
    transaction and the post-commit choreography.
    """
    payload = request.payload
    entry_ts = (
        payload.entry_ts.isoformat()
        if payload.entry_ts is not None
        else datetime.now(timezone.utc).isoformat()
    )
    cur = con.execute(
        """
        INSERT INTO positions
            (scan_id, symbol, direction, status, entry_price, entry_ts,
             sl_price, tp_price, size_usd, qty, atr_entry, be_mult, notes,
             tenant_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            payload.scan_id,
            payload.symbol,             # Pydantic validator already uppercased + allowlist-checked
            payload.direction,          # Literal["LONG","SHORT"] enforced upstream
            "open",
            payload.entry_price,
            entry_ts,
            payload.sl_price,
            payload.tp_price,
            payload.size_usd,
            payload.qty,                # Pydantic _qty_positive guarantees > 0
            payload.atr_entry,
            payload.be_mult,
            payload.notes,
            request.tenant_id,          # int from JWT, NOT from body
        ),
    )
    pos_id = cur.lastrowid
    row = con.execute("SELECT * FROM positions WHERE id=?", (pos_id,)).fetchone()
    return dict(row)


def db_last_exit_ts(
    con: sqlite3.Connection,
    symbol: str,
    tenant_id: Optional[int] = None,
) -> Optional[datetime]:
    """Return last exit_ts (UTC, tz-aware) for symbol's closed positions, or None.

    Per B.5: when tenant_id is None (default), returns the most recent exit
    across ALL users — correct semantic for scanner cooldown (system-wide).
    When tenant_id is int, filters to that user's exits only.

    Task 8 (#446): `con` is now mandatory positional first arg.
    """
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
    con: sqlite3.Connection,
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

    Task 8 (#446): `con` is now mandatory positional first arg.
    """
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
    return [dict(r) for r in rows]


def db_get_position_by_id(
    con: sqlite3.Connection, pos_id: int,
) -> Optional[dict]:
    """Read a single position by id. Pure SQL — no tenant filter.

    Caller is responsible for tenant ownership check (per Task 8.5 design
    where helpers are pure SQL operators; ownership lives in the business
    operator).
    """
    row = con.execute(
        "SELECT * FROM positions WHERE id = ?", (pos_id,),
    ).fetchone()
    return dict(row) if row else None


def db_close_position_sql(
    con: sqlite3.Connection,
    pos_id: int,
    exit_price: float,
    exit_reason: str,
    exit_ts: str,
    pnl_usd: float,
    pnl_pct: float,
) -> dict:
    """Pure SQL: UPDATE the position to closed. Returns the updated row.

    No health trigger, no notify, no logging beyond ERROR. Caller (operator)
    owns lifecycle, transaction, and side-effects.
    """
    con.execute(
        """UPDATE positions
           SET status = 'closed',
               exit_price = ?,
               exit_ts = ?,
               exit_reason = ?,
               pnl_usd = ?,
               pnl_pct = ?
           WHERE id = ?""",
        (exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct, pos_id),
    )
    row = con.execute(
        "SELECT * FROM positions WHERE id = ?", (pos_id,),
    ).fetchone()
    return dict(row)


def db_update_position(
    con: sqlite3.Connection,
    pos_id: int,
    data: dict,
    tenant_id: Optional[int] = None,
) -> Optional[dict]:
    """Update position fields. Per B.5: ownership-enforced when tenant_id given.

    Returns None if position not found OR (when tenant_id provided) the
    position does not belong to that tenant.

    Task 8 (#446): `con` is now mandatory positional first arg.
    """
    allowed = {"sl_price", "tp_price", "size_usd", "qty", "notes", "entry_price", "atr_entry", "be_mult"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return None
    # Ownership pre-check when tenant_id provided
    if tenant_id is not None:
        owner_row = con.execute(
            "SELECT id FROM positions WHERE id=? AND tenant_id=?",
            (pos_id, tenant_id),
        ).fetchone()
        if not owner_row:
            return None
    sets = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [pos_id]
    con.execute(f"UPDATE positions SET {sets} WHERE id=?", vals)
    row = con.execute("SELECT * FROM positions WHERE id=?", (pos_id,)).fetchone()
    return dict(row) if row else None
