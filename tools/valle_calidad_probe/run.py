"""Orquestador del probe valle-calidad (sondeo pre-celda).

Carga el panel (read-only), deriva cutoffs v3w (celda 4), detecta episodios
por símbolo (screener A), simula cada entrada net-of-v3w, agrega los dos
grupos, corre el bootstrap de la diferencia y aplica los gates del
pre-registro. Escribe findings.md + verdict.json + descriptivos.

UNA corrida, determinista, cero excluido. Pre-registro:
data/retune/2026-06-11-valle-calidad-probe/PREREGISTRO.md.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from .bootstrap import bootstrap_diff
from .constants import (
    DB_PATH, MIN_EPISODES_VALLE, NOTIONAL_USD, REGIME_SPLIT, STUDY_END, STUDY_START,
)

_OUT_DIR = "data/retune/2026-06-11-valle-calidad-probe"


def evaluate_verdict(valle: list[dict], no_valle: list[dict]) -> dict:
    """Aplica los gates del pre-registro. PURA: no toca disco ni red.

    UNDERPOWERED si n_valle < MIN_EPISODES_VALLE. Si no: PASS si diff>0 y el
    CI95 excluye cero por el lado positivo (ci_low>0); FAIL en otro caso."""
    boot = bootstrap_diff(valle, no_valle)
    n_valle = boot["n_valle"]
    base = {
        "n_episodios_valle": n_valle,
        "n_episodios_no_valle": boot["n_no_valle"],
        "diff": boot["diff"],
        "ci_low": boot["ci_low"],
        "ci_high": boot["ci_high"],
    }
    if n_valle < MIN_EPISODES_VALLE:
        return {**base, "verdict": "UNDERPOWERED"}
    if boot["diff"] > 0 and boot["ci_low"] > 0:
        return {**base, "verdict": "PASS"}
    return {**base, "verdict": "FAIL"}


def _resample_daily(rows: list[tuple]) -> list[dict]:
    """Resamplea klines 1h (open_time_ms, o, h, l, c, vol, quote_vol) a barras
    DIARIAS del contrato del screener. Agrupa por día UTC: open=primer open,
    high=max, low=min, close=último close, volumen sumado."""
    by_day: dict[int, list[tuple]] = {}
    for r in rows:
        day = int(r[0]) // 86_400_000
        by_day.setdefault(day, []).append(r)
    bars = []
    for day in sorted(by_day):
        g = by_day[day]
        bars.append({
            "open_time": day * 86_400_000,
            "open": float(g[0][1]), "high": max(float(x[2]) for x in g),
            "low": min(float(x[3]) for x in g), "close": float(g[-1][4]),
            "volume": sum(float(x[5]) for x in g),
            "quote_volume": sum(float(x[6]) for x in g),
        })
    return bars


def input_fingerprint(con, study_end_ms: int) -> dict:
    """Huella del input para el dictamen: conteo de filas y símbolos del panel
    dentro de la ventana, para que el verdict sea reproducible/auditable."""
    n_rows = con.execute(
        "SELECT COUNT(*) FROM spot_klines WHERE open_time <= ?", (study_end_ms,)
    ).fetchone()[0]
    n_sym = con.execute(
        "SELECT COUNT(DISTINCT symbol) FROM spot_klines WHERE open_time <= ?",
        (study_end_ms,)
    ).fetchone()[0]
    return {"spot_rows_in_window": int(n_rows), "spot_symbols": int(n_sym),
            "study_window": [STUDY_START, STUDY_END]}


def main(*, db_path: str = DB_PATH, out_dir: str = _OUT_DIR) -> dict:
    """Corrida terminal. Construye los grupos sobre el panel real, evalúa,
    escribe el dictamen. Importa el costo v3w de la celda 4 aquí (no a nivel de
    módulo) para que los tests de evaluate_verdict no requieran la calibración."""
    from backtest_costs import load_calibration
    from tools.celda4_stat_arb.costs import (
        derive_tier_cutoffs, tier_for_volume, v3w_fill_cost,
    )
    from .episodes import detect_episodes
    from .simulate import simulate_entry

    def _to_ms(s: str) -> int:
        return int(datetime.strptime(s, "%Y-%m-%d")
                   .replace(tzinfo=timezone.utc).timestamp() * 1000)

    study_start_ms, study_end_ms = _to_ms(STUDY_START), _to_ms(STUDY_END)
    calibration = load_calibration()
    cutoffs = derive_tier_cutoffs(db_path)

    valle: list[dict] = []
    no_valle: list[dict] = []
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as con:
        fp = input_fingerprint(con, study_end_ms)
        symbols = [r[0] for r in con.execute(
            "SELECT DISTINCT symbol FROM spot_klines").fetchall()]
        for sym in symbols:
            rows = con.execute(
                "SELECT open_time, open, high, low, close, volume, quote_volume "
                "FROM spot_klines WHERE symbol=? AND open_time>=? AND open_time<=? "
                "ORDER BY open_time", (sym, study_start_ms, study_end_ms)).fetchall()
            bars = _resample_daily(rows)
            eps = detect_episodes(bars)
            if not eps:
                continue
            # mediana de dollar-volume diario del símbolo (asigna tier v3w).
            vols = sorted(b["quote_volume"] for b in bars)
            mv = vols[len(vols) // 2] if vols else 0.0
            tier = tier_for_volume(mv, cutoffs)

            def _fill_cost(notional, *, median_daily_dollar_vol, forced_close):
                return v3w_fill_cost(notional, tier, calibration,
                                     median_daily_dollar_vol=median_daily_dollar_vol,
                                     forced_close=forced_close)

            for eid, ep in enumerate(eps):
                e = simulate_entry(bars, entry_idx=ep["entry_idx"], tipo=ep["tipo"],
                                   episode_id=eid, median_dollar_vol=mv,
                                   fill_cost=_fill_cost)
                (valle if ep["tipo"] == "valle" else no_valle).append(e)

    verdict = evaluate_verdict(valle, no_valle)
    verdict["fingerprint"] = fp
    verdict["coordenada"] = {"edicion": 2, "candidata": "valle-calidad",
                             "tipo": "sondeo-pre-celda", "verbo": "F"}
    verdict["fecha"] = STUDY_END  # estampar la ventana, no la fecha de corrida (determinismo)

    # Robustez (REPORTE, no gate — pre-registro §Robustez): la diferencia partida
    # en dos mitades por REGIME_SPLIT. Solo informa si la señal es estable o
    # concentrada en un régimen; el verdict de arriba corre sobre la ventana completa.
    split_ms = _to_ms(REGIME_SPLIT)
    def _half(entries, lo, hi):
        return [e for e in entries if lo <= e["entry_ts"] < hi]
    verdict["robustez"] = {
        "split": REGIME_SPLIT,
        "primera_mitad": bootstrap_diff(_half(valle, study_start_ms, split_ms),
                                        _half(no_valle, study_start_ms, split_ms)),
        "segunda_mitad": bootstrap_diff(_half(valle, split_ms, study_end_ms + 1),
                                        _half(no_valle, split_ms, study_end_ms + 1)),
    }

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "verdict.json"), "w", encoding="utf-8") as f:
        json.dump(verdict, f, indent=2, ensure_ascii=False)
    _write_findings(out_dir, verdict)
    return verdict


def _write_findings(out_dir: str, v: dict) -> None:
    """findings.md: veredicto en la línea 1, luego los números del gate."""
    linea1 = {
        "PASS": "PASS — la consolidación aporta sobre el baseline; abrir celda formal E2.",
        "FAIL": "FAIL — estar en el valle no aporta sobre comprar no-valles; ranking muere.",
        "UNDERPOWERED": "UNDERPOWERED — episodios de valle insuficientes; inconcluso.",
    }[v["verdict"]]
    txt = (
        f"# Dictamen — probe valle-calidad\n\n"
        f"**{linea1}**\n\n"
        f"- diff (mean valle − mean no_valle): {v['diff']:.4f} $/entrada\n"
        f"- CI95 (block-bootstrap por episodio, seed 42): "
        f"[{v['ci_low']:.4f}, {v['ci_high']:.4f}]\n"
        f"- N episodios valle: {v['n_episodios_valle']} "
        f"(umbral de poder: {MIN_EPISODES_VALLE})\n"
        f"- N episodios no-valle: {v['n_episodios_no_valle']}\n"
        f"- notional: ${NOTIONAL_USD:.0f} · net-of-v3w · spot · ventana "
        f"{v['fingerprint']['study_window'][0]}→{v['fingerprint']['study_window'][1]}\n\n"
        f"Pre-registro: data/retune/2026-06-11-valle-calidad-probe/PREREGISTRO.md "
        f"(criterios congelados 2026-06-11).\n"
    )
    with open(os.path.join(out_dir, "findings.md"), "w", encoding="utf-8") as f:
        f.write(txt)


if __name__ == "__main__":
    print(main()["verdict"])
