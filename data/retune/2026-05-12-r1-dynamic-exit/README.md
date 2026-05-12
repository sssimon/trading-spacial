# R1 — Signal-Reversal Exit Sweep — 2026-05-12

**Verdict: R1 FAIL** (halt-after-A fired per pre-reg §10).
**Pre-reg:** `docs/superpowers/plans/2026-05-12-r1-dynamic-exit-pre-reg.md`
**Audit:** `derivation_audit.md` in this directory.

## Quick summary

| | Result |
|---|---|
| Sub-window A executed? | ✓ (750 cells × 10 symbols × 1 window) |
| Sub-window B executed? | ✗ (§10 halt aborted) |
| Sub-window C executed? | ✗ (§10 halt aborted) |
| Pre-reg §4 primary criterion (window A) | **FAIL** (0 of 8 symbols with positive net_pnl on argmax cell; 0 with positive avg_ppt) |
| Pre-reg §4 secondary criterion (window A) | **FAIL** (0 of 6 eligible bankrupt symbols with TIME_LIMIT% < 20% on argmax cell) |
| Pre-reg §10 halt condition | **FIRED** (7 of 8 symbols with TIME_LIMIT% > 35% on argmax cell; required: >6) |
| SIGNAL_EXIT mechanism engagement | Confirmed (1,970 of 11,211 real exits = 17.6%, fires in 389/750 cells = 52%) |
| Symbols with ANY positive net_pnl cell | **0 of 8** in-data symbols (across 75 × 8 = 600 eligible cells) |

## Bayesian update

| Prior (per pre-reg §12) | Posterior (post-window-A) | Magnitude |
|---|---|---|
| P(viable strategy) ~12-15% | **~5-7%** | Drops below pre-reg §A.4 < 10% threshold ⇒ H5 escalation strongly considered |

## Operator decision required (pre-reg §4.5)

Two pre-registered options:
1. **R3 with SIGNAL_EXIT incorporated** (single-alternative R3 per audit §A.6 — recommendation: trend-pullback candidate).
2. **H5 escalation: basket re-validation under post-fix simulator.**

**Auditor recommendation: (2) H5 escalation.** The R1+R2 stack confirms exits and gates are not the binding levers; R3 has limited upside per posterior shift; basket re-validation is the smallest-bet path that respects accrued methodology debt.

See `derivation_audit.md` §8 for full reasoning.

## Reproducibility

```bash
# Re-run window A (full 750 cells, ~20 min on 8-core machine):
python tools/r1_signal_exit_sweep.py --window A

# Re-baselines (30 cells, ~5 min):
python tools/r1_signal_exit_sweep.py --baselines-only

# Compute verdict from cached JSONs:
python tools/r1_verdict.py
```

Code commit captured in `manifest.json`. Data sourced from `data/ohlcv.db` (read-only).
