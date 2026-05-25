# Issue #446 — Análisis Clínico + Dirección Ontológica

**Branch:** `feat/fix-tx-or-use-dual-contract-446`
**Commit base:** `b5b05889` (merge de PR #445)
**Fecha:** 2026-05-25
**Autores:** Dr. Adrian V. Serrano (análisis) + Dr. Aurelius K. Voronov (dirección)

---

## Parte I — Análisis clínico (Serrano)

> Nota del editor: análisis devuelto íntegro por Serrano en handoff a Voronov. Estado: implementable hoy (código corre), pero contiene 4 BLOCKERs y 5 HIGHs.

### A) Resumen diagnóstico

`_tx_or_use(con)` (`db/transaction.py:60-83`) es síntoma sintáctico de helper con **dos contratos operacionales bajo un único símbolo**, instalado por Task 8.5 del plan transaction-unit-of-work y mergeado en PR #445. No es wrapper inocuo: es **dispatcher condicional** que el caller debe interrogar para razonar correctamente sobre atomicidad, side-effects, y ciclo de vida del lock. La evidencia material está en `db/positions.py:186` (`_caller_owned_con = con`), `db/positions.py:219` (`if _caller_owned_con is None:`), y `api/positions.py:290-294` (compensación post-commit que el caller debe saber ejecutar). El bug no es un defecto de implementación — el bug es **contractual**.

### B) Estado actual — mapa exhaustivo

**44 call sites en 16 archivos** (excluyendo definición y tests):

| Módulo | Llamadas | Notas |
|---|---|---|
| `db/positions.py` | 5 | Todas las funciones públicas exponen `con: Optional` |
| `db/capital.py` | 4 | `apply_pnl_to_capital` se llama recursivamente a sí misma con `inner_con` |
| `db/signals.py` | 5 | — |
| `db/schema.py` | 4 | Schema/migration helpers |
| `db/auth_schema.py` | 5 | — |
| `db/user_preferences.py` | 2 | — |
| `auth/tokens.py` | 5 | — |
| `auth/audit.py` | 1 | — |
| `notifier/_storage.py` | 4 | — |
| `notifier/dedupe.py` | 1 | — |
| `notifier/dispatch_per_user.py` | 1 | — |
| `health.py` | 3 | Imports locales (`from db.transaction import _tx_or_use` dentro de funciones) |
| `strategy/kill_switch_v2_shadow.py` | 2 | Idem (local imports) |

**Patrones donde el código interroga el contrato vigente:**

1. `db/positions.py:186-225` — `db_close_position` recuerda `_caller_owned_con = con` y suprime `trigger_health_evaluation` cuando el caller threadeó tx.
2. `api/positions.py:290-306` — `check_position_stops` ejecuta el side-effect compensatorio (`trigger_health_evaluation`) post-commit en bucle sobre `post_tx_actions`.
3. `health.py:588-592` — re-implementa la lógica de `_tx_or_use` inline en el mismo archivo que ya importa el wrapper en otros 3 sitios (drift entre abstracción y adopción).

### C) Espacio de soluciones admisibles (enumeración no exhaustiva)

**Opción A — Bifurcar firmas** (`*_standalone()` vs `*_within_tx(con, ...)`). Eliminar `_tx_or_use`. ~30-44 call sites a renombrar. Doble superficie API; deriva potencial.

**Opción B — Side-effects fuera del helper** (helpers como SQL puro; caller orquesta). Re-localiza `trigger_health_evaluation` en TODO caller. Riesgo: garantía implícita deja de vivir en helper.

**Opción C — Named operator** (`class PositionClosure` o equivalente). El operator es el único que abre tx, orquesta side-effects, decide compensación. Helpers SQL pierden `con`. Riesgo god-object / proliferación.

**Opción D — Mantener wrapper + prohibir side-effects en helpers**. Equivale a B aplicada selectivamente + enforcement (lint/test). Sin enforcement, regresión.

**Opción E — Revertir parcialmente Task 8.5**. Helpers NO aceptan `con`. Cross-helper atomicity vía SQL inline. Duplica SQL. Pierde reutilización de ownership/IDOR.

### D) Coupling con otros issues

| Issue | A | B | C | D | E |
|---|---|---|---|---|---|
| #447 (named operator) | No lo crea | No lo crea | **Es la opción** | No lo crea | No lo crea |
| #448 (con validation) | Persiste | Persiste | **Disuelto** | Persiste | **Disuelto** |
| #449 (health trigger) | Persiste | Persiste y distribuye | Nombrable, no auto-resuelto | Persiste | Persiste |
| #450 (capital best-effort) | Persiste | Persiste | Nombrable, no auto-resuelto | Persiste | Persiste |
| #451 (test atomicity) | Test ad-hoc | Test ad-hoc | **Test natural** | Test ad-hoc | Test ad-hoc |

### E) Lo que NINGUNA opción resuelve

1. Compensación de fallas en side-effects post-commit — no hay outbox/dead-letter.
2. `apply_pnl_to_capital` best-effort silencioso (issue #450) — política, no estructura sintáctica.
3. Asimetría de rollback bajo `con=<provisto>` — persiste salvo en C completa.
4. Re-implementación inline del wrapper en `health.py:588-592` — drift no auditado.
5. Ausencia de test de invariante completo cross-helper (issue #451) — solo C lo facilita estructuralmente.
6. Multi-tenancy y `con` — `tenant_id` se threadea ortogonal al `con`; validación pre-tx o intra-tx no definida.

### G) Hallazgos clínicos numerados

1. **[BLOCKER, CONTRA/AMB]** El símbolo `_tx_or_use` enmascara dos contratos operacionales incompatibles.
2. **[BLOCKER, STATE/OPS]** El cuerpo de `db_close_position` interroga su propio contrato vía `_caller_owned_con`.
3. **[BLOCKER, GAP/OPS]** No hay compensación garantizada para side-effects post-commit fallidos.
4. **[BLOCKER, GAP/OPS/SCOPE]** `apply_pnl_to_capital` se ejecuta best-effort sin contrato de compensación.
5. **[HIGH, CONTRA]** El patrón `if conn is None` se reimplementa inline en `health.py:588-592` en el mismo archivo que usa `_tx_or_use` 3 veces.
6. **[HIGH, STATE/OPS]** La asimetría de rollback no está documentada en la firma de los helpers.
7. **[HIGH, SEC]** El parámetro `con` no se valida.
8. **[HIGH, GAP]** No existe test que ejerza el invariante completo cross-helper.
9. **[HIGH, AMB/SCOPE]** Los helpers con `con: Optional` no documentan si son seguros de llamar desde threads concurrentes.
10. **[MEDIUM, OPS]** El docstring de `_tx_or_use` justifica su existencia pero no advierte sobre la divergencia semántica.
11. **[MEDIUM, SCOPE]** `_tx_or_use` está marcado privado (`_` prefix) pero se usa en 16 módulos cross-package.
12. **[MEDIUM, STATE]** `apply_pnl_to_capital` se llama a sí mismo con `con=inner_con`, anidando wrappers sin que sea evidente.
13. **[LOW, AMB]** El nombre `_tx_or_use` no comunica que el "use" tiene contrato distinto al "tx".

### H) Preguntas explícitas handoff a Voronov

1. ¿La unidad atómica correcta es "una operación SQL multi-tabla" o "una unidad de negocio completa incluyendo side-effects compensables"?
2. ¿Side-effects post-commit son parte del cierre o consumidores asíncronos de un evento de cierre?
3. ¿Es aceptable que el ledger de capital quede silenciosamente desincronizado en caso de falla?
4. Si named operator para cierre, ¿debe haber paralelos para apertura/edición/cancelación? Costo de proliferación.
5. ¿Validación de `con` (#448) es responsabilidad del wrapper, helper, o caller?
6. ¿"Helpers no hacen side-effects" debe ser arquitectural enforced o convencional?
7. ¿Test de atomicidad cross-helper (#451) antes o después de elegir opción?
8. ¿`_tx_or_use` debe sobrevivir bajo cualquier opción, o su existencia es síntoma terminal?
9. ¿Asimetría rollback bajo `con=<provisto>` es bug o feature documentable?
10. ¿`_tx_or_use` está mal abstraída o mal adoptada?

---

## Parte II — Dirección ontológica (Voronov)

### La constante invisible

`_tx_or_use` no es un wrapper. No es un dispatcher. Es un **artefacto de indecisión ontológica**: el sistema no ha decidido si los helpers de `db/` son **operaciones SQL** o **operaciones de negocio**. Mientras esa decisión no exista, cualquier opción elegida es local; cualquier refactor es cosmético.

Las cinco opciones de Serrano operan dentro del mismo error de categoría:

> **Tratan el problema como sintáctico (forma del helper) cuando es semántico (qué cosa es un helper).**

PR #445 no introdujo el bug. Reveló el bug que ya existía: **`db/` nunca fue una capa; era una colección.**

### La ley de orquestación

Toda función que persiste estado en un sistema transaccional pertenece a **exactamente una** de estas tres categorías ontológicas:

1. **Operador SQL puro** — recibe `con`, no lo abre, no lo cierra, no dispara side-effects, no compone lógica de negocio. Su unidad es la sentencia.
2. **Operador de negocio** — abre `con` (o lo recibe explícitamente con semántica documentada), orquesta side-effects, compone múltiples operadores SQL, posee la atomicidad. Su unidad es la transición de estado del dominio.
3. **Side-effect compensable** — no participa de la atomicidad SQL; vive después del commit; tiene su propia política de retry/dead-letter. Su unidad es el evento.

Una función no puede ser dos de estas a la vez. Si lo intenta, el caller debe interrogarla para razonar — exactamente lo que `_caller_owned_con` ya hace. Esa variable es la confesión del sistema: aquí hay dos contratos viviendo en un símbolo.

### La opción: C, con cualificaciones

C es la única opción que **nombra las tres categorías** y las separa estructuralmente. A, B, D, E reorganizan dentro de la confusión.

Pero C no es suficiente como Serrano la enuncia. La forma correcta de C es más estricta:

> **C no introduce operators. C introduce la distinción de capas. Los operators son la consecuencia, no la causa.**

La decisión no es "crear `PositionClosure`". La decisión es "**`db/` se vuelve operador SQL puro; los operadores de negocio viven en una capa nueva**". `PositionClosure` aparece porque el cierre es un operador de negocio. Aparece `PositionOpening` cuando la apertura lo requiera. No antes.

Esto disuelve el riesgo de proliferación: **no se crean operators preventivamente; se crean cuando un caller actualmente compone múltiples helpers + side-effects.**

### Forma del operador

**Context manager con estado.** No clase con métodos múltiples (eso invita god-object). No función pura (eso pierde el lifecycle). No dataclass (eso es datos sin comportamiento). El operador de cierre posee un lifecycle: pre-validación → transacción → side-effects post-commit → compensación.

```python
with PositionClosure(position_id, ...) as closure:
    closure.execute()
# post-commit side-effects fired in __exit__
```

El operador es el único lugar donde la atomicidad y los side-effects post-commit coexisten. El caller no orquesta; declara intención.

### Operators que emergen hoy

Hoy, dos:
- **Cierre de posición** (evidencia: `_caller_owned_con` + compensación post-commit en `check_position_stops`)
- **Apertura de posición** (evidencia: composición de capital + signal + posición en `api/positions.py`)

No crear preventivamente otros. Cada operador aparece cuando se identifique un caller actual que orqueste >1 helper + side-effect. **Regla de creación: evidencial, no especulativa.**

### Relación con `transaction()`

El operador **posee** `transaction()`. Es el único caller legítimo de `transaction()` para su unidad de negocio. Los helpers SQL puros (`db/`) reciben `con` y nunca lo abren. `transaction()` desaparece de la API pública de `db/`; solo operadores la invocan.

Esto resuelve #448 estructuralmente: la validación de `con` deja de existir porque los helpers ya no pueden recibirlo de un caller externo — solo del operador, que es confiable por construcción.

### Respuestas a las 10 preguntas

1. **Unidad atómica:** unidad de negocio. SQL multi-tabla es mecanismo, no unidad.
2. **Side-effects post-commit:** estructuralmente consumidores de evento; pragmáticamente hoy el operador los absorbe. La forma del operador (`__exit__` post-commit) debe ser compatible con migración futura a publicación de evento.
3. **Capital desincronizado:** no aceptable. Pero ortogonal a la elección de opción — es política de compensación (#450). C hace visible la pregunta; las otras la dejan dispersa.
4. **Operators paralelos:** evidencial, no especulativa. Hoy cierre + apertura. Resto cuando aparezca.
5. **Validación de `con`:** disuelta. Bajo C, los helpers no reciben `con` de callers externos.
6. **Enforcement:** arquitectural. El mecanismo es **tipo del parámetro `con`** — helpers SQL puros reciben `Connection` no-opcional; operadores no reciben `con`.
7. **Test de atomicidad:** después de C, antes de implementar C. Secuencia: elegir C → especificar contrato del operador → escribir test contra contrato → implementar → migrar callers.
8. **`_tx_or_use`:** terminal. Bajo C, los contratos se separan; el dispatcher condicional deja de tener trabajo.
9. **Asimetría rollback:** bug bajo helpers actuales; no aplicable bajo C.
10. **`_tx_or_use` abstraída vs adoptada:** mal concebida. La herramienta materializa una categoría que no debe existir.

### Lo que se queda sin respuesta

Tres asuntos requieren evidencia que Voronov no tiene:

**a. Costo real de migración de los 44 call sites.** La distribución sugiere que la mayoría de helpers (especialmente `auth/`, `notifier/`) son operadores SQL puros que solo necesitan firma cambiada. Pero `health.py`, `kill_switch_v2_*`, `dispatch_per_user.py` necesitan auditoría individual. Esta auditoría debe preceder al plan ejecutable.

**b. Si la apertura de posición necesita operator hoy o más tarde.** La decisión depende de si la composición actual exhibe los síntomas que motivan C (interrogación de contrato, side-effects condicionales). Si no, esperar.

**c. Invariantes de multi-tenancy que el operador debe garantizar.** Mapear dónde hoy se valida tenant y qué pasa si no se valida.

### Cierre

El debate entre las cinco opciones es real pero está mal enmarcado. Las cinco preguntan "¿cómo organizamos `_tx_or_use`?". La pregunta correcta es "¿qué es un helper en `db/`?".

C no es "la opción que crea `PositionClosure`". C es **la opción que decide que `db/` es una capa con un contrato único, y que las transiciones de negocio viven en otra capa con su propio contrato**. `PositionClosure` es el primer habitante de esa segunda capa. Otros emergerán cuando la evidencia lo exija.

> `_tx_or_use` no es un bug a corregir. Es la **huella fósil** de una capa que nunca se nombró. El refactor correcto no la elimina; **la hace innecesaria.**

---

## Pre-condiciones del plan ejecutable

Antes de escribir el plan implementable:

1. **Auditoría per-archivo** de los 44 call sites — clasificar cada helper como (1) operador SQL puro, (2) operador de negocio escondido como helper, o (3) caso límite que requiere juicio.
2. **Lectura end-to-end** de `api/positions.py` flujo de apertura — confirmar si `PositionOpening` debe crearse en este PR o se difiere.
3. **Mapeo de invariantes multi-tenancy** — dónde se valida `tenant_id` hoy y qué pasa si no se valida.
4. **Especificación del contrato del operador `PositionClosure`** — qué pre-validaciones, qué SQL helpers compone, qué side-effects post-commit ejecuta, qué política de compensación. Esto ancla el test #451.

Solo entonces se escribe el plan ejecutable que migra `db/` a capa SQL pura, introduce `PositionClosure` (y `PositionOpening` si la auditoría lo exige), y desinstala `_tx_or_use`.
