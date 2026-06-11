# Spec — Binance v0.3: SL/TP observados (órdenes abiertas spot → observabilidad)

**Fecha:** 2026-06-11 · **Estado:** APROBADO (diseño validado con Samuel en sesión de brainstorming).
**Tipo:** extensión de observabilidad (read-only) — captura de órdenes de protección abiertas (SL/TP/OCO) de la cuenta spot del papá y reflejo en las posiciones EXTERNAL.
**Frontera:** SOLO LECTURA (contrato v0.1 intacto). SPOT only. Futuros, Earn y cualquier actuación sobre órdenes quedan FUERA.
**Relacionado:** `2026-06-10-conexion-binance-solo-lectura-spec.md` (v0.1), `2026-06-10-binance-v02-autocreacion-observabilidad-spec.md` (v0.2).

---

## 0. Qué es / qué NO es

**Es:** capa de observabilidad que (a) lee TODAS las órdenes abiertas spot de la cuenta del tenant (`GET /api/v3/openOrders`, una llamada, read-only), (b) las clasifica en SL/TP con función pura, (c) las persiste como snapshot en la tabla nueva `observed_orders` (cada orden con su % de cobertura del holding), y (d) refleja un resumen en `sl_price`/`tp_price` de las filas EXTERNAL con semántica fuente-de-verdad (sobrescribe; sin orden → NULL).

**NO es:**
- NO coloca, modifica ni cancela órdenes. Cero métodos de escritura en el cliente.
- NO toca filas INTERNAL (su SL/TP pertenece al camino de control `check_position_stops`).
- NO lee futuros ni Earn.
- NO altera la lógica de la señal de riesgo §7 de v0.2 (el dato nuevo la alimenta visualmente; integración formal = follow-up).
- NO mantiene estado incremental: snapshot completo en cada sync (mismo principio que el ACB recomputado de v0.2).

## 1. Decisiones de Samuel (registradas en sesión 2026-06-11)

1. **Sobrescribir siempre.** Binance es la fuente de verdad: el sync pisa cualquier valor previo de `sl_price`/`tp_price` en filas EXTERNAL — incluidas las 2 filas OPERATOR tecleadas (BTC/ETH). Decisión explícita de Samuel, consciente de la lección F4/F5 de v0.2 (que protegía el entry tecleado; aquí el SL/TP observado ES el dato real).
2. **Sin orden → NULL.** Si un holding no tiene ninguna orden SL (o TP) abierta, el campo se limpia. El dashboard nunca muestra protección ficticia; "sin stop" es un hecho visible.
3. **Observar todo.** Con múltiples órdenes SL/TP por símbolo, cada una se muestra con su porcentaje de cobertura. De ahí la tabla `observed_orders` (una fila por orden), no un solo par de campos.

## 2. Cliente — `BinanceAccountClient.get_open_orders()`

En `data/providers/binance_account.py`:

- `GET /api/v3/openOrders` firmado (USER_DATA), **sin** parámetro `symbol` → todas las órdenes abiertas de la cuenta en una llamada (weight 80). Evita iterar por símbolo (más llamadas, snapshot no atómico).
- Mismo `_signed_get`, mismas excepciones tipadas (`BinanceAuthError`, `BinanceClockSkew`, `BinanceRateBanned`, `BinanceTransportError`), misma política de scrubbing (la firma jamás entra a `str(exc)` — BNC-2 §2.4 #4).
- Devuelve la lista cruda de Binance (campos relevantes: `symbol`, `orderId`, `orderListId`, `side`, `type`, `price`, `stopPrice`, `origQty`, `executedQty`). La clasificación NO vive en el cliente (cliente delgado, patrón `get_my_trades`).

## 3. Clasificación — `classify_open_orders` (función pura, `binance_sync.py`)

Entrada: lista cruda de órdenes + `{symbol: qty_viva_del_holding}`. Salida: lista de dicts `{symbol, kind, price, qty, pct_holding, order_id, oco_group}`.

| Orden Binance (solo `side='SELL'`) | `kind` | Precio guardado |
|---|---|---|
| `STOP_LOSS` / `STOP_LOSS_LIMIT` | `SL` | `stopPrice` |
| `TAKE_PROFIT` / `TAKE_PROFIT_LIMIT` | `TP` | `stopPrice` |
| `LIMIT_MAKER` (pata alta de OCO) | `TP` | `price` |
| `LIMIT` (venta simple) | `TP` | `price` |

- Órdenes `BUY` se IGNORAN (entradas pendientes, no protección).
- Patas OCO comparten `orderListId` → se persiste como `oco_group` para que el UI las muestre pareadas. `orderListId == -1` (orden suelta) → `oco_group = NULL`.
- `qty = origQty − executedQty` (lo que queda vivo de la orden).
- `pct_holding = qty ÷ qty_viva_del_holding`. Si el holding no tiene qty viva conocida → `pct_holding = NULL` (se abstiene, no inventa). Si la orden cubre más que el holding (staleness entre balance y órdenes) → se reporta el pct real >1.0 SIN clamp — es un hecho observado, no se maquilla.
- Símbolos con órdenes pero sin fila EXTERNAL correspondiente: se persisten igual en `observed_orders` (observabilidad completa de la cuenta); simplemente no hay resumen que escribir.

## 4. Tabla nueva — `observed_orders`

Migración en `db/schema.py`, patrón `idempotency_keys` (PRAGMA-guarded `CREATE TABLE IF NOT EXISTS` idempotente, dentro de la transacción de `init_db`):

```sql
CREATE TABLE observed_orders (
    id           INTEGER PRIMARY KEY,
    tenant_id    INTEGER NOT NULL,
    symbol       TEXT    NOT NULL,
    kind         TEXT    NOT NULL CHECK (kind IN ('SL','TP')),
    price        REAL    NOT NULL CHECK (price > 0),
    qty          REAL    NOT NULL CHECK (qty > 0),
    pct_holding  REAL,
    order_id     INTEGER NOT NULL,
    oco_group    INTEGER,
    observed_at  TEXT    NOT NULL,
    UNIQUE (tenant_id, order_id)
);
CREATE INDEX idx_observed_orders_tenant_symbol
    ON observed_orders(tenant_id, symbol);
```

Capa de enforcement (registro Voronov): dominio de `kind`, positividad de `price`/`qty` y unicidad por orden viven en **Schema** — el motor rechaza, no un comentario.

**Semántica snapshot:** en cada sync, `DELETE FROM observed_orders WHERE tenant_id=?` + reinserta lo observado, todo bajo UNA `with transaction()` junto con la actualización del resumen (§5). Nunca hay estado intermedio visible.

## 5. Sync — extensión de `tools/sync_binance_spot.py`

Paso nuevo tras el flujo existente (reconcile/autocreate):

1. `get_open_orders()` → `classify_open_orders()` contra los holdings vivos del tenant.
2. Bajo UNA `with transaction()`:
   a. Snapshot a `observed_orders` (delete + insert).
   b. **Resumen** en cada fila EXTERNAL open del tenant: `sl_price` = precio del SL de mayor `qty` observado para su símbolo; `tp_price` = ídem TP; sin órdenes de ese kind → `NULL`. Aplica a OPERATOR y AUTO_DERIVED por igual (decisión §1.1). Filas INTERNAL: intocables.
3. `--dry-run` imprime el plan (órdenes clasificadas + resúmenes que escribiría) sin persistir nada.
4. **Fallo = omisión completa.** Si `get_open_orders` falla (auth, rate-ban, transporte), el paso entero se omite ese ciclo: ni snapshot parcial, ni limpieza de campos por un fallo de red. Eco del principio F8 de v0.2: parcial = incorrecto, no incompleto. Se loguea la causa.

## 6. API + Frontend

- El endpoint de posiciones que consume el dashboard adjunta a cada posición EXTERNAL su lista `observed_orders: [{kind, price, qty, pct_holding, oco_group}]`, leída con `snapshot_connection()` (lectura terminal — patrón 4b; sin write-tx posterior).
- Frontend (UI en español — regla del proyecto): en la posición, cada orden listada como "SL 50.000 (25%)" / "TP 75.000 (25%)", pareadas cuando comparten `oco_group`. Holding sin ninguna fila SL → badge "sin stop".
- Sin cambios en `types.ts` más allá del campo nuevo opcional.

## 7. Errores y pruebas

- **Cliente** (`tests/test_binance_account_client.py`): mock de `_http_get` — request firmado correcto, mapeo de errores, scrubbing de firma en transporte.
- **Clasificación** (pura, sin red): OCO completo (2 patas → SL+TP con mismo `oco_group`), SL suelto, `LIMIT` venta → TP, `BUY` ignorado, multi-órdenes con porcentajes, orden > holding (pct >1 sin clamp), holding sin qty (`pct_holding = NULL`).
- **Snapshot**: idempotencia (dos syncs consecutivos = mismo estado final), limpieza a NULL cuando una orden desaparece, no-persistencia ante fallo de red (estado previo intacto), aislamiento per-tenant (snapshot del tenant 2 no toca filas del tenant 1).
- **Resumen**: fila INTERNAL jamás tocada; OPERATOR sobrescrita; sin orden → NULL.
- Sin cambios en `CANONICAL_POSITIONS_COLUMNS` (cero columnas nuevas en `positions`) → el test de canonicidad no se toca.

## 8. Fuera de alcance (declarado)

- Futuros y Earn (igual que v0.1/v0.2).
- Integración formal del dato en la señal de riesgo §7 de v0.2 (el badge "sin stop" del frontend es presentación; la señal mantiene su lógica actual) — follow-up.
- `trailing stops` de Binance (`TRAILING_DELTA`): si aparecen en la cuenta se clasifican por su `type` base (STOP_LOSS* → SL); el delta no se modela.
- Cadencia automática del sync: sigue siendo manual/periódica como v0.2.
