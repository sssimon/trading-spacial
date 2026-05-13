# R3 — Trend-pullback sweep — Summary

**Date:** 2026-05-13
**Verdict:** **R3_FAIL (clean)** — primary criterion fails in 3 of 3 sub-windows. §1.1 hard-lock activates → path (a) of issue #321 escalation.
**Pre-reg:** `docs/superpowers/plans/2026-05-13-r3-trend-pullback-pre-reg.md`
**Full audit:** `derivation_audit.md` (in this directory)

## Primary criterion results

| Window | Primary | Net-pnl > 0 | Symbols engaged |
|---|---|---:|---:|
| A (bear 2022) | **FAIL** | 0 | 8 of 10 in-data |
| B (recovery 2023) | **FAIL** | 0 | 5 of 8 in-data |
| C (recent 2025) | **FAIL** | 0 | 9 of 10 in-data |

**§10.4 halt did NOT fire** (mechanism engaged, TL appropriate). FAIL is profitability-based, not mechanism-based.

## Bayesian update

| Component | Pre-R3 | Post-R3 |
|---|---:|---:|
| Joint P(viable strategy under current basket) | ~12-18% | **~2-4%** |
| P(R3 FAIL clean) | ~50-60% | **~100%** |

Below §A.4 trigger threshold → §1.1 hard-lock activates automatically.

## Next steps (per pre-reg §1.1 + §4.5)

1. **R3 FAIL → path (a) of #321 automatic** (no operator decision required).
2. Operator escalates to Simón with R1+R2+R3 stack as overwhelming evidence (see `derivation_audit.md` §8 for communication draft outline).
3. Issue #271 user-invitation guardrail enforced.
4. H5 escalation hard-locked NO per §1.1.

## Outputs

See `derivation_audit.md` §11 for full file inventory.

Reproducibility: `python tools/r3_trend_pullback_sweep.py` (deterministic from cached OHLCV; commit recorded in `manifest.json`).
