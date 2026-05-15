# Phase 3 regime-allocation sweep — verdict summary

**Date:** 2026-05-14 → 2026-05-15
**Epic:** [#338](../../../docs/superpowers/specs/es/2026-05-13-epic-regime-allocation-strategy-pivot.md)
**Pre-reg:** [`docs/superpowers/plans/2026-05-14-regime-allocation-phase2-pre-reg.md`](../../superpowers/plans/2026-05-14-regime-allocation-phase2-pre-reg.md)
**Full audit:** [`derivation_audit.md`](derivation_audit.md)

---

## Verdict: `PHASE_3_INSUFFICIENT_DATA`

Pre-reg §4.6 asymmetric halt-guard activated.

| | |
|---|---|
| Halt fired | YES — H2 (signal degenerate) |
| H1 (universal bankruptcy ≥6/8) | 0/8, NOT fired |
| **H2 (n_trades<5 ≥6/8)** | **8/8, FIRED** |
| Windows executed | 1/3 (Window A only — halted before B+C) |
| Sensitivity sweep | NOT RUN (halted with B+C) |
| Naive verdict before halt-guard | `STRONG_PASS` (Window A favorable in raw P&L) |
| §4.6 halt-guard applied | YES — favorable naive → `PHASE_3_INSUFFICIENT_DATA` |
| Phase 4 advance | NO (per pre-reg §4.5 — `PHASE_3_INSUFFICIENT_DATA` is automatic non-advance) |
| Self-policing 4-element check | NOT REQUIRED (gate applies to `SUCCESS_CONDITIONAL` / `PARTIAL_SUCCESS` / `INCONCLUSIVE` only) |

---

## Primary Window A — 8 in-coverage cells, vol_target=30%

| Symbol | n_trades | net_pnl_usd | insufficient_data |
|---|---:|---:|:---:|
| BTCUSDT | 2 | +$782.84 | ✓ |
| ETHUSDT | 2 | +$826.53 | ✓ |
| ADAUSDT | 1 | +$1,032.45 | ✓ |
| AVAXUSDT | 2 | +$1,055.93 | ✓ |
| DOGEUSDT | 3 | -$180.46 | ✓ |
| UNIUSDT | 1 | +$844.42 | ✓ |
| XLMUSDT | 1 | +$605.61 | ✓ |
| RUNEUSDT | 2 | +$292.26 | ✓ |
| **Portfolio** | **14** | **+$5,259.58** (+6.6% on $80K) | **8/8** |

vs Window A baselines (informational, NOT the gating comparison since halt fired):

| Strategy | Window A return |
|---|---:|
| **Regime-allocation @ vol=30%** | **+$5,259.58 (+6.6%)** *(insufficient data — see §3 below)* |
| BTC B&H | -$45,610.33 (-57.01%) |
| Hubrich 200-DMA filter on BTC | $0 (0 transitions; close < SMA200 whole window) |
| LRC archived (sum across 8 cells, cost v2) | -$35,380.23 (-44%, 1 bankruptcy) |

---

## Why the favorable Window A doesn't promote

Per pre-reg §4.6:

> "§10 halt + `n_windows < 3` → `PHASE_3_INSUFFICIENT_DATA` **only** when the naive verdict
> is favorable (PASS / SUCCESS-CONDITIONAL / PARTIAL). Negative verdicts on partial windows
> are preserved."

The Window A P&L looks favorable in raw terms (+6.6% vs BTC B&H -57%), but the mechanism
**barely engaged** — 14 trades across 8 symbols × 91 days. Per §10.4, H2 (signal degenerate)
fires when ≥75% of in-coverage symbols emit `n_trades < 5`. The 8/8 ratio is the strongest
signal possible: every symbol stayed essentially flat.

A favorable verdict from 14 trades on 1 window at 1 vol_target on 1 regime would be
spurious. The §4.6 guard correctly suspends inference and routes to `PHASE_3_INSUFFICIENT_DATA`.

---

## Operator decision (next steps within epic #338)

`PHASE_3_INSUFFICIENT_DATA` is automatic non-advance, but the Bayesian update template
(per pre-reg §12) calls out an operator decision on what to do next:

- **(A) Archive** the strategy class. Close epic #338 with `PHASE_3_INSUFFICIENT_DATA`. Default.
- **(B) Re-run with adjusted halt thresholds** (e.g., loosen `N_TRADES_MIN_FOR_ELIGIBILITY` from 5 to 3). Requires Phase 2 pre-reg amendment + ~30-35 min re-execution.
- **(C) Investigate signal calibration** (e.g., subset lookbacks {5, 10, 20}, alternative aggregation). New epic; out of pre-reg §7 scope.
- **(D) Investigate basket non-trending hypothesis** (e.g., test on top-20 rotational basket). New epic; basket-unlocking decision per epic §4.1.

See [`derivation_audit.md` §8](derivation_audit.md#8--operator-decision-hooks-next-steps-not-phase-4-advance) for full branch detail.

---

## Bayesian update (per pre-reg §12)

**Prior:** P(strategy viable for live) ~26-39% (joint over PASS branches).
**Posterior:** **preserved at ~26-39%** — §4.6 halt-guard prevents inferential weight
from a partial window. The naive Window A result is NOT counted as evidence; we have
zero data on Windows B+C and on sensitivity.

The observed `PHASE_3_INSUFFICIENT_DATA` landed in the lowest-probability bucket of the
auditor prior (~3-5% per §12) — but is precisely the case the §4.6 mechanism was designed
for. **No methodological update is warranted from this outcome alone.** Iteration on signal
calibration or basket choice would generate new evidence; this iteration generated none.

---

## Artifacts in this directory

| File | Purpose |
|---|---|
| [`derivation_audit.md`](derivation_audit.md) | Full audit — methodology, per-cell detail, Bayesian update, operator hooks, execution log |
| [`README.md`](README.md) | This file (one-screen summary) |
| [`verdict.json`](verdict.json) | Machine-readable verdict + classification + per-window aggregates (`schema_version=2`) |
| [`manifest.json`](manifest.json) | Cutoff, code commit, params locked, coverage, halt status, leakage check |
| [`coverage.json`](coverage.json) | Empirical coverage verification + pre-reg-locked table |
| [`halt_diagnostic.json`](halt_diagnostic.json) | Full per-symbol halt breach explanation (H1/H2 status) |
| [`sweep_primary_A.json`](sweep_primary_A.json) | Window A primary cells (8 in-coverage at vol=30%) |
| [`signal_diagnostics.json`](signal_diagnostics.json) | Per-cell exit-reason histograms |
| [`cost_attribution.json`](cost_attribution.json) | Per-cell gross_pnl / slippage_usd / funding_usd split |
| [`bankruptcy_diagnostics.json`](bankruptcy_diagnostics.json) | Per-cell bankruptcy events (0 in Window A) |
| `baseline_btc_bh_{A,B,C}.json` | BTC long-only baseline per sub-window |
| `baseline_hubrich_{A,B,C}.json` | Hubrich 200-DMA filter baseline per sub-window |
| `baseline_lrc_archived_{A,B,C}.json` | LRC archived strategy (cost v2 + structural fixes) per sub-window |

NOT present (halted): `sweep_primary_{B,C}.json`, `sweep_sensitivity_{A,B,C}.json`.

---

## Tests at end of sesión 2

- `tests/test_regime_allocation_sweep.py` — **104/104 PASS** (was 81; 23 new regression tests for tz / retry / inf-coerce)
- `tests/test_holdout_isolation.py` — **13/13 PASS** (unchanged; structural net active)

Pre-reg traceability fully verified by `TestPreRegTraceability` (8 tests on locked constants).
Halt logic + §4.6 asymmetric guard verified by `TestHaltDetection` (28) + `TestAsymmetricHaltGuard` (10).
