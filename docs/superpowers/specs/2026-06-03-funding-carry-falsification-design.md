# Funding-Carry Falsification (liquid universe, 2024-26) — Design

**Fecha:** 2026-06-03
**Estado:** DISEÑO (pre-registro) — pendiente de revisión de Samuel antes del plan.
**Lineaje:** double-FAIL de la celda direccional ([[fork-arm-b-fail-arm-a-pending]]) → research del roster ([[edge-landscape-funding-carry]]) → primera falsificación de la celda ortogonal (TYPE 3: carry/funding).
**No dispara:** holdout #322 (ver §9). Sin ejecución live de perps. Sin `PositionClosure`.

---

## §1 · Pregunta (congelada)

¿El **funding carry delta-neutral** (long spot + short perp), sobre el universo de símbolos **líquidos** que el proyecto ya tiene, produce un retorno **net-of-v3-cost positivo** en 2024-01 → 2026-05 que **además sobrevive un stress short-vol** de cola?

**Por qué es la celda ortogonal:** no se hace forecast. El pagador es el long apalancado que paga funding (segmentación de mercado, BIS Crypto Carry). El costo se paga una vez (entrada/salida), no por-decisión — el inverso del impuesto que mató la TA. P&L **$-denominado** → esquiva el mirage sharpe↔net_pnl ([[capa1-search1-noedge-sharpe-misalignment]]).

**Límite de scope (pre-declarado):** universo líquido únicamente. Lyra ubica el edge real en el long-tail ilíquido (large-cap arbitrado por Ethena/ETFs). Por tanto **un FAIL aquí significa "el carry líquido está arbitrado", NO "no hay carry en ningún lado".** El long-tail es un test de seguimiento separado (data más sucia, cola peor), fuera de este spec.

---

## §2 · Universo y ventana (congelados)

- **Símbolos:** el set líquido = símbolos con (a) spot klines en `data/ohlcv.db` Y (b) perp USDⓈ-M en Binance con historia de funding. El conjunto exacto se fija en la ingesta vía filtro pre-declarado (cobertura completa de funding+markPrice+spot en la ventana); se reportan los incluidos/dropeados como provenance. Candidatos: BTC, ETH, ADA, AVAX, DOGE, UNI, XLM, PENDLE, RUNE, SOL, LINK (~8-10 tras filtro).
- **Ventana:** 2024-01-01 → 2026-05-31 (foco de la literatura de decay del funding-arb; cubierta por el spot existente).

---

## §3 · Datos (nuevo + reusado)

**Nuevo (ingester `tools/funding_carry/ingest.py` → tabla sqlite):**
- **Funding:** bulk `https://data.binance.vision/data/futures/um/monthly/fundingRate/{SYM}/{SYM}-fundingRate-{YYYY}-{MM}.zip` (desde 2020; con `.CHECKSUM`). Columnas: `fundingTime` (ms), `fundingRate`, `markPrice`. **Intervalo de funding NO constante** (8h vs 4h según par/época) → usar deltas de `fundingTime` del archivo, nunca hardcodear 3×/día. Gap-fill API: `GET https://fapi.binance.com/fapi/v1/fundingRate?symbol={SYM}&limit=1000&startTime={ms}` (público, sin auth).
- **Perp mark:** bulk `markPriceKlines/{SYM}/1h/{SYM}-1h-{YYYY}-{MM}.zip` (OHLCV-shaped → ingesta drop-in). Para marcar la pata perp y computar basis = mark − spot.

**Reusado:**
- **Spot:** `data/ohlcv.db` (klines 1h), ya presente.
- **Cost-model:** `backtest_costs.compute_trade_costs(model="v3")` + `costs_calibration.json` (active=v3).
- **Deflación:** tooling de deflated-Sharpe / N-effective del proyecto (#278/#538/#555).
- **Precedente de cola:** el proyecto ya toma en serio el tail (harness #552, cost-model v3). Gate B es self-contained (ver §6 — el #552 es un replay de kill-switch acoplado a la señal, NO un inyector de shock; no se reusa su harness).

---

## §4 · Simulador del carry (núcleo)

Posición delta-neutral por símbolo: long spot notional N, short perp notional N, delta≈0. N=$10,000 pre-declarado (retornos escala-invariantes en %).

**Esquema de hold (congelado):** hold continuo por símbolo sobre la ventana — **1 entrada al inicio de cobertura, 1 salida al final**, cobrando todo el funding intermedio. (Sin rebalanceo/roll en este primer test; pre-declarado por simplicidad y para evitar superficie de parámetros.)

**Contabilidad (cash-and-carry estándar):**
```
units u = N / spot_entry  (≈ N / perp_entry)
funding_pnl = Σ_i ( fundingRate_i × markPrice_i × u )      # short recibe cuando rate>0
basis_pnl   = − u × ( basis_exit − basis_entry )           # basis = mark − spot; convergencia favorable
gross       = funding_pnl + basis_pnl
costs_v3    = v3(spot RT) + v3(perp RT)   # 4 fills, TRANSACTION-only (enable_funding=False:
                                          # el funding ya está en funding_pnl con tasas reales;
                                          # incluirlo en el cost lo doble-contaría)
net         = gross − costs_v3
net_return  = net / N      (anualizado por la longitud de la ventana del símbolo)
```
- **Signo de funding:** Binance USDM — funding>0 ⇒ longs pagan shorts ⇒ nosotros (short perp) RECIBIMOS. Confirmar el signo contra un mes conocido en los tests.
- **Costos:** v3 con liquidez derivada de volumen (mismo proxy que `backtest.py:669`) en la barra del fill; notional = u × precio.

---

## §5 · Gate A — ¿existe carry net?

- **Estimando:** retorno net-of-v3 anualizado por símbolo y **pooled** (equal-weight). Bootstrap CI (10k, seed pre-declarado) sobre los símbolos; reportar también un block-bootstrap temporal como diagnóstico.
- **Deflación:** deflactar por el número de símbolos/variantes consideradas (DSR), reusando el tooling existente. No hay sweep de parámetros (hold congelado) → deflación-N es chica pero se aplica.
- **PASS_A:** CI inferior 95% del pooled net annualized return **> 0**.
- **Reportes:** por símbolo, pooled, con/sin el símbolo más influyente (LOO).

---

## §6 · Gate B — ¿sobrevive la cola? (short-vol gate)

El carry "positivo" es short-vol disfrazado (Null Vale): paga liso hasta el cliff (liquidación de la pata short, muerte de exchange, depeg). Sin este gate, un PASS_A es premium de cola no-priceado. **Gate B es self-contained** (el harness #552 es un replay de kill-switch sobre el stream de la señal, no aplica).

- **B1 — cola in-sample:** sobre la curva de equity del carry acumulado pooled: max drawdown, y el peor intervalo único (funding más negativo × notional + el mayor salto adverso de basis) en 2024-26. Reportar.
- **B2 — shock sintético pre-registrado (el gate de cola real):** inyectar un escenario calibrado a magnitud LUNA/FTX sobre la posición: (i) funding sostenido negativo de `−F bps` por `K` días, y (ii) un basis blowout donde el perp se disloca `Y%` del spot y luego converge (mark-to-market adverso en la pata short antes de converger, con riesgo de liquidación si el margen no aguanta). Parámetros `(F, K, Y)` congelados desde eventos históricos 2022 (LUNA/FTX) documentados en el research. **PASS_B:** el carry acumulado de la ventana sobrevive net-positivo (o la pérdida queda acotada bajo un límite de riesgo/stop pre-declarado) tras el shock.
- **Pre-declaración honesta:** 2024-26 puede NO contener un evento magnitud-2022 → B1 (in-sample) probablemente subestima la cola; **B2 (sintético) es el gate dominante**. Si B2 borra el carry, el "edge" es short-vol, no carry.

---

## §7 · Criterio KILL (ambos gates)

- **PASS:** PASS_A **Y** PASS_B(1∧2) — existe carry net-of-cost que sobrevive la cola. → candidato a estrategia (diseño de sizing/rebalance/universo es un fork posterior, NO este spec).
- **FAIL:** falla A (carry no-positivo net) **o** falla B (la cola lo borra → short-vol). → con el límite de scope §1: "el carry líquido está arbitrado o es short-vol"; el long-tail queda como decisión de continuación.
- **$-denominado** → esquiva el mirage sharpe↔net_pnl por construcción.

---

## §8 · Estructura de archivos

- `tools/funding_carry/__init__.py`
- `tools/funding_carry/constants.py` — símbolos candidatos, ventana, N, seeds, params de shock `(F,K,Y)`, URLs.
- `tools/funding_carry/ingest.py` — bulk download + parse zip-CSV `fundingRate` + `markPriceKlines` → sqlite (`data/funding.db`), con CHECKSUM y gap-fill API. Read-only sobre `ohlcv.db`.
- `tools/funding_carry/simulate.py` — el simulador delta-neutral (§4): funding accrual, basis, v3 recost.
- `tools/funding_carry/evaluate.py` — Gate A (bootstrap/LOO/deflación) + Gate B (B1 in-sample + B2 shock sintético) + verdict.
- `tools/funding_carry/run.py` — orquestador → `data/retune/2026-06-03-funding-carry-falsification/{verdict,per_symbol,findings}.json|md` + manifest (provenance: símbolos, seeds, params de shock, cost-model hash, ventana).
- `tests/test_funding_carry.py` — TDD: signo de funding, accrual, basis, recost v3, bootstrap determinista, los dos gates, verdict.

---

## §9 · No-Negociables respetados

- **NN#3 holdout:** NO lee `data/holdout/`, NO llama `open_holdout`, NO llama `simulate_strategy` con frames de holdout. Es una estrategia DISTINTA (carry delta-neutral) sobre data DISTINTA (funding/perp descargada fresca de Binance Vision + spot de ohlcv.db). No toca #322 ni la señal. Aunque la ventana 2024-26 solape en tiempo con el holdout, no se lee el dataset locked ni se corre la señal — no es un peek en el sentido de NN#3.
- **NN#2:** no se accede `data/holdout/`.
- **NN#4 RISK_PER_TRADE:** intacto — N/A (no es la señal; es un sizing delta-neutral propio del backtest de carry).
- **NN#1 PositionClosure:** N/A — sim offline, sin posiciones live.

---

## §10 · Qué NO es

- **No** es diseño de estrategia (sizing óptimo, rebalanceo, selección de universo) — eso es post-PASS.
- **No** incluye el long-tail ilíquido (§1 scope).
- **No** ejecuta perps live ni toca producción.
- **No** afirma generalización fuera de la ventana/universo testeado.

---

## §11 · Preguntas abiertas para el plan

1. Los params exactos del shock B2 `(F, K, Y)` — calibrar desde números históricos LUNA/FTX concretos en el plan (el research dio las fuentes).
2. ¿El filtro de símbolos exige cobertura 100% de la ventana, o admite ventanas por-símbolo distintas (entrada al inicio de cobertura de cada uno)? Diseño: ventana por-símbolo, anualizando por su longitud. Confirmar en el plan.
3. Liquidación en B2: ¿modelar margen explícito (y stop por liquidación) o asumir margen suficiente y medir solo el mark-to-market? Diseño: margen pre-declarado + stop por liquidación como el "límite de pérdida acotada" de PASS_B.

---

## §12 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-06-03 | Diseño de la primera falsificación de la celda ortogonal (funding carry) tras el double-FAIL + research del roster. Universo líquido, hold continuo, dos gates (carry net + cola short-vol). Gate B corregido self-contained (el #552 es kill-switch replay, no inyector de shock). | Claude Opus 4.8 + sssamuelll |
