# El Instrumento — Fase 3a (el lazo vivo, pull-only) · Diseño

**Fecha:** 2026-06-13
**Rama:** `feat/instrumento-fase3a-lazo-vivo`
**Parte de:** el instrumento completo (`docs/superpowers/specs/es/2026-06-12-instrumento-lifecycle-conducta-design.md`), Fase 3 de §3/§9. F3b (tarjeta de selección A+C+D.1) es un subsistema aparte, después.

## §0 — Qué es F3a, y el invariante que lo gobierna

F3a es el **acompañante en vivo**: sigue una posición REAL del operador contra el plan que confirmó, y al cierre mide su conducta `i` con el libro de fills vivo (la comparación plan-vs-conducta que F2 difirió honestamente). Es el tercer y último refutador de la falsación progresiva (F1 envelope → F2 simulación → **F3 fills vivos**).

**El invariante (revelado por Axiom-0 sobre el deadlock push/pull):**

> El instrumento solo tiene unidades mientras permanezca **FUERA del término que mide**. Medir `i` exige que `i` nazca en el operador.

De ahí la decisión central de F3a: el instrumento **emite hechos observables, NUNCA instrucciones de conducta**. Mostrar "TP1 se llenó" es espejo (refleja `𝓕ₜ`). Decir "mové el SL a BE" es autoría (el instrumento se vuelve co-fuente del acto `i` y mediría su propio eco — gemelo no escrito de INV-1). El instrumento jamás dice qué hacer.

**Canal (decidido por el operador, honrando un lock):** **pull-only.** El hecho vive en una vista consultable; **cero push**. Esto honra el candado LOCKED CD-3/CD-5 de `docs/superpowers/specs/es/2026-06-09-posiciones-externas-control-domain-spec.md` §5 ("flag PULL en la vista, NO push Telegram" para posiciones EXTERNAL) sin tocarlo. Beneficio estructural: sin canal de push, no hay trampa de atención ni pendiente hacia un botón "cerrar" que violaría CD-1/CD-5. El operador mira cuando ÉL decide; la ley D.1 que confirmó en frío vive en la vista, no en una alarma.

## §1 — Frontera dura

- **Read-only sobre `positions`.** F3a nunca escribe `positions.status`, nunca llama `PositionClosure`. El cierre lo confirma el humano (CD-5).
- **CD-1 respetado:** las posiciones de Binance son EXTERNAL; F3a las observa, jamás las actúa.
- **Sin push, sin instrucción.** Solo hechos, vía pull.
- **Escribe solo a:** la tabla nueva `lifecycle_states` (estado vivo) y, al cierre, a `conduct_episodes` (el ledger de F1).
- **BNC-12:** opera sobre posiciones `origin IN ('SIGNAL','OPERATOR')` (AUTO_DERIVED nunca es conducta).

## §2 — Arquitectura

El estado vivo del plan por fin tiene domicilio (lo que F1 difirió, lo que Halberg reclamó).

| Pieza | Archivo | Responsabilidad |
|---|---|---|
| Domicilio | `db/lifecycle_states.py` + migración | Una fila por plan activo: plan + `LifecycleState` incremental + último snapshot observado. |
| Gate (preview) | `api/plan.py` → `GET /plan/derive/{symbol}` | Deriva el plan desde D.1 + entry y lo devuelve para revisión — NO persiste. |
| Gate (confirmar) | `api/plan.py` → `POST /plan/confirm` | El operador confirma el plan revisado → crea la fila `lifecycle_states`. |
| Detector | `instrument/tracker.py` → `detect_transitions(...)` | **Puro.** Snapshot observado previo vs actual + qty → eventos. Idempotente, honesto sobre ambigüedad. |
| Tracker | `instrument/tracker.py` → `advance_live(...)` + el hook de I/O | Corre **después** de `binance_sync`: lee posición EXTERNAL + `observed_orders`, detecta, avanza la máquina (`step`), persiste. **Cero alertas.** |
| Vista | `api/plan.py` → `GET /plan/{symbol}` | Pull: estado vivo (plan vs realidad + conducta hasta ahora). |
| Conducta al cierre | el tracker, al llegar a CLOSED | `compute_conduct` con los fills vivos → `conduct_episodes`. |

## §3 — Domicilio: tabla `lifecycle_states`

Una fila por plan activo del operador. Idempotente (CREATE TABLE IF NOT EXISTS), mismo estilo que `conduct_episodes`/`observed_orders`.

```sql
CREATE TABLE IF NOT EXISTS lifecycle_states (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id         INTEGER,
    symbol              TEXT    NOT NULL,
    tenant_id           INTEGER NOT NULL,
    estado_vivo         TEXT    NOT NULL CHECK (estado_vivo IN ('activo','cerrado','incierto')),
    plan_json           TEXT    NOT NULL,   -- el Plan confirmado (la ley)
    entry_price         REAL    NOT NULL,
    qty_original        REAL,               -- qty al confirmar (base del % restante)
    fase                TEXT    NOT NULL,    -- PLANNED|CONFIRMED|RUNNING|CLOSED
    rungs_llenos_json   TEXT    NOT NULL,    -- [0,1,...]
    consumed_orders_json TEXT   NOT NULL,    -- [order_id,...] (idempotencia)
    sl_actual           REAL,
    be_movido           INTEGER NOT NULL,    -- 0/1
    size_restante_frac  REAL,
    prev_observed_json  TEXT,               -- último snapshot de observed_orders (para el delta)
    prev_qty            REAL,
    confirmed_at        TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,
    UNIQUE (tenant_id, symbol, confirmed_at)
);
```

Helpers SQL puros (reciben `con`): `db_put_lifecycle_state`, `db_get_active_state(con, tenant_id, symbol)`, `db_list_active(con, tenant_id)`, `db_update_lifecycle_state`. La serialización `LifecycleState ↔ fila` vive en helpers dedicados (frozensets ↔ JSON listas).

## §4 — El gate (`POST /plan/confirm`)

1. El operador (UI/CLI) pide derivar: el endpoint reconstruye las zonas de D.1 (`detect_levels` sobre velas diarias hasta ahora), `derive_plan(zonas, entry_price)`, y devuelve el plan **para revisión** (no lo persiste todavía).
2. El operador **confirma** (su juicio + gate de fundamentales entran aquí, en frío): `POST /plan/confirm {symbol, entry_price, position_id?}` → crea la fila `lifecycle_states` con `estado_vivo='activo'`, `fase='CONFIRMED'`, `sl_actual=plan.sl_price`, `qty_original` de la posición. La red (D.1) corre **fuera** de la tx; el insert va en una tx corta.
3. No per-tenant cross-talk: la fila lleva `tenant_id` del caller (validado como en el resto de la API).

## §5 — El detector de transiciones (`detect_transitions`, puro)

`detect_transitions(plan, state, prev_observed, curr_observed, prev_qty, curr_qty) -> list[dict]`. Puro: sin red, sin DB. `*_observed` = listas de `{kind, price, qty, order_id}` (forma de `observed_orders`). Devuelve eventos para la máquina de F1.

`observed_orders` es un snapshot de órdenes ABIERTAS (DELETE+reinsert); un TP que desaparece pudo **llenarse** o **cancelarse** — el snapshot solo no distingue. Las reglas, honestas sobre su resolución:

1. **RUNG_FILLED:** un `order_id` TP presente en `prev_observed`, **ausente** en `curr_observed`, que matchea por proximidad de precio (`_close` 0.5%) a un rung NO en `state.rungs_llenos`, **Y** `curr_qty < prev_qty` (la posición bajó) → `RUNG_FILLED(rung_index, order_id)`. Idempotente: si `order_id ∈ consumed_orders`, no-op.
2. **Cancelación (no fill):** TP `order_id` que desaparece **sin** caída de qty → cancelación; **no** se emite evento.
3. **SL_MOVED:** el SL observado cuyo `price` cambió de `prev` a `curr` a ≈ `entry_price` → `SL_MOVED(entry)`. (Si cambió a otro precio: también `SL_MOVED` con el nuevo SL — el campo de conducta `sl_respetado` lo juzgará al cierre.)
4. **STOP_HIT / cierre:** `curr_qty ≈ 0` (con credencial ACTIVE) → `STOP_HIT` si el último SL observado matchea el cierre, o señal de cierre a confirmar (CD-5). El tracker marca `estado_vivo='cerrado'`; el sistema NO escribe `positions.status='closed'`.
5. **Ambigüedad irresoluble** (no se puede matchear el delta a una transición conocida) → **no se avanza**; el tracker marca `estado_vivo='incierto'` y lo expone en la vista. El detector **no inventa** (misma honestidad que el envelope de F1/F2). El operador puede reconciliar a mano; F3b/futuro puede leer el trade history de Binance para desambiguar fill-vs-cancel con certeza (DEFER).

## §6 — El tracker (I/O) y la vista (pull)

**Tracker** (`advance_live`): corre **después** de `binance_sync` en el mismo ciclo (consume su salida; no compite por escrituras a `positions` — solo escribe `lifecycle_states`). Para cada fila activa: lee la posición + `observed_orders` frescos, llama `detect_transitions`, aplica los eventos por `step` (F1), persiste el estado nuevo + `prev_observed`/`prev_qty`. Si llega a CLOSED → §7.

**Vista** (`GET /plan/{symbol}`, pull): devuelve el estado vivo, hechos contra el plan — sin instrucción:
```jsonc
{ "symbol": "BTCUSDT", "estado_vivo": "activo",
  "plan": { "entry": .., "sl_plan": .., "rungs": [..], "runner_frac": .. },
  "realidad": { "fase": "RUNNING", "rungs_llenos": [0], "sl_actual": .., "be_movido": true,
                "size_restante_frac": 0.5 },
  "hechos": ["TP1 se llenó", "tu SL está en break-even", "tu SL sigue debajo de la zona"] }
```
Los `hechos` son afirmaciones de `𝓕ₜ` (lo que ES verdad), **nunca** instrucciones. `estado_vivo:"incierto"` → un hecho honesto: "transición sin confirmar — revisá en Binance".

## §7 — Conducta al cierre (la comparación que F2 difirió)

Al llegar a CLOSED, ahora SÍ hay conducta **observada legítima** (los fills reales del libro vivo). El tracker corre `compute_conduct(plan, events, final_state, ...)` de F1 — **campo por campo** contra el plan confirmado: `entry_en_zona`, `sl_respetado` (¿ensanchaste el SL?, lo dice `observed_orders`), `adherencia_be` (¿moviste a BE tras TP1?), `rungs_honrados`, `escalono`, `cierre_en_plan`, `hold_hours` — con procedencia `observado`. Persiste a `conduct_episodes` (el ledger de F1). **NO una resta de PnL** (el veto de F2 sigue en pie): hechos de conducta, no rentabilidad.

## §8 — Pruebas

**Puras** (`tests/test_instrument_tracker.py`, sin red/DB):
- `detect_transitions`: TP desaparece + qty baja → `RUNG_FILLED` (idempotente por order_id); TP desaparece sin caída de qty → **sin** evento (cancelación); SL cambia a ≈entry → `SL_MOVED`/BE; qty→0 → cierre; delta no-matcheable → sin evento (incierto).
- serialización `LifecycleState ↔ fila` (round-trip de frozensets ↔ JSON).
- conducta al cierre: una secuencia de snapshots vivos → `compute_conduct` produce los campos esperados.

**DB** (`tests/test_lifecycle_states.py`): roundtrip de helpers, CHECK de `estado_vivo`, migración real vía `init_db()`, filtro por tenant.

**Endpoints** (`tests/test_plan_api.py`): `POST /plan/confirm` crea la fila (D.1 mockeada); `GET /plan/{symbol}` devuelve el estado; los `hechos` no contienen ningún imperativo (regex anti-instrucción: sin "mové/movete/cerrá/vendé/comprá").

**Tracker** (smoke `network`-marcado): un ciclo sobre datos reales.

## §9 — Invariantes preservados
- **El instrumento queda fuera del término que mide** (Axiom-0): solo hechos, nunca instrucciones. Test anti-imperativo en la vista.
- **Pull-only:** sin push; honra CD-3/CD-5. Sin canal de salida en caliente.
- **Frontera dura:** read-only sobre `positions`, sin `PositionClosure`, sin escribir `closed` (CD-5). Escribe solo `lifecycle_states` + `conduct_episodes`.
- **CD-1 / BNC-12:** EXTERNAL observado nunca actuado; conducta solo SIGNAL/OPERATOR.
- **Honestidad sobre la resolución:** ambigüedad del snapshot → `incierto`, no se inventa.
- **Sin PnL:** la conducta al cierre es campo-por-campo, no resta de retorno (veto F2 vigente).

## §10 — Fuera de alcance (defer / delete)
- **Push de cualquier tipo** — DELETE (decisión del operador + lock CD-3/CD-5).
- **Trade history de Binance para fill-vs-cancel con certeza** — DEFER (el snapshot-delta+qty es la resolución honesta de v1; el trade history desambigua los `incierto` en una iteración futura).
- **F3b — tarjeta de selección compuesta A+C+D.1** — subsistema aparte, su propio spec.
- **Follow-ups de pureza F1** (tolerancia float en BE detection, contrato de `events` en compute_conduct) — se atienden al cablear el tracker si tocan el camino vivo.
