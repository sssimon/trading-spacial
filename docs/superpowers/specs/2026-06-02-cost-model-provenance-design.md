# Cost-model-version provenance for evaluation ledgers — diseño

**Fecha:** 2026-06-02
**Estado:** diseño aprobado, listo para plan de implementación.
**Origen:** post-merge de cost-model v3 (#554, `4c6f1ec`). Voronov reencuadró la pregunta
"¿desplegar v3?" → el cost-model es un **SELECTOR**, no un dato: v3 (~30-40× más liviano que v2)
admite parámetros que v2 vetaba. Artefactos de evaluación producidos bajo cost-models distintos
**no son comparables.**
**Archivos gobernados:** `db/hypotheses.py` es el holdout falsification gate (#553) — guardrail
crítico, bala única irreversible. `db/trials.py` es el trial registry (#278). Ambos production-governing.

## 0. El problema (verificado)

Ni `db/trials.py` ni `db/hypotheses.py` registran bajo qué cost-model se produjo un artefacto.
Consecuencias **no guardadas hoy**:

1. **N de deflación contaminada.** `selection_population_stats` agrupa TODOS los trials
   exploratorios en una sola población, mezclando v2 y v3 — dos selectores distintos como si fueran
   el mismo experimento. La penalización best-of-N (López de Prado) se computa sobre una población
   incoherente.
2. **Re-selección disfrazada de falsificación.** Una hipótesis del holdout congelada bajo v2 y
   disparada bajo v3 *no fue falsificada — fue re-seleccionada bajo otro criterio de admisibilidad.*
   La bala del holdout es de un solo uso e irreversible: ese error no se deshace.

**Regla que necesitamos (Voronov):** todo artefacto de evaluación debe llevar la versión del
cost-model bajo la que se produjo; dos artefactos de cost-models distintos no son comparables sin
re-derivar el viejo. La provenance debe poder *pronunciar la frase "esto se midió bajo v2".*

## Decisiones tomadas

| Decisión | Valor |
|---|---|
| Unidad de provenance | **Ambos**: `active_model` (string legible) + `calibration_hash` (sha256 del selector). Pooling/guard comparan por **hash** |
| Guard de disparo del holdout | **Rechazo HARD** on mismatch; `cost_model_hash` se vuelve campo FROZEN (seal + trigger) |
| Origen del stamp | **Auto-stamp** en el módulo db vía `active_cost_model_id()` (cierra el footgun de raíz) |

## 1. Identidad del cost-model (`backtest_costs.py`)

Dos funciones nuevas:

```python
def calibration_identity_hash(cal: Calibration) -> str:
    """sha256 del SELECTOR — los números que deciden qué params admite la cota,
    NO la prosa. Cubre: version, active_model, global block, y por tier los
    campos de TierParams (floor + impact_tail / v2 base+size_factor). Excluye
    sources/sensitivity_note/model description (reword no cambia el selector)."""

def active_cost_model_id() -> tuple[str, str]:
    """(active_model, calibration_hash) del cost-model ACTIVO. Memoizado por
    proceso (la calibración activa no cambia mid-run)."""
```

El hash se computa sobre el objeto `Calibration` cargado (robusto a whitespace del JSON), serializando
los campos load-bearing con `json.dumps(..., sort_keys=True, default=str)` (determinista; NaN de los
campos cross-version se serializa estable como `NaN`). Para v3 cubre por tier: `half_spread_bps`,
`fee_bps_per_side`, `funding_rate_bps_per_8h`, `stress_mult`, `sigma_daily_bps`; para v2:
`base_bps`, `size_factor`, `half_spread_bps`, `fee_bps_per_side`, `funding_rate_bps_per_8h`; más el
`global` block (Y, cap, fallback, v_daily) y `version`/`active_model`.

`db/trials.py` y `db/hypotheses.py` importan `backtest_costs` a nivel de módulo — sin ciclo
(`backtest_costs` no importa `db` ni `backtest`).

## 2. `trials` (#278) — provenance + pooling homogéneo

- **Schema:** `ADD COLUMN cost_model TEXT, cost_model_hash TEXT`. Nuevas DBs los obtienen en el
  CREATE TABLE; DBs existentes vía `ALTER TABLE ADD COLUMN` idempotente (guarded por `PRAGMA
  table_info`).
- **`claim_trial`:** auto-stampa ambos vía `active_cost_model_id()`. Cero carga al caller.
- **`selection_population_stats(*, study_type='exploratory', cost_model_hash: str | None = None)`:**
  cuando `cost_model_hash` se provee, filtra `WHERE cost_model_hash = ?` — la N de deflación agrupa
  **solo trials del mismo selector**. (None = comportamiento legacy de pooling total; el holdout
  SIEMPRE pasa el hash.) Trials con `cost_model_hash` NULL no matchean ningún filtro → excluidos
  (un trial de cost-model desconocido no debe entrar a ninguna población conocida).
- **Backfill:** filas existentes (todas pre-v3 = producidas bajo v2) → `cost_model='v2'`,
  `cost_model_hash = calibration_identity_hash(load_calibration(path='costs_calibration.v2.json'))`.
  Solo donde `cost_model IS NULL` (idempotente).

## 3. `hypotheses` (#553, holdout gate) — guardrail-crítico

- **Schema:** `ADD COLUMN cost_model TEXT, cost_model_hash TEXT`. `claim_hypothesis` auto-stampa.
- **Campos FROZEN:** `cost_model` y `cost_model_hash` se añaden a `_FROZEN_FIELDS` (el seal los cubre)
  y al trigger `hypotheses_frozen_after_lock` (inmutables tras lock). El trigger se **recrea**
  (`DROP TRIGGER IF EXISTS` + `CREATE`) para incluir los dos campos nuevos — `CREATE TRIGGER IF NOT
  EXISTS` solo no actualiza un trigger existente.
- **`lock_hypothesis`:**
  - (a) la deflación 4b pools por el `cost_model_hash` de la hipótesis: `_deflation_probability`
    pasa `cost_model_hash=row['cost_model_hash']` a `selection_population_stats`.
  - (b) **nuevo criterio (4f, cost-model consistency):** el `cost_model_hash` congelado debe ==
    `active_cost_model_id()[1]` *al lockear*. Si derivó entre claim y lock (p.ej. se desplegó v3
    entremedio), `HypothesisLockError` — re-claim bajo el cost-model activo.
- **`assert_fireable` (chequeo 6, nuevo):** `active_cost_model_id()[1] == row['cost_model_hash']` o
  `HoldoutFalsificationError`, holdout intacto. Una hipótesis congelada bajo un selector solo puede
  dispararse bajo el mismo selector.

**Invariante:** claim → lock → fire, toda la cadena bajo un solo cost-model, enforced en cada
transición. Corolario gratis: como hoy hay ~0 trials v3 registrados, una hipótesis v3 no puede
lockear (su población de deflación estaría vacía → `sigma_sr_trials None` → fail-closed YA existente
en `_deflation_probability`). El diseño **fuerza construir población v3 (correr sweeps v3) antes de
afirmar una hipótesis bajo v3** — sin código nuevo para ello.

## 4. Migración (idempotente)

`_ensure_trials_schema` y `_ensure_schema` (hypotheses) corren, tras el `CREATE TABLE IF NOT EXISTS`:
1. `ADD COLUMN` para `cost_model` + `cost_model_hash` si no existen (chequeo `PRAGMA table_info`).
2. Backfill de filas con `cost_model IS NULL` → `'v2'` + hash del sibling v2 (idempotente).
3. (hypotheses) `DROP TRIGGER IF EXISTS hypotheses_frozen_after_lock` + re-`CREATE` con los dos
   campos nuevos en la cláusula `WHEN`.

Todo idempotente: corre en cada arranque de proceso, no-op tras la primera vez.

## 5. Asunción documentada (auto-stamp)

El auto-stamp asume que el trial corrió bajo la **calibración activa** — cierto para las 4 sweeps
exploratorias (`auto_tune`, `grid_search_tf`, `optimize_new_tokens`, `regime_allocation_sweep`), que
usan `simulate_strategy` con el modelo activo. Si una sweep futura pasara `model='v2'` explícito
mientras `active_model='v3'`, el stamp sería incorrecto (mislabel). `recompute.py` está fijado al
sibling v2 pero **no registra trials** (es diagnóstico), así que no aplica. Riesgo bajo, documentado.

## 6. Non-Negotiables / seguridad

- **Guardrail-crítico (holdout seal/trigger):** modificar `_FROZEN_FIELDS` + el trigger toca el
  corazón de inmutabilidad del holdout gate. Requiere auditoría adversarial antes de push
  ([[adversarial-audit-before-push-pattern]]): el seal debe cubrir los campos nuevos, el trigger
  debe bloquear su mutación, y el fire-guard debe rechazar mismatch — todo verificado por tests
  hostiles.
- **NN#3 intacto:** no se toca el acceso al holdout (`open_holdout`), ni la ventana, ni los frames.
  Esto es provenance de metadata, no acceso a datos.
- **Compatibilidad / cambio de versión del seal:** añadir `cost_model_hash`/`cost_model` a
  `_FROZEN_FIELDS` cambia el payload de `_compute_seal`, así que el seal de cualquier hipótesis
  lockeada ANTES de la migración ya no coincidiría (su seal viejo se computó sin esos campos) →
  `assert_fireable` daría seal-mismatch y exigiría re-claim. **En prod esto es un no-evento: el
  holdout gate #553 se mergeó sin consumidores, no hay ninguna hipótesis lockeada.** El backfill de
  `hypotheses` por tanto solo toca filas en estado `draft` (mutables); deja intactas las `locked`+
  (no existen). `selection_population_stats(cost_model_hash=None)` preserva el pooling legacy para
  cualquier caller no-holdout. Ver §7.

## 7. Testing

- **trials:** `claim_trial` auto-stampa (active_model + hash no-NULL); `selection_population_stats`
  con hash filtra (v2 trials no entran a población v3 y viceversa); backfill setea v2+hash en filas
  NULL idempotentemente; trials NULL-hash excluidos del pooling.
- **hypotheses:** `claim_hypothesis` auto-stampa; el seal cubre `cost_model_hash` (cambiarlo cambia
  el seal); el trigger bloquea UPDATE de `cost_model_hash` tras lock (sqlite3.IntegrityError);
  `lock_hypothesis` rechaza si el hash congelado != activo al lockear (drift); `assert_fireable`
  rechaza fire on hash-mismatch (holdout intacto); deflación 4b pools por el hash de la hipótesis.
- **migración:** ADD COLUMN idempotente; trigger recreado incluye los campos nuevos; **caso
  hipótesis ya-lockeada pre-migración:** su seal viejo (sin cost_model_hash) — definir el
  comportamiento: o bien el backfill NO toca filas lockeadas (deja cost_model_hash NULL y el seal
  viejo válido, pero entonces fire fallaría el chequeo 6 al comparar NULL vs activo → re-lock
  requerido), o el seal se considera versionado. **Decisión:** el backfill de hypotheses solo toca
  filas en estado `draft` (mutables); filas ya `locked`+ NO se tocan (su seal es inmutable) y, si
  alguna existe, su fire bajo el nuevo chequeo 6 requerirá re-claim — aceptable porque en prod NO
  hay hipótesis lockeadas todavía (el holdout gate #553 se mergeó sin consumidores).
- Todos los tests targeted; NO correr la suite completa local (cuelga ~47min en Windows).

## 8. Scope OUT

- No se re-deriva ningún artefacto v2 existente bajo v3 (la regla es: no comparables sin
  re-derivación; re-derivar es trabajo del operador cuando lo necesite, no de esta PR).
- No se despliega v3 al server (no-evento, decisión separada de Samuel).
- El estimador empírico de costo (epic separado) sigue fuera.
