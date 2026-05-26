---
name: conventions
description: How to access the database (3-layer separation) and how invariants are enforced (4 capas — schema / tipo / test / convención). Load before touching db/, operators/, auth/, or any new business transition.
triggers:
  - "transaction"
  - "operator"
  - "precheck"
  - "snapshot"
  - "invariant"
  - "schema"
  - "PositionClosure"
  - "BirthRegistrar"
  - "PRAGMA query_only"
  - "Voronov"
edges:
  - target: context/architecture.md
    condition: when a database access pattern depends on understanding which component is calling
  - target: context/decisions.md
    condition: when looking up the issue number / spec that closed a given invariant row
  - target: patterns/INDEX.md
    condition: when applying these conventions to a concrete task (closing a position, adding a precheck, etc.)
last_updated: 2026-05-26
---

# Conventions

## Database access

Three-layer separation:

### 1. Pure SQL helpers (`db/*.py`, `auth/*.py`, etc.)

Receive `con: sqlite3.Connection` as a mandatory first argument. They run SQL and return data. No `transaction()` calls. No side-effects (no HTTP, no file I/O, no logging beyond DEBUG). Examples: `db_close_position_sql`, `db_get_capital`, `apply_pnl_to_capital`, `db_create_position`.

**Documented exceptions** (Cat. 2 hidden business operators living in helper directories — operator-extraction deferred to separate tickets per the rationale in `docs/superpowers/analysis/2026-05-25-446-preconditions-synthesis.md`):

- `db/schema.py::init_db` — bootstrap orchestrator; opens its own `transaction()` and calls migration helpers.
- `db/signals.py::save_scan` — dual-transaction pattern (scan write + outcomes write); calls `transaction()` directly twice.
- `auth/audit.py::log_auth_event` — fallback to stderr if DB write fails; calls `transaction()` directly.
- `notifier/dispatch_per_user.py::dispatch_signal_to_users` — fan-out orchestrator; calls `transaction()` directly and fires `notify()` side-effect.

These four are recognized exceptions today. When their operator-extraction lands, they migrate to `operators/` and this list shrinks.

### 2. Business operators (`operators/*.py`)

Own `transaction()` for one named business transition. Orchestrate side-effects. Declare atomicity. The only legal entry point for the transitions they represent. Currently: `PositionClosure` (closing a position with atomic capital roll-in + post-commit health/notify/event-log/snapshot).

Pattern:

```python
from operators.position_closure import PositionClosure

with PositionClosure(
    pos_id=42, exit_price=110.0, exit_reason="TP_HIT",
    mode="USER", caller_tenant_id=tenant_id,
) as closure:
    outcome = closure.execute()
```

See [[../patterns/closing-a-position.md]] for the runbook.

### 3. Direct `with transaction()` for ad-hoc unit-of-work

When the caller needs a transactional scope around one or more pure SQL helpers but the operation isn't a named business transition, wrap the helpers in `with transaction() as con:` directly:

```python
from db.transaction import transaction
from db.signals import get_latest_signal

with transaction() as con:
    sig = get_latest_signal(con, "BTCUSDT")
```

### 4a. Precheck reads that feed a write transaction (`precheck_connection()`)

When an operator needs to read state BEFORE deciding whether to open a write transaction (e.g., ownership check, idempotency check), use `precheck_connection()` from `db.transaction`. The contract requires the caller to extract any field the write-tx will need into an **immutable snapshot value** (see `operators.precheck.PositionSnapshot`) BEFORE the block exits — the connection MUST NOT escape.

```python
from db.transaction import precheck_connection
from operators.precheck import PositionSnapshot

with precheck_connection() as con:
    row = db_get_position_by_id(con, pos_id)
snapshot = PositionSnapshot(pos_id=row["id"], tenant_id=row["tenant_id"], ...)
# Later: open transaction() and re-validate snapshot's mutable fields.
```

The write-tx that follows MUST re-validate the snapshot's mutable fields (e.g., `tenant_id`, `status`) against a fresh re-SELECT inside `BEGIN IMMEDIATE`. Immutable fields (e.g., `entry_price`, `qty`) are trusted from the snapshot directly. See `operators/position_closure.py` for the canonical implementation.

### 4b. Terminal reads (`snapshot_connection()`)

When a read is **terminal** — its result is serialized to an output (JSON file, HTTP response, log) and NOT used to drive a subsequent mutation — use `snapshot_connection()`:

```python
from db.transaction import snapshot_connection

with snapshot_connection() as con:
    all_pos = db_get_positions(con)
```

No follow-up write-tx, no re-validation obligation. Used today by `update_positions_json` (snapshot to JSON file).

See [[../patterns/precheck-vs-snapshot.md]] for when to pick which.

### Threat model (applies to both 4a and 4b)

Both helpers set `PRAGMA query_only = 1` on the connection. INSERT/UPDATE/DELETE raise `sqlite3.OperationalError`. **This is a cooperative latch, not a sandbox:** callers can re-enable writes via `PRAGMA query_only = 0`, `executescript` with embedded PRAGMA, or writes to `temp.*` tables. SQLite does not provide an ontologically read-only connection.

The mechanism is a **detector**, not a defense. Its value is converting bugs of "helper mistakenly mutates when contract says read-only" into LOUD errors at test time. The semantic invariant "this phase does not mutate the world" lives at the CALL SITE (extract → snapshot → terminate or write-tx), not in the primitive. Pure SQL helpers receive `con` from their caller; they never call `precheck_connection` or `snapshot_connection` themselves.

The two helpers share implementation but bear distinct call-site contracts. Mixing them (using `snapshot_connection` for a precheck that will feed a write-tx, or `precheck_connection` for a terminal read) is a documentation error that future contributors should reject in code review.

New business operators emerge from evidence (caller composes >1 helper + side-effect with conditional behavior), not preemptively. See `docs/superpowers/analysis/2026-05-25-446-tx-or-use-analysis-and-direction.md` for the rationale (Voronov, 2026-05-25).

### Known scope gap

`F-05` (trading invariant "every mutation derived from one tick of price decision belongs to one serializable transaction") **applies per-close** in Phase 2 of `check_position_stops`, **not per-tick**. The Phase 2 loop wraps each `PositionClosure(SYSTEM)` in `try/except: continue`, so partial-failure observability across N positions in the same tick is currently absent. See #453 for the issue tracking the integrity-observational debt (Voronov reframe of Serrano F-NEW Plano 1, 2026-05-25).

---

## Capas de enforcement de invariantes (Voronov 2026-05-26)

El dominio del repo afirma invariantes que el almacenamiento no garantiza por defecto. Cada vez que esa asimetría no se nombra, el código paga la diferencia en **membranas silenciosas**: `or 0`, "código de revisor", re-validaciones parciales. Este registro lista las invariantes de dominio que tocan el cluster C2 (#467/#468/#469) y la capa que las enforza.

Cuatro capas posibles, de más fuerte a más débil:

| Capa | Cómo enforza | Quién detecta violación |
|---|---|---|
| **Schema** | DDL constraint (CHECK, NOT NULL, FK, UNIQUE) | El motor SQLite, en write |
| **Tipo** | Anotación + **órgano de rechazo en runtime** (`__post_init__` con `isinstance`, factory privada con sentinel, NewType propagado al consumer). En un lenguaje sin type-checker en CI, la anotación sola es convención disfrazada de sintaxis. La rung 'tipo' sólo es real cuando el constructor o el factory rechaza la entrada equivocada con `TypeError`. | mypy estricto en CI **o** runtime check explícito (`__post_init__` / factory sentinel) |
| **Test** | Invariant test que falla si la violación ocurre | pytest en CI |
| **Convención** | Comentario en código / sección de CLAUDE.md / revisión humana | Revisor (si recuerda mirar) |

### Regla de coherencia (Voronov post-Serrano 2026-05-26)

> "La fuerza de una garantía está acotada por encima por el órgano más débil que puede rechazarla en la frontera que la garantía dice proteger."

Tres consecuencias para esta codebase:
1. Las anotaciones forward-ref en dataclasses no son enforcement. Si una clase declara un field con un tipo específico, debe tener `__post_init__` que rechace lo contrario, o el field debe construirse vía factory privada con sentinel. Sin órgano de rechazo, la anotación pertenece a la rung 'convención', no a 'tipo'.
2. `NewType` solo cuenta como 'tipo' si el consumer también está anotado y el camino completo es estructuralmente coherente. Una `PrecheckConn` definida y luego pasada a una función con anotación `sqlite3.Connection` regresa a 'convención'.
3. Cerrar un issue (`#NNN`) contra una eliminación parcial de la patología deja la enfermedad en los sitios no tocados. Closure requiere que el predicado del issue sea verdad en todos los call sites, no sólo los listados en el plan.

### Esquema de filas — una invariante, una fila (sub-task A de #488, decision 2026-05-26)

Cada fila describe **una invariante** con **una closure decision**. Si una clase, función, o componente del sistema enforza múltiples invariantes distintas, **cada una recibe su propia fila**. La fila se refiere al PREDICADO, no al artifact.

Razones (Voronov):
- La invariante es la unidad de verificación. Predicate-by-predicate rows permiten que un test, schema constraint, o reviewer audit each row independently.
- Conflated rows ("Tipo + runtime check + convención" para un solo artifact) overdeterminan la columna Capa y impiden audit por rung.
- La columna "Issue cerrado" no puede mentir si una fila = una invariante = una closure decision.
- Voronov sobre PR #496: *"the registry should record three rows, not one. Each row carries its own predicate."*

Patrón anti-fila: agrupar predicados distintos bajo el mismo artifact porque "comparten código." Cuando notes que la columna Mecanismo creció a un párrafo, eso significa que la fila está conflated y necesita split.

### Pattern: provenance markers vs safety claims (Voronov 2026-05-26 4th meta-review, #477)

Un tipo cuyo constructor está acotado por convención (single-underscore factory + sentinel importable) puede cargar una **provenance claim**, NO una **safety claim** — **iff** el safety está enforced downstream por un órgano independiente del tipo. La safety claim vive en ese órgano downstream, no en este tipo.

**El diagnostic (Voronov 2026-05-26 5th meta-review B2 — load-bearing, no opcional):**

> *"Can an attacker who imports the sentinel still be caught downstream by an independent organ?"*

- **Si la respuesta es SÍ** → el tipo es un provenance-marker; el sentinel es para legibilidad, no para safety; el rename + registry split de Path 6 aplica.
- **Si la respuesta es NO** → el tipo NO es un provenance-marker. Es algo más — un factory-as-safety-organ (ver pattern de abajo), un half-built lock, u otra cosa. NO aplica este pattern.

El diagnostic es load-bearing porque sin él, los aesthetic signs (sentinel + single-underscore + un downstream que parece chequear) pueden hacer match con tipos que estructuralmente NO son provenance-markers. Voronov: *"Visual similarity is not structural identity."*

**Aesthetic signs (descriptivos, no diagnósticos — usar SOLO después de pasar el diagnostic de arriba):**

- El tipo tiene un `__post_init__` que rechaza construcción "incorrecta" (e.g., `_sentinel is not _MODULE_SENTINEL`).
- El sentinel y el factory son single-underscore-prefix (importables — Python no enforza la barrera).
- El consumer del tipo (downstream) hace su propio chequeo de los fields antes de actuar.

**Cuál es la verdad estructural:**

- El check del sentinel NO es load-bearing para safety. Es load-bearing para LEGIBILIDAD: hace que el type signature lea como un contrato ("este snapshot vino del precheck factory") sin que el reader tenga que walk el código.
- El check de safety vive en el consumer (e.g., `PositionClosure.execute()` re-SELECT + field-by-field comparison inside BEGIN IMMEDIATE).
- Un attacker que importe el sentinel y fabrique el tipo se cuela del provenance check, pero se cae en la re-validation downstream **— este es el sí del diagnostic**.

**Cómo registrar esto en este file:**

- Una row para el **provenance predicate** (rung **convención** — el órgano más débil al frontier es la single-underscore convention).
- Una row separada para el **safety predicate** (rung **runtime check** — la re-validación field-by-field at the downstream frontier).
- NO fusionar ambos en "Tipo + runtime check para un solo predicado." Son **DOS predicados a DOS frontiers**.

**Cómo nombrar el tipo:**

- El nombre debe describir la provenance, no la safety. `OwnershipValidatedSnapshot` overclaims; `PrecheckOriginatedSnapshot` honest naming.
- El error message del sentinel-rejection debe nombrar: (a) el factory, (b) el rung convención explícitamente, (c) "provenance" como el semantic real, (d) dónde vive el safety organ downstream.

**Instancias actuales del pattern (sólo las que pasaron el diagnostic):**

- `PrecheckOriginatedSnapshot` (`operators/precheck.py`) — provenance: "snapshot came from precheck factory." Safety: `PositionClosure.execute()` field-by-field re-derivation against fresh re-SELECT inside `BEGIN IMMEDIATE`. **Diagnostic: SÍ** — un attacker que importe `_ORIGINATION_SENTINEL` fabricando un snapshot con `tenant_id` forjado se cae en la re-derivation downstream (el downstream NO trusts el tipo; lo re-checkea contra DB).

Voronov cita load-bearing (4th meta-review): *"The issue is asking which lock to install on a door that opens into a corridor where every visitor is searched. The search is the security. The lock is theatre. The honest move is to stop calling it a lock."*

### Pattern: factory-as-safety-organ (sister pattern, Voronov 2026-05-26 5th meta-review C1)

Distinct del pattern de arriba. Aquí el factory **es** el safety organ — la validation happens INSIDE el factory, no en un downstream re-derivation. El tipo's `__post_init__` sentinel check sigue siendo provenance-only (verifica que el caller pasó por el factory), pero esa provenance es load-bearing porque solo pasando por el factory el caller obtuvo safety.

**El diagnostic discriminatorio (mismo question, respuesta opuesta):**

> *"Can an attacker who imports the sentinel still be caught downstream by an independent organ?"*

- **Si la respuesta es NO** → factory-as-safety-organ. El downstream consumer trusts el tipo y NO re-deriva las claims que el factory enforzó. Bypass del factory = bypass de safety. El sentinel check del tipo es la única barrera entre el caller y la safety claim.

**Cuál es la verdad estructural:**

- El factory contiene los actual safety checks (Pydantic shape + range + type checks).
- El downstream consumer trusts el resultado del factory para esos campos. Puede enforzar OTROS invariantes (idempotency, uniqueness) — pero NO re-deriva los del factory.
- Si un attacker bypassea el factory, esos safety checks NO se rerun. El sentinel check es load-bearing.

**Diferencias clave vs el provenance-marker pattern:**

| | Provenance-marker | Factory-as-safety-organ |
|---|---|---|
| ¿El factory hace safety checks? | NO (solo marca origen) | SÍ (Pydantic + ranges + types) |
| ¿El downstream re-deriva esos checks? | SÍ (el safety organ vive allí) | NO (trusts el tipo) |
| ¿Bypass del factory = bypass de safety? | NO (downstream catches) | SÍ (sentinel es la única barrera) |
| Rung del provenance check | Convención (acceptable — safety está downstream) | Convención (problema — safety NO está downstream) |
| ¿Path 6 (honest acceptance) aplica? | SÍ | **NO** — el sentinel ES load-bearing aquí |

**Tratamiento honesto de este pattern:**

- **NO** aceptar la convention rung como "good enough" — el sentinel es la safety barrier, y rung convención significa que un import bypassea la safety.
- Considerar paths estructurales reales (closure pattern para el sentinel, name-mangling, frame inspection). El issue #477's 5 paths aplican aquí, no al provenance-marker pattern.
- En este codebase: `ValidatedOpenRequest` (`api/positions_birth.py`) — su `_build_open_request` corre Pydantic validation, rechaza `tenant_id` no-int / ≤ 0 / bool / None, raises `BodyValidationError` y `StaleEntryTsError`. El `BirthRegistrar.register` consume el tipo trusting esos checks (re-validates idempotency + uniqueness, que son SEPARATE invariantes). Bypass del factory = los Pydantic checks no corren = silent safety failure.

**Status para `ValidatedOpenRequest`:** open structural follow-up. Voronov 5th meta-review C1 surfaced this — el current state aún funciona porque ningún caller actual bypassea el factory, pero el rung at the safety frontier es convención single-underscore (el patrón que el provenance-marker pattern fue capaz de aceptar honest porque safety vivía downstream — aquí no vive downstream). Sub-task: decide between (a) closure pattern para encerrar el sentinel + factory, (b) name-mangling, (c) accept con escalation a un downstream check independiente, o (d) accept con documentation explícita del bypass risk + monitoring. **Out of scope of this PR; tracked separately.**

### Naming-mapping nota (Voronov 2026-05-26 5th meta-review C2)

Para futuro reader que busca el nombre viejo en el repo:

- `OwnershipValidatedSnapshot` (pre-#477) ↔ `PrecheckOriginatedSnapshot` (post-#477)
- `_build_validated_snapshot` (pre-#477) ↔ `_build_originated_snapshot` (post-#477)
- `_VALIDATION_SENTINEL` (pre-#477) ↔ `_ORIGINATION_SENTINEL` (post-#477)
- `tests/operators/test_ownership_validated_snapshot.py` (pre-#477) ↔ `tests/operators/test_precheck_originated_snapshot.py` (post-#477)

Los archivos en `docs/superpowers/plans/2026-05-26-*.md` retienen los nombres viejos por diseño — son event records que documentan la closure de #469+F6 bajo el state of understanding pre-#477 reframe. Rewriting them sería retroactive falsification del trajectory.

### Invariantes registradas — estado tras Cluster D (post-#471 #470 #473, post-convergencia Serrano/Aurelius)

> Esta tabla **reemplaza** la antigua tabla C2 (que listaba sólo #467/#468/#469). Las tres filas C2 están retenidas aquí; añade las siete filas de Cluster D. Una sola tabla de verdad — la duplicación adjacente previa era deuda doc nombrada por Serrano MEDIUM 10.

> **Nota:** la fila previamente conflated para #469+F6 (entonces nombrada "OwnershipValidatedSnapshot", renombrada a `PrecheckOriginatedSnapshot` por #477 cierre via Path 6) fue split en 4 filas separadas:
>
> - Las primeras 3 filas (sub-task A de #488, 2026-05-26 AM) — una por predicado, ya que el row original mezclaba tres predicados distintos en una sola fila.
> - La 4ta fila apareció después del Voronov 2026-05-26 4th meta-review (#477): el row "tipo + runtime check" que combinaba sentinel-check + field-by-field-re-validation era él mismo conflated. Son DOS predicados a DOS frontiers distintos: provenance (rung convención, frontera del precheck-to-execute hand-off) y re-validation (rung runtime check, frontera del write-tx BEGIN IMMEDIATE). Voronov: *"The sentinel guards provenance. The provenance claim does not gate safety, because safety is enforced downstream by re-validation. The sentinel exists to make the type signature readable as a contract, not to enforce the contract."*

| Invariante de dominio | Capa enforced | Mecanismo | Issue cerrado |
|---|---|---|---|
| `qty` siempre tiene valor numérico para positions activas (o `status='legacy_unmeasurable'`) | **Schema** | `CHECK (qty IS NOT NULL OR status='legacy_unmeasurable')` en `positions` (vía `_migrate_qty_not_null`) | #467 |
| `precheck_connection` y `snapshot_connection` son contratos distintos | **Tipo** | `NewType("PrecheckConn", sqlite3.Connection)` y `NewType("SnapshotConn", sqlite3.Connection)` en `db/transaction.py` — mypy detecta mis-uso | #468 |
| El snapshot consumido por `PositionClosure.execute()` provino del precheck factory (provenance marker, NO ownership-safety) | **Convención** (dos surfaces, ambas single-underscore) | `PrecheckOriginatedSnapshot.__post_init__` rechaza `_sentinel is not _ORIGINATION_SENTINEL`. El sentinel pattern es real — pero (a) `_build_originated_snapshot` (factory) y (b) `_ORIGINATION_SENTINEL` son ambos importables por nombre (Python no enforza single-underscore como barrier). El rung es convención, no tipo. La aceptación honesta es el cierre estructural — el type carries provenance, no safety. Voronov 2026-05-26 4th meta-review: *"The sentinel is not load-bearing. The field-by-field re-validation is. The sentinel is a provenance marker masquerading as a safety check — until renamed."* | #477 (closed via Path 6: honest acceptance + rename + registry split) |
| Los campos mutables del snapshot no cambiaron entre precheck y BEGIN IMMEDIATE (ownership-safety frontier) | **Runtime check** | Field-by-field re-validation en `PositionClosure.execute()` compara CADA campo del snapshot contra una fresh re-SELECT inside BEGIN IMMEDIATE. Esta es la frontera donde la ownership-safety guarantee se enforza. Independiente del provenance check de arriba: un caller que fabrique un `PrecheckOriginatedSnapshot` con un `tenant_id` forjado se cuela del provenance check pero se cae aquí. | #469 + F6 |
| El mensaje de error de `PrecheckOriginatedSnapshot.__post_init__` nombra lo que enforza (provenance) y lo que NO enforza (safety), apuntando al órgano downstream | **Test** | `test_error_message_does_not_overclaim_enforcement` en `tests/operators/test_precheck_originated_snapshot.py` — anchored sobre 5 claims: (1) reference al factory `_build_originated_snapshot`, (2) ausencia de "callable only", (3) acknowledgement explícito del rung convención, (4) nombrar "provenance" como el semantic real, (5) apuntar a `execute()` / re-validation como dónde vive el safety organ. | #481 closed by PR #486, extended by #477 closure |
| `qty > 0` para positions activas (cierra el 0.0-bypass) | **Schema** | `CHECK ((qty IS NOT NULL AND qty > 0) OR status='legacy_unmeasurable')` (via `_migrate_qty_positive`) | #471 |
| `tenant_id IS NOT NULL` para positions activas | **Schema** | `CHECK (tenant_id IS NOT NULL OR status IN ('legacy_unmeasurable','legacy_no_tenant'))` (via `_migrate_tenant_id_not_null`) | #471 |
| `tenant_id: int > 0` en la frontera de entrada (anotación + rechazo runtime) | **Tipo + runtime órgano de rechazo** | `_build_open_request` rechaza `tenant_id` no-int, ≤ 0, bool, o None con `BodyValidationError` (regla de coherencia post-Serrano) | #471 F6 |
| Idempotencia estructural: no dos open rows con el mismo `(tenant_id, scan_id)` | **Schema** | `CREATE UNIQUE INDEX idx_positions_open_scan_unique ... WHERE status='open' AND scan_id IS NOT NULL` (via `_migrate_unique_open_scan`) | #470 |
| Probe + INSERT + cache write atómicos por request (no TOCTOU race entre Idempotency-Key probe y row INSERT) | **Operador-ligero** | `BirthRegistrar.register` corre todo bajo UNA `with transaction()` (BEGIN IMMEDIATE) — colapsa los rungs por el reframe de Aurelius | #470 (race), #473 |
| Idempotencia HTTP con body-fingerprint (misma key + diferente body → 409, no replay) | **Tipo (HTTP) + Schema** | tabla `idempotency_keys` con columna `body_sha256` (SHA-256 del canonical-JSON post-Pydantic); `BirthRegistrar` levanta `DuplicateIdempotencyKeyError` si el fingerprint no matchea | #473 |
| Input externo → `Position` legítima (allowlist symbol, direction enum, qty>0, SL/TP relacional, entry_ts window) | **Tipo + runtime órgano de rechazo** | Pydantic `OpenPositionRequest` (extra='forbid') + factory privada `_build_open_request` con `_OPEN_REQUEST_SENTINEL` en `api/positions_birth.py` | #471 F5/F6/F7/F9, #473 |
| Error taxonomy 422/409/503 vs 500 — traducción al layer originante, no por substring de prosa | **Tipo** | `BirthError` hierarchy (`BodyValidationError`, `AmbiguousQtyError`, `StaleEntryTsError`, `TenantViolationError`, `DuplicateIdempotencyKeyError`, `IdempotencyCacheUnavailableError`, `UniqueViolationError`, `SchemaIntegrityError`); `BirthRegistrar._translate_integrity_error` mapea por `sqlite_errorcode` + fragmento del CHECK (no por substring de prosa inglesa). `IdempotencyCacheUnavailableError` (503) cierra el silent-duplicate window cuando el cliente pidió `Idempotency-Key` y el cache está unreachable (Serrano HIGH 2 post-convergencia) | #473 |
| Post-commit atomicidad + observabilidad de `update_positions_json` | **Operador-ligero + log estructurado** | `BirthRegistrar.register` posee la tx; si el snapshot post-commit falla, emite `log.error("POSITION_SNAPSHOT_STALE pos_id=... tenant=... snapshot_error=...")` (no swallows silencioso — Serrano HIGH 5) | #473 F8 |
| Observabilidad + fail-closed del cache de idempotencia | **Log estructurado + Tipo** | `IdempotencyCache.get/.set` emiten `log.error("IDEMPOTENCY_CACHE_UNREACHABLE ...")` cuando la tabla falla, y levantan `_CacheUnavailable`. `BirthRegistrar` lo traduce a `IdempotencyCacheUnavailableError` (503) **sólo cuando** el request portaba `Idempotency-Key` — un request sin key bypassa el cache y nunca ve la excepción. Cierra el silent-duplicate window (cache no-op + INSERT commit + retry → dos rows bajo la misma key) | Serrano MEDIUM 11 + HIGH 2 |
| Cluster D migrations atómicas como grupo | **Schema** | Las cuatro sub-migraciones (`_migrate_qty_positive`, `_migrate_tenant_id_not_null`, `_migrate_unique_open_scan`, `_migrate_idempotency_keys`) corren bajo UNA `with transaction()` en `init_db` — partial failure roll-back-ea el cluster entero | Serrano HIGH 7 |

### Principio dual de la frontera Cluster D (Voronov 2026-05-26)

> Una `Position` existe si y solo si su acto de nominación satisfizo simultáneamente: (a) el contrato existencial del schema (qué la convierte ontológicamente en Position), y (b) el contrato de nominación de la frontera de entrada (qué valida que el input externo intentaba declararla legítimamente). Schema es la frontera que ningún caller evade; nominación es donde el error toma forma semántica.

> `close()` valida una transición entre dos estados conocidos del mismo objeto. `open()` no valida transición — valida un acto de nominación. Son primos, no hermanos. Cluster D NO introduce un `PositionOpen` operador simétrico a `PositionClosure` — eso sería "falsa simetría — imitación visual; no comparte contrato". `BirthRegistrar` es un op-ligero: validación ocurrió arriba (Pydantic + `_build_open_request`); el registrar solo posee la atomicidad transacción + post-commit.

### Documented status: `legacy_no_tenant`

Status especial usado por `_migrate_tenant_id_not_null` (#471) para reconocer rows históricas pre-multi-tenant cuya `tenant_id` no es recuperable. El schema CHECK exempta `legacy_unmeasurable` Y `legacy_no_tenant`. Rows ya marcadas `legacy_unmeasurable` (de la migración C2) NO se re-clasifican — el OR del CHECK las exempta directamente. Convierte 2018 mentiras silenciosas (tenant_id=NULL implícito) en reconocimientos explícitos.

### Patrón nombrado: "invariantes de dominio sin contraparte estructural"

Cada futuro issue de la familia `or X`, "código de revisor", "trust-and-document" debería compararse contra este registro. Si la invariante pertenece a una capa más fuerte que `convención`, moverla es la fix correcta.

### Finding meta — asimetría contractual create vs close (Voronov post-medición 2026-05-26)

Medición de `signals.db` reveló 670 de 2018 positions con `qty IS NULL` (33%), **ZERO backfillables** desde `size_usd/entry_price`. La asunción del plan original era que `qty NULL` era deuda de cierre (size_usd existió, se perdió). La realidad: deuda de nacimiento (size_usd nunca prometido).

> **El sistema tiene un `close()` que asume invariantes que `open()` nunca prometió.** `qty NULL` no es el problema — es el síntoma. La membrana de cierre asume un contrato que la membrana de apertura nunca firmó.

Implicación: hasta que `create_position` exija lo que `close_position` asume, todo CHECK en la salida es teatro defensivo. Issue separado: asimetría contractual create vs close (open issue antes del PR merge — Task 13.5 del plan 2026-05-26-467-468-469).

### Documented status: `legacy_unmeasurable`

Status especial usado por `_migrate_qty_not_null` (#467) para reconocer 670 rows históricas cuya `qty` nunca fue medida y no es derivable. El schema CHECK constraint exempta este status: `CHECK (qty IS NOT NULL OR status='legacy_unmeasurable')`. Convierte 670 mentiras silenciosas en 670 reconocimientos explícitos.

### Known scope gap (post-D, post-convergencia)

Las siguientes patologías están reconocidas y deferidas — no son parte del cierre estructural de #471/#470/#473 y no bloquean su merge. Cada una tiene/necesita issue separado.

- **Rate limiting (#473 F10) — `Advances #473`, no `closes`.** El endpoint `POST /positions` no tiene throttle. Un cliente legítimo con la `Idempotency-Key` correcta puede inundar el endpoint creando rows distintas (cada body único pasa). El sistema confía en autenticación + JWT para acotar abuso. F10 vive en issue follow-up separado (#482) para evitar overstatement (Serrano LOW 15). NOTA: el PR body del Cluster D #485 citó originalmente #483 para este follow-up — error de transcripción; el issue real de rate limiting es #482.
- **Direction enum sólo en boundary (#484)** — el schema acepta cualquier TEXT en `positions.direction`; el `Literal["LONG","SHORT"]` vive sólo en la frontera Pydantic. Una migración manual o cliente legacy podría escribir `"long"` en lowercase. Mover a `CHECK (direction IN ('LONG','SHORT'))` es follow-up trivial.
- **`scan_id` FK (#483)** — `scan_id` es nullable y referencia una tabla que no existe (no hay `scans` con esa semántica de signal_id). El UNIQUE parcial cierra la race condition (#470) pero NO la integridad referencial.
- **Idempotency cache eager sweeper (Serrano MEDIUM 4)** — lazy cleanup es per-key only; one-shot keys que nadie re-pregunta nunca leakean en la tabla. El índice `idx_idempotency_expires` ya está creado para soportar un sweeper futuro. Follow-up pendiente (sin issue formal aún — bajo impacto).
- **`entry_ts` window relajada (Serrano MEDIUM 9)** — `[now-7d, now+60s]` rechaza backfills legítimos y clientes con skew >60s. Requiere decisión UX antes de relajar. Sin issue formal aún.
- **`legacy_no_tenant` consumer filters (Serrano MEDIUM 12)** — el status nuevo está en el schema; ningún consumer del UI / agente filtra rows con ese status explícitamente. Hoy es teórico (rows con `legacy_no_tenant` no son `open`, y las queries más activas filtran por status). Audit de cada consumer pendiente.

## Verify Checklist

Before merging any change touching `db/`, `operators/`, `auth/`, `api/`, or schema migrations:

- [ ] Pure SQL helpers receive `con` as first arg, no `transaction()` call inside, no side-effects beyond DEBUG logging — OR the helper is on the four-item documented exceptions list above.
- [ ] A new business transition lives in `operators/` (not in a helper) when it composes >1 helper + a side-effect with conditional behavior.
- [ ] Precheck reads use `precheck_connection()` and exit the block carrying only an immutable snapshot value (not the connection). Write-tx re-validates mutable snapshot fields inside `BEGIN IMMEDIATE`.
- [ ] Terminal reads (snapshot → JSON / HTTP / log, no follow-up write) use `snapshot_connection()`.
- [ ] If a new invariant of dominio is added, it is registered in the "Invariantes registradas" table above with **layer** (schema / tipo / test / convención) and **mechanism**. If it lives below the layer it should, open an issue.
- [ ] Type-layer claims have a runtime órgano de rechazo (`__post_init__`, factory sentinel, or `NewType` propagated to a typed consumer). Anotación sola = convención.
