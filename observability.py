"""Observability layer for the kill switch (Phase 1 of #187).

Tracks every decision taken by v1/v2 engines in the kill_switch_decisions
table. Used by the frontend dashboard, shadow-mode validation (future
phases), and audit trails.

Append-only. Queries read by symbol/engine/time window.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    import btc_api
    return btc_api.get_db()


def record_decision(
    symbol: str,
    engine: str,
    per_symbol_tier: str,
    portfolio_tier: str,
    size_factor: float,
    skip: bool,
    reasons: dict[str, Any],
    scan_id: int | None = None,
    slider_value: float | None = None,
    velocity_active: bool = False,
) -> int:
    """Insert a decision row. Returns the row id."""
    conn = _conn()
    try:
        cur = conn.execute(
            """INSERT INTO kill_switch_decisions
               (ts, scan_id, symbol, engine, per_symbol_tier, portfolio_tier,
                velocity_active, size_factor, skip, reasons_json, slider_value)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _now_iso(), scan_id, symbol, engine, per_symbol_tier, portfolio_tier,
                int(velocity_active), size_factor, int(skip),
                json.dumps(reasons, default=str), slider_value,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def query_decisions(
    symbol: str | None = None,
    engine: str | None = None,
    since: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Query decisions, newest first. Optional filters by symbol, engine, time."""
    conn = _conn()
    try:
        where: list[str] = []
        params: list[Any] = []
        if symbol:
            where.append("symbol = ?")
            params.append(symbol)
        if engine:
            where.append("engine = ?")
            params.append(engine)
        if since:
            where.append("ts >= ?")
            params.append(since)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        cols = ["id", "ts", "scan_id", "symbol", "engine", "per_symbol_tier",
                "portfolio_tier", "velocity_active", "size_factor", "skip",
                "reasons_json", "slider_value"]
        rows = conn.execute(
            f"""SELECT {', '.join(cols)} FROM kill_switch_decisions
               {where_sql}
               ORDER BY ts DESC, id DESC
               LIMIT ?""",
            (*params, limit),
        ).fetchall()

        result = []
        for r in rows:
            d = dict(zip(cols, r))
            d["skip"] = bool(d["skip"])
            d["velocity_active"] = bool(d["velocity_active"])
            result.append(d)
        return result
    finally:
        conn.close()
