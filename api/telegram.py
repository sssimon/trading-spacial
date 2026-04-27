"""Telegram + webhook outbound delivery service.

Extracted from btc_api.py:793-985 in PR3 of the api+db refactor (2026-04-27).

This is a service module (no APIRouter) — telegram is outbound-only.
- build_telegram_message: pure formatting (no I/O).
- push_telegram_direct: DEPRECATED shim (#162) — delegates to notifier.notify(SignalEvent(...)).
- _send_telegram_raw: direct Telegram Bot API HTTP POST.
- push_webhook: posts payload to configured webhook_url + writes audit row to webhooks_sent.

Signal-filter logic (should_notify_signal, _is_duplicate_signal, _mark_notified)
lives in btc_api.py until PR5 — that's signal-domain, not delivery.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests as req_lib

from db.connection import get_db
from notifier import notify, SignalEvent

log = logging.getLogger("api.telegram")

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


# DEPRECATED (#162): for new callers use notifier.notify(SignalEvent(...)).
# Kept because trading_webhook.py and a few legacy paths still consume the
# 'telegram_message' payload key emitted by scan results. Remove after those are migrated.
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
        direction = rep.get("direction", "LONG")
        market = "SPOT" if direction == "LONG" else "FUTURES"
        header = f"SENAL {direction} {symbol} {market}"
        emoji  = "OK" if direction == "LONG" else "STOP_SIGN"
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
            f"   ATR(14) 1H       : ${sz.get('atr_1h', 'N/A')}",
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


# DEPRECATED (#162): for new callers use notifier.notify(SignalEvent(...)).
# This function is now a thin shim that delegates to notifier.notify(SignalEvent(...)).
# Kept so existing callers (scanner loop, existing tests) continue to work during the
# transition. Once all callers patch notifier.notify instead, delete this function.
# trading_webhook.py and legacy paths that consume 'telegram_message' payload key are
# unaffected — they use build_telegram_message() which is unchanged.
def push_telegram_direct(rep: dict, cfg: dict):
    """Envía señal directo a Telegram con retry y backoff exponencial.

    DEPRECATED (#162): delegates to notifier.notify(SignalEvent(...)).
    Retry count is controlled by TelegramChannel (default 3). The previous
    `max_retries` kwarg on this shim was dead — it was never forwarded to
    notify() and no caller passed a non-default value. Dropped in #175.
    """
    # Kill switch #138 PR 2: stamp symbol health state so ALERT symbols get
    # a warning prefix in the Telegram message.
    symbol = rep.get("symbol", "")
    try:
        from health import get_symbol_state
        health_state = get_symbol_state(symbol) if symbol else "NORMAL"
    except Exception as e:
        log.warning("push_telegram_direct: health lookup failed for %s: %s", symbol, e)
        health_state = "NORMAL"

    receipts = notify(
        SignalEvent(
            symbol=symbol,
            score=int(rep.get("score", 0) or 0),
            direction=rep.get("direction", "LONG"),
            entry=float(rep.get("price") or 0.0),
            sl=float((rep.get("sizing_1h") or {}).get("sl_precio") or 0.0),
            tp=float((rep.get("sizing_1h") or {}).get("tp_precio") or 0.0),
            health_state=health_state,
        ),
        cfg=cfg,
    )
    return bool(receipts and receipts[0].status == "ok")


# DEPRECATED (#162): for new callers use notifier.notify(SignalEvent(...)).
# Kept because trading_webhook.py and a few legacy paths still consume the
# 'telegram_message' payload key emitted by scan results. Remove after those are migrated.
def _send_telegram_raw(message: str, cfg: dict):
    """Send a raw message to Telegram without building from a scan report."""
    token = cfg.get("telegram_bot_token", "").strip()
    chat_id = cfg.get("telegram_chat_id", "").strip()
    if not token or not chat_id:
        return
    url = _TELEGRAM_API.format(token=token)
    try:
        r = req_lib.post(url, json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
        }, timeout=10)
        if r.ok:
            log.info(f"Telegram raw send OK -> chat {chat_id}")
        else:
            log.warning(f"Telegram raw send fallo HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:
        log.warning(f"Telegram raw send error: {e}")


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
        "direction":       rep.get("direction", "LONG"),
        "price":           rep.get("price"),
        "lrc_pct":         rep.get("lrc_1h", {}).get("pct"),
        "score":           rep.get("score", 0),
        "score_label":     rep.get("score_label", ""),
        "gatillo_activo":  rep.get("gatillo_activo", False),
        "macro_ok":        rep.get("macro_4h", {}).get("price_above", False),
        "sl_precio":       rep.get("sizing_1h", {}).get("sl_precio"),
        "tp_precio":       rep.get("sizing_1h", {}).get("tp_precio"),
        "qty_btc":         rep.get("sizing_1h", {}).get("qty_btc"),
        "atr_1h":          rep.get("sizing_1h", {}).get("atr_1h"),
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
