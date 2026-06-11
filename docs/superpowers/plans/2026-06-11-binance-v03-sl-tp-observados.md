# Binance v0.3 — SL/TP observados: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capturar las órdenes SL/TP abiertas de la cuenta spot Binance del tenant y reflejarlas en las posiciones EXTERNAL (snapshot completo + resumen fuente-de-verdad).

**Architecture:** Cliente read-only gana `get_open_orders()` (una llamada, sin symbol). Función pura `classify_open_orders` mapea órdenes crudas → SL/TP con % de cobertura. Tabla nueva `observed_orders` (snapshot delete+insert por tenant, CHECKs en schema). El sync existente (`sync_tenant`) orquesta: red en FASE 1 (fuera de tx — arquitectura de lock de Halberg), writes en FASE 2 (tx corta). API adjunta la lista por posición EXTERNAL; frontend la lista en español con badge "sin stop".

**Tech Stack:** Python 3.12 / FastAPI / SQLite (sin ORM), React 18 + TypeScript (Vite), pytest + vitest.

**Spec:** `docs/superpowers/specs/es/2026-06-11-binance-v03-sl-tp-observados-spec.md` — leerlo COMPLETO antes de empezar.

**Reglas del repo que aplican aquí (no negociables):**
- Helpers SQL puros reciben `con` como primer argumento; jamás abren `transaction()` ni llaman `precheck/snapshot_connection` ellos mismos (CLAUDE.md §Database access).
- I/O de red NUNCA dentro de una transacción (incidente de contención del login 2026-06-10; ver docstring de `sync_tenant`).
- Filas INTERNAL: intocables por este feature. El resumen solo escribe `control_domain='EXTERNAL'`.
- Tests rápidos: `python -m pytest tests/<archivo> -v` por tarea; el gate completo al final es `python -m pytest tests/ -m "not network" -n auto -q`.
- UI en español (regla del proyecto — papá Simón + María).

---

### Task 1: Cliente — `get_open_orders()`

**Files:**
- Modify: `data/providers/binance_account.py` (añadir método al final de `BinanceAccountClient`, tras `get_ticker_prices`)
- Test: `tests/test_binance_account_client.py` (añadir clase al final)

- [ ] **Step 1: Write the failing test**

Añadir al final de `tests/test_binance_account_client.py`, siguiendo el patrón de mock del archivo (mirar cómo las clases existentes mockean `data.providers.binance_account._http_get`; replicar el mismo estilo de fixture/monkeypatch del archivo):

```python
class TestGetOpenOrders:
    def test_devuelve_lista_cruda_y_firma_el_request(self, monkeypatch):
        captured = {}

        def fake_get(url, params=None, headers=None, timeout=10):
            captured["url"] = url
            captured["headers"] = headers
            resp = Mock()
            resp.status_code = 200
            resp.json.return_value = [
                {"symbol": "BTCUSDT", "orderId": 7, "orderListId": 33,
                 "side": "SELL", "type": "STOP_LOSS_LIMIT",
                 "price": "49000", "stopPrice": "50000",
                 "origQty": "0.5", "executedQty": "0"},
            ]
            return resp

        monkeypatch.setattr("data.providers.binance_account._http_get", fake_get)
        client = BinanceAccountClient(api_key="K", secret="S")
        orders = client.get_open_orders()

        assert orders[0]["orderId"] == 7
        assert "/api/v3/openOrders" in captured["url"]
        assert "signature=" in captured["url"]          # request firmado
        assert "symbol=" not in captured["url"]         # SIN symbol: toda la cuenta
        assert captured["headers"]["X-MBX-APIKEY"] == "K"

    def test_error_2015_levanta_auth_error(self, monkeypatch):
        def fake_get(url, params=None, headers=None, timeout=10):
            resp = Mock()
            resp.status_code = 400
            resp.json.return_value = {"code": -2015, "msg": "Invalid API-key"}
            return resp

        monkeypatch.setattr("data.providers.binance_account._http_get", fake_get)
        client = BinanceAccountClient(api_key="K", secret="S")
        with pytest.raises(BinanceAuthError):
            client.get_open_orders()
```

Si el archivo no importa ya `Mock`/`pytest`/`BinanceAuthError`, añadirlos a los imports existentes.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_binance_account_client.py::TestGetOpenOrders -v`
Expected: FAIL con `AttributeError: 'BinanceAccountClient' object has no attribute 'get_open_orders'`

- [ ] **Step 3: Write minimal implementation**

En `data/providers/binance_account.py`, añadir al final de la clase `BinanceAccountClient`:

```python
    def get_open_orders(self) -> list[dict]:
        """TODAS las órdenes abiertas spot de la cuenta (USER_DATA, read-only).

        SIN parámetro `symbol` (una sola llamada, weight 80): snapshot atómico
        de la cuenta — evita iterar por símbolo (más llamadas, snapshot no
        atómico). Devuelve la lista CRUDA de Binance; la clasificación SL/TP
        vive en binance_sync.classify_open_orders (cliente delgado, patrón
        get_my_trades). Spec v0.3 §2."""
        resp = _signed_get(self._api_key, self._secret, "/api/v3/openOrders",
                           {}, self._offset)
        _raise_for_error_code(resp)
        return resp.json()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_binance_account_client.py -v`
Expected: PASS (los nuevos y TODOS los previos del archivo)

- [ ] **Step 5: Commit**

```bash
git add data/providers/binance_account.py tests/test_binance_account_client.py
git commit -m "feat(binance): get_open_orders() read-only en BinanceAccountClient (v0.3 §2)"
```

---

### Task 2: Migración — tabla `observed_orders`

**Files:**
- Modify: `db/schema.py` (nueva función `_migrate_observed_orders` + wiring en `init_db`)
- Test: `tests/test_observed_orders.py` (crear)

- [ ] **Step 1: Write the failing test**

Crear `tests/test_observed_orders.py`. Mirar cómo los tests de schema existentes obtienen una DB fresca (buscar en `tests/` un test que llame `init_db` con `signals.db` redirigido a tmp_path — p. ej. `tests/test_binance_credentials.py` o `tests/test_binance_autocreate.py` — y replicar su fixture EXACTA):

```python
"""Tests de Binance v0.3 — SL/TP observados (spec 2026-06-11).

Cubre: migración observed_orders, clasificación pura, snapshot + resumen.
"""
import sqlite3

import pytest

# Replicar aquí la fixture de DB fresca del archivo de tests de binance
# existente (test_binance_autocreate.py usa la misma init_db + tmp_path).


class TestMigracionObservedOrders:
    def test_tabla_existe_con_checks(self, fresh_db_con):
        con = fresh_db_con
        # La tabla existe
        row = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='observed_orders'"
        ).fetchone()
        assert row is not None
        # CHECK kind: solo SL/TP
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO observed_orders "
                "(tenant_id, symbol, kind, price, qty, order_id, observed_at) "
                "VALUES (1, 'BTCUSDT', 'XX', 100, 1, 1, '2026-06-11T00:00:00')"
            )
        # CHECK price > 0
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO observed_orders "
                "(tenant_id, symbol, kind, price, qty, order_id, observed_at) "
                "VALUES (1, 'BTCUSDT', 'SL', 0, 1, 2, '2026-06-11T00:00:00')"
            )
        # UNIQUE (tenant_id, order_id)
        con.execute(
            "INSERT INTO observed_orders "
            "(tenant_id, symbol, kind, price, qty, order_id, observed_at) "
            "VALUES (1, 'BTCUSDT', 'SL', 50000, 0.5, 7, '2026-06-11T00:00:00')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO observed_orders "
                "(tenant_id, symbol, kind, price, qty, order_id, observed_at) "
                "VALUES (1, 'ETHUSDT', 'TP', 3000, 1, 7, '2026-06-11T00:00:00')"
            )

    def test_migracion_idempotente(self, fresh_db_paths):
        # Correr init_db dos veces no debe fallar (CREATE IF NOT EXISTS)
        from db.schema import init_db
        init_db()
        init_db()
```

(Los nombres `fresh_db_con` / `fresh_db_paths` son ilustrativos: usar los nombres reales de la fixture replicada.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_observed_orders.py::TestMigracionObservedOrders -v`
Expected: FAIL — la tabla `observed_orders` no existe.

- [ ] **Step 3: Write the migration**

En `db/schema.py`, añadir tras `_migrate_idempotency_keys` (final del archivo):

```python
def _migrate_observed_orders(con: sqlite3.Connection) -> None:
    """Tabla observed_orders — Binance v0.3 (SL/TP observados, spec 2026-06-11 §4).

    Snapshot de las órdenes de protección abiertas (SL/TP/OCO) observadas en
    la cuenta spot del tenant. Semántica fuente-de-verdad: cada sync hace
    DELETE WHERE tenant_id + reinserta (sin estado incremental, mismo
    principio que el ACB recomputado de v0.2). Capa de enforcement Schema:
    dominio de kind, positividad de price/qty y unicidad por orden los
    rechaza el motor, no un comentario.

    Idempotente: CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS.
    """
    con.execute(
        """CREATE TABLE IF NOT EXISTS observed_orders (
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
           )"""
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_observed_orders_tenant_symbol "
        "ON observed_orders(tenant_id, symbol)"
    )
    log.info("_migrate_observed_orders: observed_orders table + index ensured.")
```

Wiring en `init_db`: localizar el bloque `with transaction()` que llama `_migrate_binance_credentials` (grep `_migrate_binance_credentials(` dentro de `init_db`) y añadir la llamada en ese MISMO bloque, justo después:

```python
        _migrate_binance_credentials(con_X)   # línea existente
        _migrate_observed_orders(con_X)       # v0.3: tabla independiente, mismo eje binance
```

(`con_X` = el nombre de la variable de conexión de ese bloque; usar el real.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_observed_orders.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/schema.py tests/test_observed_orders.py
git commit -m "feat(db): tabla observed_orders con CHECKs — snapshot SL/TP observados (v0.3 §4)"
```

---

### Task 3: Clasificación — `classify_open_orders` (función pura)

**Files:**
- Modify: `binance_sync.py`
- Test: `tests/test_observed_orders.py` (añadir clase)

- [ ] **Step 1: Write the failing tests**

Añadir a `tests/test_observed_orders.py`:

```python
from binance_sync import classify_open_orders


def _orden(**kw):
    base = {"symbol": "BTCUSDT", "orderId": 1, "orderListId": -1,
            "side": "SELL", "type": "STOP_LOSS_LIMIT",
            "price": "49000", "stopPrice": "50000",
            "origQty": "0.5", "executedQty": "0"}
    base.update(kw)
    return base


class TestClassifyOpenOrders:
    def test_oco_completo_dos_patas_mismo_grupo(self):
        orders = [
            _orden(orderId=1, orderListId=33, type="STOP_LOSS_LIMIT",
                   stopPrice="50000", price="49900"),
            _orden(orderId=2, orderListId=33, type="LIMIT_MAKER",
                   price="75000", stopPrice="0"),
        ]
        out = classify_open_orders(orders, {"BTC": 2.0})
        assert len(out) == 2
        sl = next(o for o in out if o["kind"] == "SL")
        tp = next(o for o in out if o["kind"] == "TP")
        assert sl["price"] == 50000.0          # SL usa stopPrice
        assert tp["price"] == 75000.0          # LIMIT_MAKER usa price
        assert sl["oco_group"] == tp["oco_group"] == 33
        assert sl["pct_holding"] == pytest.approx(0.25)   # 0.5 de 2.0

    def test_take_profit_limit_usa_stop_price(self):
        out = classify_open_orders(
            [_orden(type="TAKE_PROFIT_LIMIT", stopPrice="80000", price="79900")],
            {"BTC": 1.0})
        assert out[0]["kind"] == "TP"
        assert out[0]["price"] == 80000.0

    def test_limit_venta_simple_es_tp(self):
        out = classify_open_orders(
            [_orden(type="LIMIT", price="70000")], {"BTC": 1.0})
        assert out[0]["kind"] == "TP"
        assert out[0]["price"] == 70000.0
        assert out[0]["oco_group"] is None     # orderListId=-1 → suelta

    def test_buy_se_ignora(self):
        assert classify_open_orders([_orden(side="BUY")], {"BTC": 1.0}) == []

    def test_qty_restante_descuenta_ejecutado(self):
        out = classify_open_orders(
            [_orden(origQty="1.0", executedQty="0.4")], {"BTC": 2.0})
        assert out[0]["qty"] == pytest.approx(0.6)
        assert out[0]["pct_holding"] == pytest.approx(0.3)

    def test_orden_mayor_que_holding_pct_sin_clamp(self):
        out = classify_open_orders([_orden(origQty="3.0")], {"BTC": 2.0})
        assert out[0]["pct_holding"] == pytest.approx(1.5)   # hecho observado

    def test_holding_desconocido_pct_null(self):
        out = classify_open_orders([_orden(symbol="PEPEUSDT")], {"BTC": 2.0})
        assert out[0]["pct_holding"] is None   # se abstiene, no inventa

    def test_orden_completamente_ejecutada_se_omite(self):
        assert classify_open_orders(
            [_orden(origQty="0.5", executedQty="0.5")], {"BTC": 2.0}) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_observed_orders.py::TestClassifyOpenOrders -v`
Expected: FAIL con `ImportError: cannot import name 'classify_open_orders'`

- [ ] **Step 3: Write the implementation**

En `binance_sync.py`, añadir tras `base_asset`:

```python
_SL_TYPES = ("STOP_LOSS", "STOP_LOSS_LIMIT")
_TP_STOP_TYPES = ("TAKE_PROFIT", "TAKE_PROFIT_LIMIT")


def classify_open_orders(orders: list[dict], holdings_qty: dict[str, float]) -> list[dict]:
    """Función PURA (sin red, sin DB): órdenes crudas de get_open_orders() →
    [{symbol, kind, price, qty, pct_holding, order_id, oco_group}].

    Mapeo (solo side=SELL — un BUY es entrada pendiente, no protección):
    STOP_LOSS* → SL (stopPrice); TAKE_PROFIT* → TP (stopPrice);
    LIMIT_MAKER (pata alta de OCO) y LIMIT venta → TP (price).
    Patas OCO comparten orderListId → oco_group (orderListId=-1 → None).
    qty = origQty - executedQty (lo vivo). pct_holding = qty / holding del
    base asset (`holdings_qty` = balances {asset: free+locked}); sin holding
    conocido → None (se abstiene); orden > holding → pct real >1 SIN clamp
    (hecho observado, no se maquilla). Spec v0.3 §3."""
    out: list[dict] = []
    for o in orders:
        if o.get("side") != "SELL":
            continue
        otype = o.get("type")
        if otype in _SL_TYPES:
            kind, price = "SL", float(o["stopPrice"])
        elif otype in _TP_STOP_TYPES:
            kind, price = "TP", float(o["stopPrice"])
        elif otype in ("LIMIT_MAKER", "LIMIT"):
            kind, price = "TP", float(o["price"])
        else:
            continue  # tipo desconocido/futuro: no se clasifica, no se inventa
        qty = float(o["origQty"]) - float(o.get("executedQty", 0) or 0)
        if qty <= 0 or price <= 0:
            continue
        symbol = o["symbol"].upper()
        held = holdings_qty.get(base_asset(symbol))
        pct = (qty / held) if held else None
        olist = int(o.get("orderListId", -1))
        out.append({
            "symbol": symbol, "kind": kind, "price": price, "qty": qty,
            "pct_holding": pct, "order_id": int(o["orderId"]),
            "oco_group": olist if olist != -1 else None,
        })
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_observed_orders.py -v`
Expected: PASS (clasificación + migración)

- [ ] **Step 5: Commit**

```bash
git add binance_sync.py tests/test_observed_orders.py
git commit -m "feat(binance): classify_open_orders — mapeo puro SL/TP/OCO con pct cobertura (v0.3 §3)"
```

---

### Task 4: Snapshot + resumen — `apply_observed_orders`

**Files:**
- Modify: `binance_sync.py`
- Test: `tests/test_observed_orders.py` (añadir clase)

- [ ] **Step 1: Write the failing tests**

Añadir a `tests/test_observed_orders.py`. Necesita filas en `positions`: insertarlas con SQL directo en la fixture (mirar cómo `tests/test_binance_sync.py` inserta posiciones EXTERNAL de prueba y replicar — respeta los CHECKs: qty>0, tenant_id NOT NULL, direction enum):

```python
from binance_sync import apply_observed_orders

_OBSERVED_AT = "2026-06-11T12:00:00+00:00"


def _clasificada(**kw):
    base = {"symbol": "BTCUSDT", "kind": "SL", "price": 50000.0, "qty": 0.5,
            "pct_holding": 0.25, "order_id": 1, "oco_group": None}
    base.update(kw)
    return base


class TestApplyObservedOrders:
    def test_snapshot_inserta_y_resume_en_fila_external(self, con_with_positions):
        con = con_with_positions   # tenant 1: BTCUSDT EXTERNAL open con sl/tp previos
        apply_observed_orders(con, tenant_id=1, classified=[
            _clasificada(order_id=1, kind="SL", price=50000.0, qty=0.5),
            _clasificada(order_id=2, kind="TP", price=75000.0, qty=0.5),
        ], observed_at=_OBSERVED_AT)
        rows = con.execute("SELECT * FROM observed_orders WHERE tenant_id=1").fetchall()
        assert len(rows) == 2
        pos = con.execute(
            "SELECT sl_price, tp_price FROM positions "
            "WHERE tenant_id=1 AND symbol='BTCUSDT'").fetchone()
        assert pos["sl_price"] == 50000.0
        assert pos["tp_price"] == 75000.0

    def test_resumen_toma_orden_de_mayor_qty(self, con_with_positions):
        con = con_with_positions
        apply_observed_orders(con, tenant_id=1, classified=[
            _clasificada(order_id=1, kind="SL", price=48000.0, qty=0.2),
            _clasificada(order_id=2, kind="SL", price=50000.0, qty=1.0),
        ], observed_at=_OBSERVED_AT)
        pos = con.execute(
            "SELECT sl_price FROM positions WHERE tenant_id=1 AND symbol='BTCUSDT'"
        ).fetchone()
        assert pos["sl_price"] == 50000.0      # la de qty 1.0 gana

    def test_sin_orden_limpia_a_null(self, con_with_positions):
        con = con_with_positions   # la fila arranca con sl_price/tp_price tecleados
        apply_observed_orders(con, tenant_id=1, classified=[], observed_at=_OBSERVED_AT)
        pos = con.execute(
            "SELECT sl_price, tp_price FROM positions "
            "WHERE tenant_id=1 AND symbol='BTCUSDT'").fetchone()
        assert pos["sl_price"] is None         # fuente de verdad: sin orden = sin SL
        assert pos["tp_price"] is None

    def test_idempotente_dos_corridas_mismo_estado(self, con_with_positions):
        con = con_with_positions
        plan = [_clasificada(order_id=1)]
        apply_observed_orders(con, tenant_id=1, classified=plan, observed_at=_OBSERVED_AT)
        apply_observed_orders(con, tenant_id=1, classified=plan, observed_at=_OBSERVED_AT)
        rows = con.execute("SELECT * FROM observed_orders WHERE tenant_id=1").fetchall()
        assert len(rows) == 1                  # delete+insert: no duplica

    def test_aislamiento_per_tenant(self, con_with_positions):
        con = con_with_positions   # fixture también crea tenant 2 con sus órdenes
        con.execute(
            "INSERT INTO observed_orders "
            "(tenant_id, symbol, kind, price, qty, order_id, observed_at) "
            "VALUES (2, 'ETHUSDT', 'SL', 3000, 1, 99, ?)", (_OBSERVED_AT,))
        apply_observed_orders(con, tenant_id=1, classified=[], observed_at=_OBSERVED_AT)
        t2 = con.execute("SELECT * FROM observed_orders WHERE tenant_id=2").fetchall()
        assert len(t2) == 1                    # el snapshot de 1 no toca a 2

    def test_fila_internal_jamas_tocada(self, con_with_positions):
        con = con_with_positions   # fixture crea también una INTERNAL con SL propio
        before = con.execute(
            "SELECT sl_price, tp_price FROM positions "
            "WHERE tenant_id=1 AND control_domain='INTERNAL'").fetchone()
        apply_observed_orders(con, tenant_id=1, classified=[], observed_at=_OBSERVED_AT)
        after = con.execute(
            "SELECT sl_price, tp_price FROM positions "
            "WHERE tenant_id=1 AND control_domain='INTERNAL'").fetchone()
        assert dict(before) == dict(after)
```

La fixture `con_with_positions` debe crear: tenant 1 con una BTCUSDT EXTERNAL open (sl/tp tecleados NO-NULL para probar la limpieza) + una posición INTERNAL open con sl/tp propios; y dejar la conexión con `row_factory = sqlite3.Row`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_observed_orders.py::TestApplyObservedOrders -v`
Expected: FAIL con `ImportError: cannot import name 'apply_observed_orders'`

- [ ] **Step 3: Write the implementation**

En `binance_sync.py`, añadir tras `classify_open_orders`:

```python
def apply_observed_orders(
    con: sqlite3.Connection, *, tenant_id: int, classified: list[dict], observed_at: str,
) -> dict:
    """FASE WRITE (tx CORTA del caller, sin I/O): snapshot fuente-de-verdad.

    (a) DELETE + reinserta observed_orders del tenant (sin estado incremental).
    (b) Resumen en cada fila EXTERNAL open: sl_price/tp_price = la orden de
        mayor qty de su kind; sin orden de ese kind → NULL (decisión Samuel
        2026-06-11: sin orden abierta = sin protección real — el dashboard
        nunca muestra protección ficticia). Aplica a OPERATOR y AUTO_DERIVED
        por igual; filas INTERNAL intocables (su SL/TP es del camino de
        control check_position_stops). Spec v0.3 §5."""
    con.execute("DELETE FROM observed_orders WHERE tenant_id=?", (tenant_id,))
    for c in classified:
        con.execute(
            """INSERT INTO observed_orders
                   (tenant_id, symbol, kind, price, qty, pct_holding,
                    order_id, oco_group, observed_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (tenant_id, c["symbol"], c["kind"], c["price"], c["qty"],
             c["pct_holding"], c["order_id"], c["oco_group"], observed_at),
        )
    # Mejor orden por (symbol, kind) = la de mayor qty.
    best: dict[str, dict[str, dict]] = {}
    for c in classified:
        slot = best.setdefault(c["symbol"], {})
        cur = slot.get(c["kind"])
        if cur is None or c["qty"] > cur["qty"]:
            slot[c["kind"]] = c
    rows = con.execute(
        "SELECT id, symbol FROM positions "
        "WHERE tenant_id=? AND status='open' AND control_domain='EXTERNAL'",
        (tenant_id,),
    ).fetchall()
    summarized: list[str] = []
    for r in rows:
        slot = best.get(r["symbol"], {})
        sl, tp = slot.get("SL"), slot.get("TP")
        con.execute(
            "UPDATE positions SET sl_price=?, tp_price=? WHERE id=?",
            (sl["price"] if sl else None, tp["price"] if tp else None, r["id"]),
        )
        summarized.append(r["symbol"])
    return {"observed": len(classified), "summarized": summarized}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_observed_orders.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add binance_sync.py tests/test_observed_orders.py
git commit -m "feat(binance): apply_observed_orders — snapshot atómico + resumen fuente-de-verdad (v0.3 §5)"
```

---

### Task 5: Integración en el sync — `sync_tenant`

**Files:**
- Modify: `tools/sync_binance_spot.py`
- Test: `tests/test_sync_binance_spot.py` (añadir tests al final, replicando el estilo de mocking del archivo)

- [ ] **Step 1: Write the failing tests**

Añadir a `tests/test_sync_binance_spot.py` (replicar la fixture/mocks existentes del archivo — ya mockea `BinanceAccountClient` y `get_server_time_offset_ms`):

```python
class TestObservedOrdersEnSync:
    def test_sync_captura_y_persiste_observed_orders(self, ...):
        # mock: client.get_open_orders devuelve un OCO SELL de BTCUSDT
        # correr sync_tenant(tenant_id, ...)
        # assert: report["observed_orders"]["observed"] == 2
        # assert: tabla observed_orders tiene las 2 filas del tenant
        ...

    def test_fallo_de_open_orders_omite_paso_completo(self, ...):
        # mock: get_spot_balances OK; get_open_orders levanta BinanceRateBanned
        # pre-condición: observed_orders tiene un snapshot previo del tenant
        # correr sync_tenant
        # assert: report["observed_orders"] == "SKIPPED"
        # assert: el snapshot previo sigue INTACTO (ni borrado ni parcial — F8)
        # assert: sl_price de la fila EXTERNAL NO se limpió
        # assert: el resto del sync corrió (report["status"] == "ACTIVE")
        ...

    def test_dry_run_no_persiste_observed_orders(self, ...):
        # correr sync_tenant(dry_run=True) con open orders mockeadas
        # assert: el report las muestra, la tabla queda vacía (rollback)
        ...
```

(Los `...` de la firma son la fixture real del archivo; los del cuerpo se rellenan siguiendo el estilo de los tests vecinos. Los ASSERTS listados son el contrato — implementarlos todos.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sync_binance_spot.py -v -k Observed`
Expected: FAIL — `sync_tenant` no captura órdenes todavía (KeyError `observed_orders`).

- [ ] **Step 3: Write the implementation**

En `tools/sync_binance_spot.py`:

1. Ampliar imports:

```python
import logging
from datetime import datetime, timezone

from binance_sync import (
    apply_observed_orders, apply_spot_autocreate, classify_open_orders,
    plan_spot_autocreate, reconcile_spot,
)

log = logging.getLogger(__name__)
```

2. En `sync_tenant`, FASE 1, dentro del `try` existente justo después de `balances = client.get_spot_balances()` — pero con su PROPIO try anidado (su fallo NO debe abortar el sync entero ni tocar el estado de la credencial; el fallo del paso = omisión completa, eco F8):

```python
        # v0.3: órdenes de protección abiertas. Fallo aquí = paso OMITIDO
        # completo este ciclo (ni snapshot parcial ni limpieza por un fallo
        # de red — eco F8: parcial es incorrecto, no incompleto). Spec §5.4.
        observed = None
        try:
            observed = classify_open_orders(client.get_open_orders(), balances)
        except (BinanceAuthError, BinanceClockSkew, BinanceRateBanned,
                BinanceTransportError) as e:
            log.warning("OBSERVED_ORDERS_SKIPPED tenant=%s causa=%s",
                        tenant_id, type(e).__name__)
```

NOTA: la variable `observed` debe inicializarse a `None` ANTES del `try` externo de FASE 1, para que exista si `autocreate` está apagado y para no romper los returns tempranos.

3. En FASE 2, dentro del `with transaction() as con:` después del bloque `if autocreate:`:

```python
            if observed is not None:
                report["observed_orders"] = apply_observed_orders(
                    con, tenant_id=tenant_id, classified=observed,
                    observed_at=datetime.now(timezone.utc).isoformat(),
                )
            else:
                report["observed_orders"] = "SKIPPED"
```

(El `--dry-run` existente ya cubre el rollback vía `_DryRunAbort` — no necesita código nuevo.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sync_binance_spot.py -v`
Expected: PASS (nuevos + TODOS los previos del archivo)

- [ ] **Step 5: Commit**

```bash
git add tools/sync_binance_spot.py tests/test_sync_binance_spot.py
git commit -m "feat(sync): captura SL/TP observados en sync_tenant — red en FASE 1, write en FASE 2 (v0.3 §5)"
```

---

### Task 6: API — adjuntar `observed_orders` en GET /positions

**Files:**
- Create: `db/observed_orders.py`
- Modify: `api/positions.py:272-285` (`list_positions`)
- Test: `tests/test_observed_orders.py` (añadir clase) + verificar que `tests/test_api.py` sigue verde

- [ ] **Step 1: Write the failing test**

Añadir a `tests/test_observed_orders.py`:

```python
from db.observed_orders import db_get_observed_orders


class TestDbGetObservedOrders:
    def test_devuelve_solo_el_tenant_ordenado(self, con_with_positions):
        con = con_with_positions
        for t, sym, kind, oid in [(1, "BTCUSDT", "SL", 1), (1, "BTCUSDT", "TP", 2),
                                  (2, "ETHUSDT", "SL", 3)]:
            con.execute(
                "INSERT INTO observed_orders "
                "(tenant_id, symbol, kind, price, qty, order_id, observed_at) "
                "VALUES (?, ?, ?, 100, 1, ?, '2026-06-11T00:00:00')",
                (t, sym, kind, oid))
        out = db_get_observed_orders(con, tenant_id=1)
        assert len(out) == 2
        assert all(o["symbol"] == "BTCUSDT" for o in out)
        assert isinstance(out[0], dict)
```

Y un test del endpoint en `tests/test_api.py` siguiendo el patrón de client/auth del archivo (mirar cómo los tests existentes de GET /positions montan el TestClient + JWT):

```python
def test_list_positions_adjunta_observed_orders_a_external(...):
    # sembrar: posición EXTERNAL open del tenant + 1 fila observed_orders
    # GET /positions → la posición EXTERNAL trae observed_orders=[...]
    # y una posición INTERNAL del mismo tenant NO trae el campo
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_observed_orders.py::TestDbGetObservedOrders -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'db.observed_orders'`

- [ ] **Step 3: Write the implementation**

Crear `db/observed_orders.py` (helper SQL PURO — recibe `con`, cero transaction(), cero side-effects — CLAUDE.md §Database access capa 1):

```python
"""Helpers SQL puros de observed_orders (Binance v0.3 — SL/TP observados).

Capa 1 (CLAUDE.md §Database access): reciben `con`, corren SQL, devuelven
data. El write-path (apply_observed_orders) vive en binance_sync.py porque
participa del flujo de sync; aquí solo lecturas para la API.
"""
from __future__ import annotations

import sqlite3


def db_get_observed_orders(con: sqlite3.Connection, *, tenant_id: int) -> list[dict]:
    """Órdenes observadas del tenant, ordenadas para presentación estable
    (symbol, kind, qty DESC — la de mayor cobertura primero)."""
    rows = con.execute(
        "SELECT symbol, kind, price, qty, pct_holding, order_id, oco_group, observed_at "
        "FROM observed_orders WHERE tenant_id=? "
        "ORDER BY symbol, kind, qty DESC",
        (tenant_id,),
    ).fetchall()
    return [dict(r) for r in rows]
```

En `api/positions.py`, modificar `list_positions` (la lectura sigue siendo TERMINAL — serializa a response, no alimenta write-tx → `snapshot_connection` se mantiene, patrón 4b):

```python
from db.observed_orders import db_get_observed_orders   # junto a los imports de db.*

@router.get("", summary="Listar posiciones")
def list_positions(
    status: Optional[str] = Query("all", description="open | closed | all"),
    tenant_id: int = Depends(get_current_tenant_id),
):
    # B.5 #258: tenant_id from JWT, never from request param/header/body.
    # READ via snapshot_connection (WAL-concurrent, query_only, NO BEGIN
    # IMMEDIATE) — NOT transaction(). transaction() takes the writer lock even
    # for reads; under the scanner's write burst it 500'd with "database is
    # locked" (prod incident 2026-05-29). A read must never contend for the
    # writer lock.
    with snapshot_connection() as con:
        positions = db_get_positions(con, status, tenant_id=tenant_id)
        observed = db_get_observed_orders(con, tenant_id=tenant_id)
    # v0.3: adjuntar las órdenes observadas SOLO a filas EXTERNAL (las
    # INTERNAL no llevan el campo — su SL/TP es del camino de control).
    by_symbol: dict = {}
    for o in observed:
        by_symbol.setdefault(o["symbol"], []).append(o)
    for p in positions:
        if p.get("control_domain") == "EXTERNAL":
            p["observed_orders"] = by_symbol.get(p["symbol"], [])
    return {"total": len(positions), "positions": positions}
```

VERIFICAR antes: que `db_get_positions` devuelve dicts que incluyen `control_domain` (grep la función en `db/positions.py`). Si no lo incluye, añadir la columna al SELECT de esa función — NO inventar otra query.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_observed_orders.py tests/test_api.py -v`
Expected: PASS (nuevos + suite API previa intacta)

- [ ] **Step 5: Commit**

```bash
git add db/observed_orders.py api/positions.py tests/test_observed_orders.py tests/test_api.py
git commit -m "feat(api): GET /positions adjunta observed_orders a filas EXTERNAL (v0.3 §6)"
```

---

### Task 7: Frontend — lista de órdenes observadas + badge "sin stop"

**Files:**
- Modify: `frontend/src/types.ts` (interfaz `ObservedOrder` + campo en `Position`)
- Create: `frontend/src/components/ObservedOrders.tsx` + `frontend/src/components/ObservedOrders.test.tsx`
- Modify: `frontend/src/components/PositionsView.tsx` (~línea 302, dentro de `cardBody`) + `frontend/src/components/PositionsView.module.css`

- [ ] **Step 1: Write the failing test**

Crear `frontend/src/components/ObservedOrders.test.tsx` (replicar el setup de render de `AgentHistorySidebar.test.tsx` / `ConnectionsPanel.test.tsx`):

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ObservedOrdersList } from './ObservedOrders';
import type { ObservedOrder } from '../types';

const sl: ObservedOrder = {
  symbol: 'BTCUSDT', kind: 'SL', price: 50000, qty: 0.5,
  pct_holding: 0.25, order_id: 1, oco_group: 33,
  observed_at: '2026-06-11T12:00:00+00:00',
};
const tp: ObservedOrder = { ...sl, kind: 'TP', price: 75000, order_id: 2 };

describe('ObservedOrdersList', () => {
  it('muestra cada orden con su porcentaje', () => {
    render(<ObservedOrdersList orders={[sl, tp]} />);
    expect(screen.getByText(/SL/)).toBeInTheDocument();
    expect(screen.getByText(/25%/u, { exact: false })).toBeTruthy();
    expect(screen.getByText(/TP/)).toBeInTheDocument();
  });

  it('badge "sin stop" cuando no hay ninguna orden SL', () => {
    render(<ObservedOrdersList orders={[tp]} />);
    expect(screen.getByText('sin stop')).toBeInTheDocument();
  });

  it('sin badge cuando hay SL', () => {
    render(<ObservedOrdersList orders={[sl, tp]} />);
    expect(screen.queryByText('sin stop')).toBeNull();
  });

  it('pct desconocido no muestra porcentaje', () => {
    render(<ObservedOrdersList orders={[{ ...sl, pct_holding: null }]} />);
    expect(screen.queryByText(/%/)).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/ObservedOrders.test.tsx`
Expected: FAIL — `ObservedOrders.tsx` no existe.

- [ ] **Step 3: Write the implementation**

En `frontend/src/types.ts`, tras la interfaz `Position`:

```ts
// Binance v0.3 — orden de protección observada en la cuenta spot (read-only).
// Solo presente en posiciones EXTERNAL; el backend la adjunta en GET /positions.
export interface ObservedOrder {
  symbol:      string;
  kind:        'SL' | 'TP';
  price:       number;
  qty:         number;
  pct_holding: number | null;   // null = holding sin qty conocida (se abstiene)
  order_id:    number;
  oco_group:   number | null;   // patas OCO comparten grupo
  observed_at: string;
}
```

Y dentro de `interface Position`, tras `atr_entry`:

```ts
  observed_orders?: ObservedOrder[];   // v0.3: solo filas EXTERNAL
```

Crear `frontend/src/components/ObservedOrders.tsx`:

```tsx
import React from 'react';
import type { ObservedOrder } from '../types';
import styles from './ObservedOrders.module.css';

// Lista de órdenes de protección observadas en Binance (v0.3, read-only).
// "SL 50.000 (25%)" por orden; badge "sin stop" si ningún SL protege el hold.
export const ObservedOrdersList: React.FC<{ orders: ObservedOrder[] }> = ({ orders }) => {
  const hasSl = orders.some((o) => o.kind === 'SL');
  return (
    <div className={styles.wrap}>
      {!hasSl && <span className={`${styles.noStop} label`}>sin stop</span>}
      {orders.map((o) => (
        <span
          key={o.order_id}
          className={`${styles.order} ${o.kind === 'SL' ? styles.orderSl : styles.orderTp} num`}
        >
          {o.kind} {o.price.toLocaleString('es-VE')}
          {o.pct_holding != null && ` (${Math.round(o.pct_holding * 100)}%)`}
        </span>
      ))}
    </div>
  );
};
```

Crear `frontend/src/components/ObservedOrders.module.css` (tonos: reusar las variables CSS que ya usa `PositionsView.module.css` — inspeccionarlo y mantener la paleta):

```css
.wrap {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.order {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid var(--border, #2a2e39);
}
.orderSl { color: var(--bear, #f6465d); }
.orderTp { color: var(--bull, #0ecb81); }
.noStop {
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 4px;
  color: var(--warn, #f0b90b);
  border: 1px dashed var(--warn, #f0b90b);
}
```

En `frontend/src/components/PositionsView.tsx`: importar `ObservedOrdersList` y, dentro de `PositionCard`, después del bloque del gauge (el `hasGauge ? ... : ...` que arranca en ~línea 304), añadir:

```tsx
        {p.observed_orders && <ObservedOrdersList orders={p.observed_orders} />}
```

- [ ] **Step 4: Run tests + build to verify**

Run: `cd frontend && npx vitest run src/components/ObservedOrders.test.tsx && npm run build`
Expected: tests PASS, build limpio (tsc sin errores).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/ObservedOrders.tsx \
        frontend/src/components/ObservedOrders.module.css \
        frontend/src/components/ObservedOrders.test.tsx \
        frontend/src/components/PositionsView.tsx
git commit -m "feat(frontend): órdenes SL/TP observadas con % y badge 'sin stop' (v0.3 §6)"
```

---

### Task 8: Gate final + GROW (contrato del repo)

**Files:**
- Modify: `.mex/ROUTER.md` (sección Current Project State)
- Create: `.mex/patterns/extender-sync-binance.md` + fila en `.mex/patterns/INDEX.md`

- [ ] **Step 1: Run the full fast gate**

Run: `python -m pytest tests/ -m "not network" -n auto -q`
Expected: todo verde (~49s). Si algo falla, arreglar ANTES de seguir — no hay bypass.

- [ ] **Step 2: Frontend build final**

Run: `cd frontend && npm run build`
Expected: limpio.

- [ ] **Step 3: GROW — pattern nuevo**

Crear `.mex/patterns/extender-sync-binance.md` con el formato de los patterns existentes (Purpose / When / Steps / Gotchas / Verify Checklist). Contenido mínimo: dónde vive cada capa (cliente delgado → clasificación pura → apply en tx corta → orquestación en sync_tenant), la regla "red en FASE 1 fuera de tx, writes en FASE 2" (Halberg), el eco F8 (parcial = incorrecto: fallo de red ⇒ paso omitido completo, jamás snapshot parcial), y que el fallo del paso nuevo NO debe tumbar el sync ni el estado de la credencial. Añadir la fila a `.mex/patterns/INDEX.md`.

- [ ] **Step 4: GROW — actualizar ROUTER + log**

En `.mex/ROUTER.md` §Working añadir: "Binance v0.3: SL/TP observados (openOrders → observed_orders + resumen fuente-de-verdad en filas EXTERNAL)."

Run: `mex log "v0.3 SL/TP observados: snapshot observed_orders + resumen sobrescribe-siempre/NULL-sin-orden (decisión Samuel 2026-06-11)"`

- [ ] **Step 5: Commit**

```bash
git add .mex/
git commit -m "docs(mex): pattern extender-sync-binance + estado v0.3 en ROUTER"
```

---

## Validación end-to-end (manual, post-merge)

Con la credencial real del tenant de Simón (read-only, ya configurada desde v0.1):

```bash
python -m tools.sync_binance_spot --tenant 2 --dry-run    # ver el plan sin persistir
python -m tools.sync_binance_spot --tenant 2              # persistir
```

Verificar en el dashboard que cada posición EXTERNAL muestra sus órdenes ("SL X (N%)" / "TP Y (N%)") y que un hold sin SL muestra el badge "sin stop". Si Binance reporta una orden que el dashboard no muestra (o viceversa), comparar contra `SELECT * FROM observed_orders WHERE tenant_id=2` antes de tocar código.
