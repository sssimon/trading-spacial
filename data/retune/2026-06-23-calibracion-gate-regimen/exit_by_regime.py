"""Hipótesis de Samuel: ¿la escalera hace plata SI filtramos a alt-season?
Corta el realizado (stop vs escalera) por estado de régimen (alts/mixto/btc),
para candidatas y B2. Reusa exit_study + calib_study. Régimen 2-votantes (sin dominancia).
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import pandas as pd
import calib_study as cs
import exit_study as ex

def med(s):
    v = pd.Series(s).dropna()
    return float(v.median()) if len(v) else float("nan")

symbol_dfs = {s: ex.add_ladder(cs.compute_features(df)) for s, df in cs.load_spot_daily(cs._DEFAULT_DB).items()}
panel = cs._build_panel(symbol_dfs)
reg = cs.regime_by_date(panel, cs.load_btc_dominance(cs._DEFAULT_BTC_DOM_CSV), thresholds=None)
panel = panel.merge(reg.rename("regime"), left_on="date", right_index=True, how="left")
panel["year"] = panel["date"].dt.year

cand = panel[cs.select_rule_minimal(panel)]
b2 = panel[panel["alive"]]

def tabla(rows, nombre):
    print(f"\n=== {nombre} por régimen (realizado, mediana) ===")
    print("régimen | n      | stop    | ESCALERA")
    for est, g in rows.groupby("regime"):
        print(f"{est:6}  | {int(g['ladder_return'].notna().sum()):6} | {med(g['rule_return'])*100:6.1f}% | {med(g['ladder_return'])*100:6.1f}%")

tabla(cand, "CANDIDATAS")
tabla(b2, "B2 (cualquier alt viva)")

# alt-season + año: ¿el +escalera de 'alts' es de un año (2021) o robusto?
print("\n=== CANDIDATAS en régimen 'alts', por año (escalera) ===")
alts = cand[cand["regime"] == "alts"]
for yr, g in alts.groupby("year"):
    print(f"  {yr}: n={int(g['ladder_return'].notna().sum()):5}  escalera={med(g['ladder_return'])*100:6.1f}%")
