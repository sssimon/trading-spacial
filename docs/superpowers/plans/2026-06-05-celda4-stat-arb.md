# Plan: celda 4 stat-arb — falsificador (spec REV 4)

**Spec:** `docs/superpowers/specs/2026-06-05-celda4-stat-arb-falsification-design.md`
(REV 4, commit `94d6232`). El spec manda; ante conflicto plan↔spec, gana el
spec y se corrige el plan.

**Forma:** clona `tools/funding_carry/` (paquete + tests + run determinista).
**Dependencia nueva:** `statsmodels` (ADF/Engle-Granger — el tool estándar de
la literatura; solo lo importan módulos del estudio, no el sistema vivo).
**Data:** `data/program_ohlcv.db` (`perp_klines`, `perp_funding`) — ingest
corriendo; la implementación no lo necesita (tests con fixtures), solo la
corrida final.

## Grupo 1 — constantes y moneda de costos

### Task 1: `tools/celda4_stat_arb/constants.py`
Parámetros IRREVOCABLES de §4, literales y comentados con su ancla:
`STUDY_START="2021-01-01"`, `STUDY_END="2025-04-30"` (frontera del holdout,
NV-A; **ningún módulo lee barras/funding/volumen ≥ STUDY_END**),
`FORMATION_DAYS=180`, `TRADING_DAYS=30`, `ADF_P=0.05`, `TOP_PAIRS=20`,
`MAX_PAIRS_PER_SYMBOL=1`, `Z_ENTRY=2.0`, `Z_EXIT=0.0`, `Z_STOP=3.0`,
`SIGMA_GUARD=1e-6`, `MIN_DOLLAR_VOL_DAILY=1_000_000`, `MIN_COVERAGE=0.95`,
`NOTIONAL_PER_LEG=10_000`, `BOOTSTRAP_N=10_000`, `SEED=20260605`,
`GATE_B_START="2023-03-01"` (punto medio derivado por regla),
`MIN_POSITIONS=30`, `CONCENTRATION_MAX=0.50`, `POWER_MULT=3.0`,
`V3W_REFERENCE_WINDOW=(STUDY_START, STUDY_END)` (para derivar cortes de tier),
`SOURCE_PRIMARY="celda4-stat-arb/primary"`, `OUTPUT_DIR=
"data/retune/programa-celdas/celda4-stat-arb"`.

### Task 2: `tools/celda4_stat_arb/costs.py` — v3w (§3-bis)
- Lee `costs_calibration.json` vía `backtest_costs.load_calibration()` y reusa
  `TierParams`/`GlobalParams`/`_v3_leg_cost` SIN modificarlos (leer
  `backtest_costs.py:438-520` antes de escribir).
- `derive_tier_cutoffs(db)`: mediana de dollar-volume diario de los 10
  curados (`_TIER_BY_SYMBOL`) sobre `V3W_REFERENCE_WINDOW` en `perp_klines`;
  cortes = puntos medios geométricos entre grupos de tier adyacentes;
  **falla duro si el mapeo resultante no reproduce el tier v3 de los 10**
  (el candado del spec). Devuelve `{cutoff_large, cutoff_mid}` + derivación.
- `tier_for_volume(median_dollar_vol, cutoffs) -> "large"|"mid"|"small"`.
- `v3w_fill_cost(notional_usd, tier, calibration) -> $` por fill (taker).
  Cierres forzosos por delisting: tier `small` siempre.
- Tests: derivación con db fixture (10 símbolos sintéticos con volúmenes que
  reproducen los tiers), monotonía de cortes, fallo duro si mapeo roto,
  costo small > mid > large a mismo notional.

## Grupo 2 — formación y simulación

### Task 3: `tools/celda4_stat_arb/pairs.py`
- `eligible_symbols(db, formation_start, formation_end)`: cobertura ≥95% de
  barras esperadas Y mediana de dollar-volume diario ≥ $1M, **función pura de
  barras < formation_end** (F2/F3: prohibido mirar más allá).
- `form_pairs(db, eligible, formation_start, formation_end)`: para cada
  combinación (orden lexicográfico: X=primero, Y=segundo): OLS log(Y) =
  α + β·log(X) sobre la intersección de barras con close de ambos; ADF
  (`statsmodels.tsa.stattools.adfuller`, `regression='c'`, `autolag='AIC'`)
  sobre residuos; guard σ_residuos < 1e-6 → excluido; candidatos p<0.05
  ordenados por p ascendente; greedy top-20 con máx. 1 par por símbolo.
  Congela (α, β, μ_spread, σ_spread) de formación.
- Tests con series sintéticas: par cointegrado construido (random walk X +
  ruido estacionario) pasa; par no-cointegrado (dos random walks
  independientes) no pasa; orientación lexicográfica fija; cap por símbolo;
  σ-guard; pureza de formación (un símbolo que delista 1 día después de
  formation_end ES elegible — test del anti-survivorship F3).

### Task 4: `tools/celda4_stat_arb/simulate.py`
- Por trading window (30d tras su formación, rolling sin solape, ventanas
  desde STUDY_START+180d hasta STUDY_END): para cada par formado, z(t) =
  (spread(t) − μ_form)/σ_form sobre barras del window; entrada |z|≥2 →
  fill al close de la barra SIGUIENTE (lag 1); salida cruce z=0; stop |z|≥3
  (sin re-entrada en el window tras stop); cierre forzoso a fin de window;
  delisting → cierre en última barra disponible.
- Posición: dollar-neutral $10k/pierna; units congeladas al fill de entrada.
- P&L = Δprecio de cada pierna × units ± funding neto: settlements de
  `perp_funding` con `entry_fill_time < t ≤ exit_fill_time`, cada uno
  `rate × close(hora del settlement) × units` (aprox. declarada F1), signo
  según lado de cada pierna.
- Costos: `v3w_fill_cost` × 4 fills (tier del símbolo por su volumen de SU
  formación; cierre forzoso por delisting → small).
- Output por posición: dict con par, window, tiempos, P&L bruto, costos,
  funding, neto.
- Tests deterministas con fixture db sintética: lag de ejecución (la señal en
  barra t llena en t+1), accrual de funding (boundary `(entry, exit]` exacto
  — test del off-by-one F13), stop sin re-entrada, cierre forzoso fin de
  window, delisting mid-window, frontera STUDY_END jamás cruzada.

## Grupo 3 — gates, poder, orquestación, candado

### Task 5: `tools/celda4_stat_arb/power.py` + `evaluate.py`
- `power.py`: desde `positions` computa N, σ por window, MDE (semi-ancho
  esperado CI95 del pooled vía bootstrap de windows), T_FLOOR_v3w (mediana de
  costo round-trip / (2×NOTIONAL), anualizada por mediana de holding).
  **Escribe `power.json` ANTES de que evaluate corra** (orden F10).
  `power_gate = MDE <= 3 × T_FLOOR_v3w`.
- `evaluate.py`, orden FORZADO: (1) kills — N<30; concentración (mayor
  contribución neta positiva de un par-window > 50% de Σ positivas, inerte
  si total ≤0); LOO subset <30; Gate-B subset <30. (2) lee `power.json` —
  si no existe, EXCEPCIÓN (orden violado); power gate falla → verdict
  N-INSUFICIENTE (la celda NO cierra). (3) Gate A: bootstrap por window
  (10k, SEED). (4) Gate B: windows con inicio ≥ 2023-03-01. (5) LOO por
  símbolo y por año. **PASS ⟺ A ∧ B ∧ LOO.** Per-posición bootstrap solo
  descriptivo.
- Tests: orden forzado (evaluate sin power.json → excepción), cada kill,
  power gate con fixtures, A∧B∧LOO con P&L sintético (caso "edge muerto":
  windows 2021-22 positivos + 2023+ negativos → Gate A pasa, B falla, FAIL).

### Task 6: `tools/celda4_stat_arb/run.py` + candado + registro
- `run.py`: fingerprint del input (membresía panel∩perps, row-counts,
  min/max open_time por símbolo — F14) → pairs → simulate → power →
  evaluate → artefactos en OUTPUT_DIR (`verdict.json` con coordenada
  E1/F/n_F=3 + manifest, `positions.json`, `pairs_formed.json`,
  `power.json`, `findings.md` esqueleto NO — findings lo escribe el humano).
- Trial registry: registra la corrida primaria con
  `source="celda4-stat-arb/primary"` (leer
  `.mex/patterns/registering-a-trial.md` y clonar cómo lo hizo carry).
- Candado del estudio en `tests/test_celda4_stat_arb.py`: además de los
  tests de los grupos — (a) los 10 curados mapean a su tier v3 bajo
  `derive_tier_cutoffs` (con fixture), (b) `STUDY_END == "2025-04-30"`
  literal, (c) ningún módulo del paquete contiene `data/holdout` ni
  `open_holdout` (grep test), (d) verdict.json del estudio (si existe)
  valida contra `_validate_verdict` de `tests/test_programa_celdas.py`.
- `requirements*`: añadir statsmodels donde corresponda (ver cómo está
  declarado pandas).

### Task 7: verificación final
Gate completo `python -m pytest tests/ -m "not network" -n auto -q` verde +
INDEX del programa: pregunta abierta nueva (la moneda v3w / mandato net-of-v3
de la constitución — Voronov V1) — NO se cambia el estado de la celda 4
todavía (sigue ABIERTA hasta el verdict).

## Reglas de ejecución

- Implementadores subagent SECUENCIALES (antipatrón: jamás en paralelo).
- Review de spec-compliance tras cada grupo contra el spec REV 4.
- La corrida del verdict NO es parte de este plan: requiere ingest completo +
  paquete verde + decisión explícita de correr (es one-shot).
