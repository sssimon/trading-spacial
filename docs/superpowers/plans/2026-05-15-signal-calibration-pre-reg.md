# Pre-registration sub-spec — Epic C (signal calibration) Phase 2 + Phase 3 + Phase 4

**Fecha:** 2026-05-15
**Status:** DRAFT — pre-registration ANTES de cualquier execution. Operator review desired before Phase 1 implementation work begins.
**Autor:** Claude Opus 4.7 en colaboración con sssamuelll
**Tipo:** pre-registration sub-spec — fija metodología antes del diagnostic + sweep + walk-forward
**Trigger:** Epic C spec doc (PR #352 mergeada 2026-05-15) + 6 operator decisions Q-PR1..Q-PR6 locked vía 2 rounds de AskUserQuestion 2026-05-15
**Cierre objetivo:** Phase 3 verdict (PHASE_3_A_PASS / A_INTERVENTION_INSUFFICIENT / A_INTERVENTION_HARMFUL / DIAGNOSTIC_B_DETECTED / DIAGNOSTIC_AMBIGUOUS / PHASE_3_INSUFFICIENT_DATA) per §4 + Phase 4 walk-forward sobre Windows B + C condicional on Phase 3 PASS
**Tracking issue:** #350 (parent epic: #338)

---

## §0 · Lectura mínima requerida

Antes de revisar este pre-reg, leer en este orden (≈45 min):

1. `docs/superpowers/specs/es/2026-05-15-epic-signal-calibration.md` — epic C spec doc completo, especialmente §0 (boundaries), §4 (architecture), §6 (success criteria), §7 (phases), §8 (Q1-Q6 locked), §9 (tension table con #338 §8)
2. `docs/superpowers/specs/es/2026-05-15-h-signal-hypothesis-pullup.md` — scoping doc original (H-signal split en sub-A dilution + sub-B no-firing-enough, 6 open questions originalmente surface-eadas)
3. `data/retune/2026-05-14-regime-allocation/derivation_audit.md` — Phase 3 #338 verdict + audit §2 mechanism + §7 hypothesis table + §8 operator decision branches (trigger de epic C)
4. `docs/superpowers/specs/es/2026-05-13-epic-regime-allocation-strategy-pivot.md` — parent epic #338 spec; §8 locked decisions (heredables en epic C salvo §8.1 + §8.4 que se BREAK con justification)
5. `docs/superpowers/plans/2026-05-14-regime-allocation-phase2-pre-reg.md` — Phase 2 pre-reg de #338 (pattern source para este pre-reg)
6. CLAUDE.md "Regime-allocation strategy class (epic #338, post-Phase 1)" + "Validation Methodology — Holdout Dataset" sections

Quien ya leyó esos 6 puede saltar a §1.

---

## §1 · Contexto y alcance

### §1.1 — Trigger inmediato

PR #352 mergeada 2026-05-15 (squash commit `00d6997` en main) cierra Phase 0 deliverable #2 of 3 de epic C (signal calibration). El epic C spec doc lockea Q1-Q6 vía 2 rounds de AskUserQuestion + tension table con #338 §8 locks + decisión tree de phases. Este pre-reg es **Phase 0 deliverable #3 of 3** — closes Phase 0 (scoping → spec → pre-reg) y enables Phase 1 implementation work (observability instrumentation).

**Estado del epic C post-Phase-0:**
- ✅ Phase 0 deliverable #1 (scoping doc `2026-05-15-h-signal-hypothesis-pullup.md`) — mergeada (PR #351 squash `e5d6438`)
- ✅ Phase 0 deliverable #2 (epic spec doc `2026-05-15-epic-signal-calibration.md`) — mergeada (PR #352 squash `00d6997`)
- 🔄 **Phase 0 deliverable #3 (este pre-reg)** — en redacción
- ⏸️ Phase 1 (observability instrumentation) — bloqueado por este pre-reg
- ⏸️ Phase 2 (diagnostic pass Window A) — bloqueado por Phase 1 merge
- ⏸️ Phase 3 (A-set intervention sweep Window A + sensitivity vol_target) — bloqueado por Phase 2 verdict ≠ halt
- ⏸️ Phase 4 (walk-forward Windows B + C) — bloqueado por Phase 3 PASS
- ⏸️ Phase 5 (holdout JOINT con epic D) — bloqueado por Phase 4 + epic D Phase final + Q5 lock

**Operator-locked decisions de este pre-reg** (vía 2 rounds de AskUserQuestion 2026-05-15):

| ID | Pregunta | Resolución LOCKED |
|---|---|---|
| Q-PR1 | Counts threshold per short-lookback (N=5, N=10, N=20) for A-evidence | **T=5 firings (LONG+SHORT, no FLAT) per short-lookback** |
| Q-PR2 | Magnitude metric for dilution detection | **p50(\|sum\|) < 2 over Window A 91 days** |
| Q-PR3 | Win_rate floor for §6.1 PRIMARY criterion | **30% over closed trades, excluding SIM_END open positions** |
| Q-PR4 | H-C4 halt: win_rate degradation threshold | **win_rate intervention < 50% del equal-weight baseline** |
| Q-PR5 | Compute budget hard cap | **No hard cap (matches #338 §9.3 default)** |
| Q-PR6 | A-set default intervention if A_DETECTED | **A1 — subset {5, 10, 20} only** |

### §1.2 — Alcance del pre-reg

**Hace:**
- Lockea la operationalización completa del Phase 2 diagnostic + Phase 3 sweep + Phase 4 walk-forward ANTES de cualquier compute o code implementation.
- Carry forward los 6 locked decisions Q1-Q6 del epic C spec §8 + los 7 heredados del #338 §8 (con §8.1 + §8.4 marcados como BREAK con justification — see §9 tension table).
- Pre-registra diagnostic→intervention mapping table (Phase 2 verdict → Phase 3 action) explícitamente.
- Pre-registra halt conditions H-C1 / H-C2 / H-C3 / H-C4 con thresholds numéricos concretos.
- Pre-registra cell selection rule, tie-break determinism, asymmetric halt-guard scope mirror #338 §4.6 + R3 §4.6.
- Pre-registra observability output schema (counts + magnitudes per-lookback) heredando Q1 lock del epic C §8.1.
- Aplica pre-execution math sanity check: warmup math, signal frequency lower bound, threshold algebraic justification.
- Pre-registra auditor prior P(A_DETECTED vs B_DETECTED vs AMBIGUOUS) + Bayesian update plan post-Phase-2 + post-Phase-3.

**No hace:**
- No ejecuta nada todavía. Phase 1 (observability instrumentation code) y Phase 2/3/4 execution son separate PRs solo si operator approves este pre-reg.
- No modifica `config.defaults.json`, `backtest.py`, `strategy/core.py`, `strategy/donchian_ensemble.py`, `strategy/vol_targeting.py`, ni cualquier code path. Pre-reg only.
- No re-litiga las 6 decisiones locked en epic C §8 (Q1-Q6). Esas son hardcoded carry-forward.
- No re-litiga las 5 decisiones del #338 §8 que PRESERVE (§8.2/§8.3/§8.5/§8.6/§8.7). Solo §8.1 + §8.4 se BREAK con justification cited en §9.
- No toca holdout (issues #246 + #322 hard blocks remain hasta Phase 5, condicional joint con epic D outcome per Q5 lock).
- No promueve `cfg.regime_allocation.enabled` o features nuevos to production.
- No diseña Phase 5 (holdout) — out of scope per epic C §7, condicional joint con epic D.
- No re-evalúa basket (epic D scope, requires basket-unlock decision per #338 §4.1).

### §1.3 — Iteración

Esta es la **primera iteración** del Phase 2/3/4 methodology de epic C. Está abierta a operator pushback en §9. Mirror del single-iteration discipline del #338 Phase 2 pre-reg §1.3.

---

## §2 · Methodology

### §2.1 — Signal: Donchian ensemble heredado + observability layer

**Definición carry-forward del epic C spec §4.2 + heredada del #338 §8.1 + §8.4 baseline:**

- Lookbacks: `ZARATTINI_LOOKBACKS = (5, 10, 20, 30, 60, 90, 150, 250, 360)` días — implementado en `strategy/donchian_ensemble.py:ZARATTINI_LOOKBACKS`.
- Per-lookback signal: `-1` (SHORT — close < lower(t-1)), `+1` (LONG — close > upper(t-1)), `0` (no breakout, sticky to previous direction).
- Aggregation baseline (Phase 2 diagnostic): equal-weight vote, position direction = `sign(sum)`, flat si sum=0. Confidence = `|sum| / 9` ∈ [0, 1].
- Channels computados sobre **daily aggregated bars** (resampled from 1H OHLCV via Pandas `freq="D"`).
- Warmup mínimo: **390 daily bars** (longest lookback 360 + vol window 30). Heredado del #338.

**NEW per epic C spec §4.2 + Q-PR locks — observability layer:**

Por cada (símbolo, sub-window) cell, instrumentation emite metrics per lookback N ∈ {5, 10, 20, 30, 60, 90, 150, 250, 360}:

```json
{
  "lookback_days": 5,
  "count_long": <int>,        // count of bars con signal[N] = +1
  "count_short": <int>,       // count of bars con signal[N] = -1
  "count_flat": <int>,        // count of bars con signal[N] = 0
  "firing_count": <int>,      // = count_long + count_short
  "magnitude_mean": <float>,  // mean of |sum| values
  "magnitude_std": <float>,
  "magnitude_p50": <float>,
  "magnitude_p95": <float>
}
```

Plus aggregate metrics per cell:
```json
{
  "sum_distribution": {
    "mean": <float>,
    "std": <float>,
    "p25": <float>,
    "p50": <float>,
    "p75": <float>,
    "p95": <float>,
    "bars_total": <int>
  }
}
```

**Bar-by-bar log opcional** behind flag `observability_bar_by_bar` (default `False`). Emits `data/retune/<date>-signal-calibration/observability_bar_by_bar_<symbol>_<window>.csv` (~5-15MB per cell when enabled). NOT used in Phase 2 diagnostic verdict logic — diagnostic only on aggregated metrics. Bar-by-bar log es forensic-only post-hoc.

**A-set intervention space (Phase 3, locked per epic C spec §4.2 + Q-PR6):**

Por Q-PR6 lock, the **default A-set intervention** corrida en Phase 3 IF Phase 2 verdict = A_DETECTED es:

- **A1 — Subset lookbacks {5, 10, 20}**: restrict ensemble a 3 lookbacks. Position direction = `sign(sum_subset)` over 3 lookbacks instead of 9. Most direct test of dilution hypothesis: removes long-lookback flat votes that diluted the aggregate. Falsifiable clean: si A1 produce `n_trades ≥ 5` + win_rate ≥ 30%, dilution era el problema; si A1 FAIL, intervention space A está exhausted (epic C verdict `A_INTERVENTION_INSUFFICIENT`).

A2/A3/A4 (weighted aggregation / threshold-flip / hybrid) NOT runs por default. Operator override path per §4.5 self-policing requirement (heredar del #338 Phase 2 pre-reg).

**No-touched de este pre-reg:** los 9 lookbacks del baseline, equal-weight aggregation baseline, sticky direction logic, warmup math, daily resampling convention están locked. Phase 2 NO los varía (baseline = equal-weight Donchian-9 unchanged). Phase 3 SOLO varía la aggregation rule (A1 subset si A_DETECTED).

### §2.2 — Sizing: vol-targeting heredado (locked per epic C spec §4.3 + heredado #338 §2.2 Path B)

**Definición operacional (idéntica al #338 Phase 2 pre-reg §2.2):**

```
target_vol_per_symbol = portfolio_vol_target          # single-symbol scope, n_active=1
position_size_usd = capital × target_vol_per_symbol / realized_vol_30d_annualized
```

Donde:
- `portfolio_vol_target = 0.30` para primary pass; ∈ {0.25, 0.30, 0.35, 0.40} para sensitivity sweep.
- `realized_vol_30d_annualized = std(daily_log_returns[-30:]) × sqrt(365)`.
- `capital` = per-symbol stream capital (each símbolo runs independent $10K stream).

**Path B heredado del #338 (single-symbol scope; cross-symbol n_active coordination NOT implemented).** Position sizes bounded by hard caps:

- `position_size_usd ≤ 0.20 × capital` per símbolo (`cfg.regime_allocation.max_position_pct = 0.20`)
- `position_size_usd ≥ 50.0` (Binance min, `cfg.regime_allocation.min_position_usd`)
- `sum(|position_size_usd|) ≤ 2.0 × capital` per símbolo (leverage cap effective per-stream)

**R-multiple sizing está estructuralmente eliminado** (heredar del #338 + epic C spec §4.3).

### §2.3 — Exits: signal-based heredado (locked per epic C spec §4.4 + heredado #338 §2.3)

Exit triggers idénticos al #338 Phase 2 pre-reg §2.3:
1. **`SIGNAL_FLIP`** — ensemble vote cambia sign.
2. **`SIGNAL_EXIT`** — ensemble vote a flat (sum=0).
3. **`BANKRUPT`** — equity ≤ `BANKRUPTCY_THRESHOLD = 0.1 × INITIAL_CAPITAL` ($1000).
4. **`SIM_END`** — fin de sub-window evaluation.

**NO SL/TP/TIME_LIMIT** (LRC-specific, structurally disabled under `cfg.regime_allocation.enabled = True`).

Bankruptcy halt (#280 + #313) + K-cap (#309) preserved.

### §2.4 — Cost model: v2 sqrt-participation + funding heredado (locked per epic C spec §4.5 + Phase 0 PR #341)

Idéntico al #338 Phase 2 pre-reg §2.4. Anchored a Almgren-Chriss 2001 + Donier-Bonart 2015 + Tóth et al 2011 per `costs_calibration.json`.

**Phase 2/3/4 NO modifica cost model.** Carry-forward.

### §2.5 — Sweep grid: Phase 2 diagnostic + Phase 3 primary + Phase 3 sensitivity + Phase 4 walk-forward

**Phase 2 — Diagnostic pass:**

```
cells_submitted = 8 in-coverage símbolos × 1 sub-window (A) × 1 vol_target (30%) × 1 config (baseline equal-weight Donchian-9) = 8 cells
cells_running   = 8 (Window A coverage: BTC, ETH, ADA, AVAX, DOGE, UNI, XLM, RUNE; PENDLE+JUP excluded por warmup-fail heredado del #338 §5.1)
```

Output: `data/retune/2026-05-15-signal-calibration/phase2_diagnostic.json` + observability sidecar JSON per cell + `phase2_verdict.json`.

**Phase 3 — A-set intervention sweep (primary + sensitivity):**

Solo runs SI Phase 2 verdict = `A_DETECTED` (per §4.2 mapping table). Otherwise halt per §4.5.

```
Primary pass: 8 símbolos × 1 sub-window (A) × 1 vol_target (30%) × 1 config (A1 subset {5,10,20}) = 8 cells
Sensitivity pass: 8 símbolos × 1 sub-window (A) × 4 vol_target × 1 config (A1) = 32 cells
Total Phase 3: 40 cells (all running, no NO_DATA exclusions beyond Window A heredados)
```

Output: `data/retune/2026-05-15-signal-calibration/sweep_primary_A.json`, `sweep_sensitivity_A.json`, `verdict.json`.

**Phase 4 — Walk-forward Windows B + C (conditional on Phase 3 PASS):**

Solo runs SI Phase 3 verdict = `PHASE_3_A_PASS` (per §4.3 verdict table).

```
Walk-forward primary: (8 in B + 9 in C) × 2 sub-windows × 1 vol_target (30%) × 1 config (A1) = 17 cells
Walk-forward sensitivity: 17 × 4 vol_target = 68 cells
Total Phase 4: 85 cells
```

Output: `data/retune/2026-05-15-signal-calibration/walkforward_primary_{B,C}.json`, `walkforward_sensitivity_{B,C}.json`, `walkforward_verdict.json`.

**Total compute (worst case si todo el camino llega a Phase 4 PASS):**
- Phase 2: 8 cells
- Phase 3: 40 cells
- Phase 4: 85 cells
- **Total: 133 cells**

**Halt-conditional savings:**
- Si Phase 2 verdict ≠ A_DETECTED: total drops to 8 cells (Phase 3+4 skip).
- Si Phase 3 verdict ≠ PASS: total drops to 48 cells (Phase 4 skip).

**Baseline benchmarks (separate, not in sweep):**

Heredar del #338 Phase 2 pre-reg §2.5 — BTC B&H + Hubrich 200-DMA + LRC archived sobre cada sub-window. Reuse outputs si existen en `data/retune/2026-05-14-regime-allocation/baseline_*.json` post-#338 Phase 3 merge. SI no existen (PR #349 sigue draft), Phase 3 epic C runs them fresh: 9 backtests, ~15-20 min.

---

## §3 · Sub-windows specification (heredado del #338 + Q4 lock)

**Locked per Q4 lock — mismos Windows del #338 §3:**

| ID | Window | Regime characterization | In-coverage (390-daily warmup) |
|---|---|---|---|
| A | 2022-04-01 → 2022-07-01 | Bear market 2022 (Terra/Luna May) | **8/10** (excl. PENDLE first bar 2023-07-03, JUP first bar 2024-01-31) |
| B | 2023-04-01 → 2023-07-01 | Recovery 2023 (post-FTX) | **8/10** (excl. PENDLE — first bar AFTER B end; JUP — no bars yet) |
| C | 2025-01-30 → 2025-04-30 | Recent pre-holdout 3 months | **9/10** (excl. JUP — only ~364 daily bars < 390 warmup) |

**Properties heredadas:**
- Non-overlapping ✓
- All BEFORE `holdout_start = 2025-04-30 00:00:00 UTC` ✓
- 3 distinct regime characterizations ✓
- Coverage A=8, B=8, C=9 ✓ (verified empirically en #338 Phase 2 pre-reg §3)

**Phase 2 runs solo en Window A.** Phase 3 runs solo en Window A. Phase 4 walk-forward runs en Windows B + C.

**Initial capital per portfolio aggregate (heredado del #338):**
- Window A: 8 × $10K = $80K
- Window B: 8 × $10K = $80K
- Window C: 9 × $10K = $90K

---

## §4 · Success criterion

### §4.1 — Phase 2 diagnostic verdict criterion

**Phase 2 emits one of 4 verdicts** sobre el output de la baseline equal-weight Donchian-9 run en Window A con observability ON:

| Verdict | Condition | Action |
|---|---|---|
| **A_DETECTED** | (∀N ∈ {5,10,20}: firing_count(N) ≥ 5 per Q-PR1) ∧ (p50(\|sum\|) < 2 over Window A 91 days per Q-PR2) | Advance to Phase 3 with A1 intervention (Q-PR6 lock) |
| **B_DETECTED** | (∃N ∈ {5,10,20}: firing_count(N) < 5) ∧ NOT (p50(\|sum\|) < 2) | Halt H-C2 + escalation per Q6 lock (epic C verdict `DIAGNOSTIC_B_DETECTED`); NO Phase 3 |
| **AMBIGUOUS** | All other combinations (mixed evidence) | Halt H-C1 + operator escalation per §4.5; NO Phase 3 |
| **PHASE_2_INSUFFICIENT_DATA** | Phase 2 sweep itself halted (e.g., universal bankruptcy in baseline run — unlikely but possible) | Halt + asymmetric guard per §4.6 |

**Operationalization of the 4 cases (verbatim decision tree):**

```
counts_A_evidence = (firing_count(N=5) ≥ 5) AND (firing_count(N=10) ≥ 5) AND (firing_count(N=20) ≥ 5)
magnitudes_A_evidence = (p50(|sum_aggregate|) < 2)

IF counts_A_evidence AND magnitudes_A_evidence:
    verdict = A_DETECTED
    next_action = "Phase 3 with A1 intervention"
ELIF (NOT counts_A_evidence) AND (NOT magnitudes_A_evidence):
    verdict = B_DETECTED
    next_action = "Halt H-C2 + escalation to meta-epic per Q6 lock"
ELSE:  # mixed evidence
    verdict = AMBIGUOUS
    next_action = "Halt H-C1 + operator escalation per §4.5"
```

**Note on aggregation of evidence:** counts_A_evidence is conjunctive over the 3 short-lookbacks (5, 10, 20). All three must fire ≥ T=5 per Q-PR1 lock. magnitudes_A_evidence is single condition over aggregated `|sum|`. The two evidence types are independent.

**Per-symbol vs aggregate decision rule:** counts + magnitudes are computed **per cell** (per (símbolo, sub-window) pair). Verdict is computed at **aggregate level**: A_DETECTED if ≥ 6 of 8 in-coverage símbolos satisfy `counts_A_evidence AND magnitudes_A_evidence` individually (≥75% match heredar del #338 §10.4 threshold pattern; concrete count ≥6 in Window A coverage of 8 símbolos). Otherwise AMBIGUOUS or B_DETECTED depending on the split.

**Worked example — 4/8 split (post-review 2026-05-15 clarification):** if exactly 4 of 8 in-coverage símbolos show A-evidence and 4 don't, the verdict falls into AMBIGUOUS via the "else" clause above (does not satisfy ≥6/8 for A_DETECTED, does not satisfy ≥6/8 NOT-A for B_DETECTED). Listed explicitly here so operator interpretation of edge cases is unambiguous. NOT a separate rule — derives directly from the verdict logic.

### §4.2 — Phase 3 sweep primary criterion (post A_DETECTED)

**Phase 3 PASS criterion:** sobre Window A primary at vol_target=30% con A1 intervention (subset {5, 10, 20}):

1. **n_trades ≥ 5** per cell (heredar N_TRADES_MIN del #338 §10.4 per Q-PR3 → carry forward; aplica per (símbolo, sub-window) cell)
2. **No bankruptcies** en ningún (símbolo, sub-window) cell (heredar S4 del #338 §6.2)
3. **win_rate ≥ 30%** per cell over closed trades (excluyendo SIM_END open positions) per Q-PR3 lock

**Required for PASS:** ≥ 6/8 in-coverage símbolos satisfy all 3 conditions individually (≥75% match heredar del #338 §10.4 pattern).

**Verdict candidates (Phase 3):**

| Verdict | Condition | Action |
|---|---|---|
| **PHASE_3_A_PASS** | ≥6/8 símbolos satisfy 3 conditions ∧ sensitivity sweep ≥3/4 vol_target PASS | Advance to Phase 4 walk-forward |
| **PHASE_3_A_PASS_CONDITIONAL** | ≥6/8 símbolos satisfy 3 conditions ∧ sensitivity sweep 2/4 vol_target PASS | Operator decides per §4.5 |
| **A_INTERVENTION_INSUFFICIENT** | < 6/8 símbolos satisfy 3 conditions ∧ no halt fired | A1 intervention not effective. Verdict pre-registered as FAIL terminal. Epic C archive considerable. |
| **A_INTERVENTION_HARMFUL** | H-C3 fired (bankruptcy in ≥1 símbolo) ∨ H-C4 fired (win_rate < 50% del baseline) | Intervention rejected. Verdict pre-registered as FAIL terminal. |
| **PHASE_3_INSUFFICIENT_DATA** | H-C halt fires AND naive verdict favorable (asymmetric halt-guard §4.6) | NO advance; operator decision per §4.5 |
| **SWEET_SPOT_ARTIFACT** | ≥6/8 símbolos at vol_target=30% PASS ∧ sensitivity sweep 1/4 (only vol_target=30% passes) | FAIL: calibration overfit. NO Phase 4. |

### §4.3 — Phase 4 walk-forward verdict criterion (post Phase 3 PASS)

Heredar pattern del #338 Phase 2 pre-reg §4 conjunctive criterion over 3 sub-windows. Phase 4 walks-forward A1 intervention sobre Windows B + C:

**Phase 4 PASS criterion (conjunctive):** A1 intervention satisfies §4.2 PRIMARY conditions (n_trades ≥ 5 ∧ no bankruptcies ∧ win_rate ≥ 30%) on ≥75% of in-coverage símbolos in **BOTH** Window B AND Window C.

| Window | In-coverage | ≥75% threshold |
|---|---:|---:|
| B | 8 | ≥6 símbolos PASS |
| C | 9 | ≥7 símbolos PASS |

**Sensitivity sweep walk-forward:** vol_target ∈ {0.25, 0.30, 0.35, 0.40} × 2 sub-windows = same primary criterion evaluated at each.

| Walk-forward verdict | Condition | Phase 5 advance? |
|---|---|---|
| **PHASE_4_A_PASS_STRONG** | B PASS ∧ C PASS ∧ sensitivity 3-4/4 in both | YES (gated por Q5 joint con epic D) |
| **PHASE_4_A_PASS_ROBUST** | B PASS ∧ C PASS ∧ sensitivity 2/4 in both | Operator decides per §4.5 |
| **PHASE_4_A_PASS_PARTIAL** | B XOR C PASS (one only) | Operator decides per §4.5 (default INCONCLUSIVE) |
| **PHASE_4_A_FAIL_CLEAN** | NOT (B PASS ∧ C PASS); mechanism engaged but n_trades or win_rate inadequate | NO Phase 5 |
| **PHASE_4_A_FAIL_DEGENERATE** | H-C halt fires in Window B or C | NO Phase 5 |
| **PHASE_4_INSUFFICIENT_DATA** | H-C halt + naive favorable (asymmetric guard) | NO Phase 5 |

**Phase 5 gate (heredar del Q5 lock):** PASS_STRONG or PASS_ROBUST is necessary but NOT sufficient. Phase 5 requires AND epic D Phase final equivalent PASS. Joint gate per Q5 lock.

### §4.4 — Cell selection rule + tie-break

**Pre-registered (heredar del #338 Phase 2 pre-reg §4.1):**

- 1 cell per (símbolo, sub-window, vol_target) — NO grid sweep, params locked.
- Cell exclusion: `n_trades < 5 AND simulation_completed = True` → INSUFFICIENT_DATA marker. Halt H-C2 threshold operationalized en §10.4.
- Deterministic tie-break: `(strategy_total_return, -baseline_total_return, alphabetical_symbol)`.

### §4.5 — Operator decision hooks (only non-strong/robust verdicts)

Heredar del #338 Phase 2 pre-reg §4.5 pattern. Auto-advance for `PHASE_3_A_PASS` / `PHASE_4_A_PASS_STRONG`. Operator decision required for:

- **PHASE_3_A_PASS_CONDITIONAL** / **PHASE_4_A_PASS_ROBUST** / **PHASE_4_A_PASS_PARTIAL** / **PHASE_3_INSUFFICIENT_DATA** / **PHASE_4_INSUFFICIENT_DATA**

Self-policing requirement (heredar del #338 §4.5):
1. Document explicit Bayesian update with magnitude shift en `derivation_audit.md`
2. Open separate sub-spec doc capturing override rationale + new Phase advance scope BEFORE proceeding
3. Require auditor counter-signoff (operator may invoke `code-review-excellence` agent o equivalent)
4. Override decision logged en `verdict.json` under `operator_override` block

**Asymmetric guard scope caveat (heredar del #338 §4.5 + R3 §4.6):** §4.6 asymmetric halt-guard applies only to §10-halt-fired scenarios. Does NOT cover override paths in §4.5. The bias risk applies equally to override paths — operator self-policing requirement is the structural net.

### §4.6 — Asymmetric halt-guard scope (mirror #338 §4.6 + R3 §4.6)

§10 halt + `n_windows < 3` (Phase 4 walk-forward) OR `Phase 2/3 partial` → `PHASE_X_INSUFFICIENT_DATA` **only** when naive verdict is favorable (PASS / CONDITIONAL / PARTIAL).

Negative verdicts on partial windows are **preserved** — §10 acts on dispositive partial negative evidence, no on inferential weight suspension.

**Methodology framing:** pre-reg lock identical to #338 §4.6. Discipline-preserving favorable outcomes suspended when mechanism barely engaged; discipline-eroding mechanism-engaged failures are not artificially preserved.

---

## §5 · Edge cases pre-registrados

### §5.1 — Ensemble warmup insufficient (heredar #338 §5.1)

PENDLE + JUP excluded per coverage table §3. Bar-by-bar log opcional NO emitted para excluded cells (saves disk).

### §5.2 — Baseline equal-weight signal degenerate (Phase 2 specific)

**Risk:** Phase 2 baseline run produces sum=0 for ≥ X% of bars across all 8 in-coverage símbolos → `magnitudes_A_evidence = True` trivially (p50 < 2 from concentration at zero), `counts_A_evidence` ambiguous.

**Pre-registered handling:** 
- If aggregate sum=0 for ≥ 80% of bars in ≥ 6/8 símbolos → flag as "baseline catastrophically inactive". Combined with per-lookback counts, this typically resolves to B_DETECTED (short lookbacks didn't fire either).
- If aggregate sum=0 for ≥ 80% but short-lookback counts ≥ 5 each → genuine dilution case, verdict = A_DETECTED.

### §5.3 — A1 intervention over-active (Phase 3 specific)

**Risk:** A1 subset {5,10,20} removes the stabilizing long-lookback flat votes. Could produce whipsaw with flip-per-bar trading.

**Pre-registered handling:** 
- Per-cell `n_trades > 60` (more than 60% of 91 daily bars with flip) → flagged as "potentially degenerate over-flipping". NOT auto-FAIL; operator review.
- Bankruptcy halt + K-cap continue as safety net.

### §5.4 — Cost model v2 calibration drift (heredar #338 §5.4)

Cost model v2 calibration locked. Phase 2/3/4 don't re-calibrate. Diagnostic-only flag if cost > 30% of gross_pnl in > 50% of cells.

### §5.5 — Bankruptcy halt interaction (heredar #338 §5.5)

BANKRUPT events count by (símbolo, sub-window, vol_target). Halt H-C3 threshold per §10.4.

### §5.6 — Funding cost amplification (heredar #338 §5.6)

Bidirectional rotational + funding accrual. Cost attribution split per `total_slippage_usd` vs `total_funding_usd`. Diagnostic-only flag if funding > 50% of total_cost in > 50% of cells.

### §5.7 — Daily aggregation edge (heredar #338 §5.8)

1H → daily resampling. Skip partial-day bars (< 24 hours coverage).

### §5.8 — Observability sidecar size

**Risk:** observability JSON sidecar per cell (counts + magnitudes per lookback) ~5-10KB; aggregate over 8 + 40 + 85 = 133 cells = ~1MB. Bar-by-bar log opcional (default OFF) ~5-15MB per cell when enabled; over 133 cells if all enabled = ~1-2GB.

**Pre-registered handling:**
- Default `observability_bar_by_bar = False`. Aggregate counts + magnitudes always emitted (essential for verdict).
- Bar-by-bar opt-in solo for forensic post-hoc; operator explicit enable per cell or globally.
- `data/retune/2026-05-15-signal-calibration/.gitignore` includes `observability_bar_by_bar_*.csv` to prevent accidental commit.

---

## §6 · Deliverable structure

After operator approval of this pre-reg, **Phase 1 implementation** (separate PR) lands the observability instrumentation:

```
strategy/donchian_ensemble.py:
  + emit_observability_metrics(window_bars) -> dict
  + apply_subset_lookbacks(lookbacks_subset) for A1 intervention
tools/signal_calibration_diagnostic.py    # NEW: Phase 2 diagnostic runner
tools/signal_calibration_sweep.py         # NEW: Phase 3 + Phase 4 sweep runner
tools/signal_calibration_verdict.py       # NEW: verdict calculator + asymmetric halt-guard
tests/test_donchian_ensemble_observability.py  # NEW: ~10-15 tests
tests/test_signal_calibration_sweep.py    # NEW: ~10-15 tests
```

After Phase 1 merge, **Phase 2 execution** (separate PR) runs the diagnostic:

```
data/retune/2026-05-15-signal-calibration/
├── phase2_diagnostic.json            # per-cell counts + magnitudes
├── observability_<symbol>_<window>.json  # 8 sidecar files (one per cell)
├── phase2_verdict.json               # A_DETECTED / B_DETECTED / AMBIGUOUS / PHASE_2_INSUFFICIENT_DATA
├── phase2_manifest.json              # cutoff, code_commit, sub-window, coverage
└── phase2_derivation_audit.md        # methodology recap + Bayesian update + decision hook
```

If Phase 2 verdict = A_DETECTED, **Phase 3 execution** (separate PR) runs A1 intervention:

```
data/retune/2026-05-15-signal-calibration/
├── sweep_primary_A.json              # 8 cells with A1 intervention
├── sweep_sensitivity_A.json          # 32 cells (4 vol_target)
├── phase3_verdict.json               # PHASE_3_A_PASS / A_INTERVENTION_INSUFFICIENT / etc
├── signal_diagnostics.json           # per-cell vote distribution, trade count, exit reason
├── cost_attribution.json             # per-cell slippage + funding breakdown
├── bankruptcy_diagnostics.json       # per-cell BANKRUPT events
├── halt_diagnostic.json              # ONLY IF halt fires
└── phase3_derivation_audit.md        # interpretation tree + verdict justification
```

If Phase 3 verdict = PHASE_3_A_PASS, **Phase 4 execution** (separate PR) runs walk-forward:

```
data/retune/2026-05-15-signal-calibration/
├── walkforward_primary_B.json        # 8 cells
├── walkforward_primary_C.json        # 9 cells
├── walkforward_sensitivity_B.json    # 32 cells
├── walkforward_sensitivity_C.json    # 36 cells
├── phase4_verdict.json               # PHASE_4_A_PASS_STRONG / etc
└── phase4_derivation_audit.md        # cross-window stability + Phase 5 gating decision
```

**Live-path safety:** all phases run with `cfg.regime_allocation.enabled = True` en harness only. Production scanner default OFF preserved.

---

## §7 · What this pre-reg does NOT cover

- **B-set interventions (signal-family swap).** Per Q6 lock, epic C authority = A-set only. If Phase 2 verdict = B_DETECTED, halt + escalation to meta-epic.
- **A2 / A3 / A4 interventions.** Per Q-PR6 lock, only A1 (subset {5, 10, 20}) is the default Phase 3 intervention. A2/A3/A4 require operator override per §4.5.
- **Basket revision (H-basket / epic D scope).** Epic C uses same 10-symbol basket per Q5 scope-separation. Basket-unlock decision per #338 §4.1 is operator-only.
- **Holdout (Phase 5).** Joint gate con epic D outcome per Q5 lock. Out of scope here.
- **Cost model v2 re-calibration.** Phase 0 #338 PR #341 lock preserved.
- **`config.defaults.json` promotion.** No production changes under this pre-reg.
- **Phase 4 paper trade design (#338-style 30-60d shadow).** Epic C Phase 4 is walk-forward backtest, NOT paper trade. Live shadow mode out of scope.
- **Sensitivity sweep beyond 4 vol_target points.** Heredar #338 §8.7 expanded sensitivity scope. NO finer sweep (e.g., vol_target=0.28, 0.32) under this pre-reg.
- **Re-litigation del #338 Phase 3 verdict** (PR #349 sigue independent).
- **Iteration on this pre-reg itself.** Single-iteration discipline heredar del #338 §1.3.

---

## §8 · Pre-registered decision branches (summary table)

| Branch point | Rule | Reference |
|---|---|---|
| Strategy class | Regime-allocation (heredado #338) | Epic C §0 |
| Aggregation (Phase 2 baseline) | Equal-weight vote Donchian-9 (heredado #338 §8.1; baseline only, BREAK in Phase 3 si A_DETECTED) | §2.1 |
| Lookbacks (Phase 2 baseline) | Zarattini exact 9 (heredado #338 §8.4; baseline only, BREAK in Phase 3 A1) | §2.1 |
| Lookbacks (Phase 3 A1 intervention) | Subset {5, 10, 20} (Q-PR6 lock) | §2.1 |
| Position update frequency | Daily 23:00 UTC (heredado #338 §8.2) | §2.1 |
| Portfolio vol target primary | 30% (heredado #338 §8.3) | §2.2, §2.5 |
| Sensitivity vol_target | {25, 30, 35, 40}% (heredado #338 §8.7) | §2.5, §4.2 |
| SHORT enabled | Bidirectional rotational (heredado #338 §8.5) | §2.1 |
| Leverage cap | 2x (heredado #338 §8.6) | §2.2 |
| Sizing | Vol-targeting Path B single-symbol (heredado #338 §2.2) | §2.2 |
| Exits | Signal-based (heredado #338 §2.3) | §2.3 |
| Cost model | v2 sqrt-participation + funding (heredado Phase 0 PR #341) | §2.4 |
| Sub-windows | A 2022-04-01→07-01, B 2023-04-01→07-01, C 2025-01-30→04-30 (Q4 lock) | §3 |
| Coverage | A=8, B=8, C=9 (heredado #338 §3) | §3 |
| Observability granularity | Counts + magnitudes per lookback per window; bar-by-bar log opcional default OFF (Q1 + Q-PR locks) | §2.1 |
| Phase 2 verdict A_DETECTED | counts_A_evidence ∧ magnitudes_A_evidence en ≥6/8 símbolos | §4.1 |
| Phase 2 verdict B_DETECTED | NOT counts_A_evidence ∧ NOT magnitudes_A_evidence | §4.1 |
| Phase 2 verdict AMBIGUOUS | Mixed evidence | §4.1 |
| Counts threshold per short-lookback | T=5 firings (Q-PR1 lock) | §4.1 |
| Magnitude threshold | p50(\|sum\|) < 2 (Q-PR2 lock) | §4.1 |
| Phase 3 PRIMARY conditions | n_trades ≥ 5 ∧ no bankruptcies ∧ win_rate ≥ 30% (Q-PR3 lock) | §4.2 |
| Phase 3 PASS threshold | ≥6/8 símbolos satisfy PRIMARY conditions | §4.2 |
| Phase 4 PASS threshold | ≥75% símbolos PASS PRIMARY in BOTH Window B AND Window C | §4.3 |
| Phase 4 sensitivity ROBUST | 3-4/4 vol_target in both windows | §4.3 |
| Phase 5 gate | Phase 4 PASS_STRONG/ROBUST ∧ epic D Phase final PASS (Q5 lock) | §4.3 + Q5 |
| Halt H-C1 | Phase 2 AMBIGUOUS verdict | §4.1, §10.4 |
| Halt H-C2 | Phase 2 B_DETECTED verdict | §4.1, §10.4 |
| Halt H-C3 | Phase 3 introduces bankruptcy in ≥1 símbolo | §6.3 epic C spec, §10.4 |
| Halt H-C4 | Phase 3 win_rate < 50% del baseline en ≥4/8 símbolos (Q-PR4 lock) | §6.3 epic C spec, §10.4 |
| Asymmetric halt-guard | Favorable verdicts overridden when partial; negative preserved | §4.6 |
| A-set default if A_DETECTED | A1 subset {5, 10, 20} (Q-PR6 lock) | §2.1, §4.2 |
| A2/A3/A4 alternatives | Operator override path per §4.5 self-policing | §2.1 |
| Compute budget | No hard cap (Q-PR5 lock) | §11 |
| Operator override self-policing | 4-element requirement (heredar #338 §4.5) | §4.5 |
| Live path safety | Flag-gated; default OFF; no production promotion | §6, §7 |
| Pre-reg iteration | Single-iteration discipline; NO Phase 2.5 / re-litigation | §1.3, §7 |

---

## §9 · Open questions for operator

La mayoría de decisiones materiales están locked vía Q1-Q6 (epic C spec) + Q-PR1..Q-PR6 (este pre-reg). Las preguntas restantes son operationalization details — operator review desired antes de Phase 1 execution.

### §9.1 — [RESOLVED 2026-05-15 post-review] Baseline benchmark execution scope

**LOCKED:** Path (a) — reuse #338 baselines si PR #349 mergeada antes de Phase 3 epic C arranque. Recommended merge order de cross-PR review establece #349 → #353 → Phase 1, así que la condición se satisface naturalmente.

**Fallback (b):** Si por timing operator #349 NO está mergeada cuando Phase 3 epic C arranque, re-generar fresh baselines (+15-20 min compute). Decisión deterministic, NO deferred — el harness chequea existencia de `data/retune/2026-05-14-regime-allocation/baseline_*.json` y emit warning + auto-regenerate si missing.

**Rationale:** convención "operator pushback resolved BEFORE merge" preservada (cross-PR review observation). Lock evita ambiguity en el reviewer del Phase 3 epic C PR.

### §9.2 — [OPTIONAL] Observability disk budget

Per §5.8: aggregate observability metrics ~1MB total. Bar-by-bar log opcional default OFF; if enabled per cell, ~5-15MB each.

- (a) **Default `observability_bar_by_bar = False` (Recommended)** — operator opts in per cell post-hoc si forensic review needed.
- (b) Default ON for Phase 2 diagnostic (8 cells × ~10MB = ~80MB) — full forensic from day 1.
- (c) Default ON for Phase 2 only, OFF for Phase 3/4 — compromise.

### §9.3 — [RESOLVED — locked via Q-PR locks] Branch + PR title convention

Resolved: Phase 1 branch `feat/signal-calibration-observability` (or analog), Phase 2 branch `feat/signal-calibration-phase2-diagnostic`, Phase 3 branch `feat/signal-calibration-phase3-sweep`, Phase 4 branch `feat/signal-calibration-phase4-walkforward`. PR titles follow `feat/docs(epic signal calibration Phase N): <description> (#350)` pattern matching #338.

### §9.4 — [RESOLVED 2026-05-15 post-review] Phase 1 implementation order

**LOCKED:** Path (a) — both observability layer + A1 subset logic shipped en single Phase 1 PR. Faster path to Phase 2 + reduces context-split for reviewer.

**Rationale:** ambas piezas operacionalmente coupled (Phase 2 diagnostic requires observability; Phase 3 sweep requires A1 subset; ambos blocked por mismo TDD pattern). Staging the two no agrega review value que justifique el extra PR overhead. Convención "operator pushback resolved BEFORE merge" preservada (cross-PR review observation).

---

## §10 · Pre-execution math sanity check

### §10.1 — Warmup math (heredar #338 §10.1)

390 daily bars = longest lookback 360 + vol window 30. Verified empirically en #338 Phase 2 pre-reg §10.1. PENDLE + JUP excluded per coverage table §3.

### §10.2 — Signal frequency lower bound (heredar #338 §10.2 + new diagnostic-specific)

Phase 2 baseline equal-weight Donchian-9 expected to produce 5-25 trades per (símbolo, sub-window A) under healthy strategy (binomial approximation per #338 §10.2). Empirically en #338 Phase 3 sweep, 8/8 in-coverage símbolos produjeron `n_trades ∈ {1,1,1,1,2,2,2,2,3}` over 91 days — that's the H2 firing pattern that triggered epic C.

**Per-lookback firing count expected (Phase 2 diagnostic):**

Under random walk null over 91 days:
- Lookback N=5: ~10-30 firings (frequent flips as 5-day channel narrows)
- Lookback N=10: ~5-20 firings
- Lookback N=20: ~3-12 firings
- Lookback N=30-90: ~2-8 firings
- Lookback N=150-360: ~1-3 firings (sticky, rarely flips in 91 days)

**Q-PR1 threshold T=5 per short-lookback is conservative:** even under random walk null, expected counts for N ∈ {5, 10, 20} should comfortably exceed 5. If empirical counts < 5, that's evidence for B (signals not firing structurally).

### §10.3 — Magnitude distribution under null (heredar #338 §10.3 + new)

Under binomial(9, 0.5) null, `|sum|` distribution:
- mean ≈ 2.4
- p50 ≈ 2
- p95 ≈ 6

**Q-PR2 threshold p50 < 2 is slightly stricter than random walk null.** If empirical p50 < 2, mass is concentrated below random-walk expectation = dilution consistent with A.

Conservative interpretation: p50 = 2 exactly is **borderline** — verdict logic uses `<` strict (so 2.0 fails A-evidence). Operator may revisit threshold if borderline cases are common in real data.

### §10.4 — Halt conditions pre-registered (concrete thresholds)

**Halt H-C1 (Phase 2 AMBIGUOUS):** if Phase 2 baseline produces mixed evidence (counts_A_evidence != magnitudes_A_evidence in aggregate over 8 símbolos), halt + operator escalation. Operationalization: tie-break exactly 4/8 split (per §4.1) triggers H-C1.

**Halt H-C2 (Phase 2 B_DETECTED):** if Phase 2 baseline produces evidence consistent with H-signal-B (NOT counts_A AND NOT magnitudes_A in ≥6/8 símbolos), halt + escalate to meta-epic per Q6 lock. NO Phase 3.

**Halt H-C3 (Phase 3 A_INTERVENTION_HARMFUL — bankruptcy):** if A1 intervention causes BANKRUPT event in ≥1 símbolo in Window A primary, halt Phase 3 entirely. Concrete count: ≥1 bankruptcy (zero-tolerance, heredar S4 del #338 §6.2).

**Halt H-C4 (Phase 3 A_INTERVENTION_HARMFUL — win_rate degradation):** if A1 intervention win_rate < 50% del equal-weight baseline win_rate (computed in Phase 2 diagnostic) in ≥4/8 símbolos, halt Phase 3. Concrete count: ≥4 of 8 in-coverage símbolos exceed degradation threshold.

**Direction-of-change framing transparency (heredar #338 §10.4 pattern):**
- H-C1 / H-C2 are new (epic C-specific); no #338 anchor. Justification: Phase 2 is a diagnostic verdict step that the #338 Phase 2 pre-reg didn't have analog for.
- H-C3 mirrors #338 §6.2 S4 zero-bankruptcy target.
- H-C4 is new (Q-PR4 lock); rationale documented in epic C spec §10 risk register R-C5 (threshold leakage mitigation).

**Asymmetric halt-guard:** under halt, favorable verdicts overridden to `PHASE_X_INSUFFICIENT_DATA`. Negative verdicts on partial windows preserved. Per §4.6 + #338 §4.6 + R3 §4.6 mirror.

---

## §11 · Compute estimate

| Stage | Estimate | Notes |
|---|---|---|
| Phase 1 code (`strategy/donchian_ensemble.py` observability + A1 subset logic) | 2-3 h | TDD pattern: tests first, then implementation |
| Phase 1 tools (`tools/signal_calibration_*.py` × 3) | 3-4 h | Adapted from `tools/regime_allocation_sweep.py` + verdict tool pattern |
| Phase 1 unit tests (`tests/test_donchian_ensemble_observability.py` + `test_signal_calibration_sweep.py`) | 2-3 h | ~20-30 tests total, TDD |
| Phase 2 execution (8 cells × ~60s parallelized 8 workers) | **5-10 min wall-clock** | Single sweep Window A baseline, faster than #338 since no sensitivity |
| Phase 2 verdict computation + audit | 30-45 min | Includes Bayesian update prose |
| Phase 3 execution (40 cells parallelized) | **40-60 min wall-clock** | Primary + sensitivity sweep |
| Phase 3 verdict + audit | 45-60 min | Interpretation tree + verdict justification |
| Phase 4 execution (85 cells parallelized) | **1.5-2 h wall-clock** | Walk-forward + sensitivity |
| Phase 4 verdict + audit | 1 h | Cross-window stability + Phase 5 gating decision |
| Baseline backtests (if not heredados from #338) | 15-20 min | BTC B&H + Hubrich + LRC archived × 3 windows |
| **Total worst-case (Phase 2 + 3 + 4 + audit)** | **~3-5 h wall-clock execution** | Plus Phase 1 code (~10-15h development) |

**Halt-conditional savings:**
- Si Phase 2 verdict = B_DETECTED or AMBIGUOUS: total drops to Phase 2 only (~30 min execution + audit).
- Si Phase 3 verdict = A_INTERVENTION_INSUFFICIENT: total drops to Phase 2+3 only (~1.5 h).

**Q-PR5 lock: no hard cap.** Wallclock unbounded per #338 pattern.

---

## §12 · Auditor prior on outcomes

**Auditor (Claude Opus 4.7) prior before Phase 2 execution:**

| Outcome | Probability | Reasoning |
|---|---:|---|
| **Phase 2 A_DETECTED** | ~30-40% | Audit §2 #338 hipotetizó dilution ("short lookbacks **likely** fired but were diluted") sin observational evidence. Pre-reg adds the evidence layer. Hipótesis plausible but not certain. |
| **Phase 2 B_DETECTED** | ~30-40% | Equally plausible — bear 2022 Window A may have produced too few breakouts at any lookback (sustained downtrend without aceleration). |
| **Phase 2 AMBIGUOUS** | ~20-30% | Mixed evidence is the structural risk — some símbolos may show A pattern, others B. Q-PR1 + Q-PR2 thresholds may need adjustment post-Phase-2. |
| **Phase 2 INSUFFICIENT_DATA** | ~5-10% | Halt H-C3 fires if baseline (which is unchanged from #338 Phase 3) produces unexpected bankruptcies. Very unlikely given #338 Phase 3 results (0 bankruptcies under equal-weight). |

**Conditional priors (given Phase 2 = A_DETECTED, ~30-40% base rate):**

| Phase 3 outcome | Conditional P | Reasoning |
|---|---:|---|
| **PHASE_3_A_PASS** (strong/robust) | ~40-50% | A1 subset {5,10,20} is the most direct test; if dilution is the issue, A1 should clearly recover n_trades. Conditional on A_DETECTED, probability is high. |
| **PHASE_3_A_PASS_CONDITIONAL** | ~10-15% | Edge in 2/4 vol_target; operator decides. |
| **A_INTERVENTION_INSUFFICIENT** | ~25-35% | A1 fires more signals than baseline but win_rate or n_trades inadequate. Indicates Donchian short-lookbacks fire but don't capture genuine trend at this scale. |
| **A_INTERVENTION_HARMFUL** | ~10-15% | A1 introduces whipsaw (over-flipping per §5.3) → bankruptcies (H-C3) or win_rate degradation (H-C4). Vol-targeting + K-cap mitigate but don't eliminate. |
| **PHASE_3_INSUFFICIENT_DATA** | ~5-10% | Halt fires + asymmetric guard. |
| **SWEET_SPOT_ARTIFACT** | ~5-8% | A1 works only at vol_target=30%; sensitivity rejects. |

**Conditional priors (given Phase 3 = PASS, ~40-50% conditional, ~12-20% joint):**

| Phase 4 outcome | Conditional P |
|---|---:|
| **PHASE_4_A_PASS_STRONG** | ~30-40% |
| **PHASE_4_A_PASS_ROBUST** | ~20-30% |
| **PHASE_4_A_PASS_PARTIAL** | ~20-25% |
| **PHASE_4_FAIL** (clean / degenerate) | ~20-30% |

**Joint prior P(Phase 4 PASS_STRONG, end-to-end):** ~4-7%.
**Joint prior P(epic C reaches Phase 5 gate):** ~5-10% (PASS_STRONG or ROBUST).
**Joint prior P(epic C contributes evidence about A vs B regardless of validation):** ~80-90% (Phase 2 verdict alone is informative).

**Bayesian update plan post-each-phase:**

- **Phase 2 A_DETECTED:** P(A is correct) → ~70-80%; advance to Phase 3.
- **Phase 2 B_DETECTED:** P(A is correct) → ~5-10%; halt + meta-epic escalation.
- **Phase 2 AMBIGUOUS:** P(A is correct) preserved at ~50%; operator decides.
- **Phase 3 PASS:** P(A1 intervention generalizable) → ~60-70%; advance to Phase 4.
- **Phase 3 FAIL:** P(A-set space exhausted) → ~70-80%; archive epic C.
- **Phase 4 PASS_STRONG/ROBUST:** P(signal calibration validated joint) → ~80%; Phase 5 gated by epic D.
- **Phase 4 FAIL:** P(window-specific edge or regime artifact) → ~50%; operator decides.

**§A.4 prior re-evaluation checkpoint:** post-each-phase PR comment must include explicit Bayesian update with magnitude shift documented in 2-3 sentences. Same pattern heredado del #338 §A.4 + R1/R2/R3.

**Agent tooling note (added 2026-05-15):** Default §A.4 mechanic remains 2-3-sentence prose. For Phase 3 + Phase 4 the structured sensitivity sweep (8 símbolos × 4 vol_target × 1 sub-window = 32 cells in Phase 3; 17 símbolos × 4 vol_target × 2 sub-windows = 136 cells in Phase 4 walk-forward) is well-posed for hierarchical model. The `pymc-bayesian-modeling` skill (installed 2026-05-15) is the canonical tool for: (a) beta-binomial posterior over P(A1 valid for live) given verdict matrix; (b) hierarchical model on (símbolo × vol_target × sub-window) cell outcomes to decompose intervention-effect from símbolo-effect from regime-effect. Default stays prose-only; PyMC is operator-on-demand.

---

## §13 · Methodology limitations carried forward

Heredar del #338 Phase 2 pre-reg §13 + new epic C-specific:

1. **Cost model v2 calibration uncertainty.** Heredado #338. Phase 2/3/4 don't re-calibrate.
2. **Basket curated under contaminated simulator.** Heredado #338 §A.2.
3. **3-month sub-windows are short for days-to-months hold period.** Heredado #338.
4. **Sensitivity sweep is conservative (4 vol_target points).** Heredado #338.
5. **Bidirectional rotational requires perp markets + cross-margin.** Heredado #338.
6. **Vol-targeting depends on realized vol estimate.** Heredado #338.
7. **No regime detector means no early-warning mechanism.** Heredado #338.
8. **Daily granularity eliminates 5m entry trigger.** Heredado #338.
9. **Single-iteration discipline.** Heredado #338 + epic C §1.3.
10. **Sub-window choice may not generalize.** Heredado #338 + Q4 lock (mismos Windows).
11. **Independent-stream architecture vs Zarattini portfolio approach.** Heredado #338 Path B.
12. **Diagnostic threshold sensitivity (NEW epic C-specific).** Q-PR1 T=5 + Q-PR2 p50<2 + Q-PR3 30% are pre-registered choices. Borderline data points (e.g., exactly 5 firings, exactly p50=2) deterministically fall into "fail A-evidence" by strict `<` operator. Future epics may revisit thresholds; under this pre-reg they're locked.

    **Sub-limitation — random-walk null assumption underlying Q-PR2** (post-review 2026-05-15): Q-PR2 threshold `p50(|sum|) < 2` is anchored to the random-walk null binomial(9, 0.5) where `p50 ≈ 2.4` under independence assumption (§10.3). This stochastic-process null is an approximation; crypto-daily-closes exhibit non-random autocorrelation + clustering (well-documented en literatura), so empirical `p50` may diverge in either direction without the strategy class being defective. **If Phase 2 diagnostic verdicts hover near `p50 = 2` across many cells (e.g., `p50 ∈ [1.8, 2.2]` for ≥4/8 símbolos), eso es signal para revisitar Q-PR2 en un follow-up pre-reg, NOT to over-interpret a single borderline case as definitive.** The threshold-leakage risk is bounded: Q-PR2 is rule-derived (statistical null + operator-chosen partition), NOT data-fit on epic C-specific data; if revised, must follow the same pre-registration discipline.
13. **A1 subset {5,10,20} is one of multiple A-set candidates (NEW epic C-specific).** Q-PR6 lock pre-registers A1 as default. A2/A3/A4 alternatives require operator override per §4.5 self-policing. If A1 FAIL, default verdict is `A_INTERVENTION_INSUFFICIENT` — epic C terminal without trying A2/A3/A4 under this pre-reg.
14. **Phase 5 holdout joint gate (NEW epic C-specific).** Q5 lock makes Phase 5 conditional on epic D Phase final PASS. Epic C standalone PASS is necessary but NOT sufficient for holdout shot. Operator override via new issue + reasoning per §4.5 self-policing pattern.

---

## §14 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-15 | Pre-reg sub-spec inicial — drafted post epic C spec doc merge (PR #352, squash `00d6997`); 6 operator decisions Q-PR1..Q-PR6 locked vía 2 rounds AskUserQuestion; mirror del #338 Phase 2 pre-reg pattern; tension table con #338 §8.1 + §8.4 cited en §8 + §9 | Claude Opus 4.7 + sssamuelll |
| 2026-05-15 | **Cross-PR review fixes applied** — §9.1 baseline reuse locked to Path (a) reuse #349 baselines (recommended merge order satisfies condition); §9.4 Phase 1 PR-split locked to Path (a) single PR; §13.12 extended con sub-limitation about Q-PR2 random-walk null assumption (rule-derived, not data-fit); §4.1 tie-break wording reformulated as worked example (4/8 split derives from "else" clause, not separate rule). Review identified items resolved inline per "operator pushback resolved BEFORE merge" convention. | Claude Opus 4.7 + sssamuelll (post-/ultrareview) |
| TBD | Operator re-review + final approval | sssamuelll |
| TBD | Phase 0 deliverable #3 PR merged via gh pr merge --squash → closes epic C Phase 0 | sssamuelll |
| TBD | Phase 1 execution (separate PR after this pre-reg merge) | sssamuelll + Claude Opus 4.7 |

Reservar líneas para iteración post-operator-re-review y verdict registration en Phase 2/3/4 closure.
