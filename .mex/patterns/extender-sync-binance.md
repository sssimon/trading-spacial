---
name: extender-sync-binance
description: Runbook para añadir un paso nuevo al sync read-only de Binance — arquitectura en capas: cliente delgado → clasificación pura → apply en tx corta → orquestación en sync_tenant.
triggers:
  - "sync_tenant"
  - "BinanceAccountClient"
  - "binance_sync"
  - "observed_orders"
  - "extender el sync"
  - "nuevo paso de sync"
last_updated: 2026-06-11
---

# Pattern: Extender el sync read-only de Binance con un paso nuevo

## Propósito

El sync de Binance (`sync_tenant` en `binance_sync.py`) sigue una arquitectura de capas estricta que separa la red, la lógica pura y las escrituras en DB. Cada paso nuevo debe respetar la regla de Halberg: **jamás red dentro de `transaction()`**. Este patrón describe cómo añadir un paso sin romper esa invariante, sin propagar fallos del paso nuevo al sync completo, y con observabilidad suficiente para depurar en producción.

La arquitectura de capas, de más delgada a más orquestada:

```
BinanceAccountClient      → solo el request HTTP, lista cruda, sin lógica
clasificación pura         → función pura en binance_sync.py, testeable sin red
apply_*                    → recibe con del caller, tx corta, sin I/O
sync_tenant                → FASE 1 (red fuera de tx) → FASE 2 (writes en tx)
init_db                    → migración idempotente si hay tabla nueva
```

## Pasos

### Paso 1 — Método nuevo en `BinanceAccountClient`

Añadir un método que haga exactamente un request HTTP y devuelva la lista cruda. Cero lógica de clasificación, cero side-effects:

```python
# binance/client.py
def get_open_orders(self, symbol: str | None = None) -> list[dict]:
    """Devuelve la lista cruda de open orders de la cuenta. Sin filtrado."""
    params: dict = {}
    if symbol:
        params["symbol"] = symbol
    return self._signed_get("/api/v3/openOrders", params)
```

El cliente no interpreta los campos; eso es responsabilidad de la función de clasificación.

### Paso 2 — Función pura de clasificación en `binance_sync.py`

Una función con firma `(raw: list[dict]) -> list[Algo]` que no toca red, no toca DB. Testeable con fixtures estáticas:

```python
# binance_sync.py
def classify_open_orders(raw: list[dict]) -> list[ObservedOrder]:
    """Clasifica la lista cruda de openOrders en ObservedOrder. Pura."""
    result = []
    for item in raw:
        result.append(ObservedOrder(
            order_id=str(item["orderId"]),
            symbol=item["symbol"],
            side=item["side"],
            price=float(item["price"]) if item.get("price") else None,
            qty=float(item["origQty"]),
        ))
    return result
```

Los tests de esta función no necesitan `@pytest.mark.network` ni mocks de HTTP.

### Paso 3 — `apply_*` que recibe `con` del caller

Una función que recibe `con: sqlite3.Connection` del caller (nunca la abre ella misma) y realiza las escrituras. TX corta, sin I/O:

```python
# binance_sync.py
def apply_observed_orders(
    con: sqlite3.Connection,
    tenant_id: int,
    orders: list[ObservedOrder],
) -> int:
    """
    Sobrescribe observed_orders para el tenant. Devuelve número de filas escritas.
    Recibe con del caller — no abre transacción propia.
    """
    con.execute(
        "DELETE FROM observed_orders WHERE tenant_id = ?", (tenant_id,)
    )
    for o in orders:
        con.execute(
            "INSERT INTO observed_orders (tenant_id, order_id, symbol, side, price, qty) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tenant_id, o.order_id, o.symbol, o.side, o.price, o.qty),
        )
    return len(orders)
```

### Paso 4 — Integración en `sync_tenant`: FASE 1 fuera de tx, FASE 2 dentro

```python
# binance_sync.py
def sync_tenant(tenant_id: int, client: BinanceAccountClient) -> SyncResult:
    # ─── FASE 1: red — FUERA de cualquier transaction() ───────────────────────
    # Cada paso nuevo tiene su propio try/except anidado.
    # Un fallo de red en el paso nuevo → paso omitido, sync continúa.
    raw_orders: list[dict] = []
    try:
        raw_orders = client.get_open_orders()
    except BinanceTransportError as exc:
        log.warning("OBSERVED_ORDERS_SKIPPED tenant=%s error=%s", tenant_id, exc)

    # ... otros pasos de FASE 1 (balances, trades, etc.) ...

    # ─── FASE 2: clasificación pura ────────────────────────────────────────────
    orders = classify_open_orders(raw_orders)  # vacía si FASE 1 falló

    # ─── FASE 3: writes — DENTRO de transaction() ─────────────────────────────
    with transaction() as con:
        # ... apply de los pasos anteriores ...
        apply_observed_orders(con, tenant_id, orders)
```

El fallo del paso nuevo **no tumba el sync** ni degrada el estado de la credencial. La credencial permanece `ACTIVE`; el campo `last_sync_at` sí se actualiza (el sync completó su alcance habitual).

### Paso 5 — Migración idempotente si hay tabla nueva

Añadir una sub-función en `db/schema.py` y llamarla dentro del bloque `with transaction() as con_bc:` de `init_db`:

```python
# db/schema.py
def _migrate_observed_orders(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS observed_orders (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id  INTEGER NOT NULL,
            order_id   TEXT    NOT NULL,
            symbol     TEXT    NOT NULL,
            side       TEXT    NOT NULL,
            price      REAL,
            qty        REAL    NOT NULL,
            synced_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            UNIQUE (tenant_id, order_id)
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_observed_orders_tenant "
        "ON observed_orders (tenant_id)"
    )
```

Llamar desde `init_db`:

```python
with transaction() as con_bc:
    # ... otras migraciones del cluster ...
    _migrate_observed_orders(con_bc)
```

Todas las migraciones del cluster corren bajo UNA sola `transaction()` — fallo parcial hace rollback del grupo completo.

## Gotchas

- **Regla de Halberg — jamás red dentro de `transaction()`:** si un `client.get_*()` lanza una excepción de red, SQLite puede quedar bloqueado esperando el `COMMIT` de un `BEGIN IMMEDIATE` que nunca llega. El patrón FASE 1 / FASE 2 / FASE 3 es la única forma de cumplir esta invariante. Si la secuencia te tienta a mover la llamada HTTP dentro del `with transaction()`, para y reencuadra.

- **Eco F8 — parcial es incorrecto:** un fallo de red en FASE 1 debe resultar en un paso **completamente omitido** — jamás en un snapshot parcial ni en una limpieza de datos previos sin reemplazo. Si `get_open_orders()` falla, `raw_orders` queda vacío y `apply_observed_orders` no borra nada. El estado anterior en DB se preserva intacto hasta el próximo sync exitoso.

- **El fallo del paso nuevo NO tumba el sync NI degrada la credencial:** el `try/except` anidado en FASE 1 captura el error del paso nuevo, emite un `log.warning` estructurado, y deja que `sync_tenant` continúe con sus demás pasos. La credencial permanece `ACTIVE`.

- **Log estructurado con prefijo MAYÚSCULAS:** los mensajes de skip siguen el patrón `OBSERVED_ORDERS_SKIPPED tenant=%s error=%s`. Los tests que verifican el comportamiento de fallo de red usan `caplog` para asegurar que el prefijo aparece:

  ```python
  def test_observed_orders_skipped_on_transport_error(caplog):
      client = make_client_that_raises(BinanceTransportError("timeout"))
      with caplog.at_level(logging.WARNING):
          result = sync_tenant(tenant_id=1, client=client)
      assert "OBSERVED_ORDERS_SKIPPED" in caplog.text
      assert result.credential_status == "ACTIVE"
  ```

- **`apply_*` nunca abre su propia `transaction()`:** recibe `con` del caller. Si abriera su propia transacción, el caller no podría componerla con otros `apply_*` en una sola unidad de trabajo atómica.

- **Punto ciego cross-quote en `apply_observed_orders`:** el match entre órdenes y filas de posición es por símbolo exacto. Un SL colocado bajo otra quote (p.ej. BTCUSDC) NO se refleja en la fila BTCUSDT. Hoy `autocreate` nombra las filas `asset+USDT`, por lo que el comportamiento es consistente; pero si un usuario opera manualmente bajo una quote alternativa, ese SL/TP queda invisible para el summary.

- **Migración idempotente = `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`:** sin `DROP`, sin datos borrados en la migración. Si la tabla ya existe (deploy subsiguiente), la migración es un no-op.

## Verify Checklist

Antes de mergear cualquier PR que añada un paso nuevo al sync:

- [ ] El método nuevo en `BinanceAccountClient` hace exactamente un request y devuelve lista cruda — cero lógica de clasificación.
- [ ] La función de clasificación es pura: sin red, sin DB, sin side-effects. Sus tests no necesitan `@pytest.mark.network`.
- [ ] `apply_*` recibe `con` como primer argumento; no llama `transaction()` internamente.
- [ ] En `sync_tenant`, la llamada de red está en FASE 1 (fuera de `transaction()`), dentro de su propio `try/except` anidado.
- [ ] Un fallo de red en FASE 1 resulta en `log.warning("OBSERVED_ORDERS_SKIPPED ...")` y el paso completamente omitido — estado previo en DB preservado.
- [ ] Test de fallo de red verifica: snapshot previo intacto + credencial `ACTIVE` + `caplog` contiene el prefijo `OBSERVED_ORDERS_SKIPPED`.
- [ ] Migración (si aplica) usa `CREATE TABLE IF NOT EXISTS` dentro del bloque `with transaction() as con_bc:` de `init_db`.
- [ ] Dry-run de rollback verificado: interrumpir el proceso durante FASE 3 deja la DB en estado anterior coherente.
