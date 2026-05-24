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


PORTFOLIO_FAILURE_TIERS = {"ALERT", "REDUCED", "PAUSED", "PROBATION"}


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
    with _conn() as conn:
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


def query_decisions(
    symbol: str | None = None,
    engine: str | None = None,
    since: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Query decisions, newest first. Optional filters by symbol, engine, time."""
    with _conn() as conn:
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


def compute_portfolio_aggregate(
    per_symbol_tiers: dict[str, str],
    concurrent_alert_threshold: int = 3,
) -> dict[str, Any]:
    """Compute the portfolio-level aggregate state from per-symbol tiers.

    Phase 1 scope: concurrent-failure-count only. Real aggregate DD
    computation (REDUCED/FROZEN thresholds) lands with B2 (portfolio
    circuit breaker) in epic #187.

    Returns {"tier": "NORMAL" | "WARNED", "concurrent_failures": int}.
    """
    failures = sum(
        1 for t in per_symbol_tiers.values() if t in PORTFOLIO_FAILURE_TIERS
    )
    tier = "WARNED" if failures >= concurrent_alert_threshold else "NORMAL"
    return {"tier": tier, "concurrent_failures": failures}


def get_current_state(
    engine: str = "v1",
    concurrent_alert_threshold: int = 3,
) -> dict[str, Any]:
    """Return current per-symbol state + portfolio aggregate.

    Takes the latest decision per symbol from the log (filtered to the
    given engine) and computes portfolio aggregate.
    """
    with _conn() as conn:
        rows = conn.execute(
            """SELECT d.symbol, d.per_symbol_tier, d.portfolio_tier, d.size_factor,
                      d.skip, d.velocity_active, d.ts, d.reasons_json
               FROM kill_switch_decisions d
               INNER JOIN (
                   SELECT symbol, MAX(ts) AS max_ts
                   FROM kill_switch_decisions
                   WHERE engine = ?
                   GROUP BY symbol
               ) latest
                 ON d.symbol = latest.symbol AND d.ts = latest.max_ts
               WHERE d.engine = ?""",
            (engine, engine),
        ).fetchall()

    cols = ["symbol", "per_symbol_tier", "portfolio_tier", "size_factor",
            "skip", "velocity_active", "ts", "reasons_json"]
    symbols: dict[str, dict[str, Any]] = {}
    per_symbol_tiers: dict[str, str] = {}
    for r in rows:
        d = dict(zip(cols, r))
        d["skip"] = bool(d["skip"])
        d["velocity_active"] = bool(d["velocity_active"])
        symbols[d["symbol"]] = d
        per_symbol_tiers[d["symbol"]] = d["per_symbol_tier"]

    portfolio = compute_portfolio_aggregate(
        per_symbol_tiers,
        concurrent_alert_threshold=concurrent_alert_threshold,
    )
    return {"symbols": symbols, "portfolio": portfolio}
