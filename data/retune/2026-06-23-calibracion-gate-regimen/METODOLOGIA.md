# Metodología — Calibración del gate de exposición por régimen (prueba de falsación)

**Congelada:** 2026-06-23. Esta metodología no se modifica una vez iniciada la corrida.
**Spec de origen:** `docs/superpowers/specs/es/2026-06-23-calibracion-gate-regimen-design.md`

---

## Metodología

### Datos
- **Panel:** `data/program_ohlcv.db::spot_klines` (`symbol, open_time, open, high, low, close, volume`), 1h → **resamplear a diario** (open=primer open del día UTC, high=max, low=min, close=último, volume=suma).
- **Quote-vol:** el panel solo trae `volume` base. Derivar `quote_vol ≈ volume × close` por barra diaria (para el gate de vida `mediana(quote_vol,30d) ≥ 500_000` y `vol_ratio`). Declarar la aproximación.
- **Período de señales:** 2021-01-01 + warmup (SMA50/ventanas 30d/90d → primeras ~90 barras sin features) → **2025-04-29**. Reportar también por año.
- **BTC.D congelado:** serie diaria de dominancia BTC de fuente documentada (TradingView BTC.D / Investing / dataset público), guardada como CSV en el dir del estudio con su procedencia + fecha de descarga. El estudio la lee del CSV (reproducible).

### El régimen de 3 componentes (replica `regime/alt_season.py::compose_regime`)
Por cada fecha `t` del panel, computar el estado votado (no breadth-solo):
- **breadth50** = fracción del universo vivo con `close > SMA50` en `t`.
- **outperf_30d** = mediana sobre alts vivas de `(ret_30d_alt − ret_30d_BTCUSDT)` (BTCUSDT está en el panel; excluirlo de las alts).
- **dominancia_btc** = valor del CSV BTC.D congelado en `t`.
- Voto determinista con los umbrales (BREADTH_ALT/BEAR, OUTPERF_ALT/BEAR, DOM_ALT/BTC) + gobierno de evidencia (`COVERAGE_MIN`, `MIN_LIVE_VOTERS`) **idéntico a `compose_regime`** → estado ∈ {`alts`, `mixto`, `btc`}. Reusar la función real o un espejo verificado contra ella.

### Población y métrica
- **Población = regla-mínima:** `vivo AND pos_in_30d_range ≤ 0.25` — exactamente las candidatas que el gate esconde. (NO la conjunta: el estudio ya mostró que "agarra cuchillos".)
- **Métrica primaria:** `median(max_fwd_14d)` (excursión; el operador maneja el exit, que era el edge real de musikito). Entrada = `open` en `t+1`, igual que el estudio existente.
- **Cross-checks:** `rule_return` (TP +20% / SL −12% / cierre t+14, SL primero si ambos) y `win15` (`max_fwd_14d ≥ 0.15`).
- Por cada candidata, **bucketear por el estado de régimen de 3 componentes** en su fecha. Baseline B2 = universo vivo en `t` (mismo día).

### El test (enfoque A)
1. **A los umbrales de producción** (los provisionales de `regime/alt_season.py`): clasificar y medir la separación. Esta es la decisión.
2. **Grid-search exploratorio** (después): variar los 6 umbrales en una grilla gruesa, reportar el mejor como **cota superior** con la advertencia de overfitting. NO enciende por sí solo; si sugiere una región, el follow-up es la validación temporal (train 2021-2023 / validate 2024-2025-04).

---

## Criterio de aceptación (pre-comprometido)

**Encender el gate SOLO si TODO se cumple, a umbrales de producción, sobre el panel anti-survivorship:**

- **(a) Separación direccional:** `median(max_fwd_14d | 'alts') − median(max_fwd_14d | 'btc') ≥ +2 pp` **Y** las candidatas en `'btc'` rinden por **debajo de B2** (universo vivo) — si no, esconderlas solo pierde trades buenos.
- **(b) Significancia:** Mann-Whitney one-sided (`'alts' > 'btc'` en `max_fwd_14d`) **p < 0.01**.
- **(c) Cross-check realizado:** la dirección **no se invierte** en `rule_return` (no es artefacto de excursión).

**Resultados posibles:**
- **PASA (a∧b∧c):** los 6 umbrales (los de producción, o los del grid SI se valida temporalmente en follow-up) → `umbral_overrides` en `config.json` de prod + `enabled=true`; vigilar la tasa de supresión desde `regime_gate_audit`.
- **NO PASA:** **no enciendas.** El gate queda apagado (byte-idéntico, como está hoy). Documentar la evidencia.
- **INVERTIDO** (`'btc'` candidatas le ganan a `'alts'` con significancia): veredicto explícito — *el gate está al revés; no lo embarques como está, reconsidéralo*. La calibración tiene permiso de matar el feature.

Números movibles (pre-comprometidos ANTES de correr, para no mover el poste): margen **+2 pp**, **p<0.01**.

---

## Caveats

- **Survivorship:** aunque el panel retiene delistadas, su cobertura no es total (187 símbolos, los que el ingest 2026-06-05 capturó); es un límite inferior del sesgo, no su eliminación.
- **Quote-vol derivado** (`volume × close`) ≠ quote-vol exacto de Binance; afecta el gate de vida marginalmente.
- **BTC.D de fuente externa congelada:** declarar fuente + fecha; verificar que el metric coincide con el `/global` de CoinGecko que usa producción (deriva mínima esperada).
- **Retorno en USDT** incluye beta de BTC.
- **El test a umbrales de producción NO sufre overfitting; el grid SÍ** — reportarlos separados, el grid como exploratorio.

---

## Procedencia del BTC.D

**Estado: PENDIENTE DE ADQUISICIÓN**

El archivo `btc_dominance.csv` aún no ha sido descargado. Debe obtenerse antes de correr el estudio.

**Formato requerido:**
```csv
date,dominance
2021-01-01,69.7
2021-01-02,68.9
...
```
- Columnas: `date` (ISO `YYYY-MM-DD`) y `dominance` (porcentaje 0-100 O fracción 0-1; el loader normaliza automáticamente).
- **Rango requerido:** `2021-01-01 → 2025-04-29` (diario). Si no se cubre el rango completo, `load_btc_dominance` fallará ruidosamente (gap-check en Task 4).

**Fuente sugerida:** TradingView ticker `BTC.D` (export histórico), Investing.com "Bitcoin Dominance" históricos, o CoinGecko `/global` endpoint archivado.

Una vez obtenido, documentar aquí: fuente exacta, URL de descarga, fecha de descarga, y rango real cubierto.
