# Brazo A (reformulado) — Política de salida ciega sobre el stream real de entradas

**Fecha:** 2026-06-03
**Estado:** DISEÑO (pre-registro) — pendiente de revisión de Samuel antes de plan de implementación.
**Lineaje:** [[board-how-to-get-edge]] → [[fork-arm-b-fail-arm-a-pending]] → reformulación tras junta del roast 2026-06-03.
**No dispara:** holdout #322 (ver §10). No toca `RISK_PER_TRADE`. No cierra posiciones live (sim offline sobre trades históricos de papá).

---

## §1 · Por qué reformulado (qué mató el roast)

Brazo A original ("codificar la salida discrecional del operador, q2") fue vetado 6/6 por el panel. El defecto no era de detalle sino de premisa:

- **Error de categoría (Voronov, Null Vale).** q2 (+$10/trade, CI[3.81,15.34]) compara MANUAL (n=16) vs SL_HIT (n=4): **trades distintos**. El operador *eligió* cuáles cerrar a mano. "+$10" se explica sin skill de salida — los ganadores se cierran a mano, los perdedores pegan el stop. Es un estadístico de **selección** tratado como estimando de **timing**.
- **Evidencia propia ya lo dijo.** `q3_counterfactual.json` (`q3_pass: false`): las aprobaciones del operador no tienen poder de forward-return (aprobados rinden menos que rechazados). El operador es un **selector**, no un predictor.
- **PASS pre-ordenado (Halberg, Adrian, Null Vale).** Fit in-sample sobre los mismos paths; proxy dispara en ~3 trades dominados por id=28; baseline ATR ficticio (todos los `atr_entry`/`sl_price`/`tp_price` NULL); drift alcista se filtra. Null pre-desarmado → ritual, no test.

**La reformulación** abandona "codificar al operador" (incodificable: la selección *es* el edge) y pregunta lo **separable**:

> Fijado el stream de entradas reales (señal ya probada gross-flat), ¿una **política de salida mecánica ciega** —sin paso de selección, aplicada a *cada* posición— produce más PnL net-of-v3 que lo que realmente pasó?

Esto es la **hipótesis trend-following**: entrada casi-azar, edge (si existe) en la salida asimétrica (cortar perdedores, dejar correr ganadores). Es la última pregunta de edge legítima para este stream.

### Qué arregla / qué hereda

| Blocker del roast | ¿Resuelto? |
|---|---|
| Sesgo de selección / error de categoría | **SÍ** — la regla ciega no selecciona; corre sobre las 27. |
| Baseline ATR ficticio | **SÍ** — baseline = PnL realizado real, recosteado a v3. Sin ATR inventado para el baseline. |
| Fork asimétrico / null pre-desarmado | **SÍ** — un FAIL (la regla ciega no bate lo realizado) es un NO informativo, no ritual. |
| Techo de poder n≈27, un régimen | **NO** — irreducible. Se pre-declara como techo (§9). |

---

## §2 · Población (CONGELADA)

**Universo:** las 43 posiciones cerradas en la DB de papá (`C:\Users\simon\Desktop\Papa\trading_backup_extracted\signals.db`, tabla `positions`, `status='closed'`), ventana 2026-03-30 → 2026-05-07.

**Filtro de reconstruibilidad (pre-declarado):** se requiere cobertura OHLCV 5m completa en `data/ohlcv.db` durante la vida del trade. **16 posiciones se dropean** por cobertura CERO (ni 5m ni 1h) en 7 símbolos: ZEC(5), TRX(4), XAUT(3), TON(2), SOL(1), LINK(1). No es una elección de scope — esos trades no existen en espacio-precio.

**Población efectiva: N = 27** (sobre 9 símbolos con 5m completo: BTC, ETH, RUNE, XLM, PENDLE, UNI, DOGE, AVAX):

| exit_reason | n | sum_pnl_usd (realizado) |
|---|---:|---:|
| MANUAL | 23 | +76.88 |
| SL_HIT | 4 | −20.94 |
| **Total** | **27** | **+55.94** |

Dirección: LONG dominante; ~6 SHORT en el subset. El conteo exacto de los 27 dropeados/incluidos se vuelca al artefacto de salida como provenance.

---

## §3 · Las dos reglas (PRE-REGISTRADAS, sin sweep)

Decisión de Samuel: una primaria que decide PASS/FAIL + una confirmatoria de robustez.

### Regla PRIMARIA — Chandelier textbook (decide el veredicto)
- **Trailing stop = peak_MFE − 3 × ATR** (LONG); `trough_MAE + 3 × ATR` (SHORT). Constante 3 elegida **del libro**, ciega a esta data → cero tuning in-sample.
- **ATR:** 22-period ATR (Wilder) computado sobre barras **1h** terminando en `entry_ts`, reconstruido de `ohlcv.db` (porque `atr_entry` es NULL en las 43). Congelado al abrir; no se recomputa intra-trade (el "peak" sí trailea).
- **Stop inicial:** al abrir, `entry_price − 3×ATR` (LONG). El trailing sólo sube (LONG) / baja (SHORT), nunca afloja.
- **Disparo y fill:** evaluado sobre **closes/wicks 5m**. Convención **pesimista**: dentro de una barra 5m se asume que el extremo adverso (low LONG / high SHORT) toca antes que el favorable; si el wick adverso cruza el stop, sale **al precio del stop**. Mata la ambigüedad intra-barra (Halberg CI-1) por el lado conservador.
- **Cap de hold:** si el stop nunca dispara, sale a **200h** post-entry (apenas sobre el máximo observado 167h) — mantiene la regla ciega (no usa el `exit_ts` real del operador).

### Regla CONFIRMATORIA — 38%-giveback (robustez, NO decide)
- Trailing que suelta **38% del MFE corrido** (ancla: el operador captura 62% → suelta 38%; `d3_excursion.json` mediana capture 62.4%). Mismo motor de path/fill/costos.
- **Rol:** distingue "ninguna salida mecánica funciona" (ambas FAIL) de "sólo la del estilo del operador funciona" (textbook FAIL, giveback PASS). Cassian: **se computa pero NO gatilla el veredicto**; si la primaria FALLA, la confirmatoria no la rescata.

---

## §4 · Baseline (sin ficción)

Por cada una de las 27 posiciones:
1. **`actual_net_v3`** = recosteo del exit REAL (precio/ts realizado de la DB) bajo costos v3. El gross viene del `entry_price`/`exit_price` reales × `qty`; los costos se recomputan con `backtest_costs.compute_trade_costs(model="v3")` para apples-to-apples (NO se usa el `pnl_usd` registrado, que lleva los costos reales de papá).
2. **`blind_net_v3`** (por regla) = gross del exit elegido por la regla ciega sobre el path 5m, menos costos v3.
3. **Diferencia pareada** `Δ_i = blind_net_v3_i − actual_net_v3_i`. Mismo trade, misma entrada, misma talla — sólo cambia la salida. Pareo limpio.

---

## §5 · Estadística

- **Estimando:** media pareada `Δ̄` y suma `ΣΔ` sobre los 27. Bootstrap (10k, seed pre-declarado) sobre los Δ_i pareados.
- **Leave-one-out:** se recomputa `Δ̄` dropeando cada trade; se reporta el rango LOO. Halberg/Cassian: el resultado debe **sobrevivir dropear el trade más influyente** (esperado id=28 XLM y/o id=47 RUNE).
- **Reportes obligatorios:** CI bootstrap full-sample, CI LOO, y resultado **con y sin** el top-influencer. Por dirección (LONG/SHORT) separado como diagnóstico.

---

## §6 · Criterio KILL (ambas ramas informativas)

- **PASS (existe edge de salida extraíble):** la regla PRIMARIA bate lo realizado — `Δ̄ > 0` con CI bootstrap 95% que excluye cero, **Y** el signo/CI sobrevive el leave-one-out del trade más influyente.
- **FAIL (no hay edge extraíble):** CI incluye cero, o el signo se voltea al dropear el top-influencer. → **NO limpio** (no "underpowered ritual"): ni la salida trend-following textbook extrae expectativa de este stream.
- **Confirmatoria:** se reporta su PASS/FAIL aparte, sólo para la lectura "estilo-operador vs cualquier-salida". No mueve el veredicto.

**Ruteo:** PASS → candidato a producto exit-rule mecánico (con el techo de §9 explícito). FAIL → **Lyra Sage** (double-FAIL con Brazo B: ¿debe existir este producto? ¿el rigor-stack es el deliverable?).

---

## §7 · Datos y reconstrucción

- **Precio:** `data/ohlcv.db` tabla `ohlcv`, timeframe `5m`, columnas open/high/low/close/open_time. Reconstrucción del path por `query_ohlcv_range` (patrón `tools/manual_exit_eda.py:116-138`).
- **ATR:** 22-period Wilder sobre 1h del mismo `ohlcv.db`, ventana terminando en `entry_ts`.
- **Costos:** `backtest_costs.compute_trade_costs(model="v3")` en ambos brazos (blind y actual), idéntico, sin doble-conteo.
- **Provenance:** el artefacto de salida estampa N incluido/dropeado por símbolo, seeds, params congelados, y el commit de código. (Coherente con el patrón `selection_fingerprint` del proyecto, aunque este experimento no toca el gate.)

---

## §8 · Qué NO es

- **No** codifica al operador (incodificable: su edge es selección).
- **No** es el Brazo A original (proxy MAE-threshold vs ATR sintético — vetado).
- **No** es un grid: dos reglas pre-registradas, cero sweep. Variantes/time-decay quedan **post-PASS** (Cassian).
- **No** afirma generalización fuera de régimen (§9).

---

## §9 · Techo pre-declarado (límites honestos)

1. **n=27, un régimen alcista** (Mar30–May7 2026). Un PASS es "prometedor in-regime", **no deployable** — direccional, no producto terminado.
2. **Reconstrucción 5m** pierde sub-5m; mitigado por fill pesimista pero no eliminado.
3. **ATR 1h reconstruido** es una elección de parámetro (period=22, mult=3 textbook) — congelada, pero el resultado es condicional a ella. La confirmatoria 38%-giveback es el chequeo cruzado.
4. **Aun un PASS** se monta sobre entrada gross-flat: la salida sólo redistribuye un camino realizado vía skew. El experimento mide exactamente eso y nada más.

---

## §10 · No-Negociables respetados

- **NN#3 holdout:** este experimento NO llama `open_holdout`, NO llama `simulate_strategy` con frames de ventana holdout. Opera sobre trades históricos reales de papá + `ohlcv.db` del repo. Sin relación con #322.
- **NN#2 holdout access:** no se lee `data/holdout/`.
- **NN#4 RISK_PER_TRADE:** intacto — la regla cambia sólo la SALIDA; entrada, talla y `qty` vienen de las posiciones reales.
- **NN#1 PositionClosure:** N/A — sim offline, no cierra posiciones live.

---

## §11 · Preguntas abiertas para el plan de implementación

1. ¿ATR sobre 1h o sobre 4h (el TF macro del sistema)? Congelado a 1h en este diseño; confirmar en el plan.
2. ¿El cap de hold de 200h debe ser por-símbolo (vol-adaptado) o global? Global en este diseño.
3. Reconciliación exacta gross: ¿usar `qty`×(exit−entry) o `size_usd`×ret? Definir en el plan con una sola fórmula.
4. ¿Los 4 SL_HIT entran al baseline con su stop real recosteado a v3, o se tratan distinto? Diseño: igual que MANUAL — su `exit_price` real recosteado. Confirmar.

---

## §12 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-06-03 | Diseño reformulado tras veto 6/6 del roast a Brazo A original. Población congelada (27, 43−16 sin OHLCV). Dos reglas pre-registradas. KILL con ambas ramas informativas. | Claude Opus 4.8 + sssamuelll |
