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

**N effective contribution:** **N=1 trial** — los 9 caps cuentan como un único protocol-derived block, no como 9 trials independientes.

**Justificación del N=1 (NO N=9):**
- Los valores vienen de un único protocolo de research, ejecutado y documentado pre-implementation.
- El tier mapping es deterministic en (cost_bps_mean, framework defaults) — input data del diagnóstico, no choice del designer.
- Per-symbol assignment es directo del tier mapping, no per-symbol search.
- Si fueran 9 trials independientes, requirirían search space declarado (e.g., "para BTC, búsqueda en {0.005, 0.010, 0.015}; para ETH, búsqueda en {...}; etc."). No hay tal search space — hay 1 protocol output.

**Justificación honest del **NO N=0**:**
- Los inputs del protocol incluyen data del train segment (cost spectrum per-symbol, observed first-trade participation per-symbol). En ese sentido, el protocol es data-aware, no completamente externo.
- Si los inputs hubieran sido distintos (basket distinto, segmento de train distinto), los caps habrían sido distintos.
- Esto es lo que diferencia N=1 de N=0: el protocol consume data, así que cuenta como 1 trial.

**Riesgo si N estuviera mal estimado:**
- Si N real es 9 (cada cap independiente) y nosotros usamos N=1: el threshold del Gate 3 será demasiado bajo. Holdout va a parecer pasar más fácil de lo legítimo.
- Si N real es 1 (verdadero protocol-derived) y nosotros usamos N=9: threshold demasiado alto. Holdout puede fallar legítimo siendo válido.

**Conservative reading alternative:** si el operador o un reviewer externo no acepta N=1 como defendible, fallback es **N=2** (1 protocol-derived block + 1 implicit data-awareness penalty). N=2 sigue siendo materially distinto de N=10.

**Pre-registration commitment:** este spec fija **N=1 para los caps** como tratamiento. Si post-holdout alguien quiere argumentar N=9 (por que el resultado es desfavorable), eso es ajuste post-hoc y disqualifies el resultado como validación primaria. Si pre-holdout alguien quiere argumentar N=2 (por conservatismo), worth flagear ahora antes del threshold lock.

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

### 2.8 Tier mapping (cost-based clustering)

**Estado actual:** documented en research §2.3 — major/mid Tier-1/mid Tier-2/small/floor, mapped por `cost_bps_mean` proxy. Usado para asignar `max_participation_rate` per-symbol.

**Decisión pre-holdout:** **preservar.** El mapping es derived from data del diagnóstico, fijo en research §5.

**Justificación:** mapping es protocol-output, ya cuenta en el N=1 de §2.5.

**N effective contribution:** 0 adicional (incluido en N=1 de caps).

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

**Decisión pre-holdout:** **preservar valores actuales.** NO re-tunear (#272 deferred per Sam, A.4-1 retune harness #287 stays open draft).

**Justificación:**
- Valores fueron tuneados pre-A.4 v1 (epic #121 + iterative). Per #272, los números baseline están inflados pre-#223/#224 phantom-fix — pero el operador decidió NO re-baseline antes del structural fix epic.
- Re-tunear estos valores ahora introduce DOFs y mezcla scope con el structural fix.
- Si holdout falla, una de las hipótesis a investigar es que los ATR multipliers son los wrong numbers para el nuevo exit logic (Triple Barrier per-cluster). Esa investigación es post-holdout.

**N effective contribution:** 0 (valores preserved sin search).

**Caveat heredado:** estos valores fueron tuneados pre-PR1/PR2. El nuevo exit logic (con time-limit + cap) puede tener interacciones con los ATR multipliers que cambien la performance. Esto es exactamente parte de lo que el holdout va a revelar. Pre-registered como "valores preservados, interaction con structural fix es input al holdout, no DOF."

---

## 3 · N efectivo del DSR — propuesta pre-registered

| Asunción | Estado | N effective contribution |
|---|---|---|
| 2.1 Cooldown | rule-derived from TL | 0 |
| 2.2 BE-move asimetría | preserve (symmetric) | 0 |
| 2.3 R-multiple sizing | preserve | 0 |
| 2.4 Time-limits per-symbol (PR1) | grid protocol-derived | **1** |
| 2.5 Caps per-symbol (PR2) | priors protocol-derived | **1** |
| 2.6 Regime detector | preserve | 0 |
| 2.7 Score → size mapping | preserve | 0 |
| 2.8 Tier mapping | included en 2.5 | 0 |
| 2.9 ATR multipliers per-symbol | preserve | 0 |

**N effective propuesto: 2.**

**Conservative alternative:** N=3 si se acepta que el data-awareness de los caps merece penalty implícita extra (subiendo de 1 a 2 los caps). Total: 1 (TL) + 2 (caps) = 3.

**Strict alternative:** N=10 si se trata cada cap como trial independiente. Total: 1 (TL) + 9 (caps) = 10. **Disagree** — los caps NO son 9 search choices independientes; son 1 protocol output con 9 cells.

**Recomendación operacional:** lock N=2 ex-ante. Esto es el commitment pre-holdout. Si reviewer externo o operador prefiere N=3 por conservatismo, flagear ahora; cambiar post-holdout es disqualifying.

**Implicación para Gate 3 threshold (#249):**
- Per Lopez de Prado DSR formula: `DSR = (SR_observed - E[max SR | N trials]) / σ_SR`
- E[max SR | N=2] vs E[max SR | N=10] difiere significantly. N=2 produce threshold ~0.25-0.35 lower que N=10 (rough calibration; #249 va a calcular exact con sus formulas).
- Threshold lower = más fácil de pasar legitimately. Que N=2 sea el correcto count requiere defensibilidad ex-ante (este spec).

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
- (Los 30 valores in `config.json["symbol_overrides"]` actuales)

**Other policy:** RISK_PER_TRADE=0.01, score tiers {0.5, 1.0, 1.5}, regime thresholds {>60, <40}, tier mapping (per research §2.3).

### 4.2 Unlockable post-holdout (depending on results)

**Si holdout pasa el Gate 3 threshold** con N=2: el sistema validates as-is. Los locked values siguen locked en producción. Cualquier change futuro requiere su own validación.

**Si holdout falla el Gate 3 threshold:** los locked values quedan invalidados como production-ready. Iteraciones siguientes pueden tunear sobre train pero NO sobre holdout (que ya fue gastado en este test).

### 4.3 N effective lock (per §3)

**N=2 trials** (1 TL + 1 caps protocol-derived block). Locked.

Cualquier ajuste a este N post-holdout (e.g., "los caps eran realmente N=9, by the way") es disqualifying.

---

## 5 · Interpretación pre-registered del holdout outcome

### 5.1 Si holdout pasa Gate 3 threshold con N=2

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
