# Pre-registro — Sondeo de tractabilidad: valle-calidad (consolidación geométrica)

**Estado:** SONDEO PRE-CELDA (candidata a Edición 2 — celda "B" del trío valle A/B/C).
NO es una celda de Edición 1; NO emite un `verdict.json` de celda formal. Su único
propósito: decidir GO/NO-GO a una celda formal que estudie la *gradación* de calidad
de valle.
**Criterios congelados:** 2026-06-11, ANTES de correr o de mirar un solo resultado.
**Origen:** sesión de diseño con Samuel (2026-06-11), tras la crítica ontológica de
Voronov sobre la "Vista Valles". La pieza A (screener de vida + consolidación,
observabilidad) ya está mergeada (#584). El ranking por calidad — pieza B — es un claim
de mercado y solo puede mostrarse si gana el derecho vía falsificación. Este probe es el
paso barato GO/NO-GO antes de invertir en la celda formal.
**Molde:** réplica estructural del probe Fear&Greed (`data/retune/2026-06-09-fng-probe/`).

## Hipótesis (Tipo B — señal de MERCADO; Tipo A PROHIBIDO)

Estar en **consolidación geométrica** porta información sobre el retorno forward
**net-of-v3w**, en dirección **LONG**, **por encima del baseline de no-consolidación en
los mismos símbolos**.

- **PROHIBIDO el Tipo A** ("siento que este valle es bueno → presiono más fuerte" =
  falacia del jugador = celda 1 reanimada = `q3_pass:false`). El probe NO testea el
  juicio del operador sobre un valle; testea si la consolidación **como variable
  geométrica** predice retorno.
- El claim **NO** es "comprar valles sube" (eso sería beta de mercado: el cripto subió).
  El claim es "comprar valles sube **más que comprar no-valles** en los mismos símbolos".
  El edge es la **diferencia** tratamiento − control.

## Definición de valle (reusa el screener A — pieza ya mergeada, sin reinventar)

- **En consolidación** ≡ `screener.valley_filter.measure_consolidation(bars).en_rango == True`
  (rango `(max_high − min_low)/mediana_close ≤ 0.25` sobre la ventana de 84 días).
- **Episodio de valle** = run contiguo de días con `en_rango == True` para un símbolo.
- **Episodio de no-valle** = run contiguo de días con `en_rango == False` para ese símbolo.
- Barras diarias = resample de `spot_klines` (1h) del panel a 1d.

## Estrategia pre-declarada (tratamiento vs control)

- **Tratamiento (valle):** LONG el **primer día** de cada episodio de valle, notional
  **FIJO $1000**, hold **H = 20 días** de trading (UNO, declarado; no grid de horizontes).
- **Control (no-valle):** LONG el **primer día** de cada episodio de no-valle en los
  **mismos símbolos**, mismo notional $1000, mismo H = 20, mismo costo.
- **Edge medido:** `mean($_valle) − mean($_no_valle)`, net-of-v3w. Pooling $-weighted
  (notional fijo → evita la trampa Sharpe %-equal-weight que ya mordió al proyecto).
- Si una posición no alcanza H días porque el símbolo **delista** antes, se cierra al
  último precio disponible (el "valle muerto" entra al P&L; NO se descarta — esa es la
  razón de usar el panel anti-survivorship).

## Población

- **Panel de 187** anti-survivorship, tabla `spot_klines` de `data/program_ohlcv.db`
  (2021-01 → 2025-04, regenerable con `python -m tools.program_ingest.run`). Incluye las
  73 delistadas retenidas: un valle que termina en delisting es el contrafactual crítico
  de la hipótesis y debe entrar al contraste, no desaparecer (survivorship bias = inflaría
  la hipótesis).
- **Ventana del estudio:** 2021-01-01 → **2025-04-30** (pre-holdout en TIEMPO, no solo en
  directorio; misma frontera que la celda 4).

## Costo (net-of-v3w)

- **v3w** (la moneda de costos del panel amplio): RT floor por tier, tier asignado por
  volumen mediano del símbolo vía `tools.celda4_stat_arb.costs.derive_tier_cutoffs`
  (regla declarada de la celda 4, procedencia propia — NO se inventa un tier nuevo).
- **Sin funding:** es spot (`spot_klines`), no perp. El add-on de funding de la celda 4
  NO aplica.
- `net_ret = gross_ret − cost_bps/1e4`. Se reportan **gross** y **net**. El **gate corre
  sobre net** (el más conservador).

## Métrica

- P&L $ por entrada = `1000 × net_ret`, LONG.
- Estadístico = **diferencia de medias** `mean($_valle) − mean($_no_valle)`.
- **CI95 por BLOCK-BOOTSTRAP POR EPISODIO** (no por entrada ni por día): las entradas con
  hold 20d se solapan y los días de un episodio están correlacionados; la unidad
  independiente es el **EPISODIO**. Resample de episodios de ambos grupos con reemplazo,
  **10000 iteraciones, seed fijo (42)**. CI95 sobre la diferencia.

## Gates / kill (pre-declarados, no se mueven tras ver resultados)

- **PASS** (vale abrir celda formal de Edición 2): `mean($_valle) − mean($_no_valle) > 0`
  **Y** el CI95 (block-bootstrap por episodio) de la diferencia **EXCLUYE cero por el
  lado POSITIVO**.
- **FAIL** (no vale la celda): el CI95 de la diferencia **incluye cero**, o la diferencia
  es **negativa** (los valles rinden igual o peor que el baseline).

## Poder declarado

- N efectivo = número de **EPISODIOS de valle independientes** en la ventana.
- Si `N_episodios_valle < 30`, el probe se marca **UNDERPOWERED** y el resultado es
  **INCONCLUSO**, no FAIL (un FAIL underpowered no distingue "no hay edge" de "no tenía
  cómo verlo"). El umbral 30 (> el 10 del F&G) refleja que el estadístico es una
  diferencia de dos grupos, no una media simple.

## Robustez (reporte, NO gate)

- La diferencia se reporta partida en dos mitades temporales: **2021-01 → 2023-03** y
  **2023-03 → 2025-04**. Sirve para ver si la señal, de existir, es estable o concentrada
  en un régimen. El **gate corre sobre la ventana completa**, no sobre las mitades.

## Candados

- **Cero holdout (#322):** usa `data/program_ohlcv.db` (panel de investigación). NO toca
  `data/holdout/`, NO llama `open_holdout` ni `simulate_strategy` con holdout-window.
- **Determinista:** seed fijo, **una corrida**, sin re-correr "hasta que pase"
  (data-dredging prohibido).
- **Atlas:** sin ranking cardinal cross-celda; este sondeo solo se compara consigo mismo.
  El verdict es net-of-v3w, incomparable cardinalmente con el carry (celda 2, net-of-v3)
  sin re-pricing explícito.

## Qué significa el resultado

- **PASS** → la consolidación aporta sobre el baseline de no-consolidación; se abre una
  celda formal en Edición 2 (con junta de apertura propia) que estudia la **gradación**
  de calidad (qué tan estrecho / largo / seco = mejor). Solo si esa celda formal pasa, el
  ranking se gradúa y puede mostrarse en la UI del screener A. El probe NO gradúa el
  ranking por sí mismo — solo decide si vale estudiarlo.
- **FAIL** → "estar en el valle" no porta ventaja medible sobre comprar al azar en los
  mismos símbolos. La idea del ranking muere barato (como F&G). La lista del screener A se
  queda **neutral para siempre** — que es exactamente lo correcto si no hay edge que
  rankear. La neutralidad de A queda vindicada, no como límite técnico sino como verdad.
- **UNDERPOWERED** → no hubo suficientes episodios de valle independientes para ver una
  diferencia que importe; el probe es inconcluso y se documenta la N observada (no se
  fuerza un veredicto).
