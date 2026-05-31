# Stress-replay v1 vs v2 (kill-switch) — diseño

- **Fecha:** 2026-05-31
- **Autor:** Samuel + Claude (Opus 4.8)
- **Estado:** diseño aprobado, pendiente de plan de implementación
- **Issue relacionado:** epic kill-switch v2 (#187); promoción shadow→active
- **Tipo:** metodología de validación (no es un bugfix)

## 0. Por qué este documento existe

La promoción del kill-switch v2 de shadow a active necesita evidencia de que v2 es
**mejor** que v1 — no solo distinto. El diagnóstico del 2026-05-31 estableció dos hechos
que descartan los caminos obvios:

1. **El shadow en vivo no puede probar superioridad.** En 32 días de producción
   (2026-04-29 → 2026-05-31) ambos motores decidieron `NORMAL` el 100% del tiempo.
   El peor DD de portafolio fue **−0.49%**; el primer umbral de acción (REDUCED) está
   en ~−5.5%. El portafolio nunca se acercó ni a un orden de magnitud del umbral. Un
   circuit breaker es indistinguible de otro durante la calma: ninguno tuvo nada a qué
   reaccionar. Acumular más shadow solo discrimina cuando ocurra un drawdown real de
   ~5%+ — exactamente la catástrofe que no se quiere enfrentar con un breaker sin validar.
2. **El holdout (OOS limpio) está bloqueado.** La ventana de validación OOS solapa el
   holdout locked (`2025-04-30 → 2026-04-30`), bloqueado tras #322 (No-Negociable #3).
   No se puede ejecutar sin quemar la bala única.

El estrés vive en la historia pre-holdout (oso de 2022: LUNA mayo'22, FTX nov'22). Es
legal (no es holdout) y existe infraestructura de backtest reusable. Este documento
diseña el experimento que replica esas crisis y mide v1 vs v2 sobre ellas.

El shadow en vivo **ya aportó media promoción**: probó que v2 no dispara en falso en
operación normal (32 días, cero skips espurios). Lo que falta es la otra mitad — el
comportamiento bajo estrés — y eso solo se obtiene replicando crisis.

## 1. Objetivo y veredicto

Producir evidencia defendible de si el kill-switch **v2 domina a v1** sobre crisis
históricas reales, para justificar (o frenar) la promoción shadow→active.

El entregable es una **frontera DD/P&L**: v1 es un punto único (umbrales fijos); v2 es
una curva parametrizada por el slider de agresividad. El veredicto tiene tres niveles:

| Nivel | Condición |
|---|---|
| **STRONG** | Algún slider de v2 Pareto-domina v1: max DD de portafolio **menor** Y P&L total **≥** el de v1. |
| **PASS (DD-first)** | Algún slider de v2 reduce el max DD de portafolio en **≥3 pp absolutos O ≥15% relativo** (basta con que se cumpla una de las dos) **y** conserva **≥90% del P&L de v1**. |
| **FAIL** | Ningún slider de v2 cumple el gate DD-first. |

Los umbrales del gate (3 pp / 15% / 90%) quedan **pre-registrados** aquí y no se mueven
después de ver resultados. Mover el poste post-hoc invalida la evidencia.

Criterio primario: **DD-first con piso de P&L** (decisión del 2026-05-31). La filosofía
de breaker es que el objetivo es protección de cola; se acepta algo de costo de P&L por
ella, pero acotado por el piso.

## 2. Arquitectura — Approach C (dos pasadas)

El reto de fidelidad: la feature estrella de v2 es el **circuit breaker de portafolio**,
que se dispara con el DD **agregado cross-símbolo**. Pero `simulate_strategy` corre un
símbolo a la vez. Para que el breaker vea el DD real (que LUNA + FTX hundan el portafolio
agregado) los trades de los 10 símbolos deben evaluarse interleaveados en orden
cronológico, compartiendo una sola curva de equity.

Se eligió el enfoque híbrido de dos pasadas (C) sobre el motor fiel completo (B) y la
aproximación per-símbolo (A): captura el DD de portafolio cross-símbolo (lo que importa)
a una fracción del costo de B. Si el veredicto sale ajustado, se escala a B para la
confirmación final.

### Pasada 1 — Base stream (por símbolo, reusa `simulate_strategy`)

Para cada uno de los 10 símbolos curados:

- `load OHLCV ≤ 2025-04-29` (cutoff holdout-safe).
- `simulate_strategy(apply_kill_switch=False, ...)` con la **config de producción**
  (`symbol_overrides` + gates activos — el path live-equivalente).
- Capturar la lista de trades: `{symbol, entry_ts, exit_ts, pnl_usd, size_usd, exit_reason}`.
- Marcar **flag de bancarrota** si la equity standalone del símbolo toca ≤0.

El stream base se genera **una vez** y se reusa para todos los engines/sliders.

### Pasada 2 — Overlay replay (cronológico, portfolio-aware) — pieza nueva

- Merge de los 10 streams en un único stream ordenado por timestamp de entrada.
- Para cada `engine ∈ {none, v1, v2@30, v2@50, v2@70}`:
  - Equity de portafolio compartida; instancia del simulator del engine.
  - Por cada entrada de trade en orden cronológico:
    - `(skip, size_factor) = overlay.should_skip_or_reduce(symbol, entry_ts)`
    - Realizar `pnl_usd * size_factor` al `exit_ts` (escalado del tamaño).
    - `overlay.on_trade_close(symbol, exit_ts, pnl_escalado, exit_reason)`.
    - Actualizar equity / peak / DD de portafolio.
  - Output: curva de equity de portafolio + log de trades + conteo de engagements del
    breaker (transiciones a REDUCED/FROZEN/ALERT/velocity).

**Interfaz común:** `V2KillSwitchSimulator` ya expone `should_skip_or_reduce(symbol,
entry_ts) -> (skip, size_factor)` y `on_trade_close(...)`. Para v1 se envuelve
`backtest_kill_switch.KillSwitchSimulator` en un adapter delgado si su firma difiere.
El engine `none` es la identidad (`size_factor=1.0` siempre) — la referencia sin protección.

**Aproximación documentada:** la Pasada 2 como post-proceso aproxima la retroalimentación
de ocupación de posición — si v2 salta un trade, en la realidad ese símbolo queda libre y
una señal posterior suprimida podría disparar. La Pasada 2 no regenera señales, así que
captura fielmente el **DD de portafolio** pero aproxima **qué señales re-disparan tras un
skip**. Si el veredicto es ajustado, esta es la primera fuente de error a eliminar
escalando a B.

### Pasada 3 — Métricas + gate + reporte

Computa las métricas (§3), aplica el gate (§1) y emite las salidas (§5).

## 3. Métrica y nivel de agregación

Todo a **nivel portafolio**: las 10 equities se funden en una sola curva, porque el
breaker de v2 actúa sobre el DD agregado. Por cada `(engine, slider)` se reporta:

- **Max DD de portafolio** (métrica primaria del gate).
- **P&L total** (relativo a v1 para el piso Y).
- **# trades tomados** (vs disponibles en el base stream).
- **# engagements del breaker** (transiciones a REDUCED/FROZEN, ALERT per-símbolo,
  cooldowns de velocity).
- **Desglose por símbolo** (contribución a P&L y a DD).

El piso de P&L (Y = 10%) se mide **relativo a v1** — es el sistema que se promovería por
encima. El engine `none` sirve de referencia para cuantificar cuánto DD ahorra y cuánto
P&L cuesta cada breaker.

## 4. Guards no-negociables

- **Holdout (No-Negociable #3):** `assert sim_end ≤ 2025-04-29` duro en la Pasada 1; cero
  llamadas a `open_holdout`; cero frames de la ventana holdout. Si algún frame excede el
  cutoff → abort inmediato del experimento.
- **Bankruptcy-bias (caveat #2 de `.mex/context/decisions.md`, fix #313/#280):** un
  símbolo cuya equity standalone toca ≤0 se marca; en la agregación de portafolio su
  contribución se capa y se reporta aparte. No se deja que el artefacto de trades de
  riesgo-cero post-bancarrota sature el DD de portafolio.
- **Números inflados (No-Negociable #5):** el reporte declara explícitamente que los
  absolutos pre-#223/#224 no son baseline. La **única** conclusión es la comparación
  **relativa** v1-vs-v2-vs-none sobre el mismo base stream; la inflación afecta a los tres
  overlays por igual y se cancela en el delta.
- **Read-only:** todo el experimento es read-only sobre OHLCV; nada toca la DB de
  producción ni el estado de signals.db.

## 5. Salidas

Directorio `data/retune/2026-05-31-ks-stress-replay/`:

- `report.md` — frontera DD/P&L, tabla por `(engine, slider)`, veredicto (STRONG/PASS/FAIL),
  desglose por símbolo, lista de crisis donde el breaker se activó, caveats.
- `results.json` — datos crudos por engine/slider (curvas de equity, métricas, conteos)
  para re-análisis.
- `derivation_audit.md` — config usada, cutoff, SHA-256 de `ohlcv.db`, commit de código,
  grid de slider, umbrales del gate pre-registrados, flags de bancarrota, decisiones
  metodológicas.

## 6. Parámetros pre-registrados

| Parámetro | Valor |
|---|---|
| Símbolos | 10 curados (BTC, ETH, ADA, AVAX, DOGE, UNI, XLM, PENDLE, JUP, RUNE) |
| Ventana | `[2021-01-01, 2025-04-29]` (pre-holdout completa) |
| Engines | `none`, `v1`, `v2@30`, `v2@50`, `v2@70` |
| Grid de slider v2 | 30 / 50 / 70 |
| Config base | producción (`symbol_overrides` + gates activos) |
| Gate DD-first | max DD de v2 ≥3 pp absolutos O ≥15% relativo menor que v1 |
| Piso de P&L | P&L de v2 ≥ 90% del P&L de v1 |
| Gate STRONG | algún slider v2 Pareto-domina v1 (DD menor Y P&L ≥) |
| Capital base | el del backtest estándar (consistente con `simulate_strategy`) |

## 7. Fuera de alcance (YAGNI)

- El motor fiel completo (Approach B) — solo si C deja el veredicto ajustado.
- La corrida contra el holdout — bala única, reservada para confirmación final post-#322.
- Calibración del slider óptimo (`run_optimization_v2`) — el barrido fijo 30/50/70 evita
  el sesgo in-sample de optimizar y validar sobre la misma data.
- Cualquier cambio al código del kill-switch en sí — este experimento solo lo mide.
- Desglose por sub-ventana de crisis específica — el max DD agregado sobre la ventana
  completa ya captura las crisis; se puede añadir después si se necesita granularidad.

## 8. Criterio de éxito del experimento

El experimento es exitoso si produce un veredicto **claro y pre-registrado** (STRONG /
PASS / FAIL) con la frontera DD/P&L y el desglose que lo sustenta — independientemente de
si v2 gana o pierde. Un FAIL honesto que frene la promoción es un resultado tan válido
como un STRONG que la habilite. El valor está en la decisión informada, no en un
resultado particular.
