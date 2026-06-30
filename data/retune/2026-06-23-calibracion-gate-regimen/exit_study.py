"""¿El exit de musikito (escalera+runner) tiene edge point-in-time?
Reusa calib_study (panel anti-survivorship + features + stop=rule_return).
Escalera CONGELADA verbatim de confirm_study.py. Spec 2026-06-30-exit-edge-study.

Compara realizado de {candidatas, B2} × {stop mecánico, escalera}. Criterio:
escalera positiva + le gana al stop ≥+5pp (p<0.01) + robusto por año. Decompone
selección-vs-exit (candidatas vs B2, ambas con escalera).
No toca holdout (período hasta 2025-04-29); sin open_holdout/simulate_strategy.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
import calib_study as cs

# ── Escalera real (CONGELADA, verbatim de confirm_study.py) ──
TPS = [0.15, 0.30, 0.50, 0.90]
FRACS = [0.25, 0.25, 0.20, 0.15]
DISASTER = -0.50
HORIZON = 30

# ── Criterio pre-comprometido ──
MARGEN_PP = 5.0
P_MAX = 0.01


def ladder_return(entry, highs, lows, close_last):
    if entry is None or entry <= 0 or close_last is None or not highs:
        return None
    hi_max = max(highs); lo_min = min(lows)
    realized = 0.0; sold = 0.0
    for tp, fr in zip(TPS, FRACS):
        if hi_max >= entry * (1 + tp):
            realized += fr * tp; sold += fr
        else:
            break
    if sold == 0.0 and lo_min <= entry * (1 + DISASTER):
        return DISASTER
    runner_frac = 1.0 - sold
    runner_ret = (close_last - entry) / entry
    return realized + runner_frac * runner_ret


def add_ladder(df):
    """Añade columna ladder_return por fila: entrada=open t+1, ventana [t+1..t+HORIZON]."""
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    lo = df["low"].to_numpy(); c = df["close"].to_numpy()
    n = len(df); out = np.full(n, np.nan)
    for pos in range(n):
        if pos + 1 >= n:
            continue
        entry = o[pos + 1]
        end = min(pos + HORIZON, n - 1)
        if end < pos + 1:
            continue
        r = ladder_return(entry, list(h[pos + 1:end + 1]), list(lo[pos + 1:end + 1]), c[end])
        if r is not None:
            out[pos] = r
    df["ladder_return"] = out
    return df


def _med(vals):
    v = pd.Series(vals).dropna()
    return float(v.median()) if len(v) else None


def run():
    symbol_dfs = {s: add_ladder(cs.compute_features(df)) for s, df in cs.load_spot_daily(cs._DEFAULT_DB).items()}
    panel = cs._build_panel(symbol_dfs)
    panel["year"] = panel["date"].dt.year
    cand = panel[cs.select_rule_minimal(panel)]
    b2 = panel[panel["alive"]]

    def block(rows):
        return {"n": int(rows["ladder_return"].notna().sum()),
                "stop_med": _med(rows["rule_return"]),
                "ladder_med": _med(rows["ladder_return"])}

    res = {"candidatas": block(cand), "B2": block(b2),
           "candidatas_por_año": {}, "decomposicion": {}}
    for yr, g in cand.groupby("year"):
        res["candidatas_por_año"][str(int(yr))] = block(g)

    # Mann-Whitney escalera vs stop (candidatas)
    mw = cs.mann_whitney(cand["ladder_return"].dropna().tolist(),
                         cand["rule_return"].dropna().tolist())
    lad = res["candidatas"]["ladder_med"]; stp = res["candidatas"]["stop_med"]
    delta_pp = (lad - stp) * 100.0 if (lad is not None and stp is not None) else None
    p = mw.get("p_value")

    # robustez por año: ¿positivo Y le gana al stop en cada año con n>=50?
    años_ok = all((b["ladder_med"] is not None and b["ladder_med"] > 0 and
                   b["stop_med"] is not None and (b["ladder_med"] - b["stop_med"]) >= MARGEN_PP / 100.0)
                  for b in res["candidatas_por_año"].values() if b["n"] >= 50)

    a = lad is not None and lad > 0
    b = delta_pp is not None and delta_pp >= MARGEN_PP and p is not None and p < P_MAX
    verdict = "PASA" if (a and b and años_ok) else "NO_PASA"

    res["decomposicion"] = {"candidatas_escalera": lad, "B2_escalera": res["B2"]["ladder_med"]}
    res["acceptance"] = {"verdict": verdict, "positivo": a, "gana_al_stop": b,
                         "robusto_por_año": años_ok, "delta_pp": delta_pp, "p_value": p}
    return res


def main():
    res = run()
    HERE = os.path.dirname(__file__)
    json.dump(res, open(os.path.join(HERE, "exit_results.json"), "w"), indent=2)
    a = res["candidatas"]; b2 = res["B2"]; ac = res["acceptance"]
    L = ["# Findings — ¿el exit de musikito tiene edge point-in-time?", "",
         f"_Panel anti-survivorship hasta 2025-04-29. Escalera {[f'+{int(t*100)}%' for t in TPS]} fracs {FRACS} + runner, piso {int(DISASTER*100)}%._", "",
         f"## Veredicto: **{ac['verdict']}**", "",
         f"- positivo: {ac['positivo']} | le gana al stop ≥+5pp: {ac['gana_al_stop']} | robusto por año: {ac['robusto_por_año']}",
         f"- delta(escalera−stop)={ac['delta_pp']:.1f}pp  p={ac['p_value']}", "",
         "## Realizado por población (mediana)", "",
         f"- **candidatas** n={a['n']}  stop={a['stop_med']*100:.1f}%  escalera={a['ladder_med']*100:.1f}%",
         f"- **B2** n={b2['n']}  stop={b2['stop_med']*100:.1f}%  escalera={b2['ladder_med']*100:.1f}%", "",
         "## Decomposición selección-vs-exit", "",
         f"- candidatas+escalera={res['decomposicion']['candidatas_escalera']*100:.1f}%  vs  B2+escalera={res['decomposicion']['B2_escalera']*100:.1f}%  → si ~iguales, el edge es el exit, no elegir.", "",
         "## Candidatas por año (escalera)", ""]
    for yr, b in sorted(res["candidatas_por_año"].items()):
        if b["ladder_med"] is not None:
            L.append(f"- {yr}: n={b['n']}  stop={b['stop_med']*100:.1f}%  escalera={b['ladder_med']*100:.1f}%")
    open(os.path.join(HERE, "exit_findings.md"), "w", encoding="utf-8").write("\n".join(L))
    print(f"veredicto: {ac['verdict']}  candidatas escalera={a['ladder_med']*100:.1f}% vs stop={a['stop_med']*100:.1f}%")


if __name__ == "__main__":
    main()
