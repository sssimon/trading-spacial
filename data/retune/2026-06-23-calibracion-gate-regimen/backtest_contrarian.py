"""Backtest de equity-curve de la jugada contrarian (buy alt viva en BTC-bear + escalera).
Event-driven diario, M slots equal-weight, costo 2%. Mide la SECUENCIA (¿sobrevive 2022?).
Barra pre-comprometida (spec 2026-07-01): terminal>1 + maxDD<50% + le gana a buy-hold.
Benchmark = misma mecánica pero entrando TODOS los días (always-in) → aísla el valor del timing.
Reusa calib_study + exit_study. No toca holdout (hasta 2025-04-29).
"""
import sys, os, hashlib
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
import calib_study as cs
import exit_study as ex

COST = 0.02
HOR = ex.HORIZON  # 30

symbol_dfs = {s: ex.add_ladder(cs.compute_features(df)) for s, df in cs.load_spot_daily(cs._DEFAULT_DB).items()}
btc = symbol_dfs["BTCUSDT"]
bull_by_date = (btc["close"] > btc["close"].rolling(200, min_periods=200).mean())

panel = cs._build_panel(symbol_dfs).merge(bull_by_date.rename("btc_bull"), left_on="date", right_index=True, how="left")
panel = panel[(panel["alive"]) & (panel["symbol"] != "BTCUSDT")].dropna(subset=["ladder_return"]).copy()
panel["net"] = panel["ladder_return"] - COST

days = sorted(panel["date"].unique())
by_day = {d: g.sort_values("symbol") for d, g in panel.groupby("date")}


def simulate(M, bear_only):
    cap = 1.0
    open_pos = []  # (exit_i, pnl)
    eq = []
    for i, d in enumerate(days):
        # realizar salidas: posiciones cuyo exit_i llegó
        still = []
        for ei, p in open_pos:
            if ei <= i:
                cap += p
            else:
                still.append((ei, p))
        open_pos = still
        free = M - len(open_pos)
        is_bear = (bull_by_date.get(d) == False)  # noqa: E712  (NaN -> no bear)
        if free > 0 and (not bear_only or is_bear):
            g = by_day.get(d)
            if g is not None and len(g):
                off = int(hashlib.sha256(str(d).encode()).hexdigest(), 16) % len(g)
                pick = pd.concat([g.iloc[off:], g.iloc[:off]]).head(free)
                for net in pick["net"]:
                    size = cap / M
                    open_pos.append((min(i + HOR, len(days) - 1), size * float(net)))
        eq.append(cap)
    for ei, p in open_pos:
        cap += p
    s = pd.Series(eq, index=pd.to_datetime(days))
    return s


def metrics(s):
    terminal = float(s.iloc[-1])
    peak = s.cummax()
    dd = (s / peak - 1.0)
    maxdd = float(-dd.min())
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    cagr = terminal ** (1 / yrs) - 1 if terminal > 0 and yrs > 0 else float("nan")
    dd2022 = dd[(s.index >= "2022-01-01") & (s.index < "2023-01-01")]
    dd2022_max = float(-dd2022.min()) if len(dd2022) else float("nan")
    return terminal, maxdd, cagr, dd2022_max


print("M  | ESTRATEGIA (bear-only)                  | BENCHMARK (always-in)  | veredicto")
print("   | terminal  maxDD   CAGR   maxDD2022       | terminal               |")
votos = 0
for M in (5, 10, 20):
    st = simulate(M, bear_only=True)
    bm = simulate(M, bear_only=False)
    t, md, cg, dd22 = metrics(st)
    bt = float(bm.iloc[-1])
    passa = (t > 1.0) and (md < 0.50) and (t > bt)
    votos += 1 if passa else 0
    print(f"{M:2} | {t:7.2f}x  {md*100:5.1f}% {cg*100:6.1f}% {dd22*100:6.1f}%       | {bt:7.2f}x                | {'PASA' if passa else 'NO'}")

print(f"\nVEREDICTO (barra: terminal>1 + maxDD<50% + le gana al benchmark, en >=2 de 3 M): "
      f"{'PASA -> automatizar señal' if votos >= 2 else 'NO PASA -> Valles queda instrumento de contexto'}  ({votos}/3)")
print("\nCAVEAT: n=1 bear (2022 in-sample); esto responde '¿sobrevive el 2022 conocido?', no 'bears en general'.")
