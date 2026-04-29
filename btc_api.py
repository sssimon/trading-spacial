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

# Auth (added 2026-04-29) — JWT cookie auth + CSRF + role gating.
from api.auth import router as auth_router
from api.setup import router as setup_router
from auth.audit import log_auth_event
from auth.dependencies import require_role
from auth.middleware import AuthMiddleware, CsrfMiddleware
from auth.password import hash_password
from auth.setup import generate_token as generate_setup_token
from auth.tokens import _jwt_secret  # boot-time validation
from db.auth_schema import (
    has_any_user, init_auth_db, init_system_state,
    is_setup_completed, mark_setup_completed,
)
from db.connection import get_db

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


def _bootstrap_first_user() -> None:
    """Run before the scanner starts. Picks one of three setup paths:

      A) AUTH_INITIAL_ADMIN_EMAIL + AUTH_INITIAL_ADMIN_PASSWORD set →
         create the admin and continue silently. For Ansible/Terraform.
      B) AUTH_DISABLE_WEB_SETUP=1 → print CLI-only banner. No web setup.
      C) Default: generate setup_token, print web banner.

    XOR check: setting only one of the two env vars hard-fails at boot.
    No silent ignore — operators need to see misconfigured deploys.
    Already-configured systems (users exist OR setup_completed_at marked)
    skip this entire function.
    """
    # Already done — the most common case after first boot.
    if has_any_user() or is_setup_completed():
        return

    init_email = os.environ.get("AUTH_INITIAL_ADMIN_EMAIL", "").strip()
    init_pwd = os.environ.get("AUTH_INITIAL_ADMIN_PASSWORD", "")
    disable_web = os.environ.get("AUTH_DISABLE_WEB_SETUP", "").strip() == "1"

    # Hard-fail XOR. Empty password is treated as missing.
    if bool(init_email) != bool(init_pwd):
        raise RuntimeError(
            "Misconfigured initial admin: AUTH_INITIAL_ADMIN_EMAIL and "
            "AUTH_INITIAL_ADMIN_PASSWORD must be set together (or neither). "
            "Set both for unattended setup, or neither and use /setup or "
            "scripts/create_user.py."
        )

    # ── Path A: env vars present → create admin programmatically ─────────
    if init_email and init_pwd:
        from auth.setup import validate_setup_password
        ok, msg = validate_setup_password(init_pwd)
        if not ok:
            raise RuntimeError(
                f"AUTH_INITIAL_ADMIN_PASSWORD does not meet policy: {msg}"
            )
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        pwd_hash = hash_password(init_pwd)
        con = get_db()
        try:
            cur = con.execute(
                "INSERT INTO users (email, password_hash, role, is_active, "
                "created_at, password_changed_at) VALUES (?, ?, 'admin', 1, ?, ?)",
                (init_email.lower(), pwd_hash, now, now),
            )
            uid = int(cur.lastrowid or 0)
            con.commit()
        finally:
            con.close()
        mark_setup_completed(ip=None, method="env_vars")
        log_auth_event(
            event_type="initial_setup_completed",
            success=True,
            user_id=uid,
            metadata={"method": "env_vars", "email": init_email.lower()},
        )
        log.info(
            "First-time setup completed via env vars (user_id=%d, email=%s)",
            uid, init_email.lower(),
        )
        return

    # ── Path B: web setup disabled → CLI-only banner ─────────────────────
    if disable_web:
        bar = "=" * 64
        print(
            f"\n{bar}\n"
            f"  SETUP REQUIRED — first-time installation detected\n"
            f"{bar}\n\n"
            f"  No users exist yet, and AUTH_DISABLE_WEB_SETUP=1.\n"
            f"  Create the first admin user via CLI:\n\n"
            f"    python scripts/create_user.py\n\n"
            f"  The system will start, but every protected route will\n"
            f"  return 401 until a user is created.\n\n"
            f"{bar}\n",
            flush=True,
        )
        return

    # ── Path C: default — web setup token + banner ───────────────────────
    token = generate_setup_token()
    port = int(os.environ.get("API_PORT", str(API_PORT)))
    bar = "=" * 64
    print(
        f"\n{bar}\n"
        f"  SETUP REQUIRED — first-time installation detected\n"
        f"{bar}\n\n"
        f"  No users exist yet. Create the first admin user via:\n\n"
        f"  Web (recommended):\n"
        f"    http://localhost:{port}/setup?token={token}\n\n"
        f"  Or CLI:\n"
        f"    python scripts/create_user.py\n\n"
        f"  The setup token above is valid until setup is completed or\n"
        f"  the process restarts. It is shown only once. If you lose it,\n"
        f"  restart the process to generate a new one.\n\n"
        f"{bar}\n",
        flush=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auth (2026-04-29): fail fast if AUTH_JWT_SECRET is not configured. We
    # call _jwt_secret() once here so a misconfigured deploy crashes at boot
    # rather than on the first /auth/login request.
    _jwt_secret()

    log.info("Initializing DB schema…")
    init_db()
    init_auth_db()
    init_system_state()

    # First-time setup gate. Picks one of three paths (env / cli / web)
    # or no-ops if a user already exists.
    _bootstrap_first_user()

    log.info("Starting scanner thread…")
    start_scanner_thread()
    yield
    _scanner_state["running"] = False
    log.info("Shutdown.")


app = FastAPI(title="Crypto Scanner API", description="Ultimate Macro & Order Flow V6.0",
              version="2.0.0", lifespan=lifespan)

# Middleware order (Starlette processes outermost-first on request).
# user_middleware list builds in reverse: last add_middleware() = outermost.
# We want execution order: CORS → Auth → Csrf → app, so we add in reverse.
app.add_middleware(CsrfMiddleware)
app.add_middleware(AuthMiddleware)

# CORS — must be the OUTERMOST middleware so even 401/403 responses get
# CORS headers (otherwise the browser rejects them as CORS errors and the
# frontend can't read the auth status code).
_CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "AUTH_CORS_ORIGINS",
    "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000",
).split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,                # required for cookie auth
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)

# Auth router goes first so /auth/* doesn't accidentally inherit any
# dependencies from later routers. Setup router right after.
app.include_router(auth_router)
app.include_router(setup_router)
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


@app.post(
    "/scan",
    summary="Forzar escaneo manual",
    # TODO(auth-cleanup): remove verify_api_key after JWT migration stable
    dependencies=[Depends(verify_api_key), Depends(require_role("admin"))],
)
def force_scan(
    symbol: Optional[str] = Query(None, description="Par a escanear (ej: ETHUSDT). Sin valor escanea todos.")
):
    """Ejecuta el scanner ahora. Sin symbol escanea todos los pares activos."""
    cfg     = load_config()
    symbols = [symbol.upper()] if symbol else get_active_symbols(cfg.get("num_symbols", 20))
    results = [execute_scan_for_symbol(sym, cfg) for sym in symbols]
    return {"scanned": len(results), "results": results}


@app.get(
    "/webhook/test",
    summary="Probar webhook y Telegram directo",
    # TODO(auth-cleanup): remove verify_api_key after JWT migration stable
    dependencies=[Depends(verify_api_key), Depends(require_role("admin"))],
)
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
