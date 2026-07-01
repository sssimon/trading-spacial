"""Confirmación: ¿el -12% realizado del bucket 'alts' es robusto o ruido de 468 obs?
Reusa calib_study. Desglosa rule_return + max_fwd por (estado, año) + concentración
de fechas. 2 votantes (dominancia vacía), umbrales de producción. No re-corre el grid.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import calib_study as cs

panel = cs._build_panel({s: cs.compute_features(df) for s, df in cs.load_spot_daily(cs._DEFAULT_DB).items()})
btc_dom = cs.load_btc_dominance(cs._DEFAULT_BTC_DOM_CSV)   # vacío → 2 votantes
reg = cs.regime_by_date(panel, btc_dom, thresholds=None)
rows = panel[cs.select_rule_minimal(panel)].merge(reg.rename("regime"), left_on="date", right_index=True, how="left")
rows = rows.dropna(subset=["max_fwd_14d"])
rows["year"] = rows["date"].dt.year

print("estado | año  |    n | n_fechas | rule_return med | max_fwd_14d med")
for (est, yr), g in rows.groupby(["regime", "year"]):
    rr = g["rule_return"].dropna()
    print(f"{est:6} | {yr} | {len(g):5} | {g['date'].nunique():8} | "
          f"{(rr.median()*100 if len(rr) else float('nan')):14.1f}% | {g['max_fwd_14d'].median()*100:13.1f}%")
print("--- bucket 'alts' total ---")
a = rows[rows["regime"] == "alts"]
print(f"n={len(a)}  n_fechas={a['date'].nunique()}  rango fechas: {a['date'].min().date()} → {a['date'].max().date()}")
