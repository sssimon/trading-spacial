# Phase 2 derivation audit — epic C signal calibration

**Fecha:** 2026-05-15
**Phase 2 execution PR:** TBD
**Verdict:** `AMBIGUOUS` (n_a=0, n_not_a=0 of 8 in-coverage)
**Pre-reg ref:** `docs/superpowers/plans/2026-05-15-signal-calibration-pre-reg.md`
**Code commit:** `3ac2aa4` (post-Phase-1 merge)
**Halt fired:** `false` (no bankruptcies in any cell)

---

## §1 · Methodology recap

Per pre-reg §2.1 + §4.1 the Phase 2 diagnostic runs the **baseline equal-weight Donchian-9 ensemble** over Window A (2022-04-01 → 2022-07-01, 91 daily bars, in-coverage 8 of 10 — PENDLE/JUP excluded per warmup). For each (símbolo, Window A) cell the harness:

1. Loads df1d + df1h with 14-month pre-window warmup.
2. Runs `simulate_strategy` with `cfg.regime_allocation.enabled = True` + baseline 9 lookbacks (no A1 subset). Emits `n_trades`, `win_rate`, `bankruptcy_count`, `net_pnl_usd` per cell.
3. Runs `compute_ensemble_history` (ZARATTINI 9 lookbacks) on df1d → resampled daily breakout history.
4. Slices history to `[2022-04-01, 2022-07-01)` + calls `emit_observability_metrics` → per-lookback counts + aggregate `|sum|` distribution.

Verdict logic per pre-reg §4.1 (locked by Q-PR1 + Q-PR2):

```
counts_A_evidence_per_cell    = firing_count(N=5) ≥ 5 AND firing_count(N=10) ≥ 5 AND firing_count(N=20) ≥ 5
magnitudes_A_evidence_per_cell = p50(|sum_aggregate|) < 2.0  (strict)

A_DETECTED         if ≥6/8 cells: counts_A AND magnitudes_A
B_DETECTED         if ≥6/8 cells: NOT counts_A AND NOT magnitudes_A
AMBIGUOUS          otherwise (mixed evidence — pre-reg §4.1 line 290 worked example)
PHASE_2_INSUFFICIENT_DATA  if halt fired (universal bankruptcy)
```

---

## §2 · Per-symbol observability data

All 8 in-coverage symbols, Window A baseline equal-weight Donchian-9 (`tools/signal_calibration_diagnostic.py` run 2026-05-15 12:21 UTC, 8.3s wall-clock 8 workers parallel):

| Symbol | firing_count(N=5) | firing_count(N=10) | firing_count(N=20) | p25(\|sum\|) | p50(\|sum\|) | p95(\|sum\|) | counts_A | magnitudes_A | evidence |
|---|---:|---:|---:|---:|---:|---:|:---:|:---:|---|
| ADAUSDT  | 91 | 91 | 91 | 5.0 | 9.0 | 9.0 | ✓ | ✗ | MIXED |
| AVAXUSDT | 91 | 91 | 91 | 2.0 | 6.0 | 8.0 | ✓ | ✗ | MIXED |
| BTCUSDT  | 91 | 91 | 91 | 4.0 | 6.0 | 9.0 | ✓ | ✗ | MIXED |
| DOGEUSDT | 91 | 91 | 91 | 0.0 | 7.0 | 9.0 | ✓ | ✗ | MIXED |
| ETHUSDT  | 91 | 91 | 91 | 2.0 | 8.0 | 9.0 | ✓ | ✗ | MIXED |
| RUNEUSDT | 91 | 91 | 91 | 2.0 | 5.0 | 9.0 | ✓ | ✗ | MIXED |
| UNIUSDT  | 91 | 91 | 91 | 7.0 | 9.0 | 9.0 | ✓ | ✗ | MIXED |
| XLMUSDT  | 91 | 91 | 91 | 5.0 | 7.0 | 9.0 | ✓ | ✗ | MIXED |
| **Aggregate** | — | — | — | — | — | — | **8/8** | **0/8** | **all MIXED** |

Counts uniformly maxed out (91/91 = 100% of evaluation bars). Magnitudes uniformly **above** the Q-PR2 threshold of < 2.0 — empirical median magnitudes range from 5 to 9 (max possible 9 for a 9-lookback ensemble).

**Per-cell n_trades + halt diagnostic (from `halt_diagnostic.json`):**

| Symbol | n_trades | bankruptcy_count | net_pnl_usd |
|---|---:|---:|---:|
| ADAUSDT  | 1 | 0 | +1032.45 |
| AVAXUSDT | 2 | 0 | +1055.93 |
| BTCUSDT  | 2 | 0 |  +782.84 |
| DOGEUSDT | 3 | 0 |  -180.46 |
| ETHUSDT  | 2 | 0 |  +826.53 |
| RUNEUSDT | 2 | 0 |  +292.26 |
| UNIUSDT  | 1 | 0 |  +844.42 |
| XLMUSDT  | 1 | 0 |  +605.61 |

All cells n_trades < 5 (insufficient_data=true per pre-reg §4.1 cell exclusion threshold). Same H2 firing pattern observed in #338 Phase 3 audit §2 — consistent with `PHASE_3_INSUFFICIENT_DATA` verdict outcome that triggered epic C in the first place.

---

## §3 · Verdict

**`AMBIGUOUS`** (pre-reg §4.1 row 3). Mechanism: 8/8 cells fall in the "else" clause — neither `counts_A_evidence AND magnitudes_A_evidence` nor `NOT counts_A AND NOT magnitudes_A`. Every cell has the same evidence pattern: counts pass, magnitudes don't pass.

Per pre-reg §4.1 the AMBIGUOUS verdict triggers **Halt H-C1 + operator escalation per §4.5** (no auto-advance to Phase 3).

---

## §4 · Mechanism interpretation

The uniform "counts pass, magnitudes don't pass" pattern is **not** the originally hypothesized H-signal-A or H-signal-B failure mode. It reveals a third mechanism the pre-reg §2.1 framing did not anticipate:

**Sticky-direction × strong-trend window interaction.**

- Donchian sticky-direction logic (`strategy/donchian_ensemble.py::compute_donchian_direction_history`) holds each lookback's direction once a breakout fires until the **opposite** breakout fires. In Window A (BTC -55%, Terra/Luna crash May 2022), the SHORT direction is established early and never flips to LONG.
- For BTC observability: at N=90 and N=150, `count_short = 91` (entire window held SHORT). At N=360, `count_flat = 72` (long-lookback channel hadn't yet warmed). The aggregate ensemble vote = `sign(sum)` is therefore SHORT for the vast majority of bars in every cell.
- Aggregate `|sum|` distribution: p25 = 4, p50 = 6, p95 = 9 across most cells — well above random-walk null `p50 ≈ 2` (pre-reg §10.3). The ensemble is **strongly aligned**, not diluted.
- The mismatch with §4.1's "A vs B" framing: that framing assumed signal failure modes were either dilution (lookbacks disagree → weak aggregate) or under-firing (signals don't trigger). Empirical: signals fire AND agree.

**Translation to n_trades < 5:** the strategy opens a trade only on direction **flips** at the ensemble aggregate level (`SIGNAL_FLIP` or `SIGNAL_EXIT` per pre-reg §2.3). With sticky direction + strong-trend window, ensemble direction is established within the first few bars and almost never flips. Hence 1-3 trades per 91-day cell.

The signal is operating exactly as the Zarattini-9 baseline is designed to — high-conviction trend identification with sticky bias. The H2 firing pattern (n_trades < 5) is a **side effect of high signal quality**, not a calibration failure.

### What the data rules out

1. **H-signal-A (dilution).** Empirical `p50(|sum|) = 5-9` ≫ Q-PR2 threshold 2.0. Aggregate is strongly aligned, not diluted by flat long-lookback votes. **A1 intervention (subset {5, 10, 20}) would not change this** — short lookbacks already vote in the same direction as long lookbacks (e.g. BTC: N=5 → 75 SHORT bars, N=150 → 91 SHORT bars; both pulling SHORT). Removing long lookbacks slightly reduces `n_lookbacks` from 9 → 3, but doesn't change `sign(sum)` because all lookbacks agree.
2. **H-signal-B (no-firing-enough).** Empirical `firing_count = 91/91` for every short-lookback per cell. Counts threshold T=5 (Q-PR1) is trivially passed by ×18.
3. **H-signal verdict tree as pre-reg §4.1 defines it** does not cover this mechanism — both A and B as defined are falsified by the data.

### What the data shifts probability toward

**Emergent hypothesis H-signal-C (sticky-flip-rate limitation).** The Donchian sticky-direction in coherent-direction windows produces few aggregate flips, regardless of how many lookbacks fire or how aligned they are. This is structurally independent of dilution (A) or signal degeneracy (B); it's an interaction between signal logic and window characteristics.

H-signal-C is not pre-registered. Operator §4.5 self-policing path requires sub-spec doc before any intervention design.

---

## §5 · Bayesian update (auditor prior → posterior)

Per pre-reg §12 + §A.4 default-prose convention. The PyMC skill (`pymc-bayesian-modeling`) was **not** invoked — this is the §A.4 default prose path, not a formal posterior checkpoint.

**Prior (pre-Phase-2, pre-reg §12):**

| Hypothesis | Prior P |
|---|---:|
| Phase 2 → A_DETECTED | 30-40% |
| Phase 2 → B_DETECTED | 30-40% |
| Phase 2 → AMBIGUOUS | 20-30% |
| Phase 2 → INSUFFICIENT_DATA | 5-10% |

**Posterior (post-Phase-2):**

| Hypothesis | Posterior P | Δ vs prior | Rationale |
|---|---:|---|---|
| H-signal-A correct (dilution) | **~5%** | -25/35 pp | Empirical aggregate `|sum|` strongly aligned (p50=5-9). Falsified directly. |
| H-signal-B correct (no firing) | **~5%** | -25/35 pp | Empirical firing_count = 91/91 per short-lookback per symbol. Falsified directly. |
| AMBIGUOUS observed | **100%** | +70/80 pp | Materialized verdict per §4.1 verdict tree. |
| H-signal-C (sticky-flip rate, emergent) | **~70-80%** | +70-80 pp | Data-derived mechanism interpretation §4 above. Not pre-registered; subject to follow-up scrutiny. |
| Donchian baseline-as-shipped is structurally limited for 91-day windows in strong-trend regimes | **~70-80%** | new | Independent of H-C labeling — observation about strategy × window-length interaction. |

**Magnitude shift:** the H-signal pull-up framing (sub-A dilution vs sub-B firing) was correctly designed to falsify either pure-A or pure-B; the **data falsifies both simultaneously**. The unanticipated outcome is operator-actionable but does not vindicate the A1 intervention or pivot to meta-epic by default — both options need explicit sub-spec.

---

## §6 · Decision hook — operator §4.5 self-policing options

Per pre-reg §4.5 + §4.1 row 3, AMBIGUOUS verdict + Halt H-C1 → no auto-advance to Phase 3. Operator must choose among (a)-(e) below with the §4.5 4-element self-policing requirement: (1) Bayesian update prose in `derivation_audit.md` (THIS DOC), (2) separate sub-spec doc for the chosen path, (3) auditor counter-signoff, (4) `verdict.json` `operator_override` block populated.

### (a) Archive epic C as `AMBIGUOUS_TERMINAL`

Treat the H-signal hypothesis space (A or B as pre-registered) as exhausted. Falsified evidence is decisive — neither sub-hypothesis is correct under the as-shipped baseline. Document the §4 mechanism interpretation as Phase 2 contribution; close epic C without Phase 3.

**Pros:** clean closure, preserves single-iteration discipline (pre-reg §1.3 + §7), no Phase 3 compute spent.
**Cons:** leaves H-signal-C unprobed; doesn't surface whether A1 happens to help despite the mechanism (data suggests not, but is not direct test).

### (b) Open H-signal-C sub-spec + new pre-reg

Frame the sticky-flip-rate finding as a new sub-hypothesis. Design a Phase 2.5 diagnostic that measures **aggregate flip frequency** (count of `sign(sum)` changes per 91 days) and tests whether modifications (e.g. confidence-threshold gating, aggregation-rule changes) increase flip rate. Pre-register before any compute.

**Pros:** keeps epic C alive with data-derived hypothesis; tests something we now have empirical reason to believe.
**Cons:** breaks single-iteration discipline; sub-spec scoping is substantive (1-2 weeks doc work); ROI uncertain.

### (c) Pivot framing: strategy × window-length interaction

Recognize that the 91-day Window A is short for Donchian sticky-direction sweeps. Pre-reg locked the sub-windows per Q4; an override here would require sub-spec **and** revisiting epic #338 §3 window choice. Consider whether longer sub-windows (e.g., 12-month windows split into 3) might surface different verdict.

**Pros:** addresses root-cause window-length interaction.
**Cons:** breaks Q4 lock; reopens #338 territory; out of epic C scope per §1.2.

### (d) Override AMBIGUOUS → A_DETECTED and run Phase 3 A1

Treat AMBIGUOUS as ≈A given that counts pass, even though magnitudes don't pass dilution test. Run A1 (subset {5, 10, 20}) and observe whether n_trades increases. Auditor expectation: A1 will **not** materially change behavior because long-lookbacks vote in the same direction as short-lookbacks (all aligned SHORT in Window A); reducing 9 → 3 lookbacks doesn't change `sign(sum)`.

**Pros:** uses already-implemented harness; falsifies whether A1 happens to help despite mechanism interpretation.
**Cons:** auditor confidence in null result (P(A1 helps) ≈ 5-15%); spending Phase 3 compute on a low-probability test; weak §4.5 self-policing case (override against own data).

### (e) Override AMBIGUOUS → B_DETECTED and meta-epic escalation

Treat AMBIGUOUS as ≈B given that magnitudes don't pass (dilution test fails); meta-epic could explore signal-family swap. Per pre-reg §7 + Q6 lock this is operator-only and explicitly out of epic C scope.

**Pros:** acknowledges that pre-reg's A/B framing is incomplete; opens broader search.
**Cons:** weakest §4.5 self-policing case (override against majority of evidence — counts strongly support firing, which B would deny); meta-epic scope is undefined.

### Auditor recommendation (NOT operator decision)

**(b) is the highest-ROI option** but breaks single-iteration discipline. If single-iteration matters more than continued investigation, **(a) is the cleanest closure**. (d) and (e) both have weak §4.5 self-policing cases because they override against the majority of evidence. (c) reopens scope outside epic C.

If operator wants forward motion AND respects single-iteration discipline: **(a) is the answer**. If operator accepts breaking single-iteration discipline for additional learning: **(b) is the answer**.

---

## §7 · Operator override schema (if §4.5 chosen)

Populate `verdict.json::operator_override` per Phase 1 verdict tool docstring + #338 §4.5 pattern:

```json
{
  "operator_override": {
    "timestamp": "2026-05-XX ISO 8601 UTC",
    "rationale": "operator-written reason citing §4 mechanism + §5 Bayesian update + chosen path (a)/(b)/(c)/(d)/(e)",
    "sub_spec_doc": "docs/superpowers/plans/2026-05-XX-phase-2-override-XXX.md",
    "auditor_counter_signoff": {
      "agent_id": "code-review-excellence | pymc-bayesian-modeling | manual",
      "signoff_timestamp": "2026-05-XX ISO 8601 UTC"
    }
  }
}
```

If operator does **NOT** override (i.e., choice (a) archive), no override block needed — verdict.json stands as AMBIGUOUS terminal.

---

## §8 · Auditor caveats heredados

1. **Single-iteration discipline** (pre-reg §1.3 + §7). Phase 2 was the diagnostic step; re-running on the same Window A with the same baseline is not iteration. Either (a) accept Phase 2 result as final, or (b) explicitly break single-iteration discipline via sub-spec.
2. **Q-PR1 + Q-PR2 thresholds were not data-fit.** Both rule-derived (T=5 conservative vs random-walk null per pre-reg §10.2; p50<2 anchored to binomial null per §10.3). The thresholds are correct as stated; the *framing* (dilution-only as Type A) is what the data reveals to be incomplete. This is mechanism-clarification, not threshold-recalibration. Per pre-reg §13.12 sub-limitation: if a future iteration revisits Q-PR2, must follow same pre-registration discipline.
3. **Window A specifically.** Phase 4 walk-forward Windows B + C would surface whether the sticky-direction-×-strong-trend interpretation generalizes. Phase 4 is **blocked** by the AMBIGUOUS Phase 2 verdict per pre-reg §6 (Phase 3 not run → Phase 4 not run).
4. **§A.4 default-prose convention applied.** PyMC skill not invoked; posterior is prose, not formal Bayesian model. Materializing a posterior would require: hierarchical model on (símbolo × evidence-type), beta-binomial on P(A1 effective conditional on AMBIGUOUS), or similar. None of these are pre-registered checkpoints; PyMC is operator-on-demand per CLAUDE.md auto-memory note 2026-05-15.
5. **Manifest filename deviation.** `manifest.json` written instead of pre-reg §6 literal `phase2_manifest.json`. Cosmetic — content schema is correct (cutoff, commit, sub-window, coverage). Suggested follow-up cleanup in a separate small PR if operator wants strict §6 fidelity.

---

## §9 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-15 12:21 UTC | Phase 2 diagnostic execution (8 cells × Window A × baseline equal-weight Donchian-9, 8.3s wall-clock). Verdict AMBIGUOUS, n_a=0/8, n_not_a=0/8. Audit drafted. | Claude Opus 4.7 + sssamuelll |
| TBD | Operator §4.5 decision (option a/b/c/d/e) | sssamuelll |
| TBD | If operator chooses (a) archive: epic C closure annotated in #350 + #338 hierarchy | sssamuelll |
| TBD | If operator chooses (b)/(c)/(d)/(e): sub-spec doc + auditor counter-signoff + verdict.json override block | sssamuelll + auditor |
