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
from auth.dependencies import get_current_tenant_id, require_role
from db.transaction import snapshot_connection

log = logging.getLogger("api.health")

router = APIRouter(tags=["health"])


class ReactivateRequest(BaseModel):
    reason: str = "manual"


@router.get("/health/symbols", dependencies=[Depends(verify_api_key)])
def get_health_symbols():
    """List current health state per symbol."""
    # READ via snapshot_connection (WAL-concurrent, no writer lock) — #494
    with snapshot_connection() as con:
        rows = con.execute(
            """SELECT symbol, state, state_since, last_evaluated_at,
                      last_metrics_json, manual_override
               FROM symbol_health
               ORDER BY symbol"""
        ).fetchall()
    cols = ("symbol", "state", "state_since", "last_evaluated_at",
            "last_metrics_json", "manual_override")
    return {"symbols": [dict(zip(cols, r)) for r in rows]}


@router.get("/health/events", dependencies=[Depends(verify_api_key)])
def get_health_events(
    symbol: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500, description="Max rows to return (capped to prevent unbounded scans)"),
):
    """Transition history. Optionally filter by symbol."""
    with snapshot_connection() as con:
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
    cols = ("id", "symbol", "from_state", "to_state", "trigger_reason",
            "metrics_json", "ts")
    return {"events": [dict(zip(cols, r)) for r in rows]}


@router.get("/health/dashboard", dependencies=[Depends(verify_api_key)])
def get_health_dashboard(tenant_id: int = Depends(get_current_tenant_id)):
    """B6: single-shot consolidated state for the kill switch dashboard.

    Returns per-symbol full state + portfolio aggregate + 24h alert summary.
    Read-only; safe even when kill_switch.enabled=False (returns last-evaluated
    snapshot).

    Portfolio equity + positions are tenant-scoped (epic B #253). The dashboard
    state is computed against the caller's tenant only; cross-tenant
    aggregates are never produced.
    """
    from health import get_dashboard_state
    cfg = load_config()
    return get_dashboard_state(cfg, tenant_id=tenant_id)


@router.post(
    "/health/reactivate/{symbol}",
    # TODO(auth-cleanup): remove verify_api_key after JWT migration stable
    dependencies=[Depends(verify_api_key), Depends(require_role("admin"))],
)
def post_health_reactivate(symbol: str, body: ReactivateRequest):
    """Manually reactivate a PAUSED symbol — transitions PAUSED → PROBATION (B5 #199)."""
    from health import reactivate_symbol, get_symbol_state
    cfg = load_config()
    reactivate_symbol(symbol.upper(), reason=body.reason, cfg=cfg)
    return {"ok": True, "symbol": symbol.upper(), "state": get_symbol_state(symbol.upper())}


@router.get("/health/live", summary="Readiness: proceso + schema listos (sin scanner)")
def health_live():
    """Readiness probe: 200 si uvicorn responde y el schema está presente.

    Usa SELECT 1 FROM users LIMIT 1 (tabla canónica) — no bare SELECT 1 —
    para detectar schema incompleto. No toca el scanner.
    Es la ruta que el deploy poll-ea para confirmar que la instancia está lista.
    """
    try:
        with snapshot_connection() as con:
            con.execute("SELECT 1 FROM users LIMIT 1")
        return {"ready": True}
    except Exception as e:
        return JSONResponse(content={"ready": False, "detail": str(e)}, status_code=503)


@router.get("/health", summary="Health check (liveness del scanner vía DB)")
def health_check():
    """Returns system health status. HTTP 200 = healthy, 503 = degraded.

    Deriva la liveness del scanner de la DB via api.scanner_liveness —
    funciona en instancias web-only donde no hay scanner thread en proceso.
    """
    from api.scanner_liveness import scanner_liveness  # noqa: PLC0415

    checks = {}

    # Database connectivity
    try:
        with snapshot_connection() as con:
            con.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    snap = scanner_liveness()
    fr = snap["frescura"]["estado"]
    checks["scanner"] = fr
    checks["scan_freshness"] = fr
    checks["scans_total"] = snap.get("scans_total", 0)
    checks["signals_total"] = snap.get("signals_total", 0)

    healthy = checks["database"] == "ok" and fr == "fresco"
    return JSONResponse(
        content={"healthy": healthy, "checks": checks},
        status_code=200 if healthy else 503
    )
