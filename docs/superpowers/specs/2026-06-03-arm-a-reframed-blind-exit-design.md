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
- **PASS pre-ordenado (Halberg, Adrian, Null Vale).** Fit in-sample sobre los mismos paths; proxy dispara en ~3 trades dominados por id=28; baseline ATR ficticio (`atr_entry` es NULL en las 43 posiciones); drift alcista se filtra. Null pre-desarmado → ritual, no test.

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

**Universo:** las 43 posiciones cerradas en la DB de papá (`C:\Users\simon\Desktop\Papa\trading_backup_extracted\signals.db`, tabla `positions`, `status='closed'`), ventana 2026-03-30 → 2026-05-07. Estructura del universo: 32 MANUAL + 9 SL_HIT + 2 TP_HIT.

**Filtro de reconstruibilidad (pre-declarado, dos condiciones):**
1. Cobertura OHLCV **5m completa** en `data/ohlcv.db` durante `[entry_ts, entry_ts+200h]`.
2. **≥22 barras 1h antes de `entry_ts`** (requerido para el ATR-22 del §3). Verificado: las 27 incluidas tienen ≥20,932 barras 1h pre-entry (mínimo en PENDLE) — la condición no dropea ninguna.

**16 posiciones se dropean** por cobertura OHLCV CERO (ni 5m ni 1h, ningún tf) en 7 símbolos: ZEC(5, incl. 1 TP_HIT), TRX(4, incl. 1 TP_HIT), XAUT(3), TON(2), SOL(1), LINK(1). No es elección de scope — esos trades no existen en espacio-precio. **Los 2 TP_HIT del universo caen en este drop** (ZEC/TRX), por eso el keep-set es solo MANUAL+SL_HIT.

**Población efectiva: N = 27** (sobre 8 símbolos con 5m completo Y posiciones cerradas: BTC, ETH, RUNE, XLM, PENDLE, UNI, DOGE, AVAX; ADA tiene 5m pero 0 posiciones):

| exit_reason | n | sum_pnl_usd (realizado, recomputado fresco sobre el keep-set) |
|---|---:|---:|
| MANUAL | 23 | +76.88 |
| SL_HIT | 4 | −20.94 |
| **Total** | **27** | **+55.94** |

**Dirección: LONG 19 / SHORT 8** (verificado en DB). **sl/tp en el keep-set:** solo los 4 SL_HIT tienen `sl_price`/`tp_price`; los 23 MANUAL los tienen NULL. `atr_entry` NULL en los 27 (de ahí la reconstrucción del §3). `qty` no-NULL en los 27; `size_usd` NULL en 4 → la fórmula gross usa `qty` (§4). El conteo exacto incluido/dropeado por símbolo se vuelca al artefacto de salida como provenance.

> Nota de provenance (Adrian F-8): el +76.88 se recomputa fresco sobre los 23 MANUAL del keep-set; coincide numéricamente con el n=16 del `q2_filtering_edge.json` porque los 7 MANUAL adicionales netean ≈0, no por copia. Confirmado por query directo a la DB.

---

## §3 · Las dos reglas (PRE-REGISTRADAS, sin sweep)

Decisión de Samuel: una primaria que decide PASS/FAIL + una confirmatoria de robustez.

### Regla PRIMARIA — Chandelier textbook (decide el veredicto)
- **Trailing stop = peak_MFE − 3 × ATR** (LONG); `trough_MAE + 3 × ATR` (SHORT). Constante 3 elegida **del libro**, ciega a esta data → cero tuning in-sample.
- **ATR:** 22-period ATR (Wilder) computado sobre barras **1h** terminando en `entry_ts`, reconstruido de `ohlcv.db` (porque `atr_entry` es NULL en las 27). Congelado al abrir; no se recomputa intra-trade (el "peak" sí trailea).
- **CONGELAMIENTO IRREVOCABLE (Adrian F-1):** los tres parámetros `(mult=3, period=22, tf=1h)` quedan fijados AQUÍ, antes de cualquier corrida. El veredicto se liga a esta única combinación. **Cualquier cambio posterior de mult/period/tf es un EXPERIMENTO NUEVO con su propio pre-registro, no una lectura de robustez** — esto cierra la latitud de multiple-comparisons. Queda explícitamente prohibido "si falla en 1h, probar 4h".
- **Stop inicial:** al abrir, `entry_price − 3×ATR` (LONG). El trailing sólo sube (LONG) / baja (SHORT), nunca afloja.
- **Disparo y fill:** evaluado sobre **closes/wicks 5m**. Convención **pesimista**: dentro de una barra 5m se asume que el extremo adverso (low LONG / high SHORT) toca antes que el favorable; si el wick adverso cruza el stop, sale **al precio del stop**. Mata la ambigüedad intra-barra (Halberg CI-1) por el lado conservador.
- **Cap de hold:** si el stop nunca dispara, sale a **200h** post-entry (apenas sobre el máximo observado 167h) — mantiene la regla ciega (no usa el `exit_ts` real del operador).

### Regla CONFIRMATORIA — 38%-giveback (robustez, NO decide)
- Trailing que suelta **38% del MFE corrido** (ancla: el operador captura 62% → suelta 38%; `d3_excursion.json` mediana capture 62.4%). Mismo motor de path/fill/costos.
- **Rol:** distingue "ninguna salida mecánica funciona" (ambas FAIL) de "sólo la del estilo del operador funciona" (textbook FAIL, giveback PASS). Cassian: **se computa pero NO gatilla el veredicto**; si la primaria FALLA, la confirmatoria no la rescata.

---

## §4 · Baseline (sin ficción)

Por cada una de las 27 posiciones:
1. **`actual_net_v3`** = recosteo del exit REAL (precio/ts realizado de la DB) bajo costos v3. **Gross congelado:** `gross = qty × (exit_price − entry_price)` para LONG, `qty × (entry_price − exit_price)` para SHORT (resuelve §11 Q3; `qty` no-NULL en los 27, `size_usd` NULL en 4 → no se usa `size_usd`). Los costos se recomputan con `backtest_costs.compute_trade_costs(model="v3")` para apples-to-apples (NO se usa el `pnl_usd` registrado, que lleva los costos reales de papá).
2. **`blind_net_v3`** (por regla) = mismo gross con el `exit_price`/`exit_ts` elegidos por la regla ciega sobre el path 5m, menos costos v3.
3. **Contrato de liquidez v3 (Halberg c):** `compute_trade_costs(model="v3")` requiere `entry/exit_liquidity_usd_per_min` y `TierParams` por símbolo. El proxy de liquidez se deriva de `ohlcv` (volume × price / min) en la barra del fill; el tier vía `tier_for_symbol`; la calibración v3 vía `load_calibration`. La identidad de calibración + el proxy se estampan en provenance. Ambos brazos (blind y actual) usan EL MISMO proxy en SU barra de fill respectiva.
4. **Diferencia pareada** `Δ_i = blind_net_v3_i − actual_net_v3_i`. Mismo trade, misma entrada, misma talla — sólo cambia la salida. Pareo limpio.

---

## §5 · Estadística

- **Estimando:** media pareada `Δ̄` y suma `ΣΔ` sobre los 27. Bootstrap (10k, seed pre-declarado) sobre los Δ_i pareados.
- **Leave-one-out:** se recomputa `Δ̄` dropeando cada trade; se reporta el rango LOO. El resultado debe **sobrevivir dropear el trade más influyente**. (El preview de Halberg muestra que los dominantes son id=28 XLM, id=43 PENDLE +9.17%, e id=47 RUNE — id=43 no estaba previsto en el diseño original; los tres se reportan.)
- **Sensibilidad de fill (Adrian F-5):** se corre el estimando bajo fill **pesimista Y optimista** (favorable-antes-de-adverso). El brazo blind se simula; el baseline es el fill real (no re-simulado), así que la convención toca solo al blind. Para que un **FAIL sea limpio, debe sostenerse bajo AMBAS convenciones** — si el signo depende de la convención, el veredicto es "indeterminado por granularidad", no "no edge".
- **Reportes obligatorios:** CI bootstrap full-sample, CI LOO, resultado **con y sin** cada top-influencer, y bajo ambas convenciones de fill. Por dirección (LONG 19 / SHORT 8) separado como diagnóstico.

---

## §6 · Criterio KILL (ambas ramas informativas)

- **PASS (existe edge de salida extraíble):** la regla PRIMARIA bate lo realizado — `Δ̄ > 0` con CI bootstrap 95% que excluye cero **bajo AMBAS convenciones de fill** (pesimista Y optimista), **Y** el signo/CI sobrevive el leave-one-out del trade más influyente. (El requisito de ambos fills endurece el gate: un PASS que solo se sostiene bajo la convención optimista-favorable no cuenta. Es estrictamente más conservador que exigir solo el brazo primario — nunca produce un falso-positivo.)
- **FAIL (no hay edge extraíble):** CI incluye cero (o `Δ̄ ≤ 0`) bajo ambas convenciones, o el signo se voltea al dropear el top-influencer. → **NO limpio** (no "underpowered ritual"): ni la salida trend-following textbook extrae expectativa de este stream.
- **INDETERMINATE:** el signo del veredicto depende de la convención de fill (pesimista vs optimista discrepan) → indeterminado por granularidad intra-barra, ni PASS ni FAIL limpio.
- **Confirmatoria:** se reporta su resultado aparte, **descriptivo solamente (Adrian F-4): barrado de CUALQUIER claim de existencia-de-edge**, no solo del gate del veredicto. Su parámetro (38%) viene de la captura 62% de ESTA data → no puede ser evidencia de que existe edge independiente del operador. Sirve únicamente para la lectura cualitativa "¿solo el estilo-operador, o ninguna salida mecánica?".

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
2. **Techo de cobertura de símbolos (Adrian F-3):** los 27 viven en 8 símbolos líquidos (BTC/ETH/RUNE/XLM/PENDLE/UNI/DOGE/AVAX); los 6 dropeados son los menos líquidos. Un PASS generaliza SOLO a este subset líquido, **no "al stream"**. El estimando se lee explícitamente como "salida ciega sobre el subset reconstruible", no sobre el universo de 43.
3. **Reconstrucción 5m** pierde sub-5m; mitigado por fill pesimista + sensibilidad optimista (§5) pero no eliminado.
4. **ATR 1h reconstruido** es una elección de parámetro (period=22, mult=3 textbook) — congelada irrevocable (§3), pero el resultado es condicional a ella. La confirmatoria 38%-giveback es el chequeo cruzado (descriptivo).
5. **Cap de hold 200h:** empíricamente INERTE — el preview de Halberg muestra 27/27 disparan el trailing, 0/27 llegan al cap. No carga peso en este dataset (mitiga Adrian F-6).
6. **Aun un PASS** se monta sobre entrada gross-flat: la salida sólo redistribuye un camino realizado vía skew. El experimento mide exactamente eso y nada más.

---

## §10 · No-Negociables respetados

- **NN#3 holdout:** este experimento NO llama `open_holdout`, NO llama `simulate_strategy` con frames de ventana holdout. Opera sobre trades históricos reales de papá + `ohlcv.db` del repo. Sin relación con #322.
- **NN#2 holdout access:** no se lee `data/holdout/`.
- **NN#4 RISK_PER_TRADE:** intacto — la regla cambia sólo la SALIDA; entrada, talla y `qty` vienen de las posiciones reales.
- **NN#1 PositionClosure:** N/A — sim offline, no cierra posiciones live.

---

## §11 · Decisiones cerradas (post-auditoría Adrian+Halberg)

Todas las preguntas abiertas de la v1 quedaron resueltas y congeladas:
1. **ATR tf:** 1h, period=22, mult=3 — congelado irrevocable (§3). Cambiarlo = experimento nuevo.
2. **Cap de hold:** 200h global. Empíricamente inerte (§9.5).
3. **Fórmula gross:** `qty × (exit−entry)` con signo por dirección (§4). `size_usd` no se usa.
4. **Los 4 SL_HIT:** mismo trato que MANUAL — su `exit_price` real recosteado a v3 (§4).
5. **ATR pre-entry coverage:** verificado ≥22 barras 1h pre-entry en los 27 (§2).
6. **Convención de fill:** pesimista primaria + optimista como sensibilidad obligatoria para limpiar un FAIL (§5).

Único ítem que el plan debe instrumentar (no es decisión de diseño): el wiring exacto del proxy de liquidez v3 desde `ohlcv` (§4 contrato de liquidez).

## §12 · Preview de feasibility (Halberg) — NO es el veredicto

> Durante la auditoría, Halberg ejecutó el chandelier 3×ATR sobre los 27 paths reales (GROSS, sin v3, sin la convención de fill completa). **Esto NO es el veredicto pre-registrado** — es señal de feasibility + un prior. El veredicto solo cuenta tras correr la versión congelada de este spec (5m, fill pesimista+optimista, costos v3, LOO).

Lo que el preview mostró: 27/27 disparan el trailing (0 al cap); `Δ̄ ≈ −0.079%`, CI95 `[−1.21%, +1.18%]` cruza cero; el blind pierde contra lo realizado en 17/27; dominado por id=28/id=43/id=47. **Dirección del prior: FAIL** — el chandelier textbook NO bate las salidas del operador (consistente con q2/q3: el edge del operador es selección, no una función de timing que un chandelier replique). El recosteo v3 no debería rescatarlo (turnover similar, 1 salida por trade en ambos brazos).

---

## §13 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-06-03 | Diseño reformulado tras veto 6/6 del roast a Brazo A original. Población congelada (27, 43−16 sin OHLCV). Dos reglas pre-registradas. KILL con ambas ramas informativas. | Claude Opus 4.8 + sssamuelll |
| 2026-06-03 | Revisión post-auditoría Adrian+Halberg sobre el spec escrito. Correcciones fácticas (sl/tp NULL solo en MANUAL del keep-set, no en las 43; SHORT 8 no ~6; 2 TP_HIT en el drop). F-1 congelamiento irrevocable de (3,22,1h). F-2 cobertura ATR pre-entry verificada. F-5 sensibilidad de fill optimista para limpiar FAIL. F-3 techo de símbolos líquidos. F-4 confirmatoria descriptiva. Gross congelado a qty×Δprice. Preview de Halberg (gross FAIL) registrado como prior, no veredicto. | Claude Opus 4.8 + sssamuelll |
