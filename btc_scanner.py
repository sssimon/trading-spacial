#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║   BTC SCANNER — Ultimate Macro & Order Flow V6.0             ║
║   BTCUSDT SPOT  |  Señal 1H  +  Gatillo 5M                  ║
║                                                              ║
║   LÓGICA MULTI-TIMEFRAME:                                    ║
║     4H  →  Contexto macro (SMA100, tendencia)                ║
║     1H  →  Señal principal (LRC ≤ 25%, score C1-C8)         ║
║     5M  →  Gatillo de entrada (vela confirma reversión)      ║
╚══════════════════════════════════════════════════════════════╝

Uso:
    python3 btc_scanner.py            →  bucle continuo (revisa cada 5 min)
    python3 btc_scanner.py --once     →  un solo escaneo y salir
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import time
import os
import sys
import json
import io
import logging
import threading

from data import market_data as md
from strategy.indicators import (
    calc_lrc, calc_rsi, calc_bb, calc_sma, calc_atr, calc_adx, calc_cvd_delta,
)
from strategy.constants import (
    LRC_PERIOD, LRC_STDEV, RSI_PERIOD, BB_PERIOD, BB_STDEV, VOL_PERIOD,
    ATR_PERIOD,
    LRC_LONG_MAX, LRC_SHORT_MIN, SCORE_MIN_HALF, SCORE_STANDARD, SCORE_PREMIUM,
)
# ── BACKWARD-COMPAT RE-EXPORTS ────────────────────────────────────────────────
# These imports serve a dual purpose: (1) used internally by scan(); or (2)
# re-exported for callers that still do `from btc_scanner import …`. Removing
# a name without migrating all callers will cause silent ImportErrors.
#
# To remove a re-export in the future:
#   1. Run `grep -rn "from btc_scanner import" --include='*.py' .` (multi-line
#      imports span multiple lines — check file contents, not just grep output).
#   2. Migrate callers to import from the home module directly.
#   3. Then remove the import line here.
#
# PR8 cleanup (#225): audit removed names with confirmed 0 external callers.
# Names removed: detect_regime, _save_regime_cache, _REGIME_CACHE_{FILE,PATH},
#   _REGIME_TTL_SEC, _regime_cache, _classify_tune_result, VOL_LOOKBACK_DAYS,
#   _load_proxy, _rate_limit, _API_MIN_INTERVAL, _api_lock,
#   fmt, save_log, main, get_top_symbols, LOG_FILE, SCAN_INTERVAL, STABLECOINS.
# ──────────────────────────────────────────────────────────────────────────────

# strategy/patterns.py — used internally by scan() + some names are re-exported.
# detect_bull_engulfing, score_label, check_trigger_5m: used by scan() internally;
#   also present on module namespace for callers that do `from btc_scanner import …`
#   (detected by backtest.py — missed by single-line grep audit in PR8).
# detect_bear_engulfing, check_trigger_5m_short: same + also in noqa list.
# detect_rsi_divergence: 0 callers via single-line import; has multi-line caller in
#   backtest.py — retained as re-export.  # noqa: F401
from strategy.patterns import (
    detect_bull_engulfing,
    detect_bear_engulfing,  # noqa: F401 — re-exported; callers in tests/test_scanner.py
    detect_rsi_divergence,  # noqa: F401 — re-exported; caller in backtest.py
    score_label,
    check_trigger_5m,
    check_trigger_5m_short,  # noqa: F401 — re-exported; callers in tests/test_scanner.py
)

# strategy/direction.py — used internally by scan() + ATR_SL_MULT is re-exported.
# resolve_direction_params, metrics_inc_direction_disabled: used internally by scan();
#   also present on module namespace for callers (backtest.py, test_symbol_overrides).
# ATR_SL_MULT: has a caller in tests/test_scanner.py.
# ATR_TP_MULT, ATR_BE_MULT: 0 callers via single-line grep; have multi-line callers
#   in tests/test_symbol_overrides_resolution.py and backtest.py — retained.
from strategy.direction import (
    resolve_direction_params,
    metrics_inc_direction_disabled,
    ATR_SL_MULT,  # noqa: F401 — re-exported; caller in tests/test_scanner.py
    ATR_TP_MULT,  # noqa: F401 — re-exported; callers in test_symbol_overrides + backtest.py
    ATR_BE_MULT,  # noqa: F401 — re-exported; callers in test_symbol_overrides + backtest.py
)

# strategy/vol.py — annualized_vol_yang_zhang + TARGET_VOL_ANNUAL have callers;
# VOL_LOOKBACK_DAYS had 0 callers and was removed.
from strategy.vol import annualized_vol_yang_zhang, TARGET_VOL_ANNUAL  # noqa: F401

# strategy/regime.py — 9 names have callers (see table above);
# detect_regime, _save_regime_cache, _REGIME_CACHE_FILE, _REGIME_CACHE_PATH,
# _REGIME_TTL_SEC, _regime_cache had 0 callers and were removed.
from strategy.regime import (  # noqa: F401
    get_cached_regime, detect_regime_for_symbol,
    _compute_price_score, _compute_fng_score, _compute_funding_score,
    _compute_rsi_score, _compute_adx_score,
    _regime_cache_key, _compute_local_regime, _load_regime_cache,
)

# strategy/tune.py — _classify_tune_result had 0 callers; entire re-export removed.
# infra/http.py — _load_proxy, _rate_limit, _API_MIN_INTERVAL, _api_lock had 0 callers;
#   entire re-export block removed.
# cli/scanner_report.py — fmt, save_log, main, get_top_symbols, LOG_FILE, SCAN_INTERVAL,
#   STABLECOINS all had 0 callers; entire re-export block removed.

# Reconfigure stdout for Windows Unicode support
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except (AttributeError, io.UnsupportedOperation):
    pass

log = logging.getLogger("btc_scanner")

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────
SYMBOL         = "BTCUSDT"   # símbolo por defecto / fallback

DEFAULT_SYMBOLS = [
    "BTCUSDT","ETHUSDT","ADAUSDT","AVAXUSDT","DOGEUSDT",
    "UNIUSDT","XLMUSDT","PENDLEUSDT","JUPUSDT","RUNEUSDT",
]

SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))

# ── Parámetros de la estrategia Spot 1H ────────────────────────────────────
SL_PCT         = 2.0      # Stop Loss  2.0%
TP_PCT         = 4.0      # Take Profit 4.0%
COOLDOWN_H     = 6        # Horas mínimas entre trades

# ── Gatillo 5M ─────────────────────────────────────────────────────────────
# Condiciones que debe cumplir la última vela de 5M para activar la entrada.
# Se activa cuando 1H está en setup válido Y la vela 5M confirma reversión.
TRIGGER_RSI_RECOVERY  = True   # RSI 5M sube respecto a la vela anterior
TRIGGER_BULLISH_CLOSE = True   # Vela 5M cierra alcista (close > open)

# ── Filtro de tendencia ADX ────────────────────────────────────────────────
ADX_THRESHOLD = 25  # ADX < 25 = ranging market (OK for mean-reversion)


# ─────────────────────────────────────────────────────────────────────────────
#  SCANNER PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def scan(symbol: str = None):
    symbol = symbol or SYMBOL
    ts  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    rep = {"timestamp": ts, "symbol": symbol, "errors": []}

    # ── Datos de mercado ──────────────────────────────────────────────────────
    df5  = md.get_klines(symbol, "5m",  limit=210)   # gatillo
    df1h = md.get_klines(symbol, "1h",  limit=210)   # señal principal
    df4h = md.get_klines(symbol, "4h",  limit=150)   # contexto macro
    price = df1h["close"].iloc[-1]   # precio de cierre de la última vela 1H

    # ── Load config (reused for regime_mode + symbol_overrides) ─────────────
    _cfg_path = os.path.join(SCRIPT_DIR, "config.json")
    _cfg = {}
    if os.path.exists(_cfg_path):
        try:
            with open(_cfg_path) as _f:
                _cfg = json.load(_f)
        except Exception:
            pass

    # Kill switch #138 PR 4: PAUSED symbols do not generate signals. Early-return
    # with a structured report that mimics the "disabled in config" shape used below.
    try:
        from health import get_symbol_state
        _health_state = get_symbol_state(symbol)
    except Exception as e:
        log.warning("scan: health state lookup failed for %s: %s", symbol, e)
        _health_state = "NORMAL"

    # Observability (#187 phase 1): log the v1 decision so the dashboard
    # can visualize + so future shadow mode can compare v2 vs v1 side-by-side.
    # Fail-open: never break the scanner on observability errors.
    try:
        import observability
        _v1_size_factor = {
            "NORMAL": 1.0, "ALERT": 1.0, "REDUCED": 0.5,
            "PAUSED": 0.0, "PROBATION": 0.5,
        }.get(_health_state, 1.0)
        _v1_skip = (_health_state == "PAUSED")
        observability.record_decision(
            symbol=symbol,
            engine="v1",
            per_symbol_tier=_health_state,
            portfolio_tier="NORMAL",          # phase 1: hardcoded; B2 computes real aggregate
            size_factor=_v1_size_factor,
            skip=_v1_skip,
            reasons={"health_state": _health_state},
            scan_id=None,
            slider_value=None,
            velocity_active=False,
        )
    except Exception as _obs_err:
        log.warning("observability.record_decision failed for %s: %s", symbol, _obs_err)

    # Shadow mode for kill switch v2 (#187 B2): compute + log portfolio tier
    # as engine='v2_shadow' alongside the v1 row. No effect on trading.
    try:
        from strategy.kill_switch_v2_shadow import emit_shadow_decision, update_price
        if not df1h.empty:
            update_price(symbol, float(df1h["close"].iloc[-1]))
        # B3: read the global regime cache for regime-aware adjustment.
        # Cache is daily; this is a cheap dict lookup once warm. If cache is
        # empty (first ever run), _regime_score stays None → NEUTRAL default.
        _regime_score = None
        try:
            _cached = get_cached_regime()
            if _cached and isinstance(_cached, dict):
                _score = _cached.get("score")
                if _score is not None:
                    _regime_score = float(_score)
        except Exception as _rs_err:
            log.warning(
                "kill_switch_v2_shadow: regime score lookup failed for %s: %s "
                "— falling back to NEUTRAL default (no regime adjustment applied)",
                symbol, _rs_err, exc_info=True,
            )
        emit_shadow_decision(
            symbol=symbol,
            cfg=_cfg if _cfg else {},
            regime_score=_regime_score,
        )
    except Exception as _shadow_err:
        log.warning("kill_switch_v2_shadow emission failed for %s: %s", symbol, _shadow_err)

    if _health_state == "PAUSED":
        rep.update({
            "estado": f"🛑 {symbol} PAUSED por kill switch (#138) — reactivar manualmente",
            "señal_activa": False,
            "direction": None,
            "price": round(float(price), 2),
            "health_state": "PAUSED",
        })
        return rep

    # ── Régimen de mercado (compuesto, cacheado por detect_regime()) ──────────
    _regime_mode = _cfg.get("regime_mode", "global")
    if _regime_mode not in ("global", "hybrid", "hybrid_momentum"):
        log.warning(f"Invalid regime_mode='{_regime_mode}' in config; falling back to 'global'")
        _regime_mode = "global"

    if _regime_mode == "global":
        regime_data = get_cached_regime()
    else:
        regime_data = detect_regime_for_symbol(symbol, _regime_mode)

    # ── PURE DECISION KERNEL (#186 A5) ────────────────────────────────────────
    # Delegate the indicators → direction → score → SL/TP → classification chain
    # to strategy.core.evaluate_signal. This function is a pure mirror of the
    # block we used to have inline; it returns a SignalDecision whose fields we
    # map onto the legacy report shape below. Heavy I/O (data fetch, config,
    # regime detect, observability, health-state lookup) stays in scan().
    from strategy.core import evaluate_signal
    from strategy.sizing import compute_size  # wired per A5 spec; not yet
                                              # mapped onto riesgo_usd (see note below).

    # The pure kernel consumes a `regime` dict; pass the raw detector output so
    # evaluate_signal can read the "regime" key (BULL/BEAR/NEUTRAL).
    decision = evaluate_signal(
        df1h, df4h, df5, df1h,   # df1d slot: legacy scan doesn't fetch 1d,
                                 # so pass df1h for shape-compat (unused by core).
        symbol=symbol,
        cfg=_cfg,
        regime=regime_data,
        health_state=_health_state,
        now=datetime.now(timezone.utc),
    )

    # Legacy token: "LONG" | "SHORT" | None (distinct from the kernel's "NONE").
    direction = None if decision.direction == "NONE" else decision.direction

    # Pull indicators out of the decision for the report dict. These are the
    # same values evaluate_signal just computed — no recomputation.
    ind = decision.indicators
    lrc_pct   = ind.get("lrc_pct")
    lrc_up    = ind.get("lrc_upper")
    lrc_dn    = ind.get("lrc_lower")
    lrc_mid   = ind.get("lrc_mid")
    cur_rsi1h = ind.get("rsi_1h")
    bb_up1h_last = ind.get("bb_upper_1h")
    bb_dn1h_last = ind.get("bb_lower_1h")
    sma10_1h  = ind.get("sma10_1h")
    sma20_1h  = ind.get("sma20_1h")
    vol_1h    = ind.get("vol_1h")
    vol_avg1h = ind.get("vol_avg_1h")
    cvd_1h    = ind.get("cvd_1h")
    cur_adx   = ind.get("adx_1h")
    atr_val   = ind.get("atr_1h")
    sma100_4h = ind.get("sma100_4h")
    price_above_4h = ind.get("price_above_sma100_4h")
    bull_div  = ind.get("bull_div_1h")
    bear_div  = ind.get("bear_div_1h")

    # Engulfings are not in the indicators dict (they're boolean conditions,
    # not numeric indicators). Recompute here — cheap, read-only from df1h.
    bull_eng  = detect_bull_engulfing(df1h)
    bear_eng  = detect_bear_engulfing(df1h)

    # ── Condiciones de Exclusión (Spot V6) — legacy shape preserved ───────────
    excl = {
        "E1_BullEngulfing": {
            "activo": bull_eng,
            "nota":   "Vela alcista que cubre la anterior — entrada en micro-techo",
        },
        "E2_Noticias_Macro": {
            "activo": "VERIFICAR_MANUAL",
            "nota":   "Revisar ForexFactory / TradingView Calendar (±30 min)",
        },
        "E3_RachaPerdedora": {
            "activo": "VERIFICAR_MANUAL",
            "nota":   "¿3 o más trades perdedores consecutivos? Pausa 24h",
        },
        "E4_Capital_Min": {
            "activo": "VERIFICAR_MANUAL",
            "nota":   "Capital disponible > $100 (o > 10% del capital inicial)",
        },
        "E5_Cooldown": {
            "activo": "VERIFICAR_MANUAL",
            "nota":   f"¿Han pasado ≥ {COOLDOWN_H}h desde el último trade?",
        },
        "E6_Divergencia_Bajista": {
            "activo": bear_div,
            "nota":   "Precio sube + RSI baja (1H) — peligro de reversión bajista",
        },
        "E7_Tendencia_Fuerte": {
            "activo": "INFORMATIVO",
            "nota":   f"ADX={cur_adx} (>={ADX_THRESHOLD} = tendencia fuerte). Indicador informativo, no bloquea.",
        },
    }

    # ── Score + confirmations (report-layer derivation from decision.indicators)
    # The pure kernel exposes `decision.score` (0 when direction==NONE), but the
    # legacy report always computed the LONG scoring block when direction was
    # unset — callers + tests pin that shape. We reproduce it here without
    # recomputing any indicators: the `add()` closure just reads from the
    # already-computed `ind` dict.
    score = 0
    conf  = {}

    def add(key, pts, passed, extra=None):
        nonlocal score
        pts_earned = pts if passed else 0
        score += pts_earned
        entry = {"pass": bool(passed), "pts": pts_earned, "max_pts": pts}
        if extra:
            entry.update(extra)
        conf[key] = entry

    if direction == "SHORT":
        # Score SHORT (invertido)
        add("C1_RSI_Sobrecompra",     2, cur_rsi1h > 60,
            {"rsi_1h": cur_rsi1h})
        add("C2_Divergencia_Bajista", 2, bear_div)
        dist_res = abs(price - lrc_up) / price * 100 if lrc_up else 999
        add("C3_Resistencia_Cercana", 1, dist_res <= 1.5,
            {"dist_resistencia_pct": round(dist_res, 2)})
        add("C4_BB_Superior",         1, bb_up1h_last is not None and price >= bb_up1h_last,
            {"bb_upper_1h": round(bb_up1h_last, 2) if bb_up1h_last is not None else None})
        add("C5_Volumen",             1, bool(vol_1h >= vol_avg1h),
            {"vol_ratio": round(vol_1h / vol_avg1h, 2)})
        add("C6_CVD_Delta_Negativo",  1, cvd_1h < 0,
            {"cvd_delta": round(cvd_1h, 4)})
        add("C7_SMA10_menor_SMA20",   1, sma10_1h < sma20_1h,
            {"sma10": round(sma10_1h, 2), "sma20": round(sma20_1h, 2)})
    else:
        # Score LONG (original — also the default when direction is None for
        # legacy report parity).
        add("C1_RSI_Sobreventa",      2, cur_rsi1h < 40,
            {"rsi_1h": cur_rsi1h})
        add("C2_Divergencia_Alcista", 2, bull_div)
        dist_sup = abs(price - lrc_dn) / price * 100 if lrc_dn else 999
        add("C3_Soporte_Cercano",     1, dist_sup <= 1.5,
            {"dist_soporte_pct": round(dist_sup, 2)})
        add("C4_BB_Inferior",         1, bb_dn1h_last is not None and price <= bb_dn1h_last,
            {"bb_lower_1h": round(bb_dn1h_last, 2) if bb_dn1h_last is not None else None})
        add("C5_Volumen",             1, bool(vol_1h >= vol_avg1h),
            {"vol_ratio": round(vol_1h / vol_avg1h, 2)})
        add("C6_CVD_Delta_Positivo",  1, cvd_1h > 0,
            {"cvd_delta": round(cvd_1h, 4)})
        add("C7_SMA10_mayor_SMA20",   1, sma10_1h > sma20_1h,
            {"sma10": round(sma10_1h, 2), "sma20": round(sma20_1h, 2)})

    conf["C8_DXY"] = {
        "pass": "MANUAL", "pts": "?", "max_pts": 1,
        "nota": "DXY verificar TradingView (DXY < SMA20 para LONG, > SMA20 para SHORT)",
    }

    # ── Gatillo 5M (kept outside evaluate_signal because the report needs
    # the detailed `trigger_details` dict, which the pure kernel does not
    # expose — it returns only the boolean in decision.reasons). ──────────────
    if direction == "SHORT":
        trigger_active, trigger_details = check_trigger_5m_short(df5)
    else:
        trigger_active, trigger_details = check_trigger_5m(df5)

    # ── Sizing informativo ────────────────────────────────────────────────────
    # Kept at the legacy 1% fixed formula: the existing downstream contract
    # (tests, frontend "riesgo_usd" field, notification templates) pins this.
    # The pure `compute_size` below layers a score-multiplier on top, which is
    # the right model for future v2 sizing — we wire the call in (per #186 A5
    # spec) and pass its result through the decision.reasons for observability
    # / follow-up mapping in a separate task.
    capital    = 1000.0
    risk_usd   = capital * 0.01
    # Kill switch #138 PR 3: halve risk for REDUCED symbols.
    try:
        from health import apply_reduce_factor
        risk_usd = apply_reduce_factor(risk_usd, symbol, _cfg)
    except Exception as e:
        log.warning("scan: reduce-factor lookup failed for %s: %s", symbol, e)

    # compute_size is wired per #186 A5 but NOT mapped onto riesgo_usd to
    # preserve the legacy report contract. Value is recorded in decision.reasons
    # for future migration (epic #187 v2 sizing).
    try:
        _pure_size_usd = compute_size(
            score=int(decision.score),
            health_tier=_health_state,
            capital=capital,
            cfg=_cfg,
        )
        decision.reasons.setdefault("pure_size_usd", _pure_size_usd)
    except Exception as _sz_err:
        log.warning("scan: compute_size(score=%s, tier=%s) failed: %s",
                    decision.score, _health_state, _sz_err)

    # Per-symbol ATR overrides from config (reuse _cfg loaded above)
    _sym_overrides = _cfg.get("symbol_overrides", {})
    _so = _sym_overrides.get(symbol, {})
    if _so is False:
        # Symbol disabled — no signal
        rep.update({"estado": f"⛔ {symbol} deshabilitado en config", "señal_activa": False,
                    "direction": None, "price": round(price, 2)})
        return rep
    resolved = resolve_direction_params(_sym_overrides, symbol, direction)
    if resolved is None:
        # Direction disabled for this symbol (spec §5 form 3).
        metrics_inc_direction_disabled(symbol, direction)
        rep.update({
            "estado": f"⛔ {direction} deshabilitado para {symbol}",
            "señal_activa": False,
            "direction": direction,
            "direction_disabled": True,
            "price": round(price, 2),
        })
        return rep
    _sl_m = resolved["atr_sl_mult"]
    _tp_m = resolved["atr_tp_mult"]
    _be_m = resolved["atr_be_mult"]

    # SL/TP: use the scan-resolved ATR multipliers (honors the legacy
    # monkeypatchable resolve_direction_params). decision.sl_price / tp_price
    # were computed by the pure kernel using its own resolver copy — those
    # values agree whenever overrides aren't monkeypatched, but we recompute
    # here to preserve the legacy branch structure under test.
    sl_dist    = atr_val * _sl_m
    tp_dist    = atr_val * _tp_m

    if direction == "SHORT":
        sl_price   = round(price + sl_dist, 2)   # SL arriba para SHORT
        tp_price   = round(price - tp_dist, 2)   # TP abajo para SHORT
    else:
        sl_price   = round(price - sl_dist, 2)   # SL abajo para LONG
        tp_price   = round(price + tp_dist, 2)   # TP arriba para LONG

    sl_pct_val = round(sl_dist / price * 100, 2)
    tp_pct_val = round(tp_dist / price * 100, 2)

    qty_btc    = risk_usd / sl_dist
    val_pos    = qty_btc * price
    if val_pos > capital * 0.98:
        qty_btc = (capital * 0.98) / price
        val_pos  = qty_btc * price

    # ── Veredicto (estado string + señal flag) ────────────────────────────────
    # Reproduce the legacy branch structure:
    #   - direction is None        → SIN SETUP
    #   - blocks_auto present      → BLOQUEADA
    #   - macro_4h adversa         → SETUP pero macro mala
    #   - sin gatillo 5M           → SETUP válido, esperando gatillo
    #   - todo OK                  → SEÑAL CONFIRMADA
    # evaluate_signal produces a parallel estado string in decision.estado, but
    # the legacy scan() estado format has slightly different macro phrasing —
    # we keep the legacy template here to preserve exact-string parity.
    blocks_long: list[str] = []
    if bull_eng:
        blocks_long.append("E1: BullEngulfing activo — posible micro-techo")
    if bear_div:
        blocks_long.append("E6: Divergencia bajista RSI (1H) — agotamiento alcista")
    blocks_short: list[str] = []
    if bear_eng:
        blocks_short.append("E1S: BearEngulfing activo — posible micro-suelo")
    if bull_div:
        blocks_short.append("E6S: Divergencia alcista RSI (1H) — agotamiento bajista")

    macro_long  = price_above_4h
    macro_short = not price_above_4h
    blocks   = blocks_long if direction == "LONG" else blocks_short if direction == "SHORT" else []
    macro_ok = macro_long if direction == "LONG" else macro_short if direction == "SHORT" else False

    if direction is None:
        estado = "⏳ SIN SETUP — LRC% fuera de zona (25%-75%)"
        señal  = False
    elif blocks:
        estado = f"🚫 BLOQUEADA {direction} — {len(blocks)} exclusión(es) automática"
        señal  = False
    elif not macro_ok:
        macro_desc = "precio < SMA100 4H" if direction == "LONG" else "precio > SMA100 4H"
        estado = f"⚠️  SETUP {direction} — Macro 4H adversa ({macro_desc})"
        señal  = False
    elif not trigger_active:
        estado = f"🕐 SETUP {direction} VÁLIDO — Esperando gatillo 5M"
        señal  = False
    else:
        sl = score_label(score)
        estado = f"✅ SEÑAL {direction} + GATILLO CONFIRMADOS — Calidad: {sl}"
        señal  = True

    # ── Consolidar ────────────────────────────────────────────────────────────
    rep.update({
        "estado":         estado,
        "señal_activa":   señal,
        "direction":      direction,
        "regime":         regime_data.get("regime"),
        "regime_score":   regime_data.get("score"),
        "regime_details": regime_data.get("details"),
        "price":          round(price, 2),
        "lrc_1h": {
            "pct":   lrc_pct,
            "upper": lrc_up,
            "lower": lrc_dn,
            "mid":   lrc_mid,
        },
        "rsi_1h":         cur_rsi1h,
        "adx_1h":         cur_adx,
        "macro_4h": {
            "sma100":       round(sma100_4h, 2),
            "price_above":  price_above_4h,
        },
        "score":          score,
        "score_label":    score_label(score),
        "confirmations":  conf,
        "exclusions":     excl,
        "blocks_auto":    blocks,
        "gatillo_5m":     trigger_details,
        "gatillo_activo": trigger_active,
        "sizing_1h": {
            "capital_usd": capital,
            "riesgo_usd":  round(risk_usd, 2),
            "atr_1h":      round(atr_val, 2),
            "atr_sl_mult": _sl_m,
            "atr_tp_mult": _tp_m,
            "atr_be_mult": _be_m,
            "sl_mode":     "atr",
            "sl_pct":      f"{sl_pct_val}%",
            "tp_pct":      f"{tp_pct_val}%",
            "sl_precio":   sl_price,
            "tp_precio":   tp_price,
            "qty_btc":     round(qty_btc, 6),
            "valor_pos":   round(val_pos, 2),
            "pct_capital": round(val_pos / capital * 100, 1),
        },
    })
    # Convertir tipos numpy a tipos Python nativos para serialización JSON
    import numpy as np
    def clean_dict(d):
        if isinstance(d, dict):
            for k, v in list(d.items()):
                if isinstance(v, np.bool_):
                    d[k] = bool(v)
                elif isinstance(v, np.integer):
                    d[k] = int(v)
                elif isinstance(v, np.floating):
                    d[k] = float(v)
                elif isinstance(v, dict):
                    clean_dict(v)
                elif isinstance(v, list):
                    for i, item in enumerate(v):
                        if isinstance(item, np.bool_):
                            v[i] = bool(item)
                        elif isinstance(item, np.integer):
                            v[i] = int(item)
                        elif isinstance(item, np.floating):
                            v[i] = float(item)
                        elif isinstance(item, dict):
                            clean_dict(item)
        return d
    clean_dict(rep)
    return rep


if __name__ == "__main__":
    from cli.scanner_report import main as _cli_main
    _cli_main()
