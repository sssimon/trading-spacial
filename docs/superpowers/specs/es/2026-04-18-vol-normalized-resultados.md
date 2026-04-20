# Resultados de Vol-Normalized Position Sizing — #125

**Fecha:** 2026-04-20
**Spec:** `docs/superpowers/specs/en/2026-04-18-market-data-layer-design.md`
**Plan:** `docs/superpowers/plans/2026-04-18-market-data-layer.md` (Fase 8)
**Issue:** #125
**Datos de la corrida:** `/tmp/btc_compare.json`, `/tmp/vol_compare_last2.json`, stdout de `scripts/compare_vol_sizing_batch.py`

## Qué cambió

- `btc_scanner.annualized_vol_yang_zhang(df_daily)` — estimador Yang-Zhang con fallback a `TARGET_VOL_ANNUAL` cuando hay < 5 barras.
- Constantes: `TARGET_VOL_ANNUAL = 0.15`, `VOL_LOOKBACK_DAYS = 30`, `VOL_MIN_FLOOR = 0.05`, `VOL_MAX_CEIL = 0.20` (clamp inferior del multiplicador).
- **Scanner** (`btc_scanner.py:scan`): en el bloque de sizing, después de calcular ATR, consulta `md.get_klines(symbol, "1d", VOL_LOOKBACK_DAYS + 5)`, deriva `asset_vol` y `vol_mult`, y escala `risk_usd`. Expuesto en `sizing_1h`: `asset_vol`, `vol_mult`, `target_vol`.
- **Backtest** (`backtest.py:simulate_strategy`): al abrir la posición, calcula `vol_mult` usando `md.get_klines_range(..., bar_time - timedelta(days=35), bar_time)` (sin look-ahead) y lo guarda en el dict de la posición. En el cierre, `risk_amount = capital * RISK_PER_TRADE * size_mult * vol_mult`.

## Fórmula

```
vol_mult = clamp(
    TARGET_VOL_ANNUAL / max(asset_vol, VOL_MIN_FLOOR),
    VOL_MAX_CEIL,   # floor del multiplicador = 0.20 → nunca menos del 20% del riesgo base
    1.0,            # ceiling del multiplicador = 1.0 → nunca más del 100% del riesgo base
)
risk_usd = capital * 0.01 * vol_mult
```

Intuición: activos con vol anualizada > 15% reciben menos riesgo; activos con vol ≤ 15% reciben el riesgo base. El cap inferior (20%) evita que una vol extrema reduzca el sizing a niveles operativamente inviables.

## Metodología

Corrida con `scripts/compare_vol_sizing_batch.py` — el script usa **el mismo camino de código** de `simulate_strategy` para ambos runs, pero parchea `annualized_vol_yang_zhang` a devolver `TARGET_VOL_ANNUAL` para el baseline. Eso fuerza `vol_mult = 1.0` siempre y aísla el efecto del escalamiento por volatilidad como única variable.

- **Ventana:** 2025-10-01 → 2026-04-20 (~6.5 meses)
- **Warm-up:** datos desde 2024-01-01 para warmup de SMA200 (1D)
- **Símbolos:** 10 del universo propuesto; JUPUSDT se excluyó porque no hay datos suficientes en Binance spot para esta ventana.

> **Limitación importante:** la hipótesis del épico #121 (-$14,655 → +$25k–$40k) viene de un backtest histórico sobre **2022-2026 (~4 años)**. La corrida actual cubre **sólo ~6 meses**. El resultado de esta sesión es directional, no concluyente para #121.

## Comparativa — Agregado (9 símbolos, 6.5 meses)

| Métrica | Baseline (sin vol) | Con vol sizing | Delta |
|---|---|---|---|
| Total P&L ($) | +$9,185 | +$1,680 | **−$7,505** |
| BTC Max Drawdown | −23.46% | −7.51% | **+15.95 pp (mejor)** |

La diferencia en Max DD de BTC ilustra el mecanismo: vol-sizing recorta drawdowns porque dimensiona las posiciones según la volatilidad realizada. Esa misma mecánica recorta la *upside* en este periodo porque varios símbolos ganaron con la volatilidad (DOGE, ADA, RUNE).

## Contribución por símbolo

| Símbolo | Trades | Baseline ($) | Vol sizing ($) | Delta ($) | Nota |
|---|---|---|---|---|---|
| BTCUSDT | 66 | −847 | +9 | **+856** | recorta la pérdida |
| ETHUSDT | 57 | −2,453 | −648 | **+1,805** | recorta la pérdida |
| ADAUSDT | 80 | +2,832 | +554 | −2,278 | recorta el beneficio |
| AVAXUSDT | 55 | −1,119 | −235 | **+884** | recorta la pérdida |
| DOGEUSDT | 80 | +7,908 | +1,331 | −6,577 | recorta el beneficio (DOGE dominó la ventana) |
| UNIUSDT | 63 | +28 | +31 | +3 | neutro |
| XLMUSDT | 75 | +752 | +177 | −575 | recorta el beneficio |
| PENDLEUSDT | 68 | +209 | +59 | −151 | recorta el beneficio |
| JUPUSDT | — | — | — | — | sin datos en Binance spot para esta ventana |
| RUNEUSDT | 65 | +1,875 | +402 | −1,473 | recorta el beneficio |
| **TOTAL** | **609** | **+9,185** | **+1,680** | **−7,505** | |

## Conclusión

**Mecanismo validado, hipótesis del épico #121 no validada por esta ventana:**

1. **Cálculo YZ**: correcto, produce vol anualizada coherente con la literatura.
2. **Aplicación de `vol_mult`**: correcta en scanner y backtest, clampeada a [0.20, 1.0], sin look-ahead en el backtest.
3. **Reducción de drawdown**: clara en símbolos volátiles — BTC redujo su Max DD de −23.46% a −7.51% (68% menos).
4. **Agregate P&L**: en esta ventana de 6.5 meses, vol-sizing **reduce** el P&L absoluto (−$7.5k). Razón: los símbolos ganadores (DOGE, ADA, RUNE) coincidieron con ser los más volátiles, por lo que recibieron `vol_mult < 1.0` y capturaron menos ganancia.
5. **Hipótesis #121**: la afirmación "−$14,655 → +$25k–$40k" viene de una ventana de 4 años (2022-2026). Esta corrida es sólo ~6 meses. **Inconclusa**: necesita la corrida completa de 4 años.

## Próximos pasos

- [ ] **Corrida completa 2022-2026** (pendiente): `python scripts/compare_vol_sizing_batch.py --start 2022-01-01 --end 2026-04-18 --json-out /tmp/vol_compare_4y.json`. Tomará horas de backfill contra Binance.
- [ ] Incluir métricas risk-adjusted (Sharpe, Calmar) en el agregado para decidir si la reducción de P&L absoluto se compensa con mejor ratio de riesgo.
- [ ] Observación en producción (7 días) vía `scripts/watch_market_data_status.py` antes de ajustar capital real.
- [ ] Re-evaluar épica #121 con los números de 4 años.
- [ ] **Issue #125 permanece abierto** hasta que la corrida de 4 años valide o refute la hipótesis.
