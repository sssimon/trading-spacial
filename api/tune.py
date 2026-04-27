"""Tune API — thin router wrapper.

Extracted from btc_api.py in PR6 of the api+db refactor (2026-04-27).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from api.config import load_config, CONFIG_FILE
from api.deps import verify_api_key
from db.connection import get_db

log = logging.getLogger("api.tune")

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

router = APIRouter(prefix="/tune", tags=["tune"])


@router.get("/latest", summary="Latest tune result")
def tune_latest():
    """Returns the most recent tune_result row (with parsed results_json) or null."""
    con = get_db()
    row = con.execute(
        "SELECT * FROM tune_results ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    if not row:
        return None
    result = dict(row)
    # Parse results_json so the frontend gets an object, not a string
    if result.get("results_json"):
        try:
            result["results_json"] = json.loads(result["results_json"])
        except (json.JSONDecodeError, TypeError):
            pass
    return result


@router.post("/apply", summary="Apply pending tune proposal",
             dependencies=[Depends(verify_api_key)])
def tune_apply():
    """Applies the latest pending tune proposal to config.json symbol_overrides."""
    con = get_db()
    row = con.execute(
        "SELECT * FROM tune_results WHERE status = 'pending' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        con.close()
        raise HTTPException(status_code=404, detail="No pending tune proposal found")

    result = dict(row)
    tune_id = result["id"]

    # Parse results_json to extract CHANGE recommendations
    try:
        results = json.loads(result["results_json"]) if result.get("results_json") else {}
    except (json.JSONDecodeError, TypeError):
        con.close()
        raise HTTPException(status_code=500, detail="Invalid results_json in tune proposal")

    # Create config backup
    now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_name = f"config_backup_{now_str}.json"
    backup_path = os.path.join(_SCRIPT_DIR, backup_name)
    if os.path.exists(CONFIG_FILE):
        shutil.copy2(CONFIG_FILE, backup_path)

    # Load config and apply changes
    cfg = load_config()
    overrides = cfg.get("symbol_overrides", {})
    applied_count = 0

    # Extract CHANGE recommendations from results
    recommendations = results.get("recommendations", [])
    for rec in recommendations:
        if rec.get("action") != "CHANGE":
            continue
        symbol = rec.get("symbol", "")
        params = rec.get("params", {})
        if symbol and params:
            if symbol not in overrides:
                overrides[symbol] = {}
            overrides[symbol].update(params)
            applied_count += 1

    cfg["symbol_overrides"] = overrides
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    log.info(f"Auto-tune applied: {applied_count} changes, backup: {backup_name}")

    # Update tune_result status
    con.execute(
        "UPDATE tune_results SET status = 'applied', applied_ts = ?, changes_count = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), applied_count, tune_id)
    )
    con.commit()
    con.close()

    return {"ok": True, "applied": applied_count, "backup": backup_name}


@router.post("/reject", summary="Reject pending tune proposal",
             dependencies=[Depends(verify_api_key)])
def tune_reject():
    """Rejects the latest pending tune proposal."""
    con = get_db()
    row = con.execute(
        "SELECT id FROM tune_results WHERE status = 'pending' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        con.close()
        raise HTTPException(status_code=404, detail="No pending tune proposal found")

    con.execute(
        "UPDATE tune_results SET status = 'rejected' WHERE id = ?",
        (row["id"],)
    )
    con.commit()
    con.close()
    return {"ok": True}
