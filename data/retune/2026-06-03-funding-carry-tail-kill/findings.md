# Funding-carry tail-aware kill rule: VERDICT

**Verdict: PASS**  (G1 in-sample: True, G2 out-of-sample: True)

- Symbols used 9: BTCUSDT, ETHUSDT, ADAUSDT, AVAXUSDT, DOGEUSDT, UNIUSDT, XLMUSDT, RUNEUSDT, PENDLEUSDT
- With-kill pooled net: 0.2706   No-kill pooled net: 0.2725
- Kill vs no-kill net: mean delta -0.0018, CI95 [-0.0048, 0.0000], net_adds_value=False
- Max-DD pooled ($): with-kill 150.06 vs no-kill 149.92, kill_lowers_dd=False
- Post-2-shock pooled net: 0.0306  (shock_loss/ea 0.1200, kill-capped at K settlements)
- K-sensitivity (descriptive): K=9: net 0.1739, kills 6.1; K=18: net 0.2679, kills 0.7; K=24: net 0.2706, kills 0.2; K=36: net 0.2720, kills 0.1

Interpretation: kill adds value if net_adds_value (CI lo > 0) OR kill_lowers_dd; a PASS where
the kill neither raises net nor lowers DD means the carry is already robust without it. Leverage 2x fixed.
Scope: liquid universe, in-sample 2024-26 + 2 synthetic shocks. NOT production-deployable
(rebalance #2, long-tail #3, live #4 are separate sub-projects).
