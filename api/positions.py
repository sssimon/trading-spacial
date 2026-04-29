"""Positions API — router + service helpers.

Extracted from btc_api.py in PR4 of the api+db refactor (2026-04-27).
Uses db/positions.py for queries.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from api.config import load_config
from api.deps import verify_api_key
from auth.dependencies import require_role
from db.connection import get_db
from db.positions import (
    _calc_pnl,
    db_close_position,
    db_create_position,
    db_get_positions,
    db_update_position,
)

log = logging.getLogger("api.positions")

# Shared filesystem paths live in api/_paths.py (single source of truth).
from api._paths import DATA_DIR, LOGS_DIR, SIGNALS_LOG_FILE, _ensure_dirs  # noqa: E402,F401

POSITIONS_JSON_FILE = os.path.join(DATA_DIR, "positions_summary.json")

router = APIRouter(prefix="/positions", tags=["positions"])


def _write_position_event_log(pos: dict, reason: str, exit_price: float):
    try:
        _ensure_dirs()
        qty = pos.get("qty") or 0
        pnl_usd, pnl_pct = _calc_pnl(pos["direction"], pos["entry_price"], exit_price, qty)
        emoji = "TAKE PROFIT" if reason == "TP_HIT" else "STOP LOSS"
        ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "",
            "-" * 58,
            f"[{ts_now} UTC]  {emoji}  {pos['symbol']}  (pos_id={pos['id']})",
            "-" * 58,
            f"  Entrada : ${pos['entry_price']}  ->  Salida: ${exit_price}",
            f"  P&L     : ${pnl_usd:+.2f}  ({pnl_pct:+.2f}%)",
            f"  Tamanio : ${pos.get('size_usd', '?')}  |  Qty: {pos.get('qty', '?')}",
        ]
        with open(SIGNALS_LOG_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        log.warning(f"_write_position_event_log error: {e}")


def update_positions_json():
    """Escribe data/positions_summary.json con estado de posiciones."""
    try:
        _ensure_dirs()
        all_pos   = db_get_positions()
        open_pos  = [p for p in all_pos if p["status"] == "open"]
        closed_pos = [p for p in all_pos if p["status"] == "closed"]
        realized  = sum((p["pnl_usd"] or 0) for p in closed_pos)
        wins      = sum(1 for p in closed_pos if (p["pnl_usd"] or 0) > 0)
        win_rate  = (wins / len(closed_pos)) if closed_pos else 0
        payload = {
            "updated_at":      datetime.now(timezone.utc).isoformat(),
            "open_count":      len(open_pos),
            "closed_count":    len(closed_pos),
            "realized_pnl_usd": round(realized, 2),
            "win_rate":        round(win_rate, 4),
            "open_positions":  open_pos,
        }
        tmp = POSITIONS_JSON_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp, POSITIONS_JSON_FILE)
    except Exception as e:
        log.warning(f"update_positions_json error: {e}")


def check_position_stops(symbol: str, price: float):
    """Auto-cierra posiciones abiertas si el precio toca TP o SL. Sends notifications."""
    con = get_db()
    rows = con.execute(
        "SELECT * FROM positions WHERE symbol=? AND status='open'", (symbol.upper(),)
    ).fetchall()
    con.close()

    cfg = load_config()

    for pos in [dict(r) for r in rows]:
        reason = None
        exit_price = None

        # Trailing ratchet: move SL to breakeven when profit >= be_mult × ATR
        atr_entry = pos.get("atr_entry")
        _be_mult = pos.get("be_mult") or 1.5  # per-symbol from config, fallback 1.5
        if atr_entry and pos["direction"] == "LONG" and pos["sl_price"]:
            be_threshold = pos["entry_price"] + round(atr_entry * _be_mult, 2)
            if price >= be_threshold and pos["sl_price"] < pos["entry_price"]:
                new_sl = pos["entry_price"]
                con_trail = get_db()
                con_trail.execute(
                    "UPDATE positions SET sl_price = ? WHERE id = ?",
                    (new_sl, pos["id"])
                )
                con_trail.commit()
                con_trail.close()
                pos["sl_price"] = new_sl
                log.info(f"Trailing: #{pos['id']} {symbol} SL moved to breakeven ${new_sl:.2f}")
        elif atr_entry and pos["direction"] == "SHORT" and pos["sl_price"]:
            be_threshold = pos["entry_price"] - round(atr_entry * _be_mult, 2)
            if price <= be_threshold and pos["sl_price"] > pos["entry_price"]:
                new_sl = pos["entry_price"]
                con_trail = get_db()
                con_trail.execute(
                    "UPDATE positions SET sl_price = ? WHERE id = ?",
                    (new_sl, pos["id"])
                )
                con_trail.commit()
                con_trail.close()
                pos["sl_price"] = new_sl
                log.info(f"Trailing: #{pos['id']} {symbol} SL moved to breakeven ${new_sl:.2f}")

        if pos["direction"] == "LONG":
            if pos["tp_price"] and price >= pos["tp_price"]:
                reason, exit_price = "TP_HIT", pos["tp_price"]
            elif pos["sl_price"] and price <= pos["sl_price"]:
                reason, exit_price = "SL_HIT", pos["sl_price"]
        else:  # SHORT
            if pos["tp_price"] and price <= pos["tp_price"]:
                reason, exit_price = "TP_HIT", pos["tp_price"]
            elif pos["sl_price"] and price >= pos["sl_price"]:
                reason, exit_price = "SL_HIT", pos["sl_price"]

        if reason:
            db_close_position(pos["id"], exit_price, reason)
            log.info(f"POSICION #{pos['id']} {symbol} {reason} @ ${exit_price}")
            _write_position_event_log(pos, reason, exit_price)

            # Send exit notification via the centralized notifier (#162 PR B).
            entry = pos.get("entry_price", 0)
            qty = pos.get("qty", 0)
            pnl_usd, pnl_pct = _calc_pnl(pos["direction"], entry, exit_price, qty)

            try:
                from notifier import notify, PositionExitEvent  # noqa: PLC0415
                # Map legacy reason strings ("SL_HIT"/"TP_HIT") to tier codes.
                exit_reason_code = "SL" if reason == "SL_HIT" else (
                    "TP" if reason == "TP_HIT" else reason
                )
                notify(
                    PositionExitEvent(
                        symbol=symbol,
                        direction=pos.get("direction", "LONG"),
                        exit_reason=exit_reason_code,
                        entry_price=float(entry or 0.0),
                        exit_price=float(exit_price or 0.0),
                        pnl_usd=float(pnl_usd or 0.0),
                        pnl_pct=float(pnl_pct or 0.0),
                    ),
                    cfg=cfg,
                )
            except Exception as e:
                log.warning(f"Failed to notify {reason} for {symbol}: {e}")


@router.get("", summary="Listar posiciones")
def list_positions(
    status: Optional[str] = Query("all", description="open | closed | all")
):
    positions = db_get_positions(status)
    return {"total": len(positions), "positions": positions}


@router.post(
    "",
    summary="Abrir nueva posicion",
    # TODO(auth-cleanup): remove verify_api_key after JWT migration stable
    dependencies=[Depends(verify_api_key), Depends(require_role("admin"))],
)
def open_position(body: dict = Body(...)):
    required = {"symbol", "entry_price"}
    missing  = required - body.keys()
    if missing:
        raise HTTPException(status_code=422, detail=f"Faltan campos: {missing}")
    try:
        pos = db_create_position(body)
        update_positions_json()
        return {"ok": True, "position": pos}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put(
    "/{pos_id}",
    summary="Editar posicion (SL/TP/notas)",
    # TODO(auth-cleanup): remove verify_api_key after JWT migration stable
    dependencies=[Depends(verify_api_key), Depends(require_role("admin"))],
)
def edit_position(pos_id: int, body: dict = Body(...)):
    pos = db_update_position(pos_id, body)
    if not pos:
        raise HTTPException(status_code=404, detail=f"Posicion #{pos_id} no encontrada")
    update_positions_json()
    return {"ok": True, "position": pos}


@router.post(
    "/{pos_id}/close",
    summary="Cerrar posicion manualmente",
    # TODO(auth-cleanup): remove verify_api_key after JWT migration stable
    dependencies=[Depends(verify_api_key), Depends(require_role("admin"))],
)
def close_position(pos_id: int, body: dict = Body(...)):
    exit_price  = body.get("exit_price")
    exit_reason = body.get("exit_reason", "MANUAL")
    if exit_price is None:
        raise HTTPException(status_code=422, detail="Falta exit_price")
    pos = db_close_position(pos_id, float(exit_price), exit_reason)
    if not pos:
        raise HTTPException(status_code=404, detail=f"Posicion #{pos_id} no encontrada")
    _write_position_event_log(pos, exit_reason, float(exit_price))
    update_positions_json()
    return {"ok": True, "position": pos}


@router.delete(
    "/{pos_id}",
    summary="Cancelar/eliminar posicion",
    # TODO(auth-cleanup): remove verify_api_key after JWT migration stable
    dependencies=[Depends(verify_api_key), Depends(require_role("admin"))],
)
def delete_position(pos_id: int):
    con = get_db()
    row = con.execute("SELECT id FROM positions WHERE id=?", (pos_id,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(status_code=404, detail=f"Posicion #{pos_id} no encontrada")
    con.execute("UPDATE positions SET status='cancelled' WHERE id=?", (pos_id,))
    con.commit()
    con.close()
    update_positions_json()
    return {"ok": True, "message": f"Posicion #{pos_id} cancelada"}
