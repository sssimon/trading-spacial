# Phase 3 derivation audit — regime-allocation sweep

**Date:** 2026-05-15 (sesión 2 of Phase 3 execution, started 2026-05-14)
**Epic:** #338 (regime-allocation strategy class)
**Pre-reg:** [`docs/superpowers/plans/2026-05-14-regime-allocation-phase2-pre-reg.md`](../../superpowers/plans/2026-05-14-regime-allocation-phase2-pre-reg.md)
**Tool versions:**
- `tools/regime_allocation_sweep.py` (epic #338 Phase 3, post-sesión-2 fixes: tz-naive, retry, inf-coerce)
- `tools/regime_allocation_verdict.py` (epic #338 Phase 3, schema_version=2)
- Cost model v2 (PR #341, anchored Almgren-Chriss + Donier-Bonart)
- Structural fixes #223 sign / #309 K=10 cap / #313 BANKRUPT exclusion active

---

## §0 · TL;DR

**Verdict: `PHASE_3_INSUFFICIENT_DATA`** (per pre-reg §4.6 asymmetric halt-guard).

Window A primary sweep at `vol_target=30%` triggered §10.4 **halt H2 (signal degenerate)** —
8 of 8 in-coverage symbols emitted fewer than 5 trades over the 91-day sub-window. Per §10.4,
B+C primary + sensitivity sweep were halted before execution. Per §4.6 asymmetric guard,
the naive Window A verdict (which would have been favorable — strategy +$5,259 vs BTC B&H
-$45,610 in 2022 bear) is overridden to `PHASE_3_INSUFFICIENT_DATA` because:

1. Halt fired (yes — H2)
2. Available windows < 3 (yes — only A)
3. Naive verdict before halt-guard is favorable (`STRONG_PASS`)

**Phase 4 advance: NO.** Per pre-reg §4.5: `PHASE_3_INSUFFICIENT_DATA` is automatic
non-advance. Operator decides next step — re-run with adjusted halt thresholds OR archive
the strategy class.

**Auditor prior:** ~3-5% probability for `PHASE_3_INSUFFICIENT_DATA` (per pre-reg §12).
Outcome landed in this low-probability bucket.

---

## §1 · Methodology recap

Per pre-reg §2 (locked from epic #338 §8) and §3 (sub-windows):

| Parameter | Value | Source |
|---|---|---|
| Signal | Equal-weight Donchian ensemble, lookbacks `(5, 10, 20, 30, 60, 90, 150, 250, 360)` days | §2.1 |
| Aggregation | Sum of 9 signals ∈ {-9..+9}, direction = sign(sum) | §2.1 |
| Sizing | Volatility-targeting, `n_active=1` single-symbol scope | §2.2 |
| Update | Daily, on 23:00 UTC close of daily bar | §2.1 |
| Primary vol_target | 30% (annualized portfolio vol) | §2.5 |
| Sensitivity vol_target | ∈ {25%, 30%, 35%, 40%} (4-point sweep) | §2.5 |
| Exits | Signal-based: `SIGNAL_FLIP` / `SIGNAL_EXIT` / `BANKRUPT` / `SIM_END` | §2.3 |
| Cost model | v2 sqrt-participation + funding (per-tier conservative) | §2.4 + Phase 0 |
| Warmup | 390 daily bars (longest lookback 360 + vol window 30) | §2.1 + §10.1 |
| Sub-window A | `[2022-04-01, 2022-07-01]` — 91 days, 2022 bear regime | §3 + R3-exact |
| Sub-window B | `[2023-04-01, 2023-07-01]` — 91 days, 2023 recovery regime | §3 + R3-exact |
| Sub-window C | `[2025-01-30, 2025-04-30]` — 90 days, 2025 Q1 regime, cutoff at C end | §3 + R3-exact |
| Coverage A | 8 symbols (PENDLE+JUP excluded, warmup-fail) | §3 + §5.1 |
| Coverage B | 8 symbols (PENDLE+JUP excluded) | §3 + §5.1 |
| Coverage C | 9 symbols (JUP excluded — 364 daily bars < 390 warmup) | §3 + §5.1 |
| Halt H1 | ≥75% of in-coverage symbols bankrupt in Window A primary @ 30% | §10.4 |
| Halt H2 | ≥75% of in-coverage symbols with `n_trades < 5` in Window A primary @ 30% | §10.4 |
| §4.6 asymmetric guard | Favorable naive verdicts (`PASS`, `SUCCESS_CONDITIONAL`, `PARTIAL`) overridden to `PHASE_3_INSUFFICIENT_DATA` when halt + `n_windows<3` | §4.6 |

**Single source of truth for thresholds:** `tools/regime_allocation_sweep.py` constants
`PRIMARY_VOL_TARGET`, `SENSITIVITY_VOL_TARGETS`, `WARMUP_DAILY_BARS`,
`N_TRADES_MIN_FOR_ELIGIBILITY=5`, `HALT_FRACTION_THRESHOLD=0.75`. Pre-reg
traceability verified by `tests/test_regime_allocation_sweep.py::TestPreRegTraceability` (8 tests).

---

## §2 · Primary sweep Window A — per-cell detail

8 in-coverage symbols × `vol_target=30%` (primary cell, 1-per-(symbol,window)). All 8 returned
**`insufficient_data: true`** (`n_trades < 5` per pre-reg §4.1).

| Symbol | n_trades | net_pnl_usd | bankruptcy_count | exit_reasons |
|---|---:|---:|---:|---|
| BTCUSDT | 2 | $782.84 | 0 | `SIGNAL_EXIT: 1, SIM_END: 1` |
| ETHUSDT | 2 | $826.53 | 0 | `SIGNAL_EXIT: 1, SIM_END: 1` |
| ADAUSDT | 1 | $1,032.45 | 0 | `SIM_END: 1` (single open trade held to end) |
| AVAXUSDT | 2 | $1,055.93 | 0 | `SIGNAL_FLIP: 1, SIM_END: 1` |
| DOGEUSDT | 3 | -$180.46 | 0 | `SIGNAL_EXIT: 2, SIM_END: 1` |
| UNIUSDT | 1 | $844.42 | 0 | `SIM_END: 1` (single open trade held to end) |
| XLMUSDT | 1 | $605.61 | 0 | `SIM_END: 1` (single open trade held to end) |
| RUNEUSDT | 2 | $292.26 | 0 | `SIGNAL_FLIP: 1, SIM_END: 1` |
| **Total** | **14** | **+$5,259.58** (portfolio) | 0 | — |

**Mechanism observations:**

- 4 symbols had only 1 trade each (open at signal, hold to SIM_END).
- 0 symbols hit the 5-trade eligibility floor (`N_TRADES_MIN_FOR_ELIGIBILITY = 5`).
- 0 bankruptcies — vol-targeting (`vol_target=30%`, leverage cap 2x) prevented capital destruction.
- 7 of 8 symbols ended with POSITIVE net PnL despite the bear regime.

**Why so few trades?** Window A is 91 days. The 9-lookback Donchian ensemble votes on each
daily close (91 votes); a directional position requires `sign(sum) != 0`. The longer lookbacks
(150d, 250d, 360d) include pre-2022 highs that anchor wide upper channels — even sustained
2022 down-trending failed to push `close < lower channel` for long enough to flip the sum
decisively. Short lookbacks (5d, 10d) likely fired bearish breakouts but were diluted by
the longer-lookback flat votes in the equal-weight sum.

---

## §3 · Halt diagnostic

**Halt H1 (universal bankruptcy):** 0/8 bankrupt — NOT fired. ✅
**Halt H2 (signal degenerate, `n_trades < 5`):** 8/8 low-trade — **FIRED** at threshold ≥6/8.

Per `halt_diagnostic.json`:
```json
{
  "halt": true,
  "halt_reasons": ["H2_signal_degenerate"],
  "h2_n_symbols_low_trade": 8,
  "h2_symbols_low_trade": ["ADAUSDT", "AVAXUSDT", "BTCUSDT", "DOGEUSDT", "ETHUSDT", "RUNEUSDT", "UNIUSDT", "XLMUSDT"],
  "halt_count_threshold": 6,
  "halt_fraction_threshold": 0.75,
  "n_in_coverage": 8,
  "window_evaluated": "A",
  "vol_target_evaluated": 0.3
}
```

**Tool behavior per pre-reg §10.4:** B+C primary sweep + sensitivity sweep skipped entirely.
Per the spec — single source of truth for halt thresholds is `regime_allocation_sweep.py`
(verified by `tests/test_regime_allocation_sweep.py::TestHaltDetection`); verdict tool reads
the diagnostic file (`tests/test_regime_allocation_sweep.py::TestAsymmetricHaltGuard`).

---

## §4 · Sensitivity sweep — not executed

Per §10.4, sensitivity sweep at `vol_target ∈ {0.25, 0.30, 0.35, 0.40}` was halted along with
B+C primary. `n_pass_out_of_4 = 0/4`, `n_available_out_of_4 = 0/4` →
`sensitivity_label = FAIL_CLEAN` (mapped per §4.2 from 0 available passes).

**No inferential weight** from the sensitivity dimension. The verdict tool's
`_compute_sensitivity_per_vol_target` correctly reports `available: false` for every
(window, vol_target) cell.

---

## §5 · Verdict classification (pre-reg §4.3 + §4.6)

| Field | Value | Source |
|---|---|---|
| `halt_fired` | `True` | `halt_diagnostic.json` |
| `n_windows_available` | 1 (Window A only) | per `primary_per_window` |
| `n_primary_pass_windows` | 1 (Window A naive PASS — $5,259 > -$45,610) | |
| `n_degenerate_windows` | 1 (Window A — 8/8 cells insufficient_data) | |
| `naive_verdict_before_halt_guard` | `STRONG_PASS` | per `_classify_verdict` line 530 |
| `halt_guard_applied` | `True` | §4.6 |
| **`verdict`** | **`PHASE_3_INSUFFICIENT_DATA`** | per §4.6 + §10.4 |

**§4.6 asymmetric guard correctness check:**

> "§10 halt + `n_windows < 3` → `PHASE_3_INSUFFICIENT_DATA` **only** when the naive verdict
> is favorable (PASS / SUCCESS-CONDITIONAL / PARTIAL). Negative verdicts (FAIL clean /
> FAIL degenerate / FAIL sweet-spot) on partial windows are **preserved**"

Naive verdict = `STRONG_PASS` (favorable) → override applies → final = `PHASE_3_INSUFFICIENT_DATA`. ✅

This is precisely the case the §4.6 guard was designed for: a single available window
shows a strategy outcome that looks favorable in raw P&L terms, BUT the mechanism barely
engaged. Without the guard, this would have triggered an erroneous Phase 4 advance based
on 14 total trades across 8 symbols × 91 days at a single vol_target on a single regime.
The guard correctly preserves epistemic humility.

---

## §6 · Phase 4 advance decision (per pre-reg §4.5)

```json
{
  "auto_advance_to_phase_4": false,
  "operator_decision_required": false,
  "self_policing_required": false
}
```

`PHASE_3_INSUFFICIENT_DATA` is classified by the verdict tool as automatic non-advance
(consistent with FAIL variants per pre-reg §4.5). **Phase 4 does not advance.** No
self-policing 4-element check is required (that gate applies only to
`SUCCESS_CONDITIONAL` / `PARTIAL_SUCCESS` / `INCONCLUSIVE` per pre-reg §4.5).

**However**, the Bayesian update template indicates the verdict represents an operator
decision point about *next steps within the epic*: re-run Phase 3 with adjusted halt
thresholds OR archive the strategy class. That decision is captured separately (see §8).

---

## §7 · Bayesian update (per pre-reg §12)

**Pre-Phase-3 prior (auditor, per §12):**

| Outcome | P |
|---|---:|
| `STRONG_PASS` | 8-12% |
| `ROBUST_PASS` | 10-15% |
| `SUCCESS_CONDITIONAL` | 8-12% |
| `SWEET_SPOT_FAIL` | 5-8% |
| `PARTIAL_SUCCESS` | 10-15% |
| `FAIL_CLEAN` | 30-35% |
| `FAIL_DEGENERATE` | 5-8% |
| **`PHASE_3_INSUFFICIENT_DATA`** | **3-5%** |
| Joint PASS-or-CONDITIONAL | 26-39% |
| Joint FAIL clean+degenerate | 35-43% |
| Joint INSUFFICIENT | 3-5% |

**Observed outcome:** `PHASE_3_INSUFFICIENT_DATA`. Landed in the lowest-probability bucket
(~3-5% prior).

**Posterior update (per §12 + §A.4 checkpoint pattern, mirror R1/R2/R3):**

P(strategy viable for live) — **preserved at pre-Phase-3 prior (~26-39% range)** because
§4.6 halt-guard prevents inferential weight from a partial window. The naive Window A
result (STRONG_PASS by raw $5,259 portfolio P&L) is NOT counted as evidence: the mechanism
barely engaged (14 trades across 8 symbols × 91 days), and we have zero data on Windows B
and C and on sensitivity. We don't know:

1. Whether the strategy class beats BTC B&H in 2023 recovery (Window B).
2. Whether it beats in 2025 Q1 (Window C).
3. Whether the edge concentrates at a specific `vol_target` or is robust across {25%, 30%, 35%, 40%}.

**Magnitude of shift:** zero. The halt fired BEFORE evaluation completed, and the §4.6
guard correctly suspends inference. This is the discipline-preserving outcome.

**Key diagnostic finding (informative, not gating):** the H2 firing pattern (8/8 symbols
below 5 trades in 91 days) is consistent with two non-exclusive hypotheses:

| Hypothesis | Mechanism | Test |
|---|---|---|
| **H-basket:** 2022 bear regime was non-trending for THIS basket | Sustained directional move but no breakouts on 9-lookback ensemble because longer lookbacks anchored on Nov 2021 highs | Test on a different basket (top-20 rotational à la Zarattini) over same Window A |
| **H-signal:** Equal-weight ensemble dilutes short-lookback breakouts in bear | Short lookbacks (5d, 10d) DO fire bearish breakouts; long lookbacks (250d, 360d) stay flat; sum is too noisy to flip directionally | Test alternative aggregation (signal-strength-weighted) or subset lookback (5+10+20 only) on same window |

Neither hypothesis is testable under the current pre-reg lock (Phase 2 explicitly excludes
basket revision per §7 + epic §4.1, and excludes signal aggregation alternatives per §7).
Both are candidates for a follow-up epic if the operator chooses to investigate.

---

## §8 · Operator decision hooks (NEXT STEPS, not Phase 4 advance)

Per pre-reg §12 Bayesian update template, this verdict represents an operator decision
point about how to proceed within the epic. The verdict tool's
`phase_4_advance_decision` correctly reports `operator_decision_required: false` because
the decision is NOT about Phase 4 advance (PHASE_3_INSUFFICIENT_DATA never advances). The
operator decision is about *what to do next within epic #338*:

| Branch | Action | Cost |
|---|---|---|
| **A. Archive** | Accept that this strategy class (Donchian ensemble + vol-targeting + bidirectional rotational + signal-based exits, on this basket) is not viable under current calibration. Close epic #338 with `PHASE_3_INSUFFICIENT_DATA` verdict. Document FAIL framing. | Zero compute. Methodology stays clean; lessons captured. |
| **B. Re-run with adjusted halt thresholds** | Loosen `N_TRADES_MIN_FOR_ELIGIBILITY` (currently 5, was 10 per epic §6.3 literal). E.g., set to 3 to catch the 4 symbols that had exactly 1-2 trades. Re-run Window A primary; if halt does NOT fire, proceed with B+C+sensitivity. Requires Phase 2 pre-reg amendment + re-execution (~30-35 min compute). | 1 amendment commit + 1 sweep re-run. Risk: chasing decimals; the underlying mechanism observation (signal barely fires) doesn't change. |
| **C. Investigate signal calibration** | Hypothesis H-signal: equal-weight ensemble dilutes short-lookback breakouts. Test subset lookback (e.g., 5+10+20 only) or signal-strength weighting on Window A. Out-of-scope for this epic per pre-reg §7; requires new epic. | New epic; new pre-reg; ~1 week elapsed. |
| **D. Investigate basket non-trending hypothesis** | Hypothesis H-basket: this 10-symbol basket doesn't trend in 2022 bear. Test on alternative basket (top-20 rotational, Zarattini-style) over same Windows A/B/C. Operator hard-locked NO H5 follow-up per epic §4.1; would require unlocking. | New epic; requires basket unlocking decision (out of current epic scope). |

**Default per pre-reg §4.5:** branch (a) Archive. Any other branch requires explicit operator
decision + (for branches B/C/D) a new sub-spec doc — but those are framed as new work, not
Phase 4 advance from this verdict.

---

## §9 · Baselines context

Per pre-reg §6, baseline benchmarks were captured for portfolio-aggregate comparison.
Full data in `baseline_btc_bh_{A,B,C}.json`, `baseline_hubrich_{A,B,C}.json`,
`baseline_lrc_archived_{A,B,C}.json`.

**Per-window summary (informational):**

| Window | Regime | BTC B&H total_return_usd | Hubrich (200-DMA filter on BTC) | LRC archived (sum across in-coverage cells, cost v2 + structural fixes) |
|---|---|---:|---:|---:|
| A (2022-04→07) | bear | -$45,610 (-57%) | $0 (0 transitions, stays in cash) | -$35,380 (sum across 8 cells, 1 bankruptcy) |
| B (2023-04→07) | recovery | +$5,597 (+7%) | +$5,597 (2 transitions) | -$27,516 (sum across 8 cells, 1 bankruptcy) |
| C (2025-01→04) | 2025 Q1 | -$9,084 (-10%) | -$28,163 (14 transitions, whipsaw) | -$26,842 (sum across 9 cells, 0 bankruptcies) |

**LRC archived bankruptcy observation:** 2 of 25 cells (8%) hit `BANKRUPTCY_THRESHOLD =
0.1 × $10K`. R2 prior anticipated "majority with bankruptcy". The lower observed rate
is consistent with structural fixes #309 (K=10 cap) + #313 (BANKRUPT exclusion) being
post-R3 — earlier evidence predates the bug fixes. **Not** an anomaly under the pre-reg
escalation rule (which fires only if `bankruptcy_count == 0` for all 25 cells, which
is not the case).

**LRC archived total losses:** -$89,738 across 25 cells / $250K total basis = -36% over
3 windows under cost model v2 + structural fixes. Consistent with R1/R2/R3 findings about
LRC contamination — this baseline is NOT a viable competitor to regime-allocation, just an
internal control to verify the structural fixes are active and behaving as expected.

---

## §10 · Anti-leakage & holdout integrity

Per `manifest.json`:
```json
"leakage_check": {
  "all_sub_windows_end_le_cutoff": true,
  "method": "Worker slices all OHLCV dfs to index < cutoff before passing to simulate_strategy; sub-window end == cutoff (Window C only) → all bars strictly before cutoff.",
  "holdout_isolation_policy": "Locked holdout dataset is read-only and out of scope until Phase 5 per #246 + #322; sweep tool never references it (verified by tests/test_holdout_isolation.py)."
}
```

`tests/test_holdout_isolation.py`: 13/13 PASS at every commit during sesión 2. Sweep tool
never reads from `data/holdout/`. AST scan in CI structurally prevents accidental leakage
across all repo `.py` files.

**Cutoff:** `2025-04-30T00:00:00+00:00` (matches holdout dataset start exclusive; pre-reg §3).
Window C's `end_iso` equals the cutoff — worker's `df.index < cutoff_naive` slice strictly
excludes the cutoff bar. No holdout touch.

---

## §11 · Sesión 2 execution log (informational, not pre-reg gating)

Worth recording: sesión 2 surfaced 3 wiring bugs in tools/regime_allocation_sweep.py NOT
caught by the 81 unit tests (which mock at higher abstractions). Each was fixed with a
targeted commit + regression test:

| Bug | Commit | Surface | Fix |
|---|---|---|---|
| Tz-aware `sim_start` passed to `_simulate_strategy_regime_allocation` which compares to tz-naive `df.index` | `8fb0ebc` | Smoke test crash with `TypeError: Invalid comparison between dtype=datetime64[ms] and datetime` | Normalize `sim_start`/`sim_end` to tz-naive in both workers + `TestWorkerTzNormalization` (2 tests) |
| `AllProvidersFailedError` mid-baseline crashes pool when Binance returns TCP RST (Windows 10054) on historical chunks AND Bybit lacks historical 5m | `dd8ab71` | Baseline retry failures at RUNE 4h / DOGE 5m / ETH 5m chunks | `_get_cached_data_with_retry` helper with exp backoff (6 attempts × 3s base = ~189s worst case); data loads moved INTO try/except so exhaustion bubbles up as soft error + `TestWorkerRetryWrapper` (3 tests) |
| `profit_factor = math.inf` (zero `gross_loss`) crashes `_save_json` which has `allow_nan=False` per CHANGES_REQUESTED #5 | `f7e12bd` | Primary A crash at 3rd cell (ADA single winning trade) | `_finite_or` coerce to sentinel `99999.0` for `profit_factor`/`win_rate`/`max_drawdown_pct` + `TestProfitFactorInfCoercion` (5 tests). Cache warmer (`086cfd6`) was an env-hygiene companion: 1281s warmup that filled ~1.3M missing 5m bars across 9 symbols. |

**No methodology change.** All fixes are wiring/serialization. Test count grew 81 → 104.

**Compute envelope:** §11 estimate was 6-9h remaining for sesión 2. Actual ~3-4h
including bug-fix iterations, ~21 min cache warming, ~9 min baselines (post-warm), ~12s
Primary A (halt fired → no B+C+sensitivity). Compute saved by halt: ~30 min sensitivity
that wasn't run.

---

## §12 · Closing — methodological notes for the reader

1. The §4.6 asymmetric halt-guard worked exactly as designed. A naive reader of just
   `sweep_primary_A.json` might conclude "regime-allocation BEAT BTC B&H by $50K in 2022
   bear!" — a misleading reading. The pre-reg's structural commitment to halt + asymmetric
   guard correctly converts this into "insufficient data; suspend inference."

2. The `PHASE_3_INSUFFICIENT_DATA` verdict is NOT a `FAIL`. It is "we don't know yet."
   The discipline-preserving operator default is archive; alternative branches (re-run
   with adjusted halt, investigate signal calibration, investigate basket) all require
   explicit new work (sub-spec + amendment or new epic).

3. Cost model v2 + funding accounting are working: cells where the mechanism fired
   showed funding costs of ~$40-260 per cell, slippage ~$40-295 per cell, both on
   single-symbol $10K capital with `vol_target=30%`. These are real (non-zero) costs
   consistent with the calibration in `costs_calibration.json`.

4. The pre-reg's traceability infrastructure paid for itself: every locked constant
   verified by `TestPreRegTraceability` (8 tests), halt logic + asymmetric guard verified
   by `TestHaltDetection` + `TestAsymmetricHaltGuard` (38 tests combined), verdict
   outcomes verified by `TestVerdictOutcomes` (9 verdict states). When the smoke test
   surfaced the tz-aware bug + retry pattern + inf coercion need, the tests for the
   *primary methodology* never broke — only test count grew with regression coverage.

---

**End of audit.** See [`README.md`](README.md) for a one-screen summary table.
See [`verdict.json`](verdict.json) for the machine-readable schema (`schema_version=2`,
including `operator_override` block stub).
