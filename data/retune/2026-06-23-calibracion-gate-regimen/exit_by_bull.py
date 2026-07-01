"""Idea de Samuel: "beta de bull solo" = market timing macro.
Bull proxy point-in-time sin lookahead: BTC close > SMA200. ¿Las entradas en
días de BTC-bull, con la escalera, clarean cero? Primer filtro (no resuelve
whipsaw/lag/costos del timing — eso pide backtest continuo). Reusa exit_study.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import pandas as pd
import calib_study as cs
import exit_study as ex

def med(s):
    v = pd.Series(s).dropna()
    return float(v.median()) if len(v) else float("nan")

symbol_dfs = {s: ex.add_ladder(cs.compute_features(df)) for s, df in cs.load_spot_daily(cs._DEFAULT_DB).items()}

# Bull macro = BTC close > SMA200 (point-in-time, SMA usa pasado)
btc = symbol_dfs["BTCUSDT"].copy()
btc_bull = (btc["close"] > btc["close"].rolling(200, min_periods=200).mean()).rename("btc_bull")

panel = cs._build_panel(symbol_dfs).merge(btc_bull, left_on="date", right_index=True, how="left")
panel["year"] = panel["date"].dt.year

cand = panel[cs.select_rule_minimal(panel)]
b2 = panel[panel["alive"]]

def tabla(rows, nombre):
    print(f"\n=== {nombre}: realizado por estado macro-BTC (mediana) ===")
    print("macro   | n      | stop    | ESCALERA")
    for bull, g in rows.groupby("btc_bull"):
        etq = "BULL (BTC>SMA200)" if bull else "bear (BTC<SMA200)"
        print(f"{etq:18} | {int(g['ladder_return'].notna().sum()):6} | {med(g['rule_return'])*100:6.1f}% | {med(g['ladder_return'])*100:6.1f}%")

tabla(cand, "CANDIDATAS")
tabla(b2, "B2 (cualquier alt viva)")

print("\n=== B2 en BTC-BULL, por año (escalera) ===")
bull_b2 = b2[b2["btc_bull"] == True]  # noqa: E712
for yr, g in bull_b2.groupby("year"):
    print(f"  {yr}: n={int(g['ladder_return'].notna().sum()):6}  escalera={med(g['ladder_return'])*100:6.1f}%  stop={med(g['rule_return'])*100:6.1f}%")
