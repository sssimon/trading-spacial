# Funding-Carry Shadow-Deploy v0.1 — Design

**Fecha:** 2026-06-03
**Estado:** DISEÑO (pre-registro) — REV 5, cierra los 2 BLOCKERs de la auditoría de Adrian sobre REV 4 (puente net↔gross mal-formado; parámetros sin congelar). Listo para re-implementar.

> **Por qué REV 5 (Adrian sobre REV 4):** el fix de tipo de REV 4 (tasa intensiva `mean(rate)×1095`) es correcto y verificado. Pero (BLOCKER 1) el puente "el ancla net 0.0502 sobrevive traducida a gross" estaba mal-formado: `funding_pnl/notional = Σ rate × (perp_entry/spot_entry)` (no `Σ rate`), y el `net` incluye `basis_pnl`. (BLOCKER 2) `H_REF_YEARS`/`MARGIN` quedaban en "confirmar" = grado de libertad post-hoc. **REV 5 elimina el puente:** compara TASA-vs-TASA, todo computado idéntico desde el fósil de funding, sin net/basis/mark. El kill es tasa-vs-piso-de-costo; la banda THIN es tasa-vs-banda-histórica-del-fósil. Todo congelado en `constants.py` antes del primer run. El factor 1095 se cancela (tasa-vs-tasa).
**Lineaje:** funding carry = PASS (PR #557) → #1 kill = PASS-sin-valor (#558) → #2 sizing = ❌ muerto-por-tipo → **shadow-deploy v0.1**.

> **Por qué REV 4 (lección del smoke run):** la rev 3 monitoreaba `net_return_annual` sobre una ventana móvil, reusando `carry_for_symbol`. El smoke run a W=1 dio **−173%/año** (CI [−305%,−44%]). Diagnóstico unánime de la junta:
> - **Voronov (tipo):** `net_return_annual` no es UNA cantidad. Es un **flujo** (funding, extensivo en el tiempo) MÁS un **costo de transacción** (evento puntual fijo), sumados y divididos por el span. Span largo → `costo/span→0` (lo que vio el backtest). Span corto → `costo×52` domina. "Mismas unidades, referentes distintos." Y fusiona dos preguntas: *¿el edge persiste?* (intensivo) vs *¿es desplegable a costo?* (horizonte de tenencia fijo). El spec ató el horizonte de tenencia a la ventana de observación — ejes ortogonales.
> - **Halberg (medición):** `net_return_annual` per-ventana no tiene SNR a NINGUNA W práctica (el costo 1/W contamina a cualquier W; el ruido del rate necesita ~40-125 sem). La cantidad medible con SNR útil en **2-6 semanas** es la **tasa de funding bruta** (intensiva), directo de `funding.db` — sin marks, sin spot, sin basis, sin `recost_four_legs`.
>
> REV 4 separa los dos tipos: monitorea la **tasa bruta** (lo que se arbitra) contra un **piso de viabilidad fijo** que internaliza el costo a un `H_ref` de tenencia declarado y congelado.

**No dispara:** holdout #322. Sin `PositionClosure`, posiciones, órdenes, capital. Proceso SEPARADO de `btc_scanner.py`. Solo datos públicos (Binance FAPI).

---

## §1 · Pregunta (congelada)

El carry netea **+6.33%/año net-of-v3** en backtest (CI95[5.02, 7.45]); ese net = funding bruto + basis − costo, anualizado sobre 2.4 años (régimen donde el costo es despreciable). La cantidad que **se arbitra** es la **tasa de funding bruta** que cobra la pata corta.

**Universo congelado (9, enumerados):** `BTCUSDT, ETHUSDT, ADAUSDT, AVAXUSDT, DOGEUSDT, UNIUSDT, XLMUSDT, RUNEUSDT, PENDLEUSDT`. (Dropeados por cobertura: LINK, SOL — excluidos.)

> ¿La **tasa de funding bruta pooled**, medida en vivo, **se mantiene por encima del piso de costo** (la tasa bajo la cual el carry ya no cubre su costo de despliegue a `H_ref` fijo) — o se comprimió bajo él (edge arbitrado)? Y como señal temprana: ¿sigue dentro de su **banda histórica del fósil**?

**Hipótesis nula falsable (decay-kill §6):** "el edge persiste". El shadow genera el dato out-of-sample.

---

## §2 · Scope honesto

- **✅ v0.1 MIDE — persistencia de la TASA bruta de funding:** intensiva, invariante de ventana, directo de `funding.db`. Determinista, sin capital. Detección en semanas (Halberg).
- **❌ v0.1 NO MIDE — fricción de ejecución:** leg-lag, slippage, margin-call timing, downtime (v0.2 paper-exec / #4 capital).
- **El costo NO es lo que decae** — es un drag de despliegue fijo, intensivo en número de rebalanceos, no en el tiempo. Entra en el PISO (dónde pones la línea), nunca en la cantidad monitoreada.

---

## §3 · Estadístico (CONGELADO) — tasa de funding media pooled, intensiva, PURA

- **Por símbolo, sobre la ventana de `W` semanas:** `R(symbol) = mean(fundingRate_i over the window)` — la media de las tasas per-settlement. **Pura:** sin costo, sin mark, sin spot, sin basis, sin dividir por span. (Reportada ×`INTERVALS_PER_YEAR`=1095 solo para legibilidad humana; el factor se CANCELA en toda comparación tasa-vs-tasa, así que el redondeo 1095 vs 1095.4 es irrelevante.)
- **Pooled = media equal-weight** de los 9 `R(symbol)` (esquema de `gate_a`). Símbolos con ventana incompleta se dropean loud.
- **CI vivo:** bootstrap (`BOOTSTRAP_N`/`SEED` del backtest) sobre los 9 valores per-símbolo vía `evaluate.gate_a` — captura el SE del régimen vivo (controla falso-REFUTED in-régimen). El campo `pass_a` de `gate_a` (net>0) se IGNORA en el path de decay; aquí solo se usan `ci_lo`/`ci_hi`/`mean`.
- **Por qué pura+intensiva resuelve el defecto:** `mean(rate)` es invariante de ventana en expectativa (estacionaria intra-régimen). No tiene el término `1/W` del costo que mató el smoke run. Es exactamente la cantidad que se arbitra. Misma cantidad a W=1 semana y a 2.4 años (Voronov: tipo estable bajo cambio de ventana).

---

## §3b · Dos anclas FIJAS computadas del fósil (sin puente al net) — congeladas en constants

REV 5 NO traduce el ancla net 0.0502 (puente mal-formado, BLOCKER 1). Define dos anclas en espacio-TASA PURA, ambas computadas idéntico al estadístico vivo (`mean(rate)`), deterministas desde el fósil:

1. **Banda histórica del fósil (para THIN):** `R_FOSSIL_LO`, `R_FOSSIL_HI` = el CI bootstrap de `gate_a` sobre los 9 `R(symbol)` calculados sobre la VENTANA COMPLETA del fósil (2024-01→2026-05, mismo `mean(rate)` per-símbolo). Computado UNA vez en `power.py`, congelado.

2. **Piso de costo (para REFUTED):** `T_floor = cost_annual_at_Href + MARGIN`, donde `cost_annual_at_Href = median_over_9(roundtrip_v3_cost_symbol / NOTIONAL) / H_REF_YEARS`. **Mediana, no media** (el costo de PENDLE es ~40× el de BTC — `per_symbol.json`; la media la distorsiona, HIGH 8). El costo se amortiza sobre `H_REF_YEARS` (horizonte de tenencia, decisión de estrategia) — FIJO, NO la ventana de observación (Voronov). Es la tasa bruta mínima bajo la cual el carry ya no cubre su costo de despliegue → edge sin valor económico.

- **`basis`:** término secundario mean-reverting; NO decae, NO entra en ninguna ancla ni en el estadístico. Declarado despreciable y fuera de scope.
- **CONGELADO en `constants.py` con valores numéricos finales ANTES del primer run** (no "confirmar"): `R_FOSSIL_LO`, `R_FOSSIL_HI`, `H_REF_YEARS`, `MARGIN`, `cost_annual_at_Href` (o sus insumos), `DECAY_WEEKS_W`, `DECAY_KILL_N`. Cambiar cualquiera = experimento nuevo. `power.py` v2 los computa y se commitean firmados.

---

## §4 · Datos live (el ingest diario) — SOLO funding para el path de decay

- **Funding settled reciente:** `GET fapi/v1/fundingRate?symbol=…&limit=L` (`constants.FAPI_FUNDING`). Append idempotente a `funding.db` (PK `(symbol, funding_time_ms)`). `L` congelado (§11-A).
- **Mark/spot:** **NO necesarios para el path de decay** (el estadístico es la tasa bruta, adimensional). El ingest de `markPriceKlines`/spot queda DORMIDO en v0.1 (se reactiva en v0.2 paper-exec para la reconciliación en $). `live_ingest.fetch_recent_funding`/`append_funding` se mantienen; `fetch_mark_klines`/`fetch_spot` quedan en el módulo pero NO se llaman en `run_once` v0.1.
- **Política de gap fail-safe:** hueco > `max_gap` → ventana incompleta → el decay-kill NO se evalúa esa corrida.
- **Frecuencia:** 1×/día post-settlement, proceso separado. Fail-soft por símbolo.

---

## §5 · Reconciliación per-settlement — DIFERIDA a v0.2

REV 5 la retira de v0.1 (Adrian: nunca se llamaba en `run_once`, y su esquema per-settlement chocaba con el output per-corrida). Era un sanity-check secundario, no el decay. v0.1 registra la lectura per-corrida (§7); la reconciliación per-settlement de ingest/mark se reintroduce en v0.2 (paper-exec) cuando los marks vuelvan al path activo.

---

## §6 · Decay-kill (PRE-REGISTRADO) — tasa-vs-tasa, ventanas no-solapadas reales

- **Estado por ventana NO-solapada:** el job corre 1×/día (cadencia de polling) con una ventana trailing de `W` semanas. Pero el **conteo del kill solo avanza al cruzar un boundary de ventana no-solapada** (resuelve HIGH 6: corridas diarias se solapan). Mecánica: las ventanas se anclan a una grilla fija de bloques de `W` semanas (alineada a epoch). El estado guarda `last_counted_block`. En cada corrida, si el bloque actual ≠ `last_counted_block`, se EVALÚA el kill sobre la ventana recién cerrada y se actualiza el contador; si es el mismo bloque, solo se registra la lectura rolling (no toca el contador). Así `N` ventanas = `N` bloques independientes, no `N` días solapados.
- **`R_live` por bloque:** CI vivo de `gate_a` sobre los 9 `R(symbol)` del bloque.
- **Regla de kill (CONGELADA):** **REFUTADO** si el **CI-hi de `R_live` < `T_floor`** (§3b, piso de costo) durante `DECAY_KILL_N` bloques no-solapados consecutivos. Disparar = alerta + `decay_state=REFUTED` (declara veredicto; no cierra nada).
- **THIN (señal temprana):** `R_live` mean < `R_FOSSIL_LO` (comprimida bajo la banda histórica) PERO CI-hi ≥ `T_floor` (aún cubre costo). Registra, no mata.
- **ALIVE:** `R_live` ci_lo ≥ `R_FOSSIL_LO` (la tasa se sostiene en/sobre su banda histórica).
- **W congelado por potencia (`power.py` v2):** sobre la varianza INTRA-ventana de la TASA per-settlement del fósil (NO el sigma cross-símbolo de largo plazo, error de rev 3). `W` mínimo tal que el SE de `R` pooled ≲ (R_FOSSIL_HI − T_floor)/4 (suficiente resolución para distinguir banda-histórica de piso-de-costo).
- **Control de falso-REFUTED:** el CI vivo (de datos vivos) lo controla in-régimen; `power.py` solo evita una `W` absurdamente corta. El piso de costo es un bar conservador (kill solo cuando el edge económico se agotó), THIN da el aviso temprano.
- **Asimetría (Popper):** solo REFUTA; ausencia de decay no prueba robustez perpetua.

---

## §7 · Output — log append-only

`data/shadow/funding_carry_signals.jsonl`, **una línea por corrida diaria**, NUNCA sobrescribe:

```json
{"run_ts_utc": "...", "block_start_ms": ..., "is_new_block": true,
 "R_pooled": ..., "R_ci_lo": ..., "R_ci_hi": ..., "n": 9,
 "per_symbol_rate": {"BTCUSDT": ..., ...}, "dropped": [],
 "R_fossil_lo": ..., "t_floor": ..., "window_complete": true,
 "decay_state": "ALIVE", "blocks_below_floor": 0,
 "calibration_identity_hash": "...", "shadow_version": "v0.1"}
```

(Tasas reportadas ×1095 anualizadas para legibilidad; el `decay_state` se computa en espacio-tasa-pura donde el factor cancela.) Más `data/shadow/funding_carry_state.json` (derivado regenerable): última lectura + `last_counted_block`, `blocks_below_floor`, `decay_state ∈ {ALIVE, THIN, REFUTED, INCOMPLETE, ERROR}`, y los congelados `R_FOSSIL_LO`/`R_FOSSIL_HI`/`T_floor`/`H_REF_YEARS`/`W`/`N`.

---

## §8 · Estructura de archivos

Extiende `tools/funding_carry/`:
- `live_ingest.py` — solo `fetch_recent_funding`/`append_funding` se usan en v0.1; `fetch_mark_klines`/`fetch_spot`/`append_perp_klines` quedan latentes (v0.2). Sin cambios destructivos.
- `power.py` — `min_window_weeks` re-derivado sobre la varianza INTRA-ventana de la tasa per-settlement del fósil (NO el sigma cross-símbolo); `fossil_rate_band(funding_db) -> (R_FOSSIL_LO, R_FOSSIL_HI)` (gate_a sobre `mean(rate)` per-símbolo en la ventana completa); `cost_annual_at_href(per_symbol_json, h_ref_years) -> T_floor` (mediana per-símbolo `cost_v3/NOTIONAL` / Href + MARGIN). Corre UNA vez; sus salidas se commitean a `constants.py`.
- `shadow.py` — **reescribir el path de decay a tasa-pura:** `symbol_rate(symbol, funding_db, start_ms, end_ms)` (= `mean(fundingRate)` sobre la ventana, solo `simulate.load_funding`; ×1095 al reportar); `pooled_rate(...)` (gate_a sobre los 9); `decay_state(*, ci_lo, ci_hi, r_mean, blocks_below, r_fossil_lo, t_floor)` (ALIVE/THIN/REFUTED por §6); `block_start(now_ms, w_weeks)` (grilla no-solapada); `window_complete`; `run_once` (avanza el contador solo en bloque nuevo, §6). **RETIRAR:** `symbol_window_return`, `pooled_decay`, `reconcile_settlement`, `_HEADLINE` y todo uso de `carry_for_symbol`/marks en el path de decay.
- `constants.py` — añadir `INTERVALS_PER_YEAR=1095`, `H_REF_YEARS`, `MARGIN`, `R_FOSSIL_LO`, `R_FOSSIL_HI`, `T_FLOOR` (todos congelados con valor numérico de `power.py`); `DECAY_WEEKS_W`/`DECAY_KILL_N` re-congelados por `power.py` v2. **Retirar/actualizar** el `DECAY_CI_LO`/comentarios de la versión net si quedan colgando.
- `tests/test_funding_carry.py` — **reemplazar** los tests del estadístico net (`test_decay_statistic_matches_carry_for_symbol`, `test_pooled_decay_uses_gate_a`, los stubs net-space de `run_once`, los de `reconcile_settlement`) por: `symbol_rate` = `mean(rate)` (test con serie conocida, INVARIANTE a la longitud de la ventana a tasa constante — la guarda del fix de tipo); pooled vía gate_a; `decay_state` 3 estados vs `r_fossil_lo`/`t_floor` + contador por-bloque + INCOMPLETE + ERROR; `block_start` no-solapado (dos `now_ms` en el mismo bloque → mismo `block_start`; cruzar W semanas → nuevo); gap; append-only; `run_once` avanza contador solo en bloque nuevo.

**Herencia acotada:** fail-soft + tag + funciones puras. NO la maquinaria tenant/DB.
**Scheduling:** 1×/día, proceso separado. Documentar en setup; NO tocar el scanner.

---

## §9 · No-Negociables

- **NN#1:** sin posiciones, decay-kill REFUTA una hipótesis. **NN#3:** sin holdout. **NN#4:** sin sizing (leverage-free, intensivo).
- **Provenance:** hash del cost-model estampado. **Fail-soft:** read+append; excepción → log + estado ERROR visible, no afecta producción.

---

## §10 · Qué NO es / techo

- No capital, no paper-exec (#4/v0.2), no fricción de ejecución.
- No abre posiciones, no toca el scanner. No prueba robustez perpetua (§6).
- Mide la persistencia de la TASA bruta vs un piso; nada más. Permiso barato para v0.2.

---

## §11 · Parámetros congelados (decisiones de estrategia) — CONGELAR en constants antes del primer run

Estos NO quedan "a confirmar" (eso era el defecto N2/BLOCKER 2). Se commitean con valor final en `constants.py` ANTES del primer `run_once`; cambiarlos = experimento nuevo. Samuel puede ajustarlos en una decisión PRE-run, pero una vez commiteados están congelados.

- `H_REF_YEARS` = **2.0** (horizonte de tenencia; el carry v0.1 es hold-continuo, rebalanceo es otro sub-proyecto). Más largo → `T_floor` más bajo → kill solo ante colapso genuino (conservador). CONGELADO.
- `MARGIN` = **0.0** (colchón sobre el piso de costo; el piso ya es un bar conservador). CONGELADO.
- `R_FOSSIL_LO`, `R_FOSSIL_HI`, `T_FLOOR` = computados por `power.py` v2 del fósil, commiteados con valor numérico. CONGELADOS.
- `DECAY_WEEKS_W`/`DECAY_KILL_N` = de `power.py` v2 (varianza intra-ventana; esperado varias semanas, NO 1). `N=4` de partida. CONGELADOS tras el cómputo.

**§11-B:** EWMA (Halberg) vs media de ventana simple. v0.1: **media de ventana simple** (encaja la grilla de bloques no-solapados; KISS). EWMA = refinamiento futuro si el SNR lo pide.

**§11-C:** REFUTED notifica vs solo estado. v0.1: `mex log` + estado, sin push.

---

## §12 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-06-03 | REV 1 (junta #1) → REV 2 (Adrian: 3 BLOCKERs) → REV 3 (Adrian re-audit: N1 reusar carry_for_symbol, N2 in-sample-anchor). | Claude Opus 4.8 + sssamuelll |
| 2026-06-03 | **REV 4** post-smoke-run (W=1 dio −173% = costo-fijo/ventana + ruido) + 2da junta (Halberg+Voronov unánime). Estadístico → tasa de funding bruta intensiva vs piso de viabilidad. | Claude Opus 4.8 + sssamuelll |
| 2026-06-03 | **REV 5** post-auditoría Adrian (REV 4: 2 BLOCKERs). Elimina el puente net↔gross mal-formado (BLOCKER 1: `funding_pnl/notional = Σrate×(perp/spot)`, y net lleva basis). Pasa a **tasa-pura `mean(rate)` vs dos anclas del fósil computadas idéntico**: kill = CI-hi < `T_floor` (piso de costo, mediana, sobre `H_ref` fijo); THIN = bajo `R_FOSSIL_LO` (banda histórica). Todo CONGELADO en constants con valor numérico antes del primer run (BLOCKER 2 / N2). Conteo del kill por **bloque no-solapado** (grilla, no corrida diaria solapada, HIGH 6). 1095 cancela (tasa-vs-tasa). Reconcile per-settlement → diferida a v0.2. | Claude Opus 4.8 + sssamuelll |
