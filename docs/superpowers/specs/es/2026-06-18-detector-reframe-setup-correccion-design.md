# Reframe del detector per-coin: de "valle" a "réplica del filtro histórico de musikito" — diseño

**Fecha:** 2026-06-18
**Subproyecto 2 de la reorientación de Valles.** (SP1 = pieza de régimen, ya en `main` PR #610.
SP3 = rediseño profundo de la UI per-coin, spec aparte.)
**Estado:** revisado por el roster (5 lentes, 2026-06-18); BLOCKERS+HIGH aplicados + decisión de
honestidad completa (AC7) tomada por Samuel.

## Goal

Reemplazar el gate roto del detector (`screener/valley_filter.py`) — amplitud de 84 días
**invariante al orden** ("valle") — por el filtro real que usaba musikito: **vivo + parte baja de
su rango de 30d**. Exhibir RSI / distancia-a-SMA / consolidación / volumen como **hechos
descriptivos**. Y reframear el frontend mínimo para matar las frases falsas de "valle" — **sin
contrabandear una nueva promesa**: el objeto se nombra por su PROCEDENCIA (réplica del filtro de un
canal de 2019), no por una tesis de mercado, y se exhibe el hecho medido de que ese filtro **no le
ganó al azar** ni en su mejor régimen.

## Por qué (evidencia)

El detector actual marca igual el **techo** del rango (sobrecomprada) que el **piso** (la zona del
filtro de musikito) porque `pct_rango = (max−min)/mediana` es ciego a *dónde* está el precio. La
firma medida (`docs/superpowers/specs/es/2026-06-18-musikito-firma-estadistica-evidencia.md`):
cuartil inferior (pos≈0.165 vs azar 0.256), RSI≈38.7, bajo SMA20/50. El estudio multi-régimen
confirmó que **esa selección no tiene edge** — incluso en alt-bull 2019 (el único régimen medido,
y el "verde") el filtro **subrindió al azar** (14d: 9.92% vs 12.54%; 7d: 6.67% vs 7.56%). El régimen
es lo que paga, no la coin (cabecera SP1). Por eso el gate de posición se justifica por **fidelidad
documental** ("esto es lo que ese canal miraba en 2019"), NO por edge ni por neutralidad.

## Alcance (SP2)

El cambio de contrato del detector **fuerza** tocar el frontend (hoy lee `pct_rango`/
`semanas_consolidando` y dice "en valle"); no se puede mergear un backend que rompe el front.

- **Backend:** reframe de `screener/valley_filter.py` (gate + hechos emitidos).
- **Snapshot de prod:** `tools/run_valley_screener.py` (`build_snapshot → evaluate_symbol →
  data/valley_candidates.json`) y `api/valleys.py` **propagan el dict por spread sin editar
  código**. PERO el snapshot persistido `data/valley_candidates.json` queda **incompatible** con el
  contrato nuevo (campos eliminados/añadidos): hasta que se regenere, `/valley-candidates` sirve
  `pct_rango`/`semanas_consolidando` viejos y el front nuevo lee `undefined` → "NaN%". **El deploy
  DEBE forzar la regeneración** vía el freshness-owner del screener (`screener_loop`,
  Non-Negotiable #8) antes/al activar el front nuevo. Se documenta como paso de deploy.
- **Frontend MÍNIMO honesto:** cambio de campos del contrato + matar TODAS las frases falsas
  ("en valle", "franja angosta", "X semanas", "tiene jugada") + reetiquetar la banda del gráfico +
  exhibir el hecho medido (AC7).
- **FUERA (→ SP3):** banda de rango-30d dibujada con marcador de posición, rediseño de layout/
  jerarquía/microcopy, subordinación visual del per-coin a la cabecera.

## Backend: el detector reframeado

### Constantes (PROVISIONALES, calibración = POST-SHIP)
```python
SETUP_POS_MAX = 0.25         # pos_in_30d_range ≤ esto ⟹ candidata. Es el corte que MIDIÓ el
                             # estudio multi-régimen. (Mediana de musikito 2019 = 0.165; el corte
                             # 0.25 es laxo respecto a esa mediana — calibrar es POST-SHIP.)
RANGE_WINDOW_DAYS = 30
SMA_FAST = 20
SMA_SLOW = 50
DRAWDOWN_WINDOW_DAYS = 90
VOL_FAST_DAYS = 3
VOL_SLOW_DAYS = 30
```
`classify_liveness` ya exige ≥120 barras (`MIN_HISTORY_DAYS`), que cubre las ventanas (máx 90d).
Sobre densidad: liveness garantiza `len(bars) ≥ 120` pero no descarta huecos del exchange;
`measure_setup` opera sobre las últimas N barras disponibles y **clampa todo denominador** (abajo),
así que datos ralos producen un hecho degradado, nunca `nan`/excepción.

### `measure_setup(bars) -> dict` (función pura nueva)
Devuelve SIEMPRE las 7 claves. **Clave del contrato de barras = `quote_volume`** (la del módulo;
NO `quote_vol`, que es del DataFrame de `edge_study.py`). Cierres/altos/bajos en float.
```python
close = float(bars[-1]["close"])
lo30, hi30 = min(low,30), max(high,30)
pos_in_30d_range = (close - lo30) / max(hi30 - lo30, 1e-9 * close)     # ∈ [0,1] aprox
rsi14            = _wilder_rsi(closes, 14)                              # ver nota de equivalencia
sma20 = mean(close,20) or 1e-9*close ; pct_vs_sma20 = (close - sma20)/sma20 * 100
sma50 = mean(close,50) or 1e-9*close ; pct_vs_sma50 = (close - sma50)/sma50 * 100
med30 = median(close,30) or 1e-9*close ; consol_30d = (hi30 - lo30)/med30 * 100
qv30  = median(quote_volume,30) ; vol_ratio = (median(quote_volume,3)/qv30) if qv30 else 0.0
hi90  = max(high,90) or 1e-9*close ; drawdown_from_90h = (close - hi90)/hi90 * 100   # ≤ 0
```
Todos los denominadores **clampeados** (espejando la protección `or 1.0` de `measure_consolidation`)
— un libro muerto o datos corruptos NUNCA producen `nan`/`inf`. `_wilder_rsi` es una implementación
pura en el módulo (sin pandas) que **converge** a `edge_study.wilder_rsi` en el régimen de
producción (≥120 barras, |diff|<0.01); diverge solo en historia corta, que el gate de liveness
(120) ya excluye del candidato. Un test ancla la convergencia. Pura, determinista, espejo de `measure_consolidation`.

**Los 6 hechos no-gate (`rsi14`, `pct_vs_sma20/50`, `consol_30d`, `vol_ratio`, `drawdown_from_90h`)
son HECHOS EXHIBIDOS, NUNCA gates.** Solo `pos_in_30d_range` admite/rechaza.

### `evaluate_symbol(symbol, bars) -> dict | None` (gate nuevo)
```python
vivo, razones = classify_liveness(bars)
if not vivo:
    return None
setup = measure_setup(bars)
if setup["pos_in_30d_range"] > SETUP_POS_MAX:
    return None                      # no está en la parte baja de su rango
return {
    "symbol": symbol,
    "price": float(bars[-1]["close"]),
    **setup,                          # los 7 hechos
    "volumen_usd_dia": liquidity_value(bars),
    "distancia_ath_pct": _distancia_ath_pct(bars),
    "razones_vida": razones,
}
```
**Claves del candidato (EXACTAS):** `{symbol, price, pos_in_30d_range, rsi14, pct_vs_sma20,
pct_vs_sma50, consol_30d, vol_ratio, drawdown_from_90h, volumen_usd_dia, distancia_ath_pct,
razones_vida}`. **Eliminadas:** `pct_rango`, `semanas_consolidando`, `vol_percentil`, `en_rango`.

### `/valley-eval` (api/valleys.py)
La rama candidata spread-ea los 7 hechos (propagación libre). La rama **no-candidata pero viva**
(`pos > SETUP_POS_MAX`) sigue devolviendo `{candidata: False, vivo, razones_muerte}` **sin `pos`** —
y por eso el copy de Narrativa "no candidata" NO promete `{pos}%` (ver §Frontend). Sin cambio de
código en `valleys.py`.

### Lo que queda INTACTO (no romper tooling de investigación) — deuda conocida
- `measure_consolidation`, `RANGE_BAND_MAX`, `CONSOLIDATION_WINDOW_DAYS`, `_realized_vol`,
  `vol_percentil`, `VOL_PERCENTILE_WINDOW_DAYS` — se mantienen: los usa `tools/valle_calidad_probe/`
  (research, concepto legacy de valle). `evaluate_symbol` deja de llamarlos. **Deuda conocida
  declarada:** tras el reframe, `_realized_vol`/`vol_percentil`/`VOL_PERCENTILE_WINDOW_DAYS` quedan
  sin consumidor en el path de producto (solo el probe legacy). NO es código muerto a borrar en
  SP2; retirar el probe es otra decisión. Sus tests (`TestMeasureConsolidation`,
  `test_valle_probe_*`) NO cambian.
- `classify_liveness`, `liquidity_value`, `_distancia_ath_pct`, `order_neutral` — sin cambios.

## Contrato: tipos del frontend

`frontend/src/types.ts` — `ValleyCandidate` y `ValleyEval`:
- **Quitar:** `pct_rango`, `semanas_consolidando`, `vol_percentil`.
- **Añadir:** `pos_in_30d_range`, `rsi14`, `pct_vs_sma20`, `pct_vs_sma50`, `consol_30d`,
  `vol_ratio`, `drawdown_from_90h` (todos `number`; en `ValleyEval` opcionales como el resto).
- Mantener: `symbol, price, volumen_usd_dia, distancia_ath_pct, razones_vida` (+ los de `ValleyEval`).

## Frontend mínimo honesto

**Inventario EXHAUSTIVO de sitios (verificar con `git grep` que cero referencias a
`pct_rango`/`semanas_consolidando`/`vol_percentil`/`en valle` sobreviven al merge):**

**`PickScreen.tsx`:**
- Encabezado "Hoy hay N monedas **en valle**" → "Hoy hay N monedas **en la parte baja de su
  rango**"; vacío → "ninguna en la parte baja de su rango ahora".
- Lead "se mueven poco y siguen vivas" → "en el cuartil inferior de su rango de 30d — el filtro que
  usaba el canal de 2019".
- Tag "● en valle" → "● parte baja del rango".
- Línea por-card "se mueve X% · Y semanas quieta" → "cuartil inferior (pos
  {(pos_in_30d_range*100).toFixed(0)}%) · RSI {rsi14.toFixed(0)}".
- **Quitar el bloque `ju-pickmark` "tiene jugada" (`PickScreen.tsx:45-48`)** — veredicto de
  accionabilidad sin sustento (su propio `TODO` lo admite); NO reemplazar por otra promesa.

**`Narrativa.tsx`** (bloque ¿viva?):
- Candidata (`Narrativa.tsx:44-64`): matar "lleva X semanas en una franja angosta… estar **en
  valle**". Nuevo: "Está viva y **en la parte baja de su rango de 30d** (posición {pos}%), por
  debajo de su SMA20 ({pct_vs_sma20}%), RSI {rsi14}."
- No candidata (`Narrativa.tsx:44-45`, literal "no entra en el análisis de valle… no hay franja que
  seguir"): → "**No está en la parte baja de su rango** ahora." (sin `{pos}%`, que el backend no
  emite en esa rama). Si `vivo===false`, las razones de muerte como hoy.
- Segundo "franja" en `Narrativa.tsx:184`: eliminar/reformular.
- **Costura honesta (AC7, load-bearing copy)** — reemplaza la promesa diferida: *"Esto es el filtro
  que usaba el canal de 2019. Medido, no le ganó al azar de alts ni en su mejor régimen
  (alt-bull 2019: 14d 9.92% vs 12.54%). Lo que movió el retorno fue el régimen, no esta selección.
  La decisión es tuya."* (Redacción calibrable vía `solace-wren`; su presencia y los números son AC.)

**`chartLayers.ts` / `IdeaChart.tsx`:**
- `chartLayers.ts`: quitar el cómputo de la banda desde `pct_rango` y el uso de
  `semanas_consolidando`. La capa Vida lleva el hecho de posición + `vivoStamp`.
- `IdeaChart.tsx:281`: quitar el sufijo `· ${m.vida.semanas} sem en rango` del `vivoStamp` → "viva ·
  pos {pos}% del rango 30d".
- `IdeaChart.tsx:33` (`LAYER_LABELS.vida`): "Vida (el valle)" → "Vida (¿viva? · posición)". (La
  etiqueta vive en `IdeaChart.tsx`, NO en `chartLayers.ts`.)
- La banda dibujada (rango-30d + marcador) es SP3; SP2 solo mata lo roto y reetiqueta.

**`recap.ts`** (anclar al campo `candidata`, NO a los strings viejos — `'Muy quieta'` era
`candidata===false`, que ahora significa lo CONTRARIO):
- `candidata===true → 'En la parte baja de su rango'`
- `candidata===false → 'No en la parte baja'`
- `estado==='no_disponible' → '—'`

**`Copilot.tsx:17`:** la sugerencia hardcodeada `'¿Qué quiere decir "en valle"?'` → `'¿Qué quiere
decir "parte baja del rango"?'`.

## Doctrina anti-veredicto

**Dos clases de objeto, distinguidas explícitamente (corrige el "es un hecho neutral" nominal):**
- **Criterio de admisión** (1 ítem: `pos_in_30d_range ≤ SETUP_POS_MAX`) — filtrar 400 coins a N YA
  es un veredicto de RELEVANCIA. Se justifica por **fidelidad documental** ("esto es lo que ese
  canal miraba en 2019"), un acto de procedencia — NUNCA por edge (§1.2 lo niega) ni por neutralidad.
- **Hechos exhibidos** (6 ítems) — descriptivos, sin umbral.

**El objeto se nombra por PROCEDENCIA**, no por tesis de mercado: "réplica del filtro histórico de
musikito" / "el filtro que usaba el canal de 2019". Prohibido el rótulo "setup de corrección" y la
palabra "cazaba" (contrabandean éxito). La cabecera de régimen (SP1) **NO valida el setup per-coin**
— y como el único régimen medido es donde el filtro subrindió, AC7 exhibe ese hecho para que la
combinación lista-curada + régimen-verde NO se lea como señal de compra.

Disciplina léxica del contrato: fuera `valle`, `va a subir`, `señal`, `fuertes`, `débil`, `cazaba`,
`tiene jugada`, `setup de corrección`. "musikito"/"filtro de 2019" se permiten SOLO como procedencia
y SIEMPRE acompañados del resultado medido. El test léxico (abajo) serializa el payload completo a
JSON (keys + values + `razones_vida[]`); es **necesario pero no suficiente** — la limpieza del gate
se argumenta en este spec, no en el filtro de palabras.

## Testing (TDD)

**Backend (`tests/test_valley_filter.py` + nuevos):**
- `measure_setup`: `pos_in_30d_range` (piso=0.0, techo≈1.0, medio, frontera 0.25, denom clampeado en
  rango plano); `rsi14` (subida pura → ~100, bajada pura → ~0) **+ test de equivalencia numérica con
  `edge_study.wilder_rsi`**; `pct_vs_sma20/50`; `consol_30d`; `vol_ratio` (con `quote_volume`);
  `drawdown_from_90h` (≤0); **clamp denom-cero por cada hecho** (libro plano → sin `nan`/`inf`).
- **Test de aceptación (el bug murió):** coin viva en el **piso** de su rango 30d (`pos ≤ 0.25`)
  **PASA**; coin viva **idéntica en amplitud** pero en el **techo** (`pos ≥ 0.75`) devuelve **None** —
  aunque el `pct_rango` de 84d sea igual en ambas.
- `TestEvaluateYorden` (actualizado): el candidato tiene **EXACTAMENTE** el set de claves nuevo
  (igualdad de conjunto, no superset — para que un `measure_setup` futuro no sobrescriba
  `symbol`/`price` en silencio) y NINGUNA clave vieja.
- Doctrina léxica: el candidato y el payload de `/valley-eval` (serializados a JSON completo) NO
  contienen `valle`/`va a subir`/`señal`/`fuertes`/`débil`/`cazaba`/`tiene jugada`/`setup de
  corrección`.
- **Intactos (no tocar):** `TestMeasureConsolidation`, `test_valle_probe_episodes.py`,
  `test_valle_probe_constants.py`.

**API (`tests/test_valley_eval_api.py`, `tests/test_valles_freshness.py`):** actualizar fixtures a
los campos nuevos.

**Frontend (vitest) — lista exhaustiva a migrar:** `Narrativa.test.tsx` (ya no "X semanas"; asserta
posición + ausencia de "valle" + presencia de la costura con los números medidos),
`chartLayers.test.ts` (ya no `band`/`semanas`; asserta el sello de posición), `recap.test.ts`
(nuevos strings anclados a `candidata`), `api.test.ts`, `IdeaChart.test.tsx`, `IdeaView.test.tsx`,
`PickScreen.test.tsx`, `ValleysFlow.test.tsx`, `useValleyBundle.test.tsx`, `doctrine.test.tsx`
(fixtures + el assert de doctrina del chrome). Test nuevo: la narrativa NO dice
"valle/franja/semanas/tiene jugada" y SÍ exhibe la costura con el under-rendimiento medido.

## Acceptance criteria

1. `measure_setup` puro y testeado (incl. convergencia RSI en régimen de producción ≥120 barras y clamps denom-cero); `evaluate_symbol`
   gatea por `pos_in_30d_range ≤ SETUP_POS_MAX` y emite el set de hechos exacto.
2. Test de aceptación piso/techo en verde (techo NO pasa con amplitud idéntica).
3. `measure_consolidation` + probe + sus tests intactos.
4. Contrato del frontend actualizado; `git grep` confirma **cero** referencias supervivientes a
   `pct_rango`/`semanas_consolidando`/`vol_percentil`/"en valle"/"tiene jugada" en `frontend/src`.
5. El payload pasa el test léxico de doctrina (lista ampliada).
6. Gate del repo en verde: `python -m pytest tests/ -m "not network" -n auto -q` y el job de
   frontend (vitest + `tsc --noEmit`).
7. **(Honestidad — no-negociable):** la costura per-coin exhibe el hecho medido — *en el único
   régimen medido (alt-bull 2019) este filtro NO le ganó al azar de alts (14d 9.92% vs 12.54%)* — y
   el objeto se nombra por procedencia, no por tesis de mercado. La doctrina declara "la cabecera de
   régimen NO valida el setup per-coin".
8. **Deploy:** el snapshot `data/valley_candidates.json` se regenera (freshness-owner del screener)
   al activar el contrato nuevo; documentado como paso de deploy (#8).

## Fuera de alcance (explícito)

- **SP3:** banda de rango-30d dibujada + marcador de posición; rediseño de layout/jerarquía/
  microcopy; subordinación visual del per-coin a la cabecera de régimen.
- Retirar el probe `valle_calidad_probe` y limpiar los huérfanos `_realized_vol`/`vol_percentil`.
- Calibrar `SETUP_POS_MAX` y los umbrales contra el panel 2020–2025 (POST-SHIP; mediana de
  musikito 0.165 vs corte actual 0.25).
- Lente-momentum (deferido conscientemente).
