"""ADVERSARIAL: ¿el +7% del filtro momentum es ALFA (selección) o BETA (timing del período)?

Test central: en validate (>=2024-01-01) compara la media NET del filtro momentum contra
DOS baselines:
  (b) baseline global: media net de TODAS las alts vivas en el mismo tramo (todos los días).
  (c) day-matched: en los MISMOS días que dispara el filtro, ¿qué rinde una alt cualquiera?
      - determinista: media net de todas las alts vivas en esos días (ponderada por # trades/día).
      - Monte-Carlo: muestrea alts random esos días (mismo conteo por día) → CI.
      - "the rest": random SOLO entre las alts vivas que NO pasan el filtro ese día.

Si momentum >> day-matched  → es SELECCIÓN (alfa: elige qué alt).
Si momentum ≈ day-matched   → es TIMING (beta: los días del filtro son buenos para cualquier alt).
No toca holdout. Panel hasta 2025-04-29.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
import calib_study as cs
import exit_study as ex

COST = 0.02
SPLIT = pd.Timestamp("2024-01-01", tz="UTC")
QUERY = "vol_ratio > 2 and rsi14 > 55"
SEEDS = 500

symbol_dfs = {s: ex.add_ladder(cs.compute_features(df)) for s, df in cs.load_spot_daily(cs._DEFAULT_DB).items()}
panel = cs._build_panel(symbol_dfs)
btc = symbol_dfs["BTCUSDT"]
bull = (btc["close"] > btc["close"].rolling(200, min_periods=200).mean())

pan = panel[(panel["alive"]) & (panel["symbol"] != "BTCUSDT")].dropna(subset=["ladder_return"]).copy()
pan["net"] = pan["ladder_return"] - COST
pan["d"] = pd.to_datetime(pan["date"])
pan["btc_bull"] = pan["d"].map(lambda x: bool(bull.get(x)) if pd.notna(bull.get(x)) else False)

val = pan[pan["d"] >= SPLIT].copy()
fmask = val.eval(QUERY)

mom = val[fmask]
base_global = val["net"].mean() * 100
mom_ret = mom["net"].mean() * 100

# firing days y conteo de trades por día
fire_counts = mom.groupby("d").size()
firing_days = fire_counts.index

# universo vivo por día (todas las alts vivas ese día), indexado por día
val_by_day = {d: g for d, g in val.groupby("d")}

# --- (c) determinista: media net de TODAS las alts vivas en días de disparo, ponderada por #trades/día ---
num = 0.0; den = 0
day_all_mean = {}
day_rest_mean = {}
for d, k in fire_counts.items():
    g = val_by_day[d]
    all_mean = g["net"].mean()
    day_all_mean[d] = all_mean
    num += k * all_mean; den += k
    # "the rest": alts vivas que NO pasan el filtro ese día
    rest = g[~g.eval(QUERY)]
    day_rest_mean[d] = rest["net"].mean() if len(rest) else np.nan
random_det_all = num / den * 100

num_r = 0.0; den_r = 0
for d, k in fire_counts.items():
    rm = day_rest_mean[d]
    if pd.notna(rm):
        num_r += k * rm; den_r += k
random_det_rest = num_r / den_r * 100

# --- (c-MC) Monte-Carlo: muestrea random alts esos días, mismo conteo por día ---
rng = np.random.default_rng(42)
day_nets_all = {d: val_by_day[d]["net"].to_numpy() for d in firing_days}
day_nets_rest = {d: val_by_day[d][~val_by_day[d].eval(QUERY)]["net"].to_numpy() for d in firing_days}
mc_all = np.empty(SEEDS); mc_rest = np.empty(SEEDS)
for s in range(SEEDS):
    tot_all = 0.0; tot_rest = 0.0; n_all = 0; n_rest = 0
    for d, k in fire_counts.items():
        arr = day_nets_all[d]
        pick = rng.choice(arr, size=int(k), replace=True)
        tot_all += pick.sum(); n_all += k
        rarr = day_nets_rest[d]
        if len(rarr):
            pickr = rng.choice(rarr, size=int(k), replace=True)
            tot_rest += pickr.sum(); n_rest += k
    mc_all[s] = tot_all / n_all * 100
    mc_rest[s] = tot_rest / n_rest * 100 if n_rest else np.nan

# --- por régimen bull/bear dentro de validate ---
def split_bull(rows):
    b = rows[rows["btc_bull"]]; be = rows[~rows["btc_bull"]]
    return (b["net"].mean()*100 if len(b) else float('nan'), len(b),
            be["net"].mean()*100 if len(be) else float('nan'), len(be))

mom_bull, nmb, mom_bear, nmbe = split_bull(mom)
base_bull, nbb, base_bear, nbbe = split_bull(val)

# ¿los días de disparo son bull-heavy?
mom_bull_frac = mom["btc_bull"].mean()
val_bull_frac = val["btc_bull"].mean()

# número de días de disparo distintos y su concentración
print("="*78)
print(f"ALFA-vs-BETA  |  filtro: {QUERY}  |  validate >= {SPLIT.date()}  |  cost={COST}")
print("="*78)
print(f"\n(a) MOMENTUM filtro         : ret={mom_ret:+.2f}%   n={len(mom)}   días distintos={len(firing_days)}")
print(f"(b) BASELINE global (todo)  : ret={base_global:+.2f}%   n={len(val)}")
print(f"(c) DAY-MATCHED (mismos días que dispara el filtro):")
print(f"    determinista, todas las alts vivas   : ret={random_det_all:+.2f}%")
print(f"    determinista, SOLO 'the rest' (no-pass): ret={random_det_rest:+.2f}%")
print(f"    MC random alts   media={np.nanmean(mc_all):+.2f}%  p5={np.nanpercentile(mc_all,5):+.2f}%  p95={np.nanpercentile(mc_all,95):+.2f}%")
print(f"    MC random 'rest' media={np.nanmean(mc_rest):+.2f}%  p5={np.nanpercentile(mc_rest,5):+.2f}%  p95={np.nanpercentile(mc_rest,95):+.2f}%")
print(f"\nGAPS (momentum menos control):")
print(f"    vs baseline global : {mom_ret-base_global:+.2f}pp")
print(f"    vs day-matched all : {mom_ret-random_det_all:+.2f}pp")
print(f"    vs day-matched rest: {mom_ret-random_det_rest:+.2f}pp")
# p-valor empírico: fracción de seeds MC 'rest' que igualan/superan momentum
p_emp = float(np.mean(mc_rest >= mom_ret)) if not np.all(np.isnan(mc_rest)) else float('nan')
print(f"    p empírico (MC 'rest' >= momentum): {p_emp:.4f}")
print(f"\nBULL/BEAR dentro de validate:")
print(f"    momentum  bull ret={mom_bull:+.2f}% (n={nmb})   bear ret={mom_bear:+.2f}% (n={nmbe})")
print(f"    baseline  bull ret={base_bull:+.2f}% (n={nbb})   bear ret={base_bear:+.2f}% (n={nbbe})")
print(f"    frac días bull  momentum={mom_bull_frac:.2f}  baseline={val_bull_frac:.2f}")

# assert de sanidad: el filtro debe reproducir el +7% conocido
assert 6.5 < mom_ret < 8.0, f"momentum ret fuera de rango esperado: {mom_ret}"
print("\n[check] momentum ret reproduce ~+7.3% conocido: OK")
