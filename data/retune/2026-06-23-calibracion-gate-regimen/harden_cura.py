"""Endurecer la cura: (A) multi-fold walk-forward de la config robusta (M=20, ks-agresivo,
diversificado) en varios cortes; (B) STRESS-TEST DE BEAR OOS: aplicar la cura al 2021-2022
(incluye el bear 2022) con la config elegida SIN ver 2022 -> ¿protege de verdad vs el naive?
Ataca el unico caveat (protección de bear in-sample). Panel anti-survivorship. No toca holdout.
"""
import sys, os, hashlib, json, copy
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
import calib_study as cs
import exit_study as ex
from strategy.kill_switch_v2 import evaluate_portfolio_tier

COST = 0.02; HOR = ex.HORIZON; PEAK_WIN = 180
SF = {"NORMAL": 1.0, "WARNED": 1.0, "REDUCED": 0.5, "FROZEN": 0.0}
BASE_CFG = json.load(open(os.path.join(cs.REPO_ROOT, "config.defaults.json")))
CONFIG = dict(M=20, ks=100, cap=20)  # la robusta (5/5 semillas en el validate bull)

symbol_dfs = {s: ex.add_ladder(cs.compute_features(df)) for s, df in cs.load_spot_daily(cs._DEFAULT_DB).items()}
panel = cs._build_panel(symbol_dfs)
pan = panel[(panel["alive"]) & (panel["symbol"] != "BTCUSDT")].dropna(subset=["ladder_return"]).copy()
pan["net"] = pan["ladder_return"] - COST
days = sorted(pd.to_datetime(panel["date"].unique()))
by_day = {d: g.sort_values("symbol") for d, g in pan.assign(d=pd.to_datetime(pan["date"])).groupby("d")}


def cfg_with_aggr(aggr):
    c = copy.deepcopy(BASE_CFG)
    c.setdefault("kill_switch", {}).setdefault("v2", {})["aggressiveness"] = aggr
    return c


def simulate(M, cfg, daily_cap, seed=0):
    cap = 1.0; open_pos = []; eq = []
    for i, d in enumerate(days):
        still = []
        for ei, p in open_pos:
            if ei <= i: cap += p
            else: still.append((ei, p))
        open_pos = still
        peak = max(eq[-(PEAK_WIN - 1):] + [cap])
        dd = -(1.0 - cap / peak) if peak > 0 else 0.0
        sf = SF[evaluate_portfolio_tier(dd, 0, cfg)["tier"]] if cfg is not None else 1.0
        free = min(M - len(open_pos), daily_cap)
        if free > 0 and sf > 0:
            g = by_day.get(d)
            if g is not None and len(g):
                off = int(hashlib.sha256(f"{d}|{seed}".encode()).hexdigest(), 16) % len(g)
                for net in pd.concat([g.iloc[off:], g.iloc[:off]]).head(free)["net"]:
                    open_pos.append((min(i + HOR, len(days) - 1), (cap / M) * sf * float(net)))
        eq.append(cap)
    for ei, p in open_pos: cap += p
    return pd.Series(eq, index=pd.to_datetime(days))


def seg(s, lo=None, hi=None):
    if lo is not None: s = s[s.index >= lo]
    if hi is not None: s = s[s.index < hi]
    if len(s) < 5: return (float("nan"),) * 3
    s = s / s.iloc[0]
    term = float(s.iloc[-1]); maxdd = float(-(s / s.cummax() - 1.0).min())
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    cagr = term ** (1 / yrs) - 1 if term > 0 and yrs > 0 else -1.0
    return term, maxdd, (cagr / maxdd if maxdd > 1e-9 else 0.0)


cfg = cfg_with_aggr(CONFIG["ks"])
M, cap = CONFIG["M"], CONFIG["cap"]
runs = {sd: simulate(M, cfg, cap, seed=sd) for sd in range(5)}
naive_runs = {sd: simulate(M, None, cap, seed=sd) for sd in range(5)}

print(f"Config robusta: M={M}, kill-switch agresivo (re-armante), diversificado. 5 semillas.\n")

print("(A) MULTI-FOLD walk-forward (validate = despues del corte):")
for split in ("2022-07-01", "2023-01-01", "2023-07-01", "2024-01-01", "2024-07-01"):
    lo = pd.Timestamp(split, tz="UTC")
    ms = [seg(runs[sd], lo=lo) for sd in range(5)]
    terms = sorted(m[0] for m in ms); cals = [m[2] for m in ms]
    print(f"  corte {split} | validate term {terms[0]:.2f}-{terms[-1]:.2f}x (med {terms[2]:.2f}) "
          f"| Calmar med {sorted(cals)[2]:+.2f} | {sum(c > 0 for c in cals)}/5 positivas")

print("\n(B) STRESS-TEST DE BEAR OOS (cura aplicada al 2021-2022, config NO elegida en 2022):")
hi = pd.Timestamp("2023-01-01", tz="UTC")
cure_b = [seg(runs[sd], hi=hi) for sd in range(5)]
naive_b = [seg(naive_runs[sd], hi=hi) for sd in range(5)]
ct = sorted(m[0] for m in cure_b); cdd = sorted(m[1] for m in cure_b)
nt = sorted(m[0] for m in naive_b); ndd = sorted(m[1] for m in naive_b)
print(f"  CURADO 2021-22 | term med {ct[2]:.2f}x | maxDD med {cdd[2]*100:.0f}%")
print(f"  NAIVE  2021-22 | term med {nt[2]:.2f}x | maxDD med {ndd[2]*100:.0f}%")
print("\nLECTURA: (A) si >=4/5 folds positivos -> robusto en el tiempo. (B) si el CURADO tiene MUCHO")
print("menos maxDD que el naive en 2021-22 (el bear) -> la protección de bear SÍ generaliza OOS,")
print("cerrando el ultimo caveat. Si el curado tambien se hunde -> la protección era in-sample.")
