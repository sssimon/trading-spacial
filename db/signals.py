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

    con = get_db()
    cur = con.execute("""
        INSERT INTO scans
            (ts, symbol, estado, señal, setup, price, lrc_pct, rsi_1h,
             score, score_label, macro_ok, gatillo, payload)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (ts, symbol, estado, señal, setup, price, lrc_pct, rsi_1h,
          score, slabel, macro, gatillo, json.dumps(rep, ensure_ascii=False)))
    scan_id = cur.lastrowid
    con.commit()
    con.close()

    # Si es señal activa, registrar para seguimiento de performance
    if señal:
        try:
            con_out = get_db()
            con_out.execute("""
                INSERT OR IGNORE INTO signal_outcomes (scan_id, symbol, signal_ts, signal_price, score, macro_ok)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (scan_id, symbol, ts, price, score, macro))
            con_out.commit()
            con_out.close()
        except Exception as e:
            log.warning(f"Error iniciando tracking de señal: {e}")

    return scan_id


def get_scans(limit=50, only_signals=False, only_setups=False,
              since_hours: Optional[float] = None,
              symbol: Optional[str] = None) -> list:
    con    = get_db()
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
    rows  = con.execute(
        f"SELECT * FROM scans {where} ORDER BY id DESC LIMIT ?", params
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_latest_signal(symbol: Optional[str] = None) -> Optional[dict]:
    con = get_db()
    if symbol:
        row = con.execute(
            "SELECT * FROM scans WHERE señal=1 AND symbol=? ORDER BY id DESC LIMIT 1",
            (symbol.upper(),)
        ).fetchone()
    else:
        row = con.execute(
            "SELECT * FROM scans WHERE señal=1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    con.close()
    return dict(row) if row else None


def get_latest_scan(symbol: Optional[str] = None) -> Optional[dict]:
    con = get_db()
    if symbol:
        row = con.execute(
            "SELECT * FROM scans WHERE symbol=? ORDER BY id DESC LIMIT 1",
            (symbol.upper(),)
        ).fetchone()
    else:
        row = con.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    return dict(row) if row else None


def get_signals_summary() -> list:
    """Último escaneo de cada símbolo activo, ordenado por señal y score."""
    con  = get_db()
    rows = con.execute("""
        SELECT s.* FROM scans s
        INNER JOIN (
            SELECT symbol, MAX(id) as max_id FROM scans GROUP BY symbol
        ) latest ON s.id = latest.max_id
        ORDER BY s.señal DESC, s.score DESC
    """).fetchall()
    con.close()
    return [dict(r) for r in rows]
