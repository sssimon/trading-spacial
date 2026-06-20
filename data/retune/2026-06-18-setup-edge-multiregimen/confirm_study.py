"""Estudio de confirmación: ¿el momentum sobrevive al exit REAL de musikito (escalera + runner) +
de-dup + stress de supervivencia? Reusa el cache + edge_study + las features de momentum_study.
Ver CONFIRM_METODOLOGIA.md. Responde P1 con número."""
import json
import statistics as st
from pathlib import Path

import pandas as pd

import edge_study as es
import momentum_study as mom

HERE = Path(__file__).resolve().parent

# Exit real (congelado)
TPS = [0.15, 0.30, 0.50, 0.90]
FRACS = [0.25, 0.25, 0.20, 0.15]
DISASTER = -0.50
HORIZON = 30
DEDUP_DAYS = 14
B1_RATIO = 3


def ladder_return(entry, highs, lows, close_last):
    """Retorno realizado con escalera de targets + runner + piso de desastre. Ver metodología."""
    if entry is None or entry <= 0 or close_last is None or not highs:
        return None
    hi_max = max(highs)
    lo_min = min(lows)
    realized = 0.0
    sold = 0.0
    for tp, fr in zip(TPS, FRACS):
        if hi_max >= entry * (1 + tp):
            realized += fr * tp
            sold += fr
        else:
            break  # TPs ascendentes: si no se alcanza este, tampoco los de arriba
    # catástrofe: si NO se cobró ningún TP y el low tocó -50% → toda la posición a -50%
    if sold == 0.0 and lo_min <= entry * (1 + DISASTER):
        return DISASTER
    runner_frac = 1.0 - sold
    runner_ret = (close_last - entry) / entry
    return realized + runner_frac * runner_ret


def hit_ladder(df, pos):
    """df = DataFrame del símbolo (orden temporal, reset_index); pos = índice entero del día-señal.
    Entrada = open de pos+1; ventana forward [pos+1 .. pos+HORIZON]."""
    n = len(df)
    if pos + 1 >= n:
        return None
    entry = float(df["open"].iloc[pos + 1])
    end = min(pos + HORIZON, n - 1)
    if end < pos + 1:
        return None
    fwd = df.iloc[pos + 1:end + 1]
    highs = [float(x) for x in fwd["high"]]
    lows = [float(x) for x in fwd["low"]]
    close_last = float(df["close"].iloc[end])
    return ladder_return(entry, highs, lows, close_last)


def main():
    universe = mom.universe_from_cache()
    print(f"universo (cache): {len(universe)}")
    symbol_dfs = {}
    for sym in universe:
        try:
            rows = es.download_symbol(sym)
        except Exception:
            continue
        df = es.rows_to_df(rows)
        if df is None or len(df) < 250:
            continue
        df = es.compute_features(df)
        df = mom.add_momentum_features(df)
        df = df.reset_index()  # 'date' como columna, índice entero
        symbol_dfs[sym] = df
    print(f"símbolos con datos: {len(symbol_dfs)}")

    # date -> regime (breadth), reusando el panel
    panel = es.build_panel(symbol_dfs)
    breadth = es.compute_breadth(panel)
    regime_by_date = {d: es.regime_bucket(b) for d, b in breadth.items()}

    sig_lo, sig_hi = es.SIGNAL_START, es.SIGNAL_END

    # ── SETUP: hits momentum-conjunta, de-dup por símbolo (≥DEDUP_DAYS) ──
    setup_hits = []   # dicts: symbol, date, pos, regime, realized, max_fwd_14d
    # candidatos B1 por símbolo: filas vivas NO-breakout, en período de señal
    b1_pool_by_sym = {}
    for sym, df in symbol_dfs.items():
        in_sig = (df["date"] >= sig_lo) & (df["date"] <= sig_hi)
        cond = (df["alive"] & df["breakout_20d"]
                & (df["vol_ratio"] >= mom.VOL_SURGE_MIN) & (df["pct_vs_sma20"] > 0))
        hit_pos = df.index[in_sig & cond].tolist()
        last = None
        for pos in hit_pos:
            d = df["date"].iloc[pos]
            if last is not None and (d - last).days < DEDUP_DAYS:
                continue
            r = hit_ladder(df, pos)
            if r is None:
                continue
            last = d
            setup_hits.append({
                "symbol": sym, "date": d, "pos": pos,
                "regime": regime_by_date.get(d, "unknown"),
                "realized": r,
                "max_fwd_14d": (float(df["max_fwd_14d"].iloc[pos])
                                if pd.notna(df["max_fwd_14d"].iloc[pos]) else None),
            })
        # pool B1 del símbolo (vivos no-breakout)
        b1_cond = df["alive"] & (~df["breakout_20d"])
        b1_pool_by_sym[sym] = {d: pos for pos, d in zip(df.index[in_sig & b1_cond],
                                                         df["date"][in_sig & b1_cond])}
    print(f"setup hits (de-dupeados): {len(setup_hits)}")

    # ── B1: por cada fecha-hit, muestrear vivos-no-breakout, de-dup por símbolo (≥DEDUP_DAYS) ──
    from collections import defaultdict
    setup_by_date = defaultdict(int)
    for h in setup_hits:
        setup_by_date[h["date"]] += 1
    # candidatos por fecha
    cand_by_date = defaultdict(list)
    for sym, dmap in b1_pool_by_sym.items():
        for d, pos in dmap.items():
            cand_by_date[d].append((sym, pos))
    last_b1 = {}  # symbol -> last date sampled for B1
    b1_hits = []
    for d in sorted(setup_by_date):
        want = setup_by_date[d] * B1_RATIO
        pool = sorted(cand_by_date.get(d, []))  # determinista
        ds = pd.Timestamp(d).strftime("%Y-%m-%d")
        off = es.date_seed(ds, len(pool)) if pool else 0
        rotated = pool[off:] + pool[:off]
        taken = 0
        for sym, pos in rotated:
            if taken >= want:
                break
            lb = last_b1.get(sym)
            if lb is not None and (d - lb).days < DEDUP_DAYS:
                continue
            r = hit_ladder(symbol_dfs[sym], pos)
            if r is None:
                continue
            last_b1[sym] = d
            b1_hits.append({"symbol": sym, "date": d, "regime": regime_by_date.get(d, "unknown"),
                            "realized": r})
            taken += 1
    print(f"B1 hits: {len(b1_hits)} (ratio {len(b1_hits)/max(1,len(setup_hits)):.2f})")

    # ── agregación ──
    def agg(hits, key="realized"):
        vals = [h[key] for h in hits if h.get(key) is not None]
        if not vals:
            return {"n": 0, "median": None, "mean": None, "win15": None}
        return {"n": len(vals), "median": st.median(vals), "mean": st.mean(vals),
                "win15": sum(1 for v in vals if v >= 0.15) / len(vals)}

    def by_regime(hits):
        out = {"global": agg(hits)}
        for bk in ("alt-bull", "neutral", "bear"):
            out[bk] = agg([h for h in hits if h["regime"] == bk])
        return out

    setup_agg = by_regime(setup_hits)
    b1_agg = by_regime(b1_hits)

    # ── stress de supervivencia: breakeven p (media) global ──
    setup_real = [h["realized"] for h in setup_hits if h["realized"] is not None]
    b1_mean = b1_agg["global"]["mean"]
    breakeven_p = None
    if setup_real and b1_mean is not None:
        base_mean = st.mean(setup_real)
        # inyectar p% peor caso (-1.0) a los hits: media' = (1-p)*base_mean_de_los_que_quedan...
        # modelo: una fracción p de hits → -1.0 (muerte). media' = (1-p)*base_mean + p*(-1.0).
        # breakeven: (1-p)*base_mean + p*(-1) = b1_mean
        denom = (base_mean + 1.0)
        if denom > 0:
            p = (base_mean - b1_mean) / denom
            breakeven_p = max(0.0, min(1.0, p))

    results = {
        "meta": {
            "exit": {"tps": TPS, "fracs": FRACS, "disaster": DISASTER, "horizon": HORIZON},
            "dedup_days": DEDUP_DAYS, "b1_ratio": B1_RATIO,
            "n_setup": len(setup_hits), "n_b1": len(b1_hits),
            "survivorship_breakeven_p_mean": breakeven_p,
            "caveats": [
                "realized_return = escalera de targets (fill intrabar optimista) + runner al cierre H + piso -50%.",
                "Supervivencia: el cache solo tiene vivos hoy; el breakeven_p estima cuántas muertes ocultas igualarían la media a B1.",
                "Retorno en USDT incluye beta de BTC.",
            ],
        },
        "setup": setup_agg,
        "B1": b1_agg,
    }
    (HERE / "confirm_results.json").write_text(json.dumps(results, indent=2, default=str))
    print(f"escrito confirm_results.json")
    write_findings(results)
    print("== done ==")
    return results


def _pct(x):
    return "n/a" if x is None else f"{x*100:+.1f}%"


def write_findings(r):
    s, b = r["setup"], r["B1"]
    L = ["# Findings — ¿el momentum sobrevive al exit REAL de musikito?", ""]
    m = r["meta"]
    L.append(f"_Exit: escalera {[f'+{int(t*100)}%' for t in TPS]} fracs {FRACS} + runner + piso {int(DISASTER*100)}%, "
             f"horizonte {HORIZON}d. De-dup {DEDUP_DAYS}d. n_setup={m['n_setup']}, n_B1={m['n_b1']}._")
    L.append("")
    L.append("## realized_return (escalera + runner) — setup-momentum vs B1")
    L.append("")
    L.append("| Régimen | setup mediana / media / win15 | B1 mediana / media / win15 | Δmedia |")
    L.append("|---|---|---|---|")
    for bk in ("global", "alt-bull", "neutral", "bear"):
        sc, bc = s[bk], b[bk]
        dmean = None if sc["mean"] is None or bc["mean"] is None else sc["mean"] - bc["mean"]
        L.append(f"| {bk} | {_pct(sc['median'])} / {_pct(sc['mean'])} / "
                 f"{_pct(sc['win15']) if sc['win15'] is not None else 'n/a'} (n={sc['n']}) | "
                 f"{_pct(bc['median'])} / {_pct(bc['mean'])} / "
                 f"{_pct(bc['win15']) if bc['win15'] is not None else 'n/a'} (n={bc['n']}) | {_pct(dmean)} |")
    L.append("")
    bp = m["survivorship_breakeven_p_mean"]
    L.append("## Stress de supervivencia")
    L.append("")
    if bp is None:
        L.append("- breakeven_p: n/a")
    else:
        L.append(f"- **breakeven_p = {bp*100:.1f}%** — si esa fracción de los breakouts hubiera muerto "
                 f"(retorno −100%, no en el cache), la MEDIA del setup igualaría a la de B1. "
                 f"{'FRÁGIL (pocas muertes ocultas lo borran).' if bp < 0.15 else 'Robusto-ish (requiere muchas muertes ocultas).'}")
    L.append("")
    L.append("## Veredicto")
    L.append("")
    g_s, g_b = s["global"], b["global"]
    dmed = None if g_s["median"] is None or g_b["median"] is None else g_s["median"] - g_b["median"]
    dmean = None if g_s["mean"] is None or g_b["mean"] is None else g_s["mean"] - g_b["mean"]
    L.append(f"- Global: Δmediana realized = {_pct(dmed)}, Δmedia = {_pct(dmean)}.")
    L.append(f"- Con el exit REAL (no el stop −12%), ¿el momentum supera a B1? "
             f"{'SÍ en mediana' if (dmed or 0) > 0 else 'NO en mediana'}; "
             f"{'SÍ en media' if (dmean or 0) > 0 else 'NO en media'}.")
    L.append("")
    L.append("## Caveats")
    for c in m["caveats"]:
        L.append(f"- {c}")
    (HERE / "confirm_findings.md").write_text("\n".join(L), encoding="utf-8")
    print("escrito confirm_findings.md")


if __name__ == "__main__":
    main()
