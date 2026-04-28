"""CLI scanner — formatter + log writer + main loop + symbol fetcher.

Extracted from btc_scanner.py per #225 PR7. The scanner CLI runs:
- python btc_scanner.py [--once] [SYMBOL]   (entrypoint preserved via delegation)

Or directly:
- python -m cli.scanner_report
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from infra.http import _load_proxy
from strategy.constants import SCORE_PREMIUM, SCORE_STANDARD

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = str(REPO_ROOT / "logs" / "signals_log.txt")
os.makedirs(REPO_ROOT / "logs", exist_ok=True)

SCAN_INTERVAL = 300

STABLECOINS = {
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "USDD", "GUSD", "FRAX",
    "LUSD", "FDUSD", "PYUSD", "SUSD", "CRVUSD", "USDE", "USDS",
}

log = logging.getLogger("cli.scanner_report")


def get_top_symbols(n: int = 20, quote: str = "USDT") -> list:
    """Obtiene los N primeros criptos por capitalización desde CoinGecko.

    Excluye stablecoins y retorna pares USDT. Fallback a btc_scanner.DEFAULT_SYMBOLS
    si CoinGecko no responde.
    """
    import requests as _req
    try:
        proxies = _load_proxy()
        r = _req.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": n * 2,
                "page": 1,
                "sparkline": "false",
            },
            proxies=proxies or None,
            timeout=15,
            headers={"User-Agent": "btc-scanner/1.0"},
        )
        r.raise_for_status()
        symbols = []
        for coin in r.json():
            ticker = coin["symbol"].upper()
            if ticker in STABLECOINS:
                continue
            pair = f"{ticker}{quote}"
            symbols.append(pair)
            if len(symbols) >= n:
                break
        if symbols:
            log.info(f"CoinGecko: top {len(symbols)} símbolos → {symbols[:5]}…")
            return symbols
    except Exception as e:
        log.warning(f"CoinGecko no disponible ({e}). Usando lista por defecto.")
    from btc_scanner import DEFAULT_SYMBOLS
    return DEFAULT_SYMBOLS[:n]


def fmt(rep: dict) -> str:
    """Format a scan report dict into a human-readable text block."""
    SEP = "=" * 65
    DIV = "─" * 65

    def ok(b):
        return "✅" if b is True else ("❌" if b is False else "❓")

    lines = [
        SEP,
        f"  CRYPTO SCANNER  1H+5M  |  {rep.get('symbol','?')}  |  {rep['timestamp']}",
        SEP,
        f"  💰 PRECIO (cierre 1H) : ${rep['price']:,.2f}",
        f"  📡 ESTADO             : {rep['estado']}",
        f"  📐 DIRECCION          : {rep.get('direction') or 'N/A'}",
        DIV,
        "  ── SETUP 1H  (señal principal) ──────────────────────────",
        f"  LRC 1H : {rep['lrc_1h']['pct']}%   "
        f"{'✅ ZONA LONG (≤ 25%)' if rep['lrc_1h']['pct'] and rep['lrc_1h']['pct'] <= 25 else '🔴 ZONA SHORT (≥ 75%)' if rep['lrc_1h']['pct'] and rep['lrc_1h']['pct'] >= 75 else '⏳ Fuera de zona'}",
        f"  Upper  : ${rep['lrc_1h']['upper']}   |   Mid : ${rep['lrc_1h']['mid']}   |   Lower : ${rep['lrc_1h']['lower']}",
        f"  RSI 1H : {rep['rsi_1h']}  {'✅ Sobreventa' if rep['rsi_1h'] < 40 else ''}",
        DIV,
        "  ── CONTEXTO MACRO 4H ────────────────────────────────────",
        f"  SMA100 4H        : ${rep['macro_4h']['sma100']}",
        f"  Precio > SMA100  : {ok(rep['macro_4h']['price_above'])}  "
        f"({'alcista ✅' if rep['macro_4h']['price_above'] else 'bajista ⚠️ — solo operar si hay confluencia fuerte'})",
        DIV,
        f"  ── SCORE 1H : {rep['score']}/9  ({rep['score_label']}) ──────────────────",
    ]

    for k, v in rep.get("confirmations", {}).items():
        passed = v.get("pass")
        sym = ok(passed) if isinstance(passed, bool) else "❓"
        pts = v.get("pts", 0)
        extras = {ek: ev for ek, ev in v.items()
                  if ek not in ("pass", "pts", "max_pts", "nota")}
        nota = f"\n      → {v['nota']}" if "nota" in v else ""
        xs = ("  " + str(extras)) if extras else ""
        lines.append(f"    {sym} {k:<30} {pts}pts{xs}{nota}")

    lines += [DIV, "  ── GATILLO 5M  (precisión de entrada) ───────────────────"]
    gat = rep.get("gatillo_5m", {})

    def g_ok(b):
        return "✅" if b else "❌"

    lines += [
        f"    {g_ok(gat.get('vela_5m_alcista'))}  Vela 5M alcista (close > open)"
        f"  →  open ${gat.get('open_5m')} / close ${gat.get('close_5m')}",
        f"    {g_ok(gat.get('rsi_5m_recuperando'))}  RSI 5M recuperando"
        f"  →  {gat.get('rsi_5m_anterior')} → {gat.get('rsi_5m_actual')}",
        f"    {'✅ GATILLO ACTIVO' if rep.get('gatillo_activo') else '🕐 Gatillo inactivo — esperar próxima vela 5M'}",
    ]

    lines += [DIV, "  ── BLOQUEOS AUTOMÁTICOS ─────────────────────────────────"]
    if rep["blocks_auto"]:
        for b in rep["blocks_auto"]:
            lines.append(f"    🚫 {b}")
    else:
        lines.append("    ✅ Ningún bloqueo automático activo")

    lines += [DIV, "  ── VERIFICAR MANUALMENTE ANTES DE ENTRAR ─────────────────"]
    for k, v in rep.get("exclusions", {}).items():
        if isinstance(v, dict) and v.get("activo") == "VERIFICAR_MANUAL":
            lines.append(f"    📋 {k}: {v.get('nota','')}")

    lines += [DIV, "  ── SIZING  (ejemplo $1,000 capital) ──────────────────────"]
    sz = rep["sizing_1h"]
    lines += [
        f"    Riesgo 1%        : ${sz['riesgo_usd']}",
        f"    SL / TP          : {sz['sl_pct']} / {sz['tp_pct']}   →   R:R 2:1",
        f"    Precio SL        : ${sz['sl_precio']}",
        f"    Precio TP        : ${sz['tp_precio']}",
        f"    Cantidad BTC     : {sz['qty_btc']} BTC",
        f"    Valor posición   : ${sz['valor_pos']}  ({sz['pct_capital']}% del capital)",
    ]

    score = rep['score']
    if score >= SCORE_PREMIUM:
        lines.append(f"    💡 Score ≥ 4 → Puedes usar sizing +50% (riesgo hasta 1.5%)")
    elif score < SCORE_STANDARD:
        lines.append(f"    ⚠️  Score < 2 → Usar sizing 50% (riesgo 0.5%)")

    if rep.get("errors"):
        lines += [DIV, "  ADVERTENCIAS"]
        for e in rep["errors"]:
            lines.append(f"    ⚠️  {e}")

    lines.append(SEP)
    return "\n".join(lines)


def save_log(rep: dict, full_text: str) -> None:
    """Append scan output to logs/signals_log.txt with a per-state format."""
    estado = rep.get("estado", "")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if rep.get("señal_activa"):
            f.write(full_text + "\n\n")
            ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            score = rep.get("score", 0)
            sig_path = os.path.join(str(REPO_ROOT),
                                    f"SIGNAL_LONG_SCORE{score}_{ts_str}.txt")
            with open(sig_path, "w", encoding="utf-8") as sf:
                sf.write(full_text)
            print(f"\n  ⚡ ¡SEÑAL GUARDADA! → {sig_path}")
        elif "SETUP VÁLIDO" in estado:
            f.write(f"[{rep['timestamp']}] 🕐 SETUP VÁLIDO SIN GATILLO | "
                    f"${rep.get('price','?')} | LRC%: {rep.get('lrc_1h',{}).get('pct','?')} | "
                    f"Score: {rep.get('score', 0)}\n")
        else:
            f.write(f"[{rep['timestamp']}] {estado[:50]} | "
                    f"${rep.get('price','?')} | "
                    f"LRC%: {rep.get('lrc_1h',{}).get('pct','?')}\n")


def main() -> None:
    """Scanner CLI loop. Usage: python -m cli.scanner_report [--once] [SYMBOL]"""
    from btc_scanner import scan
    from data import market_data as md

    once = "--once" in sys.argv
    sym_arg = next((a for a in sys.argv[1:] if a != "--once"), None)

    print(f"\n{'='*65}")
    print(f"  CRYPTO SCANNER  |  Señal 1H + Gatillo 5M  |  Top 20 pares")
    print(f"  Log: {LOG_FILE}")
    if not once:
        print(f"  Revisa cada {SCAN_INTERVAL}s  |  Ctrl+C para detener")
    print(f"{'='*65}\n")

    while True:
        symbols = [sym_arg] if sym_arg else get_top_symbols(20)
        try:
            md.prefetch(symbols, ["5m", "1h", "4h"], limit=210)
        except Exception as e:
            log.warning("prefetch batch failed: %s", e)
        try:
            for sym in symbols:
                try:
                    rep = scan(sym)
                    text = fmt(rep)
                    print(text)
                    save_log(rep, text)
                except Exception as e:
                    print(f"\n  ❌ Error en {sym}: {e}\n")
                    with open(LOG_FILE, "a") as f:
                        f.write(f"[{datetime.now(timezone.utc)}] ERROR {sym}: {e}\n")
        except KeyboardInterrupt:
            print("\n\n  ⛔ Scanner detenido.\n")
            break

        if once:
            break

        print(f"\n  ⏳ Próximo ciclo en {SCAN_INTERVAL}s (Ctrl+C para detener)...\n")
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
