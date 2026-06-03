# Funding-Carry Shadow-Deploy v0.1 — Design

**Fecha:** 2026-06-03
**Estado:** DISEÑO (pre-registro) — REV 3, cierra los 2 BLOCKERs nuevos de la re-auditoría de Adrian (N1 anualización, N2 circularidad). Listo para el plan.
**Lineaje:** funding carry = PASS ([[edge-landscape-funding-carry]], PR #557) → #1 kill = PASS-sin-valor (PR #558) → #2 sizing = ❌ muerto-por-tipo (junta + Axiom-0) → **shadow-deploy v0.1** (eje de realizabilidad, el único cuyo instrumento de medida caduca).
**No dispara:** holdout #322. Sin `PositionClosure`. Sin posiciones live, sin órdenes, sin capital. Proceso SEPARADO de `btc_scanner.py`. Solo datos públicos (Binance FAPI).

> **Aclaración de tipo (corregida en REV 3):** el ESTADÍSTICO de decay NO es flujo incremental — es **recomputar `carry_for_symbol` sobre una ventana móvil de `W` semanas** (cada ventana tiene inicio/fin → entry/exit definidos → el modelo de window-cerrado del fósil aplica idéntico). El "flujo incremental sin exit" solo aplica a la reconciliación per-settlement (§5), que es SECUNDARIA y no gatea el veredicto. La rev 2 sobre-generalizó el problema incremental al estadístico; REV 3 lo confina a §5.

---

## §1 · Pregunta (congelada)

El carry netea **+6.33%/año net-of-v3** en backtest (CI95[5.02, 7.45]), donde ese número es la **media equal-weight de `net_return_annual = net/notional/years` sobre 9 símbolos** (`evaluate.gate_a` → `arr.mean()`; `simulate.py:88`), leverage-free, funding marcado a **entry-mark constante** (`funding_pnl`, `simulate.py:26`). Ventana 2024-01-01 → 2026-05-31. Es un fósil: cero datos post-merge.

**Universo congelado (los 9 del verdict, enumerados):**
`BTCUSDT, ETHUSDT, ADAUSDT, AVAXUSDT, DOGEUSDT, UNIUSDT, XLMUSDT, RUNEUSDT, PENDLEUSDT`.
Dropeados por cobertura en el backtest: `LINKUSDT, SOLUSDT` — **excluidos del shadow** aunque tengan cobertura live (o el pool no es comparable al 6.33%).

> ¿El carry medido en VIVO, recomputado idéntico al fósil sobre una ventana móvil de `W` semanas, **persiste** dentro de la banda del backtest — o el edge ya se arbitró (CI vivo cae bajo el CI-lo 5.02% de forma sostenida)?

**Hipótesis nula falsable (decay-kill, §6):** "el edge persiste". El shadow GENERA el dato out-of-sample que no existe.

---

## §2 · Scope honesto — qué mide v0.1 y qué NO (CRÍTICO)

- **✅ v0.1 MIDE — persistencia/decay del carry:** recomputa el MISMO estadístico del fósil (`net_return_annual`, §3) sobre datos vivos already-settled. Determinista, sin capital ni órdenes. Primer y más barato sub-eje de realizabilidad.
- **❌ v0.1 NO MIDE — fricción de ejecución:** leg-lag, slippage, timing de margin-call, downtime (Halberg: inobservable sin paper-execution = **v0.2**, o capital = **#4**). v0.1 usa el mismo cost-model v3 que el backtest — por construcción NO ve que la ejecución real difiera del modelo.
- **Consecuencia de tipo:** decay-kill que NO dispara → "el carry persiste", NO "deployable con capital". §6 es asimétrico: solo REFUTA.

---

## §3 · Estadístico de decay (CONGELADO) — REUSA `carry_for_symbol`, idéntico al fósil

Resuelve N1 (la rev 2 inventó una fórmula de tasa adimensional incompatible). El estadístico vivo es **la misma función del fósil**, sin fórmula nueva:

- **Por símbolo, sobre la ventana móvil de `W` semanas más reciente:** `carry_for_symbol(symbol, funding=<settlements en la ventana>, spot_entry/exit, perp_entry/exit=<precios en los bordes de la ventana>, liq, notional=NOTIONAL)` → su `net_return_annual` (`simulate.py:88`). Esto da, por construcción idéntica al fósil: funding a **entry-mark constante**, basis, costo v3 de round-trip amortizado sobre `window_hours = W semanas` (constante, no creciente → sin sesgo de warm-up, cierra BLOCKER 1/2), anualizado por **span temporal** (`window_hours/24/365`, NO por conteo de settlements).
- **Pooled = media equal-weight per-símbolo** sobre los 9 (réplica de `gate_a` → `arr.mean()`). A NOTIONAL uniforme y cobertura completa esto coincide con $-weighted; **bajo cobertura desigual (ventana incompleta, §4) NO coincide** — por eso el estadístico se declara explícitamente como equal-weight-per-símbolo por comparabilidad con el fósil, sin reclamar $-realizabilidad (N4).
- **CI vivo:** bootstrap (`BOOTSTRAP_N`/`SEED` del backtest) sobre los 9 `net_return_annual`, EXACTAMENTE como `gate_a`. El CI vivo se computa **de los datos vivos** → captura el SE del régimen vivo (no el del fósil) → controla la tasa de falso-REFUTED in-régimen (parte de la respuesta a N2).
- **Mark per-settlement:** NO entra en el estadístico (el fósil usa entry-mark constante; usar per-settlement rompería comparabilidad — N1/H10). Solo entra en la reconciliación secundaria §5.
- **Provenance:** cada registro estampa `calibration_identity_hash` del cost-model v3.

---

## §4 · Datos live (el ingest diario)

- **Funding settled reciente:** `GET fapi/v1/fundingRate?symbol=…&limit=L` (`constants.FAPI_FUNDING`). `L` congelado (§11-A) para cubrir gap + back-fill.
- **Mark (para §5 secundario):** `GET fapi/v1/markPriceKlines?symbol=…&interval=1h&limit=…` → puebla `perp_klines` **al MISMO grano que el fósil** (1h, no 8h — `ingest.py:112` usa 1h; resuelve N5), `INSERT OR IGNORE`. NO `premiumIndex`.
- **Spot:** ticker spot FAPI independiente (desacopla del scanner — §11-B). Entra solo en `basis_pnl` (bordes de ventana).
- **Persistencia:** append idempotente a `funding.db` (PK `(symbol, funding_time_ms)`). **Política de gap fail-safe:** si faltan settlements que `L` no cubre, la ventana se marca **incompleta** y el decay-kill NO se evalúa esa corrida.
- **Frecuencia:** 1×/día post-settlement, proceso separado. Fail-soft por símbolo.

---

## §5 · Reconciliación per-settlement (SECUNDARIA — sanity check operativo, NO la señal de decay)

Resuelve N3 (el baseline naïve enmascara decay). Se declara explícito: **§5 NO mide decay** (eso es §6 sobre el CI del estadístico §3). §5 es un sanity check operativo de un paso.

- Unidad = **settlement de 8h** (la corrida diaria es solo polling cadence; resuelve BLOCKER 3). Por cada settlement `s` ingerido: `realized_s = fundingRate_s × mark_settled_s × units`; `expected_s` = proyección naïve (última tasa observada persiste); `drift_s = realized_s − expected_s`.
- **Qué mide `drift_s`:** error de predicción de UN paso (sorpresa de settlement), NO decay (el baseline random-walk persigue la tasa hacia abajo y absorbería un decay monótono). Útil para detectar fallos de ingest / anomalías de mark, NO para juzgar el edge.
- El `.jsonl` lleva una línea por símbolo por settlement; emparejamiento 1:1.

---

## §6 · Decay-kill (PRE-REGISTRADO) — CI-vs-umbral, in-sample-anchored declarado

Resuelve N2 (circularidad) por **honestidad declarada**, no por pretender independencia que no hay:

- **Estadístico:** el CI vivo de §3 sobre la ventana de `W` semanas, ventanas **NO solapadas** (mata la autocorrelación).
- **Regla de kill (CONGELADA):** REFUTADO si el **CI-hi vivo < 0.0502** durante `N` ventanas no-solapadas consecutivas. Comparación CI-vs-umbral (no point-vs-CI-lo; resuelve HIGH 5). Disparar = alerta + `decay_state=REFUTED` (no cierra nada; declara veredicto).
- **Banda thin-pero-vivo:** CI vivo solapa [0.0502, 0.0633] → comprimiéndose; registra, no mata.
- **N2 — limitación declarada (no oculta):** el umbral `0.0502` ES el CI-lo del fósil → la prueba es **in-sample-anchored**: detecta "el carry vivo cayó bajo el peor caso creíble del backtest", NO "cayó bajo una referencia externa independiente". Es lo correcto para un monitor de decay (cualquier monitor out-of-sample compara contra un baseline in-sample), pero se documenta que NO es un test frecuentista con tasa de falso-positivo garantizada en el régimen vivo. El control de ruido vivo viene del **CI computado de datos vivos** (§3), no del fósil.
- **Potencia (en el plan, `power.py`):** rol REDUCIDO a heurística de `W`-mínimo. NO es el gate de falso-positivo (ese lo da el CI vivo). Se computa sobre el SE ESPERADO EN VIVO (n por símbolo y cadencia real del shadow), no sobre el fósil completo. `N` se fija para una tasa de falso-REFUTED objetivo dado el ancho del CI vivo esperado.
- **CONGELADO antes del primer run live:** `W`, `N`, umbral `0.0502`, ventanas-no-solapadas, CI-vs-umbral. Post-hoc = experimento nuevo.
- **Asimetría (Popper):** solo REFUTA; ausencia de decay en `W·N` semanas no prueba robustez perpetua.

---

## §7 · Output — log append-only

`data/shadow/funding_carry_signals.jsonl`, **una línea por símbolo por SETTLEMENT** (§5), NUNCA sobrescribe:

```json
{"settlement_ts_utc": "...", "symbol": "BTCUSDT",
 "funding_rate": ..., "mark_settled": ..., "units": ...,
 "expected_net": ..., "realized_net": ..., "drift": ...,
 "window_complete": true, "calibration_identity_hash": "...", "shadow_version": "v0.1"}
```

Más `data/shadow/funding_carry_state.json` (derivado regenerable): CI vivo de la última ventana §3, `net_return_annual` pooled, contador de ventanas-no-solapadas bajo umbral, `decay_state ∈ {ALIVE, THIN, REFUTED}`, `W`/`N`/`H_ref` congelados. El `.jsonl` es el ledger inmutable.

---

## §8 · Estructura de archivos

Extiende `tools/funding_carry/`:
- `live_ingest.py` (NUEVO) — `fetch_recent_funding(symbol, limit)`, `fetch_mark_klines(symbol, interval="1h", limit)` (puebla `perp_klines`), `fetch_spot(symbol)` contra FAPI; append idempotente + detección de gap. Fail-soft por símbolo.
- `shadow.py` (NUEVO) — `decay_statistic(symbols, window_weeks)` que **REUSA `simulate.carry_for_symbol`** sobre la ventana móvil + `evaluate.gate_a`-style bootstrap CI (idéntico al fósil; §3); `reconcile_settlement(...)` (§5 secundario); `decay_state(ci, weeks_below, ...)`; `run_once()` → escribe `.jsonl` + `.json`. La única lógica nueva de cálculo está en §5 (reconciliación per-settlement), NO en el estadístico §3.
- `power.py` (NUEVO, corre en el plan) — heurística de `W`-mínimo sobre el SE esperado EN VIVO (no el fósil completo); output documentado, valores hard-codeados en constants tras revisión.
- `constants.py` — `SHADOW_SYMBOLS` (los 9), `FAPI_MARK_KLINES`, `FAPI_SPOT`, `DECAY_CI_LO=0.0502`, `DECAY_WEEKS_W`, `DECAY_KILL_N`, `FUNDING_FETCH_LIMIT`, `SHADOW_OUTPUT_DIR`, `SHADOW_VERSION="v0.1"`.
- `tests/test_funding_carry.py` — TDD: el estadístico §3 **cuadra numéricamente con `carry_for_symbol` sobre la MISMA sub-ventana del fósil, incluyendo una sub-ventana CON gaps** (detecta el defecto N1 de anualización por-conteo vs por-span); live_ingest idempotente + fail-soft + gap→ventana-incompleta-no-evalúa-kill; reconcile per-settlement 1:1; decay_state 3 estados + contador N (ventanas no-solapadas); CI-hi vs umbral; denominador = NOTIONAL; append-only.

**Herencia del shadow existente (acotada):** solo `fail-soft + engine/version tag + reuso de funciones puras`. NO la maquinaria tenant/DB de `kill_switch_v2_shadow`.

**Scheduling:** entrada watchdog/cron Windows, 1×/día post-settlement. Documentar en setup; NO tocar el scanner.

---

## §9 · No-Negociables respetados

- **NN#1 (PositionClosure):** N/A — sin posiciones. Decay-kill REFUTA una hipótesis, no cierra un trade.
- **NN#3 (holdout #322):** sin `open_holdout`, sin `simulate_strategy` con frames de holdout. Solo público.
- **NN#4 (sizing):** N/A — estimando leverage-free.
- **Provenance ([[cost_model_provenance]]):** hash del cost-model estampado.
- **Fail-soft:** read+append; excepción → log, no afecta producción.

---

## §10 · Qué NO es / techo

- **No** es capital real ni paper-execution (#4 / v0.2). NO mide fricción de ejecución (§2).
- **No** abre posiciones, no emite órdenes, no toca el scanner.
- **No** prueba robustez perpetua (§6 asimetría); la prueba es in-sample-anchored (§6/N2).
- **No** modela liquidación.
- Un v0.1 sano dice "el carry persiste/se comprime/se arbitró", nada más. Permiso barato para v0.2 (paper-execution) y eventualmente #4.

---

## §11 · Forks abiertos — para el plan

**§11-A:** `W`, `N`, `FUNDING_FETCH_LIMIT` salen de `power.py` (SE esperado en vivo). `W` también acota `H_ref` (el costo se amortiza sobre `window_hours = W`). Partida (NO congelada): `W=2 semanas`, `N=4`. Confirmar tras la potencia.
**§11-B:** Spot source — ticker FAPI independiente (diseño). Confirmar.
**§11-C:** ¿REFUTED notifica (push) o solo estado + `mex log`? Diseño: `mex log` + estado, sin push.

---

## §12 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-06-03 | DRAFT v0.1 (rev 1) tras junta (5 lentes + Axiom-0). | Claude Opus 4.8 + sssamuelll |
| 2026-06-03 | REV 2 post-auditoría Adrian (cerró 3 BLOCKERs originales + HIGHs). | Claude Opus 4.8 + sssamuelll |
| 2026-06-03 | REV 3 post re-auditoría Adrian (cerró 6/8; 2 BLOCKERs nuevos). N1: el estadístico de decay REUSA `carry_for_symbol` sobre ventana móvil de W sem (idéntico al fósil: entry-mark, $/notional, span-anualizado) — boté la fórmula adimensional inventada; el "flujo incremental" se confina a §5 secundario. N2: circularidad declarada honestamente (umbral 0.0502 = CI-lo del fósil → prueba in-sample-anchored); control de ruido vivo vía CI de datos vivos; potencia reducida a heurística de W sobre SE-esperado-en-vivo. N3: §5 declara medir sorpresa-de-un-paso, NO decay. N4: equivalencia equal/$-weight condicionada a cobertura completa. N5: mark klines a 1h (grano del fósil). | Claude Opus 4.8 + sssamuelll |
