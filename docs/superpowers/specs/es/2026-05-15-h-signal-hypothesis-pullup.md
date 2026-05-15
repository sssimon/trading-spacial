# H-signal hypothesis pull-up — scoping for epic C (signal calibration)

**Fecha:** 2026-05-15
**Status:** SCOPING — pre-spec doc de epic C (signal calibration). NO es epic spec ni pre-reg.
**Autor:** sssamuelll en colaboración con Claude Opus 4.7
**Tipo:** hypothesis pull-up — derivación de claim standalone desde audit del Phase 3 #338
**Trigger:** Phase 3 verdict `PHASE_3_INSUFFICIENT_DATA` (PR #349 draft, 2026-05-15) + operator decision branch (C)
**Prerequisito:** PR #349 ready or mergeada (Phase 3 verdict capturado en main); spec doc + pre-reg formal de epic C pendientes
**No es:** epic spec, pre-reg formal, ni issue tracking — es scoping liviano de claim standalone

---

## 1 · Origen

Phase 3 del epic #338 (regime-allocation strategy pivot) emitió verdict `PHASE_3_INSUFFICIENT_DATA` el 2026-05-15 (sesión 2 closure, PR #349 draft). El §10.4 halt H2 firingó en Window A primary sweep al `vol_target=30%`: 8 of 8 in-coverage symbols emitieron `n_trades < 5` over 91 days. Per §4.6 asymmetric halt-guard, la naive Window A verdict (`STRONG_PASS` por raw $5,259 portfolio P&L vs BTC B&H -$45,610) fue override-eada a `PHASE_3_INSUFFICIENT_DATA` porque mecanismo barely engaged.

Cito audit §7 (`data/retune/2026-05-14-regime-allocation/derivation_audit.md`):

> H2 firing pattern (8/8 symbols below 5 trades in 91 days) is consistent with two non-exclusive hypotheses [...]
> **H-signal:** Equal-weight ensemble dilutes short-lookback breakouts in bear
> Mechanism: Short lookbacks (5d, 10d) DO fire bearish breakouts; long lookbacks (250d, 360d) stay flat; sum is too noisy to flip directionally
> Test: Test alternative aggregation (signal-strength-weighted) or subset lookback (5+10+20 only) on same window

Cito audit §8 (operator decision branches):

> **C. Investigate signal calibration** — Hypothesis H-signal: equal-weight ensemble dilutes short-lookback breakouts. Test subset lookback (e.g., 5+10+20 only) or signal-strength weighting on Window A. Out-of-scope for this epic per pre-reg §7; requires new epic.

**Operator decision (2026-05-15, post-verdict, capturada en PR #349 comment):** branches (C) signal calibration + (D) basket non-trending **SÍ** proceden como new epics separados (no extensiones del #338). Este doc es el primer entregable de scoping de epic C.

---

## 2 · Claim literal (standalone)

H-signal, portada del audit a standalone claim, se descompone en **dos sub-hipótesis no-exclusivas** que el audit conflate como una sola. La distinción es load-bearing porque las intervenciones candidate son disjuntas.

### 2.1 — H-signal-A (dilution)

**Claim:** Los lookbacks cortos (5d, 10d) emiten breakout signals direccionales en regímenes de tendencia, PERO la aggregation equal-weight sobre 9 lookbacks (incluyendo 150d/250d/360d que permanecen flat) **diluye la magnitud del `sum`** por debajo del threshold flip-de-dirección (`sign(sum) != 0`), generando posiciones flat aunque hubo señal individual.

**Implicación mecánica:** el problema NO es el detector individual de Donchian breakout — es la regla de aggregation. Fix candidates: re-weighting (e.g., signal-strength-weighted, weighted-by-lookback-length asymmetric), threshold-based flip (e.g., require `|sum| ≥ k`), o aggregation alternativa entirely.

### 2.2 — H-signal-B (no-firing-enough)

**Claim:** Los lookbacks cortos NO emiten suficientes breakout signals como para mover el `sum`, independiente de la aggregation. En 91-day bear regime, Donchian breakouts son estructuralmente raros — close cruza `lower(t-1)` rara vez incluso con sustained downtrend porque el lower channel desciende con el price, anulando la breakout condition cuando el move es persistente pero no acelera.

**Implicación mecánica:** el problema ES el detector individual — Donchian breakout es la familia de signal equivocada para capturar bear momentum a 5-10d. Fix candidates: signal family distinto (e.g., normalized momentum, breakdown-of-range, regime-conditional triggers), o triggers híbridos.

### 2.3 — Diferencia operacional A vs B

A y B son testable separately y tienen intervenciones disjuntas:

| Escenario | Intervención tipo-A (re-weight aggregation) | Intervención tipo-B (signal family swap) |
|---|---|---|
| Si A es la que aplica | **Funciona** — sub-set o re-weight reduce dilution | NO ayuda — el detector estaba OK, no era el problema |
| Si B es la que aplica | NO ayuda — re-weighting de signals que no firearon es zero | **Funciona** — signal family swap captura el move |
| Si ambas aplican | Re-weighting + signal swap, en ese orden | Signal swap primero, re-weighting después |

**El audit conflate ambas hipótesis** en una sola descripción. Epic C debe distinguirlas observacionalmente ANTES de elegir intervención, NO después.

---

## 3 · Evidencia que la motivó (del audit)

### 3.1 — Observación directa (factual, de la sweep run)

Window A primary at `vol_target=30%`, 8 of 8 in-coverage symbols (PENDLE+JUP excluded por warmup-fail):

| Symbol | n_trades | exit_reasons |
|---|---:|---|
| BTCUSDT | 2 | `SIGNAL_EXIT: 1, SIM_END: 1` |
| ETHUSDT | 2 | `SIGNAL_EXIT: 1, SIM_END: 1` |
| ADAUSDT | 1 | `SIM_END: 1` (single open trade) |
| AVAXUSDT | 2 | `SIGNAL_FLIP: 1, SIM_END: 1` |
| DOGEUSDT | 3 | `SIGNAL_EXIT: 2, SIM_END: 1` |
| UNIUSDT | 1 | `SIM_END: 1` (single open trade) |
| XLMUSDT | 1 | `SIM_END: 1` (single open trade) |
| RUNEUSDT | 2 | `SIGNAL_FLIP: 1, SIM_END: 1` |

- 4 de 8 con 1 trade only (open at first signal, hold to SIM_END).
- 0 bankruptcies (vol-targeting + leverage cap 2x prevented capital destruction).
- 0 symbols hit `N_TRADES_MIN_FOR_ELIGIBILITY = 5`.

### 3.2 — Inferencia mecánica (NO observada directamente)

Audit §2 atribuye:

> Why so few trades? [...] The longer lookbacks (150d, 250d, 360d) include pre-2022 highs that anchor wide upper channels — even sustained 2022 down-trending failed to push `close < lower channel` for long enough to flip the sum decisively. Short lookbacks (5d, 10d) **likely** fired bearish breakouts but were diluted by the longer-lookback flat votes in the equal-weight sum.

La frase "**likely** fired" es inferencia. La sweep tool actual NO reportó count de signal firings por lookback individual — reportó solo el `sum` agregado por bar y `n_trades` derivado de transitions del `sign(sum)`.

### 3.3 — Gap observacional

Epic C necesita instrumentación per-lookback (count de firings, magnitude del sum por bar, distribución de `|sum|` over the window) ANTES de poder distinguir A vs B con evidencia. Sin esos contadores, cualquier elección de intervención es ciega — y un sweep posterior con intervención mal-elegida quemaría compute sin clarificar nada.

---

## 4 · Lo que H-signal asume (supuestos no validados)

**S1. Mecanismo Donchian es estructuralmente apto para crypto bear momentum.** Tanto A como B comparten este supuesto. Si el supuesto es falso, ninguna intervención local sobre Donchian arregla — habría que cambiar de signal family completa (eso sería meta-epic, no calibration).

**S2. Window A es un test válido del mecanismo de signal degenerate.** Asume que 91 días en 2022 bear es ventana suficiente para que un mecanismo de signal calibrado correctamente fire `n_trades ≥ 5`. Si la ventana es too short para CUALQUIER calibration de Donchian, el problema es el design del Phase 2 pre-reg, no la signal — y epic C no es la respuesta.

**S3. El bajo `n_trades` es signal degenerate (no exit degenerate).** En regime-allocation strategy class, exits son `SIGNAL_FLIP / SIGNAL_EXIT` — entries y exits comparten el mismo mecanismo. Es plausible que el bajo trade count refleje exits que no firearon (positions stayed open hasta `SIM_END`) en lugar de entries que no firearon. Audit §3.1 muestra que 4 de 8 cerraron por `SIM_END` (single trade nunca exit-eó), consistent con exit-degenerate parcial. Distinción es no-trivial y debe quedar en observability.

**S4. La basket actual NO es el confound.** H-signal asume que si re-calibrás la signal, el resultado mejora con esta basket fija. H-basket (epic D) asume lo opuesto. Son hipótesis competitivas. Si epic C corre bajo basket actual y H-signal NO se confirma, queda ambiguo si el problema era signal o basket — ambos epics deben coexistir para resolver definitivamente.

---

## 5 · Lo que H-signal NO concluye (boundaries explícitos)

- **NO concluye que el strategy class regime-allocation sea viable.** El verdict Phase 3 fue `PHASE_3_INSUFFICIENT_DATA`, no PASS ni FAIL. Posterior de viability sigue en ~26-39% per pre-reg §12 Bayesian update. Epic C genera evidencia sobre A vs B; el resultado puede informar el posterior pero NO lo cierra solo.
- **NO concluye que las decisiones locked del #338 §8 fueran erróneas.** Equal-weight aggregation + Zarattini exact 9 lookbacks era el design choice correcto para validar Zarattini-as-is. El verdict `PHASE_3_INSUFFICIENT_DATA` habilita preguntar "¿qué calibration podría producir signal sufficient para evaluar?" — eso es metodológicamente distinto a "los locks estuvieron mal".
- **NO concluye que la fix es subset `{5,10,20}` ni signal-strength-weighted.** Audit §7 las menciona como test candidates, no como recommendations. Epic C debe diseñar la calibration con criterios pre-registrados, no implementar las del audit literal.
- **NO toca H-basket.** Esa es epic D scope, requiere basket-unlock decision separada (epic #338 §4.1 operator hard-lock).
- **NO re-litiga el verdict Phase 3 del #338.** PR #349 sigue independent; epic C arranca como new spec/issue con su propio ciclo Phase 0/1/2/3.

---

## 6 · Implicaciones para epic C (no es diseño todavía)

Esta sección lista qué TIENE que estructurar el epic C spec/pre-reg, NO el diseño concreto.

### 6.1 — Observability per-lookback es prerequisite

Antes de cualquier sweep de re-calibration, epic C debe agregar instrumentación a `strategy/donchian_ensemble.py` y al sweep tool para emitir:
- Count de firings por lookback (count LONG, SHORT, FLAT por N en la window).
- Distribución de `|sum|` por bar (histogram + percentiles, para ver si stays cerca de 0 o cruza thresholds).
- Bar-by-bar log opcional del sum agregado y direction (para reconstruir flip behavior si es necesario para audit).

Sin estos contadores, operator + agente NO pueden distinguir A vs B con evidencia.

### 6.2 — Distinguir A vs B requires controlled comparison

El test natural es contrastar simultáneamente:
- **Equal-weight (status quo) vs intervenciones tipo-A** (re-weighting alternatives) sobre el MISMO Donchian detector.
- **Donchian ensemble (status quo) vs signal family alternativa tipo-B** (e.g., normalized momentum, simple breakdown of N-day low) con la MISMA aggregation rule.

Si tipo-A interventions mueven `n_trades ≥ 5` en Window A pero tipo-B no mejoran sobre equal-weight, evidence favors A. Inversa = B. Ambas mejoran = both apply. Ninguna mejora = S1 (Donchian inapto) es la culpable, escalation requerida.

### 6.3 — Tension con epic #338 §8 locks

Epic C **necesita romper** §8.1 (equal-weight LOCKED) y/o §8.4 (Zarattini exact 9 lookbacks LOCKED) del #338 para investigar H-signal. Esto NO es contradicción con los locks — los locks del #338 estaban diseñados para validar **Zarattini-as-is**, y el verdict `PHASE_3_INSUFFICIENT_DATA` suspende esa validación. Epic C es structurally distinct: NO valida Zarattini-as-is, sino que investiga calibration alternative que podría producir signal sufficient.

El spec/pre-reg de epic C debe **citar y reconocer la tension explícita**, no asumir silenciosamente que los locks no aplican. El reviewer del pre-reg debe poder confirmar la trazabilidad.

### 6.4 — Re-uso de infraestructura es alto

Lo que C puede heredar de #338:
- Cost model v2 + funding rate (PR #341).
- Vol-targeting + position-sizing path (PR #344).
- Bankruptcy halt + K-cap (PR #313 + #309).
- Sweep tool harness (`tools/regime_allocation_sweep.py`) — fork or generalize.
- Pre-reg pattern (`docs/superpowers/plans/2026-05-14-regime-allocation-phase2-pre-reg.md`).
- Hold-out isolation (#246) — sigue active hard-block.

Lo que C debe construir nuevo:
- `strategy/donchian_ensemble.py` extensión con aggregation alternatives (re-weighting, threshold-flip).
- Possibly `strategy/signal_alternatives.py` para tipo-B testing (signal family swap).
- Observability instrumentation (§6.1).
- Pre-reg propio con sus locked params, sub-windows, success/halt criteria.

### 6.5 — Basket lock se respeta

Epic C MUST usar misma basket que #338 (10 símbolos, `DEFAULT_SYMBOLS` en `btc_scanner.py:191-194`). Razones:
- Aislamiento de variable — testing signal sin co-variar basket.
- Operator hard-lock del epic #338 §4.1 sobre H5 follow-up se preserva.
- Si epic D (H-basket) corre en paralelo o después, las dos hipótesis quedan testables separately.

### 6.6 — Phase 3 verdict NO se re-litiga

Epic C NO reabre el verdict del Phase 3 #338. PR #349 queda independent, mergea cuando operator marque ready. Epic C arranca como new spec/issue, con su propio Phase 0/1/2/3 ciclo.

---

## 7 · Open questions (gaps que el epic C spec/pre-reg debe resolver)

**Q1. Per-lookback observability — ¿qué granularidad exacta?**
Solo counts? Counts + magnitudes? Bar-by-bar histograms? Trade-off entre observability completeness vs tooling effort vs disk usage. Decisión debe quedar locked en el spec.

**Q2. A vs B — diagnostic-first o intervention-first?**
Dos approaches válidos:
- (a) **Diagnostic-first**: run unchanged equal-weight + Donchian sobre Window A con observability ON, analizar firings, decide intervention based on data.
- (b) **Intervention-first**: pre-register A-set + B-set interventions, run both, compare outcomes directly.

Approach (a) ahorra compute pero introduce researcher degrees of freedom (post-hoc rationalization risk). Approach (b) es más expensive pero más pre-registered. Decisión debe quedar locked en el spec.

**Q3. ¿Qué constituye "signal sufficient"?**
Audit #338 fijó `N_TRADES_MIN_FOR_ELIGIBILITY = 5` por Window. Epic C puede heredar ese threshold o re-derivarlo. Si lo hereda, debe citar la pre-reg ref. Si lo re-deriva, debe justify. Si re-derive sin justify, leakage risk on the threshold itself.

**Q4. ¿Qué Windows usa epic C — los mismos del #338 o nuevos?**
Trade-off:
- **Mismos (A=2022-bear, B=2023-recovery, C=2025-Q1)**: comparability directa con #338 verdict. Pero: si las Windows fueron parte del problema (e.g., 91d es too short para Donchian), heredar el problema.
- **Nuevos** (e.g., longer windows 180d, o different regime mix): mejor en aislamiento de variable, pero rompe comparability directa.

Decisión debe quedar locked en el spec, con justificación.

**Q5. ¿Epic C puede llegar a holdout (Phase 5)?**
Si C valida una calibration y queda PASS en Phase 3 (sus propias Windows), ¿usa la "bala única" sobre `data/holdout/` o queda blocked por #322 hasta que también H-basket se resuelva?

Default propuesto (para validación operator): holdout shot SOLO si epic C **AND** epic D ambos validan (joint), no individualmente — preserva la bala. Operator override posible bajo nuevo issue + reasoning.

**Q6. ¿Re-evaluación de scope si el diagnostic muestra B?**
Si Q2 (a) se elige y el diagnostic muestra B (signal family swap requerido), epic C deja de ser "calibration" en sentido estricto y empieza a ser "different strategy class entirely". El operator debería decidir up-front si epic C tiene authority para ese swap o si requiere escalation a meta-epic ANTES de empezar el diagnostic.

---

## 8 · Refs

- **Phase 3 verdict + audit:** `data/retune/2026-05-14-regime-allocation/derivation_audit.md` (§2 mechanism observations, §7 hypothesis table, §8 operator decision branches)
- **Phase 3 PR:** [#349](https://github.com/sssimon/trading-spacial/pull/349) (draft)
- **Epic #338 spec:** `docs/superpowers/specs/es/2026-05-13-epic-regime-allocation-strategy-pivot.md` (§4.2 signal arch, §8 locked decisions, §7 phases)
- **Phase 2 pre-reg pattern:** `docs/superpowers/plans/2026-05-14-regime-allocation-phase2-pre-reg.md`
- **Hard blocks active:** #246 (holdout) / #322 (A.4-3 holdout) / #271 (invitation) / #338 §4.1 (basket lock)

---

## 9 · Historial de revisiones

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-15 | Initial pull-up post Phase 3 #338 verdict; H-signal split into sub-hypothesis A (dilution) + B (no-firing-enough); 6 open questions surfaced | sssamuelll + Claude Opus 4.7 |

---

**End of pull-up.** Próximo entregable: epic C spec doc + pre-reg formal — pending operator authorization post-review.
