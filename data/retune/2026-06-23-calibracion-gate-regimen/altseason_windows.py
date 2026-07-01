"""¿Cuándo fue "2019-like" (alt-season) en 2021-2025, y habría ayudado operar SOLO ahí?
Usa el detector de régimen real (regime_by_date -> compose_regime, 3 componentes) sobre el
panel anti-survivorship. Lista las ventanas alt-season + mide la escalera DENTRO vs FUERA.
No toca holdout (hasta 2025-04-29).
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
import calib_study as cs
import exit_study as ex

symbol_dfs = {s: ex.add_ladder(cs.compute_features(df)) for s, df in cs.load_spot_daily(cs._DEFAULT_DB).items()}
panel = cs._build_panel(symbol_dfs)
btc_dom = cs.load_btc_dominance(cs._DEFAULT_BTC_DOM_CSV)
reg = cs.regime_by_date(panel, btc_dom).sort_index()  # estado por fecha: alts/mixto/btc

# --- ventanas alt-season (coalescer huecos <=14d para legibilidad) ---
dates = pd.to_datetime(reg.index)
is_alt = (reg.values == "alts")
windows = []
start = None
last = None
for d, a in zip(dates, is_alt):
    if a:
        if start is None:
            start = d
        elif (d - last).days > 14:      # hueco grande -> cierra ventana previa
            windows.append((start, last))
            start = d
        last = d
if start is not None:
    windows.append((start, last))

n = len(reg)
n_alt = int(is_alt.sum())
print(f"Panel: {dates.min().date()} -> {dates.max().date()}  ({n} dias)")
print(f"Alt-season (estado='alts'): {n_alt} dias = {n_alt/n*100:.0f}% del tiempo\n")
print("Ventanas 2019-like (alt-season), coalescidas:")
for a, b in windows:
    dd = (b - a).days + 1
    print(f"  {a.date()} -> {b.date()}  ({dd:4} dias)")

# --- ¿habria ayudado? escalera DENTRO vs FUERA de alt-season ---
COST = 0.02
pan = panel[(panel["alive"]) & (panel["symbol"] != "BTCUSDT")].dropna(subset=["ladder_return"]).copy()
pan["net"] = pan["ladder_return"] - COST
pan["estado"] = pan["date"].map(reg)
print("\n¿Habria ayudado operar SOLO en alt-season? (escalera neta por trade, por regimen)")
for est in ["alts", "mixto", "btc"]:
    sub = pan[pan["estado"] == est]["net"]
    if len(sub):
        print(f"  {est:6} | n={len(sub):6} | media={sub.mean()*100:+6.2f}% | mediana={sub.median()*100:+6.2f}% | %ganan={ (sub>0).mean()*100:4.1f}%")

print("\nLECTURA: si 'alts' NO le gana a 'btc' -> operar solo en 2019-like te mete en tus PEORES")
print("periodos, no en los mejores. Confirma o refuta la inversion de la calibracion.")
