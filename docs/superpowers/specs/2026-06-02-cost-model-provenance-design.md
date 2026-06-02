# Selection-world provenance fingerprint for evaluation ledgers — diseño

**Fecha:** 2026-06-02
**Estado:** diseño aprobado (revisado tras reencuadre de Voronov), listo para plan.
**Origen:** post-merge de cost-model v3 (#554, `4c6f1ec`). Voronov (consulta 1): el cost-model es un
**SELECTOR**, no un dato — v3 (~30-40× más liviano que v2) admite parámetros que v2 vetaba; artefactos
de evaluación bajo selectores distintos no son comparables. Voronov (consulta 2, sobre el primer
borrador de este spec): **el cost-model es UNA coordenada del world-state de selección, no el límite
de la comparabilidad.** La unidad honesta es la *huella del mundo de selección completo* — un
fingerprint, no una columna cost-model-específica.
**Archivos gobernados:** `db/hypotheses.py` = holdout falsification gate (#553, guardrail crítico, bala
única irreversible). `db/trials.py` = trial registry (#278). Ambos production-governing.

## 0. El problema (verificado) y su forma real

Ni `db/trials.py` ni `db/hypotheses.py` registran bajo qué **mundo de selección** se produjo un
artefacto. Consecuencias no guardadas hoy:

1. **N de deflación contaminada.** `selection_population_stats` agrupa TODOS los trials exploratorios
   en una sola población, mezclando mundos distintos. La penalización best-of-N (López de Prado) se
   computa sobre una población incoherente.
2. **Re-selección disfrazada de falsificación.** Una hipótesis del holdout congelada bajo un mundo y
   disparada bajo otro *no fue falsificada — fue re-seleccionada bajo otro criterio de admisibilidad.*
   La bala del holdout es de un solo uso e irreversible.

**El cost-model es la coordenada que se movió este trimestre (salió v3). NO es la única.** El
deflated-Sharpe que se congela al lockear es función de TODO el mundo de selección: cost-model,
**los parámetros de deflación** (`A03_DECAY_DATE`/`A03_N_FLOOR` en `db/trials.py` — el `2026-11-29`
del decay VA a pasar — más la versión del algoritmo `deflation.py`), el cutoff/ventana del holdout, y
(coordenadas más difusas) la versión del código de estrategia y el rango/versión del OHLCV. Tagear
solo el cost-model es la **falacia de celdas-discretas**: enumerar la coordenada saliente de hoy y
darle slot de schema, para descubrir el próximo trimestre que otra se movió y el seal no dijo nada.

**Regla (Voronov):** *el claim se congela relativo a su mundo de selección entero; si cualquier
constituyente del mundo se movió, el claim debe re-hacerse.* La unidad es **un fingerprint del mundo**,
extensible *dentro del digest*, nunca por acreción de columnas.

## Decisiones tomadas

| Decisión | Valor |
|---|---|
| Unidad de provenance | **Un `selection_fingerprint`** (sha256 sobre el world-state de selección). El cost-model es el **primer ingrediente del digest**, no su nombre |
| Alcance del digest HOY | Sembrado con lo capturable-limpio y genuinamente mutable: **cost-model + params de deflación** (`A03_DECAY_DATE`/`A03_N_FLOOR` + `deflation.ALGO_VERSION`). `strategy_code_sha`, `ohlcv_version` y un `holdout_window_id` documentados como ingredientes FUTUROS que el digest acepta sin churn (caveat bala única hasta que entren). El cutoff del holdout es inmutable hoy (NN#3, dataset locked) → no es eje de drift, fuera del set sembrado |
| Guard de disparo del holdout | **Rechazo HARD** on mismatch; `selection_fingerprint` es campo FROZEN (seal + trigger) |
| Origen del stamp | **Auto-stamp** en el módulo db (cierra el footgun de raíz) |

## 1. El fingerprint del mundo de selección (`selection_provenance.py`, módulo nuevo)

```python
_DIGEST_VERSION = 1  # bump cuando el SET de ingredientes cambie (añadir una coordenada
                     # re-versiona el fingerprint: mundos previos eran más gruesos y no
                     # son directamente comparables — honesto y auditable).

def selection_fingerprint() -> tuple[str, dict]:
    """(fingerprint_hash, components). sha256 sobre el MUNDO DE SELECCIÓN — el set
    completo de coordenadas bajo las que se computa un deflated-selection-metric.
    Dos artefactos con fingerprints distintos NO son comparables. Coordenadas
    sembradas (extensible: coordenadas nuevas se añaden AQUÍ, jamás como columnas
    de schema / campos frozen / cláusulas de trigger nuevas):
      - cost_model:  (active_model, calibration_identity_hash)            [backtest_costs]
      - deflation:   (A03_DECAY_DATE, A03_N_FLOOR, deflation.ALGO_VERSION) [db.trials + deflation]
    Ingredientes FUTUROS (mecanismo de captura TBD; hasta que entren, el fire-guard
    es CIEGO al drift en estos ejes — ver §6 caveat bala única):
      - strategy_code:   git HEAD SHA
      - ohlcv:           descriptor de rango/versión de datos
      - holdout_window:  un id de ventana (hoy el cutoff es inmutable, NN#3 — no
                         drifta; entra solo si se define una ventana de otra época)
    """
```

- `calibration_identity_hash(cal)` (en `backtest_costs.py`): sha256 del SELECTOR — los números que
  deciden qué params admite la cota (version, active_model, global, tiers floor+tail / v2 base+sf), NO
  la prosa (sources/sensitivity_note). Robusto a whitespace (se computa sobre el objeto `Calibration`).
- El digest serializa `{components, _digest_version}` con `json.dumps(sort_keys=True, default=str)`
  (determinista). Memoizado por proceso (el mundo activo no cambia mid-run).
- `selection_provenance.py` importa `backtest_costs`, `deflation`, y las constantes A03 de `db.trials`
  — sin ciclo (`backtest_costs`/`deflation` no importan `db`).

## 2. `trials` (#278) — provenance + pooling homogéneo

- **Schema:** `ADD COLUMN cost_model TEXT, selection_fingerprint TEXT`. `cost_model` = el string
  `active_model` (etiqueta legible para reportes); `selection_fingerprint` = el hash (la identidad).
  Nuevas DBs los obtienen en el CREATE; existentes vía `ALTER TABLE ADD COLUMN` idempotente (guarded
  por `PRAGMA table_info`).
- **`claim_trial`:** auto-stampa ambos vía `selection_fingerprint()`. Cero carga al caller.
- **`selection_population_stats(*, study_type='exploratory', selection_fingerprint=None)`:** cuando
  se provee, filtra `WHERE selection_fingerprint = ?` — la N de deflación agrupa **solo trials del
  mismo mundo**. (None = pooling legacy; el holdout SIEMPRE lo pasa.) Filas con fingerprint NULL no
  matchean → excluidas (un trial de mundo desconocido no entra a ninguna población conocida).
- **Backfill:** filas existentes (todas pre-v3) → `cost_model='v2'`, `selection_fingerprint` = el
  fingerprint con cost-model = `costs_calibration.v2.json` + los params de deflación y cutoff actuales.
  Asunción del backfill: las coordenadas no-cost-model fueron estables en la era v2 (cierto — solo el
  cost-model cambió; `A03_*` y el cutoff no se han tocado). Solo donde `selection_fingerprint IS NULL`
  (idempotente).

## 3. `hypotheses` (#553, holdout gate) — guardrail-crítico

- **Schema:** `ADD COLUMN cost_model TEXT, selection_fingerprint TEXT`. `claim_hypothesis` auto-stampa.
- **Campo FROZEN:** `selection_fingerprint` se añade a `_FROZEN_FIELDS` (el seal sha256 lo cubre) y al
  trigger `hypotheses_frozen_after_lock` (inmutable tras lock). El trigger se **recrea**
  (`DROP TRIGGER IF EXISTS` + `CREATE`) para incluirlo. `cost_model` (etiqueta) NO se sella — es
  reporte; la identidad es el fingerprint.
- **`lock_hypothesis`:** (a) la deflación 4b pools por el `selection_fingerprint` de la hipótesis
  (`_deflation_probability` → `selection_population_stats(selection_fingerprint=...)`); (b) **nuevo
  criterio 4f:** el fingerprint congelado debe == `selection_fingerprint()[0]` *al lockear*; si derivó
  entre claim y lock, `HypothesisLockError` → re-claim.
- **`assert_fireable` (chequeo 6, nuevo):** `selection_fingerprint()[0] == row['selection_fingerprint']`
  o `HoldoutFalsificationError`, holdout intacto.

**Invariante (alcance acotado):** para la **puerta de falsación** (`open_holdout_for_falsification` →
`assert_fireable` check 6, y `lock_hypothesis` 4f), claim → lock → fire corre toda la cadena bajo un
solo mundo de selección, enforced en cada transición. **NO** gobierna la **segunda puerta**
(`walk_forward.evaluate_winner_on_holdout`, la puerta #322): esa lee vía `open_holdout` SIN check de
fingerprint — hoy está doble-bloqueada (`_HOLDOUT_322_CLOSED=False` + un `NotImplementedError` antes
del read) y su migración al gate está **diferida** (PR de cierre de #322, fuera de scope). Cuando #322
abra esa puerta, deberá heredar el check de fingerprint o quedará fuera del invariante. Corolario
gratis: como hoy hay ~0 trials v3, una hipótesis bajo un mundo-v3 no puede lockear (su población de
deflación estaría vacía → `sigma_sr_trials None` → fail-closed YA existente). El diseño **fuerza
construir población bajo el mundo nuevo antes de afirmar bajo él** — sin código nuevo.

## 4. Migración (idempotente)

`_ensure_trials_schema` y `_ensure_schema` (hypotheses), tras `CREATE TABLE IF NOT EXISTS`:
1. `ADD COLUMN` para `cost_model` + `selection_fingerprint` si no existen (`PRAGMA table_info`).
2. Backfill de filas con `selection_fingerprint IS NULL` → v2 (ver §2/§5).
3. (hypotheses) `DROP TRIGGER IF EXISTS hypotheses_frozen_after_lock` + re-`CREATE` con
   `selection_fingerprint` en la cláusula `WHEN`.

Todo idempotente.

## 5. Asunciones documentadas

- **Auto-stamp** asume que el artefacto se produjo bajo el mundo ACTIVO — cierto para las 4 sweeps
  exploratorias (`auto_tune`, `grid_search_tf`, `optimize_new_tokens`, `regime_allocation_sweep`), que
  corren `simulate_strategy` con el modelo activo. `recompute.py` está fijado al sibling v2 pero **no
  registra trials**. Riesgo bajo, documentado.
- **Backfill de hypotheses solo toca filas `draft`.** Añadir `selection_fingerprint` a `_FROZEN_FIELDS`
  cambia el payload de `_compute_seal`, así que el seal de cualquier hipótesis lockeada ANTES de la
  migración ya no coincidiría → `assert_fireable` daría seal-mismatch y exigiría re-claim. **En prod
  es un no-evento: el gate #553 se mergeó sin consumidores, no hay hipótesis lockeadas.** (Premisa a
  confirmar con el operador antes de implementar.)

## 6. El caveat de la bala única (residual honesto)

El fire-guard solo puede disparar sobre drift que el fingerprint **puede ver**. Con el set sembrado
(cost-model + deflación — el cutoff del holdout es inmutable hoy, NN#3, así que NO está en el digest),
el gate es honesto sobre esos ejes y **CIEGO** a TRES ejes de drift no-vistos:

1. **versión del código de estrategia** (git SHA — diferido).
2. **versión/rango del OHLCV** (diferido).
3. **`_TIER_BY_SYMBOL`** (el routing symbol→tier en `backtest_costs.py`) — **un eje ciego que se
   esconde DENTRO de la coordenada "sembrada" cost-model.** `calibration_identity_hash` hashea los
   NÚMEROS de la calibración pero NO el dict de routing; re-asignar un símbolo de tier `mid`→`small`
   re-precia qué candidatos ganan la selección, con un fingerprint **byte-idéntico** que el check 6
   leería como "no-drift". Mitigación HOY: el dict no se ha tocado desde #277 (git lo confirma:
   introducido una vez, nunca editado) y la ceguera está documentada en `backtest_costs.py` — pero
   **el operador lee este §6, no el comentario inline**, así que se enumera aquí explícitamente.

Voronov: *el día que alguien dispare una hipótesis congelada-bajo-v2 tras desplegar v3, "redespliega
v2 para disparar", el cost-model hace match, pero OHLCV/código/tier-routing se movieron desde el lock
— un fingerprint parcial gasta la bala en una re-selección que el gate certifica como falsación.* Un
fingerprint que **atesta MATCH afirmativamente sobre ejes que no puede ver** no se abstiene: produce
luz verde falsa. Es DEFENSIBLE-by-design SOLO mientras este caveat enumere TODOS los ejes ciegos (los
tres de arriba). **Residual aceptado y documentado:** hasta que esos ejes entren al digest, el operador
NO debe disparar una hipótesis cuyo lock sea anterior a cualquier cambio de código/datos/tier-routing
sin re-claim manual. El `_DIGEST_VERSION` NO "cierra" el residual: convierte un eje ciego en un re-claim
forzado cuando entra al digest — sigue dependiendo de la disciplina "no dispares una hipótesis vieja
tras un cambio". Aceptable y honesto, siempre que el spec lo diga sin overclaim.

## 7. Non-Negotiables / seguridad

- **Guardrail-crítico (holdout seal/trigger):** modificar `_FROZEN_FIELDS` + el trigger toca el corazón
  de inmutabilidad del holdout gate → **auditoría adversarial antes de push obligatoria**
  ([[adversarial-audit-before-push-pattern]]): el seal debe cubrir el fingerprint, el trigger bloquear
  su mutación, el fire-guard rechazar mismatch — todo verificado por tests hostiles.
- **NN#3 intacto:** no se toca `open_holdout`, ni la ventana, ni los frames. Es provenance de metadata.

## 8. Testing

- **selection_provenance:** digest determinista; cambiar UN ingrediente (p.ej. el calibration hash)
  cambia el fingerprint; bumpear `_DIGEST_VERSION` lo cambia; componentes incluyen las 3 coordenadas
  sembradas.
- **trials:** `claim_trial` auto-stampa (cost_model + fingerprint no-NULL); `selection_population_stats`
  filtra por fingerprint (mundos distintos no se mezclan); backfill v2 idempotente; NULL-fingerprint
  excluido del pooling.
- **hypotheses:** `claim_hypothesis` auto-stampa; el seal cubre `selection_fingerprint` (cambiarlo
  cambia el seal); el trigger bloquea su UPDATE tras lock (`sqlite3.IntegrityError`); `lock_hypothesis`
  rechaza si el fingerprint congelado != activo al lockear (drift); `assert_fireable` rechaza fire on
  mismatch (holdout intacto); deflación 4b pools por el fingerprint de la hipótesis.
- **migración:** ADD COLUMN idempotente; trigger recreado incluye el fingerprint; backfill solo toca
  `draft` en hypotheses.
- Todos targeted; NO correr la suite completa local (cuelga ~47min en Windows).

## 9. Scope OUT

- `strategy_code_sha` y `ohlcv_version` como ingredientes del digest = fast-follow (el digest se
  construye para aceptarlos; capturarlos limpiamente es trabajo aparte). Hasta entonces, §6 caveat.
- No se re-deriva ningún artefacto v2 existente bajo v3 (re-derivar es trabajo del operador cuando lo
  necesite).
- No se despliega v3 al server (no-evento, decisión separada).
- El estimador empírico de costo (epic separado) sigue fuera.
