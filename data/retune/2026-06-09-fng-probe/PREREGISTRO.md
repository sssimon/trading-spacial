# Pre-registro — Sondeo de tractabilidad C1: Fear & Greed (sentiment)

**Estado:** SONDEO PRE-CELDA (candidato a Edición 2). NO es una celda de Edición 1;
NO emite un `verdict.json` de celda formal. Su único propósito: decidir SI vale
abrir una celda formal de sentiment.
**Criterios congelados:** 2026-06-09, ANTES de correr o de mirar un solo resultado.
**Origen:** junta de tipado 2026-06-09 — C1 (F&G) = PERSEGUIR-Y-PROBAR; C2 (order-flow)
y C3 (fundamentales) = MUERTOS-AL-LLEGAR. Diseño de la prueba: Cassian + El Cuantitativo
+ Voronov + Null Vale.

## Hipótesis (Tipo B — señal de MERCADO; Tipo A PROHIBIDO)

El nivel extremo del índice Fear&Greed (F&G), indexado as-of, porta información sobre
el retorno forward **neto-de-v3**, en dirección **CONTRARIAN**.

- **PROHIBIDO el Tipo A** ("estoy greedy → presiono más fuerte" = falacia del jugador =
  celda 1 reanimada = `q3_pass:false`). Este sondeo NO testea la conducta del operador;
  testea si el F&G **como variable** predice el retorno de mercado.

## Estrategia pre-declarada (contrarian)

- **LONG** en días de Extreme Fear: F&G as-of **≤ 25**.
- **SHORT** en días de Extreme Greed: F&G as-of **≥ 75**.
- Umbrales **FIJOS** (no cuantiles → cero look-ahead en el threshold).
- Hold: **H = 5 días** de trading (UNO, declarado; no grid de horizontes).
- Notional **FIJO $1000** por trade → pooling $-weighted (evita la trampa Sharpe
  %-equal-weight que ya mordió al proyecto).

## Población

- Los **10 símbolos curados** de `data/ohlcv.db` (donde v3 cobra), timeframe **1d**.
- F&G market-wide (UNA serie), `data/backtest/fear_greed_history.csv`, indexado as-of
  por el `open_time` del bar (`df_fng.index <= bar_time`, `.iloc[-1]`) — réplica exacta
  del patrón sin look-ahead de `backtest.py:280-282`.

## Costo (net-of-v3)

- RT floor por tier (major 13 / mid 18 / small 30 bps) + funding add-on =
  `funding_bps[tier] × floor(H_horas / 8)`, con `H_horas = 5×24 = 120` → 15 intervalos.
- `net_ret = gross_ret − cost_bps/1e4`. Se reportan **gross**, **RT-only-net** y
  **full-net**. El **gate corre sobre full-net** (el más conservador).

## Métrica

- P&L $ por trade = `1000 × net_ret`, con el signo según LONG/SHORT.
- **Pooled mean $ P&L por trade.**
- **CI95 por BLOCK-BOOTSTRAP POR EPISODIO** (no por trade ni por día): los días extremos
  son contiguos y altamente correlacionados; la unidad independiente es el **EPISODIO**
  (run contiguo de F&G en zona extrema, market-wide), no el trade. Resample de episodios
  con reemplazo, 10000 iteraciones, **seed fijo (42)**.

## Corte temporal (commoditización — caveat heredado de celda 8)

- Sub-períodos pre-declarados: **PRE-2021** (entry ≤ 2020-12-31) y **POST-2021**
  (entry ≥ 2021-01-01).
- El F&G es un agregado público difundido (mismo perfil de riesgo que la celda 8
  on-chain, DEGRADADA por commoditización). Hipótesis de supervivencia declarada: es un
  índice **diario y grueso**, no premia al rápido — el retail no "llega tarde" a un valor
  diario, a diferencia de las whale-alerts que mataron on-chain.

## Gates / kill (pre-declarados, no se mueven tras ver resultados)

- **PASS** (vale abrir celda de Edición 2): `mean $ P&L > 0` **Y** el CI95
  (block-bootstrap por episodio) **EXCLUYE cero**, **en el sub-período POST-2021**.
- **DEGRADADA**: pasa en PRE-2021 pero NO en POST-2021 (commoditización confirmada).
- **FAIL** (no vale la celda): CI95 incluye cero en POST-2021, o full-net ≤ 0.

## Poder declarado

- N efectivo = número de **EPISODIOS independientes** en POST-2021 (no días, no trades).
- Si `N_episodios post-2021 < 10` por leg, el sondeo se marca **UNDERPOWERED** y el
  resultado de esa leg es **INCONCLUSO**, no FAIL (un FAIL underpowered no distingue
  "no hay edge" de "no tenía cómo verlo").

## Candados

- **Cero holdout (#322):** usa `data/ohlcv.db` (research) + `fear_greed_history.csv`.
  NO toca `data/holdout/`, NO llama `open_holdout` ni `simulate_strategy` con holdout-window.
- **Determinista:** seed fijo, una corrida, sin re-correr "hasta que pase" (data-dredging
  prohibido).
- **Atlas:** sin ranking cardinal cross-celda; este sondeo solo se compara consigo mismo.

## Qué significa el resultado

- PASS → abrir una celda formal de sentiment en Edición 2 con junta propia; el copiloto
  de Mercado gana algo honesto **y** con confianza ganada que mostrar.
- FAIL → "momentos greedy" no porta ventaja medible; la negativa del copiloto honesto
  queda vindicada (no es deflación arbitraria) y la palanca real sigue siendo costo/varianza
  + el carry.
- DEGRADADA → tuvo edge pre-2021, murió por commoditización (como on-chain).
