"""Signals DB layer — query functions.

Extracted from btc_api.py:456-560 in PR5 of the api+db refactor (2026-04-27).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from db.connection import get_db

log = logging.getLogger("db.signals")


def save_scan(rep: dict) -> int:
    symbol  = rep.get("symbol", "BTCUSDT")
    estado  = rep.get("estado", "")
    señal   = 1 if rep.get("señal_activa") else 0
    setup   = 1 if "SETUP VÁLIDO" in estado else 0
    price   = rep.get("price")
    lrc_pct = rep.get("lrc_1h", {}).get("pct")
    rsi_1h  = rep.get("rsi_1h")
    score   = rep.get("score", 0)
    slabel  = rep.get("score_label", "")
    macro   = 1 if rep.get("macro_4h", {}).get("price_above") else 0
    gatillo = 1 if rep.get("gatillo_activo") else 0
    ts      = rep.get("timestamp", datetime.now(timezone.utc).isoformat())

    with get_db() as con:
        cur = con.execute("""
            INSERT INTO scans
                (ts, symbol, estado, señal, setup, price, lrc_pct, rsi_1h,
                 score, score_label, macro_ok, gatillo, payload)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (ts, symbol, estado, señal, setup, price, lrc_pct, rsi_1h,
              score, slabel, macro, gatillo, json.dumps(rep, ensure_ascii=False)))
        scan_id = cur.lastrowid
        con.commit()

    # Si es señal activa, registrar para seguimiento de performance
    if señal:
        try:
            with get_db() as con_out:
                con_out.execute("""
                    INSERT OR IGNORE INTO signal_outcomes (scan_id, symbol, signal_ts, signal_price, score, macro_ok)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (scan_id, symbol, ts, price, score, macro))
                con_out.commit()
        except Exception as e:
            log.warning(f"Error iniciando tracking de señal: {e}")

    return scan_id


def get_scans(limit=50, only_signals=False, only_setups=False,
              since_hours: Optional[float] = None,
              symbol: Optional[str] = None) -> list:
    conds  = []
    params = []
    if symbol:
        conds.append("symbol = ?")
        params.append(symbol.upper())
    if only_signals:
        conds.append("señal = 1")
    elif only_setups:
        conds.append("(señal = 1 OR setup = 1)")
    if since_hours:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
        conds.append("ts >= ?")
        params.append(cutoff)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    params.append(limit)
    with get_db() as con:
        rows = con.execute(
            f"SELECT * FROM scans {where} ORDER BY id DESC LIMIT ?", params
        ).fetchall()
    return [dict(r) for r in rows]


def get_latest_scan_per_symbol(
    limit: int = 10,
    only_signals: bool = False,
    since_hours: Optional[float] = None,
) -> list:
    """Return the latest scan per symbol, newest-first, capped at `limit`.

    Why this exists (PR #403 review issue 2): the agent's
    `get_symbols_with_signals` tool used to fetch `limit` raw rows from
    `get_scans()` and de-dupe client-side. Because the scanner persists
    many scans per symbol, a request for `limit=10` could surface only
    4 unique symbols after the de-dupe, leaving the model uncertain
    whether "4 results" meant "4 symbols matched" or "we silently cut
    the result set". Doing the de-dupe in SQL guarantees the caller
    gets up to `limit` distinct symbols.

    Implementation: subquery picks `MAX(id)` per symbol (the scans table
    is INSERT-only with monotonically increasing ids = newest), then
    project rows by that id set and order by ts DESC. Identical
    filtering semantics to `get_scans()`.
    """
    conds: list[str] = []
    params: list = []
    if only_signals:
        conds.append("señal = 1")
    if since_hours is not None and since_hours > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
        conds.append("ts >= ?")
        params.append(cutoff)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    params.append(limit)
    with get_db() as con:
        rows = con.execute(
            f"""SELECT * FROM scans
                WHERE id IN (
                    SELECT MAX(id) FROM scans {where}
                    GROUP BY symbol
                )
                ORDER BY ts DESC
                LIMIT ?""",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_latest_signal(symbol: Optional[str] = None) -> Optional[dict]:
    with get_db() as con:
        if symbol:
            row = con.execute(
                "SELECT * FROM scans WHERE señal=1 AND symbol=? ORDER BY id DESC LIMIT 1",
                (symbol.upper(),)
            ).fetchone()
        else:
            row = con.execute(
                "SELECT * FROM scans WHERE señal=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
    return dict(row) if row else None


def get_latest_scan(symbol: Optional[str] = None) -> Optional[dict]:
    with get_db() as con:
        if symbol:
            row = con.execute(
                "SELECT * FROM scans WHERE symbol=? ORDER BY id DESC LIMIT 1",
                (symbol.upper(),)
            ).fetchone()
        else:
            row = con.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def get_signals_summary() -> list:
    """Último escaneo de cada símbolo activo, ordenado por señal y score."""
    with get_db() as con:
        rows = con.execute("""
            SELECT s.* FROM scans s
            INNER JOIN (
                SELECT symbol, MAX(id) as max_id FROM scans GROUP BY symbol
            ) latest ON s.id = latest.max_id
            ORDER BY s.señal DESC, s.score DESC
        """).fetchall()
    return [dict(r) for r in rows]
