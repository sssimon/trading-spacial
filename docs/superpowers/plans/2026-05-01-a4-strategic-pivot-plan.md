# A.4 Strategic Pivot — Action Plan

**Fecha:** 2026-05-01
**Owner:** sssamuelll
**Reviewer agent:** Claude Opus 4.7 (proxy reviewer)
**Status:** DRAFT — pending operator approval before execution
**Branch context:** working on `feat/methodology-a4-1-retune-pre-holdout` (commits `4905031`, `136ea72`, `4c5a50d`); PR [#287](https://github.com/sssimon/trading-spacial/pull/287) OPEN as draft

---

## 1. Trigger y contexto

Durante la sesión del 2026-05-01 el reviewer aprobó la secuencia `#283 (operational model spec) → #284 (asimetrías cerrado outdated) → A.4-1 (#250, re-tune ATR pre-holdout)` siguiendo el path nominal *camino crítico → invites (#271)*. La aprobación NO consideró el output del diagnóstico A.0.2.diag (#281), ejecutado el día anterior (2026-04-30).

El operador (sssamuelll) interrumpió la ejecución antes de commitear el artefacto del Run 1 de A.4-1 con la consigna textual:

> *"yo considero importante que antes de cualquier accion revises bien el repo, y utilices los issues, el historial de commits de ayer, pilla que vimos que hay problemas en la estrategia estructurales que hay que modificar a nivel codigo. y me expliques por que estamos haciendo esto ahorita y si va alineado con el plan de arreglar la estrategia para que sea rentable con las 10 monedas"*

Este documento captura el resultado de esa revisión + las decisiones tomadas + el plan de acción concreto.

### 1.1 Hallazgos materiales del diagnóstico #281 (2026-04-30)

Citas literales de los comments del operador en [#281](https://github.com/sssimon/trading-spacial/issues/281):

- **Análisis #2 (forward-return analysis, gross only, h=+5):** Solo 3/10 símbolos con t-stat ≥ 2.5 (PENDLE 2.60, AVAX 2.68, ADA 2.55). Otros 5 son ruido puro, 2 marginales.
- **Análisis #5 (expectancy decomposition):** Los 3 con signal sólida son `STRUCTURAL` en outcome del strategy — gross expectancy ≈ 0. El exit logic ATR destruye ~100% del edge predictivo en h=+5 antes de que se materialice.
- **Análisis #7 (exit logic alternativo, ejecutado posteriormente como follow-up):** Timer fijo h=+5 captura `+0.46–0.55%` gross per-trade en PENDLE/AVAX/ADA vs `~0%` del ATR-based actual. **El exit logic ATR está destruyendo la señal predictiva del scoring.**
- **Análisis #8 (robustez temporal de la señal):** INCONCLUSIVO. La ventana pre-train accesible es de solo 2 meses (n=21–33 entries por símbolo), insuficiente para distinguir señal real vs artifact sin tocar holdout.
- **Adenda corrección (2026-04-30 15:13 UTC):** *"CONFIRMADO por #7: el exit logic ATR-based requiere rediseño estructural. Tunear ATR multipliers en A.4 sería curve-fit a la estructura equivocada."*

### 1.2 Cluster classification del basket de 10 (per #281)

| Cluster | Símbolos | Veredicto |
|---|---|---|
| **B-like sólido** | PENDLE, ADA | exit logic redesign + #279 (sizing) — 20-27% rescate por SL widening |
| **B-like parcial** | BTC, ETH | holding-period gap winner/loser 3.5x/2.8x — BE/TP redesign más que SL widening |
| **Mundo C local** | DOGE, JUP, RUNE, XLM, AVAX, UNI | NO tunear. Remover del basket o rediseñar scoring per-symbol |

> AVAX fue reclasificado de B-like a Mundo C local porque pese a t=2.68 en #2, SL widening rescata ≤6.6% — los SL exits son trades genuinamente equivocados. Decisión surfaced en el comment 2026-04-30 14:27 UTC.

### 1.3 Recomendación del benchmark de exit logic #282

Doc en `docs/superpowers/research/2026-04-30-exit-logic-benchmark-crypto.md` (branch `research/exit-logic-benchmark-281`, **NO mergeada a `main`** al 2026-05-01).

Recomendación principal (cita commit `52f688c`):

> *"Recomendación principal para A.4 prototyping: Triple Barrier puro (SL_ATR + TP_ATR + time-limit hard a t+5h) — convergencia entre Hummingbot v2 default y López de Prado academia, mínimo cambio estructural, atribución natural, bajo overfit risk. Time-decaying TP estilo Freqtrade ROI como prioridad #2."*

**Caveat material (§3.1 del benchmark):**

> *"Time-limit global a t+5h activamente daña BTC/ETH — winners hold 14h. Un t+5h global no es 'mitigable', es **incompatible** con la heterogeneidad observada del basket. Ver §5 Gate 2 para el decoupling per-cluster (que es a su vez una hipótesis, no una asunción)."*

→ Triple Barrier puro genérico no aplica. Necesita **time-limit per-cluster**: ~h+5 para B-like sólido (PENDLE/ADA), ~h+15 para B-like parcial (BTC/ETH).

### 1.4 Por qué A.4-1 actual está mal alineado

**Pregunta que A.4-1 está respondiendo:** *¿Qué `atr_sl_mult/tp/be` son óptimos sobre pre-holdout?*

**Pregunta que el sistema realmente necesita resolver:** *¿ATR es el exit logic correcto?*

#281 #7 ya respondió la segunda con NO. La primera es prematura sin la segunda. Re-tunear ATR multipliers = curve-fit a estructura equivocada.

**Lo que sí se preserva del trabajo de A.4-1 (#287) y del proceso de review:**

El harness es independiente del exit logic. Los componentes son reusables para A.4 v2 sin importar qué exit logic se elija. Y el proceso de review formalizó disciplina que también se hereda:

- `auto_tune.py --max-date YYYY-MM-DD` con propagación de cutoff a las 6 frames (1h/4h/5m/1d + F&G + funding) y assertion de no-leakage.
- `tools/retune_pre_holdout.py` wrapper (artefact directory + manifest con sha256 + atomic JSON con `sort_keys=True`).
- 21 tests entre `test_auto_tune_max_date.py` + parallelize follow-up.
- Whitelist documentada en `tests/test_holdout_isolation.py`.
- **Disciplina de review formalizada:** el proceso del benchmark de exit logic (#282) generó protocolo de gates ex-ante (Gate 0–4 + sub-gate 2a, ver §5 del benchmark) que A.4 v2 hereda directamente. Aplicable a cualquier validación futura. La auditabilidad del proceso (diff del doc, comment differential, correcciones de #282 post-review, cierre de #283/#284 con verificación) es código durable igual que la herramienta del re-tune.

El **artefacto del Run 1** (params re-tuneados ATR) es lo que NO se promueve — es output condicional al exit logic ATR.

---

## 2. Decisiones tomadas (2026-05-01)

Cada decisión tiene: ID, descripción, opciones consideradas, decisión final, reasoning, reference.

> **Nota de lectura:** D1–D6 y D8 son sobre la estrategia y su evaluación (qué hace el sistema, qué tunea, qué publica, qué baselinea). D7 y D9 son meta-decisiones — sobre cómo documentamos y comunicamos lo anterior + qué asunciones técnicas heredadas requieren documentación explícita. La separación es lectura ergonomic, no organizativa — los IDs siguen orden cronológico de cuando emergieron en la sesión.

### D1 — Path para A.4

**Opciones consideradas:**
- (i) Exit logic redesign con basket reducida, aceptar holdout como test final.
- (ii) Pausar A.4 hasta resolver #8 (robustez temporal).

**Decisión:** **(i) Exit logic redesign.**

**Reasoning:**
- #281 #7 confirmó structural fail del ATR. Redesign es la respuesta directa.
- Opción (ii) bloquearía A.4 indefinidamente — #8 es estructuralmente irresolvible sin tocar holdout (pre-train window n=21-33).
- #282 benchmark provee variantes concretas y rankeadas para prototipar.

**Reference:** [#281 adenda 2026-04-30 15:13 UTC](https://github.com/sssimon/trading-spacial/issues/281#issuecomment-4353692842).

### D2 — Variante de exit logic para A.4 v2

**Opciones consideradas (per #282 §3):**
- (3.1) Triple Barrier puro (ATR-SL + ATR-TP + time-limit at h=+5).
- (3.2) Time-decaying TP (Freqtrade ROI style).
- (3.3) Adaptive horizon por régimen / símbolo.
- (3.4) Híbrido fixed-horizon + post-horizon trailing.

**Decisión:** **(3.1) Triple Barrier con time-limit per-cluster.**

**Sub-decisión técnica abierta (no resuelta hoy, queda para A.4 v2 spec):**
- Calibración exacta del time-limit per-cluster: 5h PENDLE/ADA es lectura directa del peak h=+5 de #281 #2; 14h BTC/ETH viene del winner-holdtime mediano de #6, pero podría calibrarse.
- Si arrancar con time-limit fijo per-cluster o con time-limit que decae (mezcla de 3.1 y 3.2).

→ Ver **R1** (riesgo trackeado) y **A7** (action item donde se cierra esta calibración como deliverable explícito del spec de A.4 v2).

**Reasoning de elegir 3.1 sobre 3.2/3.3/3.4:**
- #282 lo lista como prototyping #1 — convergencia entre Hummingbot v2 (única implementación retail donde Triple Barrier es first-class) y López de Prado academia.
- Mínimo cambio estructural sobre `backtest._close_position` — solo agrega 4to bucket de exit (`TIME_LIMIT`).
- Atribución natural: `close_type` ∈ {SL, TP, BE, TIME_LIMIT} permite diagnóstico per-trade.
- Overfit risk bajo: 1 grado de libertad adicional (time-limit) vs 3.2 (curva ROI completa) o 3.3 (per-symbol horizon).

**Reference:** `docs/superpowers/research/2026-04-30-exit-logic-benchmark-crypto.md` §3.1 (recomendación de prototyping) y §5 Gate 2 (decoupling per-cluster como hipótesis, no asunción) — en branch `research/exit-logic-benchmark-281`.

### D3 — Basket scope para A.4 v2

**Decisión:** **4 símbolos: PENDLE, ADA, BTC, ETH.**

**Reasoning:**
- B-like sólido (PENDLE, ADA) tienen edge predictiva sólida (t≥2.55 en #2) que el redesign de exit busca capturar.
- B-like parcial (BTC, ETH) tienen edge en holding-period gap (#6 winner/loser 3.5x/2.8x) que el time-limit per-cluster puede explotar.
- Mundo C local (6 símbolos: DOGE, JUP, RUNE, XLM, AVAX, UNI) — diagnóstico explícito *"NO tunear"*. Decisión separada (remove from basket vs scoring redesign per-symbol) que NO bloquea A.4 v2.

**Reference:** Cluster table en §1.2 de este doc.

### D4 — Disposition de PR #287 (A.4-1 harness)

**Opciones consideradas:**
- (a) Mergear el harness con caveat fuerte en commit body, sin commitear el artefacto del Run 1.
- (b) Cerrar #287 como WIP archived, mantener branch como referencia histórica.

**Decisión recomendada por reviewer:** **(a) Mergear harness, no commitear artefacto Run 1.**

**Pendiente confirmación del operador.** Razones para (a):
- El harness es código durable, reusable en A.4 v2 sin importar el exit logic elegido.
- Tests pasan (21 nuevos en suite verde). CI verde.
- Mergear ahorra re-bootstrap del flag `--max-date` y del wrapper cuando A.4 v2 lo necesite.
- Risk de merger: ninguno material — el código no se ejecuta automáticamente; activarlo requiere el script wrapper.

**Reasoning para NO commitear el artefacto del Run 1:**
- Los params re-tuneados son ATR-based. Mantener el JSON en repo lo expone a ser leído como autoritativo por futuros readers / future Sam / Simon.
- Si se necesita la data del Run 1 para análisis futuro, está reproducible (manifest registra commit + ohlcv_sha256 + seed).

### D5 — Priority de #279 (sizing cap)

**Decisión:** **Deprioritizar a track paralelo, NO bloqueante de A.4 v2.**

**Reasoning:**
- #281 #7 explícito: *"el problema dominante no es sizing sino exit logic"*.
- 5 de los 6 símbolos donde sizing era catastrófico (DOGE, JUP, RUNE, XLM, UNI per-#281 #1) salen del basket — issue se vuelve menos urgente.
- Para PENDLE (que se queda en B-like sólido) sizing sí importa (`participation_rate p50=8.0` per #281 #1). Pero PENDLE en A.4 v2 con time-limit h=+5 va a tener distribución de trades distinta — vale re-evaluar #279 después de A.4 v2.

**Action:** Mantener #279 OPEN. Comentar en el issue marking it as "secondary to A.4 v2 / exit redesign — re-evaluate priority post A.4 v2 results".

### D6 — #272 (re-baselining) sequencing

**Decisión:** **Arrancar #272 HOY como pre-requisito de A.4 v2.**

**Reasoning:**
- #272 está marcado explícitamente como blocker de #250 (A.4 cualquiera).
- A.4 v2 va a comparar contra current params performance — si los números current están inflados (pre phantom-fix #223/#224), la comparación es engañosa.
- #272 es trabajo independiente, shovel-ready, dev puede arrancar sin esperar el spec de A.4 v2.

**Reference:** [#272](https://github.com/sssimon/trading-spacial/issues/272), [#223 phantom fix](https://github.com/sssimon/trading-spacial/pull/223), [#224 parity guard](https://github.com/sssimon/trading-spacial/pull/224).

### D7 — Mergear docs de research/diagnóstico a `main`

**Estado actual:** `docs/superpowers/specs/es/2026-04-30-a02-diag-deep-dive.md` y `docs/superpowers/research/2026-04-30-exit-logic-benchmark-crypto.md` están en branches `diag/a02-deep-dive-281` y `research/exit-logic-benchmark-281` respectivamente. **Ninguna está en `main`.**

**Decisión:** **Mergear ambos a `main` HOY** vía PRs separadas, atomic concept cada una.

**Reasoning:**
- SoT del repo no debería estar incompleto. Mergear los docs no implica accionar sobre ellos — son material de referencia. La decisión de A.4 v2 es separada y se trackea acá.
- Razonamiento de los docs sobrevive ediciones futuras de CLAUDE.md / specs si está en `main`.

**Lección operativa del miss del reviewer (auditoría completa, no solo diagnóstico):**
- Capa 1 (necesaria pero no suficiente): los docs estaban en branches no-mergeadas, no en main → el reviewer hizo `ls docs/superpowers/specs/es/` durante el inventory inicial y los docs no aparecieron.
- Capa 2 (la causa real): incluso si el reviewer hubiera escaneado todas las branches, los hallazgos load-bearing del diagnóstico (cluster classification, "ATR es structural fail" del análisis #7) **no estaban resumidos en los issue comments visibles** — vivían dentro del PDF/markdown del doc en branch.
- **Lección operativa:** cuando un comment de PR/issue contiene información que decide direction-change, la información load-bearing debe vivir en el comment text del issue (no solo como link al doc), para que readers escaneando comments encuentren la sustancia. Mergear los docs a main es necesario; resumir lo crítico en el comment del issue es igual de necesario. Las dos capas son separadas; ambas fallaron en este caso.

### D9 — Asimetrías técnicas heredadas (#284 cerrado como outdated, principio aplica)

**Contexto:** El issue #284 ("Asimetrías técnicas: BE-move solo LONG, cooldown documentado vs código diverge") fue cerrado HOY como `NOT_PLANNED` / outdated. La verificación contra main `8580cd6` mostró que sus 2 claims eran falsas: BE-move es simétrico (`backtest.py:417-426`), `COOLDOWN_H = 6` consistente entre código (`btc_scanner.py:131`, `backtest.py:489`, `strategies/trend_following_sim.py:218`) y docs.

**Pero el principio del review externo aplica:** A.4 v2 hereda asunciones técnicas que afectan el N efectivo del Deflated Sharpe. Con un exit logic nuevo (Triple Barrier per-cluster), no es obvio que esas asunciones sigan siendo apropiadas:

- **Cooldown 6h:** en el sistema actual gobierna densidad de trades. Con time-limit per-cluster (5h PENDLE/ADA, ~14h BTC/ETH), el cooldown puede entrar en conflicto: una posición de PENDLE cierra a t+5h, pero el próximo trade no se considera hasta t+11h por el cooldown. ¿Eso es deseable? ¿O cooldown debería escalar con el time-limit?
- **BE-move simétrico:** preserva LONG/SHORT con la misma lógica. ¿Sigue teniendo sentido cuando el exit dominante es time-limit?
- **Otras asunciones:** sizing R-multiple (1% risk), regime detector (composite F&G + funding + price), score tiers (0-9), etc.

**Decisión:** **El spec de A.4 v2 (A7) debe documentar explícitamente, antes de evaluar contra holdout:**
- Qué cooldown usa y por qué (preservar 6h, ajustar, eliminar).
- Qué simetría LONG/SHORT preserva en BE-move y por qué (si se mantiene BE-move).
- Cualquier otra asunción heredada del código actual que afecte el N efectivo del DSR.
- Justificación per asunción: "se preserva porque..." o "se cambia porque...".

**Reasoning:** Estas asunciones constituyen grados de libertad implícitos. Si entran al holdout sin documentación, el DSR ex-ante (#249 Gate 3 threshold) no las cuenta y el threshold se subestima. Documentación explícita = N honesto.

**Closing action:** Resolver dentro de **A7** (spec de A.4 v2) — agregado como deliverable explícito de su acceptance.

### D8 — CLAUDE.md update

**Decisión:** **Agregar caveat en el bloque de "Curated symbols" reflejando el cluster classification de #281.**

**Texto propuesto** (a confirmar por operador):

> *"Curated symbols (10) are documented above. Diagnóstico A.0.2.diag (#281, 2026-04-30) reveló que solo 4/10 tienen edge predictiva ejecutable bajo el exit logic actual: B-like sólido (PENDLE, ADA), B-like parcial (BTC, ETH). Los 6 restantes (DOGE, JUP, RUNE, XLM, AVAX, UNI) están en 'Mundo C local' — basket reduction pendiente vía A.4 v2. La lista de 10 sigue en `DEFAULT_SYMBOLS` por compatibilidad pero NO debe leerse como afirmación de viabilidad. See [a4-strategic-pivot-plan](docs/superpowers/plans/2026-05-01-a4-strategic-pivot-plan.md)."*

**Reasoning:** Sin esto, futuros readers asumen las 10 son viables. El doc de pivot apunta a la verdad operacional.

---

## 3. Action items

Cada item tiene: ID, owner, status, dependencies, acceptance criteria.

**Status legend:** `pending` | `in_progress` | `blocked` | `done`

### A1 — Comentar PR #287 con la pausa explicada
- **Owner:** dev (drafted by reviewer, executed by dev via Sam approval)
- **Status:** `pending`
- **Dependencies:** ninguna
- **Acceptance:**
  - Comment posteado en [#287](https://github.com/sssimon/trading-spacial/pull/287) explicando que el path cambió post-#281, link a este plan, decisión D4 referenciada.
  - PR queda en estado `draft` mientras se toma la decisión final D4 (merge harness vs close).

### A2 — Mergear `2026-04-30-a02-diag-deep-dive.md` a main
- **Owner:** dev
- **Status:** `pending`
- **Dependencies:** A1 (comment de pausa debe estar antes — ancla cronología del repo: pausa primero, docs de soporte después)
- **Acceptance:**
  - PR opened from `diag/a02-deep-dive-281` (commit `dc2c652`) targeting `main`.
  - PR title: `docs(methodology): A.0.2.diag deep-dive results (#281)` o similar.
  - Squash-merge **sin `Closes #281`**. #281 sigue OPEN trackeando los hilos estratégicos abiertos (basket reduction, comunicación a Simon, robustez #8). El merge solo lleva el doc a SoT del repo, no resuelve el issue.
  - PR body referencia explícita: *"Mergea el deliverable doc de #281; el issue queda OPEN trackeando hilos abiertos (ver `docs/superpowers/plans/2026-05-01-a4-strategic-pivot-plan.md` §4 Out of scope + §6 Risks)."*
  - CI verde.

### A3 — Mergear `2026-04-30-exit-logic-benchmark-crypto.md` a main
- **Owner:** dev
- **Status:** `pending`
- **Dependencies:** A1 (mismo razonamiento que A2 — cronología del repo). Independiente de A2 entre sí.
- **Acceptance:**
  - PR opened from `research/exit-logic-benchmark-281` (commit `42111bd`) targeting `main`.
  - PR title: `docs(methodology): exit logic benchmark for crypto frameworks (#282)` o similar.
  - Squash-merge **sin `Closes #282`**. #282 sigue OPEN como referencia ancla del decisión-thread de exit logic; A.4 v2 (A7) lo cita y eventualmente cerrará el loop. El merge solo lleva el doc a SoT.
  - PR body referencia explícita similar al de A2.
  - CI verde.

### A4 — Arrancar #272 (re-baseline post phantom-fix)
- **Owner:** dev
- **Status:** `pending`
- **Dependencies:** A2 + A3 (los docs deben estar en main para que el re-baseline pueda referenciarlos como contexto).
- **Acceptance criteria** (per #272 issue body):
  - Re-correr backtests honestos sobre todo el histórico con código post-#224 — train + holdout disponible (12mo locked per #247).
  - Marcar `2026-04-17-formula-ganadora-resultados-finales.md` como obsoleto (banner al inicio + link a este plan + al doc nuevo).
  - Idem `2026-04-18-documento-completo-sistema-trading.md` en las secciones de números.
  - Doc nuevo con números reales: `docs/superpowers/specs/es/2026-XX-XX-baseline-honest-numbers.md` (slug a definir cuando se ejecute).
  - PR squash-merge con `Closes #272`.
  - **NO toca `data/holdout/`** — Guard B (AST scanner) verifica.

### A5 — Decidir D4 (PR #287 disposition) y ejecutar
- **Owner:** Sam (decisión) + dev (ejecución)
- **Status:** `blocked` por A1 (comment explicando pausa) + decisión final del operador.
- **Dependencies:** A1.
- **Acceptance:**
  - Si D4 = merge: PR #287 squash-merged, harness disponible en main, **el artefacto del Run 1 NO se commitea** ni en este PR ni en futuro. Branch borrada.
  - Si D4 = close: PR #287 cerrado sin merge, branch preservada como referencia histórica, dev re-implementa el harness en A.4 v2 (overhead conocido).

### A6 — CLAUDE.md update (caveat sobre 10 monedas)
- **Owner:** dev
- **Status:** `pending`
- **Dependencies:** A2 (para poder referenciar el doc del diagnóstico) + A4 (idealmente, para referenciar el doc de números honestos; en su defecto, agregar TODO).
- **Acceptance:**
  - Diff mínimo a CLAUDE.md agregando el bloque de D8 en la subsección apropiada (después de "Curated symbols" o como subsección nueva bajo "Architecture").
  - Link al plan de pivot + al doc del diagnóstico funciona.
  - PR independiente o piggyback sobre A4 — operador decide.

### A7 — A.4 v2 spec ticket
- **Owner:** Sam (escribe spec) + reviewer (review) + dev (futura implementación)
- **Status:** `pending` (no se arranca hoy — depende de A4 para tener números baseline, y de operador para escribir el spec)
- **Dependencies:** A4 idealmente (para spec con números honestos en mano), A2 + A3 (para anclar al diagnóstico + benchmark).
- **Acceptance:**
  - Issue nuevo abierto. Título sugerido: *"feat(strategy-validation): A.4 v2 — Triple Barrier exit logic with per-cluster time-limit (B-like basket)"*.
  - Body cita: D1, D2, D3 de este plan + cluster classification de #281 + recomendación de #282 §3.1.
  - Tasks explícitos:
    - **Time-limit calibration per-cluster** (cierra la sub-decisión abierta de **D2** y mitiga **R1** — el spec debe definir grid de time-limits a evaluar: e.g. {3, 5, 8} h para B-like sólido, {10, 14, 20} h para B-like parcial, NO single value a priori).
    - **Cota explícita del grid (mitigación R1):** el grid de time-limit es el ÚNICO grado de libertad nuevo en A.4 v2. Todo lo demás (ATR multipliers, scoring, regime detector, sizing, basket composition) queda fijo en valores pre-registrados antes de evaluar contra holdout. Cualquier expansión post-hoc del grid viola integridad del Deflated Sharpe.
    - **Documentación de asunciones técnicas heredadas (cierra D9):** spec lista explícitamente y justifica cooldown (6h actual), simetría LONG/SHORT en BE-move, sizing R-multiple, regime detector inputs. Cada asunción: "se preserva porque..." o "se cambia porque...".
    - **Criterio de stop pre-registrado (mitigación R3):** A.4 v2 se considera **exitoso si y solo si pasa el threshold pre-registrado del Gate 3 / DSR de #249** sobre el holdout. Cualquier resultado por debajo de ese threshold es "falló", sin importar cercanía. Sin "casi pasó", sin sub-resultado rescatable post-hoc.
    - Basket reducida a 4 símbolos (PENDLE, ADA, BTC, ETH) per **D3**.
    - Artefacto comparado contra **#272** baseline (no contra los números inflados de `2026-04-17-formula-ganadora`).
    - Evaluation against holdout per #250 protocol.
    - Pre-registered cost-survival check per **R2** (`net_expectancy > 0` + cross de `exit_reason × hour-of-day × cost_bps`).
  - **Bloqueado por:** A4 (re-baseline) + idealmente cierre de #279 priority decision (D5 → comentado en #279).
  - **Implementation plan format:** cuando se escriba el spec, debe seguir el formato del skill `superpowers:writing-plans` (bite-sized 2-5 min steps, código exacto, comandos exactos, TDD). Este documento (strategic pivot plan) es coordination-level; el spec de A.4 v2 es engineer-level.

### A8 — Comentar #279 con la deprioritization
- **Owner:** dev (drafted by reviewer)
- **Status:** `pending`
- **Dependencies:** ninguna
- **Acceptance:**
  - Comment posteado en [#279](https://github.com/sssimon/trading-spacial/issues/279) marcando como secondary to A.4 v2 / exit redesign, citando D5 de este plan.
  - Issue NO se cierra — queda OPEN para re-evaluación post A.4 v2.

### A9 — Basket reduction decision (post-A.4 v2)
- **Owner:** Sam (decisión) + reviewer (review)
- **Status:** `blocked` por A.4 v2 ship (que a su vez está bloqueado por A7 + A4)
- **Dependencies:** A.4 v2 implementación + evaluación contra holdout completas.
- **Acceptance:**
  - Decisión documentada (issue nuevo o comment en #281) sobre los 6 símbolos en Mundo C local (DOGE, JUP, RUNE, XLM, AVAX, UNI):
    - Opción A: Remover de `DEFAULT_SYMBOLS`. Implica PR a `btc_scanner.py` + actualización de `config.json["symbol_overrides"]` + tests.
    - Opción B: Scoring redesign per-symbol (1 ticket por coin candidato). Más caro, requiere justificación per-coin.
    - Opción C: Mantener en `DEFAULT_SYMBOLS` pero excluir de signal generation (e.g., flag `disabled: true` per-symbol).
  - Si A: PR `feat(strategy): reduce basket to 4 viable symbols (post-A.4 v2 + #281)` con `Closes` apropiado.
  - Si B/C: tickets nuevos abiertos con scope.
- **Cierra:** **R4** (basket reduction sin decisión).

### A10 — Comunicación a Simon
- **Owner:** Sam
- **Status:** `blocked` por A2 + A3 + A4 + A8.
- **Dependencies:** A2 (diag doc en main), A3 (benchmark doc en main), A4 (números honestos disponibles), A8 (#279 comment posteado para que Simon vea estado actual de prioridades).
- **Acceptance:**
  - Mensaje a Simon (canal a definir por Sam — Telegram, voice, email) cubriendo:
    - Hallazgos materiales del diagnóstico #281 (4/10 viables, exit logic structural fail).
    - Decisión de pivotear a A.4 v2 con Triple Barrier per-cluster + basket reducida.
    - Re-baseline (#272) ejecutado, números honestos disponibles.
    - Plan de pivot strategic (link a este doc).
    - Timeline esperado para A.4 v2 ship + decisión de basket (A9).
  - Confirmación de Simon registrada (comment en este doc o en issue dedicado).
- **Cierra:** **R6** (comunicación a Simon pendiente).

---

## 4. Out of scope (qué NO hace este plan)

Explícito para que ningún reader / future Sam / future reviewer asuma que estamos abordando estos temas en esta ronda:

- **#8 robustez temporal de la señal NO se resuelve.** La ventana pre-train accesible es estructuralmente insuficiente (n=21-33). A.4 v2 acepta el riesgo y deja el holdout como test final. Si el holdout falla → conclusión es *"la edge era artifact del tuning del scoring"*, lo cual es información válida y limita blast radius (per **R3**).
- **Los 6 símbolos en Mundo C local NO se remueven** de `DEFAULT_SYMBOLS` en esta ronda. El caveat de CLAUDE.md (**A6**) los marca como no-viables explícitamente, pero la lista de 10 sigue intacta para preservar consistencia con baseline. La decisión "remover de basket vs scoring redesign per-symbol" queda como follow-up post-A.4 v2 (per **R4**).
- **Los 6 Mundo C tampoco se tunean ni se incluyen en A.4 v2.** Diagnóstico explícito *"NO tunear"* (per #281). Cualquier propuesta de scoring redesign per-symbol para esos coins es ticket separado.
- **Comunicación a Simon NO se hace antes de A4 (re-baseline).** Per #281 adenda 2026-04-30 15:13 UTC: comunicación pendiente de "decisión del martes" (que se está tomando hoy). Comunicar después de A2 + A3 + A4 + A8 ejecutados, con docs de diagnóstico/benchmark mergeados a main + números honestos disponibles.
- **El artefacto del Run 1 de A.4-1 NO se commitea.** Independientemente de **D4** (merge harness vs close PR), los `params.json` re-tuneados son ATR-based y el diagnóstico marcó ATR como structural fail. Mantenerlos en repo los expone a ser leídos como autoritativos. Si se necesita la data para análisis futuro, está reproducible (manifest registra commit + ohlcv_sha256 + seed).
- **#279 (sizing cap) NO se ejecuta** en esta ronda. Deprioritizado a track paralelo (per **D5**); re-evaluar post-A.4 v2.
- **El exit logic ATR NO se "arregla" inline.** A.4 v2 lo *reemplaza* con Triple Barrier per-cluster, no lo parchea. Esto es decisión deliberada — el diagnóstico mostró que parchear ATR sin time-limit no captura la edge.
- **Multi-tenancy (#253) y auth-hardening (#262) NO se mueven.** Esos epics siguen post-Epic A. Este pivot solo afecta el contenido de Epic A, no la secuencia macro hacia invites (#271).

---

## 5. Sequence (ejecución sugerida)

```
HOY (Phase 0 — primero, anchor de la decisión):
  A1 (comment #287 con la pausa explicada)

HOY (Phase 1 — paralelizable, post-A1):
  A2 (merge diag doc)
  A3 (merge benchmark)
  A8 (comment #279)

HOY (Phase 2 — secuencial post-Phase 1):
  A5 (decisión + ejecución D4 sobre #287)

HOY o mañana (Phase 3 — secuencial post-Phase 2):
  A4 (re-baseline #272)
  A6 (CLAUDE.md update) — corre DESPUÉS de A4 para tener slug del doc baseline

DESPUÉS (Phase 4 — esta semana):
  A7 (escribir spec A.4 v2)

POST-A.4 V2 (semanas):
  A.4 v2 implementación + evaluación contra holdout
  Revisitar #279 priority
  A9 (basket reduction decision sobre los 6 Mundo C)
  A10 (comunicación a Simon — disparada por A2+A3+A4+A8 done)
```

---

## 6. Risks y open questions

### R1 — Calibración del time-limit per-cluster (D2 sub-decisión)
**Riesgo:** elegir time-limit incorrecto en A.4 v2 spec puede llevar a over/under-cap del horizon predictivo.
**Mitigación:** A.4 v2 debe correr Triple Barrier con un grid de time-limits por cluster (e.g. {3, 5, 8} para B-like sólido, {10, 14, 20} para B-like parcial) y reportar performance per combo. NO elegir un único time-limit a priori.

**Cota dura (anti-N-explosion en DSR):** el grid de time-limit es el ÚNICO grado de libertad nuevo en A.4 v2. Todo lo demás (ATR multipliers, scoring, regime detector, sizing, basket composition, cooldown, simetría LONG/SHORT) queda fijo en valores pre-registrados antes de evaluar contra holdout. Cualquier expansión del grid bajo presión post-hoc ("ya que estamos exploramos también X") expande N del DSR y vuelve el Gate 3 threshold inalcanzable. **No negociable.**

### R2 — Cost-survival check pre-registrado (#282 caveat)
**Riesgo:** Triple Barrier puede ganar en gross expectancy pero perder en net (post-costos) si el time-limit clusterea exits en horas de baja liquidity.
**Mitigación:** A.4 v2 debe pre-registrar (antes de correr) que el criterio de éxito incluye `net_expectancy > 0` y un cross de `exit_reason × hour-of-day × cost_bps` para detectar el problema.

### R3 — #8 robustez temporal sigue inconclusivo
**Riesgo:** la edge en train que A.4 v2 va a explotar puede ser artifact del tuning previo del scoring (no resuelto en #281 #8).
**Mitigación:** A.4 v2 acepta el riesgo y deja al holdout como test final (per opción (i) de D1). Si holdout falla → la edge era artifact y la conclusión es "scoring también necesita redesign". Ese resultado, aunque negativo, es información válida y limita el blast radius.

**Criterio de éxito pre-registrado (anti-post-hoc-rationalization):** A.4 v2 se considera **exitoso si y solo si pasa el threshold pre-registrado del Gate 3 / Deflated Sharpe Ratio de #249** sobre el holdout. Cualquier resultado por debajo de ese threshold es "falló", sin importar cercanía. **No hay "casi pasó", no hay sub-resultado rescatable, no hay narrativa post-hoc que cambie el verdict.** Esto es lo que separa research honesta de curve-fit ex-post.

### R4 — 6 símbolos Mundo C local sin decisión
**Pregunta abierta:** ¿se remueven de `DEFAULT_SYMBOLS` ya o se dejan hasta que A.4 v2 ship con la basket de 4?
**Recomendación reviewer (no decidida):** dejar `DEFAULT_SYMBOLS` intacto hasta A.4 v2 ship (consistencia con baseline), pero el caveat de CLAUDE.md (A6) debe decir explícitamente que 6 son no-viables.
**Closing action:** **A9** (basket reduction decision post-A.4 v2). R4 se cierra cuando A9 entrega decisión documentada (Opción A/B/C).

### R5 — Holdout intacto verificable
**Riesgo:** A.4 v2 implementación puede inadvertidamente leer del holdout si no se respeta `data/holdout_access.py`.
**Mitigación:** Guard B (`tests/test_holdout_isolation.py`) está en CI. Cualquier módulo nuevo que toque OHLCV pre-cutoff debe usar `--max-date` flag (ya existe vía A.4-1 harness) y no referenciar `data/holdout/`.

### R6 — Comunicación a Simon pendiente
**Estado per #281 adenda 2026-04-30 15:13 UTC:** *"NO COMUNICADO A SIMON aún. Comunicación pendiente de la decisión del martes."*

**Recomendación reviewer:** la decisión "del martes" se está tomando hoy (per instrucción del operador). Comunicar a Simon DESPUÉS de que A2 + A3 + A4 + A8 estén ejecutados, con este plan + los docs de diagnóstico/benchmark mergeados como base.
**Closing action:** **A10** (comunicación a Simon). R6 se cierra cuando A10 entrega confirmación registrada.

---

## 7. Tracking table

Actualizar en cada acción ejecutada. Status: `pending` / `in_progress` / `blocked` / `done`.

| ID | Action | Owner | Status | Started | Completed | Notes |
|----|--------|-------|--------|---------|-----------|-------|
| A1 | Comment PR #287 con pausa | dev | `pending` | — | — | — |
| A2 | Merge diag doc to main | dev | `pending` | — | — | Bloqueado por A1; NO close #281 |
| A3 | Merge benchmark doc to main | dev | `pending` | — | — | Bloqueado por A1; NO close #282 |
| A4 | Re-baseline #272 | dev | `pending` | — | — | Bloqueado por A2 + A3 |
| A5 | Decisión + ejecución D4 (#287) | Sam + dev | `blocked` | — | — | Bloqueado por A1 |
| A6 | CLAUDE.md caveat update | dev | `pending` | — | — | Bloqueado por A4 (necesita slug del doc baseline) |
| A7 | Spec A.4 v2 (issue nuevo) | Sam + reviewer | `pending` | — | — | Bloqueado por A4 |
| A8 | Comment #279 deprioritization | dev | `pending` | — | — | — |
| A9 | Basket reduction decision post-A.4 v2 | Sam + reviewer | `blocked` | — | — | Bloqueado por A.4 v2 ship; cierra **R4** |
| A10 | Comunicación a Simon | Sam | `blocked` | — | — | Bloqueado por A2 + A3 + A4 + A8; cierra **R6** |

---

## 8. References

### Issues
- [#250](https://github.com/sssimon/trading-spacial/issues/250) — A.4 epic (re-evaluate parameters against holdout)
- [#272](https://github.com/sssimon/trading-spacial/issues/272) — re-baseline post phantom-fix (formal blocker of #250)
- [#277](https://github.com/sssimon/trading-spacial/issues/277) — A.0.2 realistic transaction costs
- [#279](https://github.com/sssimon/trading-spacial/issues/279) — participation-rate cap on R-multiple sizing
- [#281](https://github.com/sssimon/trading-spacial/issues/281) — A.0.2.diag deep-dive on no-edge finding (the trigger)
- [#282](https://github.com/sssimon/trading-spacial/issues/282) — exit logic benchmark crypto frameworks
- [#283](https://github.com/sssimon/trading-spacial/issues/283) — operational model spec (closed via PR #286)
- [#284](https://github.com/sssimon/trading-spacial/issues/284) — asimetrías técnicas (closed as outdated)

### PRs
- [#287](https://github.com/sssimon/trading-spacial/pull/287) — A.4-1 harness (OPEN draft, pending D4 decision)

### Branches
- `feat/methodology-a4-1-retune-pre-holdout` — A.4-1 harness (3 commits: `4905031`, `136ea72`, `4c5a50d`)
- `diag/a02-deep-dive-281` — diagnostic doc (commit `dc2c652`, NOT in main)
- `research/exit-logic-benchmark-281` — benchmark doc (commit `42111bd`, NOT in main)

### Docs
- `docs/superpowers/specs/es/2026-04-17-formula-ganadora-resultados-finales.md` — pre-fix, NÚMEROS INFLADOS, deprecate via A4
- `docs/superpowers/specs/es/2026-04-18-documento-completo-sistema-trading.md` — pre-fix, NÚMEROS INFLADOS, deprecate via A4
- `docs/superpowers/specs/es/2026-04-30-a1-holdout-dataset-provenance.md` — A.1 provenance, IN MAIN
- `docs/superpowers/specs/es/2026-04-30-a02-diag-deep-dive.md` — diagnostic, NOT in main, see A2
- `docs/superpowers/research/2026-04-30-exit-logic-benchmark-crypto.md` — benchmark, NOT in main, see A3
- `CLAUDE.md` — see A6 for proposed update

### Commits (recent context)
- `52f688c` — exit logic benchmark for crypto frameworks (#282)
- `dc2c652` — A.0.2.diag deep-dive (#281)
- `9b87235` — A.0.2 realistic transaction costs (#277)
- `0aaa100` — A.1 lock holdout dataset (#247)
- `ebc09f0` — operational model spec (#283) merged via #286 (current `main` HEAD)

---

## 9. Update log

| Date | Author | Change |
|------|--------|--------|
| 2026-05-01 | reviewer (Claude Opus 4.7) drafted, sssamuelll to approve | Initial draft post-strategic-review |
| 2026-05-01 | reviewer | Self-review pass: added §4 Out of scope, cross-linked D2 ↔ R1 ↔ A7, made time-limit calibration explicit deliverable in A7 acceptance, renumbered §§5-9 |
| 2026-05-01 | reviewer (post pr-review-toolkit:review-pr) | Applied 5 fixes from external review: A6 moved to Phase 3 (was contradiction §5 vs §3/§7), A2/A3 explicit NO close #281/#282, added A9 (basket reduction) closing R4, added A10 (Simon comm) closing R6, D2 cites §3.1 + §5 Gate 2 of benchmark |
| 2026-05-01 | reviewer (post external senior review by sssamuelll's mentor) | Applied 6 fixes: (1) executive summary reframed (no se sobrevende "no edge ejecutable"), (2) D7 mini-autopsia ampliada con lección operativa de 2 capas, (3) Phase 1 → Phase 0+1 — A1 anchor antes de A2/A3/A8, (4) added D9 + A7 acceptance bullet on technical asymmetries inherited from #284-closed, (5) R1 + A7 acceptance with hard cap on grid expansion (anti-DSR-N-explosion), (6) R3 + A7 acceptance with pre-registered success criterion (Gate 3 / DSR threshold from #249, no "casi pasó") |
| 2026-05-01 | reviewer (post 3rd-pass mentor review polish) | Applied 3 minor polish fixes: (1) §1.4 preserved-work bullet expanded to include review discipline / Gate 0–4 protocol from #282 as durable artefact, (2) §2 header note clarifying meta-decisions (D7/D9) vs system decisions (D1-6/D8), (3) "no hay way around" spanglish removed in non-technical version (technical doc unaffected) |
