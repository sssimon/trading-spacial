# Epic — Regime-allocation strategy pivot (post-R3 FAIL)

**Fecha:** 2026-05-13
**Status:** DRAFT — conditional on closure del A.4 (`2026-05-13-r3-fail-closure-path-a-honoring.md`) y approval de Simón
**Autor:** sssamuelll en colaboración con Claude Opus 4.7
**Tipo:** epic spec — propuesta de arquitectura estructuralmente distinta
**Trigger:** R3 FAIL (PR #336) + investigación externa 2026-05-13
**Prerequisito:** A.4 cierre formal (`2026-05-13-r3-fail-closure-path-a-honoring.md`) ratificado por Simón
**No es:** retry de la estrategia actual / H5 follow-up / parameter sweep adicional sobre arquitectura LRC

---

## 0 · Reconocimiento explícito del A.4 FAIL

**Este epic existe solo bajo la premisa de que A.4 ha sido cerrado formalmente por path (a) de #321.** El cierre lo formaliza el doc separado `2026-05-13-r3-fail-closure-path-a-honoring.md`. Este spec NO propone:

- Recovery de la estrategia LRC actual
- Modificación incremental de los parámetros existentes
- Tuning adicional sobre la arquitectura actual
- H5 (basket replacement) en el sentido del audit §6

Lo que SÍ propone:

- Arquitectura estructuralmente distinta: **regime-allocation trend-following multi-timeframe** estilo Zarattini 2025
- Reutiliza infraestructura existente (`backtest.py`, holdout isolation, bankruptcy handler, K-cap, methodology harness)
- Mantiene basket actual (10 símbolos) por decisión operacional, con caveat documentado de que la investigación sugiere lo contrario
- Honor estricto de #271, #246 y #322 (holdout intacto hasta validación documentada del nuevo strategy class)

---

## 1 · Resumen ejecutivo

Tras la cadena de fallas pre-registradas R1+R2+R3 sobre la arquitectura LRC mean-reversion / trend-pullback (1H signals, 5-14h holds, ATR exits, R-multiple sizing, basket de 10 altcoins), se propone un pivot estructural a la clase **regime-allocation multi-timeframe trend-following** modelada cercanamente sobre Zarattini, Pagani & Barbon (2025) "Catching Crypto Trends" — paper que documenta **Sharpe 1.58, CAGR 30%, Sortino 2.03, alpha +14% vs BTC** con ensemble de Donchian channels sobre top-20 más líquidos.

### Cambios estructurales clave vs arquitectura LRC

| Dimensión | LRC (FAILED) | Nuevo (proposed) |
|---|---|---|
| Signal | Single frame (LRC mean-reversion OR trend-pullback) | **Ensemble de Donchian channels** con lookbacks 5/10/20/30/60/90/150/250/360 días |
| Holding period | 5-14 horas (TL barriers) | **Días a meses** — emerge del ensemble; sin TL fijo |
| Sizing | R-multiple sobre SL distance (notional 2× capital con SL tight) | **Volatility-targeting** (position = target_vol / realized_vol) |
| Exits | ATR-based SL/TP + time-limit | **Signal-based** (ensemble flippea o weakens) |
| Regime gate | Daily composite (F&G + funding + price) → BULL/BEAR/NEUTRAL | **Implícito en ensemble** (long y short emergen del rotational portfolio); regime detector deprecated |
| Direction | LONG/SHORT regime-gated | **Bidirectional rotational** (long-short en cada timeframe del ensemble) |
| Basket | 10 altcoins (decidido por operator mantener) | 10 actuales (mismo, con caveat documentado) |
| Cost model | Lineal v1 | **Sqrt-participation v2 (Almgren-Chriss)** — prerequisite Phase 0 |
| Trigger granularity | 5m candle + RSI direction | Eliminado — entries en close de barra 1H (o frecuencia que se decida en Phase 2) |

### Expectativas de performance pre-registradas

Basado en literatura académica (NO en backtest interno):

| Tier | Sharpe target | Max DD target | Anchor |
|---|---:|---:|---|
| Minimum viable | > 0 (positive) | < 50% | Beat BTC B&H sobre misma ventana |
| Adequate | 0.8 - 1.2 | 30-40% | Hubrich 200-DMA filter |
| Strong | 1.2 - 1.5 | 25-35% | Mid-range trend funds |
| Aspirational | 1.5 - 1.8 | 20-30% | Zarattini 2025 territory |
| Above 2.0 | (sospechoso) | — | **Activar phantom-profit suspicion** |

---

## 2 · Contexto y justificación

### 2.1 Cadena de evidencia que motiva el pivot

Ver §2 del closure doc (`2026-05-13-r3-fail-closure-path-a-honoring.md`). Resumen: R1+R2+R3 FAIL, posterior P(viable bajo arquitectura actual) en 2-4%.

### 2.2 Por qué un epic separado y no recovery

El audit (#323) clasificó los hallazgos en 9 building blocks y testeó 8 hipótesis. La hipótesis confirmada con mayor evidencia (H1) es que el **signal direccional clásico — independiente del frame — no discrimina dirección con suficiente edge** en la basket actual a la granularidad probada.

Las recomendaciones R1/R2/R3 atacaron exits, gates y frame respectivamente. R3 específicamente probó el frame opuesto (momentum vs mean-reversion). Ambos -0.9R/trade. La conclusión empírica es que el cambio que falta NO es otro frame de signal direccional — es **otra clase de estrategia entera**: no predecir dirección move-by-move sino capturar moves grandes a través de un ensemble de horizontes con sizing vol-adjusted.

Esto NO viola §1.1 del R3 pre-reg ("NO retry with different signal") porque no estamos probando un signal alternativo en la misma arquitectura — estamos cambiando la arquitectura. Pero el espíritu del lock (acknowledge no edge en arquitectura actual) se respeta explícitamente en §0 y en el doc de cierre separado.

### 2.3 Por qué Zarattini 2025 como modelo

Es el paper más reciente (2025), survivorship-bias-free, multi-timeframe, con métricas concretas y replicables. Su arquitectura es el match más cercano a la intuición del operator ("operar en distintos momentos del mercado, no flipear long/short el mismo día"), pero recalibrada al horizonte que la literatura empírica indica (días-a-meses, no meses-a-trimestres).

Diferencias entre Zarattini y el resto:
- **vs Hubrich (200-DMA)**: Hubrich es BTC-only y single-signal. Demasiado simple para extraer edge en altcoins.
- **vs Liu-Tsyvinski (1-4 sem momentum)**: L/S momentum a 1-4 sem es un factor académico, no una estrategia operacionable; ranking weekly de 1827 coins no es retail-feasible.
- **vs Quantica/AHL (CTA generalistas)**: no son crypto-específicos, las allocations crypto no están públicas, y su universo (futuros líquidos globales) no se mapea 1:1.

---

## 3 · Tesis de la estrategia

### 3.1 Hipótesis central

> *En crypto, el edge sostenible no viene de predecir la próxima vela ni de timing intraday, sino de capturar moves grandes a través de un ensemble de horizontes (días-a-meses), con sizing ajustado por volatility realizada y sin gating regime explícito. La diversificación entre lookbacks es lo que produce edge robusto, no un single signal optimizado.*

### 3.2 Predicciones falsables (pre-registradas)

**P1**: Un ensemble de Donchian channels con lookbacks 5/10/20/30/60/90/150/250/360 días, con voting agregation y vol-targeting, produce avg P&L per trade > 0 en al menos 1 sub-window sobre BTC/ETH (los 2 símbolos con liquidez adecuada).

**P2**: El ensemble exhibe lower drawdown que cualquier single-lookback individual del ensemble (la diversificación entre timeframes reduce vol portfolio).

**P3**: Bajo cost model v2 (sqrt-participation), las catástrofes single-trade tipo DOGE -$30K NO se reproducen — los costos crecen con sqrt(notional/liquidity) en vez de lineal.

**P4**: En basket actual (10 símbolos), los 8 mid/small-cap probablemente **no** contribuyen edge positivo. Si P4 se confirma, abre formalmente la pregunta H5 (basket revision) bajo nuevo pre-reg.

### 3.3 Predicciones que llevarían a FAIL

- Si P1 no se cumple en ningún sub-window → el problema es upstream del ensemble (regime, basket entero, o crypto a este tamaño retail no admite trend-following).
- Si P3 no se cumple (catástrofes persisten bajo v2) → cost model v2 está mal calibrado, debe re-derivarse antes de cualquier sweep.
- Si Sharpe del ensemble es < 0.5 incluso con todos los building blocks correctos → el techo realista de la clase estrategia para retail-size es menor de lo que la literatura sugiere; reconsiderar viabilidad.

---

## 4 · Arquitectura propuesta

### 4.1 Universo (basket)

**Decisión operacional**: mantener los 10 símbolos actuales (BTC, ETH, ADA, AVAX, DOGE, UNI, XLM, PENDLE, JUP, RUNE) — `DEFAULT_SYMBOLS` en `btc_scanner.py:191-194`.

**Caveat documentado**: la investigación externa (Liu-Tsyvinski, Zarattini, industry slippage data) sugiere que 8 de los 10 (todos excepto BTC y ETH) son mid/small-cap con problemas estructurales de slippage que el cost model v2 puede mitigar pero no eliminar.

**Mitigación**: Phase 3 sweep reportará per-symbol attribution. Si P4 se confirma (los 8 no contribuyen), eso re-abre H5 bajo nuevo pre-reg fuera del scope de este epic — NO bajo este epic, para preservar la decisión operacional inicial.

**Por qué no top-20 Zarattini exact**: el operator eligió mantener continuidad con #135. La pérdida en fidelidad al benchmark se compensa con mayor auditabilidad de la transición.

### 4.2 Signal — ensemble de Donchian channels

**Definición individual de Donchian channel (lookback N)**:
- `upper(t) = max(high[t-N:t])`
- `lower(t) = min(low[t-N:t])`
- Signal LONG si `close(t) > upper(t-1)` (breakout alcista)
- Signal SHORT si `close(t) < lower(t-1)` (breakout bajista)
- Else: hold previous direction (or flat if first signal)

**Lookbacks del ensemble** (Zarattini exactos): **5, 10, 20, 30, 60, 90, 150, 250, 360 días**.

**Aggregation** (decisión abierta para §8):
- Opción A (equal-weight vote): `n_long = sum(signal_i == LONG)`, position direction = sign(n_long - n_short).
- Opción B (signal-strength weighted): pesos según `(close - midchannel) / channel_width`.
- Opción C (regime-conditional weights): pesos mayores a lookbacks largos en momentos de baja vol, mayor peso a cortos en alta vol.

**Granularidad de evaluación**: 1H (las velas siguen siendo 1H para mantener compatibilidad con infraestructura existente; lookbacks se computan agregando 1H → daily).

**Position update frequency** (decisión abierta para §8):
- Diario (calcula ensemble una vez al día, posición se sostiene)
- Por barra (1H) con threshold de cambio (ej. cambio de posición solo si voting result varía ≥ 2 signals)

### 4.3 Sizing — volatility-targeting

**Per-symbol position size**:
```
target_vol_per_symbol = portfolio_vol_target / n_active_symbols
position_size_USD = capital × target_vol_per_symbol / realized_vol_30d
```

**Parámetros tentativos** (pre-Phase 2 calibration):
- `portfolio_vol_target`: 30% anualizado (lower end of research range)
- `realized_vol`: 30-day exponentially-weighted std de daily returns
- `n_active_symbols`: dynamic (entre 1 y 10 según cuántos tengan signal != flat)

**Hard caps**:
- Position size ≤ 20% of capital per symbol (límite operacional)
- Position size ≥ $50 (mínimo de Binance per symbol)
- Sum of |positions| ≤ 200% of capital (leverage cap, asumiendo perp markets)

**Eliminación explícita de R-multiple sizing**. NO se usa `risk_amount × 100 / sl_pct_actual`. NO se usa SL distance para determinar notional. Esto es estructural — la dimensión que más mata según H4 del audit.

### 4.4 Exits — signal-based

**Exit triggers**:
1. **Signal flip**: posición LONG se cierra cuando voting result flip a SHORT (o flat si voting passa el threshold). Idem inverso para SHORT.
2. **Vol spike emergency**: si realized_vol_intraday > 3 × realized_vol_30d, reducir posición a 50% (or close, dependiendo de tier de aggressiveness).
3. **No fixed SL ni TP**. Eliminados estructuralmente.
4. **No fixed time-limit**. Eliminado estructuralmente.

**Mecanismos que se mantienen**:
- Bankruptcy halt (#280) — capital effective ≤ $1K → halt symbol
- K-cap (#309) — cap close-side overshoot at 10× SL distance equivalente (aunque sin SL fijo, K-cap se aplica en términos de % move dramático)

### 4.5 Cost model v2 — prerequisite

`costs_calibration.json` ya tiene plan de migrar a sqrt-participation Almgren-Chriss. **Este epic lo convierte en bloqueante Phase 0**.

Formula propuesta:
```
slippage_bps = base_bps + size_factor × sqrt(notional / liquidity_per_min)
```

vs lineal actual:
```
slippage_bps = base_bps + size_factor × (notional / liquidity_per_min)
```

La diferencia crítica: en lineal, single-trade en barras de baja liquidez explota sin techo. En sqrt, el growth es subaddicional, lo que matchea mejor la mecánica empírica de market impact (Almgren-Chriss anchors academic).

Calibración: anchored a referencias académicas (Almgren-Chriss 2001, Donier-Bonart). NO data-fit sobre nuestro propio historial (eso es leakage).

### 4.6 Regime detector — deprecated

El `detect_regime()` actual (F&G + funding + price compuesto, threshold 60/40) NO se usa en el nuevo strategy class. Razones:

1. H3 del audit confirmó que no discrimina outcomes en window evaluado.
2. El ensemble multi-timeframe captura "regime" implícitamente — lookbacks largos solo emiten signal cuando hay tendencia macro persistente.
3. Reduce degrees of freedom (un componente menos que tunar / over-fit).

**Implicación de código**: `strategy/regime.py` queda como módulo legacy, marcado deprecated en docstring. NO eliminado (puede servir para diagnostic post-hoc), pero NO consumido por el nuevo path.

---

## 5 · Pre-registración de benchmarks

Estos se pre-registran AHORA en este spec. NO se modifican después.

### 5.1 Floor benchmark (must beat)

**BTC buy-and-hold sobre la misma ventana pre-holdout** `[2024-01-30, 2025-04-29]`.

- Métrica: total return
- Source: precio BTC en `data/cache/BTCUSDT/1h.csv` (reuse existing OHLCV cache)
- Criterio mínimo: el strategy nuevo debe beat BTC B&H sobre la misma ventana, net of all costs (v2 cost model). Si no, no hay justificación económica.

### 5.2 Academic baseline (should beat)

**Hubrich 200-DMA filter sobre BTC**: long BTC cuando close > 200-day SMA, cash otherwise.

- Métrica: Sharpe annualized, Calmar (CAGR/MaxDD)
- Source: replicable trivialmente desde OHLCV cache
- Criterio: el nuevo strategy debe beat Hubrich-style 200-DMA en Sharpe sobre la misma ventana. Demuestra que la complejidad del ensemble agrega valor sobre la regla más simple posible.

### 5.3 Aspirational ceiling

**Zarattini paper performance**: Sharpe 1.58, CAGR 30%, Sortino 2.03, MaxDD undocumented pero anchored al artículo.

- No pretendemos igualar porque:
  - Su universo es top-20 rotational, no nuestros 10 fijos
  - Su data es 2015+, nuestra ventana es 15 meses
  - Sus costos no son los nuestros
- Anchor: si nuestro Sharpe está dentro del 60% del de Zarattini (≥ 0.95), consideramos validation strong. Si está dentro del 40% (≥ 0.63), validation adequate.

### 5.4 Internal control

**Strategy LRC actual sobre la misma ventana** (ya documented en R1/R2/R3 evidence).

- Effective annualized return: ~-95% (bankruptcy floor 8/10 + ~-9% en BTC/ETH)
- Cualquier resultado del nuevo strategy con avg P&L per trade > -0.1R representa improvement material, pero NO se considera success — solo beats el strategy archived.

---

## 6 · Criterios de éxito pre-registrados

Estos se pre-registran AHORA. Se evalúan al final de Phase 3 (pre-holdout sweep). NO se modifican después.

### 6.1 Criterio PRIMARY (decide PASS / FAIL del epic)

**Beat BTC B&H sobre la ventana pre-holdout en total return, net of v2 costs, sobre el portfolio agregado (suma de los 10 símbolos)**.

Si PASS:
- Strategy class validado emipricamente — proceder a Phase 4 (paper trade) y luego Phase 5 (holdout evaluation).

Si FAIL:
- Strategy class no validado.
- NO ejecutar Phase 5 (holdout).
- Open question explícita: ¿es viable retail trend-following crypto a nuestro tamaño/basket? Documenta como hallazgo, considera close del epic.

### 6.2 Criterios SECONDARY (informativos)

- **S1 — Sharpe ratio**: target ≥ 0.8 (adequate tier). Sharpe < 0.5 = warning flag (strategy class no rinde bien para retail).
- **S2 — Max DD**: target < 40%. MaxDD > 60% = warning flag (vol-targeting no funcionando como esperado).
- **S3 — Per-symbol attribution**: count de símbolos con P&L positivo individual. Si BTC y ETH son los únicos positivos, refuerza P4 (basket revision needed in future epic).
- **S4 — Bankruptcy events**: target = 0 bajo v2 costs. Si > 0, cost model v2 no está mitigando lo suficiente.
- **S5 — Ensemble diversification**: verificar que el ensemble (9 lookbacks) tiene lower drawdown que el mejor single-lookback. Si no, la diversificación es ilusoria.

### 6.3 Halt conditions durante Phase 3 sweep

Pre-registradas para evitar overfit en sub-window selection:

- **H1 — Universal bankruptcy**: si > 80% de símbolos bancarrotan en > 50% de cells, halt y reportar (matches R3 pattern, indica falla estructural más profunda).
- **H2 — Degenerate signal**: si avg trades/symbol < 10 en > 50% de cells, halt y reportar (ensemble sobre-filtrando, posiblemente mis-calibrado).

---

## 7 · Fases de implementación

### Phase 0 — Cost model v2 prerequisite

**Pre-requisite hard. NO comenzar Phase 1 sin Phase 0 completado.**

- Issue: #317 (re-abierto / scope updated)
- Deliverable: `backtest_costs.py` migrated a sqrt-participation Almgren-Chriss
- Acceptance:
  - Forensic case DOGE -$30K (sl=0.7) reproducido bajo v1, confirmed mitigated bajo v2
  - Calibration anchored a Almgren-Chriss 2001 + Donier-Bonart (citas explícitas en `costs_calibration.json`)
  - Tests parity para casos no-extremos (v1 y v2 deben dar resultados similares para trades de tamaño moderado)
- Estimated effort: 1-2 sesiones
- Blocker: ninguno (independiente de strategy code)

### Phase 1 — Implementation harness

Implementar el nuevo signal + sizing path detrás de feature flag `cfg.regime_allocation_enabled` (default False).

- New module: `strategy/donchian_ensemble.py`
  - `compute_donchian_signal(df, lookback_days) -> {direction, breakout_strength}`
  - `aggregate_ensemble(signals: List[Dict], aggregation_method: str) -> {direction, confidence}`
- New module: `strategy/vol_targeting.py`
  - `compute_position_size(capital, target_vol_per_symbol, realized_vol_30d, hard_caps) -> position_usd`
- Modify: `strategy/core.py`
  - Add branch: if `cfg.regime_allocation_enabled`, call new ensemble + vol-targeting instead of LRC + R-multiple path
  - LRC path remains byte-identical when flag off
- Modify: `backtest.py`
  - Accept ensemble signal in addition to current SignalDecision
  - Position sizing via vol_targeting when flag on
  - Exits: signal-flip detection instead of SL/TP barriers when flag on

**Tests (TDD)**:
- `tests/test_donchian_ensemble.py` — 15+ tests (single channel, ensemble aggregation, edge cases)
- `tests/test_vol_targeting.py` — 10+ tests (sizing math, caps, edge cases)
- `tests/test_regime_allocation_integration.py` — 10+ tests (flag-off byte-identical, flag-on uses ensemble, integration through evaluate_signal)
- All existing tests pass (no live-path regression)

- Estimated effort: 3-5 sesiones
- Blocker: Phase 0 complete

### Phase 2 — Pre-registration sub-spec

Mirror pattern de R1/R2/R3 pre-regs. Documento al estilo de `2026-05-13-r3-trend-pullback-pre-reg.md`.

- Lock all parameter values BEFORE running sweep:
  - Exact lookback list (probablemente Zarattini exactos: 5/10/20/30/60/90/150/250/360)
  - Aggregation method (probablemente equal-weight vote para evitar over-fit)
  - Position update frequency (probablemente daily)
  - Portfolio vol target (probablemente 30%)
  - Hard caps (per-symbol max 20%, leverage 2x, etc.)
- Lock 3 sub-windows del sweep (matching R3 pattern para comparability):
  - Window A: bear 2022 (`[2022-01-01, 2022-04-01]`)
  - Window B: recovery 2023 (`[2023-04-01, 2023-07-01]`)
  - Window C: recent 2025 (`[2025-01-30, 2025-04-29]`)
- Pre-register success/halt criteria del §6 explícitamente
- Operator approval requerido antes de Phase 3

- Estimated effort: 1 sesión (similar a R3 pre-reg)
- Blocker: Phase 1 complete

### Phase 3 — Sweep + verdict

Mirror pattern de R3 sweep tool. Adaptar `tools/r3_trend_pullback_sweep.py` → `tools/regime_allocation_sweep.py`.

- Sweep grid: typically 1 cell per (symbol, sub-window) cuando params están locked. La "variation" es entre símbolos y sub-windows, no entre params.
- Per-symbol metrics: avg_pnl_per_trade, n_trades, win_rate, max_dd, sharpe
- Portfolio aggregate: same metrics aggregated
- Output: `data/retune/2026-MM-DD-regime-allocation/{sweep_results_{A,B,C}.json, verdict.json, derivation_audit.md}`
- Verdict comparison vs benchmarks §5
- Halt check vs §6.3

- Estimated effort: 2-3 sesiones
- Blocker: Phase 2 approved

### Phase 4 — Paper trade / shadow mode

Si Phase 3 PASS primary criterion, NO promover directly to live. Paper trade primero.

- Run new strategy en shadow mode en producción (logs only, no actual orders) por 30-60 días
- Compare paper trade results vs Phase 3 backtest expectations
- Check drift, costs realized vs modeled, execution issues
- Operator decision: promover a live, mantener paper, or close epic

- Estimated effort: 30-60 días calendario
- Blocker: Phase 3 verdict PASS

### Phase 5 — Holdout evaluation (bala única)

Si Phase 4 confirma resultados, **única ejecución** sobre `data/holdout/`.

- Window: `[2025-04-30, 2026-04-30]` (12 meses locked)
- Pre-register criteria de PASS explícitamente in Phase 4 closing doc
- Si PASS: strategy validated, #271 guardrail evaluable for relaxation
- Si FAIL: strategy archived; NO retry sobre holdout (single shot)

- Estimated effort: 1 sesión
- Blocker: Phase 4 success criteria met

### Phase 6 — Live promotion (conditional)

Solo si holdout passes. Requiere comunicación a Simón + revisor externo + decisión sobre #271.

---

## 8 · Open questions for operator review (pre-Phase 1)

Las siguientes decisiones deben locked antes de empezar Phase 1 implementation:

1. **§9.1 — Aggregation method del ensemble**
   - (a) Equal-weight vote (Recommended — minimum degrees of freedom)
   - (b) Signal-strength weighted
   - (c) Regime-conditional weights

2. **§9.2 — Position update frequency**
   - (a) Daily (Recommended — match Zarattini, lower turnover, lower costs)
   - (b) Per 1H bar with threshold

3. **§9.3 — Portfolio vol target**
   - (a) 30% (Recommended — conservative anchor)
   - (b) 35%
   - (c) 40% (aggressive)

4. **§9.4 — Lookback list**
   - (a) Zarattini exact 5/10/20/30/60/90/150/250/360 (Recommended)
   - (b) Subset (e.g., 10/30/90/180 — 4 lookbacks, lower degrees of freedom)

5. **§9.5 — Short enabled**
   - (a) Yes — bidirectional rotational como Zarattini (Recommended)
   - (b) Long-only (more conservative, easier to operate)

6. **§9.6 — Hard cap leverage**
   - (a) 1× (no leverage, sum |positions| ≤ capital)
   - (b) 2× (sum |positions| ≤ 2 × capital)

7. **§9.7 — Compute budget Phase 3**
   - Default: same hardware/wallclock que R3 sweep (~30-60 min)

---

## 9 · Risk register

### R1 — Basket inadequacy (HIGH probability)

- **Risk**: research dice que 8 de 10 son problemáticos por slippage. Operator eligió mantener para preservar continuidad.
- **Impact**: Phase 3 puede confirmar que solo BTC/ETH contribuyen positivo, refuerza P4 del §3.2.
- **Mitigation**: per-symbol attribution reporting en Phase 3 + S3 secondary criterion. Si confirmado, abrir nuevo epic para basket revision (NO bajo este epic).

### R2 — Zarattini in-sample bias (MEDIUM probability)

- **Risk**: el paper Zarattini reporta backtest desde 2015. Sus números podrían no generalizar a 2024-2025 regime.
- **Impact**: nuestro Sharpe podría ser sub-1.0 incluso con implementación perfecta.
- **Mitigation**: 3 sub-windows del sweep cubren regimes distintos (bear 2022, recovery 2023, recent 2025). Walk-forward implícito.

### R3 — Cost model v2 fitting risk (MEDIUM probability)

- **Risk**: calibrar Almgren-Chriss tiene degrees of freedom propios (`base_bps`, `size_factor` por tier). Mal calibrado, podría sub-estimar costos.
- **Impact**: Phase 3 reporta Sharpe inflado.
- **Mitigation**: anchor a referencias académicas explícitas en `costs_calibration.json`. Cross-validate con casos forensic conocidos (DOGE -$30K bajo v1 debe quedar atenuado a -$5K-$10K bajo v2, no eliminado).

### R4 — Ensemble overfitting (LOW probability)

- **Risk**: 9 lookbacks × aggregation × vol target × rebalance freq = potencialmente sobre-parametrizado.
- **Impact**: Phase 3 muestra Sharpe alto in-sample, Phase 5 (holdout) muestra deterioro.
- **Mitigation**: pre-registrar exactos params Zarattini, NO tune internamente. 3 sub-windows independientes para detectar overfit.

### R5 — Operator expectation mismatch (MEDIUM probability)

- **Risk**: operator esperaba "regime hold meses". Architecture es "ensemble días-a-meses". La diferencia debe quedar explícita en comunicación.
- **Impact**: confusion post-Phase 3 si la distribución de hold periods no matchea la expectativa.
- **Mitigation**: este spec lo documenta explícitamente §1; metrics reporting incluye distribución de hold periods en Phase 3 output.

### R6 — Sharpe ceiling realism (MEDIUM probability)

- **Risk**: literatura indica ceiling realista 1.5-1.8 para retail-size trend-following crypto. Operator podría esperar más.
- **Impact**: Phase 3 PASS criterio primary pero secondary S1 sub-1.0 → epic considered "barely viable".
- **Mitigation**: criterios pre-registrados §6 explícitos. Adequate tier (Sharpe 0.8-1.2) es aceptable bar.

### R7 — 2024 was "Bitcoin year" (MEDIUM probability)

- **Risk**: industry observation: en 2024, diversificación crypto destruyó valor vs BTC B&H pure. Si window pre-holdout incluye mucho de 2024, el strategy podría parecer "mal" relativo a BTC simple.
- **Impact**: criterio primary (beat BTC B&H) podría FAIL even con strategy buena.
- **Mitigation**: report BTC-only attribution alongside basket-wide. Si BTC attribution alone beats B&H pero basket diversification drags, eso es informational, not necessarily epic FAIL.

### R8 — Methodology infrastructure regression (LOW probability)

- **Risk**: implementar nuevo strategy class sin romper holdout isolation tests / pre-reg discipline.
- **Impact**: contamina futura validación.
- **Mitigation**: feature flag default OFF. All new modules deben pasar `tests/test_holdout_isolation.py` (whitelisted modules updated explicitly with justification).

---

## 10 · Relación con epics y issues existentes

### Cerrados / archived antes de empezar este epic

- **#321** — closed via path (a), formalized en `2026-05-13-r3-fail-closure-path-a-honoring.md`
- **#316** — inflexión metodológica → finding confirmado empíricamente, closed
- **#323** — structural audit → R1/R2/R3 all FAILed, closed con verdict acumulado
- **#322** — holdout block durante A.4 → resolved (A.4-3 cancelado)

### Open / enforced indefinidamente bajo arquitectura LRC

- **#271** — user invitation guardrail. **Re-evaluable solo si este epic passes Phase 5 (holdout)**.
- **#246** — holdout dataset locked. Sigue protected; este epic propone touch solo en Phase 5 si todas las phases previas pasan.

### Re-scoped por este epic

- **#317** (cost model v2 deferred) — convertido en prerequisite Phase 0 hard. NO opcional.
- **#135** (basket curation) — mantiene scope actual. NO se modifica bajo este epic. Caveat documentado §4.1 + §9 R1.

### Nuevos issues proyectados

- Issue tentativo (TBD-1) — "Phase 0: cost model v2 sqrt-participation migration". Scoping post-spec approval.
- Issue tentativo (TBD-2) — "Phase 1: regime-allocation strategy implementation behind feature flag". Scoping post-Phase 0.
- Issue tentativo (TBD-3) — "Phase 2: pre-registration sub-spec". Pre-reg doc, no code.
- Issue tentativo (TBD-4) — "Phase 3: sweep execution + verdict".
- Issue tentativo (TBD-5) — "Phase 4: paper trade in shadow mode".
- Issue tentativo (TBD-6) — "Phase 5: holdout evaluation (bala única, conditional)".

---

## 11 · Fuentes y referencias

### Académicas (live-verified 2026-05-13)

- Zarattini C., Pagani A., Barbon A. (2025). *Catching Crypto Trends: A Tactical Approach for Bitcoin and Altcoins*. SSRN Working Paper 5209907. [https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5209907](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5209907)
- Liu Y., Tsyvinski A. (2021). *Risks and Returns of Cryptocurrency*. Review of Financial Studies 34(6), 2689-2727. [https://academic.oup.com/rfs/article-abstract/34/6/2689/5912024](https://academic.oup.com/rfs/article-abstract/34/6/2689/5912024). SSRN preprint: 3226952.
- Hubrich S. (2017). *'Know When to Hodl 'Em, Know When to Fodl 'Em': An Investigation of Factor Based Investing in the Cryptocurrency Space*. SSRN 3055498. [https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3055498](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3055498)
- Giudici P. et al. (2020). *A hidden Markov model to detect regime changes in cryptoasset markets*. Quality and Reliability Engineering International. Wiley. [https://onlinelibrary.wiley.com/doi/abs/10.1002/qre.2673](https://onlinelibrary.wiley.com/doi/abs/10.1002/qre.2673)
- *Bitcoin Price Regime Shifts: A Bayesian MCMC and Hidden Markov Model Analysis of Macroeconomic Influence*. MDPI Mathematics, 2025. [https://www.mdpi.com/2227-7390/13/10/1577](https://www.mdpi.com/2227-7390/13/10/1577)
- Almgren R., Chriss N. (2001). *Optimal Execution of Portfolio Transactions*. Journal of Risk 3, 5-39. (Anchor para cost model v2)

### Industry / practitioner (live-verified 2026-05-13)

- Hedgeweek (2024). *Crypto Hedge Fund Returns Trail Bitcoin in 2024*. [https://www.hedgeweek.com/bitcoin-surges-ahead-of-crypto-in-2024/](https://www.hedgeweek.com/bitcoin-surges-ahead-of-crypto-in-2024/)
- Quantica Capital Quarterly Insights Q1 2025. *When Trend-Following Hits Capacity*. [https://quantica-capital.com/en/publication/qi-2025Q1](https://quantica-capital.com/en/publication/qi-2025Q1)
- VisionTrack Crypto Hedge Fund Composite Index (2024 returns). Cited via Hedgeweek + CoinShares.
- Yahoo Finance (2025). *Crypto Hedge Funds Had Great 2024, But Failed to Top Bitcoin*. [https://finance.yahoo.com/news/crypto-hedge-funds-had-great-050000228.html](https://finance.yahoo.com/news/crypto-hedge-funds-had-great-050000228.html)

### Internal (cross-referenced)

- `docs/superpowers/specs/es/2026-05-11-a4-hallazgo-inflexion-metodologica.md`
- `docs/superpowers/specs/es/2026-05-11-strategy-structural-audit.md`
- `docs/superpowers/specs/es/2026-05-13-r3-fail-closure-path-a-honoring.md`
- `data/retune/2026-05-12-r1-dynamic-exit/derivation_audit.md`
- `data/retune/2026-05-11-r2-gates/derivation_audit.md`
- `data/retune/2026-05-13-r3-trend-pullback/derivation_audit.md`

---

## 12 · Historial de revisiones

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-13 | Initial draft post-R3 FAIL, post-research externa | sssamuelll + Claude Opus 4.7 |
| TBD | Operator review feedback incorporado | sssamuelll |
| TBD | Simón review feedback incorporado | sssamuelll |
| TBD | §8 questions resolved → spec finalized | sssamuelll |
| TBD | Phase 0 issue created | sssamuelll |
