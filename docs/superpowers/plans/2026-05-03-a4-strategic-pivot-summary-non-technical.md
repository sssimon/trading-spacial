# Plan estratégico — Pivot del sistema de señales BTC/USDT (actualización post-ejecución)

**Fecha:** 3 de mayo de 2026
**Para:** Analista de trading / stakeholder no técnico
**Supersede:** [versión del 1 de mayo](./2026-05-01-a4-strategic-pivot-summary-non-technical.md) — escrita pre-shipping
**Resumen ejecutivo:** Las 9 decisiones tomadas el 1 de mayo se ejecutaron entre el 1 y el 3 de mayo. **La parte estructural del pivot está completa**: Triple Barrier con time-limit per-symbol + sizing cap per-symbol + cooldown auto-enforce per-symbol — los tres barreras funcionando, integradas, con cobertura de tests. La fase de evaluación honesta contra holdout (A.4 v2) sigue pendiente. Esta versión incorpora feedback del senior reviewer recibido el 3 de mayo (5 observaciones) y documenta los refinamientos al plan original.

---

## 1. Qué se ejecutó del plan del 1 de mayo

Las 9 decisiones del plan del 1 de mayo se ejecutaron entre el 1-3 de mayo. Resumen:

| Decisión (1 mayo) | Estado al 3 mayo |
|---|---|
| **D1** — Path A.4 (rediseño exit, no más ATR tuning) | ✅ Ejecutado |
| **D2** — Triple Barrier per-cluster con time-limit | ✅ Mergeado (PR #296) |
| **D3** — Reducir basket a 4 monedas | 🔄 Reformulado — ver §2.1 |
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

### 2.1 — D3 reformulado: basket de 10 con valores diferenciados (no reducción a 4)

**Plan original (1 mayo):** "Reducir a 4 monedas (PENDLE, ADA, BTC, ETH); las 6 restantes no se tunean."

**Approach actualizado (3 mayo):** Las 10 monedas se mantienen en la canasta operativa, con valores per-symbol diferenciados (time-limit, cap, cooldown) derivados del research §5 (master table). Las 6 monedas "no-edge-sólida" tienen valores conservadores (basket default: TL=5h, cap más restrictiva, cooldown=6h floor) que les permiten generar trades sin destruir el sistema.

**Por qué cambió:** durante la implementación (1-3 mayo) el research §5 mostró que un basket default conservador para las 6 no-rentables genera trades con expectancy positiva neta **cuando los 3 barreras estructurales están activos**. El plan original (basket de 4) habría sido apropiado si las 6 fueran inrescatables incluso bajo Triple Barrier — el research no soporta esa conclusión más fuerte.

**La decisión final sobre permanencia de las 6** se mantiene post-A.4 holdout (igual que el plan original). El cambio es de "removerlas hoy" a "evaluarlas contra holdout primero, decidir después con datos".

### 2.2 — D5 re-clasificado: cap es crítico (no parallel track)

**Plan original (1 mayo):** "Deprioritizar cap a track paralelo. Razón: el problema dominante es exit logic, no sizing."

**Actualización (3 mayo):** Cap shipped como PR #297 en el critical path del epic estructural.

**Esto NO es un cambio de opinión — es re-clasificación tras research adicional.** La narrativa correcta:

- **1 de mayo:** con la información disponible, deprioritizar cap era razonable. El problema dominante diagnosticado era exit logic; el sizing parecía optimización secundaria.
- **2-3 de mayo:** research §5 (master table de caps con anchors Almgren-Chriss / Bouchaud / Donier-Bonart / Kaiko / Binance LiquidityBoost) mostró que **sin cap el sistema no es evaluable contra holdout**. Sin cap, los sizing estimates del backtest pueden producir notional > liquidez disponible — el backtest mide trades que no podrían haberse ejecutado en realidad. **El cap es infraestructura de evaluabilidad**, no un parámetro de tuning.

**Implicación:** el plan del 1 de mayo era correcto con la información de entonces; la iteración fue honesta cuando llegó información nueva (el research §5). La auditabilidad del proceso (cambios documentados, rationales explícitos, decisiones reversibles) es el valor — no la inmutabilidad del plan.

### 2.3 — D6 pausado: re-baseline post-A.4

**Plan original (1 mayo):** "Re-baseline ejecutado HOY como prerequisito de cualquier comparación futura."

**Actualización (3 mayo):** Ticket #272 abierto, sin trabajo activo. **Pausado intencionalmente.**

**Por qué:** medir el sistema "como está mal" no agrega información — la prioridad fue arreglar el sistema (los 3 PRs estructurales) antes de re-medir. El re-baseline se ejecutará cuando agrega valor: post-A.4 holdout, midiendo el sistema arreglado vs. baseline para cuantificar la mejora bajo el threshold del Gate 3.

### 2.4 — D9 + reformulación de R1: N=2 effective DSR pre-registered

**Plan original (1 mayo) R1:** "El grid de time-limit es el ÚNICO grado de libertad nuevo en A.4 v2."

Estrictamente, PR #297 introdujo 9 valores per-symbol del cap. Eso podría leerse como 9 grados de libertad adicionales = expansión del N del Deflated Sharpe Ratio.

**Resolución pre-registrada (en spec D9 = PR #298):**

- Los 9 caps **NO se cuentan como 9 trials independientes**. Se cuentan como **1 bloque protocol-derived** (research §5 master table con anchors externos sostenibles ante terceros).
- **N efectivo total = 2 trials**: 1 (time-limit grid) + 1 (caps bloque protocol-derived).
- Alternativa estricta N=10 (1 + 9) explícitamente rechazada en spec D9 §2.5 con rationale documentado.
- Alternativa conservadora N=3 disponible como fallback si reviewer externo no acepta N=2.

**Pre-commitment crítico (load-bearing):** cualquier ajuste post-holdout sobre N o sobre los valores locked (incluidos los 9 caps + los 10 time-limits + los 10 cooldowns) **disqualifies el resultado como validación primaria**. La spec D9 fija el commitment ex-ante; el holdout evalúa contra ese commitment, no contra commitments que sería conveniente tener post-resultado.

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

1. **§2.1** — el cambio de approach sobre la canasta (basket de 10 con valores diferenciados, vs. reducción a 4 del plan original)
2. **§2.2** — la re-clasificación del cap de "parallel track" a "crítico"
3. **§2.4** — el N=2 effective DSR (vs. N=10 estricta o N=3 conservadora)

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
