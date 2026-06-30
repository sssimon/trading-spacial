# Calibración del gate de exposición por régimen — Diseño (prueba de falsación)

**Fecha:** 2026-06-23
**Estado:** diseño aprobado (brainstorming), pendiente de plan.
**Precede a:** decidir si encender `cfg.regime_gate.enabled` en prod (gate mergeado en `1fc513f`, PR #622, hoy APAGADO).

## Objetivo

Decidir, con evidencia point-in-time, **si el gate de exposición por régimen debe encenderse** — y si sí, con qué umbrales. NO es "tunear umbrales para que el gate dispare": es una **prueba de falsación** que mide si los estados del régimen (`alts`/`mixto`/`btc`) separan los retornos forward de las candidatas alt en la **dirección que el gate asume**, con un criterio de aceptación pre-comprometido que tiene permiso de concluir **"no enciendas"** (o "el gate está invertido").

## Contexto verificado (por qué falsación y no calibración ingenua)

- El estudio multi-régimen 2020-2025 (`data/retune/2026-06-18-setup-edge-multiregimen/`) midió la población del gate (alts vivas con `pos_in_30d_range≤0.25`) por bucket de breadth. Resultado traducido a la dirección del gate: las candidatas rindieron **MEJOR en bear** (≈`btc`, donde el gate ESCONDE: +1.6 pp, único edge significativo) y **PEOR en alt-bull** (≈`alts`, donde el gate DEJA PASAR: −3.0 pp). Con esa evidencia, **la dirección del gate parece invertida.**
- **Pero esa evidencia es la menos confiable que hay**, por dos razones que esta calibración corrige:
  1. **Survivorship:** el estudio corre sobre el universo vivo de hoy (Binance exchangeInfo). El bucket bear/`btc` es el MÁS inflado (las golpeadas que no se recuperaron se delistaron y no están). Corregido por supervivencia, las candidatas en `btc` rendirían PEOR → lo que podría APOYAR al gate. El "+1.6 en bear" puede ser artefacto.
  2. **Régimen breadth-solo:** el estudio bucketea por breadth con cortes fijos (0.6/0.4); el gate de producción vota con **3 componentes** (breadth50 + outperf_30d + dominancia_btc). El régimen real puede separar distinto.
- Esta calibración corre sobre datos **point-in-time** (con delistadas) y el **régimen de 3 componentes** real, para resolver ambas dudas.

## Decisiones de diseño (tomadas en brainstorming)

1. **Falsación, no tuneo:** criterio de aceptación pre-comprometido que puede decir "no enciendas" o "invertido".
2. **Enfoque A:** test a los **umbrales de producción** primero (limpio, sin overfitting en la decisión); grid-search **exploratorio después** (cota superior, no la decisión; si sugiere región que funciona, exige validación temporal en un follow-up).
3. **Dominancia:** dataset BTC.D **congelado** como CSV en el dir del estudio, con procedencia documentada (igual que el cache de klines).
4. **Panel:** **anti-survivorship** `data/program_ohlcv.db` (187 spot, point-in-time, retiene delistadas).

## No-negociables respetadas

- **#2/#3 (holdout):** se usa `data/program_ohlcv.db` (NO `data/holdout/`), se lee directo, **sin `open_holdout`, sin `simulate_strategy`**. La ventana del holdout es **2025-04-30 → 2026-04-30** (`.mex/context/decisions.md:39`). Para no hacer un peek parcial (#3 muere en peeks parciales), el **período de señales termina el 2025-04-29** — la barra antes del holdout. El holdout queda intacto.
- **#4 (RISK_PER_TRADE):** no aplica — la calibración no toca sizing.
- **#6 (specs autoritativas):** este spec NO cambia código de producción; produce evidencia + (si pasa) valores de `umbral_overrides` para `config.json`. El encendido mismo es un cambio operacional separado (gobernado por el spec del gate `2026-06-23-regimen-al-trade-gate-design.md` §"Precondiciones de activación").

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

## Salidas
- **`data/retune/2026-06-23-calibracion-gate-regimen/`** (nuevo dir): `METODOLOGIA.md` (congela esta metodología), el CSV BTC.D congelado + su procedencia, el harness (`calib_study.py`), `results.json` (separación por estado, a producción + grid), `findings.md` (veredicto honesto: PASA / NO PASA / INVERTIDO, con los caveats).
- **Reproducible:** stdlib + numpy/pandas/scipy; lee `program_ohlcv.db` + el CSV congelado; no red en la corrida de decisión (la descarga del BTC.D es un paso previo, congelado).

## Caveats que el findings DEBE declarar
- Survivorship: aunque el panel retiene delistadas, su cobertura no es total (187 símbolos, los que el ingest 2026-06-05 capturó); es un límite inferior del sesgo, no su eliminación.
- Quote-vol derivado (`volume × close`) ≠ quote-vol exacto de Binance; afecta el gate de vida marginalmente.
- BTC.D de fuente externa congelada: declarar fuente + fecha; verificar que el metric coincide con el `/global` de CoinGecko que usa producción (deriva mínima esperada).
- Retorno en USDT incluye beta de BTC.
- El test a umbrales de producción NO sufre overfitting; el grid SÍ — reportarlos separados, el grid como exploratorio.

## Fuera de alcance (diferido)
- El **encendido** mismo (config.json + flag) — gobernado por las "Precondiciones de activación" del spec del gate; ocurre solo si esta calibración PASA.
- Validación temporal train/validate (enfoque B) — solo si el grid exploratorio sugiere una región mejor que producción y se quiere refinar.
- Re-correr el estudio de la firma musikito sobre el panel anti-survivorship (otra deuda, separada).

## Items abiertos (para el plan)
- Fuente exacta del CSV BTC.D y su formato (fecha, valor 0-1 o 0-100); normalizar a lo que `compose_regime` espera (`btc_dominance` como fracción 0-1, comparado con DOM_ALT=0.50 / DOM_BTC=0.58).
- Reusar `compose_regime` directamente vs espejo (si se reusa, hay que alimentarlo con `alt_contribs`/`btc_ret_30d`/`btc_dominance`/`coverage_ratio` por fecha — confirmar la firma).
- Resample 1h→diario: confirmar el manejo de días incompletos / gaps del panel (coverage.json enumera gaps por símbolo).
- Confirmar que `pos_in_30d_range`, `rsi14`, etc. se computan igual que `screener/valley_filter.measure_setup` (consistencia con la población real del screener).
