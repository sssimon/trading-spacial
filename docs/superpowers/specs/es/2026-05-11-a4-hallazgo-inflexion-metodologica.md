# A.4 — Hallazgo de inflexión metodológica: sin edge demostrable bajo condiciones live-equivalent

**Fecha:** 2026-05-11
**Status:** DRAFT — pausa metodológica activa, pre-revisión externa
**Autor:** Reviewer agent (Claude Opus 4.7) en colaboración con sssamuelll
**Bloqueante de:** A.4-3 (holdout evaluation, bala única). NO ejecutar A.4-3 hasta resolver este spec.
**Relacionado con:** Decisión 9 (`docs/superpowers/specs/es/2026-05-03-asunciones-tecnicas-pre-holdout.md`), CLAUDE.md "Caveats heredados — A.4 (#250)" #1 y #4

---

## 1 · Resumen ejecutivo

Tras dos sweeps consecutivos (regime A.4-1.5 y ATR A.4-1) corridos hoy sobre el pre-holdout window con TODAS las correcciones estructurales activas (#223/#224 phantom-profit, #309 K=10 cap, #313 #280 bankruptcy halt, gates time-limit/participation-cap activos), el resultado empírico es:

- **A.4-1.5 regime sweep:** sanity-halt (rc=3). Los 4 thresholds candidatos `{60_40, 70_30, 80_20, no_detector}` producen aproximadamente la misma pérdida (-$97K a -$99K agregado de 10 símbolos), con margen winner-vs-runnerup de 2.18% — dentro del ruido del bankruptcy floor. **Cada uno de los 10 símbolos** rematan en ~$-9K (initial $10K − bankruptcy floor $1K) bajo cualquier config.
- **A.4-1 ATR sweep:** rc=0 pero `recommendation: NO_DATA` para los 10 símbolos. **Ninguna** de las 105 combinaciones del grid `(sl, tp, be)` produce P&L positivo de train para ningún símbolo.

Estos dos resultados son consistentes y refuerzan un mismo finding:

> **La estrategia, parametrizada dentro del grid disponible y bajo simulación live-equivalent, no muestra edge demostrable en el pre-holdout window de 15 meses para ninguno de los 10 símbolos del basket curado.**

Este hallazgo cambia materialmente el cálculo costo-beneficio de A.4-3 (holdout, bala única). Antes de ejecutarlo, este spec convoca **revisión externa**.

---

## 2 · Evidencia empírica

### 2.1 A.4-1.5 regime sweep (rc=3)

- Cutoff: `2025-04-30T00:00:00 UTC` (locked holdout start)
- Runtime: 626s (vs 3623s del 5-06 — el #280 elimina la mayoría de los fictional zero-trades post-bancarrota)
- Decision flags: `sanity_check: True`, `stability_check: True` (margen < 5%), `degenerate_zero_pnl: False`

Per-symbol breakdown (extraído de `data/retune/2026-05-11-pre-holdout-regime-evidence/halted_summary.json`):

| Symbol | 60_40 | 70_30 | 80_20 | no_detector |
|--------|-------|-------|-------|-------------|
| BTC | -9,016 | -9,000 | -9,009 | -9,004 |
| ETH | -9,006 | -9,008 | -9,016 | -9,009 |
| ADA | -9,004 | -9,004 | -9,004 | -9,011 |
| AVAX | -9,002 | -9,002 | -9,002 | -9,061 |
| DOGE | -9,020 | -9,020 | -9,020 | -9,087 |
| UNI | -9,070 | -9,070 | -9,070 | -9,033 |
| XLM | -9,006 | -9,006 | -9,006 | -9,019 |
| PENDLE | -15,171 | -15,171 | -15,171 | -15,171 |
| JUP | -11,953 | -11,953 | -11,953 | -9,712 |
| RUNE | -9,301 | -9,301 | -9,301 | -9,301 |

Observaciones:
- 7 de 10 símbolos saturan en ~$-9K (bankruptcy floor para $10K initial / $1K threshold = pérdida máxima ~$9K antes del halt).
- PENDLE y JUP exceden el floor (saturan en -$15K y -$12K respectivamente) vía overshoots K=10-capped en el trade que cruza la línea.
- RUNE y PENDLE son idénticos entre los 3 regime configs — el detector no diferenció esos símbolos en este window.

**Reductio:** la "ganadora" `no_detector` es ganadora solo porque pierde 2.18% menos en agregado. El margen es noise dentro del piso de bancarrota.

### 2.2 A.4-1 ATR sweep (rc=0, NO_DATA universal)

- Cutoff: `2025-04-30T00:00:00 UTC`
- Runtime: 2161s con `cpu_count()` workers paralelos
- Grid: `sl ∈ {0.5, 0.7, 1.0, 1.2, 1.5, 2.0, 2.5}`, `tp ∈ {2.0, 3.0, 4.0, 5.0, 6.0}`, `be ∈ {1.5, 2.0, 2.5}` → 105 combinaciones por símbolo
- Train window: `[2024-01-30, 2025-01-30]` (12 meses)
- Validate window: `[2025-01-30, 2025-04-30]` (3 meses)

Resultado: para **cada uno** de los 10 símbolos, la mejor combinación del grid produce `pnl ≤ 0` en train, por lo que `optimize_symbol` retorna `NO_DATA` sin entrar a validate.

Artefacto: `data/retune/2026-05-11-pre-holdout-atr-evidence/{report.md, params.json, manifest.json}`. `params.json` preserva los valores actuales por diseño (no-change cuando recommendation=NO_DATA).

### 2.3 Comparativa explícita con el sweep ATR de 2026-05-01 (Gemini)

El sweep 5-01 (artefactos ya no en main, en PR history de #312) reportó CHANGE/KEEP con improvements positivos. La diferencia es exclusivamente el path del simulador:

| Aspecto | 5-01 (Gemini) | 5-11 (post-#287 fix) |
|---|---|---|
| Path simulador | legacy `atr_*` kwargs | `cfg + symbol_overrides` |
| Time-limit barrier | bypassed | **active** |
| Participation cap | bypassed | **active** |
| Bankruptcy halt (#280) | not yet in main | **active** |
| BTC resultado | CHANGE: (0.5, 3.0, 2.0), Val PnL Δ +$1,296 | NO_DATA |
| ETH resultado | CHANGE: (1.2, 3.0, 2.5), Val PnL Δ +$562 | NO_DATA |
| UNI resultado | CHANGE: (0.5, 3.0, 2.5), Val PnL Δ +$1,893 | NO_DATA |
| PENDLE resultado | CHANGE: (0.5, 2.0, 2.5), Val PnL Δ +$117 | NO_DATA |
| RUNE resultado | CHANGE: (1.5, 6.0, 2.5), Val PnL Δ +$1,379 | NO_DATA |

El 5-01 NO era live-equivalent (bypassaba gates). El 5-11 SÍ es live-equivalent. Los improvements del 5-01 eran reales bajo su modelo de simulación, pero ese modelo no refleja lo que correría en prod.

---

## 3 · Interpretación metodológica

### 3.1 Por qué esto es señal, no bug

Tres puntos de control:

1. **Las correcciones estructurales que se hicieron previo a este punto fueron motivadas por evidencia, no especulativas.** #309 (K=10 cap) salió del halt del 5-04 que mostró PENDLE -$1.7M. #313 (#280) salió del halt del 5-06 que mostró 60_40 vs no_detector contaminado por Bankruptcy Bias. Cada uno cerró un mecanismo concreto de inflación de P&L.
2. **El sweep regime del 5-11 valida que #280 funcionó.** Trade count cayó 92% (21,193 → 1,840) vs el 5-06. Los ~19K trades eliminados son exactamente los fictional zero-PnL post-bankruptcy.
3. **CLAUDE.md ya anticipaba este resultado:** *"Strategy backtest numbers in `2026-04-17-formula-ganadora-resultados-finales.md` ... are **pre-#223/#224** (phantom-profit fix). The 'real strategy contribution' decomposition in PR #223 showed those numbers were inflated."* Hoy tenemos cuantificación empírica de qué tan inflados estaban: bajo simulación live-equivalent, la edge cuantificada en docs viejos no aparece.

### 3.2 Lo que el hallazgo NO afirma

- **NO afirma "la estrategia no funciona en prod".** Live trading no es backtesting; hay interacciones (manual gating del operador, exclusiones E2-E5, condiciones de mercado en vivo) que el backtest no captura.
- **NO afirma "esta es la conclusión final".** El grid podría no cubrir el óptimo verdadero; los valores per-symbol de time-limit/participation-cap podrían ser demasiado restrictivos; el train window podría no ser representativo.
- **NO afirma "hay un bug en el simulador".** Las correcciones estructurales tienen tests, fueron revisadas. Si hubiera un bug residual, vendría aún más a favor del hallazgo (descubriríamos que aún hay inflación).

### 3.3 Connection con el aggregation gap (CLAUDE.md caveat #4)

El finding NO depende del agregado portfolio. Es per-símbolo: **cada uno** de los 10 símbolos individualmente bancarrota bajo cualquier regime, y **cada uno** retorna NO_DATA del grid search ATR. No hay un símbolo que "esté pulling positivo, otros lo arrastren". Todos cuestan capital.

Esto significa que portfolio-level aggregation fixes (eventual epic separado, deferred) no resolverían este hallazgo — la falla está en la mecánica per-símbolo.

---

## 4 · Decisiones que requieren validación externa

Antes de actuar sobre el hallazgo, las siguientes decisiones necesitan revisión por al menos un reviewer externo (humano, no agente):

### 4.1 ¿Es válido el setup de gates como contrato de "live-equivalent"?

**Lo que hicimos:** El fix de PR #287 (commits `06fcd02` y `7fef45c`) fuerza la ruta `cfg + symbol_overrides` cuando `cutoff` está activo, activando time-limit barrier + participation cap. Bankruptcy halt es unconditional.

**Pregunta para revisor:** ¿Es correcto que el ATR re-tune use gates activos? Argumentos:

- **A favor:** Sin gates activos, tuneamos sobre un mundo que NO existe en prod. Los params propuestos no transferirían. Comparabilidad con el harness regime exige misma realismo.
- **En contra:** Los gates per-symbol (`time_limit_hours`, `max_participation_rate`) fueron diseñados/tuneados bajo el simulador VIEJO (sin K=10 cap, sin bankruptcy halt). Aplicar gates "viejos" sobre simulador "nuevo" introduce un mismatch de calibración. Tal vez los gates necesitan re-tune también, antes que ATR.

**Implicación si la respuesta es "sí":** este finding es válido como está.

**Implicación si la respuesta es "necesitamos re-tune gates primero":** un epic intermedio antes de seguir con A.4-1/A.4-1.5.

### 4.2 ¿Es válido haber detenido en NO_DATA sin expandir el grid?

**Lo que hicimos:** Grid actual cubre `sl ∈ [0.5, 2.5]`, `tp ∈ [2, 6]`, `be ∈ [1.5, 2.5]`. NO_DATA significa: dentro de este grid, ningún punto produce P&L positivo. No probamos puntos fuera.

**Pregunta para revisor:** ¿Vale la pena correr un sweep con grid expandido? Si sí, ¿qué rangos? Argumentos:

- **A favor de expandir:** Tal vez el verdadero óptimo es `sl=3+ con tp=1` (estrategia mean-reversion ultra-corta) o `sl=0.3 con tp=10` (cazadora de eventos raros). El grid no cubre esos extremos.
- **En contra:** El grid es ancho — 105 puntos en una caja sensata. Si NINGUNO acerca a P&L positivo, expandir grid probablemente no rescate. Más probable que la estrategia simplemente no tenga edge en este window bajo gates realistas.
- **Sub-pregunta:** ¿qué constituiría "evidencia suficiente de no-edge" antes de aceptar el finding? ¿Necesitamos un grid de 1000+ puntos? ¿Un random search? ¿Una bayesian optimization?

### 4.3 ¿Cuál es el costo-beneficio correcto de A.4-3 (holdout) dado este finding?

**Contexto:** A.4-3 es bala única — corremos backtest sobre el holdout window 1 sola vez. Post-A.4-3 cualquier ajuste contamina la validez de la prueba.

**Pregunta para revisor:** ¿Vale la pena correr A.4-3 ahora? Argumentos:

- **A favor (ejecutar ya):** El holdout existe para ESTO. Si el pre-holdout dice "no edge", confirmar con holdout es la prueba honesta. Si holdout tampoco muestra edge, tenemos el ground truth para decidir sobre la estrategia.
- **En contra (no ejecutar todavía):** Si ya predecimos "no edge en holdout" con alta confianza, quemamos la bala por nada. Mejor primero resolver 4.1/4.2 (¿son gates correctos? ¿se debe expandir grid?) y luego correr holdout solo si esas resoluciones cambian el predictor.
- **Sub-pregunta:** ¿Qué resultado de A.4-3 sería "informativo" más allá del finding pre-holdout? Si holdout muestra exactamente lo mismo (todos bankrupt, NO_DATA), nada nuevo se aprende. Si holdout muestra otra cosa, ¿qué significa eso metodológicamente?

### 4.4 ¿Qué constituye "edge demostrable" para esta estrategia, formalmente?

**Pregunta para revisor:** El project ha estado operando con métricas implícitas. Pre-acción sobre el finding, formalizar:

- ¿Una estrategia con `sum_net_pnl > 0` en pre-holdout es "edge"? ¿O necesitamos un threshold mayor (e.g., Sharpe > 0.5, PF > 1.5, DSR > X)?
- ¿"Edge en N de M símbolos" cuenta, o tiene que ser portfolio-wide?
- ¿Cómo se cuantifica el "premium" del live trading (operator-gating, condiciones de mercado) sobre el backtest?

Sin esta formalización, no podemos decir definitivamente "este finding cierra el proyecto" vs "este finding requiere más investigación".

### 4.5 ¿El finding obliga a re-evaluar el #271 guardrail?

**Contexto:** CLAUDE.md "Inviting users — guardrail (#271)" dice: *"`trading.sdar.dev` does **not** get additional user accounts until both: (a) Epic A passes its validation bar (A.4 documented, A.6 published), and (b) Epic B (#253) is implemented."*

**Pregunta para revisor:** Si el finding se confirma post-review, ¿"Epic A passes its validation bar" pasa a ser inalcanzable con la estrategia actual? ¿Eso obliga a:
- Re-diseñar la estrategia antes de invitar usuarios?
- Re-formular qué significa "pasar la barra"?
- Comunicar el finding al actual usuario (Simón) explícitamente?

---

## 5 · Opciones de acción (con análisis preliminar)

**Operador NO toma acción hasta resolver §4. Opciones de aquí son tentativas.**

### Opción A: Aceptar el finding, archivar, parar A.4

- **Acción:** Mergear PRs #315 + este. NO correr A.4-3. NO promover params. Pausar el epic A.4. Comunicar a Simón el estado.
- **Pro:** Honestidad metodológica máxima. No quemamos holdout.
- **Con:** Cierra el camino actual sin habernos asegurado que no hay opción residual (4.2 sin explorar).
- **Reversibilidad:** alta — si nuevo finding emerge, se reabre.

### Opción B: Investigar 4.2 (grid expansion) antes de cerrar

- **Acción:** Correr un sweep ATR con grid 5-10x más amplio (e.g., `sl ∈ [0.2, 5.0]`, `tp ∈ [1.0, 10.0]`, `be ∈ [1.0, 4.0]`). Esto agrega ~24h de compute paralelizado. Si encuentra puntos positivos, refinar. Si NO_DATA persiste, refuerza el finding.
- **Pro:** Cierra una alternativa metodológicamente legítima antes de tomar la decisión grande.
- **Con:** 24h de compute. Si encuentra positivos, abre una nueva pregunta (¿es robust? ¿es overfitting?) que requiere walk-forward adicional.
- **Reversibilidad:** alta.

### Opción C: Investigar 4.1 (gate calibration) antes de cerrar

- **Acción:** Volver a Decisión 9 spec (§2.x). Verificar si los gates per-symbol fueron derivados bajo asunciones compatibles con el simulador actual. Si no, re-tunearlos. Después re-correr A.4-1.
- **Pro:** Direcciona la posibilidad metodológica más fina.
- **Con:** Riesgo de re-abrir Decisión 9 entera, que ya estaba pre-registered.
- **Reversibilidad:** media — re-abrir pre-registrations afecta credibilidad.

### Opción D: Correr A.4-3 con el finding pre-registered explícito

- **Acción:** Pre-registrar este finding como "predicción": "el holdout mostrará el mismo patrón (universal bankruptcy / NO_DATA)". Correr A.4-3. Si pasa, predicción confirmada → cierre formal del epic con evidencia. Si falla (holdout muestra edge inesperado), tenemos pregunta nueva para investigar.
- **Pro:** Usa la bala única de manera científicamente útil — testea la predicción que emerge del pre-holdout.
- **Con:** Pre-registration debe ser sólido. Si Reviewer externo encuentra debilidad en el setup pre-A.4-3, A.4-3 queda inválido también.
- **Reversibilidad:** baja — A.4-3 es bala única.

---

## 6 · Recomendación operativa preliminar

(Sujeta a override por revisor externo.)

**Camino sugerido:** **B → D**, en ese orden.

1. **B primero (grid expansion):** ~24h de compute paralelizado. Cierra metodológicamente la pregunta "¿podría haber un óptimo fuera del grid?". Resultado esperado: NO_DATA persiste; si no, abrimos una rama de investigación nueva.
2. **D después (A.4-3 con pre-registration):** Si B confirma NO_DATA universal, ejecutar A.4-3 como **predicción pre-registered** del mismo patrón. Resultado A.4-3 se evalúa contra la predicción, no contra una métrica abierta.

Razones para preferir esto sobre A (parar ya): cierra dos alternativas legítimas (grid + holdout) antes de archivar; aprovecha el holdout para EL caso de uso que justifica tenerlo.

Razones para no preferir C: re-abrir Decisión 9 antes de cerrar A.4-3 es invertir orden de pre-registrations. Si gates necesitan re-tune, es un epic post-A.4-3, no pre-.

---

## 7 · Preguntas explícitas para revisor externo

Cuando circulen este spec, los items a confirmar/discutir:

1. ¿`cfg + symbol_overrides` es el path correcto para el ATR re-tune, o el legacy kwargs path es defendible para tuning specifically?
2. ¿El grid actual es suficiente, o necesitamos expandir antes de aceptar NO_DATA?
3. ¿Qué constituye formalmente "edge demostrable" para esta estrategia? (Sharpe? PF? DSR? Win rate? Algo más?)
4. ¿La predicción "holdout mostrará el mismo patrón" es lo suficientemente sólida para pre-registrarse, o necesitamos más sub-tests antes?
5. ¿Es defendible NO promover ningún cambio de params dado este finding, o el operador debe tomar la decisión de mantener los valores actuales pre-leakage como "best guess available"?
6. Si el finding se confirma, ¿qué implicaciones tiene sobre el guardrail #271 (sin nuevos usuarios hasta epic A pase validación)?
7. ¿Hay algún experimento simple que pudiera reabrir el espacio (e.g., backtest sobre window distinto, basket diferente, parametrización dramáticamente distinta) que se debiera intentar antes de cerrar?

---

## 8 · Artefactos consultables

Todos visibles en este PR y/o el PR de evidencia regime:

- **A.4-1.5 regime sweep evidence** — PR #315, archivo `data/retune/2026-05-11-pre-holdout-regime-evidence/`
- **A.4-1 ATR sweep evidence (este PR)** — `data/retune/2026-05-11-pre-holdout-atr-evidence/`
- **Logs originales** — `logs/retune/regime_2026-05-11.log`, `logs/retune/atr_2026-05-11.log` (local only)
- **Manifests (con hashes y leakage_check PASS)** — incluidos en ambos evidence dirs
- **Harness code on main** — `tools/regime_retune_pre_holdout.py` (#306), `tools/retune_pre_holdout.py` (#287 + post-merge fix)
- **Bankruptcy handler** — `backtest.py` (#313, #280)

---

## §A · Amendment 2026-05-11 — 3 meta observations from external review

Esta sección incorpora las tres observaciones meta de la revisión externa (sesión Claude fresh, registrada en el comment chain de PR #316). El draft original sub-comunicaba estos puntos; el amendment los hace explícitos para que cualquier lector futuro (humano u otra sesión Claude) los encuentre sin tener que reconstruir el contexto del review.

### §A.1 — La "live-equivalent simulation" es una cadena de calibraciones, no un veredicto del simulador

El draft original presentaba el sweep del 2026-05-11 como "el primero que mide la estrategia bajo condiciones live-equivalent" y trataba esa propiedad como atómica. Es más preciso decir que el resultado depende de una **cadena de decisiones de calibración**, cada una con su propia banda de incertidumbre:

| Calibración | Origen | Naturaleza | Incertidumbre |
|---|---|---|---|
| `MAX_OVERSHOOT_RATIO = K = 10` (#309) | Rule-derived ("no realistic execution holds through a 10× SL move") | Modeling decision | Subject to revision under pre-registration; specific K=10 chosen as canonical conservative anchor |
| `BANKRUPTCY_THRESHOLD = 0.1 × INITIAL_CAPITAL` (#313, #280) | Convention (90% drawdown = practical force-liquidation) | Rule-derived | Stable; reviewed under pre-registration if revised |
| `time_limit_hours` per symbol | #281 "winner-median holding" computed on pre-#223 simulator output | Empirically derived | **Contamination real** — derivation input was buggy; see Issue #317 |
| `cooldown_hours` per symbol | `max(time_limit_hours, NW=4, floor=6)` | Rule-derived transitively from `time_limit_hours` | Inherits contamination of `time_limit_hours` |
| Tier mapping per symbol | #281 cost-spectrum analysis on pre-#223 simulator | Empirically derived | Contamination real (same pre-#223 source) |
| `max_participation_rate` per tier — value | Almgren-Chriss + Donier-Bonart anchors | Theoretical / anchor | Open question per Issue #317 Step 1: is it strict A-C-applied or operator-chosen anchor? |

La conclusión "no edge demostrable bajo simulación live-equivalent" **hereda la incertidumbre acumulada de esta cadena**, no es un único veredicto del simulador. Esto importa porque un stakeholder externo (Simón, futuro reviewer, futuro contribuyente) leyendo el hallazgo debe ver explícitamente qué supuestos estructurales lo sostienen y dónde está el ground potencialmente firme vs blando.

### §A.2 — Bug fixes vs modeling decisions, distintos en narrativa

El draft original enumera tres correcciones estructurales como un grupo:

> "(...) las correcciones estructurales (#223/#224, #309, #313) (...)"

Estas correcciones son **tres cosas distintas de naturaleza distinta**, y agruparlas sub-comunica la fuerza del finding:

- **#223 / #224 — Bug fix.** Error de signo en `_close_position`: la fórmula LONG se aplicaba unconditionally, lo que invertía P&L para SHORT; `abs()` faltaba en `sl_pct_actual`, lo que strip-eaba el signo y producía "phantom profit" igual a `risk_amount`. **Esto no es "calibración imprecisa", es aritmética incorrecta.** Los números pre-fix en `formula-ganadora` no eran "tuneados bajo un modelo más simple"; eran resultados de un cálculo equivocado.
- **#313 (#280) — Bug fix.** Post-bankruptcy ghost trades: el simulador seguía procesando entries con `risk_amount = 0` después del floor `effective_capital = max(0, capital)`, inflando trade counts y win rates con eventos ficticios. **También error de cálculo, no decisión de modelo.**
- **#309 — Modeling decision.** K=10 cap como respuesta al overshoot vía amplification ratio. Es una elección de modelo (no un bug); tiene su propia banda de incertidumbre; está sujeto a revisión bajo pre-registración.

El framing correcto, que reemplaza al sub-truthful "we made the simulator more realistic":

> **"Los backtests previos (incluidos los 4-year results y el `formula-ganadora` doc) reflejaban bugs del simulador, no comportamiento de la estrategia."**

Esta narrativa es más fuerte, más incómoda, y más precisa. Es la que va a la comunicación con Simón si la decisión de §5 termina escalando.

### §A.3 — Pre-registration gap: caso NO_DATA no contemplado en D9

El spec D9 (`2026-05-03-asunciones-tecnicas-pre-holdout.md`) pre-registró la secuencia A.4-1 → A.4-2 → A.4-3 bajo el supuesto implícito de que el re-tune produciría candidatos viables. **El caso "0 de 10 símbolos producen P&L positivo en ningún punto del grid" no estaba contemplado.**

Esto **no es violación** de D9 — D9 se está siguiendo exactamente. Es **incompletud** en la pre-registración misma.

Consecuencia operativa: "expandir el grid" se siente como continuación natural de A.4-1 porque la pre-registración no contempló el caso NO_DATA. Pero formalmente, **expandir el grid es un cambio metodológico post-hoc** — el tipo exacto de cosa que los pre-registers existen para prevenir.

Por eso, cualquier expansión del grid:

1. **DEBE estar justificada por criterio falsificable pre-registrado** (Issue #318 lo formaliza con el criterio "≥6 de 10 símbolos en borde + gradiente > max(2σ, $200)").
2. **DEBE estar tagged en el historial del spec** como ajuste metodológico post-hoc, con fecha y trigger.
3. **NO modifica D9** — los pre-registers son inmutables por disciplina. El amendment vive en este spec inflection-point.

Si el criterio de Issue #318 falla y NO_DATA se acepta como conclusión, no hay ajuste post-hoc — A.4-1 concluye dentro del marco pre-registrado original (resultado: no candidates). La rama post-hoc se activa solo si el criterio pasa y el grid se expande.

### Cierre del amendment

Estas tres observaciones fueron incorporadas como amendment, no como edición de §1–8 originales. La versión original del draft (§1–8 + §9 historial inicial) se conserva como historical record del estado pre-revisión. Cualquier lector futuro debe leer §A en conjunción con §1–8 para tener el marco metodológico completo.

---

## 9 · Historial de actualización

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-11 | Draft inicial post-sweeps | Claude Opus 4.7 |
| 2026-05-11 | Amendment §A — 3 meta observations from external review (sesión Claude fresh) | sssamuelll + Claude Opus 4.7 (esta sesión) |

(Reservar líneas adicionales para registrar revisión externa adicional, cambios pre-registered, decisión final del operador.)
