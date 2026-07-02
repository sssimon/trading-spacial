"""Recalibración de las 6 ventanas alt-season: escoger 2 por criterio ESTRUCTURAL
pre-comprometido (no por retorno) = las 2 con alts MÁS MACHACADAS al entrar
(menor pos_in_30d_range medio) = las más "2019-like". Luego medir su escalera.
Anti-overfit: el criterio NO mira el retorno; el n=2 se declara. No toca holdout.
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
reg = cs.regime_by_date(panel, btc_dom).sort_index()

# recomputar las 6 ventanas (coalescer huecos <=14d) — igual que altseason_windows.py
dates = pd.to_datetime(reg.index); is_alt = (reg.values == "alts")
windows = []; start = last = None
for d, a in zip(dates, is_alt):
    if a:
        if start is None: start = d
        elif (d - last).days > 14: windows.append((start, last)); start = d
        last = d
if start is not None: windows.append((start, last))

COST = 0.02
pan = panel[(panel["alive"]) & (panel["symbol"] != "BTCUSDT")].dropna(subset=["ladder_return"]).copy()
pan["net"] = pan["ladder_return"] - COST
pan["d"] = pd.to_datetime(pan["date"])


def stats(sub):
    return (len(sub), sub["net"].mean() * 100, sub["net"].median() * 100,
            (sub["net"] > 0).mean() * 100, sub["pos_in_30d_range"].mean())


print("Recalibración por-ventana (criterio de selección = pos_in_30d_range, NO el retorno):\n")
print("  ventana                    | n     | media   mediana  %win  | pos_rango (machacada<->extendida)")
rows = []
for a, b in windows:
    sub = pan[(pan["d"] >= a) & (pan["d"] <= b)]
    if not len(sub):
        continue
    n, mu, med, win, pos = stats(sub)
    rows.append((a, b, n, mu, med, win, pos))
    print(f"  {a.date()} -> {b.date()} | {n:5} | {mu:+6.2f}% {med:+6.2f}% {win:4.1f}% | {pos:.3f}")

# escoger las 2 MÁS machacadas (menor pos_rango) — criterio estructural, ciego al retorno
rows_by_pos = sorted(rows, key=lambda r: r[6])
pick2 = rows_by_pos[:2]
print(f"\n2 más 2019-like (alts más machacadas al entrar, pos_rango más bajo):")
for a, b, n, mu, med, win, pos in pick2:
    print(f"  {a.date()} -> {b.date()}  (pos_rango={pos:.3f})")

pick_dates = [(a, b) for a, b, *_ in pick2]
in_pick = pan["d"].apply(lambda d: any(a <= d <= b for a, b in pick_dates))
sub_pick = pan[in_pick]
sub_rest = pan[pan["d"].apply(lambda d: any(a <= d <= b for a, b in [(r[0], r[1]) for r in rows])) & ~in_pick]
print("\nVeredicto:")
for name, sub in [("2 escogidas (2019-like)", sub_pick), ("otras 4 ventanas", sub_rest), ("mixto (benchmark)", pan[pan["date"].map(reg) == "mixto"])]:
    if len(sub):
        n, mu, med, win, pos = stats(sub)
        print(f"  {name:26} | n={n:6} | media={mu:+6.2f}% | mediana={med:+6.2f}% | %win={win:4.1f}%")
print("\nCAVEAT: n=2 ventanas. El criterio es ciego al retorno (anti-overfit), pero 2 ventanas")
print("no prueban un edge durable — a lo sumo dicen si 'machacada temprano' > 'extendida tarde'.")
