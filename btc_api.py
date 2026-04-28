#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║   CRYPTO SCANNER API  —  Ultimate Macro & Order Flow V6.0        ║
║   FastAPI bootstrap — routers in api/*, scanner in scanner/      ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Optional

import requests as req_lib  # tests patch btc_api.req_lib.post (test_api.py); also used directly at line 187
from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# Domain routers
# _strip_secrets + load_config: used in this file (lines 96, 144, 154, 164)
from api.config import _strip_secrets, load_config
# CONFIG_FILE/DEFAULTS_FILE/SECRETS_FILE: monkeypatched by tests (test_api_config_parity,
#   test_api_health_parity, test_api_kill_switch_parity, test_api_notifications_parity,
#   test_api_positions_parity, test_api_tune_parity, test_health_persistence)
# get_config: called as btc_api.get_config in test_api.py (line 314)
# save_config: monkeypatched as btc_api.save_config in test_strategy_kill_switch_v2_calibrator.py
from api.config import CONFIG_FILE, DEFAULTS_FILE, SECRETS_FILE, get_config, save_config  # noqa: F401
from api.config import router as config_router
from api.deps import verify_api_key
from api.health import router as health_router
from api.kill_switch import router as kill_switch_router
from api.notifications import router as notifications_router
from api.ohlcv import router as ohlcv_router
# check_position_stops: called as btc_api.check_position_stops in test_api.py (lines 1115–1188)
# POSITIONS_JSON_FILE: monkeypatched as btc_api.POSITIONS_JSON_FILE in test_api.py (line 1343)
from api.positions import check_position_stops, POSITIONS_JSON_FILE  # noqa: F401
from api.positions import router as positions_router
# Re-exports consumed by test_api.py via btc_api.<name>:
#   _is_duplicate_signal, _mark_notified: lines 1575–1629
#   get_signals_performance: line 1650
#   latest_message, latest_signal, list_signals, signal_by_id: lines 310–313
#   SIGNALS_LOG_FILE: monkeypatched at line 1345
from api.signals import (  # noqa: F401
    _is_duplicate_signal, _mark_notified,
    get_signals_performance, latest_message, latest_signal, list_signals, signal_by_id,
    SIGNALS_LOG_FILE,
)
import api.signals as _api_signals  # noqa: F401
_notified_signals = _api_signals._notified_signals  # tests mutate via btc_api._notified_signals
from api.signals import router as signals_router
# build_telegram_message: called as btc_api.build_telegram_message in test_api.py (lines 121–173)
# push_telegram_direct: called as btc_api.push_telegram_direct in test_health_shim_integration.py (lines 50, 70)
# push_webhook: called as btc_api.push_webhook in test_api.py (lines 521–575)
from api.telegram import build_telegram_message, push_telegram_direct, push_webhook  # noqa: F401
from api.tune import router as tune_router
from btc_scanner import scan  # noqa: F401 — patch("btc_api.scan", ...) in test_api.py line 421
from data import market_data as md  # used directly at line 145; patch.object(btc_api.md) in test_api.py
# DB_FILE: monkeypatched as btc_api.DB_FILE by ~25 test files to redirect SQLite path
# get_db: called as btc_api.get_db() in test_api.py, test_health_persistence.py, and many others
from db.connection import DB_FILE, get_db  # noqa: F401
from db.schema import init_db  # used in lifespan() at line 67; also btc_api.init_db() in many tests
# get_latest_scan + get_signals_summary: used in this file (lines 112, 133)
# get_latest_signal, get_scans, save_scan: called as btc_api.<name> in test_api.py
from db.signals import get_latest_scan, get_latest_signal, get_scans, get_signals_summary, save_scan  # noqa: F401
# db_*: called as btc_api.db_* in test_api.py (position CRUD, lines 827–1088)
from db.positions import db_close_position, db_create_position, db_get_positions, db_update_position  # noqa: F401
from notifier import notify, SystemEvent  # used directly at line 171
from scanner.runtime import (
    _scanner_state, execute_scan_for_symbol, check_pending_signal_outcomes,
    get_active_symbols, start_scanner_thread,
)

DATA_DIR = os.path.join(SCRIPT_DIR, "data")  # noqa: F841 — patched by tests
LOGS_DIR = os.path.join(SCRIPT_DIR, "logs")  # noqa: F841 — patched by tests
API_HOST = "0.0.0.0"
API_PORT = 8000

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("btc_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Initializing DB schema…")
    init_db()
    log.info("Starting scanner thread…")
    start_scanner_thread()
    yield
    _scanner_state["running"] = False
    log.info("Shutdown.")


app = FastAPI(title="Crypto Scanner API", description="Ultimate Macro & Order Flow V6.0",
              version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

app.include_router(ohlcv_router)
app.include_router(config_router)
app.include_router(positions_router)
app.include_router(signals_router)
app.include_router(kill_switch_router)
app.include_router(tune_router)
app.include_router(health_router)
app.include_router(notifications_router)


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
    symbol: Optional[str] = Query(None, description="Par a escanear (ej: ETHUSDT). Sin valor escanea todos.")
):
    """Ejecuta el scanner ahora. Sin symbol escanea todos los pares activos."""
    cfg     = load_config()
    symbols = [symbol.upper()] if symbol else get_active_symbols(cfg.get("num_symbols", 20))
    results = [execute_scan_for_symbol(sym, cfg) for sym in symbols]
    return {"scanned": len(results), "results": results}


@app.get("/webhook/test", summary="Probar webhook y Telegram directo",
         dependencies=[Depends(verify_api_key)])
def test_webhook():
    from datetime import datetime, timezone  # noqa: PLC0415
    cfg     = load_config()
    ts      = datetime.now(timezone.utc).isoformat()
    results = {}
    token   = cfg.get("telegram_bot_token", "").strip()
    chat_id = cfg.get("telegram_chat_id", "").strip()
    if token and chat_id:
        try:
            receipts = notify(SystemEvent(kind="scanner_connected", message="Scanner online — todo OK"), cfg=cfg)
            ok = bool(receipts and receipts[0].status == "ok")
            results["telegram_directo"] = {"ok": ok, "status_code": 200 if ok else 0}
        except Exception as e:
            results["telegram_directo"] = {"ok": False, "error": str(e)}
    else:
        results["telegram_directo"] = {"ok": False, "error": "telegram_bot_token no configurado"}
    url = cfg.get("webhook_url", "").strip()
    if url:
        payload = {"event": "test", "message": "Crypto Scanner conectado — todo OK",
                   "telegram_message": f"*Scanner Conectado*\n`todo OK`\n_{ts}_",
                   "chat_id": chat_id, "ts": ts}
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
    overall_ok = results.get("telegram_directo", {}).get("ok", False) or \
                 results.get("webhook_n8n", {}).get("ok", False)
    return {"ok": overall_ok, **results}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("btc_api:app", host=API_HOST, port=API_PORT, reload=False)
