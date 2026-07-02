"""CURAR EL AZAR: la entrada es random (aceptamos la moneda al aire, NO seleccionamos).
El edge, si existe, vive en la CURA: exit asimétrico (escalera) + circuit breaker (kill_switch_v2)
+ descorrelación (rate-limit de entradas por día). Optimizamos los knobs de la CURA en train y
validamos OOS (walk-forward). Score = Calmar (CAGR/maxDD). Sin fitear al 2022.
Panel anti-survivorship (program_ohlcv.db, hasta 2025-04-29). No toca holdout.
"""
import sys, os, hashlib, json, copy
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
import calib_study as cs
import exit_study as ex
from strategy.kill_switch_v2 import evaluate_portfolio_tier

COST = 0.02
HOR = ex.HORIZON
SPLIT = pd.Timestamp("2024-01-01", tz="UTC")
SF = {"NORMAL": 1.0, "WARNED": 1.0, "REDUCED": 0.5, "FROZEN": 0.0}
BASE_CFG = json.load(open(os.path.join(cs.REPO_ROOT, "config.defaults.json")))

symbol_dfs = {s: ex.add_ladder(cs.compute_features(df)) for s, df in cs.load_spot_daily(cs._DEFAULT_DB).items()}
panel = cs._build_panel(symbol_dfs)
pan = panel[(panel["alive"]) & (panel["symbol"] != "BTCUSDT")].dropna(subset=["ladder_return"]).copy()
pan["net"] = pan["ladder_return"] - COST
days = sorted(pd.to_datetime(panel["date"].unique()))
# universo random por día = TODAS las alts vivas (sin filtro de selección)
by_day = {d: g.sort_values("symbol") for d, g in pan.assign(d=pd.to_datetime(pan["date"])).groupby("d")}


def cfg_with_aggr(aggr):
    c = copy.deepcopy(BASE_CFG)
    c.setdefault("kill_switch", {}).setdefault("v2", {})["aggressiveness"] = aggr
    return c


PEAK_WIN = 180  # pico RODANTE (~180d): el circuit breaker RE-ARMA (no se congela para siempre)


def simulate(M, cfg, daily_cap, seed=0):
    """Entrada random always-on; escalera; kill-switch (cfg=None -> off); rate-limit=daily_cap."""
    cap = 1.0; open_pos = []; eq = []
    for i, d in enumerate(days):
        still = []
        for ei, p in open_pos:
            if ei <= i: cap += p
            else: still.append((ei, p))
        open_pos = still
        peak = max(eq[-(PEAK_WIN - 1):] + [cap])  # pico rodante -> dd re-arma
        dd = -(1.0 - cap / peak) if peak > 0 else 0.0
        sf = 1.0
        if cfg is not None:
            sf = SF[evaluate_portfolio_tier(dd, 0, cfg)["tier"]]
        free = min(M - len(open_pos), daily_cap)  # rate-limit = descorrelación temporal
        if free > 0 and sf > 0:
            g = by_day.get(d)
            if g is not None and len(g):
                off = int(hashlib.sha256(f"{d}|{seed}".encode()).hexdigest(), 16) % len(g)  # random determinista + semilla
                pick = pd.concat([g.iloc[off:], g.iloc[:off]]).head(free)
                for net in pick["net"]:
                    open_pos.append((min(i + HOR, len(days) - 1), (cap / M) * sf * float(net)))
        eq.append(cap)
    for ei, p in open_pos: cap += p
    return pd.Series(eq, index=pd.to_datetime(days))


def seg_metrics(s):
    if len(s) < 5: return (float("nan"),) * 3
    s = s / s.iloc[0]
    terminal = float(s.iloc[-1])
    maxdd = float(-(s / s.cummax() - 1.0).min())
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    cagr = terminal ** (1 / yrs) - 1 if terminal > 0 and yrs > 0 else -1.0
    calmar = cagr / maxdd if maxdd > 1e-9 else 0.0
    return terminal, maxdd, calmar


CONFIGS = []
for M in (10, 20, 40):
    for ks in ("off", 0, 50, 100):
        for cap in (M, 2, 1):  # M = sin rate-limit; 2/1 = descorrelación temporal
            cfg = None if ks == "off" else cfg_with_aggr(ks)
            CONFIGS.append((M, ks, cap, cfg))

rows = []
for M, ks, cap, cfg in CONFIGS:
    s = simulate(M, cfg, cap)
    tr = s[s.index < SPLIT]; vl = s[s.index >= SPLIT]
    t_tr, dd_tr, cal_tr = seg_metrics(tr)
    t_vl, dd_vl, cal_vl = seg_metrics(vl)
    rows.append(dict(M=M, ks=ks, cap=cap, t_tr=t_tr, dd_tr=dd_tr, cal_tr=cal_tr,
                     t_vl=t_vl, dd_vl=dd_vl, cal_vl=cal_vl))

df = pd.DataFrame(rows)
naive = df[(df.ks == "off") & (df.cap == df.M)].sort_values("M")
print("NAIVE (random always-on, SIN cura) — el punto de partida:")
for _, r in naive.iterrows():
    print(f"  M={r.M:2} cap={r.cap:2} | TRAIN {r.t_tr:.2f}x dd{r.dd_tr*100:.0f}% | VALIDATE {r.t_vl:.2f}x dd{r.dd_vl*100:.0f}% calmar{r.cal_vl:+.2f}")

print("\nTOP 8 por Calmar en TRAIN (la cura se escoge acá), con su VALIDATE (walk-forward):")
print("  M  ks   cap | TRAIN term  dd   calmar | VALIDATE term  dd   calmar")
for _, r in df.sort_values("cal_tr", ascending=False).head(8).iterrows():
    print(f"  {r.M:2} {str(r.ks):4} {r.cap:3} | {r.t_tr:6.2f}x {r.dd_tr*100:3.0f}% {r.cal_tr:+5.2f}  | {r.t_vl:6.2f}x {r.dd_vl*100:3.0f}% {r.cal_vl:+5.2f}")

best = df.sort_values("cal_tr", ascending=False).iloc[0]
print(f"\nCONFIG GANADORA EN TRAIN: M={best.M} ks={best.ks} cap={best.cap}")
print(f"  -> VALIDATE (OOS): {best.t_vl:.2f}x, maxDD {best.dd_vl*100:.0f}%, Calmar {best.cal_vl:+.2f}")

# --- robustez a la SEMILLA del azar: el top-3 de train x 5 semillas ---
print("\nROBUSTEZ A LA SEMILLA (¿es la cura o fue una rotacion afortunada?):")
top3 = df.sort_values("cal_tr", ascending=False).head(3)
for _, r in top3.iterrows():
    cfg = None if r.ks == "off" else cfg_with_aggr(r.ks)
    vals = []
    for seed in range(5):
        s = simulate(int(r.M), cfg, int(r.cap), seed=seed)
        vl = s[s.index >= SPLIT]
        t, dd, cal = seg_metrics(vl)
        vals.append((t, dd, cal))
    ts = [v[0] for v in vals]; cals = [v[2] for v in vals]
    print(f"  M={r.M:2} ks={str(r.ks):4} cap={r.cap:2} | VALIDATE 5 semillas: "
          f"term {min(ts):.2f}-{max(ts):.2f}x (med {sorted(ts)[2]:.2f}) | "
          f"Calmar {min(cals):+.2f}..{max(cals):+.2f} | {sum(c>0 for c in cals)}/5 positivas")

print("\nLECTURA: si el top-3 da >=4/5 semillas positivas en validate -> la CURA es robusta al azar,")
print("no fue suerte de una rotacion. Si se dispersa a negativo -> era una semilla afortunada.")
