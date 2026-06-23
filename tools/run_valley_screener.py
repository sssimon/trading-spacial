"""Orquestador del screener de valles (Vista Valles A §5.2).

ÚNICO lugar con I/O de red: enumera el universo vivo, baja klines diarias
frescas, aplica el cálculo puro (screener.valley_filter) y escribe una foto
regenerable a data/valley_candidates.json. Un símbolo que falla se OMITE con
su razón (fallo parcial no corrompe el resultado, spec §6); la cobertura se
reporta con honestidad (complete=False si faltó alguno).

Uso: python -m tools.run_valley_screener
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone

import requests

from api.config import load_config
from regime.alt_season import compose_regime, effective_thresholds, symbol_contribution
from regime.exposure_gate import evaluar_gate
from screener.universe import list_live_usdt_spot
from screener.valley_filter import evaluate_symbol, order_neutral

log = logging.getLogger("tools.run_valley_screener")

_KLINES_URL = "https://api.binance.com/api/v3/klines"
_DOMINANCE_URL = "https://api.coingecko.com/api/v3/global"
_HISTORY_DAYS = 400   # cubre la ventana de percentil (365) + margen
_OUTPUT = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       "data", "valley_candidates.json")
_ALT_SEASON_OUTPUT = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                  "data", "alt_season.json")
_BTC_SYMBOL = "BTCUSDT"


def _http_get(url, params=None, timeout=15):
    return requests.get(url, params=params, timeout=timeout)


def _fetch_daily_klines(symbol: str, *, limit: int = _HISTORY_DAYS) -> list[list]:
    """Filas crudas de klines 1d de Binance (público). Lanza en error de red /
    HTTP no-200 para que el caller cuente el símbolo como no evaluado."""
    r = _http_get(_KLINES_URL, params={"symbol": symbol, "interval": "1d", "limit": limit})
    if r.status_code in (429, 418):
        raise RuntimeError(f"rate banned HTTP {r.status_code}")
    if r.status_code != 200:
        raise RuntimeError(f"klines HTTP {r.status_code}")
    return r.json()


def _fetch_dominance() -> float | None:
    """Dominancia de BTC (market-cap) de CoinGecko, fracción 0-1. None ante CUALQUIER
    fallo o valor fuera de rango — degradación elegante; NO tumba la pasada."""
    try:
        r = requests.get(_DOMINANCE_URL, timeout=(3.05, 10))
        if r.status_code != 200:
            log.warning("DOMINANCE_FETCH_HTTP status=%s", r.status_code)
            return None
        dom = float(r.json()["data"]["market_cap_percentage"]["btc"]) / 100.0
    except (requests.RequestException, KeyError, TypeError, ValueError) as e:
        log.warning("DOMINANCE_FETCH_FAILED causa=%s", e)
        return None
    if not (0.0 < dom < 1.0):
        log.warning("DOMINANCE_OUT_OF_RANGE value=%s", dom)
        return None
    return dom


def _rows_to_bars(rows: list[list]) -> list[dict]:
    """Filas crudas de Binance → barras del contrato puro (índices 0,1,2,3,4,5,7)."""
    return [
        {"open_time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "volume": float(r[5]),
         "quote_volume": float(r[7])}
        for r in rows
    ]


def _atomic_write_json(path: str, obj: dict) -> None:
    """Escribe JSON atómicamente: tempfile en el MISMO dir + os.replace. Un lector
    concurrente nunca ve un archivo truncado (no falso 'muerto')."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def aplicar_gate_candidatas(candidatas: list[dict], *, estado: str, votos_vivos: int) -> dict:
    """Aplica el gate (motor 'valles') a las candidatas. Devuelve un dict con
    'candidates' (las que pasan/atenúan) y, SOLO si enabled, 'candidatas_ocultas'.
    Frescura='fresco' trivial: el régimen se computó en esta misma pasada."""
    cfg = load_config()
    if not (cfg.get("regime_gate") or {}).get("enabled", False):
        return {"candidates": candidatas}            # byte-idéntico: sin campos nuevos
    visibles: list[dict] = []
    ocultas: list[dict] = []
    filas: list[dict] = []
    for c in candidatas:
        d = evaluar_gate(estado, "fresco", votos_vivos, es_alt=True, cfg=cfg)
        filas.append({"motor": "valles", "symbol": c["symbol"], "estado_regimen": d.estado_regimen,
                      "nivel": d.nivel, "es_alt": True, "regime_frescura": d.regime_frescura,
                      "votos_vivos": d.votos_vivos, "enforced": d.enforced,
                      "umbral_version": d.umbral_version, "tenant_id": None})
        if d.nivel == "suprime":
            ocultas.append({**c, "clima": d.razon})
        elif d.nivel == "atenua":
            visibles.append({**c, "clima_ambiguo": True})
        else:
            visibles.append(c)
    from db.regime_gate_audit import registrar_decisiones
    try:
        registrar_decisiones(filas)
    except Exception:
        log.warning("regime_gate_audit (valles) falló — fail-open", exc_info=True)
    return {"candidates": visibles, "candidatas_ocultas": ocultas}


def build_snapshot(*, pause_s: float = 0.0,
                   generated_at: str | None = None) -> tuple[dict, dict]:
    """Construye AMBAS fotos (candidatas + régimen) en UNA pasada del universo.
    Devuelve (candidates_snap, alt_season_snap). No escribe a disco."""
    universo = list_live_usdt_spot()
    ts = generated_at or datetime.now(timezone.utc).isoformat()
    candidatas: list[dict] = []
    alt_contribs: list[dict] = []
    btc_ret_30d: float | None = None
    btc_seen = False
    evaluadas = 0
    for sym in universo:
        try:
            rows = _fetch_daily_klines(sym)
        except (requests.RequestException, RuntimeError) as e:
            log.warning("SCREENER_SYMBOL_SKIPPED symbol=%s causa=%s", sym, e)
            continue
        evaluadas += 1
        bars = _rows_to_bars(rows)
        cand = evaluate_symbol(sym, bars)
        if cand is not None:
            candidatas.append(cand)
        contrib = symbol_contribution(sym, bars)
        if contrib is not None:
            if sym == _BTC_SYMBOL:
                btc_ret_30d = contrib["ret_30d"]
                btc_seen = True
            else:
                alt_contribs.append(contrib)
        if pause_s:
            time.sleep(pause_s)

    if not btc_seen:
        log.warning("REGIME_BTC_AUSENTE: BTCUSDT no evaluable en esta pasada")

    coverage = {"universe": len(universo), "evaluated": evaluadas,
                "complete": evaluadas == len(universo)}

    dominance = _fetch_dominance()
    dom_ts = datetime.now(timezone.utc).isoformat() if dominance is not None else None
    coverage_ratio = (evaluadas / len(universo)) if universo else 0.0
    _overrides = (load_config().get("regime_gate") or {}).get("umbral_overrides") or {}
    regime = compose_regime(alt_contribs, btc_ret_30d, dominance, coverage_ratio,
                            thresholds=effective_thresholds(_overrides))

    gate_out = aplicar_gate_candidatas(order_neutral(candidatas),
                                       estado=regime["estado"],
                                       votos_vivos=regime["votos"]["vivos"])
    cand_snap = {"generated_at": ts, "coverage": coverage, **gate_out}
    alt_season_snap = {
        "generated_at": ts,
        "coverage": coverage,
        "dominancia_fetch": {"ok": dominance is not None,
                             "fetched_at": dom_ts,
                             "source": "coingecko/global"},
        "regime": regime,
    }
    return cand_snap, alt_season_snap


def regenerate(*, pause_s: float = 0.05) -> tuple[dict, dict]:
    """build_snapshot + escribe ambos JSON. Usado por main() y por _regenerate_screener."""
    cand_snap, alt_season_snap = build_snapshot(pause_s=pause_s)
    _atomic_write_json(_OUTPUT, cand_snap)            # antes: open(...,"w")+json.dump no-atómico
    _atomic_write_json(_ALT_SEASON_OUTPUT, alt_season_snap)
    return cand_snap, alt_season_snap


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    cand_snap, alt_season_snap = regenerate()
    cov = cand_snap["coverage"]
    print(f"valley_candidates.json: {len(cand_snap['candidates'])} candidatas; "
          f"cobertura {cov['evaluated']}/{cov['universe']} "
          f"({'completa' if cov['complete'] else 'INCOMPLETA'})")
    reg = alt_season_snap["regime"]
    print(f"alt_season.json: régimen={reg['estado']} votos={reg['votos']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
