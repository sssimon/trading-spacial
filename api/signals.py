"""Signals API — router + filters + dedup + CSV/log appenders.

Extracted from btc_api.py in PR5 of the api+db refactor (2026-04-27).
Uses db/signals.py for queries.

The writing version of check_pending_signal_outcomes stays in btc_api.py
until PR7 (it's coupled to scanner_loop's writing path; splitting cleanly
requires the scanner runtime extraction).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.config import load_config
from api.deps import verify_api_key
from api.telegram import build_telegram_message
from db.connection import get_db
from db.signals import (
    get_latest_signal, get_latest_scan, get_scans, get_signals_summary, save_scan,
)

log = logging.getLogger("api.signals")

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_SCRIPT_DIR, "data")
LOGS_DIR = os.path.join(_SCRIPT_DIR, "logs")
SYMBOLS_JSON_FILE = os.path.join(DATA_DIR, "symbols_status.json")
SIGNALS_CSV_FILE = os.path.join(DATA_DIR, "signals_history.csv")
SIGNALS_LOG_FILE = os.path.join(LOGS_DIR, "signals.log")

# Cabecera del CSV de señales
_CSV_HEADER = (
    "fecha,hora_utc,symbol,tipo,precio,lrc_pct,rsi_1h,score,score_label,"
    "macro_ok,gatillo,sl_precio,tp_precio,qty_btc,estado\n"
)

router = APIRouter(prefix="/signals", tags=["signals"])

# In-memory dedup state — module-level dict matching original.
_notified_signals: dict = {}  # symbol -> last_notified_iso


def should_notify_signal(rep: dict, cfg: dict) -> bool:
    """Retorna True si este reporte debe enviarse por webhook según los filtros configurados."""
    filters    = cfg.get("signal_filters", {})
    min_score  = filters.get("min_score", 0)
    req_macro  = filters.get("require_macro_ok", False)
    notify_stp = filters.get("notify_setup", False)

    is_signal = rep.get("señal_activa", False)
    is_setup  = "SETUP VÁLIDO" in rep.get("estado", "")
    score     = rep.get("score", 0) or 0
    macro_ok  = rep.get("macro_4h", {}).get("price_above", False)

    if is_signal:
        if score < min_score:
            return False
        if req_macro and not macro_ok:
            return False
        return True

    if is_setup and notify_stp:
        if score < min_score:
            return False
        if req_macro and not macro_ok:
            return False
        return True

    return False


def _is_duplicate_signal(symbol: str, cfg: dict) -> bool:
    """Check if we already notified for this symbol within the dedup window."""
    filters = cfg.get("signal_filters", {})
    window_minutes = filters.get("dedup_window_minutes", 30)
    last = _notified_signals.get(symbol)
    if not last:
        return False
    last_dt = datetime.fromisoformat(last)
    now = datetime.now(timezone.utc)
    return (now - last_dt).total_seconds() < window_minutes * 60


def _mark_notified(symbol: str):
    """Mark a symbol as notified now."""
    _notified_signals[symbol] = datetime.now(timezone.utc).isoformat()


def _ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)


def update_symbols_json(symbols_rows: list):
    """Escribe data/symbols_status.json con el estado actual de todos los pares."""
    try:
        _ensure_dirs()
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "symbols": symbols_rows,
        }
        tmp = SYMBOLS_JSON_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp, SYMBOLS_JSON_FILE)
    except Exception as e:
        log.warning(f"update_symbols_json error: {e}")


def _csv_escape(val) -> str:
    """Escapa un valor para CSV (comillas si contiene coma o comillas)."""
    s = str(val) if val is not None else ""
    if "," in s or '"' in s or "\n" in s:
        s = '"' + s.replace('"', '""') + '"'
    return s


def append_signal_csv(rep: dict, scan_id: int):
    """Añade una fila a data/signals_history.csv cuando hay señal o setup."""
    try:
        _ensure_dirs()
        write_header = not os.path.exists(SIGNALS_CSV_FILE)
        ts      = rep.get("timestamp", "")
        dt_part = ts[:10] if len(ts) >= 10 else ""
        tm_part = ts[11:19] if len(ts) >= 19 else ""
        is_sig  = rep.get("señal_activa", False)
        is_stp  = "SETUP VÁLIDO" in rep.get("estado", "")
        tipo    = "SENAL" if is_sig else "SETUP"
        sz      = rep.get("sizing_1h", {})
        macro   = rep.get("macro_4h", {})
        fields  = [
            dt_part,
            tm_part,
            rep.get("symbol", ""),
            tipo,
            rep.get("price", ""),
            rep.get("lrc_1h", {}).get("pct", ""),
            rep.get("rsi_1h", ""),
            rep.get("score", ""),
            rep.get("score_label", ""),
            1 if macro.get("price_above") else 0,
            1 if rep.get("gatillo_activo") else 0,
            sz.get("sl_precio", ""),
            sz.get("tp_precio", ""),
            sz.get("qty_btc", ""),
            rep.get("estado", ""),
        ]
        row = ",".join(_csv_escape(v) for v in fields) + "\n"
        with open(SIGNALS_CSV_FILE, "a", encoding="utf-8") as f:
            if write_header:
                f.write(_CSV_HEADER)
            f.write(row)
    except Exception as e:
        log.warning(f"append_signal_csv error: {e}")


def append_signal_log(rep: dict, scan_id: int):
    """Añade entrada legible a logs/signals.log cuando hay señal o setup."""
    try:
        _ensure_dirs()
        is_sig = rep.get("señal_activa", False)
        is_stp = "SETUP VÁLIDO" in rep.get("estado", "")
        direction = rep.get("direction", "LONG")
        tipo   = f"SENAL {direction}" if is_sig else "SETUP VALIDO"
        sym    = rep.get("symbol", "?")
        ts     = rep.get("timestamp", "")[:19].replace("T", " ")
        price  = rep.get("price", 0)
        lrc    = rep.get("lrc_1h", {}).get("pct", "?")
        score  = rep.get("score", 0)
        slabel = rep.get("score_label", "")
        macro  = rep.get("macro_4h", {})
        macro_s = "Alcista" if macro.get("price_above") else "Adversa"
        sz     = rep.get("sizing_1h", {})
        sep    = "-" * 58
        lines  = [
            "",
            sep,
            f"[{ts} UTC]  {tipo}  {sym}  (scan_id={scan_id})",
            sep,
            f"  Precio : ${price:>12,.4f}",
            f"  LRC 1H : {lrc}%   Score: {score}/9  {slabel}",
            f"  Macro  : {macro_s}",
        ]
        if is_sig:
            lines += [
                f"  SL     : ${sz.get('sl_precio', '?')}",
                f"  TP     : ${sz.get('tp_precio', '?')}",
                f"  Qty    : {sz.get('qty_btc', '?')} (ej $1k cap, riesgo 1%)",
            ]
        lines += [f"  Estado : {rep.get('estado', '')}"]
        with open(SIGNALS_LOG_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        log.warning(f"append_signal_log error: {e}")


@router.get("", summary="Historial de escaneos / señales")
def list_signals(
    limit:        int             = Query(50,    ge=1, le=500),
    only_signals: bool            = Query(False, description="Solo señales con gatillo"),
    only_setups:  bool            = Query(False, description="Señales + setups sin gatillo"),
    since_hours:  Optional[float] = Query(None,  description="Ultimas N horas"),
    symbol:       Optional[str]   = Query(None,  description="Filtrar por par (ej: ETHUSDT)"),
):
    rows = get_scans(limit=limit, only_signals=only_signals,
                     only_setups=only_setups, since_hours=since_hours,
                     symbol=symbol)
    return {
        "total": len(rows),
        "signals": [
            {
                "id":          r["id"],
                "ts":          r["ts"],
                "symbol":      r["symbol"],
                "estado":      r["estado"],
                "señal":       bool(r["señal"]),
                "setup":       bool(r["setup"]),
                "price":       r["price"],
                "lrc_pct":     r["lrc_pct"],
                "rsi_1h":      r["rsi_1h"],
                "score":       r["score"],
                "score_label": r["score_label"],
                "macro_ok":    bool(r["macro_ok"]),
                "gatillo":     bool(r["gatillo"]),
            }
            for r in rows
        ],
    }


@router.get("/performance", summary="Métricas de éxito de las señales históricas")
def get_signals_performance():
    """
    Calcula estadísticas de acierto de las señales procesadas (status='completed').
    Win Rate se define como: precio 24h > precio señal (para LONG).
    """
    con = get_db()
    # Solo señales completadas (24h de historia)
    rows = con.execute("SELECT * FROM signal_outcomes WHERE status = 'completed'").fetchall()
    con.close()

    if not rows:
        return {
            "ok": True,
            "total_completed": 0,
            "message": "No hay suficientes señales con >24h de historia."
        }

    signals = [dict(r) for r in rows]
    total = len(signals)

    # 1. Win Rate General
    wins = sum(1 for s in signals if s["price_24h"] > s["signal_price"])
    win_rate = wins / total

    # 2. Por Score Tier
    tiers = {}
    for s in signals:
        tier = s["score"]
        if tier not in tiers:
            tiers[tier] = {"total": 0, "wins": 0}
        tiers[tier]["total"] += 1
        if s["price_24h"] > s["signal_price"]:
            tiers[tier]["wins"] += 1

    tier_stats = []
    for t in sorted(tiers.keys(), reverse=True):
        wr = tiers[t]["wins"] / tiers[t]["total"]
        tier_stats.append({
            "score": t,
            "total": tiers[t]["total"],
            "win_rate": round(wr, 4)
        })

    # 3. Métricas de Volatilidad
    avg_runup = sum(s["max_runup_pct"] or 0 for s in signals) / total
    avg_drawdown = sum(s["max_drawdown_pct"] or 0 for s in signals) / total

    return {
        "ok": True,
        "total_completed": total,
        "overall_win_rate": round(win_rate, 4),
        "avg_max_runup_pct": round(avg_runup, 2),
        "avg_max_drawdown_pct": round(avg_drawdown, 2),
        "by_score": tier_stats
    }


@router.get("/latest", summary="Ultima señal completa (con gatillo)")
def latest_signal(
    symbol: Optional[str] = Query(None, description="Filtrar por par (ej: SOLUSDT)")
):
    row = get_latest_signal(symbol)
    if not row:
        msg = f"Sin señales para {symbol}." if symbol else "Sin señales registradas."
        return {"message": msg, "señal": None}
    try:
        payload = json.loads(row["payload"]) if row.get("payload") else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return {
        "id":            row["id"],
        "ts":            row["ts"],
        "symbol":        row["symbol"],
        "estado":        row["estado"],
        "price":         row["price"],
        "lrc_pct":       row["lrc_pct"],
        "score":         row["score"],
        "score_label":   row["score_label"],
        "macro_ok":      bool(row["macro_ok"]),
        "gatillo":       bool(row["gatillo"]),
        "sizing":        payload.get("sizing_1h", {}),
        "confirmations": {
            k: v for k, v in payload.get("confirmations", {}).items()
            if isinstance(v.get("pass"), bool) and v["pass"]
        },
        "telegram_message": build_telegram_message(payload),
    }


@router.get("/latest/message", summary="Mensaje Telegram de la ultima señal")
def latest_message(
    symbol: Optional[str] = Query(None, description="Filtrar por par")
):
    row = get_latest_signal(symbol)
    if not row:
        return {"message": "Sin señales registradas aun."}
    try:
        payload = json.loads(row["payload"]) if row.get("payload") else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return {
        "scan_id": row["id"],
        "symbol":  row["symbol"],
        "ts":      row["ts"],
        "message": build_telegram_message(payload),
    }


@router.get("/{scan_id}", summary="Detalle de un escaneo por ID")
def signal_by_id(scan_id: int):
    con = get_db()
    row = con.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    con.close()
    if not row:
        raise HTTPException(status_code=404, detail=f"Escaneo #{scan_id} no encontrado")
    row     = dict(row)
    try:
        payload = json.loads(row["payload"]) if row.get("payload") else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return {**row, "full_report": payload,
            "telegram_message": build_telegram_message(payload)}
