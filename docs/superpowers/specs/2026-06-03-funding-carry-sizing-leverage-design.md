# Funding-Carry Sizing / Leverage — Design

**Fecha:** 2026-06-03
**Estado:** ❌ MUERTO — falsificado en auditoría (Adrian Serrano, 2026-06-03) y confirmado por junta directiva (Voronov/Lyra/Cassian/Halberg/Richter + Axiom-0). Para un carry delta-neutral, Calmar = r/d es invariante a leverage; leverage es ESCALA, no edge. El gate G3 estaba pre-condenado. NO implementar. Se conserva como referencia del error de tipo (interrogar una perilla de implementación como si fuera fuente de edge). El siguiente movimiento es shadow-deploy (eje de realizabilidad), no calibración. Ver §0.

## §0 · Veredicto de la junta (por qué este spec está muerto)

Axiom-0 reveló el invariante: **un edge confirmado no es una conclusión que se refina — es un TIPO que solo se mide donde su incertidumbre vive, y la única incertidumbre que caduca es la que el backtest no puede ver.** El carry comprime tres afirmaciones de tres tipos: supervivencia (¿existe? — resuelta, fósil), realizabilidad (¿sobrevive al contacto con lo inobservable? — solo medible en shadow, con reloj corriendo por el decay del short-vol thin) y robustez-de-fuente (¿es fuente o punto? — cola cross-símbolo + siguiente edge ortogonal, esperan sin coste). Sizing/leverage (este spec) y rebalanceo pertenecen al eje de CALIBRACIÓN, que está muerto-por-tipo: no es falsable como edge. El eje vivo y caducante es realizabilidad → shadow-deploy.

---

**Lineaje:** funding carry = PASS ([[edge-landscape-funding-carry]], PR #557) → fork de diseño de estrategia → sub-proyecto #1 (riesgo tail-aware, PR #558) = **PASS pero el kill NO añade valor** (el carry líquido ya es tail-robusto; palanca real = sizing/leverage) → **sub-proyecto #2 (sizing/leverage)**.
**No dispara:** holdout #322. Sin perps live. Sin `PositionClosure`. Extiende `tools/funding_carry/` (reusa `data/funding.db` + `data/ohlcv.db`, cero ingest nuevo).

---

## §1 · Pregunta (congelada) — y la trampa del mirage

#1 mantuvo **leverage = 2x** y **units fijos**, y difirió explícitamente el modelado de margen/liquidación a #2 (kill-spec §75/§104). El carry netea **+6.33%/año sobre equity a 2x**. La tentación es: "subir leverage multiplica el carry → más es mejor."

**Eso es un mirage y el spec lo nombra para no caer:** el retorno-sobre-equity del carry escala ~lineal con leverage L, **y el shock-bleed short-vol también escala ~lineal con L**. El *ratio* carry/shock es invariante a L. Subir leverage NO mejora la robustez short-vol. Lo único que leverage cambia de forma **no-lineal** es el **riesgo de liquidación de la pata short** — el evento de camino que #1 no modeló.

Por eso la pregunta de #2 **no** es "¿leverage mejora el retorno?" (trivialmente sí = mirage), sino:

> Modelando margen y liquidación explícitos, ¿existe una leverage L\* **> 2x** a la que el carry delta-neutral líquido (a) **nunca se liquida** sobre 2024-26 y (b) **sobrevive net-positivo-sobre-equity** los M=2 shocks sintéticos out-of-sample — es decir, hay **headroom** para sizing arriba del 2x conservador de #1? ¿Y ese L\* mejora el **net ajustado-a-riesgo sobre equity** de forma CI-significativa vs 2x, o el extra retorno se lo come el extra max-DD?

**Decide:** si sizing/leverage es una palanca real de valor (L\* > 2x con margen de seguridad) o si el carry ya está en su techo de tamaño seguro (L\* ≤ 2x → sizing no es palanca, FAIL).

---

## §2 · Qué significa "leverage" en delta-neutral

Posición: long spot N, short perp N (delta ≈ 0). Equity `E` respalda ambas patas vía margen. Leverage `L = (notional por pata) / E`.

- **Ingreso de carry sobre equity** ∝ L (más notional cobrando funding por dólar de equity).
- **Bleed de shock** (episodio de funding negativo) ∝ L → linealmente igual que el ingreso (ratio invariante).
- **Liquidación de la pata short:** el perp short se liquida si el precio del perp sube adversamente ≈ `(1/L − m)` donde `m` = maintenance margin. La pata spot GANA en ese move (delta-neutral económico), **pero la liquidación es un evento de camino sobre el margen del perp en aislamiento** — el exchange liquida el perp antes de que la ganancia spot lo respalde, salvo que haya rebalanceo/cross-margin. **Esta es la no-linealidad que leverage introduce y la que #2 debe modelar.**

Implicación de diseño: el **objetivo no puede ser retorno-sobre-equity** (monótono en L = mirage). Debe ser **net ajustado-a-riesgo sobre equity con liquidación como restricción dura de camino**.

---

## §3 · Estimando (CONGELADO) — $-denominado sobre EQUITY, no % equal-weight

**Corrección keystone (glitch heredado [[capa1_search1_noedge_sharpe_misalignment]]):** `gate_a` v1 corre sobre `net_return_annual` = (net / NOTIONAL fijo), equal-weight entre símbolos. Bajo cambios de leverage el % equal-weight **se desalinea** del P&L en $ sobre equity desplegado. #2 congela:

- **Equity desplegado** `E` por símbolo = margen requerido para sostener la posición a leverage L (no el notional). Net en $ se normaliza por `E`, no por NOTIONAL.
- **Métrica primaria:** `net_on_equity_annual(L)` = (funding + basis − v3 − pérdida_por_liquidación) en $ / E, anualizado. Pooled = **$-weighted** (suma de $-net / suma de equity), NO promedio de %.
- **Métrica de riesgo:** max-DD de la curva de equity-sobre-equity (per-interval mark, time-ordered) + **bandera de liquidación** (binaria por símbolo: ¿la curva tocó liquidación en algún intervalo?).
- **Métrica ajustada-a-riesgo:** `net_on_equity_annual(L) / max_dd_on_equity(L)` (Calmar-sobre-equity). Es el objetivo que decide L\* vs 2x.
- $-denominado en todos lados → esquiva el mirage sharpe que mató a Capa 1.

---

## §4 · Margen + liquidación (lo que #1 difirió) — PRE-REGISTRADO

- **Maintenance margin** `m` congelado (ej. 0.5% — confirmar tier real Binance perp en §10-B). Liquidación de la pata short cuando el precio perp sube ≥ `(1/L − m) × precio_entry_perp` respecto al margen de ese tramo.
- **Modelo de margen (FORK §10-A — decide la severidad):** ISOLATED (margen fijo por pata, sin top-up de la ganancia spot → conservador, L\* bajo) vs REBALANCED/CROSS (la ganancia spot respalda el margen del perp → L\* alto). Esto ES la pregunta "rebalanceo" del fork original resurgiendo como decisión de modelado concreta.
- **Pérdida por liquidación:** si se liquida, pérdida = margen posteado de esa pata + cierre forzado al peor precio del intervalo (conservador). Un símbolo liquidado **envenena su net** (no se promedia silenciosamente).
- Reusa `backtest_costs.compute_trade_costs(model="v3", enable_funding=False)` para los costos de transacción (transaction-only; funding modelado explícito como en v1/#1).

---

## §5 · Leverage L_test (CONGELADO con ancla, anti-overfit) + sweep descriptivo

Patrón confirmatorio heredado del Brazo A / kill (§36 kill-spec): **gatear sobre UN L_test pre-registrado con ancla empírica, NO sobre el máximo-in-sample** (eso sería overfit).

- **Ancla de L_test:** el peor move adverso de la pata short observado en 2024-26 (intervalo único + sostenido peor-caso), con **factor de seguridad 2×**: liquidación a L_test debe necesitar ≥ 2× el peor move observado. L_test sale de una regla, no de un fit.
- **CONGELADO IRREVOCABLE:** L_test (valor exacto fijado en el plan tras medir el peor move), `m`, modelo de margen (§10-A), factor de seguridad = 2. Cambiar cualquiera = experimento nuevo.
- **Sweep DESCRIPTIVO {1, 2, 3, 5, 8, 10}x:** net_on_equity, max-DD, ¿liquida?, Calmar por cada L. **NO gatea el veredicto.** Muestra si L_test es robusto o un filo, y dónde está el cliff de liquidación.

---

## §6 · Gate de veredicto (decide si sizing es palanca)

- **G1 — supervivencia in-sample a L_test:** sobre 2024-26, a L_test, net_on_equity pooled > 0 **y cero liquidaciones**. (Si liquida o se vuelve negativo a L_test, el ancla de seguridad 2× era insuficiente → FAIL.)
- **G2 — supervivencia out-of-sample (gate real):** inyectar M=2 shocks sintéticos (LUNA+FTX magnitud, mismos `SHOCK_FUNDING_PER_8H`/`SHOCK_DAYS` que #1) en los 2 peores momentos de la curva a L_test. PASS_G2 = net_on_equity pooled tras los 2 shocks ≥ 0 **y sin liquidación gatillada por el shock**.
- **G3 — palanca de valor (el que justifica #2):** Calmar-sobre-equity a L_test **>** Calmar a 2x, con bootstrap CI (10k, seed congelado `20260603`) sobre símbolos, CI-lo del Δ > 0. Si L\*=L_test no bate a 2x de forma CI-significativa → sizing NO es palanca de valor (resultado válido, análogo a "el kill no añade valor" de #1).

**VEREDICTO #2:**
- **PASS** = G1 ∧ G2 ∧ G3 → sizing/leverage ES palanca: correr a L_test > 2x mejora el net ajustado-a-riesgo sin romper la cola.
- **PASS-SIN-VALOR** = G1 ∧ G2 ∧ ¬G3 → el carry es seguro a L_test pero sizing no mejora risk-adjusted vs 2x; 2x se queda como tamaño canónico (análogo #1).
- **FAIL** = ¬G1 ∨ ¬G2 → no hay headroom seguro arriba del 2x conservador; el carry líquido está en su techo de tamaño. Re-evaluar (rebalanceo explícito #3? universo?).

---

## §7 · Estructura de archivos

Extiende `tools/funding_carry/`:
- `simulate.py` — `equity_for_leverage(...)`, `liquidation_check(perp_path, L, m, margin_model)` → curva de equity-sobre-equity por símbolo a leverage L con margen/liquidación.
- `sizing.py` (NUEVO) — `worst_adverse_short_move(perp_klines, window)` (ancla de L_test), `simulate_levered(funding, marks, perp_path, units, L, m, margin_model)` → curva + bandera de liquidación + costos.
- `evaluate.py` — `gate_g1_g3(...)`, `calmar_on_equity(...)`, `leverage_value_delta(L_test, baseline=2.0)` (bootstrap CI del Δ Calmar), `verdict_sizing`.
- `run_sizing.py` (NUEVO) — orquestador → `data/retune/2026-06-03-funding-carry-sizing/{verdict,per_symbol,sweep,findings}.json|md`.
- `constants.py` — `MAINT_MARGIN`, `LEVERAGE_SWEEP=(1,2,3,5,8,10)`, `SAFETY_FACTOR=2.0`, `MARGIN_MODEL`, `BASELINE_LEVERAGE=2.0`, `OUTPUT_DIR_SIZING`. (L_test no se hardcodea hasta medir el peor move en el plan.)
- `tests/test_funding_carry.py` — TDD: worst-move ancla, simulate_levered (equity, costos), liquidation_check (gatilla / no-gatilla en bordes conocidos), Calmar, leverage_value_delta CI, gates G1/G2/G3, verdict.

---

## §8 · No-Negociables respetados

- **NN#3 holdout:** sin `open_holdout`, sin `simulate_strategy` con frames de holdout. Reusa `data/funding.db` (pública) + spot `ohlcv.db`. No toca #322 ni la señal.
- **NN#1/#4:** N/A — backtest offline, sin posiciones live, sin `PositionClosure`, sin la señal/RISK_PER_TRADE.

---

## §9 · Qué NO es / techo

- **No** ejecuta live ni modela infra de margin-call real-time (#4).
- **No** toca el long-tail / universo iliquido (#3 — el universo se queda en los 9 líquidos de #1).
- **No** optimiza el timing de entrada/salida (eso es señal, no sizing).
- In-sample 2024-26, 9 líquidos, un régimen + 2 shocks sintéticos. Un PASS de #2 dice "sizing a L_test es seguro y de valor in-sample contra 2 shocks" — NO "deployable" (eso necesita #3/#4 + infra + holdout #322 cuando se decida).

---

## §10 · Forks abiertos — DECISIÓN DE SAMUEL antes de Adrian / plan

**§10-A (BLOCKER de scope — define todo el spec):** ¿El modelo de margen de #2 es **ISOLATED** (conservador: cada pata su margen, sin que la ganancia spot respalde el perp → L\* bajo, "¿cuánto leverage aguanta SIN rebalanceo?") o **REBALANCED/CROSS** (la ganancia spot respalda el margen del perp → L\* alto, "¿cuánto con rebalanceo?")? El fork original del funding-carry listaba "rebalanceo" como sub-proyecto separado; la memoria reformuló #2 como "sizing/leverage". **ISOLATED primero** mantiene #2 puro (sizing) y deja rebalanceo como #3 — es mi recomendación (más conservador, falsificación más limpia). Confirmar.

**§10-B:** Maintenance margin `m` — ¿0.5% fijo conservador, o el tier real escalonado de Binance perp por símbolo? Diseño: 0.5% fijo conservador para el draft; tier real si el veredicto sale marginal.

**§10-C:** Posición de los 2 shocks G2 — mismo criterio que #1 (los 2 settlements de mayor vulnerabilidad de la curva). Heredado, confirmar consistencia.

---

## §11 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-06-03 | DRAFT de #2 (sizing/leverage) tras #1 = PASS-sin-valor-del-kill. Nombra el mirage de leverage (retorno monótono en L), congela estimando $-sobre-equity (fix del glitch sharpe equal-weight), trae margen/liquidación que #1 difirió, gate G1∧G2∧G3 (supervivencia + palanca-de-valor vs 2x). Fork §10-A (isolated vs rebalanced margin) pendiente de Samuel. | Claude Opus 4.8 + sssamuelll |
