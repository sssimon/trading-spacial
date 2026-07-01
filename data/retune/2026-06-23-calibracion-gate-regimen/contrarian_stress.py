"""Stress de la jugada contrarian: comprar alts (B2) cuando BTC<SMA200 (bear) + escalera.
El cuadro más fuerte fue B2-bear escalera=+2.4% (mediana). Lo estresamos:
  - MEDIA (no solo mediana): la cola de -50% importa.
  - por AÑO: ¿el +2.4% es robusto o de un año?
  - breakeven de COSTO: a qué costo round-trip la mediana/media = 0 (alts: 1-3% real).
  - breakeven de SURVIVORSHIP: qué fracción p de muertes ocultas (-100%) lleva la MEDIA a 0.
  - vs B2-siempre (el valor del timing de bear).
Reusa exit_study. No toca holdout (hasta 2025-04-29).
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
import calib_study as cs
import exit_study as ex

symbol_dfs = {s: ex.add_ladder(cs.compute_features(df)) for s, df in cs.load_spot_daily(cs._DEFAULT_DB).items()}
btc = symbol_dfs["BTCUSDT"].copy()
btc_bull = (btc["close"] > btc["close"].rolling(200, min_periods=200).mean()).rename("btc_bull")
panel = cs._build_panel(symbol_dfs).merge(btc_bull, left_on="date", right_index=True, how="left")
panel["year"] = panel["date"].dt.year

b2 = panel[panel["alive"]].dropna(subset=["ladder_return"])
bear = b2[b2["btc_bull"] == False]  # noqa: E712  BTC<SMA200
r = bear["ladder_return"]

med = float(r.median()); mean = float(r.mean()); n = int(len(r))
print(f"B2 en BEAR (BTC<SMA200) + escalera:  n={n}  mediana={med*100:.2f}%  MEDIA={mean*100:.2f}%")
print(f"B2 SIEMPRE + escalera:  mediana={float(b2['ladder_return'].median())*100:.2f}%  MEDIA={float(b2['ladder_return'].mean())*100:.2f}%")

print("\nBEAR por año (escalera):")
for yr, g in bear.groupby("year"):
    print(f"  {yr}: n={len(g):6}  mediana={float(g['ladder_return'].median())*100:6.2f}%  media={float(g['ladder_return'].mean())*100:6.2f}%")

# breakeven de costo: net = ret - c. c* que lleva mediana/media a 0.
print(f"\nBreakeven COSTO round-trip: mediana muere a c={med*100:.2f}%  |  media muere a c={mean*100:.2f}%")
print("  (referencia: alts poco líquidas = 1-3% round-trip real)")

# breakeven de survivorship: (1-p)*mean + p*(-1) = 0  ->  p = mean/(mean+1)
if mean > 0:
    p = mean / (mean + 1.0)
    print(f"\nBreakeven SURVIVORSHIP: p={p*100:.2f}%  — si esa fracción de las entradas fueron")
    print("  monedas que murieron (-100%) y no están en el panel, la MEDIA cae a 0.")
    print("  (comprar golpeadas en bear es JUSTO donde mas se muere -> probablemente p_real >> esto)")
else:
    print("\nMEDIA ya es <=0 — la cola se come el edge; jugada muerta sin más stress.")
