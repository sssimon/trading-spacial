"""ADVERSARIAL: ¿el edge del filtro momentum es all-weather o solo 2023+?
Mide net per-trade ret del filtro vs baseline en buckets de año DISJUNTOS
(no ventanas train/val solapadas). Mismo panel que filter_search.py.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
import calib_study as cs
import exit_study as ex

COST = 0.02
QUERY = "vol_ratio > 2 and rsi14 > 55"

symbol_dfs = {s: ex.add_ladder(cs.compute_features(df)) for s, df in cs.load_spot_daily(cs._DEFAULT_DB).items()}
panel = cs._build_panel(symbol_dfs)
btc = symbol_dfs["BTCUSDT"]
bull = (btc["close"] > btc["close"].rolling(200, min_periods=200).mean())

pan = panel[(panel["alive"]) & (panel["symbol"] != "BTCUSDT")].dropna(subset=["ladder_return"]).copy()
pan["net"] = pan["ladder_return"] - COST
pan["d"] = pd.to_datetime(pan["date"])
pan["year"] = pan["d"].dt.year
pan["btc_bull"] = pan["d"].map(lambda x: bool(bull.get(x)) if pd.notna(bull.get(x)) else False)
pan["hit"] = pan.eval(QUERY)

print(f"FILTRO: {QUERY}   cost={COST}\n")
print(f"{'periodo':16} | {'base n':>7} {'base ret':>9} | {'filt n':>7} {'filt ret':>9} | {'delta':>7} | edge?")
print("-" * 80)

def row(label, sub):
    b = sub["net"].mean() * 100
    f = sub[sub["hit"]]
    fn = len(f)
    fr = f["net"].mean() * 100 if fn else float("nan")
    delta = fr - b
    edge = "SI" if (fn >= 200 and fr > b and fr > 0) else ("n<200" if fn < 200 else "no")
    print(f"{label:16} | {len(sub):7} {b:+8.2f}% | {fn:7} {fr:+8.2f}% | {delta:+6.2f}pp | {edge}")

for y in sorted(pan["year"].unique()):
    row(str(y), pan[pan["year"] == y])

print("-" * 80)
# Corte grueso: 2021-2022 (ciclo previo: bull->bear) vs 2023-2025 (recuperacion)
row("2021-2022", pan[pan["year"] <= 2022])
row("2023-2025", pan[pan["year"] >= 2023])
print("-" * 80)
# Condicional a regimen btc (200d SMA) DENTRO de 2021-2022 y 2023-2025
for lab, mask in (("21-22 bull", (pan["year"]<=2022)&pan["btc_bull"]),
                  ("21-22 bear", (pan["year"]<=2022)&~pan["btc_bull"]),
                  ("23-25 bull", (pan["year"]>=2023)&pan["btc_bull"]),
                  ("23-25 bear", (pan["year"]>=2023)&~pan["btc_bull"])):
    row(lab, pan[mask])
