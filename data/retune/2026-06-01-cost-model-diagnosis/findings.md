# Cost-model diagnosis — findings

**Branch verdict: REBUILD**
Winning correction(s): (none)

## Baseline falsification (over-charge headline)
- observable trades: 27 / 27
- winners: 14; winners whose model cost exceeds the entire price move: 14
- median over-charge ratio (model cost / observed move): 6.84

## Per-correction reconcile
| correction | winners exceeded | tier medians (bps) | reconciles |
|---|---|---|---|
| baseline | 14 | major:104.9, mid:496.0, small:1050.0 | False |
| daily_basis | 5 | major:30.0, mid:57.9, small:130.7 | False |
| sf_div_37.95 | 5 | major:30.0, mid:57.9, small:130.7 | False |
| sf_div_31.62 | 5 | major:30.4, mid:60.2, small:142.8 | False |
| sf_div_10 | 10 | major:35.7, mid:91.0, small:300.2 | False |
| both_37.95 | 4 | major:28.1, mid:45.4, small:71.6 | False |
| both_31.62 | 4 | major:28.1, mid:45.4, small:71.9 | False |
| both_10 | 4 | major:28.2, mid:46.4, small:76.1 | False |

## Cross-check C — scan-price vs fill slippage (entry, conflated w/ operator delay)
- median scan->fill slip: 0.000% over 15 trades

## Next
- RE-ANCHOR -> spec the winning correction; confirm with cross-check B (re-run pre-holdout under it).
- REBUILD -> spec real-execution data collection + re-derivation (the v3).

Read-only diagnostic. Thresholds pre-registered in the design spec section 3.
