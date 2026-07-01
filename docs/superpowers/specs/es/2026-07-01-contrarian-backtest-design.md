# Backtest de equity-curve de la jugada contrarian — Diseño

**Fecha:** 2026-07-01
**Estado:** barra pre-comprometida fijada ANTES de correr (Serrano BLOCKER #1/#3: definir éxito y la null antes de mirar el resultado).
**Origen:** el hallazgo contrarian (comprar alt viva en BTC-bear + escalera) dio media +4.58% por-trade — pero eso NO es una estrategia (es un promedio; mediana ~0; la media vive en la cola de 2 años; 2022 pierde). La junta del roster (opus, PR #624) exigió: antes de automatizar la señal a la cuenta del papá, medir la SECUENCIA — una curva de equity real que muestre si la cuenta sobrevive el sangrado de 2022 antes de la cola de 2023.

## La regla (mecánica, falsable)

En cada día con **BTC close < SMA200** (bear macro, point-in-time), abrir posiciones equal-weight en alts vivas (B2, excl. BTCUSDT); salir con la **escalera congelada** (`ladder_return`: TPS [.15,.30,.50,.90], FRACS [.25,.25,.20,.15], runner, piso −50%, HORIZON 30). Costo round-trip **2%** por posición.

## Mecánica del backtest (event-driven, diario)

- Capital arranca en 1.0. **M slots concurrentes**, cada slot = 1/M del capital (equal-weight).
- Simular día a día: en la fecha de salida de una posición (entrada + 30d), realizar su retorno neto en el slot. En día bear con slots libres, llenar cada uno con una alt viva no-tenida (pick determinista: rotación por hash de fecha, reproducible).
- Compone. Salida = curva de equity → CAGR, max drawdown, y la secuencia (comportamiento en 2022).
- **Sensibilidad:** correr M ∈ {5, 10, 20} (5%, 10%, 20% por posición) — el resultado depende de M; reportar los tres, no cherry-pick.

## Barra PRE-COMPROMETIDA (se automatiza la señal SOLO si TODO)

1. **(a) Equity terminal POSITIVO** (la cuenta termina arriba de donde empezó).
2. **(b) Sobrevive 2022:** max drawdown **< 50%** y no quiebra (equity nunca ≤ 0) durante el bear sostenido.
3. **(c) Le gana a buy-and-hold** de un basket equal-weight de las mismas alts (que no sea solo beta de mercado).

Se exige en la MAYORÍA de los M (≥2 de 3). Si no pasa → NO se automatiza; Valles queda como instrumento de contexto honesto.

## Caveats que el findings DEBE declarar (no lavar)

- **n = 1 bear.** 2022 es el único bear sostenido, y está IN-SAMPLE. No hay validación out-of-sample de "sobrevive bears" — solo tenemos uno. El backtest responde "¿sobrevive el 2022 que ya conocemos?", no "sobrevive bears en general".
- **Survivorship de cobertura:** el panel es point-in-time (una ganadora que después murió está capturada con su muerte), pero su universo (187 símbolos del ingest) no es total — puede sobre-representar sobrevivientes en AMBAS colas.
- **Pick-rule arbitrario:** cuál alt por slot afecta el resultado; por eso la rotación es determinista y se reporta sensibilidad a M.
- **OOS débil:** split 2021-2023 / 2024→2025-04, pero 2024-25 fueron años buenos → el OOS no prueba bear-survival.
- **La media por-trade (+4.58%) NO es la conclusión** — la curva de equity y su drawdown lo son.

## No-negociables

- **#2/#3:** solo `data/program_ohlcv.db`, período hasta **2025-04-29** (antes del holdout). Sin `open_holdout`/`simulate_strategy`/`data/holdout/`.
- **#4:** el sizing (fracción por slot) es un parámetro del ESTUDIO, no toca `RISK_PER_TRADE` de producción.

## Salida

`data/retune/2026-06-23-calibracion-gate-regimen/backtest_contrarian.py` (reusa el panel + `ladder_return`) + `backtest_findings.md` con el veredicto vs la barra. No entra a CI.
