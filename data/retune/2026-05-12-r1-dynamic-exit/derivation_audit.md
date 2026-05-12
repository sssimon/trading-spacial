# R1 — Signal-Reversal Exit Sweep — Derivation Audit

**Date:** 2026-05-12
**Status:** Complete. **R1 verdict: FAIL** (halt-after-A fired per pre-reg §10; window A shows primary ✗ + secondary ✗; B+C intentionally not run).
**Pre-reg:** `docs/superpowers/plans/2026-05-12-r1-dynamic-exit-pre-reg.md`
**Audit spec:** `docs/superpowers/specs/es/2026-05-11-strategy-structural-audit.md` (§6 R1, §A.5, §A.6, §A.8)
**Branch:** `feat/r1-signal-exit-execution-2026-05-12`
**Closes:** none (R1 inconclusive at the methodology level — operator decision required per §4.5)
**Triggers:** §A.4 H5 escalation consideration (joint prior crossed <10% threshold).

---

## §1 — Executive summary

R1 tested whether replacing the static TIME_LIMIT exit with an LRC-mean-reversion exit (signal-reversal at `lrc_pct ≥ threshold` for LONG, symmetric for SHORT) produces edge over the pre-R1 baseline.

**Result on sub-window A (2022-04-01 → 2022-07-01):**

- **Pre-reg §10 halt fired.** 7 of 8 currently-bankrupt symbols (excluding JUP+PENDLE no-data) have `TIME_LIMIT% > 35%` on their argmax-by-`net_pnl` cell (threshold: >6 symbols).
- **Primary criterion (§4): FAIL.** 0 symbols with `avg_pnl_per_trade > 0`, 0 with `net_pnl > 0` on argmax cells. Required: ≥1 + ≥3 + `avg_pf > 1.2`. Not even close.
- **Secondary criterion (§4 secondary): FAIL.** 0 of 6 eligible currently-bankrupt symbols satisfy `TIME_LIMIT% < 20%` on their argmax cell. Required: ≥6.
- **Sub-windows B + C NOT run** per pre-reg §10 (halt aborts to prevent wasted compute on a clearly-not-engaging mechanism).

**Diagnostic shows the mechanism does engage**, but in the parameter regions where SIGNAL_EXIT fires most (aggressive `lrc_thr=35`), the strategy still loses money — net_pnl is negative across all 750 cells × 8 in-data symbols. So R1 is not a bug-driven false-FAIL: it is a real finding about the strategy's exit mechanism not being the binding constraint on profitability.

**Joint update (Bayesian, declared per pre-reg §A.4 + §12):**

| Component | Pre-R1 prior | Post-R1 posterior | Reasoning |
|---|---:|---:|---|
| P(R1 SUCCESS) | ~12% | **~2%** | Window A primary+secondary both failed decisively; B+C remote chance of flipping but mechanism uniformly engaged in A without lifting profitability. |
| P(R1 INCONCLUSIVE) | ~35% | **~6%** | Secondary on max-SE cells reaches threshold for ADA (18.2%) + RUNE (10.0%), but max-net-pnl rule + uniformly-negative cells preclude formal INCONCLUSIVE. |
| P(R1 FAIL) | ~45% | **~90%** | All 8 in-data symbols have 100% of cells with net_pnl ≤ 0, including the cells where mechanism fires most aggressively. |
| P(R1 SUCCESS-CONDITIONAL) | ~8% | **~2%** | Single-window regime-dependent escape requires B/C to materially differ from A, with no mechanistic reason to expect that. |
| **Joint P(viable strategy)** post-R1 | **~12-15%** | **~5-7%** | Drops below the §A.4 < 10% threshold. **H5 escalation per pre-reg §A.4 is triggered.** |

**Forward direction:** operator decides per pre-reg §4.5:
- (a) Advance to R3 with SIGNAL_EXIT incorporated as baseline (single-alternative R3 per audit §A.6), OR
- (b) H5 escalation: basket re-validation under post-fix simulator (R1+R2 fixes folded in, structural changes deferred).

Auditor recommends (b) given the R1 FAIL + R2 FAIL stack: gates and exits are both confirmed non-actionable, signal alternative is the only remaining structural lever, but the joint prior says even R3 has limited upside.

---

## §2 — Methodology recap

Per pre-reg §2.1–§2.5 (locked, no amendments):

- **Variant:** signal-reversal exit (single alternative per audit §A.6).
- **Definition:** `SIGNAL_EXIT` fires when (LONG: `lrc_pct ≥ lrc_exit_threshold`) or (SHORT: `lrc_pct ≤ 100 − lrc_exit_threshold`), evaluated on bar close.
- **Tie-break (§2.2):** SL (intra-bar) > TP (intra-bar) > SIGNAL_EXIT (close-bar) > TIME_LIMIT (close-bar) > BANKRUPT.
- **TP kept active (§2.3):** `atr_tp_mult` held at the symbol's current value (no sweep) — anti-confounder.
- **Other gates unchanged (§2.4):** TL, PoV, cooldown passed through current `config.defaults.json:symbol_overrides`.
- **Sweep grid (§2.5):** `atr_sl_mult ∈ {0.5, 0.7, 1.0, 1.5, 2.5} × atr_be_mult ∈ {1.5, 2.0, 2.5} × lrc_exit_threshold ∈ {35, 40, 45, 50, 55}` = 75 cells per (symbol, sub-window).
- **Sub-window A (executed):** 2022-04-01 → 2022-07-01 (2,184 1H bars per symbol).
- **Sub-windows B + C (NOT executed per §10 halt).**
- **Cutoff (leakage cliff):** 2025-04-30T00:00:00+00:00. All sub-windows independently constrained below cutoff.
- **Cell selection rule (§4.1):** `argmax(net_pnl)` per (symbol, sub-window) subject to `n_trades ≥ 10`.

**Live-path safety (§6):** SIGNAL_EXIT branch is flag-gated by `cfg.dynamic_exit_enabled` (default False). Production scanner / `btc_api.py` / `btc_scanner.py` paths are byte-identical to pre-R1 (`test_flag_off_byte_identical_to_no_field_baseline` enforces).

Implementation:
- `backtest.py` — `_should_signal_exit(direction, lrc_pct, threshold)` helper + flag-gated SIGNAL_EXIT branch inserted between SL/TP and TIME_LIMIT in the exit-check loop (`tests/test_signal_exit.py:1` covers per-direction logic, warmup guard, tie-break ordering, flag-off regression, and legacy `atr_*` kwargs bypass).
- `tools/r1_signal_exit_sweep.py` — sweep harness (8 parallel workers, multiprocessing).
- `tools/r1_verdict.py` — verdict calculator (loads sweep JSONs, applies §4.1 argmax rule + §4 primary/secondary criteria, emits `verdict.json`).

---

## §3 — Sub-window A results (executed)

### §3.1 — Coverage (pre-reg §3 `usable_bars ≥ 500`)

| Symbol | usable_bars in A | Eligible? |
|---|---:|---|
| BTCUSDT | 2,184 | yes |
| ETHUSDT | 2,184 | yes |
| ADAUSDT | 2,184 | yes |
| AVAXUSDT | 2,184 | yes |
| DOGEUSDT | 2,184 | yes |
| UNIUSDT | 2,184 | yes |
| XLMUSDT | 2,184 | yes |
| RUNEUSDT | 2,184 | yes |
| JUPUSDT | 0 | **excluded** (pre-reg §3 — JUP starts 2024-01-31) |
| PENDLEUSDT | 0 | **excluded** (pre-reg §3 — PENDLE starts 2023-07) |

**8 of 10 symbols eligible.** Matches pre-reg §3 exclusion list exactly.

### §3.2 — argmax-by-`net_pnl` cell per symbol (§4.1)

| Symbol | cell (sl, be, lrc_thr) | n_trades | net_pnl | avg_ppt | PF | TL% | SE% |
|---|---|---:|---:|---:|---:|---:|---:|
| BTCUSDT | (2.5, 2.5, 40.0) | 32 | -3,176.27 | -99.26 | 0.09 | **25.0** | 43.8 |
| ETHUSDT | (2.5, 2.0, 55.0) | 36 | -3,341.68 | -92.82 | 0.09 | 47.2 ⚠️ | 11.1 |
| ADAUSDT | (2.5, 2.5, 45.0) | 46 | -8,174.57 | -177.71 | 0.01 | 73.9 ⚠️ | 10.9 |
| AVAXUSDT | (2.5, 2.0, 45.0) | 45 | -6,960.65 | -154.68 | 0.01 | 57.8 ⚠️ | 22.2 |
| DOGEUSDT | (2.5, 1.5, 40.0) | 38 | -7,566.75 | -199.12 | 0.00 | 65.8 ⚠️ | 13.2 |
| UNIUSDT | (1.5, 1.5, 35.0) | 14 | -8,056.98 | -575.50 | 0.00 | 71.4 ⚠️ | 14.3 |
| XLMUSDT | (2.5, 1.5, 40.0) | 13 | -7,197.80 | -553.68 | 0.00 | 61.5 ⚠️ | 30.8 |
| RUNEUSDT | (2.5, 2.0, 55.0) | 32 | -7,519.11 | -234.97 | 0.00 | 87.5 ⚠️ | 3.1 |

⚠️ = `TL% > 35%` halt-condition contributor. **7 of 8** symbols breach the `TIME_LIMIT% > 35%` threshold on their argmax cell ⇒ pre-reg §10 halt-after-A fires (required: >6).

**All 8 net_pnl values are negative.** Avg_pnl_per_trade likewise. Profit factors ≤ 0.09. Primary criterion (§4) is FAIL by every dimension.

### §3.3 — Mechanism-firing cells (max SE% per symbol)

Where does SIGNAL_EXIT fire most often? At `lrc_thr = 35` (the most aggressive). Per-symbol max-SE% cells (with `n_trades ≥ 10`):

| Symbol | max-SE cell | n_trades | net_pnl | SE% | TL% |
|---|---|---:|---:|---:|---:|
| BTCUSDT | (2.5, 2.5, 35.0) | 33 | -3,303.65 | **51.5** | 24.2 |
| ETHUSDT | (2.5, 2.5, 35.0) | 39 | -3,674.21 | 48.7 | 28.2 |
| ADAUSDT | (1.0, 2.0, 35.0) | 11 | -9,035.59 | 36.4 | **18.2** |
| AVAXUSDT | (2.5, 2.0, 35.0) | 46 | -6,993.85 | 41.3 | 43.5 |
| DOGEUSDT | (1.5, 2.0, 35.0) | 37 | -9,000.91 | 29.7 | 54.1 |
| UNIUSDT | (2.5, 1.5, 35.0) | 28 | -8,634.47 | 28.6 | 60.7 |
| XLMUSDT | (1.5, 1.5, 35.0) | 14 | -9,234.45 | 42.9 | 35.7 |
| RUNEUSDT | (1.0, 1.5, 35.0) | 20 | -9,006.13 | 45.0 | **10.0** |

**ADA and RUNE on max-SE cells satisfy the secondary criterion `TL% < 20%`** (18.2% and 10.0%). But:
- ADA's max-SE cell has `net_pnl = -9,035`.
- RUNE's max-SE cell has `net_pnl = -9,006`.

Even where the mechanism engages strongly enough to displace TIME_LIMIT, **profitability does not follow**. SIGNAL_EXIT shifts exit-reason distribution but does not lift the signal's expectancy.

### §3.4 — Aggregate exit distribution across all 750 cells

| Exit reason | Count | % of all exits |
|---|---:|---:|
| TIME_LIMIT | 4,590 | 40.9% |
| SL | 4,416 | 39.4% |
| **SIGNAL_EXIT** | **1,970** | **17.6%** |
| TP | 235 | 2.1% |
| (BANKRUPT, meta-only) | 297 | — |
| **Total real exits** | **11,211** | 100% |

**SIGNAL_EXIT fires in 389 of 750 cells** (52%). The mechanism is not silent — it engages. The aggregate share (17.6%) is meaningful but TIME_LIMIT still dominates (40.9%).

### §3.5 — Symbols with ANY positive `net_pnl` cell (n_trades ≥ 10)

**0 of 8 in-data symbols** has a cell with `net_pnl > 0`. This is the decisive negative finding: across the entire 75-cell × 8-symbol grid, no parameter combination produces profit on sub-window A.

---

## §4 — §10 halt diagnostic

`data/retune/2026-05-12-r1-dynamic-exit/halt_after_a_diagnostic.json`:

```
halt: True
n_symbols_over_threshold: 7  (required: > 6)
halt_tl_pct_threshold: 35.0%
symbols_over_threshold: [ADA, AVAX, DOGE, ETH, RUNE, UNI, XLM]
```

Pre-reg §10 listed two "most likely causes" for halt-after-A: LRC computation mismatch, or tie-break order bug. **Neither applies here:**

- `tests/test_signal_exit.py` (20 tests) covers per-direction logic, warmup guard, tie-break SL > TP > SIGNAL_EXIT > TIME_LIMIT, flag-off regression — all green.
- SIGNAL_EXIT fires in 389/750 cells and contributes 17.6% of aggregate exits — the mechanism IS engaging.
- The argmax-by-`net_pnl` optimization rule (§4.1) favors cells where SIGNAL_EXIT is least active (high `lrc_thr`, wide `atr_sl_mult`). Even those cells produce negative `net_pnl`.

The halt is firing on the operational definition pre-registered (`TIME_LIMIT% > 35%` on argmax cell). The **interpretation** is real-mechanism-failure-on-profitability, not a bug, not a computation mismatch.

---

## §5 — Sub-windows B + C: NOT executed (pre-reg §10)

Per §10 explicit halt rule: running B+C after A's halt would not change interpretation. The mechanism's lift on profitability is fundamentally not present in A; without a mechanistic reason to expect B/C to differ, compute on B+C is not justified.

**Compute saved:** modest in wall time; the principle is what matters — pre-registration of the halt condition prevents post-hoc rationalization in either direction.

**Cross-sub-window stability (§4.4) is therefore unavailable.** Only window A's argmax cells exist. The "stable across all 3 windows" / "diverges" diagnostic is empty.

If operator overrides §10 and requests B+C: the harness can resume via `python tools/r1_signal_exit_sweep.py --window B --skip-baselines` and `--window C --skip-baselines`. This is **not recommended** without explicit pre-reg amendment.

---

## §6 — Methodology nuance: argmax-net-pnl vs argmax-SE

Pre-reg §4.1 fixed the cell selection rule: `argmax(net_pnl)` subject to `n_trades ≥ 10`. This is **the cell the operator would actually deploy** (selecting for profitability, not for mechanism engagement).

Counterfactual (NOT pre-registered, informative only):
- If the cell selection rule had been `argmax(SIGNAL_EXIT%)` instead, ADA + RUNE max-SE cells would have satisfied the secondary criterion (`TL% < 20%`). Net_pnl is uniformly negative regardless.
- So even under the most charitable cell-selection counterfactual, the primary criterion still fails 8/8 on net_pnl.

This nuance matters for interpretation but does NOT change the formal verdict. The pre-registered cell rule is the contract; the verdict is FAIL.

---

## §7 — Bayesian update + posterior

Per pre-reg §A.4 + §12 (auditor pre-execution priors):

| Outcome | Prior (per pre-reg §12) | Posterior (post window A) | Magnitude shift |
|---|---:|---:|---|
| R1 SUCCESS | ~12% | ~2% | -10pp; primary+secondary both failed; A's mechanism uniformly negative |
| R1 INCONCLUSIVE | ~35% | ~6% | -29pp; max-SE cells uniformly negative ⇒ even charitable counterfactual rules out INCONCLUSIVE under §4 spirit |
| R1 FAIL | ~45% | ~90% | +45pp; halt+primary+secondary all aligned on FAIL |
| R1 SUCCESS-CONDITIONAL | ~8% | ~2% | -6pp; would require B/C to differ mechanistically without reason |

**Joint P(viable strategy) post-R1: ~5-7%** (was ~12-15% per pre-reg §12).

Per pre-reg §A.4 trigger: estimate dropped below the 10% threshold. **H5 escalation strongly considered.**

Auditor reasoning (3 sentences): R1 failed not because the mechanism didn't engage (SIGNAL_EXIT fired in 52% of cells) but because the mechanism shift didn't unlock profitability — every parameter combination of every in-data symbol on sub-window A produced negative `net_pnl`. This rules out R1 as the binding lever: the exit rule is not the bottleneck, the signal's expectancy is. Combined with R2 FAIL (gates not the lever) the joint prior on a viable strategy under the current LRC entry + cost model + basket drops below the 10% threshold pre-registered for H5 escalation in audit spec §A.4.

---

## §8 — Operator decision hooks (pre-reg §4.5)

Per pre-reg §4.5, the R1 FAIL outcome triggers an operator decision before Phase 2 advances. **Two pre-registered options:**

1. **R3 with SIGNAL_EXIT incorporated as baseline** (single-alternative R3 per audit §A.6). Operator picks R3 candidate signal (recommendation: trend-pullback per audit §A.6 R3 listing). R1's SIGNAL_EXIT branch stays flag-gated False in live; R3 uses it in the new-signal backtests as the exit rule. **Conditional**: this only makes sense if operator believes R3's expected-value lift overcomes the joint prior shift.
2. **H5 escalation: basket re-validation under post-fix simulator.** Re-run the structural fixes (R1+R2+post-#223+#280+#309+#313) without strategic changes; verify whether the original 10-symbol basket retains any subset that survives the rebuild. **Conditional**: this assumes the methodology debt audit is "complete enough" for a basket re-evaluation, not that we're confident any subset will pass.

**Auditor recommendation (per §1 above):** path (b). The R1+R2 stack confirms gates and exits are not the binding levers; signal alternative (R3) has limited upside per posterior shift; basket re-validation is the smallest-bet path that respects the methodology debt accrued.

**NOT pre-registered (out of scope without operator amendment):** running R3 with multiple signal candidates in parallel; running B+C of R1 to "see"; touching holdout (issue #322 hard block); altering pre-reg §4.1 cell selection rule to rescue R1 verdict.

---

## §9 — Methodology limitations carried forward

Per pre-reg §13 + audit §A.7/§A.8:

1. **H1 (signal expectancy ≈ −0.9R) is confirmed re-confirmed.** R1's mechanism shift didn't help — exits aren't the binding constraint.
2. **H8 (cost model v1 amplifies slippage) is untouched.** SIGNAL_EXIT closes at `close[i]` — same cost calculation as TIME_LIMIT. Issue #325 unchanged.
3. **Mean-reversion frame anchor (R2 §6) extends to R1.** Under a different (momentum/trend-pullback) signal frame, the exit rule appropriate to that frame would differ. SIGNAL_EXIT as defined is mean-reversion-anchored.
4. **Per-symbol bankruptcy halt (#313) reduces noise** but cannot manufacture positive expectancy where there is none. ADA / DOGE / RUNE bankrupt frequently in window A under R1 just as in baseline.

---

## §10 — Pre-registered exclusion list verification

Per pre-reg §3.1 / §3 coverage:

| Symbol | Window A | Window B | Window C |
|---|---|---|---|
| JUPUSDT | excluded (0 bars) ✓ | excluded (would have been 0 bars) | included (2,160 bars) |
| PENDLEUSDT | excluded (0 bars) ✓ | included (would have been 2,184 bars) | included (2,160 bars) |
| All others | included | included | included |

Window A coverage matches pre-reg exclusions exactly. (B/C not executed per §10.)

---

## §11 — Outputs

`data/retune/2026-05-12-r1-dynamic-exit/`:

| File | Status | Content |
|---|---|---|
| `derivation_audit.md` | committed | This document |
| `manifest.json` | committed | Reproducibility metadata + halt diagnostic flag |
| `sweep_results_A.json` | committed | 750 cells × per-cell metrics for window A |
| `baseline_pre_signal_exit.json` | committed | 30 cells (10 sym × 3 sub-win) baseline reference |
| `coverage.json` | committed | Per-(symbol, sub-window) usable_bars |
| `halt_after_a_diagnostic.json` | committed | Full per-symbol TL% on argmax cell |
| `halt_after_a.txt` | committed | Halt event timestamp + per-symbol breaches |
| `verdict.json` | committed | Formal §4 primary/secondary verdict + cross-window stability |
| `sweep_results_B.json` | NOT generated | §10 halt — B+C aborted |
| `sweep_results_C.json` | NOT generated | §10 halt — B+C aborted |
| `exit_distributions.json` | NOT generated separately | Aggregate is embedded in `sweep_results_A.json` per cell |

Reproducibility: `python tools/r1_signal_exit_sweep.py --window A` reproduces window A output deterministically from cached OHLCV (commit recorded in manifest).

---

## §12 — History

| Date | Change | Author |
|---|---|---|
| 2026-05-12 | R1 pre-reg locked + merged (#328) | sssamuelll + Claude Opus 4.7 |
| 2026-05-12 | Code patch (SIGNAL_EXIT branch + 20 unit tests) committed | Claude Opus 4.7 |
| 2026-05-12 | Sweep window A executed (750 cells, ~19 min wall); §10 halt fired | Claude Opus 4.7 |
| 2026-05-12 | B+C aborted per §10; derivation_audit + Bayesian update + operator decision hooks landed | Claude Opus 4.7 |
