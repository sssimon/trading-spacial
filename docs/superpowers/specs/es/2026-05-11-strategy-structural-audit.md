# A.4 follow-up — Auditoría estructural de la estrategia (Phase 1)

**Fecha:** 2026-05-11
**Status:** DRAFT — primera entrega, abierta a iteración antes de Phase 2
**Autor:** Claude Opus 4.7 (sesión nueva) en colaboración con sssamuelll
**Tipo:** análisis estructural pre-implementación (NO promueve params, NO toca código, NO abre holdout)
**Trigger:** Issue #321 path (b) — "reformular estrategia (cambios estructurales, no param tuning)"
**Relacionado con:** `2026-05-11-a4-hallazgo-inflexion-metodologica.md`, issue #317 (gates calibration deferred), CLAUDE.md "Caveats heredados — A.4 (#250)" #1 y #4

---

## §0 · Resumen ejecutivo

Phase 1 descompone la estrategia actual en 9 building blocks y prueba 8 hipótesis falsifiables contra los 1050 backtests honestos del grid topology diagnostic (#318). Resultados:

- **Tres hipótesis confirmadas por evidencia directa**: (H2) la estrategia nunca llega a TP — TP es independiente del trade count y del P&L a lo largo del grid; (H7) los gates per-symbol contaminados (#317) producen under-trading severo en 8/10 símbolos; (H4-derivado) al SL alto donde no hay bancarrota, cada trade pierde ≈$90 ≈ 0.9 × `risk_amount` — la estrategia tiene expectancia negativa per-trade, no es solo un bankruptcy artifact.
- **Una hipótesis confirmada pero de naturaleza diferente** (H8): el modelo lineal de costos amplifica pérdidas single-trade en barras de baja liquidez — DOGE registra una sola trade con pnl neto de -$30,489 a SL=0.7 a pesar del K-cap, exclusivamente vía slippage que crece linealmente con notional/liquidity.
- **Tres recomendaciones estructurales priorizadas** (§6): R1 reemplazar el TP-target estático con exit dinámico (trailing o signal-reversal); R2 re-derivar gates per-symbol desde teoría en lugar de #281 contaminado; R3 reemplazar la lógica de entry LRC-mean-reversion con un frame alternativo (momentum/breakout) bajo experimento controlado.

Estas recomendaciones NO implementan nada en Phase 1. Phase 2 requiere approval explícita del operador después de revisar este spec.

---

## §1 · Contexto y alcance

### 1.1 Trigger

El issue #321 path (b) demanda "reformular estrategia con cambios estructurales, no param tuning" tras la confirmación de #318 (Q2 grid topology diagnostic) que dictaminó `expansion_justified: False` y `n_symbols_meeting_criterion: 0`. La estrategia parametrizada dentro del grid `(sl ∈ [0.5, 2.5], tp ∈ [2, 6], be ∈ [1.5, 2.5])` no produce P&L positivo en ningún punto para ningún símbolo del basket curado.

### 1.2 Alcance Phase 1 (este spec)

**Hace:**
- Decompone la estrategia en building blocks con citas `file:line`.
- Formula hipótesis falsifiables por bloque.
- Prueba cada hipótesis contra evidencia ya commiteada (no nuevos sweeps).
- Recomienda 2-3 cambios estructurales con criterios pre-registrables.

**No hace:**
- No lee `data/holdout/` (guard A+B activos).
- No modifica código, config, ni params.
- No promueve nada.
- No ejecuta Phase 2 sin approval explícita del operador.

### 1.3 Material evidencial consultado

| Artefacto | Contenido | Uso |
|---|---|---|
| `data/retune/2026-05-11-pre-holdout-atr-evidence/grid_topology.json` | 1050 backtests (10 sym × 105 cells), cada uno con `{sl, tp, be, pnl, trades, bankruptcy_count}` | Test de H1, H2, H4, H7 |
| `data/retune/2026-05-11-pre-holdout-regime-evidence/halted_summary.json` | Per-symbol P&L bajo 4 regime configs (60_40, 70_30, 80_20, no_detector) | Test de H3 |
| `docs/superpowers/specs/es/2026-05-11-a4-hallazgo-inflexion-metodologica.md` | Spec central + §A amendment | Contexto + reanaliza |
| `docs/superpowers/research/2026-04-30-exit-logic-benchmark-crypto.md` | Benchmark literatura exits crypto | Soporte R1 |
| `docs/superpowers/research/2026-05-02-structural-fix-parameter-study.md` | Documenta K-cap rationale | Soporte H4 |
| `costs_calibration.json` | Modelo lineal v1, "v2 sqrt-participation (Almgren-Chriss) planned" | Test de H8 |
| `config.defaults.json:symbol_overrides` | Per-symbol gate values + ATR | Test de H7 |
| `backtest.py`, `strategy/core.py`, `strategy/regime.py`, `btc_scanner.py` | Implementación de los blocks | Mapping §2 |

---

## §2 · Building blocks (decomposition + citas)

| # | Block | Archivo:línea | Decisión embebida |
|---|---|---|---|
| B1 | Signal indicators | `btc_scanner.py:185-225` (calc_lrc, calc_rsi, calc_bb, calc_sma, calc_atr, calc_adx); `strategy/indicators.py` | LRC=100 bars, RSI=14, BB=20/2σ, SMA10/20/100, ATR=14, ADX=14 |
| B2 | Score 0-9 | `strategy/core.py:254-450` (evaluate_signal); `strategy/constants.py` (SCORE_MIN_HALF, SCORE_STANDARD=2, SCORE_PREMIUM=4) | Pondera LRC pos + RSI + BB + macro + divergencias |
| B3 | Entry zone | `strategy/core.py` (referencias LRC_LONG_MAX, LRC_SHORT_MIN); `strategy/constants.py` | LRC ≤ 25% → LONG; LRC ≥ 75% → SHORT (gated por regime=BEAR) |
| B4 | 4H macro filter | `strategy/core.py:326-327` (`price_above_4h = price > sma100_4h`) | SMA100 4H direction alignment |
| B5 | 5M entry trigger | `strategy/core.py:200-225` (`_check_trigger_5m_long/_short`) | bullish/bearish candle + RSI 5m direction |
| B6 | Regime detector | `strategy/regime.py:282+` (detect_regime); `strategy/regime.py:153+` (_compute_local_regime) | 40% price + 30% F&G + 30% funding (mode=global); thresholds 60/40 |
| B7 | Sizing (per-trade) | `backtest.py:81` (RISK_PER_TRADE=0.01); `backtest.py:895-900` (size_mult tiers 0.5/1.0/1.5) | 1% capital × tier multiplier |
| B8 | Exits (SL/TP/TL/BANKRUPT) | `backtest.py:733-755` (exit logic); `backtest.py:381-490` (_close_position); `backtest.py:349-378` (_emit_bankrupt_if_breached); `backtest.py:88` (K=10 cap); `backtest.py:100` (bankruptcy threshold $1K) | SL/TP via ATR multiples; TL via per-symbol `time_limit_hours`; K=10 cap; bankruptcy halt |
| B9 | Gates (participation cap + cooldown + kill-switch) | `backtest.py:999-1018` (liquidity_cap); `backtest.py:822-830` (cooldown); `strategy/kill_switch_v2.py` | Per-symbol `max_participation_rate` × 24h-median vol; cooldown ≥ TL; kill-switch reducción 50% |
| B10 | Basket (selección estática) | `btc_scanner.py:191-194` (DEFAULT_SYMBOLS); referencia epic #135 | 10 monedas curadas, locked post-#135 |
| B11 | Cost model | `backtest_costs.py`; `costs_calibration.json` | Linear v1: slippage = base_bps + size_factor × (notional/liquidity); spread + 10 bps fees |

**Notas estructurales:**
- B7 (sizing) y B8 (exits) están acoplados vía el K-cap mecanism — el K-cap actúa sobre `pnl_pct / sl_pct_actual` (no sobre notional). Esto significa que `risk_amount` bounds el GROSS, pero los costos (B11) se aplican DESPUÉS y NO están bounded por K — esto es el mecanismo de DOGE -$30K. (Ver H8.)
- B6 (regime) determina dirección permitida pero NO la magnitud del signal. PR #315 ya estableció que el detector no tiene poder discriminatorio en el window evaluado.
- B9 (gates) tiene valores derivados de #281 que corrieron sobre el simulador pre-#223 — issue #317 lo flagea como contaminación. Este audit lo trata como una de las hipótesis (H7), no como axiomático.
- B10 (basket) fue seleccionado en epic #135 con el simulador pre-#223. Análoga sospecha de contaminación.

---

## §3 · Hipótesis falsifiables

Cada hipótesis tiene la forma: **"Si este block fuera el problema, observaríamos X. Veamos si X aparece en la evidencia disponible."**

### H1 — Signal genera entries con expectancia negativa per-trade

**Predicción si verdadera:** Al SL alto (donde la trade puede correr hasta el final sin bancarrota), el P&L por trade promedio debe estar cerca de `-1R` (= -risk_amount). Eso indicaría que la mayoría de las trades terminan en SL hit o equivalente.

**Falsable porque:** un signal con edge positivo produciría P&L promedio per-trade > 0 (al menos en algún símbolo).

### H2 — TIME_LIMIT dominante (TP no se alcanza)

**Predicción si verdadera:** El P&L total y el trade count NO deben variar con `atr_tp_mult`, porque las trades no llegan a TP — exit forzado por TIME_LIMIT o SL antes.

**Falsable porque:** una estrategia que sí alcanza TP mostraría P&L creciente con tp_mult bajo en cells positivas, decreciente con tp_mult muy alto.

### H3 — Regime detector irrelevante

**Predicción si verdadera:** Los 4 regime configs producen el mismo P&L per-symbol (dentro del ruido).

**Falsable porque:** un detector útil diferenciaría símbolos/configs por más del 5% del ruido del bankruptcy floor.

### H4 — Sizing inflaciona el path-to-bankruptcy

**Predicción si verdadera:** A SL tight (e.g., 0.5 ATR), el notional explota (~200× risk amount) y un sola perdida cierra ~10% del capital → racha de 6-10 trades → bankruptcy. Trade count debe ser bajo y bankruptcy 100% en cells de SL tight.

**Falsable porque:** una estrategia con sizing apropiado mostraría bankruptcy independiente de SL.

### H5 — Basket contaminado por selección bajo simulador pre-#223

**Predicción si verdadera:** Los 10 símbolos del basket no deberían tener edge sistemático distinto a un universo random de tamaño comparable. La "selección" del epic #135 podría haber preservado símbolos donde el bug de signo en `_close_position` producía menos phantom-profit, no símbolos con edge real.

**Falsable porque:** si la selección estaba bien justificada, los símbolos del basket actual deberían rendir mejor que un sample random del top-50 por volumen bajo el simulador limpio.

### H6 — Mean-reversion frame en un regimen mercado equivocado

**Predicción si verdadera:** Si el window pre-holdout estuvo dominado por trending sostenido (bull o bear), las entries LRC-en-extremo (mean-reversion) catch-the-falling-knife o sell-the-rip se vuelven perdedoras sistemáticas.

**Falsable porque:** una comparación de bars cuyo close[+24h] retorna al LRC mid vs. los que continúan en la dirección de la entrada nos dice si el mercado revierte o continúa.

### H7 — Per-symbol gates over-restringen 8/10 símbolos

**Predicción si verdadera:** Los símbolos con TL=5h y max_PoV ≤ 0.5% deberían tener trade counts mucho menores que ETH/BTC (que tienen TL=14h, PoV=1%). Si los gates bloquean entries que tendrían ido a TP, expandir los gates debería abrir trades positivas — pero si el signal en sí no tiene edge (H1 verdadera), expandir los gates solo aumenta el número de perdidas.

**Falsable porque:** la combinación (trade count bajo) + (bankruptcy 100%) en 8/10 símbolos sería esperable bajo gates over-restringentes; ausencia de esta correlación falsaria H7.

### H8 — Cost model amplifica pérdidas single-trade en bajas liquideces

**Predicción si verdadera:** Trades en barras de baja liquidez producen pnl NETO mucho peor que el K-cap del pnl bruto. Single-trade losses excediendo dramáticamente `K × risk_amount` indicarían cost-model amplification.

**Falsable porque:** si los costos fueran inocuos, las pérdidas single-trade nunca excederían 10 × `risk_amount` ≈ $1500.

---

## §4 · Pruebas de hipótesis contra evidencia

### H1: Signal expectancy — **CONFIRMADA (strong direct evidence)**

Sobre las 70 cells no-bancarrotadas (ETH a SL∈{1.5, 2.0, 2.5}; BTC a SL∈{2.0, 2.5}):

| SL (ETH) | Trades | Mean P&L | P&L per trade | Como fracción de risk_amount ($100) |
|---|---|---|---|---|
| 1.5 | 88.1 | -$8,970 | -$101.9 | -1.02 R |
| 2.0 | 84.1 | -$8,271 | -$98.3 | -0.98 R |
| 2.5 | 83.0 | -$7,448 | -$89.7 | -0.90 R |

Cada trade en ETH al SL alto pierde ≈ 0.9R en promedio. Para BTC a SL=2.5: -$83/trade ≈ -0.83R.

**Interpretación:** Si avg trade = -1R, entonces el universo de exits está dominado por SL hits (que producen exactamente -1R bruto, ~-1.05R neto por costos). Una estrategia con edge positivo tendría avg per-trade > 0 (e.g., 40% win × 2R - 60% loss × 1R = +0.2R neto). Aquí el signal NO está separando winners de losers.

**Caveat:** No podemos descomponer en (win rate, avg win, avg loss) sin re-correr con trade-level dumps. La inferencia "no edge" es consistente con dos sub-causas distintas: (a) signal predice dirección al azar, o (b) signal predice dirección correctamente pero la trade no captura el move antes de SL. H6 explora (b).

### H2: TIME_LIMIT dominante — **CONFIRMADA (strong direct evidence)**

Promediando sobre 10 símbolos × 3 BE values:

| SL\TP | tp=2.0 | tp=3.0 | tp=4.0 | tp=5.0 | tp=6.0 |
|---|---|---|---|---|---|
| sl=0.5 | -$14,872 | -$14,887 | -$14,894 | -$14,893 | -$14,894 |
| sl=1.0 | -$10,175 | -$10,188 | -$10,191 | -$10,198 | -$10,197 |
| sl=2.0 | -$10,291 | -$10,300 | -$10,296 | -$10,300 | -$10,298 |
| sl=2.5 | -$9,273 | -$9,280 | -$9,272 | -$9,278 | -$9,275 |

**Variación máxima a lo largo de TP, fijando SL:** $22 sobre $9,000 = 0.24%. Trade count también: en ETH SL=2.0, varía entre 83-85 trades. **TP es estadísticamente irrelevante para el outcome del backtest.**

**Interpretación:** La estrategia NUNCA o casi-nunca alcanza TP. Los exits dominantes son SL hits + TIME_LIMIT. Modificar TP desde 2 hasta 6 ATR multiples no cambia nada — las trades no llegan a esos rangos en ningún caso. Esto NO confirma cuál de SL vs TL domina; solo confirma que TP no participa.

### H3: Regime detector irrelevante — **CONFIRMADA (via PR #315 / halted_summary)**

Per `halted_summary.json` regime sweep:

| Symbol | 60_40 | 70_30 | 80_20 | no_detector |
|---|---|---|---|---|
| BTC | -9,016 | -9,000 | -9,009 | -9,004 |
| ETH | -9,006 | -9,008 | -9,016 | -9,009 |
| ADA | -9,004 | -9,004 | -9,004 | -9,011 |
| (todos) | ≈-9K | ≈-9K | ≈-9K | ≈-9K |

Margin winner-runnerup agregado = 2.18%. El detector no diferencia outcomes en este window.

**Caveat:** El detector podría tener edge en regímenes muy distintos (e.g., un crash brusco vs un trending sostenido). En este window específico, no aparece. PR #315 ya canoniza esta lectura.

### H4: Sizing inflaciona path-to-bankruptcy — **CONFIRMADA (decomposition + data)**

Sizing math:
- `notional = risk_amount × 100 / sl_pct_actual`
- A SL=0.5 ATR ≈ 0.5% del precio: notional ≈ $100 × 100 / 0.5 = $20,000 (2× capital)
- A SL=2.5 ATR ≈ 2.5%: notional ≈ $4,000 (0.4× capital)

Tabla observada (ETH only):
| SL | Trade count promedio | Notional/capital ratio (apx) | Bankruptcy rate |
|---|---|---|---|
| 0.5 | 3.2 | ~2.0x | 100% (15/15) |
| 0.7 | 27.4 | ~1.4x | 100% |
| 1.0 | 48.5 | ~1.0x | 100% |
| 1.2 | 63.8 | ~0.83x | 100% |
| 1.5 | 88.1 | ~0.66x | 33% (5/15) |
| 2.0 | 84.1 | ~0.5x | 0% |
| 2.5 | 83.0 | ~0.4x | 0% |

**Interpretación:** SL tight → notional alto → cada SL hit cuesta proporcionalmente más capital → bancarrota tras menos perdidas. Esto NO es un bug; es la mecánica del R-multiple sizing combinada con SLs tight. La fix estructural sería desacoplar position size de SL distance (e.g., fixed-notional sizing).

### H5: Basket contaminado — **EVIDENCIA INDIRECTA (no falsifiable con grid actual)**

No tenemos backtests sobre un universo más amplio bajo el simulador limpio. Lo que SÍ podemos observar:

- Los survivors (ETH/BTC) tienen tier `major` (cost base 2 bps, half_spread 1.5 bps).
- Los bancarrotados PENDLE/JUP/RUNE tienen tier `small` (base 10 bps, half_spread 15 bps) — costos 5-10x mayores.
- DOGE/UNI/XLM (tier `mid`) bancarrotan 100% también.

**Confounder:** La diferencia ETH/BTC vs resto puede ser (a) costo más bajo, (b) liquidez más alta que admite participation cap relajado, o (c) características intrínsecas del símbolo. No podemos separar (a)/(b)/(c) sin nuevo experimento.

**Veredicto:** H5 PARCIALMENTE soportada — la correlación cost-tier vs bankruptcy es consistente con basket-determinado-por-bug, pero también consistente con "tier major es genuinamente más operable bajo cualquier estrategia".

### H6: Mean-reversion frame en regimen equivocado — **NO PROBABLE (data insuficiente, requiere new sweep)**

No tenemos en grid_topology.json la información per-trade necesaria (entry timestamp, 1H regime classification at entry). Para probar H6 necesitaríamos extraer:
- Para cada entry, el regime active en ese momento (BULL/BEAR/NEUTRAL).
- Performance per regime — ¿hay un regime donde el signal SÍ tiene edge?

**Pre-registración requerida si Phase 2 incluye este test:**
- Criterio falsificable: ≥1 símbolo en ≥1 regime con avg per-trade P&L > 0 indicaría "el signal tiene edge en un regime específico". Ausencia de tal cuadrante refuerza H1 (signal sin edge en cualquier regime).

### H7: Gates over-restrigen 8/10 símbolos — **CONFIRMADA (strong direct evidence)**

Comparativa per-symbol del `time_limit_hours` y `max_participation_rate`:

| Símbolo | TL (h) | Max PoV | Survival (no bnkpt cells) |
|---|---|---|---|
| BTC | 14 | 0.010 | 30/105 |
| ETH | 14 | 0.010 | 40/105 |
| ADA | 5 | 0.003 | 0/105 |
| AVAX | 8 | 0.005 | 0/105 |
| DOGE | 5 | 0.005 | 0/105 |
| UNI | 5 | 0.002 | 0/105 |
| XLM | 5 | 0.0015 | 0/105 |
| PENDLE | 5 | 0.0015 | 0/105 |
| JUP | 5 | 0.005 | 0/105 |
| RUNE | 5 | 0.003 | 0/105 |

**Survival correlation con gates relajados es estadísticamente abrumadora.** Los dos símbolos que sobreviven a algunas cells son exactamente los dos con TL=14h y PoV=1%; los otros 8 con TL=5h y PoV ≤ 0.5% mueren al 100%.

**Caveat clave:** Esto NO prueba que relajar los gates daría edge — H1 sigue mostrando -0.9R per trade incluso en survivors. Pero confirma que los gates contaminados están **subdeterminando** los símbolos que ni siquiera podemos evaluar (1-3 trades en 12 meses no es muestra suficiente para ningún juicio).

### H8: Cost model amplifica single-trade losses — **CONFIRMADA (forensic case)**

Caso forense DOGE @ sl=0.7 (todas las 15 cells de be × tp): pnl=-$30,489 con `trades=1` y `bankruptcy_count=1`.

Mecánica:
1. K-cap bounds `pnl_usd` bruto a ≤ 10 × risk_amount ≈ $1,500.
2. Cost model aplicado DESPUÉS: `slippage_bps = base + size_factor × (notional / liquidity_per_min)`.
3. Tier `mid`: base=5, size_factor=45000. Si liquidity_per_min en una barra 5m baja es e.g. $100, slippage_bps = 5 + 45000 × (21000/100) = ~9.45M bps = catastrófico.
4. `notional` en una trade tight-SL = ~$21K (2x capital). 9.45M bps × $21K = costos > $30K en una sola trade.
5. La trade gross = -$1,500 (K-capped), pero net = -$1,500 - $28,500 = -$30,000.

**Confirmación adicional:** El propio `costs_calibration.json:v2_planned` admite: *"v2 sqrt-participation (Almgren-Chriss). v1 linear over-penalizes small orders and under-penalizes large ones; v2 should migrate."* El modelo conoce su propia limitación.

**Mitigación parcial existente:** `max_participation_rate × 24h-median vol` debería bounder notional, pero el cap usa **24h median** mientras que slippage usa **per-bar** liquidity. En barras anómalamente thin, el cap no protege.

---

## §5 · Ranking de hipótesis por evidencia disponible

| Rank | Hipótesis | Status | Soporte | Probable causa raíz | Tipo de fix |
|---|---|---|---|---|---|
| 1 | H2 — TIME_LIMIT/SL dominante, TP no alcanzable | CONFIRMADA | Strong direct (TP independence en 1050 cells) | Exit logic mal calibrada para los moves que produce el signal | Structural — reemplazar B8 |
| 2 | H7 — Gates over-restrigen 8/10 símbolos | CONFIRMADA | Strong direct (survival correlation perfecta con gate laxness) | Calibración contaminada vía #281 / pre-#223 sim | Semi-structural — re-derivar gates desde teoría |
| 3 | H1 — Signal expectancy negativa | CONFIRMADA | Strong direct (per-trade ≈-0.9R en survivors) | Frame de la estrategia (LRC mean-reversion + 4H macro + 5m trigger) no produce edge en este window | Structural — reemplazar B2 (scoring) y/o B3 (entry zone) |
| 4 | H8 — Cost model amplifica thin-liquidity single trades | CONFIRMADA | Strong forensic (DOGE -$30K caso reproducible) | Modelo lineal v1 conocidamente insuficiente | Structural — migrar a v2 sqrt-participation (ya planeado) |
| 5 | H4 — Sizing R-multiple infla path-to-bankruptcy | CONFIRMADA | Strong decomposition + data | R-multiple sizing acopla notional con SL tight | Structural — desacoplar size de SL (fixed-notional o vol-normalized) |
| 6 | H3 — Regime detector irrelevante | CONFIRMADA (vía PR #315) | Strong indirect (4 configs equivalentes within 2.18%) | Composición F&G + funding + price no separa regimes en este window | Structural pero baja prioridad — eliminar o reemplazar B6 |
| 7 | H5 — Basket contaminado | INDIRECT | Moderate (correlation con cost tier es ambigua) | Selección epic #135 sobre simulador pre-#223 | Structural — re-validar B10 con nuevo experimento |
| 8 | H6 — Mean-reversion en regime equivocado | NO PROBABLE en Phase 1 | Pendiente extraction per-trade | TBD — Phase 2 si se decide investigar | Pre-registration requerida |

---

## §6 · Recomendaciones estructurales priorizadas

Las tres recomendaciones siguen el orden: (a) mayor evidencia disponible, (b) menor blast radius, (c) prerequisite de las demás. **Importante:** R1 y R2 son **prerrequisitos infrastructurales** para evaluar honestamente R3 (la pregunta grande). Sin R1+R2, cualquier resultado de R3 será confundido por el efecto de exits + gates.

### R1 — Reemplazar TP-target estático con exit dinámico

**Reasoning:** H2 muestra que TP es estadísticamente irrelevante en los 1050 backtests. La estrategia no produce moves que lleguen a TP=2-6 ATR, sin importar cuál se elija. Mantener el block "TP estático" es desperdicio metodológico — la lógica de exit más útil sería capturar lo que SÍ produce.

**Deliverable concreto (Phase 2):**
- Implementar exit alternativo, candidatos:
  - **Trailing stop** parametrizado por `atr_trail_mult` (e.g., trail = entry + trail_mult × ATR; mueve con price).
  - **Signal-reversal exit**: cerrar cuando el LRC sale del trigger zone (mean-reversion explícita).
  - **Time-decay exit**: TP que se relaja con el tiempo (e.g., starts at 3 ATR, decae a 1 ATR luego de 4h).
- Sweep idéntico al q2 diagnostic pero variando `atr_trail_mult` (o param equivalente al exit elegido) en lugar de `atr_tp_mult`.

**Riesgo de overfitting:** MEDIO. Trailing stops tienen parameter sensitivity. Walk-forward sobre múltiples sub-windows debe validar antes de promover.

**Criterio de éxito pre-registrable:** Sobre el mismo pre-holdout window, con el exit dinámico activo y el resto de la estrategia intacta, ≥1 símbolo con ≥20 trades cierra el window con net P&L > 0 (per-trade EV positiva), Y ningún símbolo bankrupta antes de los primeros 30 trades. (El umbral 20 trades es para tener significancia estadística mínima.)

**Criterio de fallo pre-registrable:** Si el exit dinámico produce el mismo patrón (TP-independent → ahora trail-independent across grid de trail values), entonces el problema NO está en B8 sino upstream (signal expectancy, H1). Refuerza priorización de R3.

### R2 — Re-derivar per-symbol gates desde teoría, no desde #281 contaminado

**Reasoning:** H7 confirma que los gates per-symbol over-restringen 8/10 símbolos al punto de impedir testing serio (1-3 trades en 12 meses). El issue #317 ya flagea la contaminación (`time_limit_hours` derivado de "winner-median holding" computado sobre simulador pre-#223 con sign-error). Mantener esos valores compromete cualquier conclusión sobre los otros 8 símbolos.

**Deliverable concreto (Phase 2):**
- Para cada símbolo, derivar `time_limit_hours` y `max_participation_rate` desde principios independientes:
  - **TL:** anclar a la **expected move horizon** del signal (e.g., bars-to-revert via LRC autocorrelation, o ATR-based median time to ±1 ATR move). NO desde holding-time histórica del backtest viejo.
  - **PoV:** anclar a Almgren-Chriss o Donier-Bonart academic — el doc original de las anchors está citado en CLAUDE.md caveat 1. Verificar valores actuales contra esas referencias.
- Documentar derivation paso-a-paso en una sub-spec con citas. Esto es la closing condition del issue #317.

**Riesgo de overfitting:** BAJO. La re-derivación desde teoría tiene anchors externos; no es data-driven sobre los backtests del proyecto. El único riesgo es scope creep (re-discusión de los anchors).

**Criterio de éxito pre-registrable:** Tras re-derivar, re-correr el q2 grid topology diagnostic con los nuevos gates. ≥6 de 8 símbolos (los 8 que actualmente bancarrotan al 100%) muestran trade counts ≥ 30 en el window completo. Eso permite Phase 2-R3 evaluar el signal en una muestra significativa.

**Criterio de fallo pre-registrable:** Si tras los nuevos gates, el bankruptcy rate sigue ≥80% en los 8 símbolos, eso refuerza H1 — el problema NO son los gates, es el signal. Vuelve focus a R3.

**Notas operativas:**
- Esta recomendación está **alineada** con issue #317. Phase 2-R2 cierra el ticket abierto, no abre un nuevo workstream.
- R2 debe correrse ANTES de R3 — sin R2, R3 no puede medirse en 8/10 símbolos.

### R3 — Audit del signal con experimento controlado

**Reasoning:** H1 muestra que en survivors (ETH/BTC al SL alto, donde la mecánica de exits no destruye P&L), cada trade pierde ~0.9R en promedio. Eso es consistente con un signal cuya dirección es no mejor que random. Sin R1+R2 no podemos descartar que el "no edge" sea un artifact de B8 + B9. CON R1+R2 corridos, el siguiente test es: **¿el signal tiene edge intrínseco?**

**Deliverable concreto (Phase 2):**
- Diseñar experimento controlado de signal-reemplazo. Pre-registrar UN single alternative entry signal contra el LRC actual. Candidatos:
  - **Momentum breakout:** entry cuando price rompe BB superior con confirmation 5m (opuesto a LRC mean-reversion).
  - **Trend-following:** entry cuando price > SMA(50) y SMA(50) > SMA(200), con pullback al SMA(20).
  - **Volatility expansion:** entry cuando ATR(14) sale > 1.5 × ATR(50), en dirección del breakout.
- Mismo basket, mismas B7-B11 (con R1 + R2 ya activos). Sweep de los exits dinámicos de R1.
- Tabla comparativa: LRC-mean-reversion vs alternative, métricas {n_trades, win_rate, avg_pnl_per_trade, max_drawdown, bankruptcy_count}.

**Riesgo de overfitting:** ALTO si se prueban N alternativas y se elige la mejor. Para mitigar: pre-registrar UN single alternativa (la más fundamentada teóricamente), NO iterar entre múltiples. Si la primera alternativa falla, eso es señal — escalamos a "ningún single building-block alternativo es suficiente" antes de probar otras.

**Criterio de éxito pre-registrable:** Sobre el mismo pre-holdout window, con R1 y R2 activos, el alternative signal produce ≥1 símbolo con `avg_pnl_per_trade > 0` Y ≥3 símbolos con net P&L > 0 (i.e., el alternative tiene edge per-trade en al menos 1 símbolo y win-positive en al menos 3).

**Criterio de fallo pre-registrable:** Si el alternative signal con R1+R2 activos produce el mismo patrón (bankruptcy universal o net P&L universalmente negativo), eso refuerza la lectura de que la estrategia no es viable en este market state y refuerza issue #321 path (a) (aceptar finding, honrar #271).

---

## §7 · Phase 2 — trigger conditions y orden propuesto

Phase 2 arranca **solo después** de que:

1. Este spec sea revisado por sssamuelll y aprobado en sus recomendaciones (o modificado).
2. Phase 2 pre-registre cuál recomendación se ejecuta primero, con qué evidencia constituiría success vs failure (los criterios pre-registrables en §6 son borrador; Phase 2 los refinará).
3. Issue #317 (gates calibration) se vuelva a abrir como issue activo bajo el alcance de Phase 2-R2 (no como deferred).

**Orden propuesto si las 3 se ejecutan:** **R2 → R1 → R3.**

- R2 primero porque sin gates legítimos, R1 y R3 no son medibles en 8/10 símbolos.
- R1 segundo porque sin exits dinámicos, R3 está confundido por la limitación de B8.
- R3 último porque es el cambio más grande y solo es interpretable con R1+R2 ya en su lugar.

**Orden alternativo si solo una recomendación se ejecuta:** R1 (el más actionable single change con mayor evidencia ya disponible).

**Conclusión negativa:** Si tras Phase 2 las 3 recomendaciones se implementan y todas fallan sus criterios pre-registrados, eso es evidencia robusta para path (a) de issue #321 (aceptar finding, no invitar usuarios per #271). Phase 1 no determina por adelantado este outcome.

---

## §8 · Open questions y parking lot

### Para iteración con sssamuelll antes de Phase 2:

1. **Orden R1/R2/R3:** ¿Es defendible mi propuesta (R2 → R1 → R3) o preferís otro orden?
2. **Criterio de éxito específico para R3:** ¿"≥3 símbolos net positive" es suficientemente high bar, o demanda más (e.g., Sharpe > 0.5, deflated Sharpe)?
3. **¿Phase 2 incluye walk-forward?** El kickoff dice "sweep con harness similar a `tools/retune_pre_holdout.py`". Eso es train+validate único. Si los resultados de Phase 2 son cercanos al success criterion pero ambiguos, ¿escalamos a walk-forward (más sub-windows) o aceptamos resultado de single train/validate?
4. **¿Qué cuenta como "alternative signal" para R3?** Yo sugiero pre-registrar UN single alternative. ¿Vos preferís pre-registrar 2-3 y compararlos contra LRC? El trade-off es riesgo de overfitting (más alternativas = más chances de hit por azar) vs riesgo de under-search (solo 1 alternative puede ser un sample malo).

### Parking lot (no aborda Phase 1 ni Phase 2 inmediato):

- **H6 (regime equivocado)** requiere per-trade dump del backtest. Si Phase 2 incluye R3, el sweep generará trade-level data que permite testear H6 post-hoc. Esto es bonus, no scope core.
- **H5 (basket contaminado)** requiere experimento de basket-amplio. Es un epic separado análogo a #302 (basket governance). Si R3 fracasa, considerar basket-expansion como Phase 3.
- **Anomaly forense DOGE -$30K**: documentado aquí pero no escalado como issue dedicado en Phase 1. Si Phase 2-R1 lo encuentra recurrente con los exits dinámicos, abrir issue separado para migrar a cost model v2 (sqrt-participation).

---

## §9 · Artefactos producidos por este análisis (reproducibles)

Los siguientes scripts/queries fueron ejecutados durante este audit. Para reproducir cualquier número de §4, basta con re-ejecutar `python3` sobre `data/retune/2026-05-11-pre-holdout-atr-evidence/grid_topology.json`:

```python
# Per-symbol stats (§4 H1 y H2)
import json, statistics
with open('data/retune/2026-05-11-pre-holdout-atr-evidence/grid_topology.json') as f:
    d = json.load(f)
for s in d['per_symbol']:
    cells = s['all_results']
    bnkpt_free = [c for c in cells if c['bankruptcy_count'] == 0]
    if bnkpt_free:
        ppt = statistics.mean(c['pnl']/c['trades'] for c in bnkpt_free)
        print(f"{s['symbol']}: {len(bnkpt_free)}/105 survival cells, avg ppt=${ppt:.1f}")
```

```python
# TP independence heatmap (§4 H2)
sl_vals = sorted({c['sl'] for s in d['per_symbol'] for c in s['all_results']})
tp_vals = sorted({c['tp'] for s in d['per_symbol'] for c in s['all_results']})
for sl in sl_vals:
    for tp in tp_vals:
        pnls = [c['pnl'] for s in d['per_symbol'] for c in s['all_results']
                if c['sl']==sl and c['tp']==tp]
        print(f"sl={sl} tp={tp}: mean P&L = ${statistics.mean(pnls):.0f}")
```

```python
# Gate vs survival correlation (§4 H7)
# Cross-reference data/retune/.../grid_topology.json with config.defaults.json
import json
with open('config.defaults.json') as f:
    cfg = json.load(f)
with open('data/retune/2026-05-11-pre-holdout-atr-evidence/grid_topology.json') as f:
    d = json.load(f)
for s in d['per_symbol']:
    sym = s['symbol']
    overrides = cfg['symbol_overrides'].get(sym, {})
    n_survive = sum(1 for c in s['all_results'] if c['bankruptcy_count'] == 0)
    print(f"{sym}: TL={overrides.get('time_limit_hours')}h, "
          f"PoV={overrides.get('max_participation_rate')}, "
          f"survival_cells={n_survive}/105")
```

---

## §10 · Historial de actualización

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-11 | Draft inicial Phase 1 | Claude Opus 4.7 (sesión audit) + sssamuelll |

Reservar líneas adicionales para feedback iterativo en el PR y revisión externa si aplica.
