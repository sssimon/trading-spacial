"""Motor de falsación de filtros: ¿existe un filtro sobre features de ENTRADA que dé edge
en la escalera y SOBREVIVA out-of-sample? Split train / validate configurable.
Sin look-ahead (umbrales de quantiles se sacan SOLO de train). Panel anti-survivorship.
No toca holdout (hasta 2025-04-29).

Uso:
  python filter_search.py                                   # barrido single-feature (split 2024-01-01)
  python filter_search.py "vol_ratio > 2 and rsi14 > 55"    # prueba UN filtro (pandas query)
  python filter_search.py --split=2023-07-01 "<query>"      # walk-forward: mueve el corte train/val
  python filter_search.py --only=bear "<query>"             # restringe a dias btc_bull==False (o =bull)
  python filter_search.py --cost=0.05 "<query>"             # costo round-trip distinto (slippage)
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
import calib_study as cs
import exit_study as ex

# --- parse flags ---
args = sys.argv[1:]
SPLIT = pd.Timestamp("2024-01-01", tz="UTC")
COST = 0.02
ONLY = None  # 'bull' | 'bear' | None
query = None
for a in args:
    if a.startswith("--split="):
        SPLIT = pd.Timestamp(a.split("=", 1)[1], tz="UTC")
    elif a.startswith("--cost="):
        COST = float(a.split("=", 1)[1])
    elif a.startswith("--only="):
        ONLY = a.split("=", 1)[1]
    else:
        query = a

symbol_dfs = {s: ex.add_ladder(cs.compute_features(df)) for s, df in cs.load_spot_daily(cs._DEFAULT_DB).items()}
panel = cs._build_panel(symbol_dfs)
btc = symbol_dfs["BTCUSDT"]
bull = (btc["close"] > btc["close"].rolling(200, min_periods=200).mean())
btc_dom = cs.load_btc_dominance(cs._DEFAULT_BTC_DOM_CSV)
reg = cs.regime_by_date(panel, btc_dom)

pan = panel[(panel["alive"]) & (panel["symbol"] != "BTCUSDT")].dropna(subset=["ladder_return"]).copy()
pan["net"] = pan["ladder_return"] - COST
pan["d"] = pd.to_datetime(pan["date"])
pan["regime"] = pan["date"].map(reg)
pan["btc_bull"] = pan["d"].map(lambda x: bool(bull.get(x)) if pd.notna(bull.get(x)) else False)
if ONLY == "bear":
    pan = pan[~pan["btc_bull"]]
elif ONLY == "bull":
    pan = pan[pan["btc_bull"]]

train = pan[pan["d"] < SPLIT]
val = pan[pan["d"] >= SPLIT]
BASE_TR = train["net"].mean() * 100
BASE_VAL = val["net"].mean() * 100
MIN_N = 200


def report(name, m_tr, m_val):
    ntr, nval = int(m_tr.sum()), int(m_val.sum())
    if ntr < MIN_N or nval < MIN_N:
        return None
    tr = train[m_tr]["net"].mean() * 100
    vl = val[m_val]["net"].mean() * 100
    survives = (tr > BASE_TR) and (vl > BASE_VAL) and (vl > 0)
    print(f"  {name:44} | train n={ntr:6} ret={tr:+6.2f}% | val n={nval:6} ret={vl:+6.2f}% | {'SOBREVIVE' if survives else ''}")
    return (name, tr, vl, survives)


tag = f"split={SPLIT.date()} cost={COST} only={ONLY}"
print(f"BASELINE (sin filtro): train ret={BASE_TR:+.2f}%  |  validate ret={BASE_VAL:+.2f}%   [{tag}]")
print(f"(escalera neta por trade; train={len(train)} val={len(val)} trades)\n")

if query:
    print(f"FILTRO: {query}")
    report(query, train.eval(query), val.eval(query))
    sys.exit(0)

# --- barrido amplio single-feature ---
FEATURES = ["pos_in_30d_range", "rsi14", "pct_vs_sma20", "pct_vs_sma50",
            "consol_30d", "vol_ratio", "ret_30d", "flat_frac_90d"]
results = []
tested = 0
print("BARRIDO single-feature (umbral = quantile de TRAIN):")
for f in FEATURES:
    for qv in (0.10, 0.25, 0.50, 0.75, 0.90):
        thr = train[f].quantile(qv)
        for op, symop in ((np.less, "<"), (np.greater, ">")):
            tested += 1
            r = report(f"{f} {symop} {thr:.4g} (q{int(qv*100)})", op(train[f], thr), op(val[f], thr))
            if r:
                results.append(r)
for est in ("alts", "mixto", "btc"):
    tested += 1
    r = report(f"regime == {est}", train["regime"] == est, val["regime"] == est)
    if r: results.append(r)
for bv in (True, False):
    tested += 1
    r = report(f"btc_bull == {bv}", train["btc_bull"] == bv, val["btc_bull"] == bv)
    if r: results.append(r)

surv = [r for r in results if r[3]]
print(f"\nRESUMEN: {tested} filtros probados, {len(results)} con n suficiente, {len(surv)} SOBREVIVEN train+validate.")
if surv:
    print("Sobrevivientes (edge en train Y en validate):")
    for name, tr, vl, _ in sorted(surv, key=lambda r: -r[2]):
        print(f"  {name:44} | train {tr:+.2f}% | val {vl:+.2f}%")
print(f"\nCAVEAT: con ~{tested} filtros probados, algunos SOBREVIVEN por azar (comparaciones multiples).")
print("Trades intra-ventana autocorrelacionados -> n efectivo << n. Sobreviviente = CANDIDATO a verificar.")
