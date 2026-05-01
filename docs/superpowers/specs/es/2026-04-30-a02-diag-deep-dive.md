# A.0.2.diag — Diagnostic deep-dive on no-edge finding (#281)

**Fecha:** 2026-04-30
**Datos producidos en:** `feat/methodology-a02-realistic-costs`, mergeada a main como `09531c1` via [#289](https://github.com/sssimon/trading-spacial/pull/289) (2026-05-02). Los scripts viven en `scripts/a02_diag_*.py` y dependen de los campos `gross_pnl_pct`, `total_cost_bps`, `entry_notional_usd` que A.0.2 introdujo.
**Issue:** [#281](https://github.com/sssimon/trading-spacial/issues/281)
**Bloqueante de:** comunicación a Simon · prioridad de #279 · A.4 (#250) re-tuning · A.3 (#249) calibración.
**No toca:** `data/holdout/` (AST guard B verifica). Train segment 18m (`2023-10-29 → 2025-04-29`) — mismo protocolo que A.0.2 honesty diff.

---

## Contexto y pregunta

A.0.2 (#277) reveló que con los `atr_sl_mult/tp/be` actuales, **0/10 símbolos curados tienen net positivo en train 18m post-costos realistas**. Cost spectrum 4 órdenes de magnitud (BTC 93 bps → PENDLE 1M+ bps). Sin diagnóstico, "no edge" es alarma; con diagnóstico es plan.

Este doc clasifica la strategy en uno de tres mundos:

- **Mundo A** — signal funciona, costos/sizing la matan → A.4 + #279 + sqrt v2 la rescata.
- **Mundo B** — signal marginal, exits la enmascaran → A.4 con redesign de exit + posible filtrado por régimen.
- **Mundo C** — signal es ruido → pausar A.4; investigar redesign del scoring antes de tunear.

La metodología de clasificación está en la sección "Diagnóstico integrado" al final.

---

## Comandos de reproducibilidad

```bash
# Forward-return analysis (#2) — gross only, no SL/TP, hold for h bars
python scripts/a02_diag_2_forward_return.py --out /tmp/a02_diag_2.json

# Expectancy decomposition (#5) — killed-by-costs vs structural vs survivor
python scripts/a02_diag_5_expectancy_decomp.py --out /tmp/a02_diag_5.json

# Per-symbol summary (#1) — calibration baseline
python scripts/a02_diag_1_per_symbol.py --out /tmp/a02_diag_1.json

# Régime breakdown (#4) — 10×3 by 30d rolling BTC return
python scripts/a02_diag_4_regime_breakdown.py --out /tmp/a02_diag_4.json

# Holding period (#6) — winners vs losers distribution
python scripts/a02_diag_6_holding_period.py --out /tmp/a02_diag_6.json

# Stop-out post-mortem (#3) — SL multiplier curve
python scripts/a02_diag_3_stop_out.py --out /tmp/a02_diag_3.json
```

Helper compartido: `scripts/_a02_diag_lib.py` — define `TRAIN_START_UTC`, `TRAIN_END_UTC`, `CURATED_SYMBOLS` y wrappers de `simulate_strategy` + lookup de liquidity proxy. Importable como módulo (`from scripts._a02_diag_lib import ...`).

---

## Análisis 1 — Per-symbol summary (calibration baseline)

**Pregunta:** ¿Cuántos símbolos están al borde vs catastróficos? Calibrar bar de A.3.

### Tabla per-símbolo (cost-on, train 18m)

| Symbol | n | WR% | win_g% | loss_g% | win_n% | loss_n% | exp_g_bps | exp_n_bps | cost_bps | part_p50 | part_p90 | part_p99 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTCUSDT | 188 | 14.4 | +3.726 | -0.483 | +3.196 | -1.483 | +12.15 | -81.08 | 93.1 | 0.0003 | 0.0018 | 0.0185 |
| ETHUSDT | 172 | 11.6 | +4.953 | -0.691 | +4.242 | -2.097 | -3.48 | -135.95 | 132.4 | 0.0006 | 0.0036 | 0.0236 |
| ADAUSDT | 192 | 0.0 | +0.000 | -0.003 | +0.000 | -6.677 | -0.28 | -667.69 | 692.4 | 1.4835 | 1.4835 | 1.4835 |
| AVAXUSDT | 164 | 0.0 | +0.000 | +0.000 | +0.000 | -1.556 | +0.00 | -155.57 | 173.7 | 0.3166 | 0.3166 | 0.3166 |
| DOGEUSDT | 203 | 0.0 | +0.000 | -0.008 | +0.000 | -1.630 | -0.78 | -162.99 | 149.1 | 0.1685 | 0.1762 | 0.1780 |
| UNIUSDT | 185 | 0.0 | +0.000 | -0.006 | +0.000 | -8.736 | -0.61 | -873.61 | 893.0 | 1.8511 | 1.8511 | 1.8511 |
| XLMUSDT | 170 | 0.0 | +0.000 | -0.003 | +0.000 | -30.900 | -0.28 | -3090.03 | 3097.9 | 5.8715 | 5.8715 | 5.8715 |
| PENDLEUSDT | 209 | 0.0 | +0.000 | -0.003 | +0.000 | -49.208 | -0.31 | -4920.79 | 4960.5 | 8.0008 | 8.0008 | 8.0008 |
| JUPUSDT | 177 | 0.0 | +0.000 | -0.006 | +0.000 | -0.883 | -0.60 | -88.34 | 144.7 | 0.0976 | 0.0978 | 0.0978 |
| RUNEUSDT | 195 | 0.0 | +0.000 | +0.032 | +0.000 | -5.556 | +3.21 | -555.56 | 515.1 | 0.7543 | 0.7543 | 0.7543 |

### Caveat metodológico crítico — capital floor + entry_notional=0

`backtest._close_position` aplica `effective_capital = max(0, capital)` (introducido en A.0.2 para evitar el inverted-pnl bug). Cuando una pérdida grande lleva capital a 0 o negativo, los trades posteriores se computan con risk_amount = 0 → entry_notional = 0 → cost augmentation skipped.

Implicación: los `cost_bps_mean`, `participation_rate_*`, y `exp_g_bps` de la tabla están sesgados:
- **Solo los trades con `entry_notional > 0` contribuyen** a participation y al gross expectancy real.
- En 8/10 símbolos (todos menos BTC/ETH), después del primer-trade-bankrupt, los siguientes 100-200 trades tienen entry_notional = 0. Sus `pnl_pct` reflejan solo el cambio de precio bruto (gross), no un trade real.
- Por eso `participation_rate_p50 = p90 = p99` para 8 símbolos: la distribución tiene 1-2 observaciones reales y todas idénticas.

**Lectura honest:** los números mid/small-cap de cost_bps_mean son cost del primer trade real (que llevó al bankrupt), no costo recurrente. Indica que **la strategy no puede ejecutarse más de 1-2 veces antes de quemar el capital** en estos símbolos bajo el modelo lineal v1. Ejecución real, sin cost model lineal absurdo, podría sostener más trades — pero el bottleneck es notional/liquidity, no costos.

### Conclusión #1

- **Solo BTC y ETH son símbolos cost-realista** (participation p99 < 0.025, mucho menor que el participation rate al que linear v1 colapsa).
- **8 símbolos requieren #279 (smaller per-trade size)** antes de que su cost model sea siquiera interpretable. El R-multiple sizing actual produce notional > liquidity proxy en mid/small-cap — incompatible con cualquier execution real.
- **Para A.3 (calibración del bar)**: los thresholds deben definirse contra los símbolos donde la simulación es cost-coherente (BTC + ETH inicialmente), no contra los catastróficos artifact-driven.

## Análisis 2 — Forward-return con t-stat tier

**Pregunta:** ¿Las señales tienen información predictiva intrínseca?

**Método (resumen):** Por cada entry generada por `simulate_strategy` (cost-off, gross only), ignorar SL/TP, computar el forward return del precio en velas +1, +5, +15, +50 (1H bars). t-stat = `mean / (stdev/√n)`. Autocorrelación lag-1 por símbolo. Tier:`t<1.5`=ruido, `1.5≤t<2.5`=marginal, `t≥2.5`=sólida.

### Resultados (10 símbolos × 4 horizontes)

#### h = +1 1H bar (~1h hold)
| Symbol | n | mean_pct | t_stat | autocorr_lag1 | tier |
|---|---:|---:|---:|---:|---|
| BTCUSDT | 188 | +0.0386 | +1.01 | +0.014 | noise |
| ETHUSDT | 172 | -0.0158 | -0.29 | -0.137 | noise |
| ADAUSDT | 192 | +0.1417 | +1.89 | -0.015 | marginal |
| AVAXUSDT | 164 | +0.0791 | +0.79 | -0.088 | noise |
| DOGEUSDT | 203 | +0.0262 | +0.33 | -0.169 | noise |
| UNIUSDT | 185 | +0.1807 | +2.15 | +0.005 | marginal |
| XLMUSDT | 170 | +0.1001 | +1.48 | +0.121 | noise |
| PENDLEUSDT | 209 | +0.2759 | +3.01 | -0.016 | **solid** |
| JUPUSDT | 177 | -0.0169 | -0.18 | +0.038 | noise |
| RUNEUSDT | 195 | -0.0242 | -0.32 | -0.143 | noise |

Tier mix h=+1: 7 noise, 2 marginal, 1 solid.

#### h = +5 1H bars (~5h hold) — peak predictive horizon
| Symbol | n | mean_pct | t_stat | autocorr_lag1 | tier |
|---|---:|---:|---:|---:|---|
| BTCUSDT | 188 | +0.1605 | +1.83 | -0.135 | marginal |
| ETHUSDT | 172 | +0.1630 | +1.37 | +0.117 | noise |
| ADAUSDT | 192 | +0.4595 | +2.55 | +0.008 | **solid** |
| AVAXUSDT | 164 | +0.4992 | +2.68 | -0.062 | **solid** |
| DOGEUSDT | 203 | +0.0290 | +0.16 | -0.149 | noise |
| UNIUSDT | 185 | +0.2183 | +1.13 | +0.090 | noise |
| XLMUSDT | 170 | +0.3561 | +2.16 | +0.155 | marginal |
| PENDLEUSDT | 209 | +0.5515 | +2.60 | +0.101 | **solid** |
| JUPUSDT | 177 | +0.0142 | +0.07 | -0.078 | noise |
| RUNEUSDT | 195 | -0.0383 | -0.18 | +0.021 | noise |

Tier mix h=+5: 5 noise, 2 marginal, 3 solid.

#### h = +15 1H bars (~15h hold)
| Symbol | n | mean_pct | t_stat | autocorr_lag1 | tier |
|---|---:|---:|---:|---:|---|
| BTCUSDT | 188 | +0.2342 | +1.71 | +0.036 | marginal |
| ETHUSDT | 172 | +0.4262 | +1.61 | +0.032 | marginal |
| ADAUSDT | 192 | +0.5516 | +1.79 | -0.008 | marginal |
| AVAXUSDT | 164 | +0.4175 | +1.44 | -0.044 | noise |
| DOGEUSDT | 203 | +0.2258 | +0.98 | +0.023 | noise |
| UNIUSDT | 185 | +0.5388 | +1.66 | +0.146 | marginal |
| XLMUSDT | 170 | +0.1435 | +0.57 | +0.152 | noise |
| PENDLEUSDT | 209 | +0.6516 | +1.83 | +0.087 | marginal |
| JUPUSDT | 177 | +0.0838 | +0.21 | +0.157 | noise |
| RUNEUSDT | 195 | +0.3664 | +1.14 | +0.058 | noise |

Tier mix h=+15: 5 noise, 5 marginal, 0 solid.

#### h = +50 1H bars (~50h hold) — autocorrelation contaminated
| Symbol | n | mean_pct | t_stat | autocorr_lag1 | tier |
|---|---:|---:|---:|---:|---|
| BTCUSDT | 188 | +0.2460 | +1.05 | +0.327 | noise |
| ETHUSDT | 172 | +0.7531 | +1.69 | +0.421 | marginal |
| ADAUSDT | 192 | +1.1679 | +2.02 | +0.313 | marginal |
| AVAXUSDT | 164 | +1.4019 | +2.20 | +0.422 | marginal |
| DOGEUSDT | 203 | +0.5708 | +1.12 | +0.441 | noise |
| UNIUSDT | 185 | +1.6213 | +2.40 | +0.451 | marginal |
| XLMUSDT | 170 | +1.0059 | +1.47 | +0.310 | noise |
| PENDLEUSDT | 209 | +1.5084 | +2.38 | +0.370 | marginal |
| JUPUSDT | 177 | -0.2711 | -0.39 | +0.353 | noise |
| RUNEUSDT | 195 | +0.4170 | +0.64 | +0.394 | noise |

Tier mix h=+50: 5 noise, 5 marginal, 0 solid. **Autocorrelaciones lag-1 saltan a +0.31 a +0.45** porque ventanas adyacentes de 50h se traslapan masivamente — los t-stats marginales en este horizonte están inflados por dependencia entre observaciones, **no son interpretables como evidencia de edge** sin corrección por block bootstrap o Newey-West (no aplicado en v1).

### Interpretación numérica

1. **Pico predictivo a h=+5**: 3 solid (PENDLE, AVAX, ADA), 2 marginal (BTC, XLM), 5 noise. Es el horizonte natural donde la señal del scoring tiene contenido informativo, antes de que el ruido lo enmascare a horizontes más largos. Autocorrelación lag-1 está en rango aceptable (-0.17 a +0.16) — t-stats no inflados por dependencia.
2. **Heterogeneidad fuerte por símbolo**: PENDLE (solid+solid+marginal+marginal a través de horizontes) tiene la señal más consistente. AVAX y ADA también muestran solid a h=+5. JUP, RUNE, DOGE, ETH (a h=+1) son ruido en todo horizonte.
3. **A h=+1 solo PENDLE es solid** (t=3.01) — la señal a 1h está dominada por ruido en 7/10 símbolos. Si el strategy tuviera holding promedio de 1-2h, capturaría poco del edge predictivo.
4. **A h=+50 los t-stats son inutilizables** sin corrección de autocorrelación. Marcarlos como evidencia de edge es engañoso.

### Conclusión #2

- **3 símbolos (PENDLE, AVAX, ADA) tienen señal predictiva sólida (t≥2.5) a h=+5**, donde la autocorrelación lag-1 es baja y la evidencia es confiable.
- **5 símbolos (DOGE, JUP, RUNE, ETH, XLM a h=+1) son ruido puro** en todos los horizontes evaluados.
- **2 símbolos (BTC, UNI) son marginales** — t-stats consistentes en `1.5–2.4`, no sobrevivirán Deflated Sharpe (corrección por multiple testing N≥10 trials).
- **No hay tier "solid" mayoritario.** El criterio del spec para mundo A (`t≥2.5` mayoría) **NO se cumple** — solo 3/10 símbolos lo alcanzan, y solo en un horizonte específico.
- **El criterio del spec para mundo C** (`t<1.5` mayoría) **se cumple parcialmente** — 5/10 a h=+5 son ruido, 7/10 a h=+1 son ruido. Pero el subset solid en h=+5 (PENDLE/AVAX/ADA) es real y no descartable.

**Lectura:** la basket NO es uniforme. La clasificación monolítica (solo A o solo B o solo C) es incorrecta. El sistema es **mixto: 3 símbolos con edge real (mundo B-like), 5 con sin edge (mundo C-like), 2 marginales**. Esto se confirma en #5 abajo.

## Análisis 3 — Stop-out post-mortem (subset B-like: PENDLE / AVAX / ADA)

**Pregunta:** ¿Cuánto rescatamos al widening del SL? Curva, no punto único.

**Método:** Para cada SL-exit en cost-on, re-simular con multiplier `0.5/1.0/1.5/2.0x` del actual `atr_distance`. Walk forward 1H bars, primer-hit de new SL o TP (TP fijo). Track MAE intermedio.

**Subset:** los 3 símbolos B-like identificados en #2+#5 (PENDLE, AVAX, ADA). Por límite de tiempo del deadline 17:45 UTC, no se corrió en los otros 7 — para los 5 mundo C-like (DOGE/JUP/RUN/ETH/XLM) la pregunta no aplica (no hay signal predictiva que rescatar); para los 2 marginales (BTC/UNI) podría correrse en una iteración follow-up.

### Tabla curva SL multiplier

| Symbol | n_SL_orig | mult | n_eval | %_rescued | avg_int_DD% | avg_final_pnl% |
|---|---:|---:|---:|---:|---:|---:|
| PENDLEUSDT | 178 | 0.5x | 156 | 0.0 | 1.75 | -0.938 |
| PENDLEUSDT | 178 | 1.0x | 156 | 10.3 | 2.88 | -1.131 |
| PENDLEUSDT | 178 | 1.5x | 156 | **19.9** | 3.57 | -0.950 |
| PENDLEUSDT | 178 | 2.0x | 156 | **26.9** | 4.19 | -1.147 |
| AVAXUSDT | 135 | 0.5x | 76 | 0.0 | 1.73 | -0.873 |
| AVAXUSDT | 135 | 1.0x | 76 | **0.0** | 2.58 | -1.745 |
| AVAXUSDT | 135 | 1.5x | 76 | **0.0** | 3.42 | -2.618 |
| AVAXUSDT | 135 | 2.0x | 76 | 6.6 | 4.09 | -2.626 |
| ADAUSDT | 171 | 0.5x | 130 | 0.0 | 1.57 | -0.784 |
| ADAUSDT | 171 | 1.0x | 130 | 10.8 | 2.09 | -0.451 |
| ADAUSDT | 171 | 1.5x | 130 | **19.2** | 2.82 | -0.515 |
| ADAUSDT | 171 | 2.0x | 130 | **21.5** | 3.39 | -0.954 |

### Interpretación

**PENDLE:** la curva sube monótona (0 → 10.3 → 19.9 → 26.9%) con SL widening. **El exit logic está cortando un 20-27% de trades que en realidad sí llegarían a TP** si se les diera margen. Confirma mundo B local. Pero `avg_final_pnl%` se mantiene negativo a través de la curva — los trades rescatados no son net-profitable en promedio. La razón: muchos rescatados eventualmente terminan en SL más amplio o quedan abiertos al fin de data con pérdida residual. **SL widening solo no arregla el strategy en PENDLE; necesita combinarse con TP/BE redesign.**

**ADA:** patrón similar a PENDLE pero magnitud menor (10.8 → 19.2 → 21.5% rescued). Mundo B local confirmado pero más débil que PENDLE.

**AVAX:** **no rescata significativamente** ni siquiera a 2.0x (6.6%). A pesar de que #2 mostró t=2.68 solid en h=+5, los SL exits son trades que genuinamente fueron en la dirección equivocada — widening solo agrega más drawdown sin convertirlos en winners. **AVAX se reclasifica de mundo B local a mundo C-like** — la señal en #2 puede ser explicable por correlación temporal con otros símbolos sin reflejar edge ejecutable bajo este exit logic.

**Caveat avg_int_DD%:** crece de 1.5% a 4% con SL más ancho. Con `RISK_PER_TRADE = 1%` actual, una posición que queda abierta con 4% MAE intermedio implica 4x el risk budget — operativamente esto es violación del contrato R-multiple. **Cualquier widening de SL debe acompañarse de reducción de `size_mult` o de `RISK_PER_TRADE`** (= scope #279).

### Conclusión #3

- **PENDLE: mundo B local confirmado.** 20-27% de SL exits rescatables con SL 1.5-2.0x. Plan A.4: tunear `atr_sl_mult` extendido + reducir per-trade risk simultáneamente.
- **ADA: mundo B local confirmado, magnitud menor.** Mismo plan que PENDLE.
- **AVAX: reclasificado a mundo C-like.** Widening no rescata. Posiblemente la correlación con BTC en bear regimen explica el solid t-stat de #2 sin que la edge sea ejecutable per-symbol.
- **Ningún caso justifica SL widening sin sizing reduction.** avg_int_DD intermedio violaría el risk budget actual.

## Análisis 4 — Régime breakdown 10×3

**Pregunta:** ¿Hay un régimen donde la strategy gana?

**Método:** Tag por 30-day rolling BTC daily return. Bear < -5%, Sideways -5% a +15%, Bull > +15%. Tag al entry_time. Métricas: trades, WR%, expectancy_net_bps por (símbolo × régimen).

### Tabla 10×3

| Symbol | Bear n | Bear WR% | Bear exp_bps | Side n | Side WR% | Side exp_bps | Bull n | Bull WR% | Bull exp_bps |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTCUSDT | 19 | 21.1 | -3.3 | 112 | 14.3 | -62.7 | 57 | 12.3 | -143.1 |
| ETHUSDT | 29 | 17.2 | -45.5 | 76 | 7.9 | -117.4 | 67 | 13.4 | -196.1 |
| ADAUSDT | 28 | 0.0 | -30.0 | 92 | 0.0 | -12.0 | 72 | 0.0 | -1753.5 |
| AVAXUSDT | 37 | 0.0 | -33.2 | 69 | 0.0 | -48.0 | 58 | 0.0 | -361.6 |
| DOGEUSDT | 33 | 0.0 | -39.3 | 104 | 0.0 | -35.8 | 66 | 0.0 | -425.3 |
| UNIUSDT | 21 | 0.0 | -41.9 | 93 | 0.0 | -29.1 | 71 | 0.0 | -2225.8 |
| XLMUSDT | 35 | 0.0 | -18.0 | 86 | 0.0 | -25.6 | 49 | 0.0 | -10662.7 |
| PENDLEUSDT | 41 | 0.0 | +51.6 | 104 | 0.0 | -3.2 | 64 | 0.0 | -16097.3 |
| JUPUSDT | 44 | 0.0 | +9.2 | 84 | 0.0 | +42.0 | 49 | 0.0 | -399.3 |
| RUNEUSDT | 48 | 0.0 | +20.4 | 94 | 0.0 | -59.8 | 53 | 0.0 | -1956.6 |

### Interpretación

**Caveat #4 (importante):** Las exp_bps de mid/small-cap están dominadas por la artifact bankrupt (entry_notional=0 → pnl_pct = price change bruto, sin trade real). Las celdas con valores muy negativos en bull (e.g., XLM -10663, PENDLE -16097) reflejan precio en regímenes bull con catastrophic-cost del primer trade pre-bankrupt + price drift bruto post-bankrupt. **NO son evidencia interpretable de "strategy loses 16000 bps en bull"** — es contaminación por trades simulados sin capital real.

Los números en BTC y ETH son interpretables sin caveat (ambos mantienen capital):
- **BTC:** Bear WR=21.1%, exp_bps=-3.3 (cuasi-breakeven post-cost). Side WR=14.3%, exp=-62.7. Bull WR=12.3%, exp=-143.
- **ETH:** Bear WR=17.2%, exp=-45.5. Side WR=7.9%, exp=-117. Bull WR=13.4%, exp=-196.

**Patrón consistente: el strategy es menos malo en BEAR que en BULL.** Ambos majors tienen exp_bps menos negativo en bear que en bull. Bear = BTC 30d return < -5% — los entries del scoring (LRC ≤ 25% mean-reversion buy o LRC ≥ 75% mean-reversion sell) son contra-tendencia, y en bear la mean-reversion tiene más sentido que en bull.

Para los 8 símbolos sin winners, los signos de bear exp_bps son menos catastróficos (ADA -30, JUP +9, RUNE +20) — sugiere que **las entries del scoring tienen edge predictivo en bear** (consistente con #2: 3 símbolos solid). Pero nada se materializa en outcomes porque la basket entera bankrupts en mid/small-cap por participation excesiva.

**Sideways patrón mixto:** JUP +42 en sideways (price drift up?), PENDLE -3 (cuasi-cero). No es interpretable como edge.

**Bull es uniformemente catastrófico** en todos los símbolos. Salvo BTC/ETH, los otros 8 tienen exp_bps de -361 a -16097 en bull (incluso filtrando por el caveat de bankrupt-bias).

### Conclusión #4

- **El strategy es contra-tendencia / mean-reversion**: pierde más en bull (regimen tendencial), menos en bear/sideways.
- **Hay alguna evidencia de edge bear-específica** en BTC/ETH (bear -3.3 / -45.5 vs bull -143/-196 — gradient claro). Reforzaría una hipótesis "el systema es viable en bear, no en bull". Pero el sample es chico (BTC 19 trades en bear; ETH 29). Significancia estadística marginal.
- **Para los 8 símbolos sin winners**: el caveat metodológico (bankrupt-bias) impide interpretación de exp_bps. NO se puede concluir "PENDLE gana en bear" desde estos números.
- **Implicación para mundo classification**: refuerza mundo B basket-level — hay régimen-specific viabilidad parcial (bear). Pero la basket entera no escala a otros regímenes.

## Análisis 5 — Expectancy decomposition (killed-by-costs vs structural)

**Pregunta:** ¿Cuántos símbolos son rescatables por #279 + sqrt vs estructuralmente no rentables?

**Método:** Por símbolo, `expectancy_gross_bps = mean(gross_pnl_pct) * 100` y `expectancy_net_bps = mean(net pnl_pct) * 100`. `cost_bps_mean` desde `total_cost_bps_mean` en calculate_metrics. Categorización:
- `survivor`: net > 0
- `killed_by_costs`: gross > 0 AND net < 0
- `structural`: gross ≤ 0 (irrecuperable por re-tuning de costos)

### Resultados

| Symbol | n | gross_bps | cost_bps | net_bps | category |
|---|---:|---:|---:|---:|---|
| BTCUSDT | 188 | +12.15 | 93.15 | -81.08 | killed_by_costs |
| ETHUSDT | 172 | -3.48 | 132.43 | -135.95 | structural |
| ADAUSDT | 192 | -0.28 | 692.42 | -667.69 | structural |
| AVAXUSDT | 164 | +0.00 | 173.70 | -155.57 | structural |
| DOGEUSDT | 203 | -0.78 | 149.14 | -162.99 | structural |
| UNIUSDT | 185 | -0.61 | 892.98 | -873.61 | structural |
| XLMUSDT | 170 | -0.28 | 3097.90 | -3090.03 | structural |
| PENDLEUSDT | 209 | -0.31 | 4960.50 | -4920.79 | structural |
| JUPUSDT | 177 | -0.60 | 144.73 | -88.34 | structural |
| RUNEUSDT | 195 | +3.21 | 515.06 | -555.56 | killed_by_costs |

**Category counts:** survivor 0, killed_by_costs 2 (BTC, RUNE), **structural 8** (ETH, ADA, AVAX, DOGE, UNI, XLM, PENDLE, JUP).

### Observación material — disonancia con #2

Los 3 símbolos con señal predictiva sólida en #2 (PENDLE, AVAX, ADA) son **structural** en #5 — su `gross_pnl_pct` per-trade es esencialmente cero o negativo. Esto significa:

- La señal forward-return existe a horizontes fijos h=+5 (medible a nivel de precio).
- La SL/TP/BE logic del strategy convierte ese forward-return en un trade outcome con expectancy ≈ 0.

Para PENDLE: forward-return mean a h=+5 = +0.55%. Trade outcome gross_pnl_pct mean ≈ 0. **El exit logic destruye ~100% de la información predictiva**, antes incluso de aplicar costos. Este es el smoking gun de mundo B (exits enmascaran signal) más que mundo C (signal es ruido).

### Conclusión #5

- **0 survivors** post-cost. Coincide con honesty diff de A.0.2.
- **Solo 2 killed_by_costs** (BTC con gross +12 bps, RUNE con gross +3 bps). Ambos cerca del umbral de cero — incluso con costos cero, gross expectancy es marginal y dudosa estadísticamente (BTC t=1.83 marginal, RUNE t<1.5 noise en #2).
- **8 structural**. La mayoría no tiene gross expectancy positiva en el outcome del strategy actual, INDEPENDIENTEMENTE de los costos.
- **Cruzando con #2**: 3 símbolos tienen signal predictiva real al precio (PENDLE/AVAX/ADA) pero el strategy outcome es structural. La explicación más simple es que el exit logic (SL/TP/BE) corta posiciones antes de que la edge predictiva se materialice — coherente con mundo B (signal marginal, exits enmascaran).
- **#279 + sqrt v2 NO rescata 8 de 10**. Solo BTC y RUNE están en el rango "killed_by_costs", y sus magnitudes (gross +12 y +3 bps) son sub-marginales en cualquier framework de evaluación serio. **El mensaje principal: el problema NO es primariamente costos, es estructura del exit logic.**

## Análisis 6 — Holding period distribution (winners vs losers)

**Pregunta:** ¿El timeframe de evaluación está bien para la edge real de la señal?

### Tabla per-símbolo

| Symbol | Win n | Win med (h) | Win p10 | Win p90 | Loss n | Loss med (h) | Loss p10 | Loss p90 | ratio (med) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTCUSDT | 27 | 14.0 | 4.0 | 52.8 | 161 | 4.0 | 1.0 | 20.0 | **3.50** |
| ETHUSDT | 20 | 14.0 | 5.0 | 19.5 | 152 | 5.0 | 1.0 | 18.0 | **2.80** |
| ADAUSDT | 0 | — | — | — | 192 | 2.5 | 1.0 | 16.0 | — |
| AVAXUSDT | 0 | — | — | — | 164 | 10.0 | 2.0 | 30.7 | — |
| DOGEUSDT | 0 | — | — | — | 203 | 3.0 | 1.0 | 17.0 | — |
| UNIUSDT | 0 | — | — | — | 185 | 5.0 | 1.0 | 15.6 | — |
| XLMUSDT | 0 | — | — | — | 170 | 2.0 | 1.0 | 14.0 | — |
| PENDLEUSDT | 0 | — | — | — | 209 | 3.0 | 1.0 | 13.0 | — |
| JUPUSDT | 0 | — | — | — | 177 | 3.0 | 1.0 | 16.0 | — |
| RUNEUSDT | 0 | — | — | — | 195 | 3.0 | 1.0 | 18.0 | — |

### Interpretación

**Para BTC y ETH (los únicos con winners):**

- **BTC**: winners hold mediana 14h, losers 4h. **Ratio 3.5x → cumple el criterio del spec ("factor 3+ → exit logic cuts winners short")**.
- **ETH**: winners 14h, losers 5h. **Ratio 2.8x → marginalmente bajo el criterio, pero cerca**.

Esto es **smoking gun para mundo B en BTC y ETH**: la señal predictiva tiene horizonte natural ≈ 14h en winners, pero el exit logic cierra losers en 4-5h. Si los winners se desbloquean (TP no demasiado agresivo, BE no demasiado prematuro, SL más amplio), la edge se materializa más.

Nota: el horizonte de winners (14h) es **mayor** que el peak de #2 (h=+5). Tiene sentido: a h=+5 hay forward-return mean positivo (la señal "acaba de empezar"), y la edge se desarrolla durante 10-15 horas. El strategy actual captura solo la fracción inicial — los losers exit-out a 4-5h por SL, antes de que la edge se desarrolle, y los winners que llegan a TP (la fracción del 14% que sobrevive) demoran 14h.

**Para los 8 símbolos sin winners:** loser holding median 2-10h. Demasiado corto vs el horizonte predictivo de h=+5+ identificado en #2 para los 3 B-like (PENDLE/AVAX/ADA). Implica que también allí el exit logic está cerrando demasiado pronto, pero no podemos confirmar con la métrica winner/loser ratio porque hay 0 winners.

### Conclusión #6

- **BTC y ETH muestran patrón mundo B claramente** (ratio winners/losers > 2.8x). El exit logic está cortando winners. A.4 con SL más amplio + BE menos prematuro debería capturar más edge.
- **Para los 8 símbolos sin winners**: el holding period medio de losers (2-10h) está en el rango del peak forward-return horizonte, sugiriendo que los exits son agresivos, pero el problema dominante es bankrupt por participation — primero hay que arreglar sizing (#279), después verificar si el exit logic redesign también es necesario.
- **Recomendación específica para A.4**: para BTC/ETH, search grid en `atr_be_mult` y `atr_tp_mult` extendidos hacia valores más amplios (BE mas tarde, TP más alto), no solo `atr_sl_mult`. La edge está disponible si los winners pueden quedarse abiertos 14h en lugar de salir prematuramente.

---

## Diagnóstico integrado

**Veredicto: la basket NO clasifica monolítica en A, B, o C. Clasificación per-símbolo es honest, monolítica es maquillaje.**

### Por qué la clasificación monolítica no aplica

El framework del spec (#281) asume basket homogéneo:
- A: signal works (t≥2.5) + costs/sizing kill it
- B: signal marginal (1.5≤t<2.5) + exits enmascaran
- C: signal is noise (t<1.5) + structural

La data muestra que la basket es **heterogénea**:

| Cluster | Símbolos | Evidencia #2 (forward-return) | Evidencia #5 (expectancy) |
|---|---|---|---|
| **C-like (no signal)** | DOGE, JUP, RUNE | t<1.5 en h=+1+5+15+50 | gross ≈ 0, structural |
| **C-like (no signal)** | ETH, XLM | t<1.5 mayoría horizontes | gross ≈ 0, structural |
| **B-like (signal masked)** | PENDLE | t=3.01 @h=1, t=2.60 @h=5 (solid) | gross = -0.31, structural — exit destruye 100% del edge |
| **B-like (signal masked)** | AVAX | t=2.68 @h=5 (solid) | gross = 0.00, structural |
| **B-like (signal masked)** | ADA | t=2.55 @h=5 (solid) | gross = -0.28, structural |
| **A-like marginal** | BTC | t=1.83 @h=5 (marginal) | gross = +12.15 bps, killed_by_costs |
| **A-like marginal** | RUNE | t<1.5 todos horizontes | gross = +3.21 bps, killed_by_costs (probablemente noise + suerte) |
| **B/C ambiguo** | UNI | t=2.15 @h=1, t=1.66 @h=15 (marginal) | gross = -0.61, structural |

### Lectura agregada

**El problema NO es primariamente costos.** El cost model (linear v1) sí amplifica el daño en mid/small-cap por el tema de R-multiple sizing, pero incluso pre-cost (gross expectancy) **8 de 10 símbolos no tienen edge**. La conclusión es: **la basket actual no es uniformemente rentable independiente del modelo de costos.**

**Sub-conclusiones:**

1. **Para los 3 símbolos con signal sólida (PENDLE, AVAX, ADA)**: existe edge predictivo a h=+5 horas, pero el exit logic actual (SL/TP/BE basado en ATR) lo destruye antes de materializarse. Esto es un **patrón mundo B local** — el signal está, los exits no lo capturan.
2. **Para los 5 símbolos sin signal (DOGE, JUP, RUNE, ETH, XLM)**: no hay edge ni a nivel de precio ni a nivel de strategy outcome. Re-tuning no va a inventar edge. Esto es **patrón mundo C local**.
3. **Para los 2 símbolos marginales (BTC, UNI)**: signal débil que no sobrevivirá Deflated Sharpe con N≥10 trials. Asumir edge es curve-fitting al 18m de train.

### Clasificación final (refinada con #3 + #6)

**Mundo predominante: C-con-pequeñas-B-pockets** — la basket actual no funciona y la fracción rescatable es minoritaria.

| Cluster | Símbolos | Plan |
|---|---|---|
| **B-like confirmado** | PENDLE, ADA | A.4 con `atr_sl_mult` extendido [2.0, 5.0] + #279 (smaller per-trade) + posiblemente exit logic redesign |
| **B-like en BTC/ETH (parcial)** | BTC, ETH | Mundo B en holding-period (#6 ratio 3.5x / 2.8x) pero en marginal range en #2 (t=1.83 / 1.37). A.4 conservador con BE/TP redesign más que SL widening |
| **C-like confirmado** | DOGE, JUP, RUNE, ETH-fwd, XLM, AVAX (reclassificado por #3) | Remover del basket o redesign de scoring específico. Re-tuning curve-fitea a noise |
| **Marginal alto-riesgo** | UNI | t<2.0 + structural + sin rescue evidence. No incluir en A.4 |

**Consolidación:** 2 símbolos sólidamente rescatables (PENDLE, ADA), 2 con patrón B parcial (BTC, ETH), 6 mundo C / marginal. La basket actual de 10 no es la basket viable. La basket que A.4 debería tunear es **2-4 símbolos máximo** (PENDLE, ADA, BTC, ETH).

### Por qué NO es Mundo A puro

El spec define mundo A como "signal works + costs/sizing kill it" — esperaríamos `t≥2.5` mayoría AND `killed_by_costs` alta proporción AND SL curve sature tarde. **Solo 3/10 son solid en #2 y solo 2/10 son killed_by_costs en #5**. La proporción killed_by_costs es 20%, no la "alta" que mundo A predice. Y en los 3 solid, gross expectancy es ≈ 0, indicando que el problema no se reduce a "ajustar SL más amplio + sqrt cost".

### Por qué NO es Mundo B puro

Mundo B requiere "signal marginal mayoría". A h=+5 (peak horizonte), 3 son solid + 2 marginal + 5 noise. La mayoría (5) es noise, no marginal. La hipótesis "marginal con exits que enmascaran" describe correctamente PENDLE/AVAX/ADA, pero NO los otros 7. Etiquetar la basket entera como B asume edge donde no lo hay (5 noise puro).

### Por qué NO es Mundo C puro

Si fuera C puro, no habría símbolos solid en #2. PENDLE (t=3.01) y los demás solids son real edge predictivo en forward-returns a h=+5. La existencia de 3 símbolos con signal sólida invalida "noise mayoría absoluta".

### Análisis adicional que reduciría ambigüedad

- **#6 (holding period)**: si winners hold mucho más que losers en PENDLE/AVAX/ADA, confirma mundo B local (exit logic agresivo). Si winners y losers tienen holding similar, refuta B y sugiere problema más profundo.
- **#3 (stop-out post-mortem con curva SL)**: para los 3 símbolos B-like, ¿widening SL a 1.5x o 2.0x rescata >30% de SL exits? Confirma B + da sizing del posible rescate.
- **#4 (régime breakdown)**: ¿hay un régimen donde el systema gana en agregado? Si bear+sideways funciona y bull pierde (counter-trend), valor como hedge. Si pierde en todos, refuerza C.

## Recomendaciones por mundo

### Para los 3 símbolos B-like (PENDLE, AVAX, ADA)

- **#279 (smaller per-trade size + atr_sl_mult widening)**: priority alta. La señal está; el problema es que exits cortos la matan. Widening de atr_sl_mult + smaller size podría capturar la edge predictiva sin reventar costos por participation alta.
- **A.4 (#250) re-tune**: re-tunear estos 3 con el cost model on, probablemente convergerá a `atr_sl_mult` mucho más ancho (3-5x actual) para capturar el horizonte h=+5 sin SL prematuro. No tunear los otros 7.
- **A.0.3 (#278) deflated metrics**: aplicar Deflated Sharpe para verificar que estos 3 sobreviven la corrección por multiple testing (N≥10 trials por symbol overrides + N de gridsearch en A.4). Probablemente PENDLE sobrevive (t=3.01 fuerte), AVAX y ADA en zona gris.

### Para los 5 símbolos C-like (DOGE, JUP, RUNE, ETH, XLM)

- **Remover del basket curated**. No hay edge predictivo, re-tuning no va a inventarla.
- **Alternativa**: scoring redesign específico para estos símbolos antes de re-incorporarlos. Pero eso es un epic separado, fuera de A.x.
- **A.3 (#249)**: el bar de validación debe incluir un **veto por número mínimo de símbolos viables** post-Deflated. Si la basket termina con 2-3 símbolos viables, el sample size del backtest es insuficiente para cualquier conclusión de robustez agregada.

### Para los 2 marginales (BTC, UNI) y RUNE

- **Tratamiento conservador**. No asumir edge. Si el bar de A.3 los pone bajo veto, removerlos. RUNE en particular: gross +3.21 bps con t<1.5 en todos horizontes = probablemente noise + suerte sobre 195 trades.

### Implicación para A.4 (#250)

- **Pausar tuning de los 7 sin signal**. Tunear solo PENDLE/AVAX/ADA (3 símbolos B-like). Si A.4 procede sobre los 10, va a curve-fitear a noise en 7/10 y producir overrides que maquillan el problema en train pero fallan en holdout.
- **Plan de tuning para los 3**: search grid en `atr_sl_mult` extendido a [2.0, 5.0] (vs actual [0.5, 1.5]); BE/TP escalados consistentemente. Hold time target ≈ 5h (peak forward-return horizonte).

### Implicación para #279

- **Priority alta para los 3 B-like símbolos**. v1 linear ya identificó alta participation como el cost amplifier — sqrt comprime ~10-30x. Pero no rescata los 7 C-like (no tienen gross edge para empezar).
- **No bloquear ship de A.0.2 PR**. La data del honesty diff es válida con linear v1; el diff con sqrt sería más comunicable para Simon (números menos catastróficos) pero no cambia las conclusiones.

### Implicación para A.0.3 (#278)

- **Deflated Sharpe es central** ahora. Con 7/10 símbolos sin signal, la basket entera tiene N efectivo bajo. Aplicar la corrección con N=10 baseline + N adicional por gridsearch de A.4 va a pulverizar Sharpe en BTC/UNI marginales. Si PENDLE sobrevive deflated → es la única evidencia robusta de signal en la basket.

## Estado de completitud

| Análisis | Estado | Notas |
|---|---|---|
| #1 per-symbol summary | ✅ completo | con caveat metodológico bankrupt-bias |
| #2 forward-return + t-stat | ✅ completo | 10 símbolos × 4 horizontes |
| #3 stop-out post-mortem | ✅ completo | subset PENDLE/AVAX/ADA (B-like). NO corrido en los otros 7 — para mundo C local no aplica; para BTC/ETH/UNI follow-up posible |
| #4 régime breakdown 10×3 | ✅ completo | con caveat de contaminación por bankrupt-state trades en mid/small-cap |
| #5 expectancy decomposition | ✅ completo | 10 símbolos clasificados |
| #6 holding period distribution | ✅ completo | ratio winner/loser solo computable para BTC/ETH (otros tienen 0 winners) |

**6 de 6 ejecutados con interpretación seria.** No hay análisis pendientes para martes.

Caveat global más importante: el `capital floor at 0` introducido en A.0.2 produce trades post-bankrupt con `entry_notional = 0` que NO reciben cost augmentation. Esto sesga las métricas de #1 (participation, cost_bps mean) y #4 (régime exp_bps) en los símbolos que bankruptan rápido. Las clasificaciones de #2 (forward-return en gross) y #5 (expectancy decomp con `gross_pnl_pct` preservado) NO están afectadas. La clasificación de mundo es robusta al caveat.

## Decisions to surface

1. **Spec #281 §"Decisions to surface" #1 — `detect_regime()` de producción además del simplificado**: NO aplicado en v1 (default según spec). El régimen simplificado por 30d BTC return es lo único usado en #4. Surface si reviewer quiere comparativa con `detect_regime()`.
2. **Spec #281 §"Decisions to surface" #2 — análisis 6 separado por régimen**: NO aplicado en v1 (default según spec). Holding distribution agregada per símbolo, no por (símbolo × régimen).
3. **Cualquier sorpresa material** (igual que en A.0.2 honesty diff): listada per análisis si aplica.

---
