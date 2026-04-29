"""Kill switch API — thin router wrapper.

Extracted from btc_api.py in PR6 of the api+db refactor (2026-04-27).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.config import load_config, save_config
from api.deps import verify_api_key
from auth.dependencies import require_role
from db.connection import get_db

log = logging.getLogger("api.kill_switch")

router = APIRouter(tags=["kill_switch"])


@router.post(
    "/kill_switch/recalibrate",
    summary="Manually trigger an auto-calibrator recommendation",
    # TODO(auth-cleanup): remove verify_api_key after JWT migration stable
    dependencies=[Depends(verify_api_key), Depends(require_role("admin"))],
)
def kill_switch_recalibrate():
    """Manually trigger the auto-calibrator (#187 B4b.1).

    In B4b.1 this returns no_feasible (stub fitness). B4b.2 (#216) will
    replace the stub with v2-aware backtest grid optimization.
    """
    from strategy.kill_switch_v2_calibrator import (
        run_optimization_stub, _persist_recommendation,
    )
    from strategy.kill_switch_v2_optimizer import run_optimization_v2
    from datetime import datetime, timezone

    try:
        cfg = load_config()
        try:
            result = run_optimization_v2(cfg)
        except Exception as opt_err:
            log.warning(
                "run_optimization_v2 failed; falling back to stub: %s",
                opt_err, exc_info=True,
            )
            result = run_optimization_stub(cfg)
        now = datetime.now(tz=timezone.utc)
        rec_id = _persist_recommendation(
            triggered_by=["manual"], result=result, now=now,
        )
    except Exception as e:
        log.error(
            "POST /kill_switch/recalibrate failed: %s", e, exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"recalibrate failed: {type(e).__name__}: {e}",
        )

    log.warning(
        "Kill switch v2: recomendación id=%d (status=%s, triggered_by=manual). "
        "Telegram pendiente B4b.3.",
        rec_id, result["status"],
    )
    return {"recommendation_id": rec_id, "status": result["status"]}


@router.get(
    "/kill_switch/recommendations",
    summary="List auto-calibrator recommendations",
    dependencies=[Depends(verify_api_key)],
)
def kill_switch_list_recommendations(
    since: Optional[str] = Query(
        None, description="ISO timestamp; only rows with ts >= since",
    ),
    status: Optional[str] = Query(
        None,
        description="Filter by status (pending, applied, ignored, "
                    "superseded, no_feasible)",
    ),
    limit: int = Query(100, ge=1, le=1000),
):
    """List auto-calibrator recommendations, latest first."""
    import json as _json

    conn = get_db()
    try:
        sql = (
            "SELECT id, ts, triggered_by, slider_value, projected_pnl, "
            "projected_dd, status, applied_ts, applied_by, report_json "
            "FROM kill_switch_recommendations WHERE 1=1"
        )
        params: list = []
        if since:
            sql += " AND ts >= ?"
            params.append(since)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(int(limit))
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    result = []
    for r in rows:
        d = {
            "id": r[0],
            "ts": r[1],
            "triggered_by": r[2],
            "slider_value": r[3],
            "projected_pnl": r[4],
            "projected_dd": r[5],
            "status": r[6],
            "applied_ts": r[7],
            "applied_by": r[8],
            "report": None,
        }
        try:
            d["triggered_by"] = _json.loads(d["triggered_by"])
        except (TypeError, ValueError) as e:
            log.warning(
                "Corrupted recommendation row id=%s: triggered_by JSON "
                "parse failed (%s); raw=%r", r[0], e, r[2],
            )
        try:
            d["report"] = _json.loads(r[9])
        except (TypeError, ValueError) as e:
            log.warning(
                "Corrupted recommendation row id=%s: report_json parse "
                "failed (%s); raw=%r", r[0], e, r[9],
            )
            d["report"] = None
        result.append(d)
    return result


@router.post(
    "/kill_switch/recommendations/{rec_id}/apply",
    summary="Apply a pending recommendation (operator action)",
    # TODO(auth-cleanup): remove verify_api_key after JWT migration stable
    dependencies=[Depends(verify_api_key), Depends(require_role("admin"))],
)
def kill_switch_apply_recommendation(rec_id: int):
    """Apply a pending recommendation: write config override + mark applied.

    Returns the updated row. 404 if not found, 400 if not pending.
    """
    from datetime import datetime, timezone

    try:
        conn = get_db()
        try:
            row = conn.execute(
                """SELECT id, status, slider_value
                   FROM kill_switch_recommendations WHERE id = ?""",
                (rec_id,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise HTTPException(status_code=404, detail=f"recommendation {rec_id} not found")
        if row[1] != "pending":
            raise HTTPException(
                status_code=400,
                detail=f"recommendation {rec_id} is already {row[1]}",
            )
        slider_value = row[2]
        if slider_value is None:
            raise HTTPException(
                status_code=400,
                detail=f"recommendation {rec_id} has no slider_value to apply",
            )
        slider_int = int(slider_value)
        if not (0 <= slider_int <= 100):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"recommendation {rec_id} has out-of-range slider_value="
                    f"{slider_int} (must be 0..100)"
                ),
            )

        # Write config override. save_config only merges at kill_switch level
        # (it replaces the entire v2 sub-dict), so do read-modify-write here to
        # preserve other v2 keys (auto_calibrator, regime_adjustments, etc).
        existing_cfg = load_config()
        existing_v2 = (existing_cfg.get("kill_switch", {}) or {}).get("v2", {}) or {}
        merged_v2 = {**existing_v2, "aggressiveness": slider_int}
        save_config({"kill_switch": {"v2": merged_v2}})

        # Update DB row
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        conn = get_db()
        try:
            conn.execute(
                """UPDATE kill_switch_recommendations
                   SET status = 'applied', applied_ts = ?, applied_by = 'operator'
                   WHERE id = ?""",
                (now_iso, rec_id),
            )
            conn.commit()
            updated = conn.execute(
                """SELECT id, ts, triggered_by, slider_value, projected_pnl,
                          projected_dd, status, applied_ts, applied_by, report_json
                   FROM kill_switch_recommendations WHERE id = ?""",
                (rec_id,),
            ).fetchone()
        finally:
            conn.close()

        log.warning(
            "Kill switch v2: operator applied recomendación id=%d slider=%d",
            rec_id, int(slider_value),
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error(
            "POST /kill_switch/recommendations/%d/apply failed: %s",
            rec_id, e, exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"apply failed: {type(e).__name__}: {e}",
        )

    return {
        "id": updated[0], "ts": updated[1], "triggered_by": updated[2],
        "slider_value": updated[3], "projected_pnl": updated[4],
        "projected_dd": updated[5], "status": updated[6],
        "applied_ts": updated[7], "applied_by": updated[8],
    }


@router.post(
    "/kill_switch/recommendations/{rec_id}/ignore",
    summary="Ignore a pending recommendation (operator action)",
    # TODO(auth-cleanup): remove verify_api_key after JWT migration stable
    dependencies=[Depends(verify_api_key), Depends(require_role("admin"))],
)
def kill_switch_ignore_recommendation(rec_id: int):
    """Mark a pending recommendation as ignored (no config change).

    404 if not found, 400 if not pending.
    """
    from datetime import datetime, timezone

    try:
        conn = get_db()
        try:
            row = conn.execute(
                """SELECT id, status FROM kill_switch_recommendations WHERE id = ?""",
                (rec_id,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise HTTPException(status_code=404, detail=f"recommendation {rec_id} not found")
        if row[1] != "pending":
            raise HTTPException(
                status_code=400,
                detail=f"recommendation {rec_id} is already {row[1]}",
            )

        now_iso = datetime.now(tz=timezone.utc).isoformat()
        conn = get_db()
        try:
            conn.execute(
                """UPDATE kill_switch_recommendations
                   SET status = 'ignored', applied_ts = ?, applied_by = 'operator'
                   WHERE id = ?""",
                (now_iso, rec_id),
            )
            conn.commit()
            updated = conn.execute(
                """SELECT id, ts, triggered_by, slider_value, projected_pnl,
                          projected_dd, status, applied_ts, applied_by, report_json
                   FROM kill_switch_recommendations WHERE id = ?""",
                (rec_id,),
            ).fetchone()
        finally:
            conn.close()

        log.warning(
            "Kill switch v2: operator ignored recomendación id=%d", rec_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error(
            "POST /kill_switch/recommendations/%d/ignore failed: %s",
            rec_id, e, exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"ignore failed: {type(e).__name__}: {e}",
        )

    return {
        "id": updated[0], "ts": updated[1], "triggered_by": updated[2],
        "slider_value": updated[3], "projected_pnl": updated[4],
        "projected_dd": updated[5], "status": updated[6],
        "applied_ts": updated[7], "applied_by": updated[8],
    }


@router.get("/kill_switch/decisions", dependencies=[Depends(verify_api_key)])
def get_kill_switch_decisions(
    symbol: Optional[str] = None,
    engine: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
):
    """Kill switch decision log (#187 phase 1). Filter by symbol/engine/since ts."""
    import observability
    rows = observability.query_decisions(
        symbol=symbol, engine=engine, since=since, limit=limit,
    )
    return {"decisions": rows}


@router.get("/kill_switch/current_state", dependencies=[Depends(verify_api_key)])
def get_kill_switch_current_state(engine: str = "v1"):
    """Current tier state per symbol + portfolio aggregate (#187 phase 1)."""
    import observability
    return observability.get_current_state(engine=engine)
