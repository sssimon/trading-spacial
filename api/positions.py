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

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query

from api.config import load_config
from api.deps import verify_api_key
from auth.dependencies import get_current_tenant_id, require_role
from db.positions import (
    _calc_pnl,
    db_get_positions,
    db_update_position,
)
from db.transaction import transaction, snapshot_connection

log = logging.getLogger("api.positions")

# Shared filesystem paths live in api/_paths.py (single source of truth).
from api._paths import DATA_DIR, LOGS_DIR, SIGNALS_LOG_FILE, _ensure_dirs  # noqa: E402,F401

POSITIONS_JSON_FILE = os.path.join(DATA_DIR, "positions_summary.json")

router = APIRouter(prefix="/positions", tags=["positions"])


from strategy._validators import (
    validated_scan_interval_sec as _validated_scan_interval_sec,
    validated_time_limit_hours as _shared_validated_tl_hours,
)


_EVENT_LOG_LABELS = {
    "TP_HIT": "TAKE PROFIT",
    "TIME_LIMIT_HIT": "TIME LIMIT",
    "SL_HIT": "STOP LOSS",
}


def _validated_time_limit_hours(value, symbol: str) -> float | None:
    return _shared_validated_tl_hours(value, symbol, "check_position_stops", log)


def _write_position_event_log(pos: dict, reason: str, exit_price: float):
    try:
        _ensure_dirs()
        qty = pos["qty"]
        pnl_usd, pnl_pct = _calc_pnl(pos["direction"], pos["entry_price"], exit_price, qty)
        emoji = _EVENT_LOG_LABELS.get(reason, "STOP LOSS")
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
        with snapshot_connection() as con:
            all_pos   = db_get_positions(con)
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


def check_position_stops(
    symbol: str | None = None,
    price: float | None = None,
    now: datetime | None = None,
    *,
    symbol_price_overrides: dict[str, float] | None = None,
):
    """Auto-cierra posiciones abiertas si el precio toca TP, SL o time-limit.

    Per-tick decision flow split into two phases (Task 6 of #446):

      Phase 1 (one tx) — read open positions for this symbol AND apply
      trailing-SL ratchet writes. This names the trading invariant from plan
      2026-05-24-transaction-unit-of-work F-05: every mutation derived from
      one tick of price decision belongs to one serializable transaction.
      Concurrent operator edits serialize via BEGIN IMMEDIATE. Trailing-SL
      is per-tick mutation, NOT part of the close-flow.

      Phase 2 (per-position) — for each position marked to close, run
      `PositionClosure(mode="SYSTEM")` outside the trailing-SL tx. Each
      closure owns its own atomic close + capital roll-in tx, then fires
      post-commit side-effects (event log, health trigger, notify, snapshot)
      via __exit__. Failures are logged and the loop continues so one bad
      position cannot stall the scanner.

    `now` is injectable for deterministic testing; defaults to current UTC time.

    `symbol_price_overrides` is a test-injection seam: when provided, the
    function iterates per (symbol, price) entry and runs the full decision
    cycle for each. Default `None` preserves the legacy `(symbol, price)`
    signature for production callers.
    """
    if symbol_price_overrides is not None:
        for sym, p in symbol_price_overrides.items():
            check_position_stops(sym, p, now=now)
        return

    if symbol is None or price is None:
        raise TypeError(
            "check_position_stops requires (symbol, price) or "
            "symbol_price_overrides={...}"
        )

    if now is None:
        now = datetime.now(timezone.utc)

    cfg = load_config()

    # Phase 1: read open positions + apply trailing-SL writes in one tx.
    # Collect (pos_id, exit_price, reason) tuples for positions that should
    # close; the close-flow itself runs in Phase 2 via PositionClosure.
    pos_list_to_close: list[tuple[int, float, str]] = []

    with transaction() as con:
        # control_domain='INTERNAL': el auto-cierre (SL/TP/TIME) es un ACTUADOR
        # del sistema. Una posición EXTERNAL (abierta por fuera, p.ej. en Binance)
        # se observa pero NUNCA se actúa — CD-1. Sin este filtro el scanner
        # cerraría el ledger de una posición que sigue viva en el broker
        # (spec 2026-06-09-posiciones-externas §4.1).
        rows = con.execute(
            "SELECT * FROM positions "
            "WHERE symbol=? AND status='open' AND control_domain='INTERNAL'",
            (symbol.upper(),),
        ).fetchall()
        pos_list = [dict(r) for r in rows]

        for pos in pos_list:
            # Trailing ratchet: move SL to breakeven when profit >= be_mult × ATR
            atr_entry = pos.get("atr_entry")
            _be_mult = pos.get("be_mult") or 1.5  # per-symbol from config, fallback 1.5
            if atr_entry and pos["direction"] == "LONG" and pos["sl_price"]:
                be_threshold = pos["entry_price"] + round(atr_entry * _be_mult, 2)
                if price >= be_threshold and pos["sl_price"] < pos["entry_price"]:
                    new_sl = pos["entry_price"]
                    con.execute(
                        "UPDATE positions SET sl_price = ? WHERE id = ?",
                        (new_sl, pos["id"]),
                    )
                    pos["sl_price"] = new_sl
                    log.info(f"Trailing: #{pos['id']} {symbol} SL moved to breakeven ${new_sl:.2f}")
            elif atr_entry and pos["direction"] == "SHORT" and pos["sl_price"]:
                be_threshold = pos["entry_price"] - round(atr_entry * _be_mult, 2)
                if price <= be_threshold and pos["sl_price"] > pos["entry_price"]:
                    new_sl = pos["entry_price"]
                    con.execute(
                        "UPDATE positions SET sl_price = ? WHERE id = ?",
                        (new_sl, pos["id"]),
                    )
                    pos["sl_price"] = new_sl
                    log.info(f"Trailing: #{pos['id']} {symbol} SL moved to breakeven ${new_sl:.2f}")

        for pos in pos_list:
            reason = None
            exit_price = None

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

            if reason is None:
                overrides = cfg.get("symbol_overrides", {}).get(symbol.upper(), {})
                _tl_h = _validated_time_limit_hours(overrides.get("time_limit_hours"), symbol)
                if _tl_h is not None and pos.get("entry_ts"):
                    try:
                        entry_dt = datetime.fromisoformat(pos["entry_ts"])
                        if entry_dt.tzinfo is None:
                            entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                    except (ValueError, TypeError) as e:
                        log.warning(
                            f"check_position_stops: malformed entry_ts on position "
                            f"#{pos['id']} ({symbol}): {pos.get('entry_ts')!r} — "
                            f"skipping time-limit check (SL/TP unaffected). Error: {e}"
                        )
                        continue

                    hours_open = (now - entry_dt).total_seconds() / 3600
                    if hours_open >= _tl_h:
                        # Stateless config resolution: a lowered time_limit_hours
                        # edit applies retroactively to long-open positions. The
                        # buffer (2× scan_interval) absorbs normal scanner lag —
                        # warn only when we're materially past that.
                        scan_interval_sec = _validated_scan_interval_sec(
                            cfg, "check_position_stops", log
                        )
                        buffer_h = (scan_interval_sec / 3600) * 2
                        if hours_open > _tl_h + buffer_h:
                            log.warning(
                                f"check_position_stops: TIME_LIMIT trigger fired "
                                f"materially past horizon for #{pos['id']} {symbol} "
                                f"(hours_open={hours_open:.1f} vs "
                                f"time_limit_hours={_tl_h}, "
                                f"buffer={buffer_h:.2f}h) — likely config edit "
                                f"while position open"
                            )
                        reason, exit_price = "TIME_LIMIT_HIT", price

            if reason:
                pos_list_to_close.append((int(pos["id"]), float(exit_price), reason))
    # Trailing-SL writes are now durable.

    # Phase 2: close each marked position via PositionClosure in SYSTEM mode.
    # Each closure owns its own atomic close + capital tx and fires
    # post-commit side-effects (event log, health, notify, snapshot) via
    # __exit__. Wrap each in try/except so a single failure cannot stall
    # the scanner across the remaining positions for this tick.
    from operators.position_closure import PositionClosure  # noqa: PLC0415
    for pos_id, exit_price, reason in pos_list_to_close:
        try:
            with PositionClosure(
                pos_id=pos_id,
                exit_price=exit_price,
                exit_reason=reason,
                mode="SYSTEM",
                cfg=cfg,
                now=now,
            ) as closure:
                closure.execute()
            log.info(f"POSICION #{pos_id} {symbol} {reason} @ ${exit_price}")
        except Exception:
            log.exception("PositionClosure failed for pos_id=%s", pos_id)
            continue


@router.get("", summary="Listar posiciones")
def list_positions(
    status: Optional[str] = Query("all", description="open | closed | all"),
    tenant_id: int = Depends(get_current_tenant_id),
):
    # B.5 #258: tenant_id from JWT, never from request param/header/body.
    # READ via snapshot_connection (WAL-concurrent, query_only, NO BEGIN
    # IMMEDIATE) — NOT transaction(). transaction() takes the writer lock even
    # for reads; under the scanner's write burst it 500'd with "database is
    # locked" (prod incident 2026-05-29). A read must never contend for the
    # writer lock.
    with snapshot_connection() as con:
        positions = db_get_positions(con, status, tenant_id=tenant_id)
    return {"total": len(positions), "positions": positions}


@router.post(
    "",
    summary="Abrir nueva posicion",
    # TODO(auth-cleanup): remove verify_api_key after JWT migration stable
    dependencies=[Depends(verify_api_key), Depends(require_role("admin"))],
)
def open_position(
    body: dict = Body(...),
    tenant_id: int = Depends(get_current_tenant_id),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """Open a new position (#473 — Voronov Cluster D dual rung).

    Validation is delegated to api.positions_birth:
      - _build_open_request runs Pydantic boundary validation (D-Tipo). Any
        shape / field / cross-field failure raises BodyValidationError → 422.
      - The schema-level partial UNIQUE index (idx_positions_open_scan_unique)
        is the last-resort rejection for (tenant_id, scan_id) duplicate-open
        races: sqlite3.IntegrityError is translated to 409 here (we
        log.exception because Pydantic was supposed to catch reordering
        upstream; an integrity error escaping to this layer signals a gap).
      - Idempotency-Key is read from the request header but its full caching
        semantics land with BirthRegistrar in Task 15. For now we just
        accept the header so the route signature is stable.
      - NO bare `except Exception`. Unknown server faults bubble to
        FastAPI's default 500 handler with the traceback logged — never
        leak str(e) to the client (closes Serrano BLOCKER 3 / #473).

    Task 15 wired this to BirthRegistrar.register, which owns:
      - the write transaction (db_create_position_sql inside `with transaction()`)
      - same-tx Idempotency-Key cache write (Task 16 lights up the persistence)
      - post-commit update_positions_json (closes F8)
      - structured POSICION OPENED log at birth (closes F15)
      - translation of sqlite3.IntegrityError on the partial UNIQUE index to
        UniqueViolationError (409)

    The route is now thin: validate → register → return. Errors are typed
    BirthError subclasses; their status_code is honored. No bare `except
    Exception` — server faults bubble to FastAPI's default 500 handler with
    the traceback logged (closes Serrano BLOCKER 3 / #473).
    """
    from api.positions_birth import (  # noqa: PLC0415
        BirthError, BirthRegistrar, _build_open_request,
    )
    try:
        validated = _build_open_request(body, tenant_id, idempotency_key)
        pos = BirthRegistrar.register(validated)
        return {"ok": True, "position": pos}
    except BirthError as e:
        log.warning(
            "open_position rejected: %s detail=%s", e.message, e.detail,
        )
        # Pydantic ValidationError.errors() may embed non-JSON-serializable
        # objects under ctx.* (the original Python exception). Stringify to
        # a JSON-safe shape before handing to FastAPI.
        safe_detail = json.loads(json.dumps(e.detail, default=str)) if e.detail else None
        raise HTTPException(status_code=e.status_code, detail={
            "error": e.__class__.__name__,
            "message": e.message,
            "detail": safe_detail,
        })


@router.put(
    "/{pos_id}",
    summary="Editar posicion (SL/TP/notas)",
    # TODO(auth-cleanup): remove verify_api_key after JWT migration stable
    dependencies=[Depends(verify_api_key), Depends(require_role("admin"))],
)
def edit_position(
    pos_id: int,
    body: dict = Body(...),
    tenant_id: int = Depends(get_current_tenant_id),
):
    # B.5 #258: ownership-enforced. Returns None if pos doesn't belong to tenant.
    with transaction() as con:
        pos = db_update_position(con, pos_id, body, tenant_id=tenant_id)
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
def close_position(
    pos_id: int,
    body: dict = Body(...),
    tenant_id: int = Depends(get_current_tenant_id),
):
    """Close a position. USER-mode PositionClosure handles all the choreography
    (atomicity, ownership enforcement via IDOR-safe NOT_FOUND collapse,
    capital roll-in, post-commit side-effects: event log + health trigger +
    notify + snapshot)."""
    from operators.position_closure import PositionClosure  # noqa: PLC0415

    exit_price = body.get("exit_price")
    exit_reason = body.get("exit_reason", "MANUAL")
    if exit_price is None:
        raise HTTPException(status_code=422, detail="Falta exit_price")

    with PositionClosure(
        pos_id=pos_id,
        exit_price=float(exit_price),
        exit_reason=exit_reason,
        mode="USER",
        caller_tenant_id=tenant_id,
    ) as closure:
        outcome = closure.execute()

    # IDOR-safe: NOT_FOUND collapses ownership-mismatch and truly-missing
    # into one indistinguishable 404 (PositionClosure.__enter__ contract).
    if outcome.status == "not_found":
        raise HTTPException(status_code=404, detail=f"Posicion #{pos_id} no encontrada")
    if outcome.status == "already_closed":
        return {"ok": True, "position": outcome.position, "already_closed": True}
    if outcome.status == "rejected_unexpected_state":
        # F2 (Voronov): position in a state neither 'open' nor 'closed'
        # (e.g., 'cancelled' set by DELETE endpoint). 409 Conflict — caller
        # must reconcile state before retrying.
        real_status = outcome.position.get("status") if outcome.position else "unknown"
        raise HTTPException(
            status_code=409,
            detail=f"Posicion #{pos_id} en estado '{real_status}', no se puede cerrar",
        )
    return {"ok": True, "position": outcome.position}


@router.delete(
    "/{pos_id}",
    summary="Cancelar/eliminar posicion",
    # TODO(auth-cleanup): remove verify_api_key after JWT migration stable
    dependencies=[Depends(verify_api_key), Depends(require_role("admin"))],
)
def delete_position(
    pos_id: int,
    tenant_id: int = Depends(get_current_tenant_id),
):
    # B.5 #258: ownership-enforced via inline SELECT (db helper doesn't have
    # delete primitive yet; refactor deferred to follow-up).
    # Wrapped in a single transaction so the ownership check + status flip
    # serialize against any concurrent operator edit of the same row.
    with transaction() as con:
        row = con.execute(
            "SELECT control_domain FROM positions WHERE id=? AND tenant_id=?",
            (pos_id, tenant_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Posicion #{pos_id} no encontrada")
        if row[0] == "EXTERNAL":
            # CD-5: una EXTERNAL corrió y tiene P&L; cancelarla (outcome nulo)
            # corrompería su EpisodioDeConducción. El sistema no la lleva a
            # cancelled ni a closed; solo el operador la cierra vía
            # PositionClosure(USER) tras cerrar en Binance (spec §4, HIGH #7).
            raise HTTPException(
                status_code=409,
                detail=f"Posicion #{pos_id} es EXTERNAL; no se puede cancelar desde el sistema (ciérrala en Binance)",
            )
        con.execute("UPDATE positions SET status='cancelled' WHERE id=?", (pos_id,))
    update_positions_json()
    return {"ok": True, "message": f"Posicion #{pos_id} cancelada"}
