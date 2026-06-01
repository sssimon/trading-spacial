# Diagnóstico del modelo de costo — re-anclar vs reconstruir (Phase 0 de #14)

- **Fecha:** 2026-06-01
- **Autor:** Samuel + Claude (Opus 4.8)
- **Estado:** diseño aprobado, pendiente de plan de implementación
- **Issue relacionado:** #14 (recalibrar el modelo de costo contra ejecución real)
- **Tipo:** diagnóstico read-only que ramifica (no es un fix ni una recalibración)

## 0. Por qué este documento existe

El diagnóstico del 2026-06-01 (`data/retune/2026-06-01-base-edge-diag/FINDINGS.md`)
estableció que el modelo de costo del backtest (`backtest_costs.py` +
`costs_calibration.json`) produce slippage catastrófico (−90% sobre pre-holdout,
91% de eso slippage) que la data live **falsifica**: 27 posiciones cerradas en
producción muestran 52% win / +$30 net con costos reales, vs el backtest
prediciendo bancarrota — con **inversión de signo** por símbolo (AVAX-short:
backtest −$7,345 vs live +$35). El costo real implícito es ~5-15 bps vs los
45-700 bps del modelo.

El motor de costo es **backtest-only** (no alcanzable desde la ruta live:
PositionClosure, scanner, API). El riesgo es de **gate de promoción** — el
backtest predice catástrofe donde la realidad muestra break-even, envenenando
toda decisión basada en backtest (#272 re-baselining, "fórmula ganadora", la
premisa entera del stress-replay del kill-switch).

Plumb Lindgren dimensionó la recalibración completa en **L**, con 3-4 decisiones
arquitectónicas. La decisión de scope (2026-06-01) fue **diagnosticar-luego-decidir**:
no comprometer re-anclaje ni reconstrucción hasta medir cuál defecto domina. Este
documento diseña ese diagnóstico.

## 1. Objetivo y criterio de éxito

Determinar si el defecto dominante del modelo de costo es **corregible con
re-anclaje** (basis de participación y/o constantes de calibración) o **requiere
reconstrucción** (el framework sqrt-participation + proxy de liquidez está mal de
raíz).

Entregable: un findings-doc con (a) el factor de sobre-cobro medido contra live,
(b) la corrección candidata ganadora si existe, y (c) **la recomendación de rama,
pre-registrada**. El diagnóstico es exitoso si produce una rama clara
(re-anclar / reconstruir) — independientemente de cuál salga.

El diagnóstico **no toca producción** y **no cambia** `costs_calibration.json`.
Solo decide cuál de las dos specs siguientes escribir.

### Holdout (No-Negociable #3)

Los 27 trades live son de 2026-05-21 en adelante — **después** del cierre del
holdout (2026-04-30). Están fuera de la ventana locked; sin conflicto con #3. Son
trades de producción reales, no simulación: no se invoca `simulate_strategy` ni
`open_holdout`.

## 2. La medición de falsificación (Approach A — el core)

Para cada uno de los 27 trades live cerrados:

- `observed_move_pct = |exit_price − entry_price| / entry_price` — el viaje real
  de precio entre fills.
- `model_cost_bps` — se recalcula lo que el modelo cobraría:
  `compute_trade_costs(symbol, entry_notional=size_usd, exit_notional=size_usd,
  entry_liquidity=liq@entry_ts, exit_liquidity=liq@exit_ts,
  holding_hours=(exit_ts−entry_ts), tier_params, model='v2')`, usando el **mismo
  proxy de liquidez del backtest** (`_usd_per_min = (close×volume)/60`, rolling
  720 sobre barras 1H). La data 1H de 2026-05 está disponible localmente.
- **Flag de falsificación:** si el trade ganó (`pnl_usd > 0`) **y**
  `model_cost_bps/100 > observed_move_pct` → contradicción dura: el modelo afirma
  que el slippage se comió más que todo el viaje de precio, pero el trade fue
  rentable. Imposible si el costo modelado fuera real.
- `over_charge_ratio = (model_cost_bps / 100) / observed_move_pct` por trade.

Esta es la versión rigurosa y por-trade de la inversión de signo de AVAX. Usa solo
precios de fill + el recompute del modelo; no requiere separar costo de movimiento.

### Liquidez en los timestamps live

`liq@ts` se computa idéntico al backtest: barras 1H del símbolo, USD por minuto
`(close×volume)/60`, media móvil de 720 barras (30 días). Para timestamps de
2026-05 hace falta ≥30 días de barras 1H previas, disponibles. Si la liquidez en
algún `ts` resulta NaN/no observable, ese trade se reporta aparte (mismo trato que
el fallback de 100 bps del modelo) y no se cuenta en el factor agregado.

## 3. Barrido de corrección y decisión de rama (pre-registrado)

Se recalcula `model_cost_bps` sobre los mismos 27 trades bajo correcciones
candidatas:

- **(a) Basis diario:** participación medida contra `liquidez × 1440` (volumen
  diario) en vez de por-minuto.
- **(b) `size_factor` escalado:** el `size_factor` por tier dividido por un factor
  de un **barrido fijo predefinido** — `{√1440 ≈ 37.95, 31.62, 10}` — NO ajustado
  a la respuesta. (El primero es la equivalencia per-minuto→diario bajo sqrt; los
  otros dos acotan la sensibilidad.) Esto evita la circularidad de fit-a-target que
  el diseño le critica al cross-check B.
- **(c) Ambas** (basis diario + cada divisor del barrido).

### Umbrales pre-registrados (no se mueven tras ver resultados)

Una corrección **"reconcilia"** si, sobre los 27 trades live:

1. ya **no excede** `observed_move_pct` en **ningún** trade ganador (criterio
   primario, tier-agnóstico y riguroso), **y**
2. su costo round-trip mediano aterriza en banda plausible **por tier**:
   **≤ 30 bps** major/mid, **≤ 50 bps** small, a tamaño ~$644 (techos generosos; el
   real implícito es ~5-15 bps). El piso es la base del modelo (no puede bajar de
   `base_bps`). Los 27 trades cubren los tres tiers (major BTC/ETH, mid
   ADA/AVAX/DOGE/UNI/XLM, small JUP/RUNE).

### Decisión de rama

- **≥1 corrección reconcilia** → rama **RE-ANCLAR** (opción 1, ~M). La spec
  siguiente calibra esa corrección contra el full pre-holdout (cross-check B) y
  re-ancla los asserts de anchor-parity en los tests.
- **Ninguna corrección reconcilia** → rama **RECONSTRUIR** (opción 2). El framework
  sqrt+liquidez es el defecto; la spec siguiente diseña recolección de data real
  (post-trade Binance, el "v3" que el propio modelo anticipa) + re-derivación.

Mover estos umbrales después de ver resultados invalida el diagnóstico.

## 4. Cross-checks (respaldo, no decisores)

- **C — slippage real de entrada:** `scan.price` (precio en la señal) vs
  `position.entry_price` (fill), enlazados por `positions.scan_id`. Acota el
  slippage real de entrada, conflado con el delay del operador (entra minutos/horas
  después de la señal). Sirve de cota superior de cordura, no de ancla precisa.
- **B — sanidad del full backtest:** re-correr el pre-holdout bajo la corrección
  ganadora y verificar que el −90% se vuelve plausible (break-even, consistente con
  el break-even live). Confirmación, no decisor (riesgo de circularidad).

Ambos respaldan la rama; la decisión la toma la sección 3.

## 5. Guards

- **Read-only:** live en `mode=ro` (cero escrituras a prod, cero reinicios de
  servicios); recompute offline sobre los trades + proxy de liquidez; cero cambios
  a `costs_calibration.json` o a cualquier código de producción.
- **Pre-registro:** los umbrales de la sección 3 quedan fijados aquí.
- **No-Negociable #5:** el diagnóstico es **relativo** (modelo vs realidad live);
  no cita absolutos pre-#223/#224 como baseline.
- **Aislamiento del motor:** el recompute importa `backtest_costs` directamente; no
  modifica el motor, solo lo invoca con inputs alternativos.

## 6. Salidas

Directorio `data/retune/2026-06-01-cost-model-diagnosis/`:

- `findings.md` — factor de sobre-cobro (mediano + conteo de ganadores excedidos),
  resultado de cada corrección candidata, cross-checks C y B, **rama recomendada**
  con la corrección ganadora (si la hay).
- `per_trade.json` — por trade live: symbol, direction, size_usd, entry/exit ts y
  price, observed_move_pct, liq@entry, liq@exit, model_cost_bps (baseline + cada
  corrección), over_charge_ratio, flag de falsificación.

## 7. Componentes e interfaces

- `tools/cost_diagnosis/live_trades.py` — carga read-only de los 27 trades cerrados
  desde prod (`mode=ro`); retorna lista de dicts. Aislado para que el resto corra
  offline sobre el dump.
- `tools/cost_diagnosis/recompute.py` — dado un trade + parámetros de liquidez,
  invoca `backtest_costs.compute_trade_costs` bajo baseline y cada corrección;
  retorna el dict de costos. Reusa el motor, no lo duplica.
- `tools/cost_diagnosis/liquidity.py` — computa `liq@ts` desde barras 1H idéntico
  al backtest (factorizado para test independiente).
- `tools/cost_diagnosis/reconcile.py` — aplica los umbrales pre-registrados de la
  §3 a los resultados por-trade; retorna `(branch, winning_correction)`.
- `tools/cost_diagnosis/run.py` — driver: carga live → recompute baseline+correcciones
  → reconcile → escribe `findings.md` + `per_trade.json`.

Cada unidad tiene un propósito único y se testea sola.

## 8. Testing

- `recompute.py`: un trade conocido (symbol, size, liquidez fija) → `model_cost_bps`
  esperado contra un cálculo a mano de la fórmula sqrt.
- `liquidity.py`: serie 1H sintética → `liq@ts` esperado (incluye el caso NaN/fallback).
- `reconcile.py`: casos sintéticos por-trade que reconcilian / no reconcilian →
  rama esperada. Cubre el borde "ningún ganador excedido pero costo > 30 bps"
  (no reconcilia) y "reconcilia exacto en el techo".

## 9. Fuera de alcance (YAGNI)

- La recalibración en sí (re-anclar o reconstruir) — es la spec **siguiente**, que
  esta rama elige.
- Recolección sistemática de fills reales de Binance (el "v3") — solo si la rama
  sale RECONSTRUIR.
- Tocar `costs_calibration.json` o el motor `backtest_costs.py`.
- Cualquier cambio a la ruta live de producción.

## 10. Criterio de éxito del experimento

Exitoso si produce una **rama clara y pre-registrada** (RE-ANCLAR / RECONSTRUIR)
con el factor de sobre-cobro y los cross-checks que la sustentan. Una rama
RECONSTRUIR honesta (el modelo está mal de raíz) es tan válida como una RE-ANCLAR.
El valor está en saber cuál de las dos specs escribir, con evidencia.
