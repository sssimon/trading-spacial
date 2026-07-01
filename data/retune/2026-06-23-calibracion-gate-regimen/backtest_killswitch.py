"""Backtest contrarian + el KILL-SWITCH v2 REAL del repo (strategy.kill_switch_v2).
Usa evaluate_portfolio_tier (tiers por drawdown de portafolio: NORMAL/WARNED/REDUCED/FROZEN)
con los umbrales configurados en config.defaults.json — NO inventados por mí. size_factor
aplicado a nuevas entradas: REDUCED=0.5, FROZEN=0 (no entra). Mide: ¿el circuit breaker real
baja el drawdown sin matar la cola de recuperación de 2023? Reusa calib_study + exit_study.
No toca holdout (hasta 2025-04-29).
"""
import sys, os, hashlib, json
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
import calib_study as cs
import exit_study as ex
sys.path.insert(0, str(cs.REPO_ROOT))
from strategy.kill_switch_v2 import evaluate_portfolio_tier, get_portfolio_thresholds

CFG = json.load(open(os.path.join(cs.REPO_ROOT, "config.defaults.json")))
TH = get_portfolio_thresholds(CFG)
SF = {"NORMAL": 1.0, "WARNED": 1.0, "REDUCED": 0.5, "FROZEN": 0.0}
COST = 0.02
HOR = ex.HORIZON

symbol_dfs = {s: ex.add_ladder(cs.compute_features(df)) for s, df in cs.load_spot_daily(cs._DEFAULT_DB).items()}
btc = symbol_dfs["BTCUSDT"]
bull_by_date = (btc["close"] > btc["close"].rolling(200, min_periods=200).mean())
panel = cs._build_panel(symbol_dfs).merge(bull_by_date.rename("btc_bull"), left_on="date", right_index=True, how="left")
panel = panel[(panel["alive"]) & (panel["symbol"] != "BTCUSDT")].dropna(subset=["ladder_return"]).copy()
panel["net"] = panel["ladder_return"] - COST
days = sorted(panel["date"].unique())
by_day = {d: g.sort_values("symbol") for d, g in panel.groupby("date")}


def simulate(M, use_ks):
    cap = 1.0
    peak = 1.0
    open_pos = []
    eq = []
    frozen_days = 0
    reduced_days = 0
    for i, d in enumerate(days):
        still = []
        for ei, p in open_pos:
            if ei <= i:
                cap += p
            else:
                still.append((ei, p))
        open_pos = still
        peak = max(peak, cap)
        dd = -(1.0 - cap / peak) if peak > 0 else 0.0  # NEGATIVO en drawdown (convención del módulo)
        sf = 1.0
        if use_ks:
            tier = evaluate_portfolio_tier(dd, 0, CFG)["tier"]
            sf = SF[tier]
            frozen_days += 1 if tier == "FROZEN" else 0
            reduced_days += 1 if tier == "REDUCED" else 0
        free = M - len(open_pos)
        is_bear = (bull_by_date.get(d) == False)  # noqa: E712
        if free > 0 and is_bear and sf > 0:
            g = by_day.get(d)
            if g is not None and len(g):
                off = int(hashlib.sha256(str(d).encode()).hexdigest(), 16) % len(g)
                pick = pd.concat([g.iloc[off:], g.iloc[:off]]).head(free)
                for net in pick["net"]:
                    open_pos.append((min(i + HOR, len(days) - 1), (cap / M) * sf * float(net)))
        eq.append(cap)
    for ei, p in open_pos:
        cap += p
    return pd.Series(eq, index=pd.to_datetime(days)), frozen_days, reduced_days


def metrics(s):
    terminal = float(s.iloc[-1])
    dd = s / s.cummax() - 1.0
    d22 = dd[(s.index >= "2022-01-01") & (s.index < "2023-01-01")]
    return terminal, float(-dd.min()), (float(-d22.min()) if len(d22) else float("nan"))


print(f"Kill-switch v2 REAL (config.defaults.json): REDUCED a {TH['reduced_dd']*100:.1f}% DD, "
      f"FROZEN a {TH['frozen_dd']*100:.1f}% DD  (umbrales del repo, no inventados)\n")
print("M  | SIN kill-switch                | CON kill-switch v2             | %tiempo frozen/reduced")
print("   | terminal maxDD  maxDD22         | terminal maxDD  maxDD22         |")
for M in (5, 10, 20):
    s0, _, _ = simulate(M, False)
    s1, fz, rd = simulate(M, True)
    t0, md0, d0 = metrics(s0)
    t1, md1, d1 = metrics(s1)
    n = len(days)
    print(f"{M:2} | {t0:6.2f}x {md0*100:5.1f}% {d0*100:5.1f}%          | {t1:6.2f}x {md1*100:5.1f}% {d1*100:5.1f}%          | {fz/n*100:.0f}% frz / {rd/n*100:.0f}% red")

print("\nLECTURA: el kill-switch baja el DD (bien) — pero mira el terminal. Si se aplana/baja,")
print("te cortó de la cola de 2023. Umbrales del repo, no tuneados a 2022.")
