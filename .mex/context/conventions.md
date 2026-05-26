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

### Invariantes registradas — estado tras Cluster D (post-#471 #470 #473, post-convergencia Serrano/Aurelius)

> Esta tabla **reemplaza** la antigua tabla C2 (que listaba sólo #467/#468/#469). Las tres filas C2 están retenidas aquí; añade las siete filas de Cluster D. Una sola tabla de verdad — la duplicación adjacente previa era deuda doc nombrada por Serrano MEDIUM 10.

| Invariante de dominio | Capa enforced | Mecanismo | Issue cerrado |
|---|---|---|---|
| `qty` siempre tiene valor numérico para positions activas (o `status='legacy_unmeasurable'`) | **Schema** | `CHECK (qty IS NOT NULL OR status='legacy_unmeasurable')` en `positions` (vía `_migrate_qty_not_null`) | #467 |
| `precheck_connection` y `snapshot_connection` son contratos distintos | **Tipo** | `NewType("PrecheckConn", sqlite3.Connection)` y `NewType("SnapshotConn", sqlite3.Connection)` en `db/transaction.py` — mypy detecta mis-uso | #468 |
| Los campos del snapshot consumidos por el write-tx no cambian entre precheck y BEGIN IMMEDIATE | **Tipo + runtime check + convención** | `OwnershipValidatedSnapshot.__post_init__` sentinel check (rung tipo — sentinel `is _VALIDATION_SENTINEL`) + field-by-field re-validation en `PositionClosure.execute()` (rung runtime check). La construcción está bounded por **dos superficies de convención** que comparten una invariante: el factory `_build_validated_snapshot` mantiene single-call-site por convención (single-underscore), y el sentinel `_VALIDATION_SENTINEL` es importable directamente (`from operators.precheck import _VALIDATION_SENTINEL`). Ambas surfaces componen el rung convención — #477 (widened post-Voronov 2026-05-26) tracks la decisión única: instalar organ que cierre ambas surfaces (closure pattern / name-mangling / frame inspection) o aceptar permanentemente. PR #486 aplica Path 3 (honest narrowing) en docstrings + error message; el organ structural sigue abierto. Test rung: `test_error_message_does_not_overclaim_enforcement` ancla la doc-honesty contract. | #469 + F6 (#481 closed by PR #486; #477 advanced — wider invariant captures both surfaces; meta-arch next-moves tracked in #488) |
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
