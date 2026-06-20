"""Estudio momentum/breakout — reusa el harness de edge_study.py (cache + panel + baselines +
régimen + Mann-Whitney + retornos forward). Solo cambia la REGLA (momentum) y su control.
Ver MOMENTUM_METODOLOGIA.md. Cero re-fetch (usa el cache de raw_klines)."""
import json
from pathlib import Path

import pandas as pd

import edge_study as es

HERE = Path(__file__).resolve().parent
BREAKOUT_WINDOW = 20
VOL_SURGE_MIN = 1.5


def add_momentum_features(df):
    high = df["high"]
    df["high_20d_prev"] = high.rolling(BREAKOUT_WINDOW, min_periods=BREAKOUT_WINDOW).max().shift(1)
    df["breakout_20d"] = (df["close"] > df["high_20d_prev"]).fillna(False)
    return df


def universe_from_cache():
    return sorted(p.stem for p in es.RAW_DIR.glob("*.json"))


def select_momentum_minimal(panel):
    return panel["alive"] & panel["breakout_20d"]


def select_momentum_conjunct(panel):
    return (panel["alive"] & panel["breakout_20d"]
            & (panel["vol_ratio"] >= VOL_SURGE_MIN)
            & (panel["pct_vs_sma20"] > 0))


def build_b1_momentum(panel, setup_mask):
    """Control: pares vivos que NO rompieron, matcheados por fecha. Determinista."""
    setup_rows = panel[setup_mask]
    if setup_rows.empty:
        return [], 0.0
    cand = panel[panel["alive"] & (~panel["breakout_20d"])]
    cand_by_date = {d: g.index.to_list() for d, g in cand.groupby("date")}
    counts = setup_rows.groupby("date").size()
    idx, tgt, picked = [], 0, 0
    for date, n in counts.items():
        pool = cand_by_date.get(date, [])
        if not pool:
            continue
        want = min(len(pool), int(n) * es.B1_MATCH_RATIO)
        tgt += int(n)
        ds = pd.Timestamp(date).strftime("%Y-%m-%d")
        ps = sorted(pool)
        off = es.date_seed(ds, len(ps))
        chosen = (ps[off:] + ps[:off])[:want]
        idx.extend(chosen)
        picked += len(chosen)
    return idx, (picked / tgt if tgt else 0.0)


def _beats(blk):
    if not blk or blk.get("delta_median_max_fwd_14d") is None:
        return None
    d = blk["delta_median_max_fwd_14d"]
    p = blk["mann_whitney"].get("p_value")
    return (d > 0, p is not None and p < 0.05)


def _vtxt(b):
    if b is None:
        return "sin datos"
    better, sig = b
    if better and sig:
        return "SÍ (Δ>0, p<0.05)"
    if better:
        return "marginal (Δ>0, no significativo)"
    return "NO (Δ≤0)"


def _line(name, blk):
    if not blk or "setup" not in blk:
        return f"- **{name}**: sin datos"
    s, b = blk["setup"], blk["baseline"]
    d = blk["delta_median_max_fwd_14d"]
    return (f"- **{name}**: setup n={s['n']} mediana max_fwd_14d={es._fmt_pct(s['median_max_fwd_14d'])} "
            f"win15={es._fmt_pct(s['win15'])} | B1 n={b['n']} mediana={es._fmt_pct(b['median_max_fwd_14d'])} "
            f"win15={es._fmt_pct(b['win15'])} | Δmediana={es._fmt_pct(d)} ({es._fmt_p(blk['mann_whitney'])})")


def write_findings(results):
    conj = results["tables"]["momentum_conjunct"]["delta_vs_B1"]
    mini = results["tables"]["momentum_minimal"]["delta_vs_B1"]
    g = conj["global"]
    ab = conj["by_regime"].get("alt-bull")
    ne = conj["by_regime"].get("neutral")
    be = conj["by_regime"].get("bear")
    nb = es._combine_outside_altbull(results, "momentum_conjunct")

    L = []
    L.append("# Findings — ¿la lectura MOMENTUM de musikito tiene edge?")
    L.append("")
    h = results["meta"]["hits"]
    L.append(f"_Regla: breakout sobre máx de {BREAKOUT_WINDOW}d + volumen ≥{VOL_SURGE_MIN}× + sobre SMA20. "
             f"{results['meta']['symbols']} símbolos, panel {results['meta']['panel_rows']} filas. "
             f"hits mínima={h['momentum_minimal']}, conjunta={h['momentum_conjunct']}._")
    L.append("")
    L.append("## Veredicto (regla-conjunta vs B1, mediana max_fwd_14d, Mann-Whitney one-sided)")
    L.append("")
    L.append(f"- (a) **Global**: {_vtxt(_beats(g))}")
    L.append(f"- (b) **En alt-bull** (breadth≥0.6): {_vtxt(_beats(ab))}")
    L.append(f"- (c) **Fuera de alt-bull**: {_vtxt(_beats(nb))}")
    L.append("")
    L.append("## Regla-conjunta (momentum completo) vs B1 por régimen")
    L.append("")
    L.append(_line("Global", g))
    L.append(_line("alt-bull", ab))
    L.append(_line("neutral", ne))
    L.append(_line("bear", be))
    L.append("")
    L.append("## Regla-mínima (solo breakout) vs B1")
    L.append("")
    L.append(_line("Global", mini["global"]))
    L.append(_line("alt-bull", mini["by_regime"].get("alt-bull")))
    L.append(_line("neutral", mini["by_regime"].get("neutral")))
    L.append(_line("bear", mini["by_regime"].get("bear")))
    L.append("")
    L.append("## Caveats")
    L.append("")
    for c in results["meta"]["caveats"]:
        L.append(f"- {c}")
    L.append("")
    (HERE / "momentum_findings.md").write_text("\n".join(L), encoding="utf-8")
    print(f"escrito {HERE / 'momentum_findings.md'}")


def main():
    universe = universe_from_cache()
    print(f"universo (cache): {len(universe)}")
    symbol_dfs = {}
    for i, sym in enumerate(universe, 1):
        try:
            rows = es.download_symbol(sym)  # cache hit
        except Exception as e:
            print(f"  {sym}: ERROR {e}")
            continue
        df = es.rows_to_df(rows)
        if df is None or len(df) < 250:
            continue
        df = es.compute_features(df)
        df = add_momentum_features(df)
        symbol_dfs[sym] = df
        if i % 75 == 0:
            print(f"  [{i}/{len(universe)}] {len(symbol_dfs)} con datos")
    print(f"símbolos con datos: {len(symbol_dfs)}")

    panel = es.build_panel(symbol_dfs)
    breadth = es.compute_breadth(panel)
    panel = panel.merge(breadth.rename("breadth"), left_on="date", right_index=True, how="left")
    panel["regime"] = panel["breadth"].apply(es.regime_bucket)
    print(f"filas panel: {len(panel)}")

    m_min = select_momentum_minimal(panel)
    m_conj = select_momentum_conjunct(panel)
    n_min = int((m_min & panel["max_fwd_14d"].notna()).sum())
    n_conj = int((m_conj & panel["max_fwd_14d"].notna()).sum())
    print(f"hits momentum-mínima: {n_min} ; conjunta: {n_conj}")

    b1_min_idx, r_min = build_b1_momentum(panel, m_min)
    b1_conj_idx, r_conj = build_b1_momentum(panel, m_conj)
    b2_idx = es.build_b2(panel)

    rows_min, rows_conj = panel[m_min], panel[m_conj]
    rows_b1_min = panel.loc[b1_min_idx]
    rows_b1_conj = panel.loc[b1_conj_idx]
    rows_b2 = panel.loc[b2_idx]

    results = {
        "meta": {
            "rule": "momentum/breakout",
            "breakout_window": BREAKOUT_WINDOW, "vol_surge_min": VOL_SURGE_MIN,
            "hits": {"momentum_minimal": n_min, "momentum_conjunct": n_conj},
            "b1_ratio_used": {"minimal": round(r_min, 3), "conjunct": round(r_conj, 3)},
            "panel_rows": int(len(panel)), "symbols": len(symbol_dfs),
            "caveats": [
                "Sesgo de supervivencia: cache solo de símbolos vivos hoy; niveles absolutos "
                "inflados para setup y baseline por igual; el DELTA sigue informativo.",
                "Retorno en USDT incluye beta de BTC.",
            ],
        },
        "tables": {
            "momentum_minimal": {
                "setup": es.stratify(rows_min, panel),
                "B1": es.stratify(rows_b1_min, panel),
                "B2": es.stratify(rows_b2, panel),
                "delta_vs_B1": es.delta_and_pval(rows_min, rows_b1_min),
            },
            "momentum_conjunct": {
                "setup": es.stratify(rows_conj, panel),
                "B1": es.stratify(rows_b1_conj, panel),
                "B2": es.stratify(rows_b2, panel),
                "delta_vs_B1": es.delta_and_pval(rows_conj, rows_b1_conj),
            },
        },
    }
    (HERE / "momentum_results.json").write_text(json.dumps(results, indent=2))
    print(f"escrito {HERE / 'momentum_results.json'}")
    write_findings(results)
    print("== done ==")
    return results


if __name__ == "__main__":
    main()
