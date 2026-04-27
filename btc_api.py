#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║   CRYPTO SCANNER API  —  Ultimate Macro & Order Flow V6.0        ║
║   FastAPI  •  SQLite  •  Webhook push  •  Localhost:8000         ║
║   Top 20 pares por capitalización de mercado                     ║
║                                                                  ║
║   Endpoints principales:                                         ║
║     GET  /                       →  bienvenida + estado          ║
║     GET  /symbols                →  estado de cada par           ║
║     GET  /status                 →  estado del scanner           ║
║     GET  /signals                →  historial (filtros)          ║
║     GET  /signals/latest         →  última señal completa        ║
║     GET  /signals/latest/message →  mensaje listo para Telegram  ║
║     POST /scan                   →  forzar escaneo manual        ║
║     GET  /docs                   →  documentación Swagger UI     ║
╚══════════════════════════════════════════════════════════════════╝
"""

from fastapi import FastAPI, HTTPException, Query, Body, Depends, Security
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager
from typing import Optional, List
import threading
import sqlite3
import json
import os
import time
import glob
import shutil
import hmac
import requests as req_lib
from datetime import datetime, timezone, timedelta
import logging
from logging.handlers import RotatingFileHandler

import sys
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from btc_scanner import scan, get_top_symbols
from data import market_data as md
from notifier import notify, SignalEvent, SystemEvent
from api.ohlcv import router as ohlcv_router
# Config domain moved to api/config.py in PR2 of the api+db domain refactor.
# Re-exports preserved for legacy callers (scanner_loop, position routes, etc.) until PR7.
from api.config import (  # noqa: F401
    load_config, save_config, _strip_secrets, _SECRET_KEYS,
    SignalFiltersUpdate, ConfigUpdate, _deep_merge, _load_json_file,
    get_config, update_config,
    CONFIG_FILE, DEFAULTS_FILE, SECRETS_FILE,
)
from api.config import router as config_router


# ─────────────────────────────────────────────────────────────────────────────
#  TRACKING DE PERFORMANCE HISTÓRICO
# ─────────────────────────────────────────────────────────────────────────────

def check_pending_signal_outcomes(current_prices: dict[str, float]):
    """
    Recorre señales pendientes y actualiza su precio 1h, 4h y 24h después.
    También actualiza max_runup y max_drawdown si no han pasado 24h.

    current_prices: {symbol: price} recolectado del ciclo de scan actual,
    para evitar llamadas extra a la API de Binance.
    """
    con = get_db()
    rows = con.execute("SELECT * FROM signal_outcomes WHERE status = 'pending'").fetchall()
    con.close()

    if not rows:
        return

    now = datetime.now(timezone.utc)
    updated_count = 0

    # Cache de klines 1h por symbol para runup/drawdown (una llamada por symbol)
    _klines_cache: dict[str, object] = {}

    for r in [dict(row) for row in rows]:
        try:
            sig_ts = datetime.fromisoformat(r["signal_ts"])
            if sig_ts.tzinfo is None:
                sig_ts = sig_ts.replace(tzinfo=timezone.utc)

            age_hours = (now - sig_ts).total_seconds() / 3600
            symbol    = r["symbol"]
            sig_price = r["signal_price"]
            cur_price = current_prices.get(symbol)

            updates = {}

            # 1. Capturar precios en hitos (1h, 4h, 24h)
            #    Usa el precio actual del ciclo de scan (sin llamada API)
            if cur_price is not None:
                if r["price_1h"] is None and age_hours >= 1.0:
                    updates["price_1h"] = cur_price

                if r["price_4h"] is None and age_hours >= 4.0:
                    updates["price_4h"] = cur_price

                if r["price_24h"] is None and age_hours >= 24.0:
                    updates["price_24h"] = cur_price
                    updates["status"] = "completed"

            # 2. Max Runup / Drawdown con velas 1h (una llamada por symbol único)
            if age_hours <= 25.0:
                if symbol not in _klines_cache:
                    try:
                        _klines_cache[symbol] = md.get_klines(symbol, "1h", limit=25)
                    except Exception:
                        _klines_cache[symbol] = None

                df = _klines_cache[symbol]
                if df is not None and not df.empty:
                    high = df["high"].max()
                    low  = df["low"].min()
                    updates["max_runup_pct"]    = round((high - sig_price) / sig_price * 100, 2)
                    updates["max_drawdown_pct"] = round((low - sig_price) / sig_price * 100, 2)

            if updates:
                updates["last_checked_ts"] = now.isoformat()
                set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
                params     = list(updates.values()) + [r["id"]]

                con_up = get_db()
                con_up.execute(f"UPDATE signal_outcomes SET {set_clause} WHERE id = ?", params)
                con_up.commit()
                con_up.close()
                updated_count += 1

        except Exception as e:
            log.warning(f"Error trackeando performance de {r['symbol']} (id={r['id']}): {e}")

    if updated_count > 0:
        log.info(f"Performance Tracking: {updated_count} señales actualizadas.")


# ─────────────────────────────────────────────────────────────────────────────

#  CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────
DB_FILE           = os.path.join(SCRIPT_DIR, "signals.db")
DATA_DIR          = os.path.join(SCRIPT_DIR, "data")
LOGS_DIR          = os.path.join(SCRIPT_DIR, "logs")
SIGNALS_LOG_FILE  = os.path.join(LOGS_DIR, "signals.log")
SYMBOLS_JSON_FILE = os.path.join(DATA_DIR, "symbols_status.json")
SIGNALS_CSV_FILE  = os.path.join(DATA_DIR, "signals_history.csv")
POSITIONS_JSON_FILE = os.path.join(DATA_DIR, "positions_summary.json")
API_HOST          = "0.0.0.0"
API_PORT          = 8000
SCAN_INTERVAL_SEC   = 300
SYMBOLS_REFRESH_SEC = 3600   # refrescar top 20 cada 1 hora

# Cabecera del CSV de señales
_CSV_HEADER = (
    "fecha,hora_utc,symbol,tipo,precio,lrc_pct,rsi_1h,score,score_label,"
    "macro_ok,gatillo,sl_precio,tp_precio,qty_btc,estado\n"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("btc_api")




# ─────────────────────────────────────────────────────────────────────────────
#  API KEY AUTHENTICATION
# ─────────────────────────────────────────────────────────────────────────────

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(key: str = Security(_api_key_header)):
    """Verify API key for sensitive endpoints. If no key configured, allow all."""
    cfg = load_config()
    expected = cfg.get("api_key", "").strip()
    if not expected:
        return  # No key configured = open access (backward compatible)
    if not key or not hmac.compare_digest(key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")




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


# ─────────────────────────────────────────────────────────────────────────────
#  DEDUPLICACIÓN DE SEÑALES
# ─────────────────────────────────────────────────────────────────────────────

_notified_signals: dict = {}  # symbol -> last_notified_iso


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


# ─────────────────────────────────────────────────────────────────────────────
#  ARCHIVOS DE DATOS  (data/ y logs/)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
#  POSICIONES
# ─────────────────────────────────────────────────────────────────────────────

def _calc_pnl(direction: str, entry: float, exit_p: float, qty: float):
    if direction == 'LONG':
        pnl_usd = (exit_p - entry) * qty
        pnl_pct = ((exit_p - entry) / entry) * 100
    else:
        pnl_usd = (entry - exit_p) * qty
        pnl_pct = ((entry - exit_p) / entry) * 100
    return round(pnl_usd, 4), round(pnl_pct, 4)


def db_create_position(data: dict) -> dict:
    con = get_db()
    entry = float(data["entry_price"])
    qty   = float(data.get("qty") or (float(data.get("size_usd", 0) or 0) / entry if entry else 0))
    ts    = data.get("entry_ts") or datetime.now(timezone.utc).isoformat()
    cur = con.execute("""
        INSERT INTO positions
            (scan_id, symbol, direction, status, entry_price, entry_ts,
             sl_price, tp_price, size_usd, qty, atr_entry, be_mult, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        data.get("scan_id"),
        data["symbol"].upper(),
        data.get("direction", "LONG").upper(),
        "open",
        entry,
        ts,
        data.get("sl_price"),
        data.get("tp_price"),
        data.get("size_usd"),
        qty,
        data.get("atr_entry"),
        data.get("be_mult"),
        data.get("notes", ""),
    ))
    pos_id = cur.lastrowid
    con.commit()
    row = con.execute("SELECT * FROM positions WHERE id=?", (pos_id,)).fetchone()
    con.close()
    return dict(row)


def db_get_positions(status: Optional[str] = None) -> list:
    con = get_db()
    if status and status != "all":
        rows = con.execute(
            "SELECT * FROM positions WHERE status=? ORDER BY id DESC", (status,)
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM positions ORDER BY id DESC"
        ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def db_close_position(pos_id: int, exit_price: float, exit_reason: str) -> Optional[dict]:
    con = get_db()
    row = con.execute("SELECT * FROM positions WHERE id=?", (pos_id,)).fetchone()
    if not row:
        con.close()
        return None
    pos = dict(row)
    qty = pos.get("qty") or 0
    pnl_usd, pnl_pct = _calc_pnl(pos["direction"], pos["entry_price"], exit_price, qty)
    exit_ts = datetime.now(timezone.utc).isoformat()
    con.execute("""
        UPDATE positions
        SET status=?, exit_price=?, exit_ts=?, exit_reason=?, pnl_usd=?, pnl_pct=?
        WHERE id=?
    """, ("closed", exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct, pos_id))
    con.commit()
    row = con.execute("SELECT * FROM positions WHERE id=?", (pos_id,)).fetchone()
    con.close()
    closed = dict(row)
    # Kill switch #138: trigger health evaluation for this symbol.
    try:
        from health import trigger_health_evaluation
        trigger_health_evaluation(pos["symbol"], load_config())
    except Exception as e:
        log.warning("health trigger skipped for position close: %s", e)
    return closed


def db_update_position(pos_id: int, data: dict) -> Optional[dict]:
    allowed = {"sl_price", "tp_price", "size_usd", "qty", "notes", "entry_price", "atr_entry", "be_mult"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return None
    con = get_db()
    sets = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [pos_id]
    con.execute(f"UPDATE positions SET {sets} WHERE id=?", vals)
    con.commit()
    row = con.execute("SELECT * FROM positions WHERE id=?", (pos_id,)).fetchone()
    con.close()
    return dict(row) if row else None


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
                from notifier import notify, PositionExitEvent
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


# ─────────────────────────────────────────────────────────────────────────────
#  CACHÉ DE SÍMBOLOS  (validados contra Binance)
# ─────────────────────────────────────────────────────────────────────────────

_symbols_cache: List[str] = []
_symbols_fetched_at: float = 0.0

_binance_valid: set = set()
_binance_valid_at: float = 0.0
BINANCE_INFO_REFRESH_SEC = 6 * 3600   # refrescar lista de pares válidos cada 6h


def _get_binance_usdt_symbols() -> set:
    """Devuelve el conjunto de pares USDT activos en Binance Spot."""
    global _binance_valid, _binance_valid_at
    if _binance_valid and (time.time() - _binance_valid_at) < BINANCE_INFO_REFRESH_SEC:
        return _binance_valid
    try:
        r = req_lib.get(
            "https://api.binance.com/api/v3/exchangeInfo",
            params={"permissions": "SPOT"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        _binance_valid = {
            s["symbol"]
            for s in data.get("symbols", [])
            if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT"
        }
        _binance_valid_at = time.time()
        log.info(f"Binance exchange info cargado: {len(_binance_valid)} pares USDT activos")
    except Exception as e:
        log.warning(f"_get_binance_usdt_symbols error: {e}")
    return _binance_valid


def get_active_symbols(n: int = 20) -> List[str]:
    """Retorna la lista CURADA de 10 símbolos rentables (epic #135).

    Previamente pedía top-N por market cap a CoinGecko, lo que reinsertaba
    los 13 tokens confirmados no rentables (BNB, SOL, XRP, DOT, MATIC,
    LINK, LTC, ATOM, NEAR, FIL, APT, OP, ARB) en cada refresh.

    Diseño actual (epic #121/#135): la lista es ESTÁTICA y vive en
    `btc_scanner.DEFAULT_SYMBOLS`. La validación contra Binance spot se
    mantiene como guarda adicional para evitar scanear pares delisted.
    """
    from btc_scanner import DEFAULT_SYMBOLS
    global _symbols_cache, _symbols_fetched_at
    if not _symbols_cache or (time.time() - _symbols_fetched_at) > SYMBOLS_REFRESH_SEC:
        candidates = DEFAULT_SYMBOLS[:n]
        valid_on_binance = _get_binance_usdt_symbols()
        if valid_on_binance:
            dropped = [s for s in candidates if s not in valid_on_binance]
            if dropped:
                log.warning(f"Símbolos curados no listados en Binance (serán omitidos): {dropped}")
            candidates = [s for s in candidates if s in valid_on_binance]
        _symbols_cache = candidates
        _symbols_fetched_at = time.time()
        log.info(f"Símbolos activos (curados): {_symbols_cache}")
    return _symbols_cache


# ─────────────────────────────────────────────────────────────────────────────
#  BASE DE DATOS  (SQLite)
# ─────────────────────────────────────────────────────────────────────────────
# Connection layer (get_db, _DictRow, backup_db) moved to db/connection.py in
# PR0 of the api+db domain refactor (2026-04-27). Re-exports preserved for
# compatibility with scanner_loop and other legacy callers until PR7.
from db.connection import get_db, backup_db, _DictRow  # noqa: F401

_BACKUP_INTERVAL_CYCLES = 288  # ~24h at 5min cycles (288 × 5min = 1440min) — used by scanner_loop
_backup_cycles_since_last = 0


# DB schema (init_db) moved to db/schema.py in PR0 of the api+db domain refactor.
from db.schema import init_db  # noqa: F401



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


# Telegram service moved to api/telegram.py in PR3 of the api+db refactor.
# Re-exports preserved for legacy callers (scanner_loop, etc.) until PR7.
from api.telegram import (  # noqa: F401
    build_telegram_message,
    push_telegram_direct,
    _send_telegram_raw,
    push_webhook,
)


# ─────────────────────────────────────────────────────────────────────────────
#  BACKGROUND SCANNER THREAD
# ─────────────────────────────────────────────────────────────────────────────

_scanner_state = {
    "running":        False,
    "last_scan_ts":   None,
    "last_symbol":    None,
    "last_estado":    "Iniciando...",
    "scans_total":    0,
    "signals_total":  0,
    "errors":         0,
    "symbols_active": [],
}


def execute_scan_for_symbol(sym: str, cfg: dict) -> dict:
    """Ejecuta scan-save-notify para un símbolo. Único punto de verdad usado
    tanto por scanner_loop como por force_scan.

    Retorna un dict con los resultados del escaneo o con clave 'error' si falla.
    """
    try:
        rep     = scan(sym)
        scan_id = save_scan(rep)

        # Auto-check TP/SL para posiciones abiertas en este símbolo
        price_now = rep.get("price")
        if price_now:
            check_position_stops(sym, price_now)

        _scanner_state["last_scan_ts"] = rep.get("timestamp")
        _scanner_state["last_symbol"]  = sym
        _scanner_state["last_estado"]  = rep.get("estado", "")
        _scanner_state["scans_total"] += 1

        estado    = rep.get("estado", "")
        is_signal = rep.get("señal_activa", False)
        is_setup  = "SETUP VÁLIDO" in estado

        if is_signal:
            _scanner_state["signals_total"] += 1
            log.info(f"SENAL {sym} — score {rep.get('score')}/9  "
                     f"precio ${rep.get('price')}")
            append_signal_log(rep, scan_id)
            append_signal_csv(rep, scan_id)
        elif is_setup:
            log.info(f"SETUP {sym} — score {rep.get('score')}/9 (sin gatillo)")
            append_signal_log(rep, scan_id)
            append_signal_csv(rep, scan_id)

        if should_notify_signal(rep, cfg):
            if not _is_duplicate_signal(sym, cfg):
                push_telegram_direct(rep, cfg)
                if cfg.get("webhook_url", "").strip():
                    push_webhook(rep, scan_id, cfg)
                _mark_notified(sym)
            else:
                log.info(f"{sym}: senal duplicada, notificacion omitida")
        else:
            log.info(f"{sym}: {estado[:55]}")

        return {
            "symbol":    sym,
            "scan_id":   scan_id,
            "timestamp": rep.get("timestamp"),
            "estado":    rep.get("estado"),
            "price":     rep.get("price"),
            "lrc_pct":   rep.get("lrc_1h", {}).get("pct"),
            "score":     rep.get("score"),
            "señal":     rep.get("señal_activa"),
            "gatillo":   rep.get("gatillo_activo"),
        }

    except Exception as e:
        _scanner_state["errors"] += 1
        log.error(f"Error escaneando {sym}: {e}")
        return {"symbol": sym, "error": str(e)}


def scanner_loop():
    cfg      = load_config()
    interval = cfg.get("scan_interval_sec", SCAN_INTERVAL_SEC)
    n_sym    = cfg.get("num_symbols", 20)
    log.info(f"Scanner iniciado — intervalo: {interval}s  |  simbolos: {n_sym}")
    _scanner_state["running"] = True

    while _scanner_state["running"]:
        cycle_start = time.time()
        symbols     = get_active_symbols(n_sym)
        _scanner_state["symbols_active"] = symbols
        log.info(f"Ciclo iniciado — {len(symbols)} simbolos")

        # Calentar el caché OHLCV en paralelo para que los scans por símbolo
        # siguientes sean hits del caché en lugar de cold fetches a Binance.
        # El diagnóstico per-símbolo luego hace md.get_klines en 5m/1h/4h/1d.
        try:
            md.prefetch(symbols, ["5m", "1h", "4h"], limit=210)
        except Exception as e:
            log.warning(f"prefetch batch fallo: {e}")

        cycle_prices = {}
        for sym in symbols:
            if not _scanner_state["running"]:
                break
            result = execute_scan_for_symbol(sym, cfg)
            if result and result.get("price"):
                cycle_prices[sym] = result["price"]

        # Actualizar data/symbols_status.json al final de cada ciclo
        try:
            rows = get_signals_summary()
            update_symbols_json(rows)
        except Exception as e:
            log.warning(f"update_symbols_json error en ciclo: {e}")

        # Actualizar data/positions_summary.json
        try:
            update_positions_json()
        except Exception as e:
            log.warning(f"update_positions_json error en ciclo: {e}")

        # Seguimiento de performance de señales
        try:
            check_pending_signal_outcomes(cycle_prices)
        except Exception as e:
            log.warning(f"check_pending_signal_outcomes error en ciclo: {e}")

        # Periodic DB backup (~every 24h, counted per cycle not per symbol)
        global _backup_cycles_since_last
        _backup_cycles_since_last += 1
        if _backup_cycles_since_last >= _BACKUP_INTERVAL_CYCLES:
            backup_db()
            _backup_cycles_since_last = 0

        elapsed    = time.time() - cycle_start
        sleep_time = max(5, interval - elapsed)
        log.info(f"Ciclo completo en {elapsed:.0f}s. Proximo en {sleep_time:.0f}s.")
        time.sleep(sleep_time)


def start_scanner_thread():
    t = threading.Thread(target=scanner_loop, daemon=True, name="crypto-scanner")
    t.start()
    # Kill switch daily sweep (#138)
    from health import health_monitor_loop
    health_thread = threading.Thread(
        target=health_monitor_loop,
        args=(lambda: load_config(),),
        daemon=True,
        name="health-monitor",
    )
    health_thread.start()
    log.info("Health monitor thread started (daily @ 00:00 UTC)")

    # Kill switch v2 auto-calibrator (#214 B4b.1)
    from strategy.kill_switch_v2_calibrator import kill_switch_calibrator_loop
    calibrator_thread = threading.Thread(
        target=kill_switch_calibrator_loop,
        args=(lambda: load_config(),),
        daemon=True,
        name="kill-switch-calibrator",
    )
    calibrator_thread.start()
    log.info("Kill switch v2 calibrator thread started (daily @ 00:00 UTC)")
    return t


# ─────────────────────────────────────────────────────────────────────────────
#  FASTAPI APP
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scanner_thread()
    log.info(f"API disponible en http://localhost:{API_PORT}")
    log.info(f"Documentacion Swagger en http://localhost:{API_PORT}/docs")
    yield
    _scanner_state["running"] = False
    log.info("API detenida.")


app = FastAPI(
    title="Crypto Scanner API",
    description="Top 20 pares USDT Spot 1H — Señal LRC + Score + Gatillo 5M",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

app.include_router(ohlcv_router)
app.include_router(config_router)


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/", summary="Bienvenida y estado general")
def root():
    cfg = load_config()
    return {
        "service":     "Crypto Scanner API — Ultimate Macro V6.0",
        "version":     "2.0.0",
        "symbols":     _scanner_state.get("symbols_active", []),
        "num_symbols": cfg.get("num_symbols", 20),
        "docs":        f"http://localhost:{API_PORT}/docs",
        "scanner":     _scanner_state,
        "webhook_configurado": bool(cfg.get("webhook_url")),
    }


@app.get("/symbols", summary="Estado actual de cada par monitoreado")
def list_symbols():
    """Retorna el último escaneo de cada símbolo, ordenado por señal y score."""
    symbols = _scanner_state.get("symbols_active") or get_active_symbols()
    rows    = get_signals_summary()
    by_sym  = {r["symbol"]: r for r in rows}
    result  = []
    for sym in symbols:
        row = by_sym.get(sym)
        result.append({
            "symbol":  sym,
            "estado":  row["estado"]        if row else "Sin datos aun",
            "price":   row["price"]         if row else None,
            "lrc_pct": row["lrc_pct"]       if row else None,
            "score":   row["score"]         if row else None,
            "señal":   bool(row["señal"])   if row else False,
            "gatillo": bool(row["gatillo"]) if row else False,
            "ts":      row["ts"]            if row else None,
        })
    return {"total": len(result), "symbols": result}


@app.get("/status", summary="Estado detallado del scanner",
         dependencies=[Depends(verify_api_key)])
def status():
    latest = get_latest_scan()
    return {
        "scanner_state": _scanner_state,
        "ultimo_escaneo": {
            "ts":      latest["ts"]      if latest else None,
            "symbol":  latest["symbol"]  if latest else None,
            "estado":  latest["estado"]  if latest else None,
            "price":   latest["price"]   if latest else None,
            "lrc_pct": latest["lrc_pct"] if latest else None,
            "score":   latest["score"]   if latest else None,
        } if latest else None,
        "config": _strip_secrets(load_config()),
        "market_data": md.get_stats(),
    }


@app.post("/scan", summary="Forzar escaneo manual", dependencies=[Depends(verify_api_key)])
def force_scan(
    symbol: Optional[str] = Query(
        None,
        description="Par a escanear (ej: ETHUSDT). Sin valor escanea todos los activos."
    )
):
    """Ejecuta el scanner ahora. Sin symbol escanea todos los pares activos."""
    cfg     = load_config()
    symbols = [symbol.upper()] if symbol else get_active_symbols(cfg.get("num_symbols", 20))
    results = [execute_scan_for_symbol(sym, cfg) for sym in symbols]
    return {"scanned": len(results), "results": results}


@app.post(
    "/kill_switch/recalibrate",
    summary="Manually trigger an auto-calibrator recommendation",
    dependencies=[Depends(verify_api_key)],
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


@app.get(
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


@app.post(
    "/kill_switch/recommendations/{rec_id}/apply",
    summary="Apply a pending recommendation (operator action)",
    dependencies=[Depends(verify_api_key)],
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


@app.post(
    "/kill_switch/recommendations/{rec_id}/ignore",
    summary="Ignore a pending recommendation (operator action)",
    dependencies=[Depends(verify_api_key)],
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


@app.get("/signals", summary="Historial de escaneos / señales")
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


@app.get("/signals/performance", summary="Métricas de éxito de las señales históricas")
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


@app.get("/signals/latest", summary="Ultima señal completa (con gatillo)")
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


@app.get("/signals/latest/message", summary="Mensaje Telegram de la ultima señal")
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


@app.get("/signals/{scan_id}", summary="Detalle de un escaneo por ID")
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


# ── Posiciones ────────────────────────────────────────────────────────────────

@app.get("/positions", summary="Listar posiciones")
def list_positions(
    status: Optional[str] = Query("all", description="open | closed | all")
):
    positions = db_get_positions(status)
    return {"total": len(positions), "positions": positions}


@app.post("/positions", summary="Abrir nueva posicion", dependencies=[Depends(verify_api_key)])
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


@app.put("/positions/{pos_id}", summary="Editar posicion (SL/TP/notas)", dependencies=[Depends(verify_api_key)])
def edit_position(pos_id: int, body: dict = Body(...)):
    pos = db_update_position(pos_id, body)
    if not pos:
        raise HTTPException(status_code=404, detail=f"Posicion #{pos_id} no encontrada")
    update_positions_json()
    return {"ok": True, "position": pos}


@app.post("/positions/{pos_id}/close", summary="Cerrar posicion manualmente", dependencies=[Depends(verify_api_key)])
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


@app.delete("/positions/{pos_id}", summary="Cancelar/eliminar posicion", dependencies=[Depends(verify_api_key)])
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


@app.get("/webhook/test", summary="Probar webhook y Telegram directo", dependencies=[Depends(verify_api_key)])
def test_webhook():
    cfg     = load_config()
    ts      = datetime.now(timezone.utc).isoformat()
    results = {}

    # ── 1. Telegram directo ──────────────────────────────────
    token   = cfg.get("telegram_bot_token", "").strip()
    chat_id = cfg.get("telegram_chat_id", "").strip()
    if token and chat_id:
        try:
            receipts = notify(
                SystemEvent(kind="scanner_connected", message="Scanner online — todo OK"),
                cfg=cfg,
            )
            ok = bool(receipts and receipts[0].status == "ok")
            results["telegram_directo"] = {"ok": ok, "status_code": 200 if ok else 0}
        except Exception as e:
            results["telegram_directo"] = {"ok": False, "error": str(e)}
    else:
        results["telegram_directo"] = {"ok": False, "error": "telegram_bot_token no configurado"}

    # ── 2. Webhook n8n (opcional) ────────────────────────────
    url = cfg.get("webhook_url", "").strip()
    if url:
        payload = {
            "event":            "test",
            "message":          "Crypto Scanner conectado — todo OK",
            "telegram_message": f"*Scanner Conectado*\n`todo OK`\n_{ts}_",
            "chat_id":          chat_id,
            "ts":               ts,
        }
        headers = {"Content-Type": "application/json"}
        if cfg.get("webhook_secret"):
            headers["X-Scanner-Secret"] = cfg["webhook_secret"]
        try:
            r = req_lib.post(url, json=payload, headers=headers, timeout=10)
            results["webhook_n8n"] = {"ok": r.ok, "status_code": r.status_code, "url": url}
        except Exception as e:
            results["webhook_n8n"] = {"ok": False, "error": str(e), "url": url}
    else:
        results["webhook_n8n"] = {"ok": False, "error": "webhook_url no configurado"}

    # Overall OK if at least one notification channel works
    overall_ok = results.get("telegram_directo", {}).get("ok", False) or \
                 results.get("webhook_n8n", {}).get("ok", False)
    return {"ok": overall_ok, **results}


# ── Auto-Tune Endpoints ──────────────────────────────────────────────────────

@app.get("/tune/latest", summary="Latest tune result")
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


@app.post("/tune/apply", summary="Apply pending tune proposal",
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
    backup_path = os.path.join(SCRIPT_DIR, backup_name)
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


@app.post("/tune/reject", summary="Reject pending tune proposal",
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


# ── Kill switch / health endpoints (#138) ─────────────────────────────


class ReactivateRequest(BaseModel):
    reason: str = "manual"


@app.get("/health/symbols", dependencies=[Depends(verify_api_key)])
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


@app.get("/health/events", dependencies=[Depends(verify_api_key)])
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


# ── Notification center endpoints (#162 PR C) ────────────────────────
@app.get("/notifications", dependencies=[Depends(verify_api_key)])
def get_notifications(
    unread: bool = True,
    limit: int = Query(50, ge=1, le=200,
                        description="Max rows returned (capped to prevent unbounded scans)"),
):
    """List notifications recorded by the notifier.

    By default returns only unread entries; pass ?unread=false to include
    read ones too. Sorted most-recent-first.
    """
    from notifier._storage import list_unread
    if not unread:
        # Full list (both read + unread) — use a direct query since list_unread
        # filters on read_at IS NULL.
        con = get_db()
        try:
            rows = con.execute(
                """SELECT id, event_type, event_key, priority, payload_json,
                          channels_sent, delivery_status, sent_at, read_at, error_log
                   FROM notifications_sent
                   ORDER BY sent_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        finally:
            con.close()
        cols = ("id", "event_type", "event_key", "priority", "payload_json",
                "channels_sent", "delivery_status", "sent_at", "read_at", "error_log")
        return {"notifications": [dict(zip(cols, r)) for r in rows]}
    return {"notifications": list_unread(limit=limit)}


@app.post("/notifications/{notif_id}/read", dependencies=[Depends(verify_api_key)])
def post_notification_read(notif_id: int):
    """Mark a single notification as read."""
    from notifier._storage import mark_read
    mark_read(notif_id)
    return {"ok": True, "id": notif_id}


@app.post("/notifications/read-all", dependencies=[Depends(verify_api_key)])
def post_notifications_read_all():
    """Mark all currently-unread notifications as read. Returns how many were updated."""
    from notifier._storage import mark_all_read
    n = mark_all_read()
    return {"ok": True, "marked": n}


# ─────────────────────────────────────────────────────────────────────────────
#  KILL SWITCH OBSERVABILITY ENDPOINTS (#187 phase 1)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/kill_switch/decisions", dependencies=[Depends(verify_api_key)])
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


@app.get("/kill_switch/current_state", dependencies=[Depends(verify_api_key)])
def get_kill_switch_current_state(engine: str = "v1"):
    """Current tier state per symbol + portfolio aggregate (#187 phase 1)."""
    import observability
    return observability.get_current_state(engine=engine)


@app.get("/health/dashboard", dependencies=[Depends(verify_api_key)])
def get_health_dashboard():
    """B6: single-shot consolidated state for the kill switch dashboard.

    Returns per-symbol full state + portfolio aggregate + 24h alert summary.
    Read-only; safe even when kill_switch.enabled=False (returns last-evaluated
    snapshot).
    """
    from health import get_dashboard_state
    cfg = load_config()
    return get_dashboard_state(cfg)


@app.post("/health/reactivate/{symbol}", dependencies=[Depends(verify_api_key)])
def post_health_reactivate(symbol: str, body: ReactivateRequest):
    """Manually reactivate a PAUSED symbol — transitions PAUSED → PROBATION (B5 #199)."""
    from health import reactivate_symbol, get_symbol_state
    cfg = load_config()
    reactivate_symbol(symbol.upper(), reason=body.reason, cfg=cfg)
    return {"ok": True, "symbol": symbol.upper(), "state": get_symbol_state(symbol.upper())}


@app.get("/health", summary="Health check for monitoring and Docker")
def health_check():
    """Returns system health status. HTTP 200 = healthy, 503 = degraded."""
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

    from fastapi.responses import JSONResponse
    return JSONResponse(
        content={"healthy": healthy, "checks": checks},
        status_code=status_code
    )


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("btc_api:app", host=API_HOST, port=API_PORT, reload=False)
