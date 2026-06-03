# Funding-Carry Tail-Aware Kill Rule — Design

**Fecha:** 2026-06-03
**Estado:** DISEÑO (pre-registro) — pendiente de revisión de Samuel antes del plan.
**Lineaje:** funding carry = PASS ([[edge-landscape-funding-carry]], PR #557) → fork de diseño de estrategia → sub-proyecto #1 (de 4: riesgo tail-aware → rebalanceo → long-tail → live).
**No dispara:** holdout #322. Sin perps live. Sin `PositionClosure`. Extiende `tools/funding_carry/` (reusa `data/funding.db`, cero ingest nuevo).

---

## §1 · Pregunta (congelada)

El funding carry PASÓ (6.33%/año net-of-v3, CI[5.02,7.45], 9/9 líquidos positivos) pero Gate B2 fue **thin**: el carry anual (6.33%) < un solo shock (7.5%) = short-vol. La cola empírica 2024-26 (funding negativo 15–34% de los intervalos, rachas hasta 16d) **ya está absorbida** en ese +6.33%. La pregunta de #1:

> Con (a) **per-interval mark** (precisión) y (b) una regla de **KILL por funding-negativo** pre-registrada, ¿el carry sobrevive net-positivo una cola **PEOR que 2024-26** (M shocks sintéticos out-of-sample), Y el kill **AÑADE valor** (mejor net y/o menor max-DD) vs no-kill, neto del costo de churn?

Decide si el edge short-vol-thin se vuelve **tail-robusto y deployable**, o si el churn del kill se come el beneficio.

---

## §2 · Per-interval mark (corrección de precisión)

El falsification v1 usó mark constante (entry) en el accrual — conservador (subestima funding en activos que aprecian). #1 lo corrige:

- **funding_pnl con per-interval mark:** `Σ_i fundingRate_i × mark_i × units`, donde `mark_i` = perp mark close en (o antes de) `funding_time_i`, de `perp_klines`. units fijo (sin rebalanceo; eso es #2).
- Da la **curva de equity real en el tiempo** (no solo el total) — necesaria para max-DD verdadero y para la simulación con-kill.
- Probablemente **sube** el carry medido vs el v1 (el mark crece en el bull) → el PASS de Gate A es robusto a esto.

---

## §3 · KILL rule (PRE-REGISTRADA, K congelado)

- **Estado:** la posición está IN o OUT. Empieza IN al inicio de cobertura del símbolo.
- **Trigger de salida:** cerrar el carry cuando el funding lleva **≥ K = 24 settlements consecutivos negativo** (≈ 8 días a 8h). Ancla empírica: las caídas normales reversibles son cortas (el carry netó +6.33% absorbiéndolas); las peligrosas son inversiones sostenidas >1 semana (AVAX 16d, SOL 7d, XLM 8d). Un kill a 8d corta el episodio peligroso sin matar el carry normal.
- **Re-entrada:** re-entrar (IN) en el primer settlement con funding ≥ 0 después de un kill. Sin cooldown (pre-declarado, el más simple).
- **Costo de churn:** cada ciclo OUT→IN cobra v3 en las 4 patas de re-apertura + las 4 de la salida previa. El kill NO es gratis — el simulador lo cobra explícitamente.
- **CONGELADO IRREVOCABLE:** K=24, re-entrada-on-positive, sin cooldown. Cambiar cualquiera = experimento nuevo. La sensibilidad de K {9,18,24,36} se reporta **descriptiva** (§5), NO decide el veredicto (anti-overfit, patrón confirmatorio del Brazo A).

---

## §4 · Simulador con-kill (núcleo)

Por símbolo, sobre la ventana, con per-interval mark:

```
estado = IN; equity = 0; legs_open_at = entry
para cada settlement i (en orden de tiempo):
    si estado == IN:
        equity += fundingRate_i × mark_i × units            # cobra/paga funding
        si racha_negativa(i) >= K:                            # 24 settlements negativos
            equity -= v3_close(symbol, units, spot_i, perp_i) # cierra (2 RT, transaction-only)
            estado = OUT
    si estado == OUT y fundingRate_i >= 0:
        equity -= v3_open(symbol, units, spot_i, perp_i)      # re-abre
        estado = IN
al final: si IN, cobra v3_close (cierre final). Sumar basis_pnl(entry,exit) de los tramos IN.
net = equity - costos_v3_totales ; net_return = net / NOTIONAL ; annualized por longitud.
```
- **Baseline no-kill:** el mismo símbolo con per-interval mark pero SIN la regla (hold continuo) — para el reporte kill-vs-no-kill (§5).
- Reusa `backtest_costs.compute_trade_costs(model="v3", enable_funding=False)` (transaction-only; funding modelado explícito) y el liquidity proxy del v1.

---

## §5 · Reportes y comparación

- **Por símbolo y pooled (equal-weight):** net_return anualizado **con-kill** y **no-kill**; max-DD de la curva de equity (per-interval mark, time-ordered); número de ciclos kill/re-entry; costo total de churn.
- **kill-vs-no-kill:** Δ = net_with_kill − net_no_kill, pooled, con bootstrap CI (10k, seed congelado) sobre símbolos. ¿El kill mejora net? ¿reduce max-DD? (Un kill que baja DD pero también net es un trade-off explícito a reportar, no un FAIL.)
- **Sensibilidad K {9,18,24,36}:** net+maxDD pooled por cada K — DESCRIPTIVO. No gatea. Muestra si K=24 es una elección robusta o un filo.

---

## §6 · Gate de supervivencia de cola (decide deployabilidad)

- **G1 — supervivencia empírica:** con-kill, net pooled > 0 sobre 2024-26 (ya sabemos que el no-kill lo logra; G1 confirma que el kill no lo rompe por churn).
- **G2 — supervivencia out-of-sample (el gate real):** inyectar **M = 2 shocks sintéticos** (2022 = LUNA+FTX = 2 en ~6 meses) magnitud `SHOCK_FUNDING_PER_8H × SHOCK_DAYS` (0.5%/8h × 5d) en los 2 peores momentos de la curva con-kill. **El kill DEBE dispararse durante el shock** (el shock ES funding negativo sostenido > K) — esa es su razón de ser. PASS_G2 = con-kill, net pooled tras los 2 shocks ≥ 0; el kill acota la pérdida de cada shock a ~K settlements de bleed (no los 5 días completos).
- **Leverage:** constante conservadora **2x** pre-declarada → liquidación de la pata short necesita ~50% adverso, muy por encima de los moves de basis observados (la liquidación NO es el binding risk aquí; el funding-bleed sí, y el kill lo ataca). Modelado de margen explícito = sub-proyecto #2/#4, fuera de #1.

**VEREDICTO #1:** PASS = G1 ∧ G2 (el carry con-kill sobrevive la cola peor-que-empírica, net-positivo). Reporta si el kill AÑADE valor (kill-vs-no-kill) — un PASS con kill que NO mejora a no-kill significa "el carry ya es robusto sin kill" (resultado válido). FAIL = el churn del kill rompe G1, o ni con kill sobrevive G2 (el carry líquido es too-thin para deployment tail-aware → re-evaluar tamaño/universo).

$-denominado → esquiva el mirage sharpe.

---

## §7 · Estructura de archivos

Extiende `tools/funding_carry/`:
- `simulate.py` — añadir `funding_pnl_per_interval(funding, marks, units)` y `perp_mark_series(funding_db, symbol, times)` (lookup de mark por settlement).
- `kill_rule.py` (NUEVO) — `negative_run_lengths`, `simulate_with_kill(funding, marks, units, symbol, ..., K)` → curva de equity + ciclos + costo churn; `simulate_no_kill(...)` baseline.
- `evaluate.py` — añadir `kill_vs_nokill(...)`, `inject_shocks(equity_curve, M, ...)`, `gate_tail(...)` (G1∧G2) + `verdict_kill`.
- `run_kill.py` (NUEVO) — orquestador → `data/retune/2026-06-03-funding-carry-tail-kill/{verdict,per_symbol,findings}.json|md`.
- `constants.py` — añadir `KILL_K=24`, `K_SENSITIVITY=(9,18,24,36)`, `N_SHOCKS=2`, `LEVERAGE=2.0`.
- `tests/test_funding_carry.py` — TDD: negative_run, per-interval funding, kill simulator (entra/sale/re-entra + churn), kill-vs-nokill, shock injection, gate G1∧G2, verdict.

---

## §8 · No-Negociables respetados

- **NN#3 holdout:** sin `open_holdout`, sin `simulate_strategy` con frames de holdout. Reusa `data/funding.db` (pública) + spot. No toca #322 ni la señal.
- **NN#1/#4:** N/A — backtest offline, sin posiciones live, sin la señal/RISK_PER_TRADE.

---

## §9 · Qué NO es / techo

- **No** modela margen/liquidación explícito (palanca pasiva; #2/#4).
- **No** rebalancea/rolea (units fijo; #2).
- **No** toca el long-tail (#3).
- **No** ejecuta live (#4).
- In-sample 2024-26, 9 líquidos, un régimen. Un PASS de #1 dice "el kill hace el carry líquido tail-robusto in-sample contra 2 shocks sintéticos" — no "deployable en producción" (eso necesita #2-#4 + infra).

---

## §10 · Preguntas abiertas para el plan

1. `racha_negativa`: ¿cuenta settlements con `rate < 0` estrictos, o incluye `rate == 0`? Diseño: estrictos `< 0` rompen la racha en `>= 0`. Confirmar.
2. Posición de los 2 shocks G2: ¿los 2 peores drawdowns existentes, o 2 puntos equiespaciados? Diseño: los 2 settlements donde la curva con-kill tiene mayor vulnerabilidad (mayor equity acumulado a riesgo). Definir métrica exacta en el plan.
3. basis_pnl con kill: cada tramo IN tiene su propio entry/exit basis. Diseño: sumar basis por-tramo. Confirmar la contabilidad en el plan.

---

## §11 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-06-03 | Diseño de #1 (riesgo tail-aware) tras el PASS del funding carry. Per-interval mark + kill por funding-negativo (K=24/8d congelado) + gate de supervivencia (G1 empírico ∧ G2 2-shocks). Palanca primaria = kill (leverage 2x fijo). Reusa funding.db. | Claude Opus 4.8 + sssamuelll |
