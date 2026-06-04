# Funding-Carry Execution-Realism v0.2 — Design (REV 2)

**Fecha:** 2026-06-03 (REV 2: 2026-06-04)
**Estado:** DISEÑO (pre-registro) — REV 2 tras roster completo (Adrian 3 BLOCKERs + Halberg CONDITIONAL-RUN + Richter reframe estructural + Cassian triage) y colapso de Axiom-0. Pendiente: re-audit corto de Adrian → plan.
**Lineaje:** funding carry = PASS (PR #557) → shadow-deploy v0.1 (PR #560, monitor de decay de la tasa, eje realizabilidad sub-eje *persistencia*) → **v0.2 = sub-eje *fricción de ejecución*** del mismo eje. Lo que v0.1 declaró fuera de scope (leg-lag, slippage), v0.2 ataca la parte medible **paper-only**.
**Depende de:** v0.1 merged + desplegado (reusa `constants.py` congeladas, `data/shadow/funding_carry_state.json` como fuente del rate vivo, `power.cost_floor` como plantilla de tipo).
**No dispara:** holdout #322. Sin `PositionClosure`, sin posiciones, sin órdenes, **sin capital** (capital real = #4). Solo datos públicos Binance. Proceso SEPARADO de `btc_scanner.py` y del decay-monitor de v0.1.

---

## §0 · El invariante que gobierna esta REV (Axiom-0, 2026-06-04)

> **Una comparación es un experimento solo cuando ambos operandos están co-localizados en CADA eje que el veredicto lee** — época, ontología de costo, momento de la distribución, hora del instrumento, denominador.

REV 1 violaba esto en tres ejes: (a) **época** — costeaba trades 2024-2026 con el libro de junio-2026 ("quimera temporal", Richter; la aproximación no declarada era `depth_2024 ≈ depth_2026`); (b) **momento** — U2 comparaba un 2º momento (σ) contra un 1er momento (costo esperado); renombrar `drag→risk_bound` declaró el tipo pero igual ejecutó la comparación cruzada; (c) **hora del instrumento** — depth a las 10:15 local ≠ depth al settlement, que es cuando un carry transacta. Además v0.1 ya lee el rate vivo en **3.6% THIN** — medir fricción contra el 6.33% del fósil validaría un mundo que ya no existe ("veredicto verde sobre un cadáver").

REV 2 mueve el veredicto al único conjunto de ejes donde todos los operandos ya co-localizan: **costo real de HOY vs rate vivo de HOY**, con el mismo tipo y denominador que el estadístico REV 5 de v0.1.

---

## §1 · Pregunta (congelada, REV 2)

> **(Unidad 1 — VEREDICTO)** ¿El rate de funding VIVO (el que v0.1 mide a diario) cubre el **piso de costo REAL** — el costo de transacción medido caminando el orderbook real, amortizado sobre `H_REF_YEARS`, construido idéntico al `T_FLOOR` de v0.1 pero contra el libro en vez del modelo v3?
> **(Unidad 1 — DIAGNÓSTICO)** ¿El modelo v3, evaluado HOY contra el libro de HOY, sigue siendo upper bound del costo real (`cost_real ≤ cost_v3_hoy`)? Per-símbolo, all-in totals.
> **(Unidad 2 — DESCRIPTIVO, SIN VEREDICTO)** ¿Cuánta dispersión añade al net una pata desnuda durante una ventana de ejecución T? Tabla σ_T per-símbolo. **No se compara contra nada** — un risk bound (2º momento) solo es comparable contra un risk budget (2º momento), y no existe uno definido. El veredicto de U2 espera a que exista; medirlo hoy es barato y honesto.

El replay del fósil con `cost_real` (el veredicto primario de REV 1) **muere como veredicto**: sus operandos no co-localizan en época y sus inputs (serie de funding, precios entry/exit) no están persistidos en `per_symbol.json` mientras `funding.db` es appendeada en vivo por v0.1 (Adrian BLOCKER 2 — inverificable el claim "solo cambió el costo"). El gap-al-6.33% muere con él.

---

## §2 · Scope honesto

- **✅ v0.2 MIDE (paper-only, one-shot):** (1) piso de costo real del libro live vs el rate vivo (mismo instante, mismo tipo); (2) validación del modelo v3 contra el libro de hoy; (3) σ del basis sobre ventanas de ejecución (descriptivo).
- **❌ v0.2 NO MIDE:** latencia de fill real, margin-call timing, downtime — requieren órdenes reales (= #4). Tampoco emite veredicto de leg-lag (sin risk budget no hay comparación legítima).
- **❌ v0.2 NO ES un monitor:** el veredicto es un **snapshot** (Cassian: la cadencia diaria añadía un eje que el veredicto no lee). Si el snapshot sale THIN-but-positive, un monitor de degradación de depth se gana su lugar como follow-up — no antes.
- **Universo:** los 9 líquidos congelados (`SHADOW_SYMBOLS`). `NOTIONAL`=10000 por pata.

---

## §3 · Unidad 1 — Piso de costo real (ONE-SHOT, `execution_cost.py`)

**Momento de ejecución (congelado y ENFORCED):** la corrida se dispara **dentro de los 15 minutos POSTERIORES a un settlement de funding (00:00 / 08:00 / 16:00 UTC)** — la hora a la que un carry real cruza el libro (Halberg CI-2; co-localización hora-del-instrumento). **El proceso hard-refuse al arrancar si `now_utc − settlement_anterior > SETTLEMENT_WINDOW_MIN`** (Adrian REV2-F7: la co-localización se verifica, no se confía al operador). El timestamp del settlement objetivo se registra en el artefacto.

**Método, por símbolo:**
1. Bajar depth live: perp `GET fapi/v1/depth?symbol=…&limit=1000`, spot `GET api/v3/depth?symbol=…&limit=5000`. El snapshot crudo de cada libro se persiste en el artefacto (provenance de época de depth — Richter).
2. `walk_book(levels, notional_usd, side)` — definición exacta (Adrian REV2-F6): `mid = (best_bid + best_ask) / 2` **del mismo libro**; `qty_target = notional_usd / mid`; se caminan niveles (asks para comprar, bids para vender) acumulando cantidad hasta `qty_target`; `slippage_cost = |VWAP_fill − mid| × qty_target` (≥ 0 por construcción, ambos lados). Fill a USD-target fijo convertido a cantidad en el mid — no a cantidad fija. El test TDD lleva un ejemplo numérico trabajado que pina la aritmética.
3. **Costo real round-trip all-in (4 patas)** = Σ sobre las 4 patas (spot-buy + perp-sell apertura; spot-sell + perp-buy cierre) de `slippage_cost + taker_fee × NOTIONAL`, sobre el MISMO snapshot. Aproximación declarada §6.1. **Convención de patas/denominador (Adrian REV2-F5, verificada contra código):** idéntica al fósil — `cost_v3` del fósil = `recost_four_legs` = 4 fills / 2 roundtrips con notional per-leg, y `power.cost_floor` divide ese costo 4-patas entre `NOTIONAL`=10000 (per-leg, NO 2×). v0.2 usa exactamente la misma base: 4 patas ÷ 10000.
4. **`T_FLOOR_REAL`** = `mediana_per-símbolo(cost_real_roundtrip / NOTIONAL) / H_REF_YEARS + MARGIN` — **construcción idéntica a `power.cost_floor`** (mediana, H_REF=2.0, MARGIN=0.0), solo cambia la fuente del costo: libro real en vez de modelo v3. El keystone test (`cost_real := cost_v3` ⇒ `T_FLOOR_REAL == cost_floor(...)`) tiene valor esperado numérico fijo.

**Precondiciones de lectura del state de v0.1 (congeladas — Adrian REV2-F4/F8):**
- Fuente: el `data/shadow/funding_carry_state.json` más reciente. **ABORT** si `now_utc − run_ts_utc > 26 horas` (cadencia diaria + margen).
- **ABORT** si `decay_state ∉ {ALIVE, THIN}`: `ERROR`/`INCOMPLETE` → el operando izquierdo no existe ese día; `REFUTED` → v0.1 ya mató el edge y v0.2 es moot (se registra la razón, no se computa veredicto).
- **ABORT limpio (hard-refuse, no KeyError)** si falta cualquiera de `R_ci_lo` / `R_ci_hi` / `calibration_identity_hash` — el branch ERROR de v0.1 no escribe esas claves; el guard debe sobrevivir exactamente cuando v0.1 está enfermo.

**Veredicto primario (mismo tipo y semántica que REV 5 de v0.1):**
- Operando izquierdo = `R_pooled`/`R_ci_lo`/`R_ci_hi` del state validado arriba — NO recomputar; v0.1 es la fuente canónica del rate. (Nota: ese CI resume una ventana trailing de 1 semana, no un rate instantáneo del día — §6.6.)
- **PASS** = `R_ci_lo ≥ T_FLOOR_REAL` (el rate vivo cubre el costo real con confianza).
- **THIN** = `R_ci_lo < T_FLOOR_REAL ≤ R_ci_hi` (indistinguible del piso).
- **FAIL** = `R_ci_hi < T_FLOOR_REAL` (el rate vivo no cubre ni el costo real — misma semántica que el kill REV 5, pero contra el piso real, snapshot no contado en el kill-counter de v0.1).
- Reportar además `T_FLOOR_REAL / T_FLOOR` (cuánto más caro es el libro real que el modelo: >1 = v3 optimista en el piso, <1 = v3 conservador).

**Diagnóstico per-símbolo (same-epoch, costo-vs-costo):**
- `cost_v3_hoy` = **`recost_four_legs` — la MISMA función que produjo el `cost_v3` del fósil** (Adrian REV2-F9: una invocación, no una reconstrucción de `compute_trade_costs` a mano) — con `units = NOTIONAL / spot_mid`, precios mid del snapshot, `liq` computado en vivo por el mismo método del fósil (`spot_liquidity`, trailing), y `holding_hours = H_REF_YEARS × 8760 = 17520` congelado (con `enable_funding=False` el holding solo afecta términos no-funding; valor logueado). Misma base 4-patas que `cost_real` por construcción.
- `liq` se lee vía helper propio `_liq_ro` en `execution_cost.py`: misma query que `spot_liquidity`, conexión `mode=ro` + `busy_timeout=5000ms` explícito — **sin modificar las funciones de v0.1** (Adrian REV2-F10 resuelve la tensión: v0.2 no toca `simulate.spot_liquidity`).
- Comparación de **TOTALES all-in únicamente** — no se intenta mapear término-a-término (el walk-book embebe el half-spread en el slippage; v3 lo separa con stress_mult y cap; los componentes no co-localizan en cost-space, los totales sí — Adrian BLOCKER 1 resuelto por agregación declarada).
- Flag de violación per-símbolo si `cost_real > cost_v3_hoy` + ratio de tightness. El `cost_v3` del fósil (`per_symbol.json`) se reporta solo como contexto, marcado cross-epoch.

**Reglas de pooling/aborto (congeladas — Adrian F4, Halberg CI-3):**
- `FETCH_FAILED` (red/rate-limit, tras reintentos) ≠ `INSUFFICIENT_DEPTH` (el libro real no llena NOTIONAL dentro de los niveles) — **dos sentinels distintos, significados opuestos**: el primero es clima de red, el segundo es un HALLAZGO contra el edge.
- Cualquier `FETCH_FAILED` residual → **ABORT** de la corrida completa (se reintenta al siguiente settlement). El veredicto jamás se computa sobre una muestra determinada por el clima de red.
- `INSUFFICIENT_DEPTH` en k símbolos → se excluyen de la mediana, se FLAGGEAN en la línea del veredicto; **regla única: `k > MAX_INSUFFICIENT_SYMBOLS` (=2) → veredicto INVALID** (Adrian REV2-F11: la prosa y el constant expresan la misma desigualdad; k=3 invalida, k=2 no).

---

## §4 · Unidad 2 — σ del basis (ONE-SHOT, DESCRIPTIVO, `leg_lag.py`)

**Método:**
- Bajar klines 1m: spot `GET api/v3/klines` (close) + perp `GET fapi/v1/markPriceKlines` (close), ventana `LEG_LAG_DAYS`=30. **Paginación obligatoria** (`startTime`/`endTime`, cap 1500/request, ~29 páginas por símbolo/mercado); **tolerancia de gaps numérica (Adrian REV2-F13): hard-fail si `count < 0.98 × LEG_LAG_DAYS × 1440`** (≤2% de barras ausentes por mantenimiento/thin-perp es benigno; más es serie corta) — jamás computar σ sobre 25h etiquetadas como 30d (Halberg BP-1).
- `basis_t = (perp_close_t − spot_close_t) / spot_close_t`; `σ_1m` = std de Δbasis por minuto.
- `σ_T = σ_1m × √(T/60)` para T ∈ {1, 10, 60, 300}s.

**Salida (tabla, sin veredicto):** per-símbolo, dos columnas etiquetadas sin ambigüedad (Adrian REV2-F12): `σ_T × NOTIONAL` (**per-evento**, una pata desnuda durante T) y `√2 × σ_T × NOTIONAL` (**hold-continuo**, agregado de los 2 eventos entrada+salida, independencia asumida). Para contexto, se imprime junto al costo round-trip real de U1 y al carry vivo — **como columnas adyacentes, NO como comparación con umbral**. Sin NEGLIGIBLE, sin FLAG, sin T_REF privilegiado. La interpretación queda para cuando exista un risk budget de 2º momento (candidato natural: ancho del CI del net — diseño futuro, fuera de scope).

---

## §5 · Estructura de archivos

Extiende `tools/funding_carry/`:
- `live_ingest.py` — añadir `fetch_perp_depth(symbol, limit)`, `fetch_spot_depth(symbol, limit)`, `fetch_klines_1m_paginated(symbol, market, days)`. **Upgrade de `_get_json`:** leer `X-MBX-USED-WEIGHT-1M`, honrar `Retry-After` en 429/418, reintento acotado (3x backoff) ante reset transitorio; distinguir y propagar `FETCH_FAILED` (Halberg BP-2/BP-3). No toca las funciones existentes de v0.1.
- `execution_cost.py` (NUEVO, U1) — `walk_book`, `roundtrip_real_cost(symbol, perp_book, spot_book)`, `t_floor_real(per_symbol_costs)`, `verdict(t_floor_real, state)`, `run()` → artefacto one-shot. Escritura atómica (temp + rename).
- `leg_lag.py` (NUEVO, U2) — `basis_sigma_1m(symbol, days)`, `scale_to_window(sigma_1m, t_seconds)`, `run()` → tabla en el mismo artefacto.
- `constants.py` — añadir `FAPI_PERP_DEPTH`, `SPOT_DEPTH`, `SPOT_KLINES_1M`, `FAPI_MARK_KLINES_1M`, `DEPTH_LIMIT_PERP=1000`, `DEPTH_LIMIT_SPOT=5000`, `LEG_LAG_DAYS=30`, `LEG_LAG_T_SWEEP=(1,10,60,300)`, **`PERP_TAKER_FEE=0.0005`, `SPOT_TAKER_FEE=0.001`** (Binance público VIP0, congelados numéricamente AHORA — no "leer de v3 si está": el fee de v3 va multiplicado por stress_mult y no co-localiza; Adrian F6), `EXEC_REALISM_OUTPUT_DIR`, `SETTLEMENT_WINDOW_MIN=15`, `MAX_INSUFFICIENT_SYMBOLS=2`. Nota de saneamiento: `FAPI_SPOT` actual apunta al ticker spot (mislabel, Halberg RC-1) — los nuevos constants usan prefijos veraces `SPOT_*` / `FAPI_*`; renombrar el viejo queda para un PR de limpieza, no este.
- **Artefacto único:** `data/retune/2026-06-04-funding-carry-exec-realism/{findings.md, per_symbol.json, depth_snapshots/}` — incluye: veredicto + T_FLOOR_REAL + ratio vs T_FLOOR, diagnóstico per-símbolo, tabla σ, snapshots crudos de depth (época estampada), `calibration_identity_hash`, timestamp del settlement objetivo, `R_pooled`/CI leídos de v0.1 y el timestamp de ese state.
- `tests/test_funding_carry.py` — TDD: `walk_book` (libro conocido → VWAP/slippage exactos por lado; signo ≥0; profundidad insuficiente → sentinel); `roundtrip_real_cost` (4 patas); `t_floor_real` cuadra con `power.cost_floor` cuando cost_real=cost_v3 (test keystone de identidad de tipo); semántica PASS/THIN/FAIL/INVALID/ABORT; paginación (mock multi-página, hard-fail en serie corta); `scale_to_window` (√T exacto); sentinels FETCH_FAILED vs INSUFFICIENT_DEPTH.

**Datos que U1/U2 LEEN:** `data/shadow/funding_carry_state.json` (rate vivo), `per_symbol.json` del fósil (solo contexto), red pública. **PROHIBIDO leer `funding.db` / `ohlcv.db`** (race SQLITE_BUSY con v0.1 + el clima de red no debe tocar la fuente del decay-monitor; Halberg CF-1) — salvo `spot_liquidity` para el `liq` del diagnóstico, que lee `ohlcv.db` en modo read-only con `busy_timeout` explícito.

**Scheduling:** NINGUNO. One-shot manual, settlement-adjacent. Sin watchdog, sin Task Scheduler, sin `.jsonl` de serie temporal.

---

## §6 · Aproximaciones declaradas (honestidad pre-registrada)

1. **Depth de cierre ≈ depth de apertura (mismo snapshot):** el round-trip usa un solo libro para las 4 patas. Sesgo: en stress la profundidad de salida es peor → `cost_real` (y por tanto `T_FLOOR_REAL`) está **subestimado** → el veredicto primario PASS carga un sesgo optimista declarado (Adrian F9: dirección explícita). El FAIL, en cambio, es conservadoramente creíble.
2. **√T sub-minuto (U2):** σ a T<60s extrapolada de σ_1m vía random-walk. Sub-segundo puede tener microestructura distinta. Es descripción, no medición.
3. **T es una ventana ASUMIDA (U2):** sin órdenes no hay latencia de fill real; el sweep es descriptivo.
4. **Mark-basis ≠ basis ejecutable (U2):** `markPriceKlines` no es el precio tradeable del perp; σ del mark-basis puede subestimar la σ ejecutable (Adrian F12).
5. **Snapshot único ≠ distribución de depth:** una corrida settlement-adjacent muestrea UN settlement. El veredicto es válido para ese instante; la variabilidad inter-settlement no está medida (es el follow-up monitor si THIN).
6. **El rate vivo viene de una ventana 1-semana (v0.1):** `R_pooled` y su CI heredan el ruido de ventana corta de v0.1 — el operando izquierdo del veredicto es ruidoso por diseño; por eso la semántica THIN existe.

---

## §7 · No-Negociables

- **NN#3 holdout:** sin `open_holdout`, sin frames de holdout, sin `simulate_strategy`.
- **NN#1/#4:** sin posiciones, sin órdenes, sin capital, sin `PositionClosure`.
- **Co-localización (nuevo, Axiom-0):** ningún veredicto de este experimento compara operandos de épocas, ontologías o momentos distintos. Toda comparación cross-epoch o cross-tipo se reporta como CONTEXTO marcado, jamás como veredicto.
- **Provenance:** `calibration_identity_hash` + época de depth + timestamp del state de v0.1 estampados en el artefacto. Si el hash del calibration vivo ≠ hash del state de v0.1 del día → **hard-refuse** (Adrian F10, patrón del holdout gate).
- **Fail-loud, no fail-soft, en el veredicto:** `FETCH_FAILED` → ABORT (no encoge el pool en silencio). `INSUFFICIENT_DEPTH` → FLAG/INVALID según k. La muestra del veredicto jamás es función del clima de red (Halberg).
- **PROHIBIDO escribir en `data/shadow/`** — ese namespace es de v0.1. v0.2 escribe solo su artefacto en `data/retune/`.

---

## §8 · Qué NO es / techo

- No es capital real ni ejecución real (#4). NO mide latencia/margin-timing/downtime.
- No abre posiciones, no toca el scanner ni el decay-monitor.
- No emite veredicto de leg-lag (no hay budget contra qué).
- No actualiza el kill-counter de v0.1 (el FAIL de v0.2 es un snapshot informativo; el kill sigue siendo de v0.1, 4 bloques).
- U1 sana dice: "el rate vivo cubre / no-cubre / es-indistinguible-del piso de costo real HOY, y v3 es/no-es upper bound del libro HOY". U2 sana entrega una tabla. Nada más.
- **Deployability sigue sin tener estimador conjunto** (Richter): v0.1 mide persistencia, v0.2 mide fricción same-epoch — la CONJUNCIÓN (rate − costo − riesgo, todo same-epoch, un solo artefacto go/no-go para #4) es deuda declarada de nivel meta, no de este sub-proyecto. Que conste para que tres luces verdes aisladas no se lean como una.

---

## §9 · Forks resueltos (nada queda abierto al plan)

- **§9-A (RESUELTO):** `TAKER_FEE` congelado numéricamente en constants (5bps perp / 10bps spot, VIP0 público). No se lee de v3.
- **§9-B (RESUELTO):** semántica del veredicto = PASS/THIN/FAIL/INVALID/ABORT como §3, isomorfa a REV 5 de v0.1. No hay bar alternativo.
- **§9-C (MUERTO):** no hay recompute del fósil como veredicto → no hay denominador `years` del fósil en ningún lado. `H_REF_YEARS=2.0` en ambos lados del veredicto por construcción.
- **T_REF (MUERTO):** sin veredicto U2 no hay T privilegiado; el sweep completo es la entrega.

---

## §10 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-06-03 | Diseño v0.2 (fricción de ejecución, sub-eje realizabilidad) tras v0.1 (PR #560). Brainstorming: 2 unidades — cost-fidelity daily (walk-the-book vs v3) + leg-lag one-shot. Paper-only, cero capital, cero holdout. | Claude Opus 4.8 + sssamuelll |
| 2026-06-03 | Pasada de Voronov (pre-commit). U1: denominador `years` del fósil. U2: `/H_REF` + rename `risk_bound` + §6.4. Reframe padre-hijo. | Claude Opus 4.8 + sssamuelll |
| 2026-06-04 | **REV 2.1 — re-audit de Adrian sobre REV 2.** Los 3 BLOCKERs de REV 1 verificados como genuinamente cerrados. 2 BLOCKERs nuevos resueltos: (F4/F8) precondiciones de lectura del state de v0.1 — staleness ≤26h, `decay_state ∈ {ALIVE, THIN}` requerido, ABORT limpio si faltan claves (el branch ERROR de v0.1 no las escribe); (F5) denominador del T_FLOOR_REAL pinned contra código — 4 patas ÷ NOTIONAL per-leg=10000, idéntico a `recost_four_legs`/`cost_floor`, keystone test con valor numérico fijo. HIGHs: walk_book definido exacto (mid, qty_target, fill USD-fijo); settlement-window ENFORCED (hard-refuse); `cost_v3_hoy` = `recost_four_legs` (misma función del fósil, holding congelado 17520h); `_liq_ro` con busy_timeout sin tocar v0.1. Mediums: regla INVALID como desigualdad única (`k > MAX_INSUFFICIENT_SYMBOLS`), tolerancia de gaps numérica (2%), columnas σ per-evento vs √2 hold-continuo. | Claude Opus 4.8 + Adrian |
| 2026-06-04 | **REV 2 — roster completo + Axiom-0.** Adrian (3 BLOCKERs: ontologías de costo, inputs del fósil no persistidos, target U2 indefinido), Halberg (CONDITIONAL-RUN: paginación, rate-limits, sesgo 10:15-vs-settlement, sentinels), Richter (quimera temporal; U1 validaba el mundo del fósil mientras v0.1 lee 3.6% THIN; U2 casi-tautología con error de categoría residual), Cassian (one-shot, cortar cadencia no rigor). Axiom-0: invariante de co-localización; síntesis validada. **Cambios:** veredicto primario = `T_FLOOR_REAL` vs rate vivo de v0.1 (same-epoch, same-type que REV 5); replay del fósil muere como veredicto; diagnóstico v3 = same-epoch costo-vs-costo en totales; U2 degradada a descriptivo sin veredicto (decisión de Samuel); one-shot settlement-adjacent; reglas ABORT/INVALID congeladas; fees congelados; paginación hard-fail; prohibido `funding.db` y escribir en `data/shadow/`; forks §9 todos resueltos. | Claude Opus 4.8 + sssamuelll + roster |
