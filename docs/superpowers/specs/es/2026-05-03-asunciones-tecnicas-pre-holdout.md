# Asunciones técnicas pre-holdout — Decisión 9 del plan estratégico

**Fecha:** 2026-05-03
**Status:** DRAFT — pre-registered commits para el holdout, pending operator approval before circulation
**Autor:** Reviewer agent (Claude Opus 4.7), drafted en colaboración con sssamuelll y senior reviewer feedback
**Cumple:** Decisión 9 del [strategic pivot plan](../plans/2026-05-01-a4-strategic-pivot-plan.md)
**Bloqueante de:** evaluación contra holdout (A.4 v2 final test del epic #294)

---

## 1 · Propósito y alcance

Este doc cumple **Decisión 9** del plan estratégico del 1 de mayo: documentar explícitamente, **antes del holdout**, las asunciones técnicas heredadas que afectan el N efectivo del Deflated Sharpe Ratio (DSR) que el Gate 3 (#249) va a usar como threshold.

**El propósito no es solo documentar — es pre-registrar.** Cualquier ajuste post-holdout sobre estas asunciones (e.g., recalcular N para hacer el threshold más alcanzable después de ver resultados) **disqualifies el resultado como validación primaria**. Este doc fija los commits ex-ante; lo que pase después se evalúa contra estos commits, no contra los que sería conveniente tener.

**Alcance:**
- Inventory de las 9+ asunciones técnicas que el sistema preserva, cambia, o reemplaza pre-holdout.
- Cálculo propuesto del N efectivo del DSR.
- Lista explícita de commits ex-ante (los valores que NO se mueven post-holdout).
- Interpretación pre-registered de scenarios de éxito y fracaso del holdout.

**Out of scope:**
- Implementación del threshold del Gate 3 — eso es trabajo de #249.
- Re-evaluación del basket (A9 del plan estratégico) — eso es post-holdout, no pre-.

---

## 2 · Inventory de asunciones técnicas

Cada asunción documentada con: estado actual, decisión pre-holdout, justificación.

### 2.1 Cooldown post-trade

**Estado actual (post-PR1+PR2, pre-PR3):** global `COOLDOWN_H = 6` (`btc_scanner.py:141`), enforced en backtest (`backtest.py:654-657`) y en `strategies/trend_following_sim.py:218`. Live scanner lo flaguea como `VERIFICAR_MANUAL` per operational model spec (`docs/superpowers/specs/es/2026-05-01-operational-model-manual-gating.md`) — operador aprueba manualmente.

**Decisión pre-holdout:**
- **PR3 (cooldown parity)** va a introducir per-symbol `cooldown_hours` con valores derivados del parameter study (research §3, master table §5):
  - BTC, ETH: 14h (matches time-limit, deterministic rule `max(TL, NW=4, floor=6)`)
  - ADA, PENDLE: 6h (TL=5h, floor 6 binds — basket default)
  - AVAX: 8h (TL=8 binds)
  - DOGE, UNI, JUP, RUNE, XLM: 6h (basket default, floor binds)
- Scanner enforce automáticamente, eliminando el manual-check de E5_Cooldown.

**Justificación:**
- 6h floor preserva legacy backtest parity (los 6 valores de "basket default" no se mueven del status quo).
- 14h BTC/ETH es el único valor que cambia — derivado de la rule deterministic, no tuneado. El research mostró que cooldown < TL crea pathology (re-entry antes de que el trade previo cierre por TL).
- Rule `max(TL, NW=4, floor=6)` es deterministic en (TL, NW, floor) — 0 grados de libertad nuevos más allá del time-limit grid de PR1.

**N effective contribution:** 0 nuevos. Los valores son rule-derived desde TL (que ya cuenta en el N del time-limit grid).

### 2.2 BE-move asimetría LONG/SHORT

**Estado actual:** simétrico en código (`backtest.py:564-573` mirror logic explícito LONG/SHORT — trailing ratchet at SL/TP check site). Verificado en sesión 2026-05-01 al cerrar issue #284 (issue claim de "BE-move only LONG" era falsa).

**Decisión pre-holdout:** **preservar simetría.** No se modifica.

**Justificación:**
- El código actual es correcto. No hay drift documentado vs la spec original (Spot V6).
- Cambiar simetría sin causa específica introduce un grado de libertad innecesario.
- Si el holdout revela problema con BE-move (e.g., asimetría real entre dynamics LONG vs SHORT), eso es información para iteración post-holdout, no decisión a tomar pre-.

**N effective contribution:** 0. Sin cambio.

### 2.3 R-multiple sizing

**Estado actual:** fixed 1% risk per trade (`RISK_PER_TRADE = 0.01` en `backtest.py:79`), score-multiplied (`size_mult` ∈ {0.5, 1.0, 1.5} per score tier), kill-switch reduces (factor 0.5 si REDUCED), capital-floor at 0 (post-#277).

**Decisión pre-holdout:** **preservar.** El cap de PR2 (`max_participation_rate`) es ortogonal — es un constraint downstream que rejecta el R-multiple output cuando excede liquidez, NO modifica el R-multiple itself.

**Justificación:**
- Risk per trade fijo 1% es policy del operador (CLAUDE.md explícito: "Do not add multiplicative risk scalers on top").
- Score tiers (0.5/1.0/1.5) son legacy del epic #121 — preserved.
- El cap NO altera la fórmula de sizing — solo agrega un guard "if size > X: skip".

**N effective contribution:** 0. Sin cambio al sizing primario.

### 2.4 Time-limits per-symbol (PR1, ya en main vía #296)

**Estado actual:** per-symbol `time_limit_hours` en `config.json["symbol_overrides"]`:

| Symbol | TL (hours) |
|---|---|
| BTC, ETH | 14 |
| ADA, PENDLE | 5 |
| AVAX | 8 |
| DOGE, UNI, JUP, RUNE, XLM | 5 |

Implementación: bar-by-bar check en backtest + `check_position_stops` en live scanner.

**Decisión pre-holdout:** **preservar valores actuales.** No re-tunear post-PR1.

**Justificación:**
- Valores derivados de research §5, anchored en:
  - BTC/ETH: winners-median holding 14h del diagnóstico §6
  - ADA/PENDLE/B-like sólido: peak predictivo h=+5 del diagnóstico §2
  - AVAX: compromiso por TL uncertainty band 5-8h
  - Mundo C basket: AFML floor (peak predictive horizon)
- Tier framework del research clasificó cada valor con confidence: 1 high · 3 medium · 1 medium-low · 1 low-medium · 4 low.
- **El grid de time-limit fue el único grado de libertad nuevo que la R1 cota del 1 de mayo permitía.** Pre-registered.

**N effective contribution:** **N=1 trial** (el grid de time-limit como bloque protocol-derived).

### 2.5 Caps per-symbol (PR2, en flight via #297)

**Estado actual:** per-symbol `max_participation_rate` en `config.json["symbol_overrides"]`:

| Symbol | Cap |
|---|---|
| BTC, ETH | 0.010 |
| JUP, DOGE, AVAX | 0.005 |
| RUNE, ADA | 0.003 |
| UNI | 0.002 |
| XLM, PENDLE | 0.0015 |

**Decisión pre-holdout:** **preservar valores actuales.** No re-tunear post-PR2.

**Justificación + tratamiento como priors protocol-derived (sección load-bearing — flag para review extra):**

Los 9 caps no fueron tuneados sobre smoke results. Fueron derivados ex-ante de un protocolo único (research §5 master table) con anchors externos:

- **Tier mapping** (major/mid/mid-thin/small/floor) derivado de:
  - Almgren-Chriss (2001) "Optimal Execution" — recomendación 1% daily POV equities institucional
  - Bouchaud (2018) "Trades, Quotes, Prices" — sqrt-impact validez 0.5-20% daily POV
  - Donier-Bonart (2014) BTC sqrt-impact validation
  - Kaiko liquidity research (top-50 altcoin depth)
  - Binance LiquidityBoost program tier thresholds
- **Translation note crítica del research:** caps sobre 1H bar volume son materially lower que daily-POV equivalents (1H ≈ 1/24 of daily).
- **Per-symbol assignment** se hizo por tier matching, no por per-symbol grid search:
  - BTC/ETH (major, deep liq): 1.0% bar POV
  - JUP/DOGE/AVAX (mid Tier-1): 0.5%
  - RUNE/ADA (mid-thin Tier-2): 0.3%
  - UNI (small): 0.2%
  - XLM/PENDLE (floor cap): 0.15%
- **Evidencia de no-tuning post-smoke:** los smoke results de PR2 (BTC/ETH 100% retención, PENDLE 4%, etc.) NO motivaron ajustes a los valores. La distribución observed se reportó honestly como "the structural fix funcionando as designed", no como motivation para cambiar caps.

**N effective contribution:** **N=1 trial** — los 9 valores numéricos del cap por tier cuentan como un único protocol-derived block (los anchors externos Almgren-Chriss / Bouchaud / Donier-Bonart / Kaiko / Binance LiquidityBoost).

**Decomposición explícita (revisión 2026-05-03 post external reviewer feedback):** los caps tienen DOS componentes que merecen counting separado:

- **(a) Cap values per tier** (1.0% / 0.5% / 0.3% / 0.2% / 0.15%): protocol-derived genuinely externo. Si el basket fuera otro, estos valores no cambiarían (vienen de literatura). Cuenta aquí en §2.5: **N=1**.
- **(b) Asignación moneda → tier** (BTC/ETH=major, JUP/DOGE/AVAX=mid Tier-1, etc.): data-derived del basket — anchored en `cost_bps_mean` per-symbol del train segment. Si el basket fuera otro, las asignaciones serían diferentes. Cuenta separadamente en §2.8: **N=1 adicional**.

Esta separación responde a la observación del external reviewer (3 mayo) que el bloque "protocol-derived" anteriormente colapsado contenía un componente data-derived (la asignación). El total caps section = 2 (1 cap-values + 1 tier-mapping). Strict alternative N=10 (cada cap como trial independiente) sigue rechazada.

**Justificación de N=1 para cap-values (NO N=9):**
- Los valores vienen de un único protocolo de research, ejecutado y documentado pre-implementation.
- Si fueran 9 trials independientes, requirirían search space declarado (e.g., "para BTC, búsqueda en {0.005, 0.010, 0.015}; para ETH, búsqueda en {...}; etc."). No hay tal search space — hay 1 protocol output con 5 cells (un value por tier, no por symbol).
- Per-symbol assignment es vía §2.8 tier mapping, no per-symbol search.

**Riesgo si N estuviera mal estimado:**
- Si N real total es 10 (cada cap independiente + tier mapping) y usamos N=3: threshold del Gate 3 será demasiado bajo. Holdout puede parecer pasar más fácil de lo legítimo.
- Si N real es 2 (cap-values + tier-mapping verdadero protocol-derived) y usamos N=3: threshold demasiado alto. Holdout puede fallar legítimo siendo válido.
- N=3 PRIMARY es el balance honest entre estos riesgos, surfaced por external reviewer.

**Pre-registration commitment (revisado 2026-05-03):** este spec fija **N=2 para la sección de caps** (1 cap-values en §2.5 + 1 tier-mapping en §2.8). Total con TL grid = 3. Si post-holdout alguien quiere argumentar N=10 (resultado desfavorable), eso es ajuste post-hoc y disqualifies el resultado como validación primaria. Si pre-holdout alguien quiere argumentar N=2 estricto (collapsing tier-mapping back into cap-values protocol), require reviewer override antes del threshold lock.

### 2.6 Regime detector

**Estado actual:** `detect_regime()` (composite F&G + funding rate + price), once daily, cached en `data/regime_cache.json`. Scores >60 = BULL/LONG, <40 = BEAR/SHORT-enabled, 40-60 = NEUTRAL/LONG-only. Per CLAUDE.md.

**Decisión pre-holdout:** **preservar.** No se modifica.

**Justificación:**
- Composite + thresholds son legacy validados sobre el train segment.
- Cambiar pre-holdout introduce DOFs no controlados.
- Si holdout revela que regime detector está mal calibrated, es información para iteración post-holdout.

**N effective contribution:** 0.

### 2.7 Score → size mapping (tier multipliers)

**Estado actual:** premium tier (score ≥4) = 1.5x, standard tier (score 2-3) = 1.0x, low tier (score 0-1) = 0.5x.

**Decisión pre-holdout:** **preservar.**

**Justificación:** legacy del epic original. Cambiar mid-stream invalidates train baseline.

**N effective contribution:** 0.

### 2.8 Tier mapping (cost-based clustering) — data-derived, separate N count

**Estado actual:** documented en research §2.3 — major/mid Tier-1/mid Tier-2/small/floor, mapped por `cost_bps_mean` proxy. Usado para asignar `max_participation_rate` per-symbol.

**Decisión pre-holdout:** **preservar.** El mapping es derived from data del diagnóstico, fijo en research §5.

**Justificación + N effective re-classification (2026-05-03 post external reviewer feedback):**

Anteriormente esta sección listaba `N=0 adicional (incluido en N=1 de caps)`. La observación del external reviewer (3 mayo) corrigió ese counting: el tier mapping ES un grado de libertad real, separable de los cap-values.

- **Cap values** (§2.5): genuinely external, anchored en literatura institucional. Independent del basket actual. **N=1 (protocol-derived).**
- **Tier mapping** (esta sección): anchored en `cost_bps_mean` spectrum del basket actual. Si el basket fuera otro (otros 10 símbolos), las asignaciones de cada moneda a tier serían distintas. **N=1 (data-derived).**

Es la asignación lo que es fitting a la data, no los valores numéricos.

**N effective contribution:** **N=1** (data-derived block, separable from cap-values).

### 2.9 Per-symbol ATR multipliers (atr_sl_mult, atr_tp_mult, atr_be_mult)

**Estado actual:** valores actuales en `config.json["symbol_overrides"]`:

| Symbol | atr_sl | atr_tp | atr_be |
|---|---|---|---|
| BTC | 1.0 | 4.0 | 1.5 |
| ETH | 1.2 | 4.0 | 1.5 |
| ADA | 0.5 | 4.0 | 1.5 |
| AVAX | 1.5 | 4.0 | 1.5 |
| DOGE | 0.7 | 4.0 | 1.5 |
| UNI | 1.0 | 3.0 | 1.5 |
| XLM | 0.5 | 4.0 | 1.5 |
| PENDLE | 0.5 | 3.0 | 2.0 |
| JUP | 0.5 | 4.0 | 2.5 |
| RUNE | 0.7 | 6.0 | 2.5 |

**Decisión pre-holdout (revisada 5 mayo 2026):** **re-tune mecánico requerido** sobre ventana `[earliest, 2025-04-30T00:00:00Z)` antes de la evaluación holdout. Implementación: A.4-1 retune harness (#287, Phase 3).

**Justificación del refinamiento de scope respecto a la pre-registration original:**
- Los valores actuales fueron tuneados sobre full history que **incluye** el rango holdout [2025-04-30 → 2026-04-30]. Esto es leakage: el holdout no está fuera-de-muestra para esos parámetros. CLAUDE.md "Caveats heredados — A.4 (#250) MUST honor" caveat #1 reconoce el leakage explícitamente.
- El re-tune es **mecánico**, no introduce DOFs nuevos: grid + objective function están locked-at-Phase-2 (`auto_tune.py`); el harness solo restringe el dataset de entrada al pre-holdout window y re-corre el grid existente. No hay decisión humana en los 30 valores resultantes.
- El §2.9 original era **over-broad**: pretendía prohibir iteración sobre grid/objective (que introduce DOFs) pero su lenguaje cubría también el caso adyacente de re-correr grid locked sobre dataset restringido (no introduce DOFs — output mecánico determinístico). Combinado con el caveat de leakage en CLAUDE.md, refinamos el scope al intent original: prohibir iteración meta-paramétrica, permitir re-correr el protocolo determinístico sobre el dataset legítimo.

**Window de re-tune:** `[earliest, 2025-04-30T00:00:00Z)`. Cutoff = holdout_start, strict `<` slicing. Train/Validate derivados cutoff-relative via `auto_tune.calculate_periods` (`[cutoff − 15mo, cutoff − 3mo]` Train + `[cutoff − 3mo, cutoff)` Validate; ratios hardcoded en `auto_tune.py:86-89`).

**Justificación de la window choice:**
- Maximiza trade count en Train (variance estadística menor en grid search)
- Evita sub-elección de período dentro del rango pre-holdout (que sería un DOF adicional al N=0)
- Alternativas consideradas:
  - (a) Ventana fija 12-24mo pre-cutoff — rechazada por reducir N de trades sin justificación cuantitativa de cambio de régimen materially significativo dentro del rango pre-holdout
  - (b) Multi-window stability check — out-of-scope para A.4-1, candidato para A.4 walk-forward harness (post-holdout)

**Gate bias acknowledgment:**

El acceptance gate (`auto_tune.should_recommend`, líneas 105-124) compara baseline_val_pnl (current params evaluados sobre Validate window que estaba en el dataset original de tuning) contra candidate_val_pnl (Train→Validate split, candidate fitted en Train). El primero es quasi-in-sample para current; el segundo es genuine OOS para candidate. La comparación con 15% improvement floor está estructuralmente sesgada hacia KEEP.

Mantenemos el gate sin modificación (preserva N=0). **Específicamente: las cuatro condiciones de `auto_tune.should_recommend` (val_pf ≥ 1.1, total_trades ≥ 50, current_pnl ≤ 0 → proposed_pnl > 0, current_pnl > 0 → improvement_pct ≥ 15%) permanecen como locked-at-Phase-2.**

Aceptamos que el artifact resultante puede contener KEEPs con valores leakeados que un gate justo habría declarado CHANGE. Pre-registramos thresholds procedurales sobre la distribución de KEEPs/CHANGEs para limitar el blast radius:

- **J_primary = 3:** si ≥3 de 4 PRIMARY (PENDLE, ADA, BTC, ETH) salen KEEP → pause de 72h antes de Phase 4. Razón: PRIMARY es lo que cuenta para Gate 3; si tres de cuatro no se movieron, el holdout testea mayormente leakage.
- **J_total = 6:** si ≥6 de 10 totales salen KEEP → pause de 72h antes de Phase 4. Razón: re-tune mayoritariamente cosmético; mezcla del artifact dominada por leakage residual.

**Magnitude gate (no situational awareness):**

Para cada symbol que sale CHANGE, comparar magnitudes de los 3 atr_* multipliers. Si para CUALQUIER mult (sl_mult, tp_mult, be_mult): `max(new/current, current/new) ≥ 2.0`, el symbol cuenta hacia threshold K.

- **K = 3:** si ≥3 de 10 symbols disparan magnitude flag → pause de 72h antes de Phase 4 + audit explícito antes de Phase 5. Razón: deltas grandes pueden indicar (a) leakage substancial siendo corregido (esperado, OK), o (b) bug en harness/dataset (blocker). Discriminar entre los dos casos requiere inspección humana, no inferencia.

Métrica simétrica `max(new/current, current/new) ≥ 2.0` captura tanto upsides (new = 2× current) como downsides (current = 2× new) materiales del mismo orden.

**Pause action menu (post-trigger, J_primary | J_total | K):**

La elección post-pause es post-hoc dada la distribución del output; el trigger es ex-ante. Opciones registradas para discusión reviewer + analyst:

- **(iv) Halt y debug:** si la distribución observada es inconsistente con expectativas de primer orden (e.g., ≥3/4 PRIMARY KEEP cuando el caveat #1 declaraba leakage substancial; o magnitudes K-flagged en >half del basket), pausar para inspección de harness + dataset ANTES de cualquier opción (i)-(iii). El sesgo del gate predice algunos KEEPs adicionales, no necesariamente todos. Magnitude flag puede indicar bug en lugar de leakage correction.
- (i) Drop floor sobre símbolos KEEP-flagged y re-correr declarando DOF (rompe N=0; pre-registrar antes de re-run).
- (ii) Reportar holdout sobre subset CHANGE-only en paralelo (preserves N=0; doble lectura).
- (iii) Accept con caveat ampliado en interpretación del holdout (preserves N=0; loss of crispness en Gate 3).

**Pause action menu para magnitude gate específicamente:** audit verifica (a) harness/dataset bug → halt y fix; (b) leakage correction substancial → continue con caveat ampliado en interpretación holdout; (c) inestabilidad de óptimos sobre dataset → flag para A.4 walk-forward harness post-holdout (no blocker para A.4-1 actual).

**N effective contribution:** 0. Los thresholds procedurales (J_primary, J_total, K) son pre-registered triggers sobre la distribución del output, no DOFs sobre el grid/objective/gate del re-tune. La elección post-trigger sí es DOF si ocurre — y debe pre-registrarse antes de cualquier re-run.

**Caveat heredado:** los valores actuales del table de arriba fueron tuneados pre-PR1/PR2 sobre data leakeada. Post-retune, esos valores son históricos — el snapshot pre-tune queda en commit history. Los valores re-tuneados quedan en `data/retune/2026-05-04-pre-holdout/params.json` y se promueven a `config.json` en Phase 5 separate PR.

---

## 3 · N efectivo del DSR — propuesta pre-registered

| Asunción | Estado | N effective contribution |
|---|---|---|
| 2.1 Cooldown | rule-derived from TL | 0 |
| 2.2 BE-move asimetría | preserve (symmetric) | 0 |
| 2.3 R-multiple sizing | preserve | 0 |
| 2.4 Time-limits per-symbol (PR1) | grid protocol-derived | **1** |
| 2.5 Caps per-symbol (PR2) — cap values | priors protocol-derived (external anchors) | **1** |
| 2.6 Regime detector | preserve | 0 |
| 2.7 Score → size mapping | preserve | 0 |
| 2.8 Tier mapping (cost-based) | data-derived (basket cost spectrum) | **1** |
| 2.9 ATR multipliers per-symbol | re-tune mecánico (grid locked, dataset restringido) | 0 |

**N effective propuesto: 3 (PRIMARY).**

**Cambio histórico:** este spec inicialmente proponía N=2 (con tier mapping colapsado en §2.5). External reviewer (3 mayo) surfaced que el tier mapping es data-derived (anchored en basket cost_bps spectrum) y por tanto merece counting separado del cap-values protocol-derived. Elevation a N=3 PRIMARY refleja esa observación. Ver §2.5 + §2.8 para detalle decomposition.

**Optimistic alternative:** N=2 si se argumenta que tier mapping debe colapsarse en cap-values protocol (treating la asignación moneda→tier como output puro del protocol, no como data fitting). **Discouraged** — el reviewer externo argumentó convincingly que la asignación SÍ es data-aware (el basket actual fija las asignaciones).

**Strict alternative:** N=10 si se trata cada cap como trial independiente (1 TL + 9 caps independent). **Disagree** — los cap-values NO son 9 search choices independientes; son 5 tier values (un value por tier) con asignación per-symbol determinada por §2.8.

**Recomendación operacional:** lock N=3 ex-ante. Esto es el commitment pre-holdout. Cambiar post-holdout es disqualifying.

**Implicación para Gate 3 threshold (#249):**
- Per Lopez de Prado DSR formula: `DSR = (SR_observed - E[max SR | N trials]) / σ_SR`
- E[max SR | N=3] vs E[max SR | N=10] difiere significantly. N=3 produce threshold materially lower que N=10 (rough calibration; #249 va a calcular exact con sus formulas).
- N=3 vs N=2: el incremento marginal del threshold (~0.05-0.10 rough) es honest cost de reconocer el data-awareness del tier mapping. Threshold ligeramente más alto = harder de pasar = mayor confianza si pasa.
- Threshold lower = más fácil de pasar legitimately. Que N=3 sea el correcto count requiere defensibilidad ex-ante (este spec).

---

## 4 · Commits ex-ante para el holdout

Lista explícita de valores que NO se mueven post-holdout. Cualquier ajuste a estos post-holdout disqualifies el resultado como validación primaria.

### 4.1 Locked values (no movement post-holdout)

**Time-limits (per §2.4):**
- BTC=14, ETH=14, ADA=5, PENDLE=5, AVAX=8, DOGE=5, UNI=5, JUP=5, RUNE=5, XLM=5 (hours)

**Caps (per §2.5):**
- BTC=0.010, ETH=0.010, JUP=0.005, DOGE=0.005, AVAX=0.005, RUNE=0.003, ADA=0.003, UNI=0.002, XLM=0.0015, PENDLE=0.0015

**Cooldowns (per §2.3, post-PR3):**
- BTC=14, ETH=14, ADA=6, PENDLE=6, AVAX=8, DOGE=6, UNI=6, XLM=6, JUP=6, RUNE=6 (hours)

**ATR multipliers (per §2.9):**
- 30 valores **re-tuneados** por A.4-1 harness sobre ventana `[earliest, 2025-04-30Z]`. Lock-at-retune, no lock-at-current-value. Snapshot del artefact en `data/retune/2026-05-04-pre-holdout/params.json` con SHA-256 en manifest. Cualquier movimiento post-holdout disqualifies.

**Other policy:** RISK_PER_TRADE=0.01, score tiers {0.5, 1.0, 1.5}, regime thresholds {>60, <40}, tier mapping (per research §2.3).

### 4.2 Unlockable post-holdout (depending on results)

**Si holdout pasa el Gate 3 threshold** con N=3 PRIMARY: el sistema validates as-is. Los locked values siguen locked en producción. Cualquier change futuro requiere su own validación.

**Si holdout falla el Gate 3 threshold:** los locked values quedan invalidados como production-ready. Iteraciones siguientes pueden tunear sobre train pero NO sobre holdout (que ya fue gastado en este test).

### 4.3 N effective lock (per §3)

**N=3 trials PRIMARY** (1 TL + 1 caps protocol-derived block + 1 tier mapping data-derived). Locked.

Cualquier ajuste a este N post-holdout (e.g., hacia arriba: "los caps eran realmente N=9, by the way"; hacia abajo: "tier mapping no debió contar") es disqualifying.

### 4.4 Procedural commitments for amendments to this spec

Lección post-#303 sequencing:

- **Cualquier amendment a D9 entre el cierre del paquete A.4-1 y el run del holdout** dispara 72h pause + external review. NO same-day merge.
- **Amendments tocando valores locked en §4.1 require analyst sign-off BEFORE merge** — not retroactive notification. Sequencing convenido por todas las partes; aprendizaje sistémico sobre el proceso de adversarial collaboration, no error individual.

### 4.5 Internal review status (A.4-1 retune harness, PR #287)

Reviewer interno (Claude agent) revisó pre-publicación del paquete (sesiones del 4-5 mayo 2026):
- Plan del dev agent en `docs/superpowers/plans/2026-05-04-a4-1-phase3-retune-execution.md`
- PR #287 Phase 2 harness diff + tests
- Spec D9 §2.9/§4.1 alignment con CLAUDE.md caveat #1
- Schema mismatch claim del dev agent (verificado 6 keys post-rebase antes de aprobar Option A pass-through)
- Drafts de notification + dossier al analista externo

**Objeciones que el reviewer interno NO planteó pero el externo SÍ (registradas en este addendum):**
- Gate bias toward KEEP (objeción externa #1)
- Magnitude flag soft framing — registrado inicialmente como Phase 5 awareness, no como gate (objeción externa #3a)
- "Do nothing, document contamination" alternative — no evaluada explícitamente al elegir Path A (objeción externa #6)

Estas son las objeciones que justifican adversarial collaboration con review externo además del interno.

---

## 5 · Interpretación pre-registered del holdout outcome

### 5.1 Si holdout pasa Gate 3 threshold con N=3 PRIMARY

**Conclusión:** structural fix validated. Los 4 viable symbols (BTC, ETH, PENDLE, ADA) tienen edge ejecutable bajo el lifecycle nuevo.

**Action items:**
- Comunicar a Simon (per A10 del plan estratégico).
- Ship system con production basket = 4 viable symbols (decisión A9 sobre Mundo C symbols se toma separada — probablemente disabled-by-flag o removed).
- Open separate ticket para re-baseline (#272) con números honestos post-holdout.

### 5.2 Si holdout NO pasa Gate 3 threshold

**Sub-cases:**

**5.2.a Holdout misses by margin pequeño (<10% gap to threshold):**
- Indica que el structural fix está cerca pero no enough.
- **NO ajustar valores y re-correr** — eso es disqualifying.
- Información productiva: el sistema necesita iteración adicional. Posibilidades:
  - ATR multipliers re-tune (pre-existing per #272)
  - Time-limit calibration finer (más valores en el grid pre-registered)
  - Sizing cap relaxation (sqrt v2 cost model lands)
- Comunicar a Simon: "structural fix shipped, holdout missed by X%, iteration needed."

**5.2.b Holdout misses by margin grande (>30% gap):**
- Indica que el problema es más profundo que el structural fix.
- Hipótesis a investigar:
  - Scoring puede ser artifact (análisis #8 inconclusivo per diagnóstico)
  - Edge predictivo del train segment puede no generalizar
- Comunicar a Simon: "structural fix shipped, holdout failed materially, scoring redesign or basket reduction may be needed."

**5.2.c Holdout passes BTC/ETH but fails B-like sólido (PENDLE, ADA):**
- Indica que el viable basket is even smaller (BTC/ETH only).
- Pre-registered as plausible outcome.
- Comunicar a Simon: "system viable for majors only post-fix; consider basket reduction."

### 5.3 Edge cases

**Si el smoke / validación previa al holdout ya muestra que el sistema tradeará 0 en algún símbolo del holdout (e.g., PENDLE post-cap):** ese símbolo aporta 0 trades al holdout assessment. Es información válida ("PENDLE effectively dormant by design"), no failure case.

**Si el holdout dataset descubre data drift (F&G provider revised, funding rate provider revised):** per A.4 caveat 3 del plan A.1 holdout provenance, esto debe re-fetched + diff'd. Si revisions son materiales, holdout assessment se aborta y new holdout window se locks.

---

## 6 · Sub-decisiones que requieren operator confirmation pre-circulación

Antes de circular este spec a stakeholders más allá del operador (e.g., al senior reviewer o post-PR3 al doc para Simon), confirmar:

1. **N=2 vs N=3 vs otra cifra:** ¿operador acepta N=2 como ex-ante commitment? El senior reviewer flageó esto como la decisión más cargada del spec.
2. **ATR multipliers preserved (§2.9):** confirmar que `#272` re-baseline + `#287` retune harness siguen postponed durante el holdout assessment.
3. **Si holdout falla case 5.2.b:** confirmar que la action item es "scoring redesign investigation" no "re-tune cap valores con data del holdout" (que sería disqualifying).

---

## 7 · References

- Strategic pivot plan: `docs/superpowers/plans/2026-05-01-a4-strategic-pivot-plan.md` — Decisión 9 source.
- Diagnóstico: `docs/superpowers/specs/es/2026-04-30-a02-diag-deep-dive.md` — input data para tier mapping.
- Parameter study: `docs/superpowers/research/2026-05-02-structural-fix-parameter-study.md` — §5 master table source-of-truth.
- Exit logic benchmark: `docs/superpowers/research/2026-04-30-exit-logic-benchmark-crypto.md` — Triple Barrier rationale.
- Operational model: `docs/superpowers/specs/es/2026-05-01-operational-model-manual-gating.md` — current cooldown enforcement state.
- Issues: #294 (epic), #281 (diagnostic, OPEN), #282 (benchmark, OPEN), #279 (cap addressed by PR2), #272 (re-baseline deferred), #287 (retune harness deferred), #249 (Gate 3 / DSR threshold definition), #250 (A.4 holdout evaluation epic).
- PRs: #296 (PR Estructural 1 time-limit, merged), #297 (PR Estructural 2 cap, in flight).

---

## 8 · Update log

| Date | Author | Change |
|------|--------|--------|
| 2026-05-03 | reviewer agent (drafted), sssamuelll to approve | Initial draft per Decisión 9 commitment + senior reviewer feedback on (4) caps as DOFs |
| 2026-05-03 (later) | reviewer agent (drafted), external reviewer (raised), sssamuelll to approve | **N effective elevated 2 → 3 PRIMARY.** External reviewer surfaced que el tier mapping (§2.8) es data-derived (anchored en basket cost_bps spectrum), separable de los cap-values (§2.5) que son protocol-derived externally. Sections affected: §2.5 (decomposition explicit), §2.8 (N=0 → N=1), §3 (table updated, N=3 PRIMARY, N=2 demoted to "optimistic alternative — discouraged"). Pre-holdout commitment integrity preservada: el spec se actualiza ANTES del holdout, alineado con su propio §1 ("este doc fija los commits ex-ante; lo que pase después se evalúa contra estos commits"). |
