# R3 FAIL — Cierre formal y handoff a path (a) de #321

**Fecha:** 2026-05-13
**Status:** DRAFT — pendiente comunicación a Simón
**Autor:** sssamuelll en colaboración con Claude Opus 4.7
**Tipo:** decisión metodológica / closure document
**Trigger:** R3 trend-pullback sweep verdict FAIL clean (PR #336, mergeado 2026-05-13)
**Honors:** §1.1 hard-lock del R3 pre-reg (PR #333) — path (a) del issue #321
**Bloqueante de:** apertura del epic regime-allocation pivot (spec separado: `2026-05-13-epic-regime-allocation-strategy-pivot.md`)

---

## 1 · Resumen ejecutivo

Tras la cadena pre-registrada **R1 FAIL + R2 FAIL + R3 FAIL** (PRs #329, #327, #336 respectivamente), todas las tres recomendaciones estructurales del audit (#323) han sido ejecutadas y rechazadas por sus criterios pre-registrados. Por §1.1 del R3 pre-reg, este hallazgo activa automáticamente **path (a) de #321**: aceptar el finding, no invitar nuevos usuarios, archivar la estrategia LRC actual como "no edge demostrable bajo simulación live-equivalent".

Este documento es el **cierre formal** de esa decisión. No propone retry, no propone follow-up sobre la estrategia actual. La continuación del proyecto (si la hay) se hace bajo un epic **estructuralmente distinto**, no como recovery de la actual.

---

## 2 · Cadena de evidencia acumulada

| Fase | PR | Pre-reg | Verdict | Mecanismo | Evidencia |
|---|---|---|---|---|---|
| **R1** Dynamic exit | #329 | PR #328 | **FAIL clean** | Signal-reversal exit reemplaza TP estático | El signal LRC sigue produciendo -0.9R/trade — los exits no rescatan |
| **R2** Gate re-derivation | #327 | PR (R2 pre-reg) | **FAIL strong** | Re-derivar `time_limit_hours` y `max_participation_rate` desde teoría limpia | Los gates restringían volumen pero no tapaban edge — no había edge que tapar |
| **R3** Trend-pullback | #336 | PR #333 | **FAIL clean** | Reemplazo del frame LRC mean-reversion por momentum SMA50/200 + retrace SMA20 | Frame momentum también -0.9R/trade. El mecanismo SÍ se enganchó (trades 30-50), FAIL es por profitabilidad pura. |

### Findings empíricos consolidados (de #316 + #323 + #336)

1. **H1 (signal expectancy)** — CONFIRMADA. -0.9R/trade en survivors (ETH/BTC al SL alto). Probada para ambas direcciones del razonamiento técnico (reversión y continuación). El stack 4H+1H+5M de indicadores clásicos no discrimina dirección con suficiente edge.
2. **H2 (TP unreachable)** — CONFIRMADA. Variar TP entre 2-6 ATR cambia P&L en 0.24%. La estrategia casi nunca alcanza TP.
3. **H4 (sizing path-dependency)** — CONFIRMADA. R-multiple sobre SL tight produce notional 2× capital → bancarrota en 6-10 trades.
4. **H7 (gates over-restringen)** — CONFIRMADA pero NO rescata (R2 FAIL). 8 de 10 símbolos con gates contaminados producían 1-3 trades en 12 meses; con gates limpios producen más trades pero igual de negativos.
5. **H8 (cost amplification)** — CONFIRMADA. DOGE -$30,489 en single trade documentado.

**Posterior bayesiano**: P(viable strategy under current basket + current architecture) cayó de pre-R3 12-18% a **post-R3 2-4%**. Below threshold para further investment.

---

## 3 · Decisión: path (a) del #321 honrado

Por §1.1 del R3 pre-reg (PR #333):

> *"§1.1 operator-locked hard constraint: R3 FAIL → DIRECT to path (a) of issue #321 (NO H5 follow-up, NO further phase, NO retry with different signal)."*

Por path (a) del issue #321:

1. **Aceptar el finding empírico**. La estrategia LRC mean-reversion + 4H/1H/5M indicators + ATR exits + R-multiple sizing no muestra edge demostrable en pre-holdout window bajo simulación live-equivalent.
2. **NO ejecutar A.4-3 (holdout evaluation)**. La bala única queda sin gastar — el pre-holdout ya es evidencia suficiente. Issue #322 (holdout hard-block) permanece enforced.
3. **NO promover ningún parámetro al config de producción**. Los valores actuales en `config.defaults.json[symbol_overrides]` quedan como "last known values pre-deprecation", sin claim de optimalidad.
4. **NO retry sobre la estrategia actual**. NO grid expansion, NO basket replacement bajo arquitectura actual, NO single-signal reemplazo additional.
5. **NO H5 follow-up** (basket contaminado). H5 queda en parking lot indefinidamente; si una arquitectura futura demanda revisión del basket, será bajo nuevo pre-reg.

### Implicaciones operacionales

| Item | Estado pre-cierre | Estado post-cierre |
|---|---|---|
| Estrategia LRC en `btc_scanner.py` / `strategy/core.py` | Live, default config | **Deprecated, marked "no edge demonstrable"** |
| Production scanner | Corriendo en `trading.sdar.dev` | Sigue corriendo en shadow / informational mode; NO se promueve a clientes |
| `trading_webhook.py` Telegram alerts | Activo | Sigue activo para el operador (sssamuelll); no para nuevos usuarios |
| Issue #271 (user invitation guardrail) | Enforced pre-cierre | **Enforced permanentemente bajo arquitectura LRC**. Nuevos usuarios condicionados al éxito de un epic estructuralmente distinto (ver §6). |
| Issue #246 (holdout dataset) | Locked, never touched by strategy code | **Mantiene lock** — A.4-3 cancelado |
| Issue #317 (cost model v2 deferred) | Open | **Sigue open**; convertido en prerequisito formal del próximo epic |
| Epic #323 (structural audit) | Open con R1/R2/R3 in progress | **Cerrado** con verdict FAIL acumulado |

---

## 4 · Findings de investigación externa (2026-05-13)

Como parte del proceso de cierre, se ejecutó investigación externa profunda en literatura académica + practitioner sobre viabilidad estructural de estrategias regime-allocation crypto. Resultados clave:

### 4.1 Trend-following SÍ funciona en crypto, pero a horizontes específicos

- **Liu & Tsyvinski (RFS 2021, SSRN 3226952)**: momentum en crypto funciona a **1-4 semanas**, no a meses. L/S momentum portfolio ~3% retorno semanal en exceso, universo 1,827 coins.
- **Zarattini, Pagani, Barbon (SSRN 5209907, 2025)**: ensemble Donchian (lookbacks 5/10/20/30/60/90/150/250/360 días) sobre top-20 más líquidos, vol-based sizing → **Sharpe 1.58, CAGR 30%, Sortino 2.03, alpha +14% vs BTC**.
- **Hubrich (SSRN 3055498, 2017)**: time-series momentum factor confirmado en crypto. Reglas simples MA reducen drawdown dramáticamente vs B&H.

### 4.2 Regime persistence en crypto es DÉBIL

- HMM literature (Giudici 2020, MDPI 2025, Preprints 2026): *"hidden states for all coins are not persistent, but they present frequent alternations"*.
- Implicación: la intuición de "regimes duran meses" (típica en equities) **no aplica directamente en crypto**. Sweet spot empírico es semanas, con ensemble cubriendo días-a-meses.

### 4.3 Performance de fondos 2024 — leccion crítica

- BTC: +120% en 2024
- VisionTrack composite (130 funds): +40%
- Best individual fund (Reflexive Capital): +106% — **aún por debajo de BTC**
- VisionTrack Quant Directional (proxy trend-following): +53.7% — menos de la mitad de BTC
- **Conclusión**: NINGÚN fondo activo le ganó a BTC buy-and-hold en 2024. La diversificación hacia altcoins destruyó valor en un "Bitcoin year".

### 4.4 Costos en small-caps confirman nuestro DOGE -$30K

- Small-cap altcoins: **1-5% slippage por trade single**, $10K trade puede mover precio 10%+
- Retail traders: +0.4% slippage extra vs institucionales
- Confirma que el cost model lineal v1 está sub-modelando el riesgo; v2 sqrt-participation (Almgren-Chriss) es prerequisito para cualquier estrategia que toque mid/small-caps

### 4.5 Realismo del techo Sharpe

- Sharpe documentado para trend-following crypto: **1.0-1.8** (Zarattini, Hubrich, vol-targeted variants)
- Sharpes 3-4 publicados (e.g., Palazzi 2025 pairs trading) están **typicamente overfitted** (90/10 train/test split sobre misma ventana)
- Cualquier backtest que prometa > 2.0 Sharpe debe activar phantom-profit suspicion

**Estos findings son evidencia externa que justifica el pivot estructural, no recovery del actual**. Soportan path (a) del #321 (la estrategia actual no es viable) Y también informan el diseño del epic siguiente (espera Sharpe 1.0-1.8, no 3-4; horizonte semanas, no meses; vol-targeting, no R-multiple).

Sources documentadas en el spec del epic (§4 de `2026-05-13-epic-regime-allocation-strategy-pivot.md`).

---

## 5 · Comunicación a Simón (template / outline)

Este es el outline. El mensaje final lo editás vos según contexto y tono.

**Subject:** Cierre formal de epic A.4 — no edge demostrable, pivot estructural propuesto

**Outline:**

> Hola Simón,
>
> Después de ~6 semanas de trabajo metodológico sobre la pregunta "¿la estrategia LRC actual tiene edge demostrable bajo simulación live-equivalent?", el resultado es **no**. Te resumo y propongo el siguiente paso.
>
> **Cadena de evidencia (toda pre-registrada antes de ejecución):**
> 1. R1 (dynamic exit) — FAIL: cambiar el exit no rescata.
> 2. R2 (gate re-derivation) — FAIL: los gates no tapaban edge.
> 3. R3 (frame replacement, mean-reversion → momentum) — FAIL: el frame alternativo también pierde.
> 4. P(viable bajo arquitectura actual) cayó de 12-18% a 2-4% post-R3.
>
> **Implicaciones:**
> - Estrategia LRC actual queda archived como "no edge demonstrable". Sigue corriendo en producción para mi uso personal (informational), pero no se promueve a clientes.
> - Issue #271 (guardrail invitación usuarios) queda enforced permanentemente bajo esta arquitectura.
> - Holdout dataset queda intacto (A.4-3 cancelado). La bala única no se gasta.
>
> **Investigación externa que hicimos:**
> Revisamos literatura académica reciente (Zarattini 2025, Liu-Tsyvinski 2021, Hubrich 2017, HMM regime detection) y performance de fondos crypto 2024. Findings principales:
> - Trend-following crypto SÍ funciona, pero a horizontes de semanas (no horas como teníamos, no meses como mi intuición), Sharpe documentado 1.0-1.8.
> - Cero fondos activos crypto le ganaron a BTC buy-and-hold en 2024.
> - El basket de altcoins mid/small-cap (8 de nuestros 10) tiene problemas estructurales de slippage que confirman lo que vimos en backtest (caso DOGE -$30K).
>
> **Propuesta de siguiente paso:**
> Abrir un epic separado con strategy class estructuralmente distinto: regime-allocation tipo Zarattini (ensemble Donchian multi-timeframe, vol-targeting, basket de 10 actuales mantenido bajo caveat documentado). Detalles en spec `2026-05-13-epic-regime-allocation-strategy-pivot.md` adjunto.
>
> Antes de abrir el epic nuevo necesito tu aprobación de:
> 1. Aceptar el cierre del A.4 según este documento.
> 2. Confirmar que enforced #271 hasta que el nuevo epic pase su validation bar (si lo abrimos).
> 3. Confirmar que el holdout se mantiene locked (no A.4-3, no nada).
> 4. Revisar el spec del epic propuesto y dar feedback antes de empezar Phase 0.
>
> Si preferís parar acá sin abrir nada nuevo, también es opción válida — el cierre A.4 es independiente de la decisión de pivot.
>
> Saludos,
> Samuel

---

## 6 · Handoff al epic regime-allocation pivot

Este cierre **NO obliga** a abrir el epic nuevo. Son decisiones independientes:

- **Decisión 1 (este doc)**: cerrar A.4 / R1+R2+R3 por path (a). Honor #271, mantener holdout intacto.
- **Decisión 2 (separada)**: abrir epic regime-allocation. Spec drafted en `2026-05-13-epic-regime-allocation-strategy-pivot.md`. Requires explicit operator approval + (eventualmente) Simón approval antes de Phase 0.

El draft del epic existe para que la pregunta "¿hay un siguiente paso viable?" tenga respuesta concreta, no para forzar la decisión. Si Simón o el operator deciden no abrirlo, el draft se archiva en `docs/superpowers/specs/es/archived/` sin pérdida.

### Issues que se cierran con este doc

- `#321` — path (a) honored, decisión tomada explícitamente.
- `#316` — spec de inflexión metodológica → finding confirmado empíricamente, cerrado.
- `#322` — holdout hard-block → permanente bajo arquitectura LRC. Re-evaluable solo bajo nuevo pre-reg de un epic distinto.
- `#323` — structural audit → todas las recomendaciones (R1, R2, R3) ejecutadas y FAILed. Closed.

### Issues que permanecen open

- `#271` — guardrail invitación usuarios. Enforced permanentemente bajo arquitectura LRC; re-evaluable solo bajo validación de epic estructuralmente distinto.
- `#317` — cost model v2 deferred. Sigue open; convertido en prerequisito formal Phase 0 del epic pivot (si se abre).
- `#246` — holdout dataset locked. Sigue open; protected indefinidamente.

---

## 7 · Reproducibility y artefactos

Todo el material que soporta este cierre está commiteado y reproducible:

- **R1 evidence**: `data/retune/2026-05-12-r1-dynamic-exit/{verdict.json, derivation_audit.md, sweep_results_A.json}` (PR #329)
- **R2 evidence**: `data/retune/2026-05-11-r2-gates/{verdict.json, derivation_audit.md}` (PR #327)
- **R3 evidence**: `data/retune/2026-05-13-r3-trend-pullback/{verdict.json, derivation_audit.md, sweep_results_{A,B,C}.json, signal_diagnostics.json}` (PR #336)
- **Inflexion spec**: `docs/superpowers/specs/es/2026-05-11-a4-hallazgo-inflexion-metodologica.md`
- **Structural audit**: `docs/superpowers/specs/es/2026-05-11-strategy-structural-audit.md`
- **External research log**: §4 de este doc + §4 del epic spec (sources sub-section)

---

## 8 · Historial de revisiones

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-13 | Initial draft post-R3 FAIL (PR #336 merged) | sssamuelll + Claude Opus 4.7 |
| TBD | Comunicación enviada a Simón | sssamuelll |
| TBD | Respuesta de Simón incorporada (acceptance / pushback / amendment) | sssamuelll |
| TBD | Closure final ratificado | sssamuelll |
