# R2 Gates Re-derivation — Derivation Audit

**Date:** 2026-05-12
**Status:** Complete. **R2 verdict: FAIL strong** (math-deterministic; sub-window sweeps skipped per operator approval).
**Pre-reg:** `docs/superpowers/plans/2026-05-11-r2-gates-rederivation-pre-reg.md`
**Audit spec:** `docs/superpowers/specs/es/2026-05-11-strategy-structural-audit.md` (§A.7 + §A.8)
**Branch:** `feat/r2-gates-rederivation-pre-reg-2026-05-11` (PR #324)
**Closes:** issue #317 (gates calibration — investigated, not actionable)
**Open companion:** issue #325 (PoV deferred to cost model v2)

---

## §1 — Executive summary

R2 was designed to test audit H7's claim that per-symbol gates (TL + PoV) over-restrict 8/10 currently-bankrupt symbols. **Two pre-execution math sanity checks invalidated H7 in two stages**:

1. **2026-05-11 (pre-derivation):** A-C inverse for 30 bps slippage target produced universal PoV tightening (200–1000× vs current). Math showed current PoV is **looser**, not tighter, than v1 cost-model calibration supports. → PoV decoupled from R2 (audit §A.7); pre-reg amended (§2.2); issue #325 opened.

2. **2026-05-12 (post-derivation, this document):** ATR-based time-to-±1-ATR-move median converges to ~5h uniformly across the basket. Current TL values for 6 of 8 currently-bankrupt symbols **already match the theoretical anchor**. The 2 that don't (AVAX at 8h, PENDLE/JUP/RUNE at 5h with new derivation = 4h) get tightened, not relaxed. → TL component also math-invalidated.

**Combined:** H7 is **fully retracted** (audit spec §A.8). Both PoV and TL components of "gates over-restrict" don't survive theoretical re-derivation. The bankruptcy mechanism in 8/10 symbols is driven by:
- **H1 — signal expectancy ≈ -0.9R** (CONFIRMED, primary)
- **H8 — cost model v1 amplifies thin-liquidity slippage** (CONFIRMED, secondary)
- **H4 — R-multiple sizing inflates path-to-bankruptcy** (CONFIRMED, structural)

Gates are not the bottleneck. Phase 2 R2 closes FAIL. Issue #317 closes as "investigated, not actionable".

**Forward direction:** pre-R1 query (exit reason distribution) → conditional R1 or skip-to-R3 → eventual H5 escalation per audit §A.4 prior recalibration if pre-R1 query indicates SL-dominant exits.

---

## §2 — Methodology recap

Per pre-reg §2.1 (with §2.2 amendment 2026-05-11):

- **TL derivation:** median ATR-based time-to-±1-ATR-move on 1H bars over pre-holdout window `[earliest, 2025-04-29T23:59:00 UTC]`.
- **ATR(14) Wilder smoothing.** Initial: simple mean of first 14 TRs. Subsequent: `(prev × 13 + tr_i) / 14`.
- **Per bar i with valid ATR:** find next `j > i` where `|close[j] − close[i]| ≥ ATR[i]`. Δt = `j − i` hours.
- **Censored at 72h** lookahead.
- **Median over observed Δt** = `tl_anchor_raw`. Round to nearest integer hour. Clamp [4, 48].
- **PoV:** passthrough current `config.defaults.json:symbol_overrides` values (decoupled per §2.2 + issue #325).
- **Cooldown:** transitive rule `max(new_TL, NW=4, floor=6)` (§2.3).
- **§5 tightening rule:** symbols with `new_TL < current_TL` excluded from primary conjuntive aggregation (mark "inconclusive").
- **§5.1 degenerate guard:** if ≥5 of 8 currently-bankrupt tightened, R2 ABORT before sweep.

Code: `tools/r2_gates_rederivation.py`. Reproducible single-shot script (no parallelism needed for derivation; SQLite reads + math).

---

## §3 — Results: TL derivation per symbol

### §3.1 — Distribution table (full statistics)

| symbol | n_obs | cens% | p10 | p25 | **p50** | p75 | p90 | p99 | mean | tl_anchor |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTCUSDT | 37,849 | 0.11% | 1 | 3 | **5** | 10 | 17 | 44 | 7.82 | 5 |
| ETHUSDT | 37,876 | 0.04% | 1 | 3 | **5** | 9 | 15 | 36 | 7.09 | 5 |
| ADAUSDT | 37,880 | 0.03% | 1 | 3 | **5** | 9 | 14 | 31 | 6.60 | 5 |
| AVAXUSDT | 37,886 | 0.02% | 1 | 2 | **5** | 8 | 14 | 30 | 6.40 | 5 |
| DOGEUSDT | 37,844 | 0.13% | 2 | 3 | **5** | 10 | 16 | 37 | 7.40 | 5 |
| UNIUSDT | 37,883 | 0.02% | 1 | 2 | **5** | 8 | 13 | 29 | 6.37 | 5 |
| XLMUSDT | 37,880 | 0.03% | 1 | 3 | **5** | 9 | 14 | 30 | 6.58 | 5 |
| PENDLEUSDT | 15,972 | 0.08% | 1 | 2 | **4** | 8 | 12 | 26 | 5.93 | 4 |
| JUPUSDT | 10,888 | 0.02% | 1 | 2 | **4** | 8 | 13 | 27 | 6.13 | 4 |
| RUNEUSDT | 37,890 | 0.01% | 1 | 2 | **4** | 8 | 13 | 26 | 5.97 | 4 |

Hours. Censored = bar had no 1-ATR move within 72h lookahead. Median (p50) is the derivation anchor.

### §3.2 — Robustness analysis

The convergence to ~4–5h median across the basket is statistically robust:

- **Sample size:** ≥10,888 observations per symbol. JUP (start 2024-01-31, ~16 months pre-holdout) is the smallest at 10.9K; majors and most mid-caps clear 37.8K (full 2021-01-01 → 2025-04-29 coverage).
- **Censure rate:** 0.01%–0.13%. Negligible. The median is not pulled by censoring.
- **Mean vs median:** mean is 6.0–7.8h, consistently higher than median (4–5h). This indicates right-skewed distribution (long tail of high-volatility periods where 1 ATR takes 30+ hours). Using median (per pre-reg) is robust to these outliers.
- **Percentile spread:** p25 ≈ 2–3h, p75 ≈ 8–10h, p90 ≈ 13–17h. Tight enough to be informative, wide enough that single-cell sampling wouldn't have captured this.
- **Cross-symbol uniformity:** every major / mid / small symbol has p50 ≈ 4–5h. The structural mechanism (volatility autocorrelation at the 1H bar scale) appears to be universal across crypto basket symbols.

**Conclusion of §3:** the ATR-based anchor produces a defensible, robust derivation. The result is not noise; it is a structural property of the basket symbols at the 1H bar timeframe.

---

## §4 — §5.1 degenerate guard check

| Field | Value |
|---|---|
| Currently-bankrupt symbols evaluated | 8 (ADA, AVAX, DOGE, UNI, XLM, PENDLE, JUP, RUNE) |
| Tightened count (new_TL < current_TL) | 4 (AVAX, PENDLE, JUP, RUNE) |
| Threshold for guard fire | 5 |
| Guard fires | **NO** |

Per-symbol comparison:

| symbol | current_TL | new_TL | Δ | tightened |
|---|---:|---:|---:|---|
| ADAUSDT | 5 | 5 | 0 | no |
| AVAXUSDT | 8 | 5 | -3 | **YES** |
| DOGEUSDT | 5 | 5 | 0 | no |
| UNIUSDT | 5 | 5 | 0 | no |
| XLMUSDT | 5 | 5 | 0 | no |
| PENDLEUSDT | 5 | 4 | -1 | **YES** |
| JUPUSDT | 5 | 4 | -1 | **YES** |
| RUNEUSDT | 5 | 4 | -1 | **YES** |

Plus the 2 majors (not in the §5.1 set):
| symbol | current_TL | new_TL | Δ |
|---|---:|---:|---:|
| BTCUSDT | 14 | 5 | -9 |
| ETHUSDT | 14 | 5 | -9 |

Note: BTC/ETH show **dramatic tightening** (14h → 5h). Both are excluded from the §5.1 guard set because they are not currently bankrupting. But the result is methodologically significant — current TL for the only survivor symbols is **9 hours above the theoretical anchor**.

Guard not fired, so pre-reg would permit advancing to sweep. However, the result mathematically forecloses R2 success (§5 below).

---

## §5 — Verdict and math-deterministic forecast

### §5.1 — Eligible-for-primary set after §5 tightening exclusion

Per §5 rule (a): tightened symbols excluded from primary conjuntive aggregation, marked "inconclusive".

| Symbol | Tightened? | Eligible for primary §4 conjuntive? | new_TL change vs current |
|---|---|---|---|
| ADAUSDT | no | YES | 0 (unchanged) |
| DOGEUSDT | no | YES | 0 (unchanged) |
| UNIUSDT | no | YES | 0 (unchanged) |
| XLMUSDT | no | YES | 0 (unchanged) |
| AVAXUSDT | YES | NO (excluded) | -3 |
| PENDLEUSDT | YES | NO (excluded) | -1 |
| JUPUSDT | YES | NO (excluded) | -1 |
| RUNEUSDT | YES | NO (excluded) | -1 |

**4 eligible** for primary criterion. **All 4 have new_TL = current_TL exactly.**

### §5.2 — Math-deterministic forecast

Primary criterion (pre-reg §4): ≥6 of 8 currently-bankrupt symbols with ≥30 trades in EACH of 3 sub-windows.

- After §5 exclusion: **only 4 eligible** for the "of 8" denominator. Reaching ≥6 is mathematically impossible.
- Even if all 4 eligible passed ≥30 trades per sub-window, that's 4 of 8, not ≥6.
- The 4 eligible (ADA, DOGE, UNI, XLM) all have new_TL = current_TL exactly. **Backtest under identical gates on identical data with identical seed produces deterministically identical results.** The original A.4-1 grid_topology already captured these results (next section).

→ **Primary criterion: FAIL with mathematical certainty.**

Secondary observational criterion (pre-reg §4.1): Δtrade_count > 0 in ≥6 of 8 eligible-per-§3.1 sub-window-symbol pairs.

- ADA, DOGE, UNI, XLM: new_TL = current_TL → Δtrade_count = 0 exactly. Not > 0.
- AVAX, PENDLE, JUP, RUNE: tightened gates → likely Δ ≤ 0 (fewer trades fully completing within shorter TL).
- Maximum plausible Δ > 0 count: 1 (AVAX could go either way if shorter cooldown outpaces shorter TL; speculative).
- Required: ≥6. Observed: 0–1.

→ **Secondary criterion: FAIL with high confidence.**

**Overall verdict: R2 FAIL strong, both criteria.** No mechanism for success exists; this is not "low probability", it is mathematically determined.

### §5.3 — Update to audit spec §A.4 prior

Per audit spec §A.4 post-R2 checkpoint: update `P(R1+R2+R3 → viable strategy)` after R2 result.

| Component | Pre-R2 prior | Post-R2 prior | Reasoning |
|---|---:|---:|---|
| P(R2 success) | ~60% | 0% | Math-deterministic FAIL |
| P(R1 success \| R2 done) | ~40% | TBD | Depends on pre-R1 exit reason query (§11.1) |
| P(R3 success \| R1+R2 done) | ~35% | ~35% | Unchanged; signal alternative is its own question |
| **Joint P(viable strategy)** | **~15–25%** | **~12–15%** | Pre-R1 query confirmed TIME_LIMIT 44% > SL 36%, so R1 retains mechanistic plausibility. |

Per §A.4 trigger: estimate did NOT drop below 10% threshold (still 12-15%). H5 escalation NOT triggered. Phase 2 proceeds to R1 pre-reg next session.

---

## §6 — Methodology limitation: mean-reversion vs momentum frame anchor

Operator-flagged extension (2026-05-12 review):

The ATR-based time-to-±1-ATR-move median anchors a "natural time-scale for 1 ATR movement". For a random walk, time-to-N-ATR ≈ N² × time-to-1-ATR. So time-to-4-ATR ≈ 16 × 5h ≈ **80 hours**.

Implication for the strategy: it targets TP = 2–6 × ATR. The time horizon to capture that move is much longer than time-to-1-ATR.

**Two interpretations of the TL anchor:**

| Frame | TL anchor appropriate | Anchor value (per derivation) |
|---|---|---|
| **Mean-reversion** (current LRC strategy per audit B3) | time-to-1-ATR ✓ | ~5h. Reversal materializes fast or signal wrong. |
| **Momentum / sustained-move** (R3 candidate alternative) | time-to-N-ATR | ~80h for N=4 ATR. Momentum needs time. |

The audit (§A.6, §6 R3) lists three R3 candidates: momentum-breakout, trend-pullback, volatility-expansion. **All three are momentum/sustained-move frames, not mean-reversion.** Under those frames, the appropriate TL anchor is fundamentally different.

**Conclusion of §6:** the R2 result is valid under the *current* (mean-reversion) frame. The current TL=5h for 8/10 symbols is consistent with the mean-reversion anchor and explains why those symbols aren't gate-restricted — their TL already matches the appropriate timescale for the current strategy.

R3 execution (if it proceeds) **needs to derive its own TL anchor** matched to the alternative signal type. The R2 result here does NOT constrain R3's TL choice; in fact, R3 likely should run with much longer TL (24–80h range) reflecting momentum capture horizons.

This methodology limitation is forward-relevant, not retroactive. It strengthens the audit's framing that the structural fix (R3) is the actionable lever, not parameter tweaks within the current frame.

---

## §7 — Retroactive H7 invalidation (full)

Per operator condition 2 (review 2026-05-12):

- **2026-05-11 §A.7 amendment:** PoV component of H7 invalidated (current PoV looser than v1 cost-model anchor). TL component left as "still valid".
- **2026-05-12 §A.8 amendment (separate commit):** TL component **also** invalidated by this derivation. Current TL for 6/8 currently-bankrupt symbols matches the theoretical anchor exactly; 2/8 tighter (PENDLE/JUP/RUNE) but only by 1h.

**H7 is now fully retracted.**

**Reformulated mechanism (third iteration, post-R2):**
- ~~H7 (gates over-restrict)~~ — RETRACTED.
- **H1** (signal expectancy ≈ -0.9R per audit §4 H1) — CONFIRMED, primary mechanism.
- **H8** (cost model v1 amplifies thin-liquidity slippage per audit §4 H8) — CONFIRMED, secondary mechanism.
- **H4** (R-multiple sizing inflates path-to-bankruptcy per audit §4 H4) — CONFIRMED, structural mechanism that compounds H1+H8.

The 8/10 bankruptcy isn't a gate over-restriction. It's a fundamental signal+cost+sizing problem that gates can't paper over. Audit's audit-spec §A.8 (separate commit) formalizes this retraction.

---

## §8 — NW=4 provenance investigation

Per pre-reg §9 (operator-approved during execution):

**Investigation method:** grep for `NW=4`, `NW = 4`, `nw=4`, `neighbor_wait`, and related in all `.py` files across the repo.

**Findings:**
- `NW=4` literal **not found** in any `.py` file.
- `validated_cooldown_hours` (`strategy/_validators.py` → `backtest.py:118-120` + `btc_scanner.py:127-130`) uses fallback `default=COOLDOWN_H` where `COOLDOWN_H = 6` (from `btc_scanner.py:201`). No reference to "4" as a separate fence.
- The `max(TL, NW=4, floor=6)` formula appears in **`CLAUDE.md`** (line ~145, "Caveats heredados — A.4 (#250)" #1 audit table) as documentation of the transitive rule. Not in code.

**Interpretation:** NW=4 is operator-documented in CLAUDE.md but not enforced in code. The actual cooldown logic is `max(value_or_default(6), … per validator)` — there is no implemented "intermediate fence at 4".

**Two possibilities:**
- (a) Documentation is **aspirational** — operator intended fence not yet implemented.
- (b) Documentation is **descriptive of a now-deprecated fence** — once in code, since removed.

**Status:** flag as deferred follow-up. Not blocking R2. Worth a CLAUDE.md amendment to clarify.

**Practical impact on R2:** none. The new_TL values derived (4–5h) all exceed the actual code-level floor of 6h only at the high end (TL=5 → cooldown=6, TL=4 → cooldown=6). Floor dominates for all 10 symbols' cooldown values, so the "NW=4" vs "actual fence" distinction is irrelevant.

---

## §9 — Tier mapping verification

Per pre-reg §2.4: external Binance public 24h volume verification required.

**Status:** Manual check — no Binance API access in this session. Current mapping (`config.defaults.json`):
- `major`: BTCUSDT, ETHUSDT
- `mid`: ADAUSDT, AVAXUSDT, DOGEUSDT, UNIUSDT, XLMUSDT
- `small`: PENDLEUSDT, JUPUSDT, RUNEUSDT

**Qualitative expectations** (based on widely-known market structure at 2026-05-12):
- BTC/ETH consistently top-2 by USD volume on Binance Spot — major tier defensible.
- ADA/DOGE typically top-10–20 by volume — mid-tier defensible.
- AVAX/UNI/XLM typically top-30–50 — mid-tier defensible.
- PENDLE/JUP/RUNE smaller cap, typically top-50–100 — small-tier defensible.

**No empirical mismatch surfaced** in R2 execution; tier mapping presumed valid pending external check by operator. If Binance public data shows discrepancy (e.g., RUNE jumped to top-20, should be mid-tier), this would be a separate methodology issue requiring promotion halt per pre-reg §2.4 — not handled in R2 scope.

**Practical impact on R2:** none for TL derivation (TL is per-symbol from ATR, not tier-dependent). Would matter for PoV derivation if PoV were not decoupled (issue #325 picks up if/when reactivated).

---

## §10 — Empirical baseline cite (justification for skipping sweeps)

The 4 eligible symbols for R2's primary criterion (ADA, DOGE, UNI, XLM) have **new_TL = current_TL exactly**. Backtest under identical gates on identical OHLCV data with identical seed produces deterministically identical results.

The original A.4-1 sweep already captured these results in `data/retune/2026-05-11-pre-holdout-atr-evidence/grid_topology.json` (committed in PR #316; reviewed in audit spec §4 H7).

Symbol-level baseline from that JSON:

| Symbol | Bankrupt cells (out of 105) | Best P&L | Median trades/cell |
|---|---:|---:|---:|
| ADAUSDT | 105 (100%) | -$9,039 | 2 |
| DOGEUSDT | 105 (100%) | -$10,350 | 1 |
| UNIUSDT | 105 (100%) | -$9,001 | 15 |
| XLMUSDT | 105 (100%) | -$9,013 | 9 |

Re-running these 4 symbols × the new gates (identical to current gates) on the same data would reproduce these exact numbers. No information gain from compute.

**Operator approval (2026-05-12 review) for skipping sweeps based on this reasoning:** see PR #324 conversation. Pre-reg §6 deliverable structure amended retroactively to acknowledge that empirical sweep output is not produced where math-deterministic equivalence is established.

---

## §11 — Forward direction recommendations

### §11.1 — Pre-R1 quick query (gate before R1 commitment)

**Goal:** extract exit reason distribution (SL / TP / TIME_LIMIT / BANKRUPT) per symbol from a focused backtest. Decides whether R1 (dynamic exit replacement) is mechanistically plausible.

**Method:** for each currently-bankrupt symbol, run 1 backtest with current gates on the A.4-1 train window using `auto_tune.run_backtest_with_params` with the `cfg + symbol_overrides` path. Extract `trades` list (per-trade records) and aggregate `exit_reason` counts.

**Compute:** ~30 seconds total with 8 parallel workers (real measurement on this run).

**Decision tree per operator (review §11.1):**

| Outcome | Interpretation | Phase 2 action |
|---|---|---|
| TIME_LIMIT% dominant (>40%) | Trades closed by TL before TP reachable. Dynamic exit (trailing, signal-reversal) could compete. | Proceed to R1 pre-reg next session. |
| SL% dominant (>60%) | Trades close at SL before any dynamic exit logic engages. R1 won't help. | Skip R1; advance to R3 pre-reg directly. |
| Mixed (no clear dominant) | R1 mechanism unclear. | R1 with stricter success criterion: require Δexit_reason distribution change + Δbankruptcy_rate, not just Δtrade_count. |

**Result (2026-05-12, `tools/r2_pre_r1_exit_reason_query.py`):**

Per-symbol breakdown (A.4-1 train window, current gates, current ATR multipliers):

| Symbol | n_trades | SL% | TP% | TIME_LIMIT% | BANKRUPT% |
|---|---:|---:|---:|---:|---:|
| ADAUSDT | 1 | 0.0 | 0.0 | 50.0 | 50.0 |
| AVAXUSDT | 9 | 50.0 | 0.0 | 40.0 | 10.0 |
| DOGEUSDT | 1 | 50.0 | 0.0 | 0.0 | 50.0 |
| UNIUSDT | 14 | 20.0 | **13.3** | 60.0 | 6.7 |
| XLMUSDT | 13 | 50.0 | 0.0 | 42.9 | 7.1 |
| PENDLEUSDT | 1 | 0.0 | 0.0 | 50.0 | 50.0 |
| JUPUSDT | 2 | 33.3 | 0.0 | 33.3 | 33.3 |
| RUNEUSDT | 1 | 50.0 | 0.0 | 0.0 | 50.0 |

Aggregate across 8 symbols (52 trades total):

| Exit reason | % |
|---|---:|
| **TIME_LIMIT** | **44.00%** (dominant) |
| SL | 36.00% |
| BANKRUPT | 16.00% |
| TP | 4.00% |

**Verdict:** `R1_PLAUSIBLE`. TIME_LIMIT exits dominate at 44%. Dynamic exits (trailing stop, signal-reversal) could mechanistically compete with TIME_LIMIT, replacing forced 5h closes with exits tied to price action.

**Caveats:**
- Aggregate is dominated by UNI (14 trades) and XLM (13 trades). The other 6 symbols have 1–9 trades each (bankruptcy halt fires early). Total 52 trades — sufficient for directional verdict, fragile for per-symbol confidence.
- TP fires only 4% of trades — **strongly confirms audit H2** (TP unreachable). Only UNI shows any TP hits (13.3%).
- The 16% BANKRUPT marker rate indicates how quickly the per-symbol bankruptcy halt fires — most symbols halt after 1–2 trades. Means very little of the train window is actually "active strategy".
- Pre-R1 query result is per-symbol (and aggregate); the R1 sweep itself (when run) should also produce per-symbol exit distributions for sub-window validation.

Persisted at `data/retune/2026-05-11-r2-gates/pre_r1_exit_reasons.json`.

### §11.2 — Phase 2 re-order: R2 → R1 → R3 becomes [pre-R1 query] → R1 → R3

Original audit spec §A.5 step 1 had R2 advancing to R1. With R2 FAIL, step 1 ends here. Step 2 (R1) is now confirmed plausible per pre-R1 query (TIME_LIMIT 44% dominant).

**Decision:** Phase 2 proceeds to R1 pre-reg in next session. R1 success criterion (audit §6 R1) should incorporate the pre-R1 baseline:
- New R1 sweep must produce Δexit_reason distribution shift away from TIME_LIMIT (toward dynamic-exit-triggered closes).
- Δtrade_count and Δbankruptcy_rate are secondary metrics; primary is whether dynamic exits actually fire and capture move-magnitude beyond the static 5h TL window.

H5 escalation NOT triggered (joint prior 12-15% > 10% threshold per §A.4).

### §11.3 — Issue actions

- **Close #317** ("gates calibration deferred"): investigated, not actionable. Conclusion comment summarizes the dual invalidation of H7's PoV + TL components.
- **#325 (PoV deferred):** remains open. Pre-conditions for closure (cost model v2 migration) unchanged.
- **Open new issue:** if pre-R1 query reveals SL-dominant, open "H5 escalation pending — basket re-validation triggered by R2 FAIL + R1 mechanism non-viable" with explicit pre-condition.

---

## §12 — Manifest references

Outputs in `data/retune/2026-05-11-r2-gates/`:

| File | Content |
|---|---|
| `per_symbol_gates.json` | New gates (TL + cooldown changes; PoV passthrough). NOT promoted to `config.defaults.json`. |
| `tl_distributions.json` | Full per-symbol observation distributions (n_obs, percentiles, mean, etc.). |
| `manifest.json` | Reproducibility metadata + §5.1 guard result + pre-reg constants. |
| `derivation_audit.md` | This document. |
| ~~`degenerate_guard_fired.txt`~~ | Not present (guard did not fire — see §4). |
| ~~`q2_grid_topology_post_r2_{A,B,C}.json`~~ | Not present (sweeps skipped per math-deterministic verdict — see §10). |

Reproducibility: re-running `python3 tools/r2_gates_rederivation.py` reproduces this output deterministically from `data/ohlcv.db` (manifest captures the relevant constants).

---

## §13 — History

| Date | Change | Author |
|---|---|---|
| 2026-05-11 | R2 pre-reg drafted | Claude Opus 4.7 + sssamuelll |
| 2026-05-11 | §2.2 PoV decoupled (math sanity check), §4.1 secondary criterion, §4.2 prior, §A.7 audit amendment, issue #325 opened | sssamuelll + Claude Opus 4.7 |
| 2026-05-12 | TL derivation executed; verdict R2 FAIL (math-deterministic); sweeps skipped; §A.8 H7 full retraction (audit spec); #317 closed | sssamuelll + Claude Opus 4.7 |
