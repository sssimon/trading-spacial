"""Sondeo C1 — Fear & Greed como senal de mercado (Tipo B). Pre-registrado.

Ver data/retune/2026-06-09-fng-probe/PREREGISTRO.md. Estrategia contrarian
pre-declarada (fear<=25 LONG / greed>=75 SHORT, H=5d, $1000 notional, net-of-v3),
pooling $-weighted, CI95 por block-bootstrap POR EPISODIO, corte pre/post-2021.

Funciones puras (testeables offline) + run() que lee data real y emite verdict.
Cero holdout: usa data/ohlcv.db (research) + data/backtest/fear_greed_history.csv.
"""
from __future__ import annotations

import math
from collections import defaultdict

from backtest_costs import UnknownSymbolError, load_calibration, tier_for_symbol

# ---- Criterios congelados (== PREREGISTRO.md) ----
HOLD_DAYS = 5
HOLD_HOURS = HOLD_DAYS * 24       # 120 -> floor(120/8)=15 intervalos de funding
FEAR_MAX = 25                     # F&G <= 25 -> Extreme Fear -> LONG
GREED_MIN = 75                    # F&G >= 75 -> Extreme Greed -> SHORT
NOTIONAL = 1000.0
N_BOOT = 10_000
SEED = 42
MIN_EPISODES = 10                 # < esto por leg -> UNDERPOWERED (INCONCLUSO, no FAIL)
SPLIT_YEAR = 2021                 # entry < 2021 = PRE; >= 2021 = POST


def signal_for_fng(fng: float) -> int:
    """+1 = LONG (extreme fear), -1 = SHORT (extreme greed), 0 = sin senal."""
    if fng <= FEAR_MAX:
        return 1
    if fng >= GREED_MIN:
        return -1
    return 0


def zone_for_fng(fng: float) -> str:
    """'F' fear, 'G' greed, 'N' neutral."""
    if fng <= FEAR_MAX:
        return "F"
    if fng >= GREED_MIN:
        return "G"
    return "N"


def cost_bps_for(symbol: str, cal=None, hold_hours: int = HOLD_HOURS) -> tuple[float, float, float]:
    """(rt_bps, funding_bps, total_bps) net-of-v3 para un round-trip direccional.
    rt = stress*(2*half_spread + 2*fee); funding = funding_bps_8h * floor(hold/8).
    Lanza UnknownSymbolError si el simbolo no tiene tier (solo curados)."""
    cal = cal or load_calibration()
    tp = cal.tiers[tier_for_symbol(symbol)]
    rt = tp.stress_mult * (2.0 * tp.half_spread_bps + 2.0 * tp.fee_bps_per_side)
    funding = tp.funding_rate_bps_per_8h * (hold_hours // 8)
    return rt, funding, rt + funding


def trade_net_ret(direction: int, gross_ret: float, cost_bps: float) -> float:
    """Retorno neto del trade. direction +1 long / -1 short. cost en bps (resta siempre)."""
    return direction * gross_ret - cost_bps / 1e4


def assign_episodes(dates: list, zones: list[str]) -> dict:
    """{date -> episode_id} para fechas en zona extrema. Un EPISODIO = run contiguo
    de la MISMA zona extrema (la unidad independiente para el bootstrap). dates ordenadas."""
    ep: dict = {}
    cur = 0
    prev = "N"
    for d, z in zip(dates, zones):
        if z in ("F", "G"):
            if z != prev:
                cur += 1
            ep[d] = cur
        prev = z
    return ep


def block_bootstrap_ci(values: list[float], episode_ids: list, n_iter: int = N_BOOT, seed: int = SEED):
    """CI95 del mean por resample de EPISODIOS con reemplazo. Devuelve (lo, hi, n_episodios)."""
    import numpy as np

    groups: dict = defaultdict(list)
    for v, e in zip(values, episode_ids):
        groups[e].append(v)
    keys = list(groups.keys())
    n_ep = len(keys)
    if n_ep == 0:
        return (float("nan"), float("nan"), 0)
    arrs = [np.asarray(groups[k], dtype=float) for k in keys]
    rng = np.random.default_rng(seed)
    means = np.empty(n_iter)
    for i in range(n_iter):
        idx = rng.integers(0, n_ep, n_ep)
        means[i] = np.concatenate([arrs[j] for j in idx]).mean()
    lo, hi = np.percentile(means, [2.5, 97.5])
    return (float(lo), float(hi), n_ep)


def gate(mean_pnl: float, ci_lo: float, ci_hi: float, n_episodes: int,
         min_episodes: int = MIN_EPISODES) -> str:
    """PASS / FAIL / UNDERPOWERED segun los gates pre-registrados (post-2021)."""
    if n_episodes < min_episodes:
        return "UNDERPOWERED"
    if mean_pnl > 0 and ci_lo > 0:
        return "PASS"
    return "FAIL"


# ──────────────────────────────────────────────────────────────────────────
# run() — lee data real, construye trades, agrega, emite verdict. NO es unit test.
# ──────────────────────────────────────────────────────────────────────────

def _load_fng(path: str = "data/backtest/fear_greed_history.csv"):
    import pandas as pd
    df = pd.read_csv(path, index_col="date", parse_dates=True).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


def _load_closes(db_path: str, symbol: str):
    """[(date, close)] 1d para un simbolo, ordenado. date = pandas Timestamp (naive)."""
    import sqlite3
    import pandas as pd
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT open_time, close FROM ohlcv WHERE symbol=? AND timeframe='1d' ORDER BY open_time",
            (symbol,),
        ).fetchall()
    finally:
        con.close()
    out = []
    for ot, close in rows:
        # open_time es ms epoch (heuristica: > 1e12). Robusto a s/ms.
        ts = pd.Timestamp(int(ot), unit="ms") if int(ot) > 1e12 else pd.Timestamp(int(ot), unit="s")
        out.append((ts.normalize(), float(close)))
    return out


def _asof_fng(fng_df, bar_date):
    """Valor F&G as-of (<= bar_date), o None. Replica backtest.py:280-282 (sin look-ahead)."""
    mask = fng_df.index <= bar_date
    if not mask.any():
        return None
    return int(fng_df.loc[mask, "fng"].iloc[-1])


def run(db_path: str = "data/ohlcv.db", cal=None) -> dict:
    """Corre el sondeo pre-registrado. Determinista. Devuelve el dict del verdict."""
    import pandas as pd
    from backtest_costs import _TIER_BY_SYMBOL

    cal = cal or load_calibration()
    fng = _load_fng()

    # Calendario market-wide de zonas (sobre fechas F&G) -> episodios.
    fng_dates = list(fng.index)
    fng_zones = [zone_for_fng(int(v)) for v in fng["fng"].values]
    ep_map = assign_episodes(fng_dates, fng_zones)  # {fng_date -> episode_id}

    symbols = sorted(_TIER_BY_SYMBOL.keys())
    cost_cache = {s: cost_bps_for(s, cal) for s in symbols}

    trades = []  # cada uno: dict(symbol, entry_date, direction, gross, net_ret, pnl, episode, period, leg)
    for sym in symbols:
        closes = _load_closes(db_path, sym)
        _, _, cost_bps = cost_cache[sym]
        for i in range(len(closes) - HOLD_DAYS):
            entry_date, entry_px = closes[i]
            exit_date, exit_px = closes[i + HOLD_DAYS]
            fval = _asof_fng(fng, entry_date)
            if fval is None:
                continue
            direction = signal_for_fng(fval)
            if direction == 0:
                continue
            # episodio por la fecha F&G as-of (<= entry_date)
            asof_date = fng.index[fng.index <= entry_date][-1]
            episode = ep_map.get(asof_date)
            if episode is None:
                continue
            gross = exit_px / entry_px - 1.0
            net_ret = trade_net_ret(direction, gross, cost_bps)
            trades.append({
                "symbol": sym, "entry_date": entry_date, "direction": direction,
                "gross": gross, "net_ret": net_ret, "pnl": NOTIONAL * net_ret,
                "episode": episode, "period": "POST" if entry_date.year >= SPLIT_YEAR else "PRE",
                "leg": "fear_long" if direction == 1 else "greed_short",
            })

    def _agg(rows):
        if not rows:
            return {"n_trades": 0, "n_episodes": 0, "mean_pnl": None, "ci95": [None, None],
                    "mean_gross_pct": None, "verdict": "SIN_DATOS"}
        pnls = [r["pnl"] for r in rows]
        eps = [r["episode"] for r in rows]
        mean_pnl = sum(pnls) / len(pnls)
        lo, hi, n_ep = block_bootstrap_ci(pnls, eps)
        return {
            "n_trades": len(rows), "n_episodes": n_ep,
            "mean_pnl": round(mean_pnl, 4), "ci95": [round(lo, 4), round(hi, 4)],
            "mean_gross_pct": round(100 * sum(r["gross"] * r["direction"] for r in rows) / len(rows), 4),
            "verdict": gate(mean_pnl, lo, hi, n_ep),
        }

    result = {"hold_days": HOLD_DAYS, "fear_max": FEAR_MAX, "greed_min": GREED_MIN,
              "notional": NOTIONAL, "n_boot": N_BOOT, "seed": SEED, "symbols": symbols,
              "cost_bps_by_tier": {t: cost_bps_for(next(s for s in symbols if tier_for_symbol(s) == t), cal)
                                   for t in ("major", "mid", "small")},
              "by_period": {}, "by_period_leg": {}}

    for period in ("PRE", "POST"):
        rows = [t for t in trades if t["period"] == period]
        result["by_period"][period] = _agg(rows)
        for leg in ("fear_long", "greed_short"):
            result["by_period_leg"][f"{period}_{leg}"] = _agg([t for t in rows if t["leg"] == leg])

    # Veredicto del sondeo = el gate POST-2021 pooled (criterio pre-registrado primario).
    post = result["by_period"]["POST"]
    pre = result["by_period"]["PRE"]
    if post["verdict"] == "PASS":
        result["sondeo_verdict"] = "PASS"
    elif post["verdict"] == "UNDERPOWERED":
        result["sondeo_verdict"] = "INCONCLUSO_UNDERPOWERED"
    elif pre.get("verdict") == "PASS" and post["verdict"] == "FAIL":
        result["sondeo_verdict"] = "DEGRADADA"
    else:
        result["sondeo_verdict"] = "FAIL"
    return result


def main() -> int:
    import json
    import pathlib
    res = run()
    out = pathlib.Path("data/retune/2026-06-09-fng-probe")
    out.mkdir(parents=True, exist_ok=True)
    (out / "verdict.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    print(json.dumps(res, indent=2, default=str))
    print(f"\nSONDEO C1 (F&G): {res['sondeo_verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
