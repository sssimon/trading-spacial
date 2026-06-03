# Funding-carry falsification: VERDICT

**Verdict: PASS**  (Gate A: True, Gate B2: True)

- Symbols used 9: BTCUSDT, ETHUSDT, ADAUSDT, AVAXUSDT, DOGEUSDT, UNIUSDT, XLMUSDT, RUNEUSDT, PENDLEUSDT (dropped ['LINKUSDT', 'SOLUSDT'])
- Gate A (ANNUALIZED net return): mean 0.0633, CI95 [0.0502, 0.0745], LOO min 0.0608
- Gate B1 max drawdown of pooled funding equity (TIME-ordered, $): 232.11; worst single settlement -32.44
- Gate B2 (TOTAL-window return vs one-time shock): bleed 0.0750, post-shock 0.0777

NOTE on scales: Gate A judges the ANNUALIZED carry-rate CI; Gate B2 judges the TOTAL
accumulated window carry against a single 5-day shock (7.5% of notional). Different
scales by design (spec §5/§6). Funding income uses the entry mark (conservative for
appreciating assets; per-interval mark is the fast-follow if the verdict is marginal).

Scope: LIQUID universe only. A FAIL = liquid carry arbed/short-vol, NOT 'no carry anywhere'.
PASS -> strategy-design fork (sizing/rebalance/long-tail). FAIL -> portfolio decision.
