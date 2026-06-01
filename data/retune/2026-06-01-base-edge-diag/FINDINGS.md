# Base-edge diagnosis — the backtest cost model is falsified by live data

**Date:** 2026-06-01
**Origin:** "promote kill-switch v2 shadow→active" → "prove v2>v1" → "is the base
strategy even a winner?" (Samuel's reframe). This doc is the answer chain.

## TL;DR

The base strategy is **not** a catastrophic loser. The backtest **says** it is
(−90%), but that verdict is an artifact of a deliberately-pessimistic,
**unvalidated** transaction-cost calibration that live data now falsifies by
enough to **invert** per-symbol conclusions. The real priority is **recalibrating
the cost model against real execution**, not building a new strategy and not
validating the kill-switch (both premature).

## Evidence chain

1. **SIGN** (`measure_base_edge.py`): base strategy (no kill switch) over
   pre-holdout (≤2025-04-29), production config: **0/7 winners, 6/7 bankrupt,
   −90%**, win rates 2-19%. (3 curated symbols have no pre-holdout data: PENDLE,
   JUP, RUNE listed only 2026.)

2. **SIGNAL vs COST** (`diagnose_base_edge.py`): aggregate **gross ≈ flat
   (−$720)**, net −$71,470, **cost $70,750**. The signal has ~zero gross edge;
   cost is 98% of the loss.

3. **COST DECOMPOSITION**: **slippage = 91.1% ($64,442)**; fee 6.3%; spread 2.7%;
   funding 0.0% (mean hold 5.3h, no funding intervals). Not direction, not
   funding — slippage.

4. **SLIPPAGE SOURCE**: NOT the 100bps fallback (0 fills hit it). It is the
   genuine v2 sqrt-participation model. Per-fill medians: BTC 45bps, ETH 44,
   ADA 111, AVAX 159, DOGE 235, UNI 345, XLM 283, RUNE 360. The 500bps extreme
   cap binds on illiquid alts (RUNE 25% of fills capped).

5. **CALIBRATION AUDIT** (`backtest_costs.py` + `costs_calibration.json`):
   - **No units bug.** `_usd_per_min = (close × volume) / 60` is correct
     (backtest.py:1018). Liquidity proxy realistic (BTC ~$2.1M/min).
   - The model is the v2 calibrated Almgren-Chriss sqrt, behaving as designed.
   - The calibration is **self-described as worst-quartile / conservative /
     unvalidated**: *"Specific empirical paper not cited because crypto perp
     slippage literature is sparse"*; *"v3 plan includes systematic Binance
     post-trade slippage data collection"*.
   - Open structural hypothesis (unproven): participation is measured against
     **per-minute** flow while the cited literature (Tóth; Donier-Bonart) uses
     **daily** metaorder volume — a ~1440x participation basis gap → ~38x
     slippage under sqrt. size_factor derivation is undocumented.

6. **LIVE RECONCILIATION** (prod signals.db, read-only): 27 closed positions,
   2026-05-21 → 06-01 (11 days), **all SHORT**, avg size $644.
   - **52% win rate (14/27), +$30.67 net** — with real costs included.
   - Per-trade magnitudes ±0.1-0.9%. Incompatible with a 45-700bps cost regime.
   - **Sign inversion**: AVAX-short backtest −$7,345 (bankruptcy) vs live +$35
     (best performer). RUNE-short backtest −$9,020 vs live −$22.87.
   - Arithmetic: if real AVAX-short cost were ~300bps (~$19/trade @ $644), the
     6 live shorts would lose ~$114 in cost alone; they netted +$35 → real cost
     must be ~5-15bps, ~30-40x below the model.

## What this does NOT prove

- Live is **not** proven profitable: 27 trades, 11 days, all-short, one regime,
  +$30 = noise. It refutes the catastrophe; it does not establish edge.
- The gross-flat finding still stands: even at zero cost the signal is
  break-even, not a winner. Recalibration is **necessary, not sufficient**.

## Implication

Every backtest-derived number in the repo is suspect until the cost model agrees
with live to first order: the −90% here, the #272 re-baselining, the
"formula ganadora" numbers, and the entire kill-switch stress-replay premise.

## Recommended next work

**Recalibrate the cost model against real execution** (the model's own "v3").
Validation anchor: the live realized-P&L magnitudes. Bounded, well-defined,
prerequisite to both edge research and any kill-switch validation. Needs its own
brainstorm→spec→plan; touches a production-governing calibration file.

## Reproduce

```
python -m tools.ks_stress_replay.measure_base_edge      # the SIGN
python -m tools.ks_stress_replay.diagnose_base_edge      # signal/cost + dumps base_stream.json
# cost decomposition + slippage dist + live queries: ad-hoc (see this doc)
```

Read-only on OHLCV; holdout cutoff (≤2025-04-29) enforced in base_stream.py
(NON-NEGOTIABLE #3). Live queries strictly `mode=ro`.
