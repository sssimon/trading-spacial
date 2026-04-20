# Vol-Normalized Sizing — Investigación y Decisión Final (#125)

**Fecha:** 2026-04-20
**Issue:** #125 (cerrado — implementación revertida)
**Spec de referencia:** `docs/superpowers/specs/en/2026-04-18-market-data-layer-design.md`
**Doc canónico del sistema:** `docs/superpowers/specs/es/2026-04-18-documento-completo-sistema-trading.md`

## TL;DR

Se investigó e implementó position sizing normalizado por volatilidad (Yang-Zhang) en Fase 8 del epic market-data-layer (PR #148). **La implementación fue revertida** porque en el backtest comparativo sobre 9 símbolos curados × 6.5 meses se observó una **regresión agregada de −$7,505** en P&L vs. el sistema original.

**La causa raíz es de diseño**, no de implementación: el sistema ya normaliza riesgo por volatilidad a través del tuning per-symbol de `atr_sl_mult/tp/be` (epic #121/#122/#124, 735+ simulaciones). Aplicar un `vol_mult` multiplicativo encima rompe esa calibración.

## Qué se construyó y qué se revirtió

### Construido (PR #148, commits `ede1b46`, `2024f20`, `f9072fd`)

- `btc_scanner.annualized_vol_yang_zhang(df_daily)` — estimador Yang-Zhang con fallback a `TARGET_VOL_ANNUAL` cuando hay < 5 barras
- Constantes `TARGET_VOL_ANNUAL = 0.15`, `VOL_LOOKBACK_DAYS = 30`, `VOL_MIN_FLOOR = 0.05`, `VOL_MAX_CEIL = 0.20`
- Scanner: cálculo de `asset_vol`, derivación de `vol_mult`, aplicación sobre `risk_usd`. Campos nuevos en `sizing_1h`
- Backtest: `vol_mult` computado al entrar (sin look-ahead, `end=bar_time`), guardado en el dict de la posición, aplicado en ambos sitios de `risk_amount`

### Revertido (commit `f39b686`)

- Aplicación de `vol_mult` al `risk_usd` del scanner: eliminada. `risk_usd = capital * 0.01` restaurado
- `vol_mult` y campos relacionados en `sizing_1h` del reporte: eliminados
- `vol_mult` en el position dict del backtest: eliminado. `risk_amount = capital * RISK_PER_TRADE * size_mult` restaurado
- Constantes `VOL_MIN_FLOOR` y `VOL_MAX_CEIL`: eliminadas del scanner (sólo tenían sentido con la fórmula clamp)

### Retenido como utilidad diagnóstica

- `annualized_vol_yang_zhang()` y `TARGET_VOL_ANNUAL` / `VOL_LOOKBACK_DAYS` permanecen en `btc_scanner.py` con un comentario explicando que **NO** están conectados a sizing. Son útiles para futura telemetría, dashboards, o investigación

## Backtest comparativo — evidencia

**Metodología:** `scripts/compare_vol_sizing_batch.py` parchea `annualized_vol_yang_zhang` para devolver `TARGET_VOL_ANNUAL` en el baseline, forzando `vol_mult = 1.0`. Así ambos runs comparten el mismo camino de código y las mismas velas OHLCV; la única variable es el escalamiento de riesgo.

**Ventana:** 2025-10-01 → 2026-04-20 (~6.5 meses)
**Símbolos:** 9 de los 10 curados (JUPUSDT sin datos en Binance spot para la ventana)

### Resultados agregados

| Métrica | Baseline | Con vol_mult | Delta |
|---|---|---|---|
| Total P&L (9 símbolos) | +$9,185 | +$1,680 | **−$7,505** |
| BTC Max Drawdown | −23.46% | −7.51% | +15.95 pp (DD más pequeño) |

### Contribución por símbolo

| Símbolo | Trades | Baseline ($) | Vol sizing ($) | Delta ($) | Lectura |
|---|---|---|---|---|---|
| BTCUSDT | 66 | −847 | +9 | +856 | recorta la pérdida |
| ETHUSDT | 57 | −2,453 | −648 | +1,805 | recorta la pérdida |
| ADAUSDT | 80 | +2,832 | +554 | **−2,278** | recorta el beneficio |
| AVAXUSDT | 55 | −1,119 | −235 | +884 | recorta la pérdida |
| DOGEUSDT | 80 | +7,908 | +1,331 | **−6,577** | recorta el beneficio (DOGE dominó la ventana) |
| UNIUSDT | 63 | +28 | +31 | +3 | neutro |
| XLMUSDT | 75 | +752 | +177 | −575 | recorta el beneficio |
| PENDLEUSDT | 68 | +209 | +59 | −151 | recorta el beneficio |
| RUNEUSDT | 65 | +1,875 | +402 | −1,473 | recorta el beneficio |
| **TOTAL** | **609** | **+9,185** | **+1,680** | **−7,505** | |

## Por qué no funciona (análisis del diseño)

El diseño canónico del sistema (ver `2026-04-17-formula-ganadora-resultados-finales.md`) ya resuelve el problema que #125 quería atacar, pero con una herramienta diferente y superior:

**Epic #121/#122/#124 — optimización per-symbol:**

| Tipo de token | σ anual aprox | SL óptimo | TP óptimo | Lógica |
|---|---|---|---|---|
| Baja vol, rebote limpio (ADA, XLM, PENDLE, JUP) | 40-60% | 0.5x ATR | 3-4x ATR | SL tight, TP moderado |
| Media vol (BTC, DOGE) | 45-80% | 0.7-1.0x ATR | 4x ATR | parámetros estándar |
| Alta vol, explosiva (AVAX, RUNE) | 80-100% | 0.7-1.5x ATR | 4-6x ATR | SL ancho, TP muy ancho |
| DeFi (UNI) | 50-70% | 1.0x ATR | 3x ATR | TP corto |

Esta tabla es volatilidad-normalización **estructural**, descubierta vía 735+ backtests. No confunde "vol alta = riesgo malo": reconoce que RUNE tiene vol alta **buena** (capturada por TP 6x) y que el problema con los tokens descartados (BNB, SOL, XRP, DOT...) no era su volatilidad sino la ausencia de ciclos mean-reversion explotables.

**`vol_mult` multiplicativo falla** porque:

1. Contradice el resultado de 735+ simulaciones
2. No distingue "vol buena" de "vol mala" — sólo mide σ
3. Cap especialmente el upside en los símbolos que generan el P&L (DOGE aporta 60% del P&L histórico y es high-vol)
4. Duplica el trabajo que ya hacen los `atr_sl_mult/tp` per-symbol, restando en lugar de sumar

## La hipótesis del Issue #121 ya estaba resuelta

Del `2026-04-18-documento-completo-sistema-trading.md` §7 Historial de Mejoras:

> **Abr 15** — Parámetros iguales → optimizados per-symbol (735 sims): **−$14,655 → +$54,706 portfolio**

La cifra `−$14,655 → +$25-40k` de la descripción del épico #121 ya se superó antes de llegar al punto #125. El camino real hasta el +$98,446 (o +$168,692 en 4 años con SHORT) fue:

1. SL/TP fijo → ATR dinámico: +33% → +53%
2. Parámetros iguales → per-symbol tuning: **+$54,706** (resuelve #121)
3. Regime detector multi-signal: +53% → +62%
4. Portfolio curado (7 ganadoras): +$54,706
5. 3 nuevos tokens (PENDLE, JUP, RUNE): +$54,706 → +$86,596
6. Umbrales de régimen 70/30 → 60/40: +$86,596 → +$98,446

No hay un paso 7 con vol-normalized sizing — y al intentar agregarlo, el sistema pierde dinero.

## Decisión

- **Issue #125 permanece CERRADO** (la implementación se hizo y se evaluó — la evaluación dijo "no").
- **Aplicación del `vol_mult` revertida** en `btc_scanner.py` y `backtest.py`.
- **Yang-Zhang vol estimator retenido** como utilidad disponible (por si se quiere añadir a un dashboard futuro).
- **Spec con esta decisión:** este documento.

## Próximos pasos (si se quiere mejorar sizing)

Alternativas que **sí** podrían aportar encima del per-symbol tuning (sin reemplazarlo):

1. **Sizing por convicción del score** — ya existe (`size_mult` 0.5/1.0/1.5). Puede refinarse con datos históricos por tier de score.
2. **Kill-switch por deterioro** — pausar un símbolo si su Profit Factor rolling cae bajo 1.0 durante N trades. Es el Próximo Paso #2 del doc canónico.
3. **Re-tuning automático trimestral** — Issue #137 (sistema de auto-tuning per-symbol). Ya hay base de datos `tune_results` y endpoints.
4. **Corrida comparativa 2022-2026 completa** del per-symbol tuning vs. baseline plano — para documentar oficialmente la contribución de +$54,706 a +$98,446.

Ninguna de estas usa un multiplicador flat sobre `risk_usd`.
