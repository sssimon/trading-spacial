# Diseño del agregador del kill-switch — output del brainstorming, PAUSADO esperando #272

**Fecha:** 2026-05-28
**Estado:** PAUSADO. Spec parcial. **Bloqueado por #272 (re-baselining honesto del backtest)**. Reabrir cuando #272 cierre y existan baselines limpios.
**Autores:** Samuel + asistente, con reframes de Aurelius Voronov y Axiom-0 + medición de scope de Plumb-Lindgren.
**Issues relacionados:** #272 (prerrequisito), #397 (bias DD precondición del flip), #322 (holdout unblock), ks-v2 epic (roadmap del kill-switch).

---

## Por qué este doc existe

Una sesión de brainstorming intentó diseñar "el agregador del kill-switch" para destrabarjar la promoción shadow→active del kill-switch v2. La sesión descubrió a media camino que el verdadero prerrequisito no es el agregador — es **#272 (re-baselining honesto)**. Sin baselines limpios cualquier experimento que compare configuraciones del agregador compara ruido contra ruido.

Este doc captura lo que la sesión sí pudo cerrar estructuralmente y deja explícita la decisión que falta tomar empíricamente. Se reabre cuando #272 cierre.

## Lo que la sesión confirmó estructuralmente

### 1. La cosmología original "v1 vs v2" era ambigua

La intención del proyecto era que v2 fuera **la mejora de v1** que, una vez validada en shadow, lo reemplazaría. Esto es válido como **intención de roadmap**, pero el código no la realiza literalmente: v2 no implementa "lo mismo que v1, mejor". v2 implementa **tres filtros separados** (portfolio DD, velocity, per-symbol baseline-σ), de los cuales solo uno (per-symbol baseline-σ) es comparable a lo que medía v1 (salud mensual por símbolo). Los otros dos son **dimensiones nuevas**.

Por lo tanto "promover v2 y deprecar v1" no es una operación atómica. Descompone en:

- **Reemplazo:** el sensor per-symbol de v2 (baseline-σ, rolling) sustituye al sensor de v1 (PnL mensual, calendario).
- **Adición:** los otros dos filtros de v2 (portfolio DD, velocity) se activan como restricciones nuevas.

La pregunta empírica abierta es **cuánto del filtro v1 mensual queda cubierto por el filtro v2 baseline-σ**. No es respondible por inspección — requiere experimento.

### 2. La invariante actual que el sistema respeta sin nombrarla

Hoy el callsite es el único agregador. Cada motor (v1, v2) escribe evidencia al decision log; ninguno decide `(size_factor, skip)` por sí solo. El callsite (`btc_scanner.py:262-326`) hace un mapeo ad-hoc del estado v1 a `size_factor` y el shadow path de v2 escribe `size_factor=1.0, skip=False` hardcoded. El "shadow mode" no es un estado del sistema — es el nombre del periodo durante el cual el sistema aplazó la creación del agregador.

### 3. Forma del artefacto

**B1 — Funciones libres + combinador puro.**

```
strategy/
  predicates/                    # cada filtro es una función pura
    portfolio_dd.py              # evaluate(ctx) -> Restriction
    velocity.py                  # evaluate(ctx) -> Restriction
    per_symbol_health.py         # mensual (v1) — opcional según experimento
    per_symbol_baseline.py       # baseline-σ (v2)
  gating.py                      # compose([Restriction, ...]) -> Decision

btc_scanner.py
  ↓ llama cada predicado explícitamente
  ↓ pasa los outputs a compose()
```

Cada predicado se entiende solo. El combinador se entiende solo. Tests por predicado + un test del combinador.

Descartadas: B2 (registry tipado) por over-engineering hasta que N>4; B3 (callsite como agregador implícito) por no resolver el drift backtest↔live ni producir artefacto auditable.

### 4. Semántica de combinación

**Producto multiplicativo sobre [0, 1].**

```python
size_factor = portfolio_factor * per_symbol_factor * velocity_factor * (mensual_factor opcional)
skip = (size_factor == 0.0)
```

No se eligió "min" (la operación natural bajo "restricciones ortogonales con veto") por una razón empírica, no estética: **el simulator de backtest (`strategy/kill_switch_v2_simulator.py:151-152`) ya implementa producto multiplicativo**, y los baselines de fitness de #187 / #216 / B4b.2 están calibrados sobre esa semántica. Cambiar a min rompe baselines y viola Non-Negotiable #5 sin un re-baselining explícito. Producto se interpreta como "factores multiplicativos de reducción independientes" — cada predicado modula la confianza disponible.

Este punto se reabre **solo si #272 también re-baselinea contra una semántica alternativa**, lo cual sería un add-on al alcance de #272 que hoy no está propuesto.

### 5. Origen del agregador

**No se construye de cero. Se extrae del simulator.**

El código que se llamaría `strategy/gating.py` ya existe — vive dentro de `V2KillSwitchSimulator.should_skip_or_reduce()` (`strategy/kill_switch_v2_simulator.py:84-152`). El trabajo de implementación es:

1. Extraer la composición multiplicativa del simulator a un módulo puro `strategy/gating.py`.
2. Hacer que el simulator de backtest consuma esa función (en vez de tener su propia copia inline).
3. Hacer que el scanner live consuma esa misma función (en vez del dict ad-hoc actual en `btc_scanner.py:262-278`).

Esta extracción cierra el drift backtest↔live por construcción: una sola función pura, dos consumidores, paridad garantizada.

### 6. Renombre conceptual

El sustantivo "kill-switch v2" deja de ser preciso. Lo que el sistema tiene es un **gating layer con N predicados independientes** (donde N = 3 o 4 según el experimento decida). El renombre no es prioridad alta — puede hacerse en la PR de extracción o diferirse.

## Lo que la sesión NO resolvió y depende del experimento

### A. Qué filtros forman parte del agregador final

Cuatro filtros candidatos:

1. **Salud mensual por símbolo** (v1, `health.py`)
2. **Portfolio DD live** (v2)
3. **Velocity** (v2)
4. **Per-symbol baseline-σ** (v2)

El espacio de combinaciones es 2⁴ = 16 (cada filtro on/off). El experimento debe identificar qué subset opera mejor.

### B. Si el filtro mensual de v1 queda cubierto por baseline-σ

Si el experimento muestra que activar baseline-σ pero desactivar mensual no degrada métricas, entonces v1 mensual se puede deprecar limpiamente. Si activar ambos es mejor, v1 mensual se queda como filtro adicional independiente.

### C. Cómo evitar data dredging

Comparar 16 combinaciones sobre el mismo backtest y elegir la ganadora es overfitting al dataset. Estrategia esperada:

- Backtest sobre **train slice** (no holdout) para el grid search.
- Holdout solo para validar la combinación ganadora — una vez, irrevocable (Non-Negotiable #3, bala única).
- Pre-registro: la métrica de éxito y el criterio de selección se escriben antes de correr nada.

### D. Diseño completo del experimento

Métrica de éxito, dataset de train, baseline limpio, anti-overfitting, criterio de "ganadora", criterios de stopping. Todo eso pertenece a la sesión post-#272.

## Camino crítico

```
#272 (re-baselining honesto)               ← prerrequisito desbloqueante
    ↓
Diseño del experimento                     ← brainstorming separado, post-#272
    ↓
Construir la plataforma de experimentación ← extraer simulator → strategy/gating.py
    ↓
Correr el experimento sobre backtest train ← NO holdout
    ↓
Validar combinación ganadora con holdout   ← UNA vez, tras #322 cerrado
    ↓
Flip shadow→active de la combinación       ← #397 ya cerrado en este punto
```

#397 (bias del DD en `emit_shadow_decision`) sigue siendo precondición del flip y puede avanzar en paralelo a #272.

## Restricciones del proyecto que el agregador debe respetar

1. **Non-Negotiable #3:** el holdout solo se toca para validar la combinación final, una vez, tras cierre de #322. Cualquier código que llame `simulate_strategy` sobre holdout-window durante el experimento es violación.
2. **Non-Negotiable #5:** los números pre-#223/#224 son inflados. El experimento no puede usar esos números como baseline ni para comparar.
3. **Non-Negotiable #2:** el holdout dataset solo se lee vía `open_holdout(rel_path, evaluation_mode=True)`. Aplica también al código del experimento si llega a tocar holdout en la fase de validación final.

## Reframes que sostuvieron el diseño

- **Voronov (meta-arquitecto):** "Lo que hay no es v1 vs v2 — es un gating layer con N predicados independientes, y cada uno necesita su propia política tier→action."
- **Axiom-0 (unificador):** "Donde no hay agregador, no hay decisión — sólo evidencia esperando ser nombrada. El hardcoded `size_factor=1.0, skip=False` es el silencio estructural del agregador faltante."
- **Plumb-Lindgren (calibrador):** verdict L, modo brainstorming + spec; 7 decisiones arquitectónicas iniciales, reducidas a 4 tras destapar que el simulator ya implementaba B1 con producto multiplicativo.
- **Samuel (operador):** "Podríamos hacer un test de las múltiples combinaciones y ver cuál nos da mejor resultado y desde allí tomar alguna decisión basada en los datos — es la única razón por la que esto todavía no está operativo."

## Decisiones pendientes al reabrir

Cuando #272 cierre, este brainstorming retoma sobre estos puntos:

1. **Diseño del experimento:** métrica, train slice, anti-overfitting, criterio de "ganadora", pre-registro.
2. **Combinaciones a evaluar:** ¿2⁴ = 16, o un subset razonado?
3. **Naming del toggle:** una vez decidida la combinación, qué config key gobierna el modo (`kill_switch.gating.enabled`, `kill_switch.mode = shadow|active|v1_only`, etc.).
4. **Plan de rollback:** cómo revertir sin redeploy si la combinación promovida falla en producción.
5. **Migración del decision log:** ¿flag de versión en `reasons_json` para distinguir rows pre/post flip, o nueva tabla?
6. **Renombre de "kill-switch v2":** se hace en la PR de extracción o se difiere.

## Cómo no perder este contexto

- Este doc está committeado en la branch `docs/kill-switch-aggregator-design-paused`.
- PR draft abierto contra `main` con el mismo título, marcado claramente como PAUSADO.
- Cuando #272 cierre, el siguiente paso es retomar la PR y continuar el brainstorming sobre las decisiones pendientes listadas arriba.
- Halcyon (operador) puede recalibrar el orden del board en general, dado que esta cadena afecta la prioridad de #397, #322, #250, y la promoción del kill-switch v2.
