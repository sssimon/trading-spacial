# Resultados de Vol-Normalized Position Sizing — #125

**Fecha:** 2026-04-20
**Spec:** `docs/superpowers/specs/en/2026-04-18-market-data-layer-design.md`
**Plan:** `docs/superpowers/plans/2026-04-18-market-data-layer.md` (Fase 8)
**Issue:** #125

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

## Comparativa

> **Nota:** la corrida comparativa completa (2022-01-01 → 2026-04-18, 10 símbolos) requiere varias horas de _backfill_ + simulación contra Binance. Los valores `[FILL]` se completan al ejecutar:
>
> ```bash
> # Baseline (antes de Fase 8)
> git checkout $(git merge-base HEAD origin/main)~1
> python backtest.py --start 2022-01-01 --end 2026-04-18 --output /tmp/baseline.json
>
> # Con vol-normalized sizing
> git checkout -
> python backtest.py --start 2022-01-01 --end 2026-04-18 --output /tmp/vol_sized.json
>
> python scripts/diff_backtest.py /tmp/baseline.json /tmp/vol_sized.json
> ```

| Métrica | Baseline (sin vol) | Con vol sizing | Delta |
|---|---|---|---|
| Total P&L ($) | [FILL] | [FILL] | [FILL] |
| Max drawdown (%) | [FILL] | [FILL] | [FILL] |
| Sharpe | [FILL] | [FILL] | [FILL] |
| Profit Factor | [FILL] | [FILL] | [FILL] |
| Trades | [FILL] | [FILL] | [FILL] |

## Contribución por símbolo

| Símbolo | Baseline ($) | Vol sizing ($) | Delta ($) | Vol anualizada |
|---|---|---|---|---|
| BTCUSDT | [FILL] | [FILL] | [FILL] | [FILL] |
| ETHUSDT | [FILL] | [FILL] | [FILL] | [FILL] |
| ADAUSDT | [FILL] | [FILL] | [FILL] | [FILL] |
| AVAXUSDT | [FILL] | [FILL] | [FILL] | [FILL] |
| DOGEUSDT | [FILL] | [FILL] | [FILL] | [FILL] |
| UNIUSDT | [FILL] | [FILL] | [FILL] | [FILL] |
| XLMUSDT | [FILL] | [FILL] | [FILL] | [FILL] |
| PENDLEUSDT | [FILL] | [FILL] | [FILL] | [FILL] |
| JUPUSDT | [FILL] | [FILL] | [FILL] | [FILL] |
| RUNEUSDT | [FILL] | [FILL] | [FILL] | [FILL] |

## Conclusión

- **Hipótesis del épico #121**: pasar de -$14,655 (pérdida histórica con sizing fijo) a un rango de +$25,000–$40,000 al escalar inversamente al riesgo.
- **Validación**: pendiente de la corrida comparativa. Si el swing total P&L se acerca al objetivo: VALIDADO, cerrar #125. Si no: iterar sobre los clamps (`VOL_MIN_FLOOR`, `VOL_MAX_CEIL`, lookback, `target_vol`).

## Próximos pasos

- [ ] Corrida comparativa completa (bloqueado por tiempo de cómputo, ~horas contra Binance live).
- [ ] Llenar tablas `[FILL]` y rehacer este documento con números reales.
- [ ] Observación en producción (4 semanas) antes de ajustar capital real.
- [ ] Revisión de épica #121 con los resultados.
- [ ] Si validado: `gh issue close 125`.
