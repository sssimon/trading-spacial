# Cost-model v3 — falsification gate run (merge-precondition evidence, spec §9)

**Date:** 2026-06-02
**Branch:** `feat/cost-model-v3` (PR #554)
**Harness:** `tools/ks_stress_replay/falsify_cost_bound.py` (post-roast fixed version: R_i looseness diagnostic, independent exit-leg liquidity, per-symbol counts, honest read-only docstring)
**Data source:** live production `signals.db` at `/var/www/trading/signals.db` (server `atrium-aws`), read-only (`mode=ro`).
**Method:** `git archive feat/cost-model-v3` → isolated `/tmp/cmv3` clone on the server + live `config.json` + a consistent `.backup()` copy of `data/ohlcv.db`; ran with the prod `.venv` (Python 3.11) from the clone. Prod files untouched (verified: `backtest_costs.py` / `costs_calibration.json` mtime unchanged at 2026-06-01T18:59:45 = the prod v2 versions). NN#3-clean: OHLCV `data_start=2026-01-01`, position window `>= 2026-05-21`, both well past the holdout cutoff 2025-04-29; no holdout access.

## Verdict: PASS

```
loaded 28 closed shorts; scoreable=28 unresolved(liquidity)=0
checked symbols: ['AVAXUSDT', 'RUNEUSDT', 'BTCUSDT', 'PENDLEUSDT', 'JUPUSDT']
skipped (noise band): ['ADAUSDT', 'XLMUSDT', 'ETHUSDT', 'DOGEUSDT']
LOOSENESS (diagnostic, not a gate): 14 winners, R_i median=0.352 max=2.846
  (R_i = v3_cost / gross_move; >=1 would invert a winner; 3 winners at R_i>=1)
per-symbol counts: {ADAUSDT: 4, AVAXUSDT: 6, RUNEUSDT: 4, XLMUSDT: 1, BTCUSDT: 2,
  ETHUSDT: 2, PENDLEUSDT: 4, JUPUSDT: 4, DOGEUSDT: 1}
SCOPE CAVEAT: SHORT-only, ~$644 notional, NORMAL regime May-2026, low participation.
  Does NOT license long cost, crisis/wide-spread regimes, high-participation fills,
  any edge claim, or 'validated'.
PASS: v3 preserves all per-symbol price-winner signs (no sign inversion, fee floor intact).
```

## What this proves (and does NOT)

- **PASS (the gate):** no per-symbol price-winner is inverted into a net loser by v3 cost, and no
  modeled cost sits below the external mandatory fee floor (2× published taker = 10 bps RT). v2's
  AVAX-short sign inversion (live +$35 → backtest bankruptcy via ~159 bps) is GONE under v3.
- **The R_i diagnostic (the real signal, not a gate):** v3 is **not uniformly tight**. Median winner
  keeps ~65% of its gross move after v3 cost (R_i median 0.352), but **3 of 14 winners have R_i ≥ 1**
  (max 2.846) — at the per-trade level v3's cost eats the entire gross move for small winners
  (small-tier 30 bps RT floor is heavy relative to tiny moves). The per-symbol aggregate gate passes
  because those small per-trade inversions are outweighed within their symbol's sum. This is honest,
  load-bearing information for the edge search ahead: **small winners may not survive v3 costs.**
- **R1 limits:** this falsifies bound-BREAKAGE (no sign inversion, no sub-fee charging). It does NOT
  prove edge, does NOT prove the bound is "tight", and does NOT validate v3 — tightness cannot be
  validated against single-regime live data without assuming the edge answer (mutual identification).
  SHORT-only, one NORMAL regime, May-2026. `stress_mult` ships inert → crisis-survivability unproven.

## Reproduce

```bash
# On a host with the v3 branch + the server signals.db reachable (read-only) + cached OHLCV:
python -m tools.ks_stress_replay.falsify_cost_bound <path-to-signals.db>
```
n ≥ 20 closed shorts required (got 28). The OHLCV path writes the local cache + may hit the network
(provider failover) — run against a pre-warmed cache or a copy, never the prod `ohlcv.db` directly.
