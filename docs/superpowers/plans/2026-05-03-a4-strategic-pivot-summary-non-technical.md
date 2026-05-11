# Plan estratégico — Pivot del sistema de señales BTC/USDT (actualización post-ejecución)

**Fecha:** 3 de mayo de 2026
**Para:** Analista de trading / stakeholder no técnico
**Supersede:** [versión del 1 de mayo](./2026-05-01-a4-strategic-pivot-summary-non-technical.md) — escrita pre-shipping
**Resumen ejecutivo:** Las 9 decisiones tomadas el 1 de mayo se ejecutaron entre el 1 y el 3 de mayo. **La parte estructural del pivot está completa**: Triple Barrier con time-limit per-symbol + sizing cap per-symbol + cooldown auto-enforce per-symbol — los tres barreras funcionando, integradas, con cobertura de tests. **Importante:** "pivot estructural completo" significa que la nueva estructura funciona técnicamente — NO prueba que el sistema tenga edge. Edge real se decide en el holdout (A.4 v2), pendiente. Esta versión incorpora feedback del senior reviewer recibido el 3 de mayo (5 observaciones, 3 sustantivas + 2 menores) y documenta los refinamientos al plan original.

---

## 1. Qué se ejecutó del plan del 1 de mayo

Las 9 decisiones del plan del 1 de mayo se ejecutaron entre el 1-3 de mayo. Resumen:

| Decisión (1 mayo) | Estado al 3 mayo |
|---|---|
| **D1** — Path A.4 (rediseño exit, no más ATR tuning) | ✅ Ejecutado |
| **D2** — Triple Barrier per-cluster con time-limit | ✅ Mergeado (PR #296) |
| **D3** — Reducir basket a 4 monedas | 🔄 Modulado: basket operativo de 10 + holdout primario de 4 + secondary exploratory en 6 — ver §2.1 |
| **D4** — Mergear código re-tune, no params | ✅ Ejecutado |
| **D5** — Cap de sizing deprioritizado | 🔄 Re-clasificado a crítico (PR #297) — ver §2.2 |
| **D6** — Re-baseline | ⏸️ Pausado — ver §2.3 |
| **D7** — Mergear research/diagnóstico | ✅ Ejecutado (PRs #295, #293) |
| **D8** — Update doc maestro con caveat cluster | ✅ Ejecutado vía operational model spec |
| **D9** — Asunciones técnicas heredadas pre-holdout | ✅ Documentado (spec D9 = PR #298) |

**Resultado neto al 3 de mayo:** 3 PRs estructurales (Triple Barrier completo) + spec D9 + base técnicamente lista para A.4 holdout. 1254 tests + 3 smoke tests passing. Sistema en estado evaluable.

---

## 2. Cinco refinamientos al plan original (feedback del senior reviewer)

El senior reviewer revisó el doc del 1 de mayo el 3 de mayo y propuso 5 observaciones críticas. Cómo se incorporan:

### 2.1 — D3 modulado: basket de 4 para holdout primario + 6 en secondary exploratory

**Plan original (1 mayo):** "Reducir a 4 monedas (PENDLE, ADA, BTC, ETH); las 6 restantes no se tunean."

**Approach actualizado (3 mayo, post-feedback del senior reviewer):**

- **Basket operativo (production scanner):** se mantienen las 10 monedas con valores per-symbol diferenciados — preserva continuidad operacional sin código removido.
- **Holdout primario (la evaluación que cuenta para Gate 3 / DSR):** **basket de 4** — PENDLE, ADA, BTC, ETH. Igual que el plan original.
- **Secondary exploratory (reportado separadamente):** las 6 monedas "no-edge-sólida" (DOGE, JUP, RUNE, XLM, AVAX, UNI) se evalúan contra holdout pero los resultados se reportan **explícitamente marcados como exploratorios**. **NO cuentan para el threshold del Gate 3.** Sirven para informar la decisión post-A.4 sobre permanencia (mantener / remover / rediseñar scoring per-coin).

**Por qué este cambio frente al primer borrador del 3 mayo:**

El primer borrador del doc del 3 mayo afirmó que "research §5 mostró que un basket default conservador para las 6 no-rentables genera trades con expectancy positiva neta". El senior reviewer flaggeó esa frase como infundada. **Verificación honesta:** research §5 línea 247 dice literalmente "5/10 símbolos con basket-default driven by absence-of-evidence, no presence-of-evidence... if A.4 validation muestra que estos 5 siguen perdiendo bajo cualquier TL, the next loop debe revisitar basket reduction (deferred — out of scope here)". Research §5 NO contiene quantified expectancy positiva sobre las 6. La claim del primer borrador era unsupported.

**Honestidad sobre el riesgo:** incluir las 6 en el holdout primario habría aumentado el N efectivo del DSR (~6 evaluaciones adicionales) sin evidencia de viabilidad. La separación "primario = 4, secondary exploratory = 6" preserva el commitment del plan original ("no incluirlas hasta tener evidencia OOS de viabilidad") sin tirar el dato exploratorio que las 6 contra holdout pueden producir.

**N effective del holdout primario:** el cómputo (ver §2.4) cuenta solo los 4 símbolos del basket primario. El secondary exploratory NO se incluye en el N — por construcción, no afecta threshold.

**La decisión final sobre permanencia de las 6** se hace post-A.4 holdout, informada por: (a) primary holdout result sobre 4, (b) secondary exploratory result sobre 6. Igual que el plan original — solo la metodología cambia.

### 2.2 — D5 re-clasificado: cap es crítico (no parallel track)

**Plan original (1 mayo):** "Deprioritizar cap a track paralelo. Razón: el problema dominante es exit logic, no sizing."

**Actualización (3 mayo):** Cap shipped como PR #297 en el critical path del epic estructural.

**Esto NO es un cambio de opinión — es re-clasificación tras research adicional.** La narrativa correcta:

- **1 de mayo:** con la información disponible, deprioritizar cap era razonable. El problema dominante diagnosticado era exit logic; el sizing parecía optimización secundaria.
- **2-3 de mayo:** research §5 (master table de caps con anchors Almgren-Chriss / Bouchaud / Donier-Bonart / Kaiko / Binance LiquidityBoost) mostró que **sin cap el sistema no es evaluable contra holdout**. Sin cap, los sizing estimates del backtest pueden producir notional > liquidez disponible — el backtest mide trades que no podrían haberse ejecutado en realidad. **El cap es infraestructura de evaluabilidad**, no un parámetro de tuning.

**Implicación:** el plan del 1 de mayo era correcto con la información de entonces; la iteración fue honesta cuando llegó información nueva (el research §5). La auditabilidad del proceso (cambios documentados, rationales explícitos, decisiones reversibles) es el valor — no la inmutabilidad del plan.

### 2.3 — D6 pausado: re-baseline post-A.4

**Plan original (1 mayo):** "Re-baseline ejecutado HOY como prerequisito de cualquier comparación futura."

**Actualización (3 mayo):** Ticket #272 abierto, sin trabajo activo. **Pausado intencionalmente** — se ejecuta post-A.4 holdout, no pre.

**Por qué (framing revisado post-feedback del senior reviewer):**

El plan original del 1 de mayo asumía que la decisión del 1 de mayo era reversible — si el re-baseline mostraba algo inesperado, podía cambiar el plan. Para el 3 de mayo, los 3 PRs estructurales ya están mergeados; **el re-baseline del sistema pre-fix no afecta decisiones ya tomadas**.

**El re-baseline post-fix sí informa:** cuantifica la mejora del sistema arreglado vs. el baseline histórico, contra el threshold del Gate 3. Por eso D6 se ejecuta post-A.4 holdout, no pre.

(El framing original — "medir el sistema como está mal no agrega información" — era defendible pero ambiguo. El framing revisado especifica POR QUÉ no agrega información: porque las decisiones ya están tomadas. Sin esa especificación, suena a racionalización.)

### 2.4 — D9 + reformulación de R1: N=3 effective DSR pre-registered (elevado de N=2 post-feedback)

**Plan original (1 mayo) R1:** "El grid de time-limit es el ÚNICO grado de libertad nuevo en A.4 v2."

Estrictamente, PR #297 introdujo 9 valores per-symbol del cap. Eso podría leerse como 9 grados de libertad adicionales = expansión del N del Deflated Sharpe Ratio.

**Primer borrador del 3 mayo proponía N=2 con la siguiente decomposition:** 1 (time-limit grid) + 1 (caps tratados como bloque protocol-derived).

**Feedback del senior reviewer:** el bloque "caps protocol-derived" tiene DOS componentes que merecen counting separado:

- **(a) Cap values per tier** (1.0% / 0.5% / 0.3% / 0.2% / 0.15%) — protocol-derived genuinely externo (Almgren-Chriss / Bouchaud / Donier-Bonart / Kaiko / Binance LiquidityBoost). Si el basket fuera otro, los valores no cambiarían (vienen de literatura).
- **(b) Asignación moneda → tier** — data-derived del basket actual (anchored en `cost_bps_mean` per-symbol del train segment). Si el basket fuera otro, las asignaciones serían distintas.

El senior reviewer elevó esto a "tier mapping = N=1 separable, no folded into cap-values protocol".

**Resolución actualizada (spec D9 PR #301 — elevación 2 → 3 PRIMARY):**

- **N efectivo total = 3 trials PRIMARY:** 1 (TL grid) + 1 (cap values, §2.5 spec D9) + 1 (tier mapping, §2.8 spec D9, data-derived).
- Alternativa optimista N=2 (collapsing tier mapping back into cap-values) — discouraged. El reviewer externo argumentó convincingly que la asignación SÍ es data-aware.
- Alternativa estricta N=10 (1 TL + 9 caps independent) — rechazada. Los cap-values son 5 tier values con asignación per-symbol determinada por §2.8.

**Implicación:** N=3 vs N=2 produce threshold del Gate 3 ligeramente más alto (~0.05-0.10 en SR units, rough). Threshold más alto = harder de pasar = mayor confianza si pasa. **Esto es honest cost de reconocer el data-awareness del tier mapping** — preferible a curve-fit ex-post.

**Pre-commitment crítico (load-bearing):** cualquier ajuste post-holdout sobre N o sobre los valores locked (incluidos los 9 caps + los 10 time-limits + los 10 cooldowns + el tier mapping) **disqualifies el resultado como validación primaria**. La spec D9 fija el commitment ex-ante; el holdout evalúa contra ese commitment, no contra commitments que sería conveniente tener post-resultado.

### 2.5 — Vocabulario: "segmento de entrenamiento" / "train segment"

**Plan original (1 mayo):** Usa "segmento de prueba" en §1.

El término "segmento de prueba" en español es ambiguo: en machine learning, "prueba" se traduce típicamente como "test set" — que es lo opuesto a lo que el doc describe. **El término correcto es "segmento de entrenamiento" o "train segment"** — los 18 meses de datos sobre los que se ejecutaron diagnóstico, calibración, y smoke tests. El holdout (test set) está intacto y locked desde 2025-04-30.

### 2.6 — Reframe de "shipped = validated" para los 3 PRs estructurales

El plan del 1 de mayo no usaba la palabra "validated" para PR1 explícitamente, pero el narrativo de "shipping" puede leerse implícitamente como validación. Honestamente:

- Los 3 PRs estructurales (#296 + #297 + #299) **implementan** el diseño Triple Barrier completo.
- **No están "validados"** en términos de edge. Ningún número de smoke test prueba edge. Smokes verifican que la estructura funciona técnicamente (los 3 barreras se activan correctamente cuando deben); no prueban que la estructura genera P&L positivo en producción.
- **La validación cuantitativa es la fase A.4 holdout** — el único test que matters es el holdout pre-registered contra el threshold del Gate 3.

---

## 3. Lo que queda — A.4 holdout evaluation

Phase 5 del plan del 1 de mayo. Sigue siendo el único capítulo pendiente para cerrar el epic estructural y comunicar a Simon con números honestos.

**Pasos concretos:**

1. **Re-tune `atr_sl_mult/tp/be` sobre `[earliest, holdout_start - 1 bar]`.** Caveat #1 de spec D9: los valores actuales del SL/TP fueron tuneados sobre full history (incluyendo el holdout range). Eso es leakage — usar esos valores contra el holdout no es test honesto. El re-tune correcto = re-calibrar SL/TP solo sobre el train segment (sin tocar el holdout).
2. **Evaluar el sistema arreglado contra holdout** (12 meses, locked desde 2025-04-30, SHA-256 verified).
3. **Aplicar interpretation tree de spec D9 §5** — tres scenarios pre-registrados: 5.2.a (pasa con confianza), 5.2.b (pasa marginal, requiere reflexión sobre robustez), 5.2.c (falla).
4. **Comunicación a Simon** con números honestos + decisión final sobre las 6 monedas no-rentables (mantenerlas en la canasta vs. removerlas vs. rediseñar scoring per-coin).

**Eta:** 1-2 semanas. Bloqueador: requirement del re-tune correcto (caveat #1) — sin él, la evaluación contra holdout es leakage.

---

## 4. Estado actual

| Aspecto | Estado |
|---|---|
| Pivot estructural | ✅ Completo (PRs #296 + #297 + #298 + #299 mergeados a main) |
| Pre-registered commitments | ✅ Locked en spec D9 |
| Sistema evaluable contra holdout | ✅ Sin las 3 barreras, holdout sería test contra estructura rota |
| Edge real demostrada | ⏳ Pendiente A.4 holdout — smokes no son prueba de edge |
| Re-baseline post-fix | ⏸️ Pausado intencionalmente — se ejecuta post-A.4 |
| Comunicación a Simon | ⏳ Pendiente A.4 holdout (con números honestos, no antes) |

---

## 5. Lo que NO cambia (cotas duras preservadas)

- **Holdout locked.** 2025-04-30 cutoff, 12 meses, SHA-256 verified, file system read-only, AST-based test scanner falla CI si código nuevo lo referencia fuera del protocolo.
- **Criterio de éxito pre-registrado.** A.4 v2 se considera exitoso si y solo si **pasa el threshold del Gate 3 / DSR de ticket #249** contra el holdout. Cualquier resultado por debajo es "falló", sin importar cercanía. No hay "casi pasó", no hay sub-resultado rescatable post-hoc, no hay narrativa que cambie el verdict.
- **Cota dura del N efectivo.** Cualquier expansion post-hoc del grid de TL o de los locked values (caps, cooldowns) **disqualifies como validación primaria**. Esta cota es lo que separa research disciplinada de curve-fit ex-post.
- **Las 6 monedas "no-edge-sólida"** siguen en la canasta operativa con valores conservadores; decisión final post-A.4.

---

## 6. Para el analista — preguntas concretas

Si tiene observaciones sobre:

1. **§2.1** — el approach actualizado sobre la canasta (basket operativo de 10 + holdout primario de 4 + secondary exploratory en 6)
2. **§2.2** — la re-clasificación del cap de "parallel track" a "crítico"
3. **§2.3** — el framing del re-baseline pausado (re-baseline pre-fix no afecta decisiones tomadas; post-fix sí informa cuantificación de mejora)
4. **§2.4** — el N=3 effective DSR PRIMARY (elevado de N=2 post-feedback, vs. N=10 estricta o N=2 optimista discouraged)

por favor compártalas **antes de arrancar A.4 holdout**. Una vez la holdout evaluation arranque, los commitments están fijados — la honestidad del test depende de que el test arranque contra commitments inmutables. El window para refinamiento es ahora.

Si tiene confirmación de que el plan revisado es razonable, también es valor — destraba A.4.

---

**Apéndice — referencias técnicas (para quien quiera profundizar):**

- Plan completo del 1 de mayo: `docs/superpowers/plans/2026-05-01-a4-strategic-pivot-plan.md`
- Spec D9 (asunciones pre-holdout, N efectivo): `docs/superpowers/specs/es/2026-05-03-asunciones-tecnicas-pre-holdout.md`
- Diagnóstico que disparó el pivot: `docs/superpowers/specs/es/2026-04-30-a02-diag-deep-dive.md`
- Research §5 master table (caps + TL + cooldown): `docs/superpowers/research/2026-05-02-structural-fix-parameter-study.md`
- Benchmark de exit logic crypto: `docs/superpowers/research/2026-04-30-exit-logic-benchmark-crypto.md`
- Operational model (E5 promotion): `docs/superpowers/specs/es/2026-05-01-operational-model-manual-gating.md`
