"""ADVERSARIAL: ¿el +7.3% validate del filtro es MEDIANA real o cola gorda?
Reusa el panel de filter_search.py (net = ladder_return - cost, d = fecha).
Saca en validate (>=2024-01-01): media, MEDIANA, %win, media recortando top 1% ganadoras.
Si mediana ~0 y media colapsa al recortar top 1% -> cola gorda, no edge capturable.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
import calib_study as cs
import exit_study as ex

SPLIT = pd.Timestamp("2024-01-01", tz="UTC")
COST = float(next((a.split("=")[1] for a in sys.argv[1:] if a.startswith("--cost=")), 0.02))
QUERY = next((a for a in sys.argv[1:] if not a.startswith("--")), "vol_ratio > 2 and rsi14 > 55")

symbol_dfs = {s: ex.add_ladder(cs.compute_features(df)) for s, df in cs.load_spot_daily(cs._DEFAULT_DB).items()}
panel = cs._build_panel(symbol_dfs)
pan = panel[(panel["alive"]) & (panel["symbol"] != "BTCUSDT")].dropna(subset=["ladder_return"]).copy()
pan["net"] = pan["ladder_return"] - COST
pan["d"] = pd.to_datetime(pan["date"])
val = pan[pan["d"] >= SPLIT]


def stats(x, label):
    x = np.asarray(x, float)
    n = len(x)
    mean = x.mean() * 100
    med = np.median(x) * 100
    pwin = (x > 0).mean() * 100
    # recorta top 1% de GANADORAS (los retornos mas altos)
    k = max(1, int(np.ceil(n * 0.01)))
    thr = np.sort(x)[-k]  # umbral del top-k
    trimmed = x[x < thr]
    mean_trim = trimmed.mean() * 100 if len(trimmed) else float("nan")
    # cuanto del retorno TOTAL viene del top 1%
    total = x.sum()
    top_share = (np.sort(x)[-k:].sum() / total * 100) if total != 0 else float("nan")
    print(f"\n[{label}] n={n}")
    print(f"  media        = {mean:+6.2f}%")
    print(f"  MEDIANA      = {med:+6.2f}%")
    print(f"  %win         = {pwin:5.1f}%")
    print(f"  media s/top1%= {mean_trim:+6.2f}%   (recortadas k={k} mas altas)")
    print(f"  top1% aporta = {top_share:5.1f}% del retorno total")
    return mean, med, pwin, mean_trim, top_share


print(f"FILTRO: {QUERY}   cost={COST}  split={SPLIT.date()}")
stats(val["net"], "validate baseline (sin filtro)")
sel = val[val.eval(QUERY)]
stats(sel["net"], f"validate FILTRADO")

# sanity self-check: media recortada nunca puede superar la media completa (quita los mas altos)
_x = sel["net"].values
_m = _x.mean()
_k = max(1, int(np.ceil(len(_x) * 0.01)))
_thr = np.sort(_x)[-_k]
_mt = _x[_x < _thr].mean()
assert _mt <= _m + 1e-12, "recorte top1% deberia bajar la media"
print("\nself-check OK: media_recortada <= media")
