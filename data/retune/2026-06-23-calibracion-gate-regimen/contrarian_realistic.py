"""Test decisivo de la jugada contrarian (B2 en BTC-bear + escalera):
¿la media +4.58% aguanta fill REALISTA + COSTOS?
  - fill conservador: un target cuenta solo si el high lo TRASPASA por BUFFER (no un mecha).
  - costos: round-trip C (fee+slippage) restado a cada trade.
Si la media neta sigue positiva (esp. en 2022) → edge real. Reusa exit_study.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
import calib_study as cs
import exit_study as ex

TPS, FRACS, DISASTER, HORIZON = ex.TPS, ex.FRACS, ex.DISASTER, ex.HORIZON
BUFFER = 0.005  # el high debe traspasar el target por 0.5% para contar el fill


def ladder_conservative(entry, highs, lows, close_last):
    if entry is None or entry <= 0 or close_last is None or not highs:
        return None
    hi_max = max(highs); lo_min = min(lows)
    realized = 0.0; sold = 0.0
    for tp, fr in zip(TPS, FRACS):
        if hi_max >= entry * (1 + tp) * (1 + BUFFER):   # traspasa, no toca
            realized += fr * tp; sold += fr
        else:
            break
    if sold == 0.0 and lo_min <= entry * (1 + DISASTER):
        return DISASTER
    return realized + (1.0 - sold) * (close_last - entry) / entry


def add_cons(df):
    o = df["open"].to_numpy(); h = df["high"].to_numpy(); lo = df["low"].to_numpy(); c = df["close"].to_numpy()
    n = len(df); out = np.full(n, np.nan)
    for pos in range(n):
        if pos + 1 >= n:
            continue
        end = min(pos + HORIZON, n - 1)
        if end < pos + 1:
            continue
        r = ladder_conservative(o[pos + 1], list(h[pos + 1:end + 1]), list(lo[pos + 1:end + 1]), c[end])
        if r is not None:
            out[pos] = r
    df["cons_return"] = out
    return df


symbol_dfs = {s: add_cons(cs.compute_features(df)) for s, df in cs.load_spot_daily(cs._DEFAULT_DB).items()}
btc = symbol_dfs["BTCUSDT"].copy()
btc_bull = (btc["close"] > btc["close"].rolling(200, min_periods=200).mean()).rename("btc_bull")
panel = cs._build_panel(symbol_dfs).merge(btc_bull, left_on="date", right_index=True, how="left")
panel["year"] = panel["date"].dt.year

bear = panel[(panel["alive"]) & (panel["btc_bull"] == False)].dropna(subset=["cons_return"])  # noqa: E712
g = bear["cons_return"]
print(f"B2-bear FILL CONSERVADOR (buffer {BUFFER*100:.1f}%):  n={len(g)}  mediana={g.median()*100:.2f}%  media={g.mean()*100:.2f}%")
print(f"  (recuerdo: fill optimista era mediana=2.37% media=4.58%)")
print("\nMedia NETA (fill conservador -costo round-trip):")
for C in (0.01, 0.02, 0.03):
    print(f"  costo {C*100:.0f}%:  media_neta={(g.mean() - C)*100:6.2f}%  mediana_neta={(g.median() - C)*100:6.2f}%")
print("\nMedia NETA por año (fill conservador -2% costo):")
for yr, gy in bear.groupby("year"):
    print(f"  {yr}: n={len(gy):6}  media_neta={(gy['cons_return'].mean() - 0.02)*100:6.2f}%")
