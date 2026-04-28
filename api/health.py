"""Health API — thin router wrapper.

Extracted from btc_api.py in PR6 of the api+db refactor (2026-04-27).

NOTE: GET /health references btc_api._scanner_state (the runtime scanner
dict). Until PR7 extracts the scanner runtime, this module imports it from
btc_api at call time (lazy import) to avoid circular-import issues at module
load time.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.config import load_config
from api.deps import verify_api_key
from db.connection import get_db

log = logging.getLogger("api.health")

router = APIRouter(tags=["health"])


class ReactivateRequest(BaseModel):
    reason: str = "manual"


@router.get("/health/symbols", dependencies=[Depends(verify_api_key)])
def get_health_symbols():
    """List current health state per symbol."""
    con = get_db()
    try:
        rows = con.execute(
            """SELECT symbol, state, state_since, last_evaluated_at,
                      last_metrics_json, manual_override
               FROM symbol_health
               ORDER BY symbol"""
        ).fetchall()
    finally:
        con.close()
    cols = ("symbol", "state", "state_since", "last_evaluated_at",
            "last_metrics_json", "manual_override")
    return {"symbols": [dict(zip(cols, r)) for r in rows]}


@router.get("/health/events", dependencies=[Depends(verify_api_key)])
def get_health_events(
    symbol: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500, description="Max rows to return (capped to prevent unbounded scans)"),
):
    """Transition history. Optionally filter by symbol."""
    con = get_db()
    try:
        if symbol:
            rows = con.execute(
                """SELECT id, symbol, from_state, to_state, trigger_reason,
                          metrics_json, ts
                   FROM symbol_health_events WHERE symbol=?
                   ORDER BY ts DESC LIMIT ?""",
                (symbol, limit),
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT id, symbol, from_state, to_state, trigger_reason,
                          metrics_json, ts
                   FROM symbol_health_events ORDER BY ts DESC LIMIT ?""",
                (limit,),
            ).fetchall()
    finally:
        con.close()
    cols = ("id", "symbol", "from_state", "to_state", "trigger_reason",
            "metrics_json", "ts")
    return {"events": [dict(zip(cols, r)) for r in rows]}


@router.get("/health/dashboard", dependencies=[Depends(verify_api_key)])
def get_health_dashboard():
    """B6: single-shot consolidated state for the kill switch dashboard.

    Returns per-symbol full state + portfolio aggregate + 24h alert summary.
    Read-only; safe even when kill_switch.enabled=False (returns last-evaluated
    snapshot).
    """
    from health import get_dashboard_state
    cfg = load_config()
    return get_dashboard_state(cfg)


@router.post("/health/reactivate/{symbol}", dependencies=[Depends(verify_api_key)])
def post_health_reactivate(symbol: str, body: ReactivateRequest):
    """Manually reactivate a PAUSED symbol — transitions PAUSED → PROBATION (B5 #199)."""
    from health import reactivate_symbol, get_symbol_state
    cfg = load_config()
    reactivate_symbol(symbol.upper(), reason=body.reason, cfg=cfg)
    return {"ok": True, "symbol": symbol.upper(), "state": get_symbol_state(symbol.upper())}


@router.get("/health", summary="Health check for monitoring and Docker")
def health_check():
    """Returns system health status. HTTP 200 = healthy, 503 = degraded."""
    # Lazy import to avoid circular dep: btc_api imports api.health at module
    # load time, so we cannot import btc_api at the top of this module.
    import btc_api as _btc_api  # noqa: PLC0415
    _scanner_state = _btc_api._scanner_state

    checks = {}

    # Database connectivity
    try:
        con = get_db()
        con.execute("SELECT 1")
        con.close()
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    # Scanner thread status
    checks["scanner"] = "ok" if _scanner_state.get("running") else "stopped"

    # Last scan freshness
    last_ts = _scanner_state.get("last_scan_ts")
    if last_ts:
        try:
            last_dt = datetime.fromisoformat(last_ts)
            age_sec = (datetime.now(timezone.utc) - last_dt).total_seconds()
            cfg = load_config()
            interval = cfg.get("scan_interval_sec", 300)
            checks["scan_freshness"] = "ok" if age_sec < interval * 3 else f"stale ({int(age_sec)}s ago)"
        except Exception:
            checks["scan_freshness"] = "unknown"
    else:
        checks["scan_freshness"] = "no_scans_yet"

    # Stats
    checks["scans_total"] = _scanner_state.get("scans_total", 0)
    checks["signals_total"] = _scanner_state.get("signals_total", 0)
    checks["errors"] = _scanner_state.get("errors", 0)

    healthy = checks["database"] == "ok" and checks["scanner"] == "ok"
    status_code = 200 if healthy else 503

    return JSONResponse(
        content={"healthy": healthy, "checks": checks},
        status_code=status_code
    )
