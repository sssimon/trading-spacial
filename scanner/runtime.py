"""Scanner runtime — background scan loop, symbol cache, and thread management.

Extracted from btc_api.py in PR7 of the api+db domain refactor (2026-04-27).

Contains:
- _scanner_state          — shared mutable state dict (/, /status, /symbols routes read it)
- _BACKUP_INTERVAL_CYCLES — periodic DB backup cadence
- get_active_symbols      — curated symbol list with Binance validation
- execute_scan_for_symbol — single-symbol scan-save-notify (scanner_loop + /scan endpoint)
- scanner_loop            — background thread target
- start_scanner_thread    — launch scanner + health-monitor + kill-switch-calibrator threads

Import boundary note (test_import_boundaries.py §3.2):
  scanner/* must NOT import api.config, api.signals, api.positions, etc. at top level.
  Those imports are lazy (inside function bodies) — intentional escape hatches per spec.
  api.telegram IS allowed at top level (it's a service, not a router).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import List

import requests as req_lib

from api.telegram import push_telegram_direct, push_webhook
from btc_scanner import scan
from data import market_data as md
from db.connection import backup_db, get_db
from db.signals import get_signals_summary, save_scan
from notifier import notify, SystemEvent

log = logging.getLogger("scanner.runtime")

# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

SCAN_INTERVAL_SEC = 300
SYMBOLS_REFRESH_SEC = 3600
BINANCE_INFO_REFRESH_SEC = 6 * 3600

_BACKUP_INTERVAL_CYCLES = 288  # ~24h at 5min cycles (288 × 5min = 1440min)
_backup_cycles_since_last = 0

# ─────────────────────────────────────────────────────────────────────────────
#  SHARED STATE  (/, /status, /symbols routes read this dict)
# ─────────────────────────────────────────────────────────────────────────────

_scanner_state: dict = {
    "running":        False,
    "last_scan_ts":   None,
    "last_symbol":    None,
    "last_estado":    "Iniciando...",
    "scans_total":    0,
    "signals_total":  0,
    "errors":         0,
    "symbols_active": [],
}

# ─────────────────────────────────────────────────────────────────────────────
#  SYMBOL CACHE  (curated list validated against Binance)
# ─────────────────────────────────────────────────────────────────────────────

_symbols_cache: List[str] = []
_symbols_fetched_at: float = 0.0

_binance_valid: set = set()
_binance_valid_at: float = 0.0


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
    from btc_scanner import DEFAULT_SYMBOLS  # noqa: PLC0415  (lazy — btc_scanner not in top-level)
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
#  PERFORMANCE TRACKING
# ─────────────────────────────────────────────────────────────────────────────

def check_pending_signal_outcomes(current_prices: dict[str, float]):
    """
    Recorre señales pendientes y actualiza su precio 1h, 4h y 24h después.
    También actualiza max_runup y max_drawdown si no han pasado 24h.

    current_prices: {symbol: price} recolectado del ciclo de scan actual,
    para evitar llamadas extra a la API de Binance.
    """
    from datetime import datetime, timezone  # noqa: PLC0415

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
#  SCAN EXECUTION
# ─────────────────────────────────────────────────────────────────────────────

def execute_scan_for_symbol(sym: str, cfg: dict) -> dict:
    """Ejecuta scan-save-notify para un símbolo. Único punto de verdad usado
    tanto por scanner_loop como por force_scan.

    Retorna un dict con los resultados del escaneo o con clave 'error' si falla.
    """
    # Lazy imports to stay within scanner/ boundary rules (see module docstring)
    from api.positions import check_position_stops  # noqa: PLC0415
    from api.signals import (  # noqa: PLC0415
        _is_duplicate_signal, _mark_notified, append_signal_csv,
        append_signal_log, should_notify_signal,
    )

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


# ─────────────────────────────────────────────────────────────────────────────
#  SCANNER LOOP
# ─────────────────────────────────────────────────────────────────────────────

def scanner_loop():
    # Lazy imports to stay within scanner/ boundary rules
    from api.config import load_config  # noqa: PLC0415
    from api.positions import update_positions_json  # noqa: PLC0415
    from api.signals import update_symbols_json  # noqa: PLC0415

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


# ─────────────────────────────────────────────────────────────────────────────
#  THREAD MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def start_scanner_thread():
    # Lazy imports to stay within scanner/ boundary rules
    from api.config import load_config  # noqa: PLC0415

    t = threading.Thread(target=scanner_loop, daemon=True, name="crypto-scanner")
    t.start()
    # Kill switch daily sweep (#138)
    from health import health_monitor_loop  # noqa: PLC0415
    health_thread = threading.Thread(
        target=health_monitor_loop,
        args=(lambda: load_config(),),
        daemon=True,
        name="health-monitor",
    )
    health_thread.start()
    log.info("Health monitor thread started (daily @ 00:00 UTC)")

    # Kill switch v2 auto-calibrator (#214 B4b.1)
    from strategy.kill_switch_v2_calibrator import kill_switch_calibrator_loop  # noqa: PLC0415
    calibrator_thread = threading.Thread(
        target=kill_switch_calibrator_loop,
        args=(lambda: load_config(),),
        daemon=True,
        name="kill-switch-calibrator",
    )
    calibrator_thread.start()
    log.info("Kill switch v2 calibrator thread started (daily @ 00:00 UTC)")
    return t
