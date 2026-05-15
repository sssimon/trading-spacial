# R1 — Pre-registration sub-spec: signal-reversal exit replacing static TP

**Fecha:** 2026-05-12
**Status:** DRAFT — pre-registration ANTES de cualquier execution. Operator review desired before sweep runs.
**Autor:** Claude Opus 4.7 (sesión kickoff post-R2) en colaboración con sssamuelll
**Tipo:** pre-registration sub-spec — fija metodología antes de implementación + sweep
**Trigger:** Audit spec §6 R1 + §A.5 step 2 + §A.6 single-alternative discipline + R2 verdict (FAIL strong, math-deterministic) + pre-R1 exit reason query (R1_PLAUSIBLE per `pre_r1_exit_reasons.json`)
**Cierre objetivo:** R1 verdict (SUCCESS / INCONCLUSIVE / FAIL) per §4 → conditional advance to R3 or H5 escalation

---

## §0 · Lectura mínima requerida

Antes de revisar este pre-reg, leer en este orden (≈30 min):

1. `data/retune/2026-05-11-r2-gates/derivation_audit.md` §11.1 — pre-R1 exit reason query (R1_PLAUSIBLE verdict + per-symbol distribution)
2. `docs/superpowers/specs/es/2026-05-11-strategy-structural-audit.md` §A.6 — R3 single-alternative discipline (R1 inherits same constraint)
3. `docs/superpowers/plans/2026-05-11-r2-gates-rederivation-pre-reg.md` — format template + sub-window methodology

Quién ya leyó esos 3 puede saltar a §1.

---

## §1 · Contexto y alcance

### §1.1 — Trigger inmediato

Phase 2 R2 cerró FAIL strong (math-deterministic) — gates re-derivation no es la palanca. La pregunta queda: **¿el cambio estructural de "replace static TP con dynamic exit" produce edge (R1)?**

Pre-R1 query (52 trades, A.4-1 train window, current gates) confirma `R1_PLAUSIBLE`:

| Exit reason | % |
|---|---:|
| **TIME_LIMIT** | **44.00%** (dominant) |
| SL | 36.00% |
| BANKRUPT | 16.00% |
| TP | 4.00% |

44% de las trades cierran por TIME_LIMIT en el time horizon de 5h sin alcanzar TP. Si un dynamic exit captura el move antes del corte forzado de 5h, mecanismo de competencia es directo.

### §1.2 — Alcance del pre-reg

**Hace:**
- Selecciona y justifica UN single dynamic exit variant (signal-reversal) — sin iteration entre múltiples (per §A.6 audit discipline).
- Pre-registra la implementación concreta del variant (param, sweep range, tie-break rules).
- Pre-registra criterio falsificable de SUCCESS / INCONCLUSIVE / FAIL antes de cualquier compute.
- Aplica pre-execution math sanity check (§10) — si invalidante, abort.
- Pre-registra Bayesian prior + post-execution update plan.

**No hace:**
- No ejecuta nada todavía. Code + sweep son commits subsecuentes solo si operator approves §9.
- No modifica `config.defaults.json`, `backtest.py`, o cualquier code path. Pre-reg only.
- No re-litiga R2 result, gates calibration, signal frame (LRC), basket curado, o cost model. Esos son separados.
- No cambia la entry signal (LRC 25%/75% + score) — R1 es exit-only. Signal alternative es R3.

### §1.3 — Iteración

Esta es la **primera iteración** del R1 methodology. Está abierta a operator pushback en §9.

---

## §2 · Methodology

### §2.1 — Selección del variant: signal-reversal exit

**Tres candidates desde audit §6 R1 + kickoff context:**

| Variant | Mechanism | Fit con mean-reversion frame | # params nuevos | Overfitting risk |
|---|---|---|---:|---|
| **Trailing stop** (atr_trail_mult) | Stop sigue al precio favorable | Medio — captura cualquier move pero exits en pequeñas reversiones intra-bar | 1 | Medio (parameter-sensitive; trail tightness × volatility) |
| **Signal-reversal exit** (lrc_exit_threshold) | Exit cuando LRC sale del trigger zone hacia mid | **Alto** — alineado con la thesis explícita LRC mean-reversion | 1 | Bajo (single threshold, monotone parameter, 5 sweep values) |
| **Time-decay TP** (tp_initial, decay_rate, tp_final) | TP relaja desde wide hasta tight con horas | Medio — híbrido entre TP estático y signal | 3 | Alto (multi-param, decay shape elegible) |

**Selección: signal-reversal exit.** Justificación teórica (NO basada en backtest preview):

1. **Maximum mechanism alignment con la signal thesis.** B3 (`strategy/core.py` LRC_LONG_MAX=25%, LRC_SHORT_MIN=75%) bets explícitamente en LRC mean-reversion: la entry assume que el price está en un extremo del Linear Regression Channel y va a revertir hacia mid (50%). El exit natural del thesis es "salir cuando LRC efectivamente reverte". Static TP (ATR-based) es metodológicamente desconectado del mechanism que el strategy bets — usa ATR como proxy de "distance traveled", ignora si la travel happened en la dirección que el thesis predice.

2. **Replaces TIME_LIMIT mechanism head-to-head.** TIME_LIMIT es un cutoff arbitrario (5h) que viola el thesis. Signal-reversal cierra cuando la price action confirma o disconfirma el thesis. Si LRC reverts dentro de 5h → SIGNAL_EXIT con whatever pnl materialized. Si no reverts → cae a TL (kept activo como backstop) o SL.

3. **Single parameter, monotone semantics.** `lrc_exit_threshold` ∈ [35, 55] tiene interpretación clara: 35 = exit conservador (capture parcial reversion), 50 = exit en midline (full target), 55 = exit greedy (slight overshoot). Sweep de 5 values cubre conservative-to-greedy. Bajo overfitting surface vs trailing stop's interaction con volatility regime.

4. **Coherente con R2 §6 frame caveat.** R2 derivation_audit.md §6 estableció que time-to-1-ATR ≈ 5h es el natural horizon para mean-reversion frame. Signal-reversal exit opera en este horizon — espera que LRC reverte dentro del time-to-1-ATR window. Si el thesis es correcto, la mayoría de las reversiones happen antes del 5h cutoff.

5. **Audit §6 lo lista como "mean-reversion explícita" candidate.** Auditor recomendación original (mas non-vinculante) de §A.6 R3 fue trend-pullback para R3 (alternative signal). Para R1 (exit replacement, signal-preserving), signal-reversal es el natural fit.

**¿Por qué NO trailing stop, segunda mejor opción?**
- Trailing stop captura movement direccional, pero en mean-reversion frame el direccional es justamente lo que la strategy NO bets (bets en reversión). Un trailing stop tight cerraría early en el move de reversion mismo (locking pequeñas gains), un trailing wide expone a SL.
- Trail-mult interactua fuertemente con per-symbol volatility (ATR), introduciendo confounding con las gates ATR-based ya re-evaluadas.
- Trailing stop es más natural para R3 momentum-breakout candidate (donde la thesis ES direccional).

**¿Por qué NO time-decay TP?**
- 3 parámetros nuevos (tp_initial, decay_rate, tp_final) inflan overfitting surface.
- Decay shape (linear / exponential / step) es decisión adicional que requiere su propia justificación.
- Mechanism es híbrido — no testea cleanly ningún single thesis.

### §2.2 — Definición concreta del exit logic

**Nuevo exit reason category:** `SIGNAL_EXIT` (string literal en `trade["exit_reason"]`).

**Per-bar evaluation (en `simulate_strategy`, after SL/TP check, before TIME_LIMIT check, on bar close):**

```
For each open position with `direction` ∈ {LONG, SHORT}:
    lrc_pct_current = compute_lrc_pct(close[i], lrc_lower[i], lrc_upper[i])  # 0–100
    if direction == LONG and lrc_pct_current >= lrc_exit_threshold:
        close_position(price=close[i], reason="SIGNAL_EXIT")
    elif direction == SHORT and lrc_pct_current <= (100 - lrc_exit_threshold):
        close_position(price=close[i], reason="SIGNAL_EXIT")
```

Donde `compute_lrc_pct` es la misma función que el signal de entry usa (`strategy/core.py` LRC computation; reutilizar — no re-implementar).

**Tie-break order (mismo bar) — pre-registrado:**

1. SL hit (intra-bar high/low check)
2. TP hit (intra-bar high/low check) — *kept active as legacy backstop; ver §2.3*
3. SIGNAL_EXIT (close-bar evaluation only)
4. TIME_LIMIT (close-bar evaluation only)
5. BANKRUPT halt (post-trade equity check)

Razón del orden: SL/TP usan high/low intra-bar y son "más conservadores" en captura de price action (hit-by-hit). SIGNAL_EXIT y TIME_LIMIT usan close-bar (no intra-bar). Si SL y SIGNAL_EXIT triggean en el mismo bar, SL gana (más pesimista, anchor-conservador).

**Implementación nota:** `simulate_strategy` actualmente evalúa exits en `backtest.py:733-755`. SIGNAL_EXIT logic se inserta como nuevo branch entre SL/TP check y TIME_LIMIT check, preservando structure existente. Sin cambios al `_close_position` mechanism (sign error post-#223 fix permanece intact).

### §2.3 — TP estático: KEPT (no removed)

**Decision:** mantener `atr_tp_mult` activo en su valor `symbol_overrides` actual. No removed.

**Razón:**
- TP fired solo en 4% de pre-R1 trades (essentially noise floor en current calibration).
- Removerlo introduce un confounder: cualquier shift observado en exit distribution post-R1 podría ser "SIGNAL_EXIT replacing TP" en lugar de "SIGNAL_EXIT replacing TIME_LIMIT". Mantenerlo aísla la pregunta.
- Honesta admission: si el sweep produce shift en TP%, eso es evidence adicional sobre el frame question — no un bug.
- Cost de mantenerlo: trivial (existing code path, zero new bugs introduced).

**Alternative considered + rejected:** "remove TP entirely, sweep solo lrc_exit_threshold". Rejected porque:
- Pierde el comparison "TP fires X% pre vs post" como diagnostic.
- Introduces ambiguity en si SIGNAL_EXIT está realmente capturing nuevo behavior o solo reemplazando TP que igual no fired.

### §2.4 — Other gates: UNCHANGED (matching R2 PoV decoupling pattern)

**Per R2 §2.2 amendment pattern (PoV decoupled), R1 hace mismo decoupling para todo gate no-bajo-test:**

| Gate | R1 status | Source value |
|---|---|---|
| `time_limit_hours` (per-symbol) | UNCHANGED — keep at current `config.defaults.json` | Acts as backstop after SIGNAL_EXIT in tie-break order |
| `max_participation_rate` (per-symbol) | UNCHANGED — same as R2 (decoupled, deferred to cost model v2 / issue #325) | — |
| `cooldown_hours` (per-symbol) | UNCHANGED | — |
| `atr_sl_mult` (per-symbol) | **SWEPT** — same range as Q2 (5 values: {0.5, 0.7, 1.0, 1.5, 2.5}) — see §2.5 | Symbol-specific currently in `config.defaults.json`; sweep replaces per-cell |
| `atr_tp_mult` (per-symbol) | UNCHANGED — keep at current per-symbol value (kept-active per §2.3) | — |
| `atr_be_mult` (per-symbol) | **SWEPT** — same range as Q2 (3 values: {1.5, 2.0, 2.5}) | — |
| `lrc_exit_threshold` (NEW, global) | **SWEPT** — 5 values: {35, 40, 45, 50, 55} | New parameter — pre-registered range |

**Razón de sweepear SL + BE además del nuevo lrc_exit_threshold:**
- SL/BE interactuan con SIGNAL_EXIT vía tie-break order. SL tight reduces SIGNAL_EXIT share (más SL hits intra-bar).
- Mantener Q2's grid dimensionality (105 cells per symbol per sub-window) preserva interpretability vs Q2 baseline.
- 5 × 3 × 5 = 75 cells. Slightly menor que Q2's 105 (5×3×7); intentional to limit compute. Si operator quiere full 105, sweep `atr_sl_mult` a 7 values (add 0.7, 1.2, 2.0).

### §2.5 — Sweep grid concreto

```
atr_sl_mult ∈ {0.5, 0.7, 1.0, 1.5, 2.5}              # 5 values (Q2 subset)
atr_be_mult ∈ {1.5, 2.0, 2.5}                          # 3 values (Q2 same)
lrc_exit_threshold ∈ {35, 40, 45, 50, 55}              # 5 values (NEW)
                                                        # → 75 cells per (symbol, sub-window)
```

**Total compute:** 10 symbols × 3 sub-windows × 75 cells = **2,250 backtests.**

**Per-cell baseline reference:** A.4-1 con current `(sl, tp, be) = symbol_overrides`, lrc_exit_threshold = "infinity" (i.e., disabled) — this is the pre-R1 state. Re-computed por cell con SIGNAL_EXIT logic disabled (boolean flag in harness) for direct Δ calculation.

**Per-cell comparison method:** for each (symbol, sub-window), compute:
- Baseline (current strategy, no SIGNAL_EXIT) — single backtest.
- Treatment (75-cell sweep with SIGNAL_EXIT enabled at varying thresholds) — 75 backtests.
- Per-cell: `Δnet_pnl`, `Δexit_reason_distribution`, `Δavg_pnl_per_trade`, `Δbankruptcy_count`.

---

## §3 · Sub-windows specification

**Mismo Option B que R2 — leakage-protected, regime-diverse:**

| ID | Window | Regime characterization | Notable coverage |
|---|---|---|---|
| A | 2022-04-01 → 2022-07-01 | Bear market 2022 (Terra/Luna May) | 8/10 (excl. PENDLE start 2023-07, JUP start 2024-01) |
| B | 2023-04-01 → 2023-07-01 | Recovery 2023 (post-FTX) | 9/10 (excl. JUP) |
| C | 2025-01-30 → 2025-04-30 | Recent pre-holdout 3 months | 10/10 |

**Properties:**
- Non-overlapping ✓
- All BEFORE holdout_start = 2025-04-30 ✓
- Genuinely OUTSIDE A.4-1 train window [2024-01-30, 2025-01-30] ✓
- 3 distinct regime characterizations
- Identical a R2 sub-windows — same usable_bars rules apply

**Per-symbol coverage: same as R2 §3.1.** JUP excluded from A+B (no data); PENDLE excluded from A.

**Pre-registered exclusion threshold:** `usable_bars ≥ 500` per (symbol, sub-window) for inclusion en aggregation. Same as R2.

---

## §4 · Success criterion

**Primary criterion (conjuntiva sobre 3 sub-windows):**

R1 SUCCESS = en CADA uno de los 3 sub-windows, **simultáneamente**:
- ≥1 symbol con `avg_pnl_per_trade > 0`, AND
- ≥3 symbols con `net_pnl > 0` (over the cell selected per §4.3 selection rule), AND
- avg `profit_factor > 1.2` sobre el subset de positive-net-pnl symbols.

**Secondary observational criterion (additive, mechanism check):**

Para cada (symbol, sub-window) pair eligible per §3, sobre la cell selected per §4.3:
- TIME_LIMIT% < 20% (i.e., dynamic exit displaces TIME_LIMIT por debajo de 20% de exits)

Required: secondary holds para ≥6 of 8 currently-bankrupt symbols (con ≥10 trades en el sub-window — minimum sample for distribution claim) en CADA sub-window.

**Notes:**
- "currently-bankrupt symbols" = ADA, AVAX, DOGE, UNI, XLM, PENDLE, JUP, RUNE (same 8 as R2).
- "≥10 trades minimum sample" — without this, single-trade SIGNAL_EXIT events would falsely satisfy "TIME_LIMIT% < 20%" trivially.

### §4.1 — Cell selection rule per (symbol, sub-window)

**Pre-registrado (no post-hoc maximization):**

For each (symbol, sub-window): select the cell that maximizes `net_pnl` over the 75-cell grid for THAT (symbol, sub-window) pair, subject to constraint `n_trades ≥ 10`. If no cell satisfies `n_trades ≥ 10`, mark (symbol, sub-window) as INSUFFICIENT_DATA and exclude from §4 aggregation.

**Why max-net-pnl cell:** R1 success criterion is "is there a viable parameter combo for this symbol+window?", not "is the average cell viable". Max-net-pnl cell is the analog of A.4-1's `optimize_symbol` selection rule — picking the cell that the operator would actually deploy if R1 advances.

**Anti-overfitting safeguard (cross-sub-window validation):**

The cell selected for symbol S in sub-window A must NOT be required to match the cell selected for symbol S in sub-windows B or C — selection is per-sub-window. The conjuntive "success in all 3" requires that **some cell exists** in each sub-window for each criterion-passing symbol; it does NOT require the same cell across sub-windows. Cross-sub-window cell stability is a separate diagnostic in §4.4 (informative, not gating).

### §4.2 — Failure modes pre-registrados

| Outcome (across 3 sub-windows) | Verdict | Phase 2 action |
|---|---|---|
| Primary ✓ in 3/3 sub-windows + Secondary ✓ in 3/3 sub-windows | **R1 SUCCESS** | Proceed to integrated re-run (audit §A.5 step 4) — A.4-1.5 + A.4-1 with SIGNAL_EXIT active over full pre-holdout |
| Primary ✗ in any sub-window + Secondary ✓ in ≥2/3 sub-windows | **R1 INCONCLUSIVE** (mechanism right, magnitude insufficient) | Per kickoff: escalate to R3 con SIGNAL_EXIT incorporated al baseline. Operator decision required (§4.5). |
| Primary ✗ + Secondary ✗ in ≥2/3 sub-windows | **R1 FAIL** | Exit mechanism is not the bottleneck. Per §A.4 prior recalibration: drop estimate <10% triggers H5 escalation consideration. |
| Primary ✓ in 2/3 + Secondary ✓ in 3/3 | **R1 SUCCESS-CONDITIONAL** (regime-dependent) | Document which sub-window failed + regime characterization. Operator decides: advance with regime-conditional notation, or treat as INCONCLUSIVE. |

### §4.3 — Cell selection rule (re-stated for clarity)

[Already in §4.1; this anchor exists to make §4 readable as a self-contained spec.]

### §4.4 — Cross-sub-window cell stability (informative, not gating)

For each currently-bankrupt symbol, report:
- Cell selected in window A: `(sl_A, be_A, lrc_A)`
- Cell selected in window B: `(sl_B, be_B, lrc_B)`
- Cell selected in window C: `(sl_C, be_C, lrc_C)`

If a symbol passes §4 primary+secondary in all 3 sub-windows but cells diverge wildly (e.g., `lrc_A=35, lrc_C=55`), flag as "regime-sensitive params" — informative for any downstream operator decision but does NOT void R1 SUCCESS verdict per §4.

If cells converge (same cell selected in 3/3 sub-windows for ≥3 symbols), flag as "robustly tunable" — bonus signal for operator interpretation.

### §4.5 — Operator decision hooks (R1 INCONCLUSIVE branch)

Per kickoff: "si primary falla pero secondary muestra TIME_LIMIT shift correcto, R1 mechanism validated pero magnitude insuficiente — escalar a R3 con R1's dynamic exit incorporado al baseline."

**Pre-registered operator decision items** (require sssamuelll explicit confirmation BEFORE Phase 2 advances):

1. R3 con SIGNAL_EXIT incorporated as baseline (single-alternative R3 per audit §A.6) — operator confirms R3 candidate signal (recommendation: trend-pullback per audit §A.6).
2. OR: H5 escalation (basket re-validation under post-fix simulator + R1+R2 fixes) — defer R3.

Operator picks one. Pre-registered: NO running both in parallel. NO running R3 with multiple signal candidates.

### §4.6 — Halt-guard scope (2026-05-13 amendment, post-PR #330)

§10 halt + `n_windows < 3` → `R1_INSUFFICIENT_DATA` **only** when the naive verdict is `R1_SUCCESS` / `R1_SUCCESS_CONDITIONAL`. Negative verdicts (`R1_FAIL` / `R1_INCONCLUSIVE`) on partial windows are **preserved** — §10 was pre-registered to act on dispositive partial negative evidence, not to suspend its inferential weight.

**Asymmetry rationale:** spurious favorable verdicts have one-sided incentive bias (operator + project momentum favor declaring success early); honest negative evidence does not carry the same bias. Demanding symmetric sample-size discipline here would penalize the discipline-preserving move (acting on dispositive evidence) and reward the discipline-eroding move (burning compute to formalize an already-decided outcome).

**Scope of this amendment:** clarifies §4.2 verdict-table behavior under §10 halt. Does **not** modify the verdict criteria themselves nor the §10 halt threshold. Implementation lives at `tools/r1_verdict.py:_classify_verdict` (PR #330, merged 2026-05-12).

**Implication for R1:** recorded R1 verdict (halt=True, n_windows=1, primary 0/0, secondary 0/0) classifies as `R1_FAIL` per the asymmetric guard. §A.4 P(viable) <10% trigger fires; H5 escalation strongly considered (per audit §A.4 + §A.5).

**Methodology framing:** this is an **explicit pre-reg amendment**, not a soft post-hoc clarification. The asymmetric scope was chosen by the dev agent during PR #330 implementation as the more methodologically defensible interpretation; operator + reviewer concurred (post-multi-agent review). Documenting the choice here, in the pre-reg, prevents future readers from interpreting the asymmetry as silent rationalization.

---

## §5 · Edge cases pre-registrados

### §5.1 — Degenerate `lrc_exit_threshold = 35` case

If `lrc_exit_threshold = 35` for LONG positions: SIGNAL_EXIT triggers when LRC ≥ 35. Since LONG entry requires LRC ≤ 25, the trade entry-to-exit window allows only 10 percentile-points of reversion before SIGNAL_EXIT fires.

**Risk:** trades close very fast with minimal gain (if LRC immediately reverts) or at SL (if LRC continues lower before reverting). Trade duration becomes ~few bars. Could artificially inflate trade count without producing edge.

**Pre-registered handling:** include `lrc_exit_threshold = 35` in sweep; if it dominates `argmax_net_pnl` for symbols with `avg_trade_duration_hours < 2`, flag as "potentially degenerate fast-cycle" in derivation_audit.md and exclude from §4 aggregation for that (symbol, sub-window) pair. Operator reviews the flag before R1 SUCCESS verdict is confirmed.

### §5.2 — `lrc_exit_threshold = 55` reaches TIME_LIMIT before firing

If for some symbol the median time-to-LRC-55 > 5h (TL), SIGNAL_EXIT @ 55 essentially never fires for LONG (price doesn't reach LRC=55 within the holding window). The cell collapses to baseline (TP or TL exits dominate).

**Pre-registered handling:** if `lrc_exit_threshold = 55` shows `SIGNAL_EXIT% < 5%` for ≥6 of 8 symbols, document as "55 is past the achievable horizon under current TL gate" but no other action — the data is informative. Sweep continues.

### §5.3 — SIGNAL_EXIT firing intra-warmup

LRC requires 100-bar warmup. During first 100 bars of any sub-window, LRC is undefined. SIGNAL_EXIT must not evaluate during warmup.

**Pre-registered handling:** harness skips SIGNAL_EXIT branch when `bar_idx < 100`. Documented explicitly in tool source comment + manifest. Tested via assertion in unit test before sweep.

### §5.4 — Bankruptcy halt interaction

Per `simulate_strategy` post-#313: BANKRUPT exits halt new entries for that symbol. SIGNAL_EXIT does NOT change bankruptcy threshold. Pre-bankruptcy trades may be SIGNAL_EXIT; post-bankruptcy halt symbol is dormant (same as baseline).

**Pre-registered handling:** no special logic. BANKRUPT count remains diagnostic. If bankruptcy_count drops sharply with SIGNAL_EXIT active, that's a positive signal — captured in §4 secondary criterion (reduced TIME_LIMIT% likely correlates).

### §5.5 — Cost amplification persistence

Cost model v1 (linear, H8 confirmed in audit) is NOT touched by R1. SIGNAL_EXIT closes at `close[i]` — same cost calculation as TIME_LIMIT. If H8's slippage destruction dominates, SIGNAL_EXIT may not rescue P&L despite mechanism shift.

**Pre-registered handling:** no R1 action. R1 INCONCLUSIVE result with secondary ✓ but primary ✗ on PF criterion is the natural surface where H8 amplification would manifest. Documented in §A.7 of audit spec already; R1 inherits.

---

## §6 · Deliverable structure

After operator approval of this pre-reg, R1 execution lands the following on a new branch (stacked on current `feat/r2-gates-rederivation-pre-reg-2026-05-11` or on main post-#324 merge — operator choice in §9):

```
data/retune/2026-05-12-r1-dynamic-exit/
├── derivation_audit.md       # methodology recap + per-symbol per-sub-window verdict + cross-window stability
├── manifest.json             # cutoff, code_commit, leakage_check, sub_windows, sweep grid, baseline ref
├── sweep_results_A.json      # 75 cells × N_eligible_symbols, sub-window A
├── sweep_results_B.json      # sub-window B
├── sweep_results_C.json      # sub-window C
├── baseline_pre_signal_exit.json  # per (symbol, sub-window) baseline backtest (no SIGNAL_EXIT) — for Δ comparison
├── exit_distributions.json   # per cell, per symbol: SL/TP/SIGNAL_EXIT/TIME_LIMIT/BANKRUPT counts
└── README.md                 # summary verdict + primary/secondary tables + cross-window stability
```

Plus:
- `tools/r1_signal_exit_sweep.py` — reproducible sweep script (adapted from `tools/q2_grid_topology_diag.py` pattern)
- New patch on `backtest.py` adding SIGNAL_EXIT branch (per §2.2) — gated behind config flag `dynamic_exit_enabled` for safety; live path unchanged unless flag is set
- Unit test `tests/test_signal_exit.py` — verifies tie-break order + warmup skip + per-direction logic
- Update `docs/superpowers/specs/es/2026-05-11-strategy-structural-audit.md` §10 history table con R1 outcome
- PR comment con (a) verdict per §4, (b) updated prior estimate per §A.4 checkpoint, (c) operator decision hooks per §4.5

**Live-path safety:** the SIGNAL_EXIT branch is wrapped in `if cfg.get("dynamic_exit_enabled", False):` — defaults to False. Production scanner (`btc_api.py`, `btc_scanner.py`) keeps current static-TP behavior. R1 sweep runs with the flag set in harness only. NO changes to `config.defaults.json:dynamic_exit_enabled` proposed in this pre-reg. Promotion to live is a separate operator decision (out of R1 scope).

---

## §7 · What this pre-reg does NOT cover

- **R3 (signal alternative)** — separate workstream per audit §A.6. R1 is exit-only; entry signal preserved.
- **Cost model v2** — H8 mitigation deferred to issue #325 / cost model v2 epic.
- **`config.defaults.json` promotion of SIGNAL_EXIT** — pre-reg only commits to derivation methodology + sweep. Promotion is post-R1-SUCCESS + operator decision.
- **A.4-1 ATR re-sweep with SIGNAL_EXIT** — happens AFTER R1 SUCCESS in step 4 of audit §A.5 success path. NOT in R1's scope.
- **Holdout (A.4-3)** — hard-blocked by issue #322. R1 does NOT touch holdout under any outcome.
- **Trailing stop, time-decay TP, or any other exit variant** — single-alternative discipline per §A.6. If signal-reversal R1 fails, those candidates are NOT auto-promoted to a "second R1 attempt"; operator decides scope of next investigation.

---

## §8 · Pre-registered decision branches (decision tree summary)

| Branch point | Rule | Reference |
|---|---|---|
| Variant choice | Signal-reversal (single, no iteration) — locked | §2.1 |
| TP estático | Kept active at current per-symbol values | §2.3 |
| Other gates | UNCHANGED (decoupled per R2 pattern) | §2.4 |
| Sweep grid | 5 × 3 × 5 = 75 cells × 10 symbols × 3 sub-windows = 2,250 backtests | §2.5 |
| Sub-windows | A, B, C identical to R2 | §3 |
| Cell exclusion | `n_trades < 10` → INSUFFICIENT_DATA | §4.1 |
| Cell selection | argmax(net_pnl) per (symbol, sub-window) subject to n_trades ≥ 10 | §4.1 |
| Cross-window stability | Informative only, not gating | §4.4 |
| R1 SUCCESS | Primary 3/3 + Secondary 3/3 | §4 |
| R1 INCONCLUSIVE | Primary fail any sw + Secondary ≥2/3 | §4.2 + operator hook §4.5 |
| R1 FAIL | Primary fail + Secondary ≥2/3 fail | §4.2 |
| `lrc=35` degenerate | Flag if `avg_trade_duration < 2h` dominates | §5.1 |
| `lrc=55` past horizon | Document but continue | §5.2 |
| Warmup | Skip SIGNAL_EXIT for `bar_idx < 100` | §5.3 |
| Live path safety | Flag-gated; defaults to False; no live promotion in R1 | §6 |

Cada branch tiene rule pre-registered ANTES de ver el data. Eliminates rationalización post-hoc.

---

## §9 · Open questions for operator

3 short confirmations + 1 optional choice before R1 execution begins:

### §9.1 — [REQUIRED] Confirm signal-reversal as the variant

Pre-reg locks signal-reversal per §2.1 justification (mean-reversion frame alignment, single-param, audit §6 candidate). Alternative paths if you disagree:

- (a) Trailing stop instead — would require R1 to re-justify against §2.1's frame-alignment argument. Possible if you read trailing stop as "captures whatever move materializes regardless of frame".
- (b) Defer R1 entirely; advance to R3 — possible if you interpret R2 verdict as so dispositive that exit changes are wasted compute pre-signal change.
- (c) Run R1 with signal-reversal as locked. **Default if no override.**

### §9.2 — [REQUIRED] Confirm sweep grid dimensionality

5 × 3 × 5 = 75 cells preserves Q2 interpretability while limiting compute. Alternatives:
- (a) Full 7 SL × 5 TP × 3 BE × 5 lrc_exit = 525 cells. Compute ~5×. Argument: full Q2 analog; rejection of TP-as-frozen.
- (b) Reduce to 3 SL × 3 BE × 5 lrc_exit = 45 cells. Compute ~0.6×. Argument: focus on lrc_exit dimension; SL/BE secondary.
- (c) **75 cells as proposed.** Default.

### §9.3 — [REQUIRED] Confirm sub-windows

Sub-windows A/B/C are R2-identical. Reusing them means leakage protection is consistent; also means we don't independently validate R1 sub-window choice. Alternative:
- (a) Add a 4th sub-window (e.g., 2024-09 → 2024-12, late-2024 trending pre-A.4-1-train) for additional regime coverage. Compute +33%.
- (b) **Reuse R2 A/B/C as proposed.** Default.

### §9.4 — [OPTIONAL] Branch placement for R1 execution PR

- (a) New branch stacked on `feat/r2-gates-rederivation-pre-reg-2026-05-11` (PR #324 chain). Cleaner if #324 isn't merged yet.
- (b) New branch off `main` post-#324 merge. Cleaner if #324 is about to merge.
- (c) **Auditor recommendation: (b)** — #324 is a complete unit (R2 verdict + #317 closure); rebasing on top of it adds dependency without benefit. R1 standalone branch is clearer.

### §9.5 — [OPTIONAL] Compute budget hard cap

R1 sweep estimate (per §11): ~3-4h paralelizado. If runtime exceeds, abort + diagnose? Default: yes — `tools/r1_signal_exit_sweep.py` includes wall-clock check at sub-window boundary; if (window A complete) + (window B started) but elapsed > 4h, abort with progress checkpoint and surface to operator.

---

## §10 · Pre-execution math sanity check

**Mechanism plausibility check (per kickoff requirement):**

The pre-R1 query showed TIME_LIMIT 44% / SL 36% / TP 4% / BANKRUPT 16% over 52 trades.

For SIGNAL_EXIT to materially shift the distribution, it must fire BEFORE the 5h TL on a meaningful fraction of trades. Plausibility argument:

1. **LRC has measurable autocorrelation at 1H scale.** The LRC is computed over a 100-bar window; LRC(t) and LRC(t+1) differ by a small percentile shift in normal regimes. For LRC to move from ≤25 (entry) to ≥50 (exit @ threshold=50) within 5h, price must traverse ~25 percentile points of the channel. R2 §3 derivation confirmed time-to-1-ATR ≈ 5h on 1H bars; 25 percentile points of LRC corresponds (very roughly) to 0.5–1 ATR depending on channel width and slope. So the 5h horizon is **right at the boundary** of what's achievable — signal-reversal exits should fire in some non-trivial fraction (~20-50%) of trades, not zero and not all.

2. **The 22 TIME_LIMIT trades are NOT all "no movement" trades.** Some closed at 5h with positive pnl (price moved favorably but didn't hit TP), some with negative pnl (price moved unfavorably but didn't hit SL). SIGNAL_EXIT replaces a portion of these — specifically, the ones where LRC did revert toward mid before 5h. Even if only 30% of TIME_LIMIT trades are SIGNAL_EXIT-eligible, that's 7 trades shifted out of 22 — TIME_LIMIT% drops from 44% to ~30%, which is meaningful but does NOT meet the secondary criterion threshold (TIME_LIMIT% < 20% required).

3. **For secondary criterion to pass**, SIGNAL_EXIT must capture >50% of currently-TIME_LIMIT trades. That's a strong claim — bets that LRC reverts toward mid in MAJORITY of cases within 5h. **NOT mathematically determined either way.** Plausible mechanism, uncertain magnitude. → COMPUTE JUSTIFIED.

4. **For primary criterion to pass**, SIGNAL_EXIT must additionally produce avg_pnl_per_trade > 0 in ≥1 symbol AND net_pnl > 0 in ≥3 symbols. This requires not just mechanism shift but also that the captured reversions are profitable on average net of cost tax. Cost model v1 (H8 confirmed) eats slippage on both entry and exit; SIGNAL_EXIT doesn't reduce cost per trade. Given H1 (signal expectancy ≈ -0.9R per trade in current calibration), SIGNAL_EXIT would have to convert losses to break-even or small wins to lift avg_ppt above zero. **Plausible but uncertain.**

**Verdict of pre-execution math sanity check: PROCEED.** Unlike R2 (where math deterministically forecast FAIL), R1 has uncertain magnitude with plausible mechanism. Compute is justified — sweep produces information not derivable from arithmetic.

**Halt condition:** if during execution, the first sub-window (A) shows TIME_LIMIT% > 35% across >6 symbols (i.e., SIGNAL_EXIT essentially didn't fire), halt before sub-windows B+C. The mechanism failed to engage; running B+C would not change interpretation. Operator notified for diagnosis (most likely cause: LRC computation mismatch between signal and exit code, or bug in tie-break order).

---

## §11 · Compute estimate

| Stage | Estimate | Notes |
|---|---|---|
| Code patch (`backtest.py` SIGNAL_EXIT branch + flag) | 2-3 h | Includes unit tests for tie-break + warmup |
| Sweep harness (`tools/r1_signal_exit_sweep.py`) | 1-2 h | Adapted from `tools/q2_grid_topology_diag.py` |
| Sweep execution (2,250 backtests, parallelized) | **3-4 h wall-clock** | 8 workers; per-backtest avg ~30s |
| Baseline backtests (30 = 10 sym × 3 sub-win) | 15 min | Sequential, single config each |
| Derivation audit + JSON outputs + README | 1-2 h | Math/data interpretation + PR comment |
| **Total session compute time** | **~10-12 h** | Single contiguous session OR split across 2 sessions |

**Halt-conditional savings:** if sub-window A halt fires per §10 verdict, total drops to ~5-6 h (no B+C sweeps).

**Comparison with Q2 baseline:** Q2 was 1,050 backtests in ~1.5h. R1 is 2,250 backtests = ~3-4x compute. Within operator-acceptable range per audit §A.3 (compute budget).

---

## §12 · Auditor prior on R1 outcome

**Auditor (Claude Opus 4.7) prior antes de execution:**

| Outcome | Probability | Reasoning |
|---|---:|---|
| **R1 SUCCESS (primary 3/3 + secondary 3/3)** | ~12% | Requires LRC reversion to be both fast AND profitable across regimes. High bar given H1 (signal expectancy ≈-0.9R). |
| **R1 INCONCLUSIVE (secondary ✓, primary ✗)** | ~35% | Mechanism shift is plausible (LRC has some autocorrelation), but cost tax + signal expectancy may keep avg_ppt negative even with shifted exits. |
| **R1 FAIL (secondary ✗ + primary ✗)** | ~45% | Most likely if LRC doesn't revert fast enough on average within 5h, or if SL hits dominate before SIGNAL_EXIT fires. Cost amplification (H8) compounds. |
| **R1 SUCCESS-CONDITIONAL (primary 2/3 + secondary 3/3)** | ~8% | Regime-dependent edge possible — sub-window C (most recent) could pass while A or B fail under different volatility regimes. |

**Joint prior on Phase 2 path post-R1:**

| Phase 2 outcome (R1 + R3 joint) | Probability | Conditional on R1 result |
|---|---:|---|
| R1 SUCCESS → integrated re-run passes | ~8% | (12% × 65% conditional that integrated re-run survives) |
| R1 INCONCLUSIVE → R3 with SIGNAL_EXIT baseline → R3 SUCCESS | ~7% | (35% × 20% R3 success conditional on signal-reversal baseline) |
| R1 FAIL → H5 escalation → basket re-validation produces viable subset | ~3% | (45% × 7% H5 net-positive on a different basket) |
| **Joint P(viable strategy) post-R1** | **~12-18%** | Roughly unchanged from current 12-15%; R1 result will sharpen the estimate substantially. |

**Operator's prior (declared kickoff): 12-15%.** Gap analysis:
- My R1 SUCCESS estimate (12%) may be too optimistic if LRC autocorrelation at 1H scale is weaker than I'm assuming. Pre-R1 query had 4% TP rate — if LRC doesn't move 25 percentile points within 5h either, primary criterion is essentially infeasible.
- My R1 INCONCLUSIVE estimate (35%) may be too high if H1 (-0.9R per trade) is too pessimistic for SIGNAL_EXIT-shifted trades. SIGNAL_EXIT could capture early small wins that lift avg_ppt above zero.

**Bayesian update plan post-R1:**
- Update P(viable) based on actual R1 outcome category.
- If R1 SUCCESS: P(viable) jumps to ~30-50%; advance to integrated re-run.
- If R1 INCONCLUSIVE: P(viable) ~10-15%; R3 is the next test (operator decides candidate).
- If R1 FAIL: P(viable) drops to ~5-8%; H5 escalation strongly considered per §A.4 trigger (<10% threshold crossed).

**§A.4 prior re-evaluation checkpoint:** post-R1 PR comment must include explicit Bayesian update with magnitude shift documented in 2-3 sentences. Same pattern as R2.

**Agent tooling note (added 2026-05-15).** The 2-3-sentence prose update is the default §A.4 mechanic. If the operator wants the post-R1 update materialized as a formal posterior — beta-binomial over P(viable) given R1 verdict, hierarchical model across símbolos × exit-strategy variants, or LOO/WAIC between {R1 SUCCESS, R1 INCONCLUSIVE, R1 FAIL} hypotheses — invoke the `pymc-bayesian-modeling` skill (installed 2026-05-15, available via the `Skill` tool). PyMC + NUTS + LOO/WAIC + posterior predictive checks ship with the skill. Default remains prose-only; PyMC is on-demand.

---

## §13 · Methodology limitations carried forward

Per audit §A.7 + §A.8 + §6 retrospective, R1 inherits these caveats:

1. **Signal expectancy gap (H1).** R1 doesn't fix the signal — it changes what happens to losing trades. If signal is fundamentally not predictive in this market regime, SIGNAL_EXIT shifts losing-distribution shape but doesn't lift expectation.
2. **Cost model v1 (H8).** Slippage on SIGNAL_EXIT closes is identical to TIME_LIMIT closes. Cost amplification on thin-liquidity bars persists. Issue #325 separate.
3. **Mean-reversion frame anchor (R2 §6).** SIGNAL_EXIT only makes sense under the LRC mean-reversion thesis. If R3's eventual signal alternative is momentum/breakout, SIGNAL_EXIT-as-defined needs re-derivation under the new frame.
4. **Per-symbol bankruptcy halt (#313).** Symbols halt independently at $1K floor. R1 doesn't change this. If a symbol bankrupts in 1-2 trades, SIGNAL_EXIT can't help (insufficient data).

---

## §14 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-12 | Pre-reg sub-spec inicial — drafted from kickoff prompt + R2 derivation_audit + structural audit context | Claude Opus 4.7 (sesión kickoff) + sssamuelll |
| 2026-05-13 | §4.6 amendment — halt-guard scope clarified (asymmetric, favorable-direction only); references §4.2 + §10. Post-PR #330 multi-agent review surfaced the methodology question; operator + reviewer concurred on interpretation A. | sssamuelll + Claude Opus 4.7 |

Reservar líneas para iteración post-operator-review en §9.
