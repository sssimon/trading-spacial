# R3 — Pre-registration sub-spec: trend-pullback entry signal replacing LRC mean-reversion

**Fecha:** 2026-05-13
**Status:** DRAFT — pre-registration ANTES de cualquier execution. Operator review desired before sweep runs.
**Autor:** Claude Opus 4.7 (sesión kickoff post-R1+R2 FAIL) en colaboración con sssamuelll
**Tipo:** pre-registration sub-spec — fija metodología antes de implementación + sweep
**Trigger:** Audit spec §6 R3 + §A.5 step 3 + §A.6 single-alternative discipline + R1 verdict (FAIL clean, #329) + R2 verdict (FAIL strong, #327) + operator-locked hard constraint that R3 FAIL → path (a) of issue #321 directly
**Cierre objetivo:** R3 verdict (SUCCESS / INCONCLUSIVE / FAIL) per §4 → SUCCESS branches to integrated re-run (audit §A.5 step 4); FAIL is hard-locked to stakeholder escalation per §1.1

---

## §0 · Lectura mínima requerida

Antes de revisar este pre-reg, leer en este orden (≈45 min):

1. `data/retune/2026-05-12-r1-dynamic-exit/derivation_audit.md` — R1 verdict (FAIL) + §7 Bayesian update + §6 cell selection nuance
2. `data/retune/2026-05-11-r2-gates/derivation_audit.md` §6 — mean-reversion vs momentum frame anchor caveat (forward-relevant to R3 TL)
3. `docs/superpowers/specs/es/2026-05-11-strategy-structural-audit.md` §6 R3 + §A.5 step 3 + §A.6 single-alternative discipline + §A.4 prior + §A.8 H7 retraction
4. `docs/superpowers/plans/2026-05-12-r1-dynamic-exit-pre-reg.md` §4.6 — halt-guard scope amendment (will mirror for R3)
5. `docs/superpowers/specs/es/2026-05-11-a4-hallazgo-inflexion-metodologica.md` §A.1 (calibration chain) + §A.2 (bug-fix vs modeling framing)

Quién ya leyó esos 5 puede saltar a §1.

---

## §1 · Contexto y alcance

### §1.1 — Trigger inmediato

Phase 2 R1 cerró FAIL clean (PR #329): SIGNAL_EXIT mechanism engaged (52% cell coverage, 17.6% aggregate exit share) pero 0 de 8 in-data symbols con positive `net_pnl` across 600 cells in window A; halt-after-A fired; B+C aborted per pre-reg §10. Phase 2 R2 cerró FAIL strong (PR #327, math-deterministic): gates already match theoretical anchor for 6/8 currently-bankrupt symbols; H7 fully retracted (audit §A.8).

**Reformulated mechanism (post-R2 + R1):**
- **H1 (signal expectancy ≈ -0.9R)** — CONFIRMED, primary.
- **H8 (cost model amplifies thin-liquidity slippage)** — CONFIRMED, secondary.
- **H4 (R-multiple sizing inflates path-to-bankruptcy)** — CONFIRMED, structural.
- ~~H7 (gates over-restrict)~~ — RETRACTED FULLY.

**Joint posterior P(viable strategy under current basket): ~5-7%** (down from pre-R1 ~12-15%, below §A.4 trigger).

R3 attacks H1 head-on by replacing the entry signal frame (mean-reversion → momentum/trend-pullback). It is the **last defendible structural lever** under the current basket per audit §A.6.

**Operator-locked hard constraint (kickoff):** R3 FAIL → DIRECT to path (a) of issue #321 (stakeholder escalation to Simón). NO H5 follow-up, NO further phase, NO retry with different signal candidate. Posterior post-R3-FAIL would drop to ~2-4% — below any defendible re-investigation threshold.

### §1.2 — Alcance del pre-reg

**Hace:**
- Locks trend-pullback como single alternative entry signal per audit §A.6 single-alternative discipline (no iteration among momentum-breakout / trend-pullback / volatility-expansion).
- Pre-registra implementación concreta del entry logic (params, regime-gating, SMA200 warmup, sweep range).
- Pre-registra criterio falsificable de SUCCESS / SUCCESS-CONDITIONAL / INCONCLUSIVE / FAIL antes de cualquier compute.
- Aplica pre-execution math sanity check (§10) — algebraic reasoning + empirical halt conditions for Phase 3.
- Pre-registra Bayesian prior + post-execution update plan.
- Locks operator decision branches: R3 SUCCESS → automatic integrated re-run; R3 FAIL → automatic path (a) of #321; ambiguous outcomes have operator hooks per §4.5.

**No hace:**
- No ejecuta nada todavía. Code + sweep + verdict son commits subsecuentes solo si operator approves §9.
- No modifica `config.defaults.json`, `backtest.py`, `strategy/core.py`, or any code path. Pre-reg only.
- No re-litiga R1+R2 verdicts, basket curado, cost model, regime detector. Out of scope.
- No cambia R1's SIGNAL_EXIT logic (kept active at fixed `lrc_exit_threshold = 50` per kickoff lock — see §2.3 + §13 #3 frame-mismatch caveat).
- No toca holdout (issue #322 hard block remains).
- No promueve `trend_pullback_enabled = True` to production config under any outcome (flag-gated; promotion is post-SUCCESS + separate operator decision).

### §1.3 — Iteración

Esta es la **primera iteración** del R3 methodology. Está abierta a operator pushback en §9.

---

## §2 · Methodology

### §2.1 — Variant selection: trend-pullback (locked)

**Audit §A.6 lists three R3 candidates:**

| Candidate | Mechanism | Literature support | Implementation cost |
|---|---|---|---|
| Momentum breakout | Entry on BB upper-band break + 5m confirmation | Moderate (fragile in crypto) | Low |
| **Trend-pullback** | Entry on SMA50 > SMA200 + price retrace to SMA20 ± 0.5 ATR | **High** (best literature anchor for retail crypto majors) | Low (SMA50/200 already in `strategy/indicators.py`) |
| Volatility expansion | Entry on ATR(14) > 1.5 × ATR(50) | Moderate | Medium |

**Auditor recommendation (audit §A.6 non-binding):** trend-pullback. **Operator locked the choice** in kickoff brief.

**Pre-reg lock:** trend-pullback is **the single alternative**. Per audit §A.6 single-alternative discipline + operator §1.1 hard-lock:
- NO iteration between alternatives within R3.
- If trend-pullback fails, **THAT IS the signal**. NO probing momentum-breakout or volatility-expansion in a subsequent R3' or R3''.
- R3 FAIL → DIRECT to path (a) of #321 per §1.1.

**Justification for trend-pullback (independent of operator lock):**

1. **Frame contrast with current LRC strategy.** Current entry (LRC ≤ 25 LONG, ≥ 75 SHORT) bets explicitly on mean-reversion. Trend-pullback bets on trend continuation post-pullback. The two frames test fundamentally different theses about crypto bar dynamics — orthogonal experimental design.

2. **Literature anchoring for retail crypto.** "Buy the dip in established uptrend" is the canonical retail-momentum approach with the longest historical evidence base. If any retail signal frame has edge in crypto majors, trend-pullback is the most likely candidate per audit §6 R3 listing.

3. **Cheap implementation, low new-bug surface.** SMA20/50/200 + ATR(14) already exist in `strategy/indicators.py` (`calc_sma`, `calc_atr`). Trend-pullback entry logic is ~10-15 lines parallel to (or replacing) the LRC entry in `strategy/core.py`. No new indicator infrastructure required.

4. **Different time horizon than R1's mean-reversion frame.** Trend-pullback bets on multi-bar trend continuation post-pullback — natural horizon is much longer than 5h (R2 §6 caveat). This is methodologically distinct from R1's SIGNAL_EXIT (which targets fast mean-reversion). R3's TL anchor must be re-derived (§2.4 + §10.1).

### §2.2 — Signal logic concreto

**Entry conditions (1H bar close-evaluation, after SMA200 warmup at bar 200):**

```
For each bar i with valid indicators (i ≥ 200 for SMA200 warmup):
    is_uptrend   = SMA50[i] > SMA200[i]
    is_downtrend = SMA50[i] < SMA200[i]
    pullback_ok  = abs(close[i] - SMA20[i]) <= pullback_distance * ATR[i]

    if is_uptrend and pullback_ok:
        emit LONG_ENTRY signal

    elif is_downtrend and pullback_ok:
        if regime_state == "BEAR":
            emit SHORT_ENTRY signal
        # else: skip (BULL or NEUTRAL regimes block SHORT, same discipline as current LRC strategy)
```

Where:
- `SMA50[i]`, `SMA200[i]`, `SMA20[i]` use existing `calc_sma(close, period)` (Pandas rolling mean).
- `ATR[i]` uses existing `calc_atr(df, 14)` (Wilder smoothing).
- `pullback_distance` is a sweep parameter (default 0.5; sweep range per §2.5).
- `regime_state` comes from existing `strategy/regime.py:detect_regime` (60/40 thresholds; cached daily per CLAUDE.md).

**5m entry trigger: LOCKED active (operator §9.7 confirmation, 2026-05-13 PR #333 review):** the 1H signal candidate must be confirmed by a 5m bullish/bearish candle + RSI 5m direction match (per `strategy/core.py:200-225`). Trend-pullback inherits this lock — no new 5m trigger logic, no opt-out within R3 scope. The 5m trigger preservation was implicit in the kickoff brief and explicitly confirmed in operator review (§9.7); recorded here as a pre-registered lock alongside the SMA-based entry logic.

**Replaces LRC entry (per audit §A.6 wording + §9.1 confirmation):** the LRC signal evaluation (`LRC_LONG_MAX = 25`, `LRC_SHORT_MIN = 75`) is **disabled** when `cfg.trend_pullback_enabled = True`. The two signal frames do NOT operate in parallel. This is locked per audit §A.6 single-alternative discipline — confirm in §9.1.

**Score derivation (locked at uniform `SCORE_STANDARD = 2` for R3):** trend-pullback signal has different inputs than LRC, so the existing 0-9 score (`strategy/core.py:254-450` `evaluate_signal`) does NOT apply unchanged. **Pre-registered design:** assign uniform `score = 2` (`SCORE_STANDARD`) to all trend-pullback entries during R3 sweep. Rationale:
- Tier-multiplier sizing (`backtest.py:935-940`: 0.5 / 1.0 / 1.5 for 0-1 / 2-3 / ≥4 score tiers; CLAUDE.md cite at line 895-900 is stale) collapses to `1.0×` uniform on trend-pullback trades — neutral test, no aggressive/defensive sizing override.
- Eliminates score-related confounding within R3 — every trade gets standard sizing.
- If R3 SUCCESS, integrated re-run (audit §A.5 step 4) can revisit score derivation for trend-pullback context.
- Alternative considered + rejected: porting LRC's RSI/BB/divergence score components to trend-pullback. Rejected as scope creep that confounds the signal-frame question with the score-derivation question. Flag in §9.5 if operator wants a different uniform score value.

### §2.3 — SIGNAL_EXIT (R1 mechanism): kept active as baseline

**Per kickoff lock:** R1's SIGNAL_EXIT logic (close on LRC threshold reach, per R1 pre-reg §2.2) is **kept active** during R3 sweep at a **fixed threshold value `lrc_exit_threshold = 50` (LRC midline)** — NOT swept in R3.

**Reasoning per kickoff:**
- R1 FAIL on its own merit but mechanism engaged (52% cell coverage, 17.6% aggregate exit share).
- Keeping it doesn't degrade R3.
- May help if trend-pullback trades reach LRC=50 within the holding window (price retraces from extreme back to midline).

**Methodological tension acknowledged:** SIGNAL_EXIT is LRC-anchored (mean-reversion frame). Applying it to a trend-pullback entry (momentum frame) is conceptually inconsistent — the exit could fire prematurely (LRC=50 reached while trend continues; lost continuation gains) or never fire (LRC stays distant while trend continues; trade depends on TL or TP). This tension is operator-locked per kickoff and documented in §5.6 + §13 #3 limitations. R3 verdict interpretation MUST acknowledge it.

**Tie-break order (same bar) — preserved from R1 §2.2:**

1. SL hit (intra-bar high/low check)
2. TP hit (intra-bar high/low check) — *kept active per §2.4; per-symbol value from `config.defaults.json:symbol_overrides:atr_tp_mult`*
3. SIGNAL_EXIT (close-bar; LRC threshold = 50, fixed)
4. TIME_LIMIT (close-bar; trend-pullback TL per §2.4)
5. BANKRUPT halt (post-trade equity check)

**Implementation flag-gating:** SIGNAL_EXIT branch remains gated by `cfg.get("dynamic_exit_enabled", False)`. R3 sweep sets this to `True` in harness. Production scanner unaffected (R1 baseline preserved per R1 §6).

### §2.4 — Other gates: TL re-derived for momentum frame; PoV/cooldown decoupled

**TL anchor for trend-pullback (CRITICAL — different from mean-reversion 5h):**

R2 §6 established that the ATR-based time-to-1-ATR anchor (~5h) is appropriate for the *mean-reversion* frame. Trend-pullback expects multi-bar trend continuation — natural horizon is much longer. Per random-walk approximation (R2 §6): `time-to-N-ATR ≈ N² × time-to-1-ATR`. For trend-pullback target = 2-3 ATR: ≈ 20-45h.

**Three options for TL anchor (operator decides in §9.2):**

| Option | Method | Pros | Cons |
|---|---|---|---|
| (a) **Conservative uniform 36h** | Single value all symbols | Simple, defensible mid-range covering ~2.7 ATR target horizon under RW. Auditor recommendation. | Doesn't reflect per-symbol volatility profile differences |
| (b) Conservative uniform 48h | Single value all symbols | Covers ~3.1 ATR RW horizon; ceiling per R2 §2.1 clamp policy | Lower trade frequency potentially (TL=48h → cooldown=48h → ~180 entries/yr/symbol max) |
| (c) Per-symbol derived | Empirical median time-from-pullback-to-trend-resumption pre-holdout | Tailored to each symbol's volatility profile + trend behavior | Adds ~30-45 min derivation compute as Phase 3 first-step |

**Auditor recommendation (non-binding):** option (a), **36h uniform**. Cheapest to execute, defendible vs RW theory (`time-to-2.7-ATR ≈ 36h` RW equivalent), keeps cooldown floor-active without dominating trade frequency.

**If operator picks (c):** Phase 3 first-step runs `tools/r3_tl_derivation.py` (NEW) BEFORE any sweep:

1. For each symbol, in pre-holdout window `[earliest, 2025-04-29T23:59 UTC]`, identify "pullback bars" where `SMA50[i] > SMA200[i]` AND `|close[i] - SMA20[i]| ≤ 0.5 × ATR[i]` (or symmetric SHORT condition).
2. For each pullback bar `i`, find next bar `j > i` where either `close[j] > SMA50[j] + 1.0 × ATR[j]` (trend resumption, LONG) OR `SMA50[j] ≤ SMA200[j]` (trend invalidation), with 72h lookahead censor.
3. `tl_anchor_raw_pullback = median(j - i)` in hours over non-censored observations.
4. Round to nearest integer hour. Clamp `[12, 48]`. Symmetric SHORT derivation (downtrend pullback to SMA20 from below).
5. Output `data/retune/2026-05-13-r3-trend-pullback/tl_distributions.json` for operator review BEFORE sweep proceeds.

**PoV (`max_participation_rate`): UNCHANGED.** Decoupled per R2 §2.2 (cost model v1 limitation; deferred to issue #325 / cost model v2 epic). Identical treatment as R1 §2.4 + R2.

**Cooldown: UNCHANGED.** Transitive rule from current `symbol_overrides`. Under longer TL (e.g., 36h), the rule `max(TL, NW=4, floor=6)` produces TL-dominated cooldown — no separate derivation needed.

**`atr_sl_mult`, `atr_be_mult`: SWEPT** (per §2.5).

**`atr_tp_mult`: UNCHANGED** at per-symbol current value (anti-confounder, same as R1 §2.3 pattern). Holding TP fixed isolates the entry-signal-frame question from the exit-tuning question.

### §2.5 — Sweep grid concreto

**Recommended grid (75 cells per (symbol, sub-window)):**

```
atr_sl_mult       ∈ {0.5, 0.7, 1.0, 1.5, 2.5}              # 5 values (R1+Q2 subset)
atr_be_mult       ∈ {1.5, 2.0, 2.5}                        # 3 values (R1+Q2 same)
pullback_distance ∈ {0.3, 0.4, 0.5, 0.6, 0.7}              # 5 values (NEW — trend-pullback specific)
                                                            # → 75 cells per (symbol, sub-window)
```

**Total compute:** 10 symbols × 3 sub-windows × 75 cells = **2,250 backtests** (same magnitude as R1; per §11).

**Why sweep `pullback_distance` instead of, e.g., `lrc_exit_threshold`?**
- `pullback_distance` is the **defining parameter of the trend-pullback entry**. Sweeping it tests the signal's sensitivity to entry-window tightness.
  - 0.3 = tight envelope around SMA20, fewer entries, higher conviction
  - 0.7 = loose envelope, more entries, lower conviction
  - 0.5 (kickoff default) is the middle point
- `lrc_exit_threshold` was swept in R1 already — locking it at 50 for R3 inherits the most-natural "LRC midline" interpretation; sweeping it again would re-litigate R1 within R3 (not the test).

**Alternative grid dimensionalities for operator §9.3:**

| Option | Grid | Total cells | Compute estimate |
|---|---|---:|---:|
| (a) **75 cells (proposed)** | 5 × 3 × 5 | 75 | ~3-4h paralelizado |
| (b) 105 cells | 7 × 3 × 5 (extend SL to {0.5, 0.7, 1.0, 1.2, 1.5, 2.0, 2.5}) | 105 | ~4-5h paralelizado |
| (c) 45 cells | 3 × 3 × 5 (SL ∈ {0.5, 1.0, 2.5}; focus on extremes + middle) | 45 | ~2-3h paralelizado |
| (d) 125 cells | 5 × 5 × 5 (extend BE to {1.5, 1.75, 2.0, 2.25, 2.5}) | 125 | ~5-6h paralelizado |

**Baseline reference:** for each (symbol, sub-window), a single baseline backtest with `cfg.trend_pullback_enabled = False` (current LRC entry, R1+R2 fixes active including SIGNAL_EXIT). Used for Δ comparison in §4.

---

## §3 · Sub-windows specification

**Same Option B as R1 + R2 — leakage-protected, regime-diverse:**

| ID | Window | Regime characterization | Notable coverage |
|---|---|---|---|
| A | 2022-04-01 → 2022-07-01 | Bear market 2022 (Terra/Luna May) | 8/10 (excl. PENDLE start 2023-07, JUP start 2024-01) |
| B | 2023-04-01 → 2023-07-01 | Recovery 2023 (post-FTX) | 9/10 (excl. JUP) |
| C | 2025-01-30 → 2025-04-30 | Recent pre-holdout 3 months | 10/10 |

**Properties identical to R1+R2:**
- Non-overlapping ✓
- All BEFORE holdout_start = 2025-04-30 ✓ (Window C ends at holdout_start exclusive, i.e., last pre-holdout bar at 2025-04-29T23:59 UTC per R2 derivation_audit §2; safe)
- Genuinely OUTSIDE A.4-1 train window [2024-01-30, 2025-01-30] ✓
- 3 distinct regime characterizations

**Per-symbol coverage: same as R1+R2.** Exclusion rule: `usable_bars ≥ 500` per (symbol, sub-window) for inclusion. JUP excluded from A+B; PENDLE excluded from A.

**Warmup considerations specific to trend-pullback:**

- SMA200 needs 200 1H bars warmup (~8.3 days).
- Each sub-window is 91 days × 24 bars = 2,184 bars. SMA200 warmup consumes ~9% of usable bars.
- Effective evaluation window per (symbol, sub-window) for in-data symbols: ~1,984 bars (after warmup).
- Pre-registered: `usable_bars` defined as `total_bars_after_SMA200_warmup ≥ 300`. All sub-windows for in-data symbols comfortably pass this.

---

## §4 · Success criterion

**Primary criterion (conjuntive over 3 sub-windows, per audit §A.6 + kickoff strict 3/3):**

R3 SUCCESS = in EACH of the 3 sub-windows, **simultaneously**:
- ≥3 symbols with `net_pnl > 0` (over the cell selected per §4.1), AND
- avg `profit_factor > 1.2` over the subset of positive-`net_pnl` symbols.

**Required conjunctive holding:** success in 3/3 sub-windows. Per kickoff strictness; audit §A.6 had ≥2/3, operator tightened to 3/3.

**Notes:**
- The "≥3 symbols net_pnl > 0" threshold matches audit §A.6 baseline. It is the floor for declaring "trend-pullback has edge in part of the basket".
- "avg PF > 1.2" filters for materially-beating-cost-tax winners (PF = 1.0 is gross break-even; need margin to be defendible given H8 cost amplification persists in R3).
- Per-trade expectancy condition (`avg_pnl_per_trade > 0` for ≥1 symbol, as in R1 §4) is NOT pre-registered for R3 — the kickoff omits it; audit §A.6 omits it. If a symbol passes `net_pnl > 0` it implicitly passes `avg_ppt > 0` (since net_pnl = sum of per-trade pnl).
- "EACH of 3 sub-windows" does NOT require the same 3 symbols across windows. Sub-window-by-sub-window evaluation; cross-window cell stability is §4.4 informative.

### §4.1 — Cell selection rule per (symbol, sub-window)

**Pre-registered (no post-hoc maximization):**

For each (symbol, sub-window): select the cell that maximizes `net_pnl` over the 75-cell grid for THAT (symbol, sub-window) pair, subject to constraint `n_trades ≥ 10`. If no cell satisfies `n_trades ≥ 10`, mark (symbol, sub-window) as INSUFFICIENT_DATA and exclude from §4 aggregation.

**Anti-overfitting safeguard (cross-sub-window cell stability):**

The cell selected for symbol S in sub-window A need NOT match the cell selected in sub-windows B or C — selection is per-sub-window per audit §A.3. The conjuntive "success in 3/3" requires that **some cell exists** in each sub-window for each criterion-passing symbol; it does NOT require the same cell across sub-windows. Cross-sub-window stability is a separate diagnostic in §4.4 (informative, not gating).

**Deterministic tie-break** (per issue #332 tooling-debt item 1 + §9.4 closure): if two cells share `net_pnl`, tie-break by `(net_pnl, -atr_sl_mult, -atr_be_mult, -pullback_distance)` tuple key. Mirror R1 verdict pattern.

### §4.2 — Failure modes pre-registrados

**Precondition:** the rows below assume §10.4 halt did NOT fire. If a halt did fire: H1 → R3 FAIL (signal degenerate) automatically; H2 → R3 FAIL (clean — TL horizon mismatch) automatically. Both per §10.4 + §1.1 hard-lock. The classification is reflected in `tools/r3_verdict.py:_classify_verdict` (the halt-guard scope per §4.6 applies to favorable verdicts only; negative verdicts are preserved under partial-window evidence).

**Engagement threshold conventions (resolves PR #333 review I-3 denominator question):** in-data symbols per window: A=8, B=9, C=10 (per §3 exclusions). The rows below use absolute thresholds (`≤2 engage` or `≥3 engage`) rather than percentages, so the denominator difference across windows does NOT change the row criteria. "Engage in a window" means: at least one cell in the 75-cell grid produces `n_trades ≥ 10` for that (symbol, window) — i.e., the symbol is not INSUFFICIENT_DATA per §4.1.

| Outcome (post-execution, no halt fired) | Verdict | Phase 2 action |
|---|---|---|
| Primary ✓ in 3/3 sub-windows | **R3 SUCCESS** | Automatic advance to integrated re-run (audit §A.5 step 4): re-run A.4-1.5 + A.4-1 with trend-pullback + SIGNAL_EXIT active over full pre-holdout. |
| Primary ✓ in 2/3 sub-windows AND cross-window cells stable per §4.4 | **R3 SUCCESS-CONDITIONAL** (regime-dependent) | Document failed sub-window + regime characterization. **Operator decides** (per §4.5): (a) advance with regime-conditional notation; (b) treat as INCONCLUSIVE → hard-lock per §1.1. **Asymmetry rationale (resolves PR #333 review I-2):** §4's strict 3/3 conjuntive criterion is tightened from audit §A.6's ≥2/3 to suppress single-window noise as a SUCCESS pathway. The 2/3 SUCCESS-CONDITIONAL row is informationally distinct from FAIL — regime-conditional partial success identifies a strategy that works in SOME regimes (e.g., bull+recovery) but not others (e.g., bear-2022 stress), which an operator may find actionable under explicit regime-gating notation. Treating 2/3 as bare FAIL would discard this signal. Default §4.5 path: hard-lock per §1.1 unless explicit override with documented Bayesian update + new ticket scope. |
| Primary ✓ in 2/3 sub-windows AND cross-window cells diverge wildly per §4.4 | **R3 INCONCLUSIVE** | Cell instability suggests success not robust. **Operator decides** (per §4.5): default = hard-lock per §1.1 (treat as FAIL); override = investigate sub-window-specific failure as separate ticket. **Priority note (resolves PR #333 review Minor 1):** this row takes precedence over the SUCCESS-CONDITIONAL row above when cells diverge wildly per §4.4 reading rules. SUCCESS-CONDITIONAL requires both 2/3 primary ✓ AND cross-window cell stability. |
| Primary ✗ in ≥2/3 sub-windows AND mechanism is degenerate (≤2 of in-data symbols engage per the threshold convention above, in ≥2 of 3 sub-windows) | **R3 FAIL (signal degenerate)** | Per §1.1 hard-lock: path (a) of #321 directly. Signal fires too rarely to evaluate across the basket. |
| Primary ✗ in ≥2/3 sub-windows AND mechanism is NOT degenerate (≥3 of in-data symbols engage in ≥2 of 3 sub-windows) | **R3 FAIL (clean)** | Per §1.1 hard-lock: path (a) of #321 directly. **Default fall-through (resolves PR #333 review Minor 2):** any primary-✗ outcome that does NOT trigger the signal-degenerate row above lands here. The intermediate engagement range (3-5 of in-data symbols engaging) is mapped to FAIL clean — substantial enough engagement to evaluate, but profitability absent. Mirror R1 FAIL framing — "mechanism engaged, profitability absent". |

**Operator hard-lock from §1.1 (re-stated):** R3 FAIL of any flavor → path (a) of #321 automatically. R3 INCONCLUSIVE defaults to hard-lock unless operator overrides per §4.5. Posterior post-R3-FAIL ~2-4%, below any threshold for further investigation. Escalate to Simón with R1+R2+R3 stack as overwhelming evidence.

### §4.3 — Cell selection rule (re-stated for clarity)

Same as §4.1. This anchor exists to make §4 readable as a self-contained spec.

### §4.4 — Cross-sub-window cell stability (informative, not gating)

For each in-data symbol, report:
- Cell selected in window A: `(sl_A, be_A, pull_A)`
- Cell selected in window B: `(sl_B, be_B, pull_B)`
- Cell selected in window C: `(sl_C, be_C, pull_C)`

**Reading rules:**
- All 3 windows pick same cell for ≥3 symbols → "robustly tunable" (bonus signal for SUCCESS interpretation).
- Cells diverge wildly (e.g., `pull_A=0.3, pull_C=0.7`) → "regime-sensitive params" — informative for operator decision but does NOT void §4 primary verdict.
- All 3 windows show different SL values for ≥6 symbols → flag as "high parameter sensitivity" in derivation_audit.md.

### §4.5 — Operator decision hooks (only INCONCLUSIVE / SUCCESS-CONDITIONAL — SUCCESS and FAIL are pre-locked)

**R3 SUCCESS branch:** automatic advance to integrated re-run (audit §A.5 step 4). No operator decision needed unless integrated re-run output materially differs.

**R3 FAIL branch (clean OR signal degenerate):** automatic escalation to path (a) of #321 per §1.1 hard-lock. No operator decision needed — pre-locked. Derivation_audit.md emits the escalation summary + initial communication outline draft for Simón (per kickoff Phase 4 deliverable spec).

**R3 SUCCESS-CONDITIONAL branch (primary ✓ in 2/3 sub-windows):** operator decides:
- (a) Advance with regime-conditional notation (e.g., "R3 SUCCESS in bull+recovery regimes; fails in bear-2022 regime"). Document the constraint as part of integrated re-run scope. Phase 2 advances.
- (b) Treat as INCONCLUSIVE; trigger §4.5 INCONCLUSIVE decision tree.

**R3 INCONCLUSIVE branch (signal fires but cross-window cells diverge wildly):** operator decides:
- (a) **Default:** per §1.1 hard-lock — treat as FAIL. Path (a) of #321. Auditor recommendation if no override.
- (b) Operator override: investigate sub-window-specific failure (e.g., bear-2022 stress test reveals trend-pullback expects too-strong trend continuation). Document explicit override + new investigation scope as separate ticket. Posterior shift documented.

**R3 FAIL (signal degenerate) branch (explicit):** automatic — signal fires too rarely to evaluate. Either trend-pullback signal definition is wrong for this basket (insufficient SMA50/200 trend coherence + pullbacks), or basket is fundamentally non-trending. Path (a) of #321 directly. Document signal-firing frequency per symbol in derivation_audit.md as concrete evidence.

### §4.6 — Halt-guard scope (mirroring R1 §4.6 amendment 2026-05-13)

§10 halt + `n_windows < 3` → `R3_INSUFFICIENT_DATA` **only** when the naive verdict is `R3_SUCCESS` / `R3_SUCCESS_CONDITIONAL`. Negative verdicts (`R3_FAIL` / `R3_INCONCLUSIVE`) on partial windows are **preserved** — §10 is pre-registered to act on dispositive partial negative evidence, not to suspend its inferential weight.

**Asymmetry rationale (mirror R1 §4.6):** spurious favorable verdicts have one-sided incentive bias (operator + project momentum favor declaring success early); honest negative evidence does not carry the same bias. Demanding symmetric sample-size discipline here would penalize the discipline-preserving move (acting on dispositive evidence) and reward the discipline-eroding move (burning compute to formalize an already-decided outcome).

**Scope of this amendment:** clarifies §4.2 verdict-table behavior under §10 halt. Does **not** modify the verdict criteria themselves nor the §10 halt thresholds. Implementation lives at `tools/r3_verdict.py:_classify_verdict` (NEW — mirror `tools/r1_verdict.py` pattern with the asymmetric guard already in place).

**Implication for R3 hard-lock (§1.1):** R3 FAIL classification under partial windows is consistent with the hard-lock to path (a) of #321. The asymmetric guard does not allow R3 SUCCESS on partial-window evidence; it preserves R3 FAIL on partial-window dispositive evidence. The hard-lock cannot be bypassed by a "halt prevented us from seeing the success" argument.

**Methodology framing:** this is an **explicit pre-reg lock**, not a soft post-hoc clarification. The asymmetric scope is pre-registered now, before any R3 sweep runs, to prevent future readers from interpreting the asymmetry as silent rationalization.

---

## §5 · Edge cases pre-registrados

### §5.1 — Signal degenerate: SMA50/200 trend rarely crosses + pullback rarely fires

**Risk:** if SMA50/200 trend condition is rarely satisfied in the basket (e.g., long sideways regimes where SMA50 ≈ SMA200), or if pullback to SMA20 ± 0.5 ATR rarely happens during confirmed trends, the trend-pullback signal fires very few times per symbol per sub-window.

**Pre-registered handling:**
- Halt condition H1 in §10.4 fires if ≥6 of 8 in-data symbols have <10 trades on their argmax cell across the whole 75-cell grid in sub-window A.
- Sub-window A also emits signal-firing diagnostic in `signal_diagnostics.json` (count of bars where signal would fire per symbol, BEFORE applying gates and SL/TP filters). Operator can inspect for diagnostic if H1 doesn't fire.
- If signal degenerate per H1: classify as R3 FAIL (signal degenerate) per §4.2 + automatic escalation per §1.1.

### §5.2 — Signal over-active: every bar fires (pullback envelope too wide)

**Risk:** with `pullback_distance = 0.7`, the envelope around SMA20 may capture nearly every bar during sustained trends with low intraday volatility, inflating trade count without producing edge.

**Pre-registered handling:**
- If `pullback_distance = 0.7` dominates `argmax_net_pnl` for symbols with `avg_trade_duration_hours < 4`, flag as "potentially degenerate over-firing" in derivation_audit.md.
- Cell still counts toward §4 aggregation if it passes §4 primary criteria, but operator reviews the flag before R3 SUCCESS verdict is confirmed.

### §5.3 — SMA200 warmup interaction

SMA200 requires 200 bars warmup (~8.3 days on 1H bars). During first 200 bars of any sub-window, SMA200 is undefined. Trend-pullback signal must not evaluate during warmup.

**Pre-registered handling:** harness skips trend-pullback signal evaluation when `bar_idx < 200`. Documented explicitly in tool source comment + manifest. Tested via assertion in unit test before sweep. Symbols with insufficient data for warmup (per §3 `usable_bars` rule) marked INSUFFICIENT_DATA, excluded from §4 aggregation.

### §5.4 — Bankruptcy halt interaction

Per `simulate_strategy` post-#313: BANKRUPT exits halt new entries for that symbol. Trend-pullback signal does NOT change bankruptcy threshold. Pre-bankruptcy trades may be trend-pullback entries; post-bankruptcy halt symbol is dormant (same as baseline).

**Pre-registered handling:** no special logic. BANKRUPT count remains diagnostic. If `bankruptcy_count` drops sharply with trend-pullback active vs LRC baseline, that's a positive signal (different signal frame may reduce path-to-bankruptcy) — captured incidentally in derivation_audit.md.

### §5.5 — Cost amplification persistence (H8)

Cost model v1 (linear, H8 confirmed in audit) is NOT touched by R3. Trend-pullback entries face the same per-trade cost as LRC entries. If H8's slippage destruction dominates per-trade economics, trend-pullback may not rescue P&L despite different signal frame.

**Pre-registered handling:** no R3 action. R3 INCONCLUSIVE or FAIL with primary ✗ but mechanism engaged is the natural surface where H8 amplification would manifest. Documented in §13 #2 limitation; R3 inherits the limitation. Cost model v2 migration remains scope of issue #325 (separate epic).

### §5.6 — SIGNAL_EXIT frame mismatch (LRC exit on momentum entry)

R1's SIGNAL_EXIT mechanism is LRC-anchored (mean-reversion frame). Applied to trend-pullback (momentum frame), it could:
- Fire prematurely: trade closes when LRC reaches midline 50 while the trend is still resuming (lost continuation gains).
- Never fire: trade entered at LRC distant from midline, trend continues, LRC stays distant — SIGNAL_EXIT inactive while trade depends on TL or TP.

**Pre-registered handling:**
- `lrc_exit_threshold` LOCKED at 50 (midline) for R3 — no sweep on this dimension per §2.3 + §2.5.
- Frame mismatch acknowledged in §13 #3 as inherited methodological limitation per kickoff operator lock.
- If R3 INCONCLUSIVE or SUCCESS-CONDITIONAL, operator may flag SIGNAL_EXIT mismatch in retrospective review (e.g., "what if R3 had used a trend-pullback-appropriate exit like trailing stop?"). NOT in scope for R3 itself. Out-of-scope analysis is fine; out-of-scope re-runs are not.

---

## §6 · Deliverable structure

After operator approval of this pre-reg, R3 execution lands the following on a new branch off `main`:

```
data/retune/2026-05-13-r3-trend-pullback/
├── derivation_audit.md       # methodology recap + per-symbol per-sub-window verdict + cross-window stability + Bayesian update
├── manifest.json             # cutoff, code_commit, leakage_check, sub_windows, sweep grid, baseline ref
├── sweep_results_A.json      # 75 cells × N_eligible_symbols, sub-window A
├── sweep_results_B.json      # sub-window B (ONLY IF §10 halt NOT fired)
├── sweep_results_C.json      # sub-window C (ONLY IF §10 halt NOT fired)
├── baseline_pre_trend_pullback.json  # per (symbol, sub-window) baseline backtest (LRC entry, no trend-pullback) — for Δ comparison
├── signal_diagnostics.json   # per (symbol, sub-window): signal fire count, mean trade duration, exit distribution
├── tl_distributions.json     # ONLY IF §9.2 option (c) chosen — per-symbol TL anchor derivation
├── halt_after_a_diagnostic.json  # ONLY IF §10 halt fires — full per-symbol breach explanation
├── verdict.json              # formal §4 primary verdict + classification per §4.6 halt-guard
└── README.md                 # summary verdict + primary table + cross-window stability + Bayesian update prose
```

Plus:
- `tools/r3_trend_pullback_sweep.py` — reproducible sweep script (adapted from `tools/r1_signal_exit_sweep.py` pattern; applies tooling-debt items 1, 2, 5 per §9.4)
- `tools/r3_verdict.py` — verdict calculator (adapted from `tools/r1_verdict.py` pattern; reuses `_classify_verdict` asymmetric halt-guard scope per §4.6)
- IF §9.2 option (c) chosen: `tools/r3_tl_derivation.py` — per-symbol TL anchor derivation (run BEFORE sweep; output reviewed by operator before sweep proceeds)
- Patches on `backtest.py` + `strategy/core.py` adding trend-pullback signal logic — gated behind config flag `trend_pullback_enabled` (default False); live path unchanged unless flag set
- Unit tests `tests/test_trend_pullback_signal.py`:
  - LONG entry condition (SMA50>SMA200 + pullback envelope)
  - SHORT entry condition (SMA50<SMA200 + regime-gated)
  - Regime detector gating on SHORT (BEAR=allowed, BULL/NEUTRAL=blocked)
  - SMA200 warmup skip (bar_idx < 200)
  - Tie-break with R1 SIGNAL_EXIT (still active at threshold=50)
  - Flag-off byte-identical regression test (extending `tests/test_backtest_refactor_parity.py`)
  - LRC entry disabled when `trend_pullback_enabled = True` (mutual exclusion test)
- Update `docs/superpowers/specs/es/2026-05-11-strategy-structural-audit.md` §10 history table with R3 outcome
- Update this pre-reg (`2026-05-13-r3-trend-pullback-pre-reg.md`) §14 history with execution + verdict
- PR comment with (a) verdict per §4, (b) updated prior estimate per §A.4 checkpoint (2-3 sentence Bayesian update with magnitude shift), (c) operator decision hooks per §4.5 (only relevant if INCONCLUSIVE or SUCCESS-CONDITIONAL — SUCCESS and FAIL are pre-locked), (d) IF R3 FAIL: initial draft communication outline for Simón per §1.1 hard-lock + kickoff Phase 4 spec.

**Live-path safety:** the trend-pullback branch in `strategy/core.py` is wrapped in `if cfg.get("trend_pullback_enabled", False):` — defaults to False. Production scanner (`btc_api.py`, `btc_scanner.py`) preserves current LRC entry behavior. R3 sweep runs with the flag set in harness only. NO changes to `config.defaults.json:trend_pullback_enabled` proposed in this pre-reg. Promotion to live is a separate operator decision (out of R3 scope per §7).

---

## §7 · What this pre-reg does NOT cover

- **Other R3 alternatives** (momentum-breakout, volatility-expansion) — single-alternative discipline per audit §A.6 + §1.1 hard-lock. If trend-pullback fails, **THAT IS the signal**.
- **H5 (basket re-validation)** — operator hard-locked NO H5 follow-up post-R3-FAIL per §1.1.
- **Cost model v2** — H8 mitigation deferred to issue #325 / cost model v2 epic. Not touched by R3.
- **`config.defaults.json` promotion of `trend_pullback_enabled`** — pre-reg only commits to derivation methodology + sweep. Promotion is post-R3-SUCCESS + separate operator decision (not auto from SUCCESS).
- **A.4-1 / A.4-1.5 / A.4-2 / A.4-3 holdout work** — issue #322 hard block remains. R3 does NOT touch holdout under any outcome. The integrated re-run (audit §A.5 step 4) on R3 SUCCESS uses pre-holdout window only.
- **R1 SIGNAL_EXIT modification** — kept active at fixed `lrc_exit_threshold = 50` per kickoff lock. NOT swept in R3. Frame mismatch acknowledged in §5.6 + §13 #3.
- **Score derivation review for trend-pullback** — pre-registered as uniform `SCORE_STANDARD = 2` for R3 sweep. Revisited only if R3 SUCCESS (integrated re-run audit §A.5 step 4 in scope).
- **Regime detector modification** — gating on SHORT preserved (BEAR=allowed); thresholds 60/40 preserved. Out of R3 scope per kickoff.
- **5m entry trigger modification** — bullish/bearish candle + RSI 5m direction match preserved. Trend-pullback inherits. Flag in §9 if operator wants to drop, but default is preserve.
- **R3 retry with different signal definition** — pre-reg is locked to ONE entry definition. Modifications to definition post-execution would invalidate pre-reg discipline.

---

## §8 · Pre-registered decision branches

Resumen de branch points donde la metodología tiene rule explícita:

| Branch point | Rule | Reference |
|---|---|---|
| Variant choice | Trend-pullback (single, no iteration) — operator-locked + audit §A.6 | §2.1 |
| Trend-pullback REPLACES LRC entry | YES (per audit §A.6 wording + §9.1 confirmation) | §2.2 |
| 5m entry trigger preservation | LOCKED active (operator §9.7 confirmation 2026-05-13 review) — no opt-out within R3 scope | §2.2 |
| Score for trend-pullback trades | Uniform `SCORE_STANDARD = 2` during R3 (1.0× sizing) | §2.2 |
| SIGNAL_EXIT (R1 mechanism) | Kept active, fixed threshold = 50 (midline) | §2.3 |
| TP estático | Kept active at per-symbol current values | §2.4 |
| TL anchor for trend-pullback | Pending §9.2 operator choice (a/b/c) — default (a) 36h uniform | §2.4 |
| PoV gates | UNCHANGED (decoupled per R2 pattern + issue #325 deferred) | §2.4 |
| Sweep grid | 5 × 3 × 5 = 75 cells (per §9.3) — default (a) | §2.5 |
| Sub-windows | A, B, C identical to R1+R2 | §3 |
| Cell exclusion | `n_trades < 10` → INSUFFICIENT_DATA | §4.1 |
| Cell selection | argmax(net_pnl) per (symbol, sub-window) subject to n_trades ≥ 10 | §4.1 |
| Deterministic tie-break | (net_pnl, -sl, -be, -pullback_distance) tuple key per issue #332 item 1 | §4.1 |
| Cross-window cell stability | Informative only, not gating | §4.4 |
| R3 SUCCESS criterion | Primary ✓ in 3/3 sub-windows (≥3 net_pnl > 0 AND avg PF > 1.2) | §4 |
| R3 SUCCESS-CONDITIONAL | Primary ✓ in 2/3 sub-windows — operator decides advance vs INCONCLUSIVE | §4.2 + §4.5 |
| R3 INCONCLUSIVE | Mechanism engages but cross-window cells diverge — operator decides hard-lock (default) vs override | §4.2 + §4.5 |
| R3 FAIL (signal degenerate) | <10 trades per cell for ≥6 of 8 symbols → automatic + halt H1 | §4.2 + §5.1 + §10.4 |
| R3 FAIL (clean) | Mechanism engages but profitability absent → automatic per §1.1 hard-lock | §4.2 + §1.1 |
| Halt-after-A H1 (signal degenerate) | ≥6 of 8 symbols <10 trades → halt B+C | §10.4 |
| Halt-after-A H2 (TL horizon mismatch) | ≥6 of 8 symbols TIME_LIMIT% > 50% on argmax cell → halt B+C | §10.4 |
| §4.6 asymmetric halt-guard | Favorable verdicts overridden under partial windows; negative preserved | §4.6 |
| `pullback_distance = 0.7` degenerate | Flag if `avg_trade_duration < 4h` dominates argmax | §5.2 |
| SMA200 warmup | Skip trend-pullback for `bar_idx < 200` | §5.3 |
| Live path safety | Flag-gated; defaults to False; no live promotion in R3 scope | §6 + §7 |
| R3 FAIL → escalation | Path (a) of #321 directly (NO H5, NO retry) — operator-locked | §1.1 + §4.5 |
| R3 SUCCESS → next step | Integrated re-run (audit §A.5 step 4) — automatic | §4.5 |

Cada branch tiene rule pre-registered ANTES de ver el data. Eliminates rationalización post-hoc.

---

## §9 · Open questions for operator

5 required confirmations + 1 optional before R3 execution begins:

### §9.1 — [REQUIRED] Confirm trend-pullback REPLACES LRC entry (not parallel)

Audit §A.6 wording is "Pre-registrar UN single alternative entry signal contra el LRC actual" — implies replacement, not parallel. Pre-reg §2.2 locks REPLACES. Alternative paths if you disagree:

- (a) **Trend-pullback REPLACES LRC entry** during R3 sweep. LRC signal disabled when `trend_pullback_enabled = True`. **Recommended; pre-reg locked here.** Default.
- (b) Trend-pullback ADDED as parallel signal — both LRC and trend-pullback can fire concurrently. Increases trade frequency potentially 2× but confounds the question "which signal frame works?". Requires re-drafting §2.2 + §4 success criterion (joint evaluation).
- (c) Trend-pullback PRIMARY + LRC FALLBACK (only fires if trend-pullback doesn't fire in current bar). Operationally complex, methodologically less clean.

### §9.2 — [REQUIRED] Confirm TL anchor for trend-pullback frame

Per §2.4 — mean-reversion 5h anchor does NOT apply to trend-pullback. Three options:

- (a) **Conservative uniform 36h** — auditor recommendation. Defensible vs RW theory (covers ~2.7 ATR target horizon). Default.
- (b) Conservative uniform 48h — covers up to ~3.1 ATR; lower trade frequency potentially under TL-dominated cooldown.
- (c) Per-symbol empirically derived — `tools/r3_tl_derivation.py` runs first in Phase 3; produces tailored TL per symbol clamped `[12, 48]`. +30-45 min derivation compute. Output (`tl_distributions.json`) reviewed by operator BEFORE sweep proceeds.

### §9.3 — [REQUIRED] Confirm sweep grid dimensionality

Per §2.5 — proposed 5 × 3 × 5 = 75 cells. Alternatives:

- (a) **75 cells (proposed)** — `atr_sl_mult ∈ {0.5, 0.7, 1.0, 1.5, 2.5} × atr_be_mult ∈ {1.5, 2.0, 2.5} × pullback_distance ∈ {0.3, 0.4, 0.5, 0.6, 0.7}`. **Default.**
- (b) 105 cells — extend SL to 7 values `{0.5, 0.7, 1.0, 1.2, 1.5, 2.0, 2.5}`.
- (c) 45 cells — reduce SL to 3 values `{0.5, 1.0, 2.5}` (focus on extremes + middle).
- (d) 125 cells — extend BE to 5 values `{1.5, 1.75, 2.0, 2.25, 2.5}`.

Compute estimate scales ~linearly with cell count (per §11).

### §9.4 — [REQUIRED] Tooling-debt closure: separate PR or bundled with R3 implementation

Per issue #332: items 1, 2, 5 (and possibly 3, 4) appear to be addressed in commit `4b2b763` on the `feat/r1-tooling-debt-2026-05-12` worktree branch — **but the PR has not been opened**. Two paths:

- (a) **Open the tooling-debt PR standalone** based on commit `4b2b763` BEFORE R3 implementation begins. R3 implementation then rebases on the merged tooling-debt and applies the same patterns to `tools/r3_*_sweep.py` + `tools/r3_verdict.py`. **Recommended.**
- (b) **Bundle tooling-debt with R3 implementation PR.** Rebase commit `4b2b763` onto R3 branch, then add R3 code on top. Single PR closes both #332 (in full) and ships R3 — but mixes review attention.

Auditor recommendation: (a) — cleaner review boundaries, tooling-debt is a self-contained quality-bar improvement that benefits multiple downstream tools (R3 sweep + verdict + future sweeps).

### §9.5 — [REQUIRED] Score derivation for trend-pullback trades

Per §2.2: trend-pullback signal has different inputs than LRC, so existing `evaluate_signal` 0-9 score does NOT apply unchanged. Options:

- (a) **Uniform `SCORE_STANDARD = 2`** → 1.0× sizing — auditor recommendation. Neutral test; eliminates score confounding within R3. **Default.**
- (b) Uniform `SCORE_PREMIUM = 4` → 1.5× sizing. Aggressive on untested signal; can mask edge OR amplify losses (K=10 cap bounds catastrophic case per CLAUDE.md).
- (c) Uniform `SCORE = 0 or 1` (SCORE_HALF) → 0.5× sizing. Defensive; under-tests the signal at production-equivalent sizing.
- (d) Adapt scoring logic for trend-pullback (e.g., SMA50/200 spread + RSI confirmation + ATR percentile) — scope creep; confounds signal-frame question with score-derivation question.

If operator picks (d), this pre-reg needs §2.2 re-draft before execution.

### §9.6 — [OPTIONAL] Branch placement + compute budget hard cap

- **Branch placement:** new branch off `main` (post-#331 merge state, current HEAD `46b34e3`). **Default.** No stacking needed if §9.4 (a) chosen.
- **Compute budget hard cap:** R3 sweep estimate (per §11) ~3-4h paralelizado. If runtime exceeds 4h wall-clock, abort + diagnose (per R1 pre-reg §9.5 pattern). Default: yes, with progress checkpoint at sub-window boundary.

---

## §10 · Pre-execution math sanity check

**Algebraic reasoning + empirical halt conditions (mirroring R1 §10 pattern; R2 §2.2 PoV-decouple-style algebra not applicable here because trend-pullback signal frequency is empirically determined).**

### §10.1 — TL anchor justification under random-walk approximation

Mean-reversion frame anchor (R2 §3 derivation): median time-to-1-ATR ≈ 5h on 1H bars.

Random-walk approximation (R2 §6 invocation of the standard property; not derived from first principles in that section): `time-to-N-ATR ≈ N² × time-to-1-ATR`.

| N (target ATR multiples) | RW-approximated time | Use case |
|---|---:|---|
| 1 | 5h | Mean-reversion (R1 frame) |
| 2 | 20h | Modest trend continuation (low-end TP target per `atr_tp_mult` ∈ {2, 3, 4, 5, 6}) |
| 2.7 | ≈ 36h | **Conservative trend-pullback target** (auditor recommendation, §9.2 option a) |
| 3 | 45h | Decent trend continuation (mid-low TP target) |
| 3.1 | ≈ 48h | Upper bound (§9.2 option b; operational frequency limit per R2 §2.1 clamp) |
| 4 | 80h | R2 §6 default momentum anchor — but **above operational cap** (see "Why 36h, not 80h" below) |

**Why 36h, not 80h** (justification for downgrading from R2 §6's stated 4-ATR / 80h anchor):

1. **Strategy TP target spectrum.** `atr_tp_mult` per-symbol currently in `config.defaults.json:symbol_overrides` clusters in {2, 3, 4} for most symbols. Trend-pullback's natural target is the *first leg* of trend continuation post-pullback — typically 2-3 ATR, not the full 4-ATR RW horizon. 2.7 ATR (≈36h) is the geometric midpoint of the 2-3 ATR low-end target band, where the strategy is most likely to capture the move profitably. 4 ATR represents a full complete trend leg, closer to the holding ceiling than the typical trade target.

2. **§10.4 H2 halt would almost certainly fire under TL=80h.** Under 91-day sub-windows (2,184 bars), a TL=80h cap is loose enough that TIME_LIMIT exits would dominate other exit reasons for most symbols — the `TIME_LIMIT% > 50%` halt condition (§10.4 H2) would trigger on ≥6 of 8 in-data symbols in window A, halting B+C before R3 can produce a discriminating verdict. Choosing TL=36h preserves test viability: H2 fires only if the *strategy* (not the TL choice itself) is the binding constraint.

Both arguments are defensible independently; together they form the rationale for the 36h preference. Option (c) per-symbol empirical derivation (§9.2) refines per symbol around this same horizon — typically clustering 24-48h depending on volatility profile.

**Caveat:** RW approximation assumes Brownian motion with no drift. Trend-following dynamics have non-zero drift (trends compound). Empirical per-symbol derivation (§9.2 (c)) corrects for this; uniform values don't.

### §10.2 — Signal frequency plausibility (algebraic upper bound)

For trend-pullback signal to fire ≥10 times per (symbol, sub-window):
- `SMA50 > SMA200` (or vice versa) must hold for non-trivial fraction of bars.
- Within those bars, price must touch SMA20 ± 0.5 ATR envelope.

Both are empirical questions answerable only by running the indicator computation on actual data. **Pre-registered as Phase 3 first-step diagnostic:** `signal_diagnostics.json` reports per-symbol signal-firing count BEFORE the full sweep launches. Operator can review and override if signal degenerate is evident pre-sweep.

**Algebraic upper bound for sanity (informative, not pre-registered as condition):** if SMA50 > SMA200 holds 50% of bars (random) and price is within SMA20 ± 0.5 ATR for 50% of those bars (random walk distribution around SMA20), trend-pullback fires on 25% of bars. Over a 91-day sub-window (2,184 bars; ~1,984 post-SMA200-warmup for in-data symbols), that's ~496 signal-eligible bars per symbol. With cooldown + TL gates filtering, realized trade count would be far lower (perhaps 30-100 trades). **Plausibility passes:** signal should fire frequently enough under random-walk assumptions to produce ≥10 trades.

**Honest caveat:** random walk assumption may break down. Actual data may have:
- Long sideways regimes where SMA50 ≈ SMA200 (signal frame never triggers) — penalizes signal frequency.
- Sustained trends without pullbacks (SMA50 > SMA200 but price never touches SMA20 envelope) — penalizes signal frequency.
- Pullbacks too steep (price overshoots SMA20 ± 0.5 ATR repeatedly) — inflates frequency under loose `pullback_distance = 0.7`, may be degenerate.

§10.4 halt H1 catches the first two; §5.2 catches the third.

### §10.3 — Mechanism plausibility for edge

For trend-pullback to produce edge in this basket (10 curated symbols that already FAILED under LRC mean-reversion frame in R1):

**Coexistence hypothesis:** trend behavior and mean-reversion behavior can coexist in the same symbol in different regimes. R3 tests whether trend-pullback engages in sub-windows where mean-reversion didn't. Some symbols may "be momentum" while others "are mean-reversion".

**Honest acknowledgment:** the basket was curated under epic #135 using a contaminated simulator (per audit §A.2 H5 caveat). The curation criteria may have selected for symbols with phantom-LRC-edge that doesn't generalize to other signal frames either. Even with trend-pullback signal, the basket may still be unfavorable.

**Per audit §A.6 prior (refined post-R1+R2 FAIL stack):** R3 SUCCESS conditional probability ~12-18%. The audit's pre-R1 R3 prior was ~25-35% conditional on R1+R2 succeeding (audit §A.4 probability tree); with R1+R2 both FAIL the residual uncertainty about whether ANY structural lever works is reduced, and R3-specific prior settles at ~12-18% per §12 detailed reasoning.

**Verdict of §10 math sanity check: PROCEED.** Algebraic TL anchor is sound (any choice in {36h, 48h, per-symbol} produces defendible horizon). Empirical signal frequency check stages as Phase 3 first-step with explicit halt rule. R3 SUCCESS prior >5% — compute is justified.

### §10.4 — Halt conditions pre-registered

**Halt condition H1 (signal degenerate):** during sub-window A execution, if ≥6 of 8 in-data symbols have <10 trades on their argmax cell (over the 75-cell grid), halt before B+C. Mechanism fails to engage — signal-firing too rare in this basket. Mirror R1 §10 halt-after-A pattern.

**Halt condition H2 (TL horizon mismatch):** during sub-window A execution, if ≥6 of 8 in-data symbols have `TIME_LIMIT% > 50%` on their argmax cell, halt before B+C. TL choice (per §9.2) is too short for trend-pullback frame — trades close on time before capturing trend continuation.

**Either halt → write halt diagnostic.** `data/retune/2026-05-13-r3-trend-pullback/halt_after_a_diagnostic.json` with full per-symbol breach explanation. Verdict classifies as `R3_FAIL` (H1 → signal degenerate; H2 → clean) per §4.2. Path (a) of #321 escalation per §1.1.

**Pre-reg §10 most-likely causes (mirroring R1 §10):**
- H1 fires from low SMA50/200 trend coherence in the basket (curation contaminated per audit §A.2).
- H2 fires from RW assumption breaking down (actual trend dynamics differ from RW); operator may consider §9.2 (c) per-symbol derivation in retrospect — but R3 itself is hard-locked to FAIL per §1.1.
- Neither fires + primary criterion still ✗: signal engages but doesn't produce edge → R3 FAIL (clean) per §4.2.

The §10 halt-guard scope (§4.6 amendment) preserves R3 FAIL on partial-window dispositive evidence per asymmetric guard — no SUCCESS rationalization possible from partial data.

---

## §11 · Compute estimate

| Stage | Estimate | Notes |
|---|---|---|
| §9.2 option (c) TL derivation (if chosen) | 30-45 min | `tools/r3_tl_derivation.py` runs sequentially on 10 symbols |
| Code patch (`backtest.py` + `strategy/core.py` trend-pullback signal + flag) | 3-4 h | Includes ~10 unit tests + flag-off byte-identical regression |
| Sweep harness (`tools/r3_trend_pullback_sweep.py`) | 1-2 h | Adapted from `tools/r1_signal_exit_sweep.py` pattern + tooling-debt items applied |
| Verdict tool (`tools/r3_verdict.py`) | 1 h | Adapted from `tools/r1_verdict.py` + §4.6 halt-guard scope mirror |
| Baseline backtests (30 = 10 sym × 3 sub-win) | 15 min | Sequential, single config each |
| Sweep execution (2,250 backtests, parallelized 8 workers) | **3-4 h wall-clock** | Per-backtest avg ~30s; mirror R1 |
| Derivation audit + JSON outputs + README + PR comment | 1-2 h | Math/data interpretation + Bayesian update + decision hooks |
| **Total session compute time** | **~10-13 h** | Single contiguous session OR split across 2 sessions (per kickoff estimate) |

**Halt-conditional savings:** if sub-window A halt fires per §10.4 (H1 or H2), total drops to ~5-6 h (no B+C sweeps + simpler derivation_audit + FAIL path).

**If §9.4 (a) chosen (standalone tooling-debt PR first):** add ~2-3 h for tooling-debt PR review cycle (multi-agent review per #332 acceptance criteria) BEFORE R3 implementation starts.

**Comparison with R1:** R1 was 2,250 backtests in ~19 min wall-clock (per R1 derivation_audit §11 — was faster than estimate due to halt). R3 is identical magnitude → same wall-clock estimate as R1's pre-execution forecast (~3-4h paralelizado). Per-cell complexity comparable.

---

## §12 · Auditor prior on R3 outcome

**Auditor (Claude Opus 4.7) prior before execution:**

| Outcome | Probability | Reasoning |
|---|---:|---|
| **R3 SUCCESS (primary ✓ in 3/3 sub-windows)** | ~8-10% | Requires trend-pullback to materially beat LRC in 3+ symbols across 3 regimes. High bar given basket contamination (audit §A.2) + H1 + H8 persistence. |
| **R3 SUCCESS-CONDITIONAL (primary ✓ in 2/3)** | ~5-8% | Regime-dependent edge possible — sub-window C (recent) most likely to show momentum coherence; sub-window A (bear 2022) least likely. |
| **R3 INCONCLUSIVE (mechanism engages, cross-window diverges)** | ~15-20% | Signal fires, some symbols positive in some sub-windows, but cells diverge enough to cast doubt on generalization. Hard-lock §1.1 still applies under §4.5 default. |
| **R3 FAIL (signal degenerate)** | ~5-10% | SMA50/200 trend rarely coherent in this basket OR pullback envelope rarely fires. Possible if basket was curated under noise rather than trend coherence. |
| **R3 FAIL (clean — mechanism engages, no profitability)** | ~50-60% | Most likely. Trend-pullback fires sufficiently but H1+H8+basket contamination together produce -EV per-trade similar to LRC. Mirror R1 framing: "mechanism engaged, profitability absent". |

**Joint prior:** ~13-18% R3 SUCCESS-or-CONDITIONAL. Below operator's pre-R1 prior of 25-35% by ~10-20pp due to R1+R2 FAIL stack reducing residual uncertainty.

**Operator's prior (kickoff): 12-18%.** Aligned with auditor. Gap analysis: no material disagreement.

**Bayesian update plan post-R3:**
- R3 SUCCESS (3/3): P(viable) jumps to ~30-50%; advance to integrated re-run (audit §A.5 step 4).
- R3 SUCCESS-CONDITIONAL (2/3): P(viable) ~15-25%; operator decides per §4.5.
- R3 INCONCLUSIVE: P(viable) drops to ~3-5%; default §4.5 hard-lock activates.
- R3 FAIL (any flavor): P(viable) drops to ~2-4%; §1.1 hard-lock → path (a) of #321 automatic.

**§A.4 prior re-evaluation checkpoint:** post-R3 PR comment must include explicit Bayesian update with magnitude shift documented in 2-3 sentences. Same pattern as R2 + R1 per audit §A.4.

**Agent tooling note (added 2026-05-15).** The 2-3-sentence prose update is the default §A.4 mechanic. If the operator wants the post-R3 update materialized as a formal posterior — beta-binomial over P(viable) given R3 verdict + R1/R2 stack, hierarchical model across símbolos × sub-window × cells in the 75-cell sweep, or LOO/WAIC between {R3 SUCCESS 3/3, R3 SUCCESS-CONDITIONAL 2/3, R3 INCONCLUSIVE, R3 FAIL clean, R3 FAIL degenerate} verdicts — invoke the `pymc-bayesian-modeling` skill (installed 2026-05-15, available via the `Skill` tool). PyMC + NUTS + LOO/WAIC + posterior predictive checks ship with the skill. Default remains prose-only; PyMC is on-demand.

---

## §13 · Methodology limitations carried forward

Per audit §A.2 + §A.7 + §A.8 + R1 §13 + R2 §6, R3 inherits these caveats:

1. **H1 (signal expectancy gap) persists if trend-pullback fails too.** R3 tests if a different signal frame has edge. If not, H1 is confirmed at the level of "no retail signal frame has edge in this basket". This is the substantive R3 hypothesis under test.

2. **H8 (cost model v1) untouched.** Trend-pullback entries pay same per-trade cost as LRC entries. Slippage destruction in thin-liquidity bars persists. Issue #325 unchanged.

3. **SIGNAL_EXIT frame mismatch (LRC exit on momentum entry).** Per §2.3 + §5.6: R1's mean-reversion-anchored exit applied to trend-pullback's momentum-anchored entry. Operator-locked per kickoff. Verdict interpretation MUST acknowledge — if R3 INCONCLUSIVE, the mismatch is a candidate cause but does NOT void §1.1 hard-lock per §4.5.

4. **TL anchor uncertainty for momentum frame.** Even with empirical per-symbol derivation (§9.2 option c), the "right horizon" for capturing trend continuation is uncertain. RW approximation may underestimate (trends compound faster) or overestimate (consolidation periods interrupt continuation). §10.4 halt H2 detects horizon mismatch.

5. **Basket curated under contaminated simulator (audit §A.2 H5 caveat).** Selection by epic #135 used pre-#223 simulator. The 10 curated symbols may NOT generalize to "all crypto majors with trend-pullback edge". H5 escalation is hard-locked NO per §1.1 — but the conceptual caveat is acknowledged here for forensic understanding of any R3 FAIL.

6. **Per-symbol bankruptcy halt (#313) reduces noise** but cannot manufacture positive expectancy where there is none. Some symbols may bankrupt before generating sufficient evidence (5-10 trades). Trend-pullback may or may not change bankruptcy frequency vs LRC baseline.

7. **Score uniformity for trend-pullback during R3.** Per §2.2 + §9.5: all trend-pullback trades get `SCORE_STANDARD = 2` → tier-multiplier 1.0×. Eliminates score-related confounding within R3 but means R3 SUCCESS doesn't validate "trend-pullback + variable scoring works"; only "trend-pullback at standard sizing works". Integrated re-run (audit §A.5 step 4) revisits score derivation if R3 SUCCESS.

8. **Regime detector unchanged.** SHORT gating preserved (BEAR=allowed); thresholds 60/40 preserved. R3 does NOT test if regime-aware long-only would change R3 verdict. Out of scope.

9. **5m entry trigger unchanged.** Bullish/bearish candle + RSI 5m direction match preserved. Trend-pullback inherits this. If 5m trigger is methodologically misaligned with momentum frame (e.g., requires too-tight intra-bar confirmation), that's a structural issue not addressed by R3.

10. **Pre-reg single-iteration discipline.** R3 is locked to one entry definition + one execution. If results are ambiguous, no "R3.5" or "R3 v2" — operator decides per §4.5 (default hard-lock) or escalates per §1.1.

---

## §14 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-13 | Pre-reg sub-spec inicial — drafted from kickoff prompt + R1+R2 derivation_audits + structural audit context + §A.4-§A.8 amendments | Claude Opus 4.7 (sesión kickoff post-R1+R2 FAIL) + sssamuelll |
| 2026-05-13 | Incorporated PR #333 review feedback (multi-agent code-reviewer + comment-analyzer): §10.1 RW math justification for 36h preference over R2 §6's 80h anchor (I-1); §4.2 verdict tree restructured for explicit asymmetry rationale (I-2), per-window denominator clarification (I-3), priority between SUCCESS-CONDITIONAL vs INCONCLUSIVE on 2/3 (Minor 1), and fall-through mapping for intermediate engagement 3-5 of in-data (Minor 2); §2.2 5m entry trigger LOCKED active per §9.7 confirmation; citation fixes (CCM-1 `backtest.py:935-940`, CCM-2 "invocation" not "derivation", CCE-1 "exclusive" not "−1 day") | sssamuelll + Claude Opus 4.7 |
| 2026-05-13 | R3 execution complete. Code: signal logic + harness + verdict tool (3 commits). Sweep: 30 baselines + 2,250 backtests (3 sub-windows × 10 symbols × 75 cells) in ~14+15+13 min wall-clock per window. **Verdict: R3_FAIL (clean)** — primary FAIL 3/3 windows, 0/22 engaged-symbol-window pairs positive, cells diverge wildly per §4.4. §10.4 halt NOT fired (mechanism engaged 8/5/9 of in-data, TL appropriate). Joint posterior P(viable strategy) ~12-18% → ~2-4% per §A.4. §1.1 hard-lock → path (a) of #321 escalation. Full audit at `data/retune/2026-05-13-r3-trend-pullback/derivation_audit.md` | sssamuelll + Claude Opus 4.7 |

Reservar líneas para iteración post-operator-review en §9.
