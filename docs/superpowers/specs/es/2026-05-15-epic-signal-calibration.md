# Epic — Signal calibration (H-signal follow-up post #338 Phase 3)

**Fecha:** 2026-05-15
**Status:** DRAFT — Q1-Q6 locked 2026-05-15 vía 2 rounds de `AskUserQuestion`; pre-reg formal pendiente; spec doc en review
**Autor:** sssamuelll en colaboración con Claude Opus 4.7
**Tipo:** epic spec — sub-investigation de signal calibration tras #338 Phase 3 verdict `PHASE_3_INSUFFICIENT_DATA`
**Trigger:** PR #349 (Phase 3 verdict, draft) + operator decision branch (C) capturada en PR #349 comment 2026-05-15
**Prerequisito:** Scoping doc `2026-05-15-h-signal-hypothesis-pullup.md` (PR #351 draft) en review
**No es:** retry del #338 / signal-family swap / basket revision (H-basket es epic D scope) / pre-reg formal

---

## 0 · Reconocimiento explícito de boundaries

**Este epic existe solo bajo la premisa de que el #338 Phase 3 verdict `PHASE_3_INSUFFICIENT_DATA` (PR #349) se preserva como independent y NO se re-litiga.** El verdict capturó halt H2 firing en Window A primary: 8 de 8 in-coverage symbols emitieron `n_trades < 5` over 91 days. Per pre-reg §4.6 asymmetric halt-guard, naive Window A `STRONG_PASS` fue override-eado a `PHASE_3_INSUFFICIENT_DATA` porque mecanismo barely engaged.

Este spec NO propone:

- Re-litigar el verdict del #338 (PR #349 sigue independent y mergeable cuando operator marque ready)
- Modificar las decisiones locked del #338 §8 silenciosamente (la tensión con §8.1 + §8.4 se cita explícitamente en §9)
- Signal-family swap (e.g., normalized momentum, breakdown-of-range) — si diagnostic Phase 2 muestra que B-set intervention es necesaria, epic C escala a meta-epic per Q6 lock
- Basket revision (epic D scope, requires basket-unlock decision separate per #338 §4.1)
- Holdout touch independent (Q5 lock: joint con epic D outcome)
- Promote a producción (`config.defaults.json`) bajo este epic

Lo que SÍ propone:

- Diagnostic-first approach (Q2 locked): Phase 2 corre unchanged equal-weight + Donchian-9 sobre Window A con observability ON, analiza firings per-lookback, decide intervention based on data
- Intervention space limitado a **A-set** (Q6 locked): re-weighting alternatives sobre Donchian detector + subset lookbacks + threshold-flip rules
- Per-lookback observability como prerequisite (Q1 locked): counts + magnitudes per lookback per window
- Misma basket que #338 (epic D scope-separation)
- Misma windows que #338 (Q4 locked) — comparability directa con Phase 3 verdict
- Heredar `N_TRADES_MIN_FOR_ELIGIBILITY = 5` del #338 §10.4 (Q3 locked)
- Holdout shot JOINT con epic D outcome (Q5 locked)

---

## 1 · Resumen ejecutivo

El #338 Phase 3 verdict `PHASE_3_INSUFFICIENT_DATA` (PR #349, 2026-05-15) deja abierta una pregunta diagnostic: ¿por qué el mecanismo de signal del Donchian ensemble barely engaged en Window A bear 2022 (8/8 symbols con `n_trades < 5` over 91 days)? El audit §7 del Phase 3 (`data/retune/2026-05-14-regime-allocation/derivation_audit.md`) lista dos hipótesis no-exclusivas:

- **H-signal-A (dilution)**: short lookbacks DO fire breakouts pero equal-weight aggregation sobre 9 lookbacks (incluyendo 150d/250d/360d flat) **diluye `|sum|`** por debajo del flip-de-dirección threshold
- **H-signal-B (no-firing-enough)**: short lookbacks NO emiten suficientes breakouts; Donchian es signal family equivocada para bear momentum a 5-10d

El scoping doc `2026-05-15-h-signal-hypothesis-pullup.md` (PR #351 draft) distingue A vs B como sub-hipótesis testable separately con intervenciones disjuntas. Este epic implementa la investigation con diagnostic-first approach (Q2): observability primero, decision second, intervention third.

### Cambios estructurales clave vs #338

| Dimensión | #338 (PHASE_3_INSUFFICIENT_DATA) | Epic C (this) |
|---|---|---|
| Signal | Donchian ensemble 9 lookbacks (5/10/20/30/60/90/150/250/360 d), equal-weight | Donchian ensemble (heredado) + observability per-lookback + A-set interventions (re-weight/subset/threshold-flip) |
| Aggregation | Equal-weight vote (LOCKED §8.1) | Equal-weight como baseline; alternatives A-set pre-registradas en Phase 3 pre-reg |
| Lookback list | Zarattini exact 9 (LOCKED §8.4) | Subset alternativas (e.g., 5+10+20 only) en A-set space |
| Observability | Solo `sum` agregado + `n_trades` derived | **Per-lookback counts + magnitudes (LOCKED Q1)** |
| Approach | Pre-registered run; verdict via §4.6 halt-guard | **Diagnostic-first (Q2)**: Phase 2 = diagnostic, Phase 3 = sweep con intervention chosen |
| Scope intervention | N/A (validation de Zarattini-as-is) | **A-set only (Q6)**: signal-family swap escala a meta-epic |
| Basket | 10 símbolos (LOCKED §4.1) | 10 símbolos (heredado; epic D scope-separation) |
| Cost model | v2 sqrt-participation + funding (Phase 0) | v2 heredado |
| Sizing/exits | Vol-targeting + signal-based (Phase 1) | Heredado |
| Windows | A=2022-bear 91d, B=2023-recovery, C=2025-Q1 | Mismos (LOCKED Q4) — comparability directa con #338 verdict |
| N_TRADES_MIN | 5 (LOCKED §10.4 #338) | 5 (heredado, LOCKED Q3) |
| Holdout | Phase 5 gated por phases anteriores | Joint con epic D outcome (LOCKED Q5) |

### Expectativas pre-registradas

Epic C NO tiene Sharpe/CAGR/DD targets propios — su PRIMARY metric es **¿produce el A-set intervention `n_trades ≥ 5` en Window A primary sobre ≥ 6/8 in-coverage symbols, sin introducir bankruptcies y sin degrade material de win rate vs equal-weight baseline?**

Si SÍ → A-set intervention validated, advance to Phase 4 walk-forward.
Si NO → epic C verdict candidates: `A_INTERVENTION_INSUFFICIENT` (equivalente al `PHASE_3_INSUFFICIENT_DATA` del #338) o `DIAGNOSTIC_B_DETECTED` (escalation gate per Q6).

---

## 2 · Contexto y justificación

### 2.1 Cadena de evidencia que motiva el epic

Ver scoping doc §3 (`docs/superpowers/specs/es/2026-05-15-h-signal-hypothesis-pullup.md`). Resumen factual:

- Window A primary (vol_target=30%), 8 of 8 in-coverage symbols: `n_trades ∈ {1,1,1,1,2,2,2,2,3}` over 91 days
- 4 de 8 con 1 trade only (open at first signal, hold to SIM_END)
- 0 bankruptcies (vol-targeting + leverage cap 2x prevented capital destruction)
- 0 symbols hit `N_TRADES_MIN_FOR_ELIGIBILITY = 5`

Audit §2 inferencia mecánica:

> Why so few trades? [...] Short lookbacks (5d, 10d) **likely** fired bearish breakouts but were diluted by the longer-lookback flat votes in the equal-weight sum.

La frase "**likely** fired" es inferencia, NO evidence. La sweep tool actual del #338 NO reportó count de signal firings por lookback individual — reportó solo el `sum` agregado por bar y `n_trades` derivado de transitions del `sign(sum)`. Gap observacional documentado en scoping §3.3.

### 2.2 Por qué un epic separado y no extensión del #338

El #338 §8.1 + §8.4 son hard locks por design — validan Zarattini-as-is. Cambiar aggregation o lookbacks DENTRO de #338 violaría el lock + invalidaría la pre-reg del Phase 2. Epic C es structurally distinct: NO valida Zarattini-as-is; investiga calibration alternative que podría producir signal sufficient para evaluar.

Spec/pre-reg de epic C cita y reconoce esta tension explícitamente (§9). El reviewer del pre-reg debe poder confirmar la trazabilidad.

### 2.3 Por qué diagnostic-first y no intervention-first

Operator decision Q2 = diagnostic-first. Razones documentadas en AskUserQuestion 2026-05-15:

- Cheap upfront (1 diagnostic sweep + 1 analysis vs 2-3 intervention sweeps pre-registered)
- Mitigates researcher degrees-of-freedom risk via pre-registered diagnostic→intervention mapping table (locked en el pre-reg formal Phase 0 deliverable #3)
- Si diagnostic natural era A, las B-set interventions de intervention-first hubieran wasted compute — diagnostic-first preserva el budget para la intervention correcta
- Análoga estructural al patrón del #338 §4.6 asymmetric halt-guard: stop antes de inferir, preserva la bala única

Trade-off aceptado: introduce a small window where researcher could rationalize post-hoc. Mitigation: el pre-reg formal Phase 0 deliverable #3 documenta la diagnostic→intervention mapping table BEFORE running diagnostic (counts ranges → A-set alternative, magnitude patterns → B-set escalation trigger).

---

## 3 · Tesis y predicciones falsables

### 3.1 Hipótesis central

> *En crypto bear regime (Window A 91d), el bajo `n_trades` del Donchian ensemble equal-weight reflects either dilution de short-lookback breakouts por la mayoría flat (A) o no-firing-enough estructural del detector Donchian a 5-10d (B). Una intervención local sobre aggregation/lookback subset/threshold-flip rules — **sin cambiar la signal family** — puede recuperar `n_trades ≥ 5` SOLO si A es la hipótesis predominante. Si B predomina, ninguna intervención A-set mueve la aguja y escalation a signal-family swap (meta-epic) es required.*

### 3.2 Predicciones falsables (pre-registradas en Phase 2 diagnostic + Phase 3 sweep)

**P1 — Observability instrumentation funciona:** Phase 1 deliverable produce counts + magnitudes per-lookback per window correctly. Verificable via unit tests + smoke run on Window A.

**P2 (favors A) — Short-lookback firings observed:** instrumentation muestra `count_LONG + count_SHORT` por lookback N ∈ {5, 10, 20} en Window A ≥ threshold pre-registrado (TBD en pre-reg). Implica: short lookbacks DID fire breakouts; A es candidate.

**P3 (favors A) — Magnitude pattern consistent with dilution:** distribución de `|sum|` agregado over Window A muestra mass concentrated cerca de 0 (e.g., p50 < 2) even cuando short-lookback signals were strong individually. Implica: aggregation diluted los signals → re-weighting es candidate intervention.

**P4 (favors B) — Short-lookback firings absent or weak:** counts por lookback N ∈ {5, 10, 20} muestran ≤ threshold OR magnitudes individuales sub-threshold. Implica: detector individual no captured bear momentum → signal-family swap es required → ESCALATION per Q6.

**P5 (intervention validation) — A-set intervention recovers n_trades:** pre-registered A-set intervention (re-weighting alternative chosen via Phase 2 diagnostic mapping) corrida en Phase 3 sweep produce `n_trades ≥ 5` (heredado N_TRADES_MIN=5) sobre ≥ 6/8 in-coverage symbols en Window A sin bankruptcies y win_rate ≥ 30% (umbral conservador, TBD final en pre-reg).

### 3.3 Predicciones que llevarían a FAIL/halt

- **P2 + P3 fail simultáneo** → diagnostic favors B → halt y escalate per Q6
- **P5 fail** después de Phase 3 → A-set intervention insufficient → verdict `A_INTERVENTION_INSUFFICIENT`, considerar close del epic
- **P2 + P3 + P4 ambiguous** (e.g., evidence parcial for both) → fall-through condition: halt + escalate al operator para decisión (no auto-advance)

---

## 4 · Arquitectura propuesta

### 4.1 Universe (basket)

**Heredado de #338 §4.1:** 10 símbolos (BTC, ETH, ADA, AVAX, DOGE, UNI, XLM, PENDLE, JUP, RUNE). `DEFAULT_SYMBOLS` en `btc_scanner.py`. Basket lock preserved per Q5 scope-separation con epic D.

**Caveat heredado:** Phase 3 #338 reportó PENDLE + JUP fuera de coverage (warmup-fail), efectivo 8/10 in-coverage. Epic C inherits.

### 4.2 Signal — Donchian ensemble heredado + observability

**Heredado de #338 §4.2:**

- 9 lookbacks: 5/10/20/30/60/90/150/250/360 días
- Signal individual: LONG si `close > upper(t-1)`, SHORT si `close < lower(t-1)`, else hold previous
- Baseline aggregation (Phase 2 diagnostic): equal-weight vote, position direction = `sign(sum)`

**Nuevo (Q1 locked) — observability layer:**

- Per lookback N, per window: counts(LONG, SHORT, FLAT) + magnitude distribution(`|sum|`: mean, std, p50, p95)
- Bar-by-bar log opcional behind flag `observability_bar_by_bar` (default OFF; size ~5-15MB per sweep run when ON)
- Emit en sweep tool output como JSON sidecar: `data/retune/<date>/observability_<window>.json`

**A-set intervention candidates (Phase 3, locked post-Phase-2-diagnostic):**

- **A1 — Subset lookbacks:** restrict ensemble a {5, 10, 20} only (3 lookbacks) — elimina dilution structural
- **A2 — Weighted aggregation:** pesos inversely proportional to lookback length (short lookbacks weighted higher) — preserves 9 lookbacks but reduces dilution
- **A3 — Threshold-flip rule:** position direction = sign(sum) solo si `|sum| ≥ k` (e.g., k=2), else flat — filters noisy aggregates
- **A4 — Hybrid (subset + threshold):** combinar A1 + A3 — most aggressive intervention

Pre-reg formal (Phase 0 deliverable #3) lockea cuál de A1-A4 va a Phase 3 basado en Phase 2 diagnostic output (mapping table pre-registrada).

### 4.3 Sizing — vol-targeting heredado

**Heredado de #338 §4.3:** `target_vol_per_symbol = 0.30 / n_active_symbols`. Position size USD = `capital × target_vol_per_symbol / realized_vol_30d_annualized`. Hard caps: position ≤ 20% capital per symbol, ≥ $50 min, sum ≤ 2× capital. K-cap (#309) y bankruptcy halt (#280/#313) preserved.

### 4.4 Exits — signal-based heredado

**Heredado de #338 §4.4:** signal flip, no fixed SL/TP, no fixed TL. Bankruptcy halt + K-cap preserved.

### 4.5 Cost model v2 + funding — heredado

**Heredado de #338 §4.5 (Phase 0 PR #341):** sqrt-participation Almgren-Chriss, EXTREME_PARTICIPATION_CAP_BPS=500, funding per 8h interval per `costs_calibration.json`.

### 4.6 Regime detector — heredado deprecated

**Heredado de #338 §4.6:** `strategy/regime.py` queda legacy, NO consumido por regime-allocation path.

### 4.7 Boundary — A-set scope only

Per Q6 lock: epic C tiene authority limited al **A-set space** (re-weighting + subset lookbacks + threshold-flip rules sobre el detector Donchian individual). Cualquier intervención que requiera:

- Signal family swap (e.g., normalized momentum, breakdown-of-range, RSI-derived breakouts)
- Cambio del detector Donchian individual (e.g., asymmetric upper/lower channels)
- Adición de exogenous filters (e.g., volatility filter, regime filter — recordar que `strategy/regime.py` está deprecated por design)

…requiere **escalation a meta-epic** con su propio scoping + pre-reg. Epic C halts en Phase 2 con verdict `DIAGNOSTIC_B_DETECTED` si Phase 2 diagnostic muestra evidence consistent con B-set need.

---

## 5 · Pre-registración de benchmarks

Estos se pre-registran AHORA en este spec. NO se modifican después.

### 5.1 Floor benchmark (must beat)

**Equal-weight Donchian-9 status quo (PR #349 Window A primary results) sobre la misma ventana A=2022-bear 91d.**

- Métrica: `n_trades ≥ 5` sobre ≥ 6/8 in-coverage symbols
- Source: `data/retune/2026-05-14-regime-allocation/sweep_results_A.json` (extant en main post-#349 merge)
- Criterio mínimo: A-set intervention debe producir n_trades ≥ 5 sobre 6/8 in-coverage symbols. Si NO, intervention ineffective.

### 5.2 BTC B&H over Window A (heredado #338 §5.1)

Mantenido como criterio secondary informational. NO primary porque epic C es diagnostic-driven, no P&L-driven. Window A `BTC B&H = -$45,610` (per PR #349 sweep results).

### 5.3 Internal control — status quo

**Equal-weight Donchian-9 baseline (PR #349 results)** como contrafactual directo. A-set intervention debe demostrar improvement material sobre baseline en `n_trades` count sin degrade de win_rate.

### 5.4 Per-lookback firing baseline

Phase 2 diagnostic produce el primer reporte per-lookback counts + magnitudes sobre Window A. Este baseline NO existe en main post-#349 (no era parte del Phase 3 instrumentation). Phase 2 establishes baseline; Phase 3 measures intervention effect contra baseline.

---

## 6 · Criterios de éxito pre-registrados

Estos se pre-registran AHORA. Refinables solo en pre-reg formal (Phase 0 deliverable #3) con justificación.

### 6.1 Criterio PRIMARY (decide PASS / FAIL del epic)

**A-set intervention chosen via Phase 2 diagnostic mapping produces `n_trades ≥ 5` sobre ≥ 6/8 in-coverage symbols en Window A primary, sin bankruptcies, y con win_rate ≥ 30% over closed trades (i.e., excluding SIM_END open positions).**

Si PASS:
- A-set intervention validated emipricamente
- Advance to Phase 4 walk-forward sobre Windows B + C
- Phase 5 (holdout) gating depende de Phase 4 + epic D outcome (per Q5)

Si FAIL:
- Two sub-verdicts:
  - `A_INTERVENTION_INSUFFICIENT` (intervention ran cleanly pero n_trades still < 5): A es candidate pero magnitude insufficient; considerar close del epic
  - `DIAGNOSTIC_B_DETECTED` (Phase 2 fired Q6 escalation trigger): epic C halts en Phase 2 sin running Phase 3; new meta-epic for signal-family swap
- NO ejecutar Phase 4 ni Phase 5

### 6.2 Criterios SECONDARY (informativos, no decide PASS/FAIL)

- **S-C1 — Per-lookback firing distribution:** Phase 2 diagnostic emite per-lookback counts + magnitudes; reportar en derivation_audit
- **S-C2 — Magnitude distribution shift:** comparar `|sum|` distribution pre/post intervention en Phase 3
- **S-C3 — Hold period distribution:** reportar avg hold period under intervention vs equal-weight baseline
- **S-C4 — Cost attribution:** v2 cost + funding accruals per intervention symbol (heredar reporting de #338)
- **S-C5 — Bankruptcy events:** target = 0 (heredar de #338 §6.2 S4)

### 6.3 Halt conditions durante Phase 2 + Phase 3 (asymmetric halt-guard heredado)

Mismo patrón que #338 §10.4 + §4.6:

- **H-C1 (Phase 2) — Diagnostic ambiguous:** Phase 2 diagnostic NO produce evidence clean for A NOR B (mixed magnitudes, contradictory counts). Halt → escalate al operator para decisión, NO auto-advance.
- **H-C2 (Phase 2) — Diagnostic favors B:** P4 confirmada (short-lookback firings ≤ threshold OR magnitudes weak across the board). Halt → emit `DIAGNOSTIC_B_DETECTED` verdict, escalate per Q6.
- **H-C3 (Phase 3) — Intervention introduces bankruptcies:** any A-set intervention causes bankruptcy event en ≥ 1 symbol. Halt → emit `A_INTERVENTION_HARMFUL` verdict, NO advance.
- **H-C4 (Phase 3) — Intervention degrades win_rate materially:** win_rate post-intervention < 50% del baseline (e.g., baseline 40% → intervention < 20%) sobre ≥ 4/8 in-coverage symbols. Halt → consider intervention rejection.
- **Asymmetric halt-guard (heredado #338 §4.6):** si halt fires AND naive verdict en Window A es favorable, override a `<phase>_INSUFFICIENT_DATA` analog. NO inferir desde partial windows.

---

## 7 · Fases de implementación

### Phase 0 — Scoping + spec + pre-reg formal (CURRENT)

- **Deliverable 1:** Scoping doc — DONE vía PR #351 (`2026-05-15-h-signal-hypothesis-pullup.md`)
- **Deliverable 2:** Epic spec doc (this doc) — DONE vía PR pending current sesión
- **Deliverable 3:** Pre-reg formal — PENDING, próxima sesión. Mirror del pattern `2026-05-14-regime-allocation-phase2-pre-reg.md`. Locks:
  - Phase 2 diagnostic threshold ranges (counts pivot, magnitudes pivot)
  - Diagnostic → intervention mapping table (A1-A4 → criterio para elegir)
  - Phase 3 sweep params: which A-set intervention runs, on Window A primary, vol_target sensitivity scope (heredar #338 §8.7 expanded sensitivity)
  - Halt thresholds H-C1 / H-C2 / H-C3 / H-C4 con valores numéricos
  - Asymmetric halt-guard analog (Window A only, NO inferir desde B/C)

- Estimated effort: 1 sesión (pre-reg formal write)
- Blocker: epic spec doc en review

### Phase 1 — Observability instrumentation

- New code en `strategy/donchian_ensemble.py`:
  - Extend signal emission to include per-lookback metadata (count contribution, magnitude contribution)
  - New `emit_observability_metrics(window_bars)` function → returns dict per lookback
- Extend sweep tool `tools/regime_allocation_sweep.py`:
  - Aggregate per-cell observability output → JSON sidecar
  - Optional bar-by-bar log behind flag (`observability_bar_by_bar=True/False`, default False)
- Tests (TDD):
  - `tests/test_donchian_ensemble_observability.py` — 10+ tests (per-lookback emission, aggregation, edge cases)
  - `tests/test_regime_allocation_sweep.py` extended for observability sidecar emission
  - All existing tests pass (no regression on equal-weight baseline behavior)
  - `tests/test_holdout_isolation.py` — 13/13 PASS

- Estimated effort: 1-2 sesiones
- Blocker: Phase 0 deliverable #3 (pre-reg formal) merged

### Phase 2 — Diagnostic pass on Window A

- Run unchanged equal-weight + Donchian-9 sobre Window A (mismo input que #338 Phase 3) con observability ON
- Analyze: counts + magnitudes per-lookback
- Apply diagnostic → intervention mapping pre-registrado en Phase 0 deliverable #3
- Emit verdict de Phase 2:
  - `A1_CHOSEN` / `A2_CHOSEN` / `A3_CHOSEN` / `A4_CHOSEN` (advance to Phase 3 with locked intervention)
  - `DIAGNOSTIC_B_DETECTED` (halt + escalation per Q6)
  - `DIAGNOSTIC_AMBIGUOUS` (halt H-C1 + operator escalation)
- Output: `data/retune/<date>-signal-calibration/phase2_diagnostic.json` + derivation_audit
- Estimated effort: 1 sesión (wallclock ~30-60 min compute)
- Blocker: Phase 1 merged

### Phase 3 — A-set intervention sweep

- Run A-set intervention chosen en Phase 2 sobre Window A primary
- Sensitivity sweep (heredar #338 §8.7): vol_target ∈ {0.25, 0.30, 0.35, 0.40} si Phase 2 verdict ≠ `DIAGNOSTIC_B_DETECTED`
- Halt diagnostic per H-C3 / H-C4
- Asymmetric halt-guard per §6.3
- Output: `data/retune/<date>-signal-calibration/sweep_results.json` + `verdict.json` + derivation_audit
- Verdict candidates: `PHASE_3_A_PASS` / `A_INTERVENTION_INSUFFICIENT` / `A_INTERVENTION_HARMFUL` / `PHASE_3_INSUFFICIENT_DATA` (analog asymmetric halt-guard)
- Estimated effort: 2-3 sesiones (matching #338 Phase 3 envelope)
- Blocker: Phase 2 verdict ≠ halt-state

### Phase 4 — Walk-forward on Windows B + C (conditional)

- Si Phase 3 verdict `PHASE_3_A_PASS` → walk-forward sobre Windows B (2023-recovery) + C (2025-Q1)
- Same A-set intervention, same params, same observability
- Verdict aggregated: pass/fail/inconclusive over 3-window walk-forward
- Estimated effort: 1-2 sesiones
- Blocker: Phase 3 PASS

### Phase 5 — Holdout shot JOINT con epic D (gated)

Per Q5 lock: holdout shot SOLO si epic C ∧ epic D ambos validan en sus respectivos phases anteriores. Operator override possible bajo new issue + reasoning.

- Window: `[2025-04-30, 2026-04-30]` (12 meses locked, heredar #246)
- Pre-register criterio de PASS en separate doc antes de ejecutar
- Si PASS: strategy validated joint con epic D; #271 guardrail evaluable for relaxation
- Si FAIL: archived, NO retry (single shot)
- Estimated effort: 1 sesión
- Blocker: epic C Phase 4 PASS ∧ epic D Phase final PASS

### Phase 6 — Live promotion (conditional, heredado #338 phase 6)

Solo si Phase 5 passes. Requires Simón communication + revisor externo + decisión #271.

---

## 8 · Operator decisions locked (2026-05-15)

Las 6 decisiones fueron locked via 2 rounds de `AskUserQuestion` el 2026-05-15. Se preserva la deliberación con la opción elegida marcada **[LOCKED]** para traceability.

### §8.1 — Q2: Approach diagnostic-first vs intervention-first — **LOCKED: diagnostic-first**

- **(a) Diagnostic-first [LOCKED]** — Phase 2 corre unchanged equal-weight + Donchian-9 sobre Window A con observability ON, analiza firings, decide intervention basada en data. Phase 3 sweep con intervention chosen. Mitigates researcher DOF via pre-registered diagnostic→intervention mapping en pre-reg formal.
- (b) Intervention-first — rejected: más expensive, risks wasted compute si diagnosis es mis-aligned.

**Definición operacional:** Phase 2 deliverable es `phase2_diagnostic.json` con counts + magnitudes per-lookback per symbol + mapping verdict (A1/A2/A3/A4/B/AMBIGUOUS). Phase 3 lockea intervention en pre-reg formal antes de ejecutar.

### §8.2 — Q1: Observability granularity — **LOCKED: counts + magnitudes per-lookback**

- **(a) Counts + magnitudes [LOCKED]** — emit por lookback N: count de firings (LONG/SHORT/FLAT), distribución de |sum| (mean, std, p50, p95) over la window. Bar-by-bar log opcional behind flag (default OFF).
- (b) Counts solo — rejected: insufficient para A vs B distinction sin magnitudes.
- (c) Bar-by-bar completo — rejected: overkill, ~10-50MB extra disk + tooling complexity.

**Definición operacional:** `strategy/donchian_ensemble.py` extended con `emit_observability_metrics(window_bars)` function. Output schema: `{lookback: int, count_long: int, count_short: int, count_flat: int, magnitude_mean: float, magnitude_std: float, magnitude_p50: float, magnitude_p95: float}` per lookback. Sweep tool aggregates to JSON sidecar.

### §8.3 — Q3: N_TRADES_MIN — **LOCKED: heredar = 5 del #338 §10.4**

- **(a) Heredar = 5 [LOCKED]** — cita pre-reg ref del #338 §10.4. Methodologically clean: sample-size rule-of-thumb, NO data-fitted. Avoids threshold leakage risk.
- (b) Re-derivar — rejected: requires explicit justify; leakage risk si re-derive sin justify riguroso.

**Definición operacional:** PRIMARY criterio §6.1 usa `N_TRADES_MIN_FOR_ELIGIBILITY = 5` exactly como #338 Phase 3 pre-reg §10.4. Pre-reg formal de epic C cita el #338 ref explícitamente.

### §8.4 — Q4: Windows — **LOCKED: mismos del #338 (A/B/C)**

- **(a) Mismos del #338 [LOCKED]** — Window A=2022-bear 91d, Window B=2023-recovery, Window C=2025-Q1. Comparability directa con Phase 3 verdict. Mismas estructuras de halt-guard heredables.
- (b) Nuevos Windows — rejected: rompe comparability directa con #338 verdict; requires new justification para selección.

**Definición operacional:** Phase 2 diagnostic + Phase 3 sweep + Phase 4 walk-forward usan exactos mismos sub-windows que #338 Phase 3. Bar boundaries y sim_start/sim_end heredados sin modificación.

### §8.5 — Q5: Holdout gating — **LOCKED: joint con epic D**

- **(a) Joint con epic D [LOCKED]** — Holdout shot (bala única per #246/#322) solo si epic C ∧ epic D ambos validan en sus Phase 3/4 respectivos. Preserves la bala para validación del sistema completo (signal calibrated + basket validated).
- (b) Independent — rejected: quema bala en validación parcial del sistema; si epic D falla después, no se puede cross-check.

**Definición operacional:** Phase 5 de epic C NO se ejecuta hasta que (a) epic C Phase 4 PASS AND (b) epic D Phase final equivalente PASS. Override path: new issue + explicit operator reasoning citing why joint constraint relax es justified.

### §8.6 — Q6: Scope si diagnostic muestra H-signal-B — **LOCKED: escalation a meta-epic**

- **(a) Escalation a meta-epic [LOCKED]** — Epic C halts en Phase 2 con verdict `DIAGNOSTIC_B_DETECTED`. Authority limitada al A-set space (re-weighting, subset lookbacks, threshold-flip rules sobre Donchian detector). Signal-family swap (e.g., normalized momentum, breakdown-of-range) requires new meta-epic con su propio scoping + pre-reg.
- (b) Authority dentro de epic C para signal-family swap — rejected: blurs epic boundary; scope creep risk; post-hoc rationalization risk.

**Definición operacional:** Phase 2 diagnostic emite verdict `DIAGNOSTIC_B_DETECTED` si P4 confirmada (short-lookback firings ≤ threshold OR magnitudes weak). Verdict halts epic C; new issue + scoping doc + pre-reg required para signal-family swap meta-epic. NO se ejecuta Phase 3 con B-set intervention DENTRO de epic C.

---

## 9 · Tension explícita con #338 §8 locks

Epic C **necesita romper** §8.1 (equal-weight LOCKED) y/o §8.4 (Zarattini exact 9 lookbacks LOCKED) del #338 para investigar H-signal en Phase 3. Tabla de tension:

| #338 §8 lock | ¿Epic C respeta? | Justificación |
|---|---|---|
| §8.1 equal-weight vote | **MAY BREAK** (Phase 3) | A2/A3/A4 interventions modifican aggregation. Justified: §8.1 validaba Zarattini-as-is; Phase 3 verdict `PHASE_3_INSUFFICIENT_DATA` suspende esa validation. Epic C investiga alternative aggregation explícitamente. |
| §8.2 daily position update | **PRESERVE** | No tocado por epic C. |
| §8.3 portfolio vol target 30% | **PRESERVE** (con sensitivity heredada) | Phase 3 sensitivity sweep sobre vol_target ∈ {0.25, 0.30, 0.35, 0.40} heredado #338 §8.7. |
| §8.4 Zarattini exact 9 lookbacks | **MAY BREAK** (A1, A4 interventions) | A1/A4 restringen ensemble a subset (e.g., {5,10,20}). Justified: same as §8.1 — validar Zarattini-as-is fue suspended; epic C investiga subset alternative. |
| §8.5 SHORT bidirectional | **PRESERVE** | Bidirectional rotational. |
| §8.6 leverage cap 2x | **PRESERVE** | Heredado sin modificación. |
| §8.7 sensitivity sweep compute budget | **PRESERVE** | Phase 3 sensitivity sweep heredado. |

**El reviewer del pre-reg formal Phase 0 deliverable #3 DEBE confirmar la trazabilidad de esta tabla.** El break de §8.1/§8.4 está explícitamente authorized por el `PHASE_3_INSUFFICIENT_DATA` verdict + operator decision branch (C) capturada en PR #349 comment 2026-05-15.

---

## 10 · Risk register

### R-C1 — Diagnostic ambiguous (HIGH probability)

- **Risk:** Phase 2 diagnostic puede mostrar evidence parcial for both A and B (e.g., short lookbacks fire weak signals que also get diluted). Falls between clean A and clean B.
- **Impact:** H-C1 fires → escalation al operator → potential epic stall.
- **Mitigation:** pre-reg formal Phase 0 deliverable #3 documenta explicit thresholds for "ambiguous" verdict, con escalation path articulado. Operator decision via `AskUserQuestion` round.

### R-C2 — A-set intervention insufficient (MEDIUM probability)

- **Risk:** Phase 3 sweep PASS halt diagnostic pero n_trades still < 5 — A es candidate but magnitude insufficient.
- **Impact:** Verdict `A_INTERVENTION_INSUFFICIENT` — epic C terminal sin Phase 4 advance.
- **Mitigation:** scoping doc Q3.3 + §6.1 explicitly pre-register este outcome como FAIL state, no degraded PASS. NO post-hoc threshold relaxation. NO escalate to meta-epic without operator decision.

### R-C3 — Observability instrumentation bug regress (LOW probability)

- **Risk:** Phase 1 instrumentation changes `strategy/donchian_ensemble.py`; bug introduces silent behavior shift on equal-weight baseline.
- **Impact:** Phase 2 diagnostic uses corrupted baseline; verdict invalid.
- **Mitigation:** TDD per Phase 1 (10+ tests sobre observability emission); regression test que equal-weight baseline byte-identical pre/post-instrumentation; smoke run Phase 2 baseline outputs match PR #349 baseline outputs byte-identical pre intervention.

### R-C4 — Scope creep into B-set silently (MEDIUM probability)

- **Risk:** Phase 3 sweep results se interpretan favorably con post-hoc B-set rationalization, blurring epic boundary.
- **Impact:** Epic C ships verdict que claims A validation pero actually mixed A+B reasoning.
- **Mitigation:** Q6 lock + §6.1 PRIMARY criterion explicitly textual: "A-set intervention chosen via Phase 2 diagnostic mapping". Pre-reg formal locks the mapping table BEFORE diagnostic runs. Verdict candidates explicit en §6.1 (no "partial PASS with caveats" option).

### R-C5 — Threshold leakage (LOW probability)

- **Risk:** N_TRADES_MIN=5 re-derived post-hoc para fit verdict outcome.
- **Impact:** Threshold itself becomes free parameter; leakage compromises pre-reg discipline.
- **Mitigation:** Q3 lock — `N_TRADES_MIN_FOR_ELIGIBILITY = 5` heredado from #338 §10.4 explicitly. Pre-reg formal cita ref + asserts no re-derivation. Win_rate threshold (currently 30% draft en §6.1) gets locked en pre-reg formal.

### R-C6 — Coordination overhead con epic D (MEDIUM probability)

- **Risk:** Epic C + epic D running parallel can cause review fatigue, context split, or coordination errors (e.g., basket assumption drift between epics).
- **Impact:** Phase 5 joint decision blocked porque epic D outcome unclear; operator burden increases.
- **Mitigation:** Q5 lock makes Phase 5 explicitly gated; epic D issue creation deferred to operator decision separate; CLAUDE.md auto-loaded para every session preserves context invariants (basket lock, structural fixes).

### R-C7 — Window A is too short for ANY Donchian calibration (LOW-MEDIUM probability)

- **Risk:** 91 days bear 2022 may be insufficient para CUALQUIER Donchian-based ensemble (incluyendo subset {5,10,20}), regardless of A-set intervention. Per scoping §4 supuesto S2.
- **Impact:** All A-set interventions FAIL not because A es wrong sino porque la window misma is inadequate.
- **Mitigation:** Phase 4 walk-forward sobre Windows B + C (heredados, Q4 lock) serves as multi-window sanity check. Si Window A FAIL pero Windows B/C succeed bajo intervention, evidence informs operator decision (potentially: re-open Window selection question en future epic, NO bajo epic C).

### R-C8 — Holdout drift detection (LOW probability)

- **Risk:** Heredado from CLAUDE.md "Caveats heredados — A.4 (#250)": F&G + funding rate hashes freeze snapshot at fetch time; Phase 5 needs drift check.
- **Impact:** Holdout snapshot may have provider-side revisions invalidating bala única integrity.
- **Mitigation:** Phase 5 doc pre-registrará drift check protocol against `data/holdout/MANIFEST.json` hashes before holdout shot. Heredado from #322 closure criterion #3.

---

## 11 · Relación con epics e issues existentes

### Parent / predecessor

- **#338** — parent epic. Phase 3 verdict `PHASE_3_INSUFFICIENT_DATA` (PR #349 draft) es el trigger.
- **#350** — epic C tracker issue (OPEN). This spec is Phase 0 deliverable #2 of 3.
- **PR #349** — Phase 3 verdict ship. Independent; epic C does NOT re-litigate.
- **PR #351** — Phase 0 deliverable #1 (scoping doc). Currently draft.

### Companion (parallel epic)

- **Epic D** — basket non-trending (H-basket). No issue todavía; requires basket-unlock decision per #338 §4.1. Q5 lock makes Phase 5 holdout shot joint.

### Hard blocks respected (preserved indefinitely)

- **#246** — holdout dataset locked, read-only. Phase 5 only authorized touch, AND only joint with epic D.
- **#322** — A.4-3 holdout block active. Joint with epic D resolution required.
- **#271** — invitation guardrail. Re-evaluable solo si epic C ∧ epic D ambos pass Phase 5.

### Nuevos issues proyectados

- Issue tentativo (TBD-C1) — "Phase 0 deliverable #3: pre-reg formal de epic C". Scoping post-spec approval.
- Issue tentativo (TBD-C2) — "Phase 1: observability instrumentation". Scoping post-pre-reg merge.
- Issue tentativo (TBD-C3) — "Phase 2: diagnostic pass". Scoping post-Phase 1.
- Issue tentativo (TBD-C4) — "Phase 3: A-set intervention sweep + verdict". Scoping post-Phase 2 verdict ≠ halt.
- Issue tentativo (TBD-C5) — "Phase 4: walk-forward Windows B + C". Conditional on Phase 3 PASS.
- Issue tentativo (TBD-C6) — "Phase 5: holdout shot JOINT con epic D". Gated.

---

## 12 · Refs

### Internal — primary sources

- **Scoping doc:** `docs/superpowers/specs/es/2026-05-15-h-signal-hypothesis-pullup.md` (PR #351, Phase 0 deliverable #1)
- **#338 spec doc:** `docs/superpowers/specs/es/2026-05-13-epic-regime-allocation-strategy-pivot.md` (pattern source + §8 locks cited)
- **#338 Phase 3 verdict + audit:** `data/retune/2026-05-14-regime-allocation/derivation_audit.md` (§2 mechanism, §7 hypothesis table, §8 operator decision branches)
- **#338 Phase 2 pre-reg pattern:** `docs/superpowers/plans/2026-05-14-regime-allocation-phase2-pre-reg.md` (pre-reg formal mirror)

### Internal — supporting

- **CLAUDE.md** — repo invariants (structural fixes #223/#309/#313 baseline; holdout policy; #338 §8 locks cited in regime-allocation strategy class section)
- **PR #349** — Phase 3 verdict ship (Window A primary sweep, halt H2 diagnostic, verdict `PHASE_3_INSUFFICIENT_DATA`)
- **PR #351** — Phase 0 deliverable #1 (scoping doc)
- **Issue #350** — epic C tracker (scope SÍ/NO, AC1-6, decision tree)
- **Issue #322** — A.4-3 holdout block (closure criteria heredados)

### External (heredado #338 §11)

- Zarattini C., Pagani A., Barbon A. (2025). *Catching Crypto Trends: A Tactical Approach for Bitcoin and Altcoins*. SSRN 5209907. (Donchian ensemble origin; epic C extends but does NOT abandon Zarattini detector)
- Almgren R., Chriss N. (2001). *Optimal Execution of Portfolio Transactions*. Journal of Risk 3, 5-39. (Cost model v2 anchor, heredado)

---

## 13 · Historial de revisiones

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-15 | Initial spec doc post-scoping (Phase 0 deliverable #2 of 3); Q1-Q6 locked vía 2 rounds `AskUserQuestion`; tension table con #338 §8 explicit; pre-reg formal deferred a deliverable #3 | sssamuelll + Claude Opus 4.7 |

---

**End of spec.** Próximo deliverable: pre-reg formal (Phase 0 deliverable #3), pending operator authorization post-review.
