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

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from typing import Optional, List
import threading
import sqlite3
import json
import os
import time
import requests as req_lib
from datetime import datetime, timezone, timedelta
import logging

import sys
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from btc_scanner import scan, get_top_symbols

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────
CONFIG_FILE       = os.path.join(SCRIPT_DIR, "config.json")
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


def load_config() -> dict:
    defaults = {
        "webhook_url":       "",
        "webhook_secret":    "",
        "notify_setup_only": False,
        "scan_interval_sec": SCAN_INTERVAL_SEC,
        "num_symbols":       20,
        "telegram_chat_id":  "",   # chat_id de Telegram donde llegan las alertas
        "telegram_bot_token": "",  # token del bot (envío directo, sin n8n)
        "signal_filters": {
            "min_score":       0,      # score mínimo para enviar (0 = sin filtro)
            "require_macro_ok": False, # exigir macro 4H alcista
            "notify_setup":    False,  # enviar también setups sin gatillo
        },
    }
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            stored = json.load(f)
        # merge signal_filters en lugar de reemplazarlo
        sf_defaults = defaults["signal_filters"].copy()
        defaults.update(stored)
        if "signal_filters" in stored:
            sf_defaults.update(stored["signal_filters"])
        defaults["signal_filters"] = sf_defaults
    return defaults


def save_config(updates: dict) -> dict:
    """Actualiza config.json con los campos recibidos y retorna la config resultante."""
    cfg = load_config()
    # signal_filters se fusiona, no reemplaza
    if "signal_filters" in updates:
        sf = cfg.get("signal_filters", {}).copy()
        sf.update(updates.pop("signal_filters"))
        cfg["signal_filters"] = sf
    cfg.update(updates)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    log.info("config.json actualizado.")
    return cfg


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
        tipo   = "SENAL LONG" if is_sig else "SETUP VALIDO"
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
             sl_price, tp_price, size_usd, qty, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
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
    return dict(row)


def db_update_position(pos_id: int, data: dict) -> Optional[dict]:
    allowed = {"sl_price", "tp_price", "size_usd", "qty", "notes", "entry_price"}
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
    """Auto-cierra posiciones abiertas si el precio toca TP o SL."""
    con = get_db()
    rows = con.execute(
        "SELECT * FROM positions WHERE symbol=? AND status='open'", (symbol.upper(),)
    ).fetchall()
    con.close()
    for pos in [dict(r) for r in rows]:
        if pos["direction"] == "LONG":
            if pos["tp_price"] and price >= pos["tp_price"]:
                db_close_position(pos["id"], pos["tp_price"], "TP_HIT")
                log.info(f"POSICION #{pos['id']} {symbol} TP HIT @ ${pos['tp_price']}")
                _write_position_event_log(pos, "TP_HIT", pos["tp_price"])
            elif pos["sl_price"] and price <= pos["sl_price"]:
                db_close_position(pos["id"], pos["sl_price"], "SL_HIT")
                log.info(f"POSICION #{pos['id']} {symbol} SL HIT @ ${pos['sl_price']}")
                _write_position_event_log(pos, "SL_HIT", pos["sl_price"])


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
    """Retorna la lista de símbolos validados contra Binance, refrescada cada hora."""
    global _symbols_cache, _symbols_fetched_at
    if not _symbols_cache or (time.time() - _symbols_fetched_at) > SYMBOLS_REFRESH_SEC:
        log.info("Actualizando lista de símbolos desde CoinGecko...")
        # Pedir el doble para tener margen al filtrar
        candidates   = get_top_symbols(n * 2)
        valid_on_binance = _get_binance_usdt_symbols()
        if valid_on_binance:
            candidates = [s for s in candidates if s in valid_on_binance]
            log.info(f"Simbolos validos en Binance: {len(candidates)} de {n*2} candidatos")
        _symbols_cache = candidates[:n]
        _symbols_fetched_at = time.time()
        log.info(f"Simbolos activos: {_symbols_cache}")
    return _symbols_cache


# ─────────────────────────────────────────────────────────────────────────────
#  BASE DE DATOS  (SQLite)
# ─────────────────────────────────────────────────────────────────────────────

def get_db():
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = get_db()
    con.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT    NOT NULL,
            symbol      TEXT    NOT NULL DEFAULT 'BTCUSDT',
            estado      TEXT    NOT NULL,
            señal       INTEGER NOT NULL DEFAULT 0,
            setup       INTEGER NOT NULL DEFAULT 0,
            price       REAL,
            lrc_pct     REAL,
            rsi_1h      REAL,
            score       INTEGER,
            score_label TEXT,
            macro_ok    INTEGER,
            gatillo     INTEGER,
            payload     TEXT
        )
    """)
    # Migración: agregar columna symbol si la tabla ya existía sin ella
    try:
        con.execute("ALTER TABLE scans ADD COLUMN symbol TEXT NOT NULL DEFAULT 'BTCUSDT'")
        log.info("DB migrada: columna 'symbol' añadida.")
    except sqlite3.OperationalError:
        pass  # columna ya existe

    con.execute("""
        CREATE TABLE IF NOT EXISTS webhooks_sent (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER REFERENCES scans(id),
            ts      TEXT,
            url     TEXT,
            status  INTEGER,
            ok      INTEGER
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id     INTEGER REFERENCES scans(id),
            symbol      TEXT    NOT NULL,
            direction   TEXT    NOT NULL DEFAULT 'LONG',
            status      TEXT    NOT NULL DEFAULT 'open',
            entry_price REAL    NOT NULL,
            entry_ts    TEXT    NOT NULL,
            sl_price    REAL,
            tp_price    REAL,
            size_usd    REAL,
            qty         REAL,
            exit_price  REAL,
            exit_ts     TEXT,
            exit_reason TEXT,
            pnl_usd     REAL,
            pnl_pct     REAL,
            notes       TEXT
        )
    """)
    con.commit()
    con.close()
    log.info(f"DB inicializada: {DB_FILE}")


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


# ─────────────────────────────────────────────────────────────────────────────
#  FORMATO TELEGRAM
# ─────────────────────────────────────────────────────────────────────────────

def build_telegram_message(rep: dict) -> str:
    estado = rep.get("estado", "")
    symbol = rep.get("symbol", "BTCUSDT")
    price  = rep.get("price", 0)
    lrc    = rep.get("lrc_1h", {})
    score  = rep.get("score", 0)
    slabel = rep.get("score_label", "")
    sz     = rep.get("sizing_1h", {})
    macro  = rep.get("macro_4h", {})
    gat    = rep.get("gatillo_5m", {})
    ts     = rep.get("timestamp", "")

    if rep.get("señal_activa"):
        header = f"SENAL LONG {symbol} SPOT"
        emoji  = "OK"
    elif "SETUP VÁLIDO" in estado:
        header = f"SETUP VALIDO {symbol} - Sin gatillo aun"
        emoji  = "CONFIG"
    else:
        header = f"Scanner Update {symbol}"
        emoji  = "SCAN"

    lines = [
        f"*{header}*",
        f"`{ts}`",
        "",
        f"`{estado}`",
        "",
        f"*Precio:* `${price:,.2f}`",
        f"*LRC 1H:* `{lrc.get('pct')}%`  _(zona <= 25% = LONG)_",
        f"*Score:* `{score}/9`  _{slabel}_",
        f"*Macro 4H:* `{'Alcista' if macro.get('price_above') else 'Adversa'}`  _(Precio vs SMA100)_",
        "",
    ]

    if rep.get("señal_activa"):
        lines += [
            "GESTION DE RIESGO (1H Spot)",
            f"   SL:  `${sz.get('sl_precio', '?')}` _{sz.get('sl_pct', '2%')} abajo_",
            f"   TP:  `${sz.get('tp_precio', '?')}` _{sz.get('tp_pct', '4%')} arriba_",
            "   R:R: `2:1`",
            f"   Qty: `{sz.get('qty_btc', '?')}` _(ejemplo $1,000 capital, riesgo 1%)_",
            "",
        ]
        active_c = [k for k, v in rep.get("confirmations", {}).items()
                    if isinstance(v.get("pass"), bool) and v["pass"]]
        if active_c:
            lines.append("*Confirmaciones activas:*")
            for c in active_c:
                lines.append(f"   - `{c}`")
            lines.append("")

        lines += [
            "Gatillo 5M activo",
            f"   Vela alcista: `{'SI' if gat.get('vela_5m_alcista') else 'NO'}`   "
            f"RSI recuperando: `{'SI' if gat.get('rsi_recuperando') else 'NO'}`",
        ]

    lines += [
        "",
        "*Verificar manualmente:* noticias macro, racha, capital, cooldown 6h, DXY",
        f"_{symbol} Spot 1H V6_",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  TELEGRAM DIRECTO + WEBHOOK
# ─────────────────────────────────────────────────────────────────────────────

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def push_telegram_direct(rep: dict, cfg: dict):
    """Envía señal directo a Telegram (sin n8n). Usa telegram_bot_token del config."""
    token   = cfg.get("telegram_bot_token", "").strip()
    chat_id = cfg.get("telegram_chat_id", "").strip()
    if not token or not chat_id:
        log.debug("Telegram directo no configurado (falta bot_token o chat_id)")
        return
    msg = build_telegram_message(rep)
    url = _TELEGRAM_API.format(token=token)
    try:
        r = req_lib.post(url, json={
            "chat_id":    chat_id,
            "text":       msg,
            "parse_mode": "Markdown",
        }, timeout=10)
        if r.ok:
            log.info(f"Telegram directo OK [{rep.get('symbol')}] -> chat {chat_id}")
        else:
            log.warning(f"Telegram directo fallo HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:
        log.warning(f"Telegram directo error: {e}")


def push_webhook(rep: dict, scan_id: int, cfg: dict):
    url = cfg.get("webhook_url", "").strip()
    if not url:
        log.debug("Webhook no configurado — saltando")
        return

    msg     = build_telegram_message(rep)
    payload = {
        "event":           "crypto_signal",
        "scan_id":         scan_id,
        "chat_id":         cfg.get("telegram_chat_id", ""),
        "timestamp":       rep.get("timestamp"),
        "symbol":          rep.get("symbol", "BTCUSDT"),
        "señal_activa":    rep.get("señal_activa", False),
        "estado":          rep.get("estado", ""),
        "direction":       "LONG",
        "price":           rep.get("price"),
        "lrc_pct":         rep.get("lrc_1h", {}).get("pct"),
        "score":           rep.get("score", 0),
        "score_label":     rep.get("score_label", ""),
        "gatillo_activo":  rep.get("gatillo_activo", False),
        "macro_ok":        rep.get("macro_4h", {}).get("price_above", False),
        "sl_precio":       rep.get("sizing_1h", {}).get("sl_precio"),
        "tp_precio":       rep.get("sizing_1h", {}).get("tp_precio"),
        "qty_btc":         rep.get("sizing_1h", {}).get("qty_btc"),
        "telegram_message": msg,
        "confirmations": {
            k: v for k, v in rep.get("confirmations", {}).items()
            if isinstance(v.get("pass"), bool) and v["pass"]
        },
    }

    headers = {"Content-Type": "application/json"}
    secret  = cfg.get("webhook_secret", "").strip()
    if secret:
        headers["X-Scanner-Secret"] = secret

    try:
        r      = req_lib.post(url, json=payload, headers=headers, timeout=10)
        status = r.status_code
        ok     = r.ok
        log.info(f"Webhook enviado [{rep.get('symbol')}] -> {url}  HTTP {status}")
    except Exception as e:
        status, ok = 0, False
        log.warning(f"Webhook fallo -> {e}")

    con = get_db()
    con.execute(
        "INSERT INTO webhooks_sent (scan_id, ts, url, status, ok) VALUES (?,?,?,?,?)",
        (scan_id, datetime.now(timezone.utc).isoformat(), url, status, 1 if ok else 0)
    )
    con.commit()
    con.close()


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
            push_telegram_direct(rep, cfg)
            if cfg.get("webhook_url", "").strip():
                push_webhook(rep, scan_id, cfg)
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

        for sym in symbols:
            if not _scanner_state["running"]:
                break
            execute_scan_for_symbol(sym, cfg)

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

        elapsed    = time.time() - cycle_start
        sleep_time = max(5, interval - elapsed)
        log.info(f"Ciclo completo en {elapsed:.0f}s. Proximo en {sleep_time:.0f}s.")
        time.sleep(sleep_time)


def start_scanner_thread():
    t = threading.Thread(target=scanner_loop, daemon=True, name="crypto-scanner")
    t.start()
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


@app.get("/status", summary="Estado detallado del scanner")
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
        "config": {k: v for k, v in load_config().items()
                   if k not in ("webhook_secret",)},
    }


@app.post("/scan", summary="Forzar escaneo manual")
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


@app.get("/config", summary="Leer configuracion actual")
def get_config():
    cfg = load_config()
    # nunca exponer el secreto del webhook
    cfg.pop("webhook_secret", None)
    return cfg


@app.post("/config", summary="Actualizar configuracion")
def update_config(body: dict = Body(...)):
    # proteger campos sensibles que no se tocan desde el frontend
    body.pop("webhook_secret", None)
    try:
        updated = save_config(body)
        updated.pop("webhook_secret", None)
        return {"ok": True, "config": updated}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ohlcv", summary="Velas OHLCV para graficar (fuente: Binance)")
def get_ohlcv(
    symbol:   str = Query("BTCUSDT", description="Par de trading (ej: ETHUSDT)"),
    interval: str = Query("1h",      description="Intervalo: 5m,15m,1h,4h,1d"),
    limit:    int = Query(300,       ge=1, le=1000, description="Número de velas"),
):
    """Retorna datos OHLCV listos para lightweight-charts (timestamps en segundos UTC)."""
    VALID = {"1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w","1M"}
    if interval not in VALID:
        raise HTTPException(status_code=400, detail=f"Intervalo invalido: {interval}")
    url = (
        f"https://api.binance.com/api/v3/klines"
        f"?symbol={symbol.upper()}&interval={interval}&limit={limit}"
    )
    try:
        r = req_lib.get(url, timeout=15)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error Binance: {e}")

    candles, volumes = [], []
    for k in raw:
        ts = int(k[0]) // 1000          # ms → segundos UTC
        o, h, l, c, v = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
        candles.append({"time": ts, "open": o, "high": h, "low": l, "close": c})
        volumes.append({
            "time":  ts,
            "value": v,
            "color": "rgba(34,197,94,0.35)" if c >= o else "rgba(239,68,68,0.35)",
        })

    return {"symbol": symbol.upper(), "interval": interval, "candles": candles, "volumes": volumes}


# ── Posiciones ────────────────────────────────────────────────────────────────

@app.get("/positions", summary="Listar posiciones")
def list_positions(
    status: Optional[str] = Query("all", description="open | closed | all")
):
    positions = db_get_positions(status)
    return {"total": len(positions), "positions": positions}


@app.post("/positions", summary="Abrir nueva posicion")
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


@app.put("/positions/{pos_id}", summary="Editar posicion (SL/TP/notas)")
def edit_position(pos_id: int, body: dict = Body(...)):
    pos = db_update_position(pos_id, body)
    if not pos:
        raise HTTPException(status_code=404, detail=f"Posicion #{pos_id} no encontrada")
    update_positions_json()
    return {"ok": True, "position": pos}


@app.post("/positions/{pos_id}/close", summary="Cerrar posicion manualmente")
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


@app.delete("/positions/{pos_id}", summary="Cancelar/eliminar posicion")
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


@app.get("/webhook/test", summary="Probar webhook y Telegram directo")
def test_webhook():
    cfg     = load_config()
    ts      = datetime.now(timezone.utc).isoformat()
    results = {}

    # ── 1. Telegram directo ──────────────────────────────────
    token   = cfg.get("telegram_bot_token", "").strip()
    chat_id = cfg.get("telegram_chat_id", "").strip()
    if token and chat_id:
        try:
            test_msg = f"*Scanner Conectado* ✅\n`Prueba de conexión directa`\n_{ts}_"
            r = req_lib.post(
                _TELEGRAM_API.format(token=token),
                json={"chat_id": chat_id, "text": test_msg, "parse_mode": "Markdown"},
                timeout=10,
            )
            results["telegram_directo"] = {"ok": r.ok, "status_code": r.status_code}
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

    overall_ok = results.get("telegram_directo", {}).get("ok", False)
    return {"ok": overall_ok, **results}


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("btc_api:app", host=API_HOST, port=API_PORT, reload=False)
