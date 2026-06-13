# El Instrumento — Fase 3a (el lazo vivo, pull-only) · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir el acompañante en vivo pull-only: sigue una posición real contra el plan confirmado (detector de transiciones desde `observed_orders`), persiste el estado vivo, lo expone por pull, y mide conducta al cierre — sin push, sin instrucción, sin tocar el cierre.

**Architecture:** Un detector puro (`instrument/tracker.py`) que deriva eventos de snapshots de `observed_orders` + qty y avanza la máquina de F1; un domicilio `lifecycle_states` (tabla + helpers + serialización en `db/lifecycle_states.py`); endpoints de gate y vista (`api/plan.py`); y un hook de I/O en `tools/sync_binance_spot.py` que corre tras el sync, más la conducta al cierre vía `compute_conduct` de F1.

**Tech Stack:** Python 3.12 (`dataclasses`, `json`), FastAPI, pytest. Reutiliza F1 (`instrument/lifecycle.py`, `instrument/plan.py`, `instrument/conduct.py`) y `screener/sr_levels.py` (D.1).

**Spec:** `docs/superpowers/specs/es/2026-06-13-instrumento-fase3a-lazo-vivo-design.md`.

**Branch:** `feat/instrumento-fase3a-lazo-vivo` (ya creada).

**Frontera dura:** read-only sobre `positions`, sin `PositionClosure`, sin escribir `closed`, **sin push, sin instrucción**. Escribe solo `lifecycle_states` + `conduct_episodes`.

---

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `instrument/tracker.py` (crear) | `detect_transitions` (puro) + `advance_live` (puro). |
| `db/lifecycle_states.py` (crear) | Serialización `Plan`/`LifecycleState` ↔ JSON + helpers SQL del domicilio. |
| `db/schema.py` (modificar) | Migración `lifecycle_states`. |
| `api/plan.py` (crear) | `GET /plan/derive/{symbol}`, `POST /plan/confirm`, `GET /plan/{symbol}` + el constructor de `hechos` (anti-imperativo). |
| `btc_api.py` (modificar) | Registrar el router de `plan`. |
| `tools/sync_binance_spot.py` (modificar) | Hook: tras el sync, correr `advance_live` sobre las filas activas + conducta al cierre. |
| `tests/test_instrument_tracker.py`, `tests/test_lifecycle_states.py`, `tests/test_plan_api.py` (crear) | Tests. |

---

### Task 1: `detect_transitions` — el detector puro

**Files:**
- Create: `instrument/tracker.py`
- Test: `tests/test_instrument_tracker.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_instrument_tracker.py
"""Tests del detector/tracker en vivo (instrumento F3a). Puro: sin red, sin DB. Spec §5."""
from instrument.plan import derive_plan
from instrument.lifecycle import LifecycleState
from instrument.tracker import detect_transitions


def _z(tipo, bajo, alto, centro):
    return {"tipo": tipo, "precio_bajo": bajo, "precio_alto": alto,
            "centro": centro, "toques": 3, "confluencia_redondo": []}


def _plan():
    zonas = [_z("soporte", 94, 96, 95),
             _z("resistencia", 104, 106, 105),
             _z("resistencia", 109, 111, 110)]
    return derive_plan(zonas, entry_price=100.0)


def _armed(p, **kw):
    return LifecycleState(plan_id=0, fase="CONFIRMED", sl_actual=p.sl_price, **kw)


def _tp(price, oid, qty=1.0):
    return {"kind": "TP", "price": price, "qty": qty, "order_id": oid}


def _sl(price, oid, qty=1.0):
    return {"kind": "SL", "price": price, "qty": qty, "order_id": oid}


def test_tp_desaparece_con_caida_de_qty_es_rung_filled():
    p = _plan()
    prev = [_tp(105, 11), _sl(p.sl_price, 99)]
    curr = [_sl(p.sl_price, 99)]                  # el TP de 105 ya no está
    evs = detect_transitions(p, _armed(p), prev, curr, prev_qty=1.0, curr_qty=0.5)
    rung = [e for e in evs if e["tipo"] == "RUNG_FILLED"]
    assert len(rung) == 1 and rung[0]["rung_index"] == 0 and rung[0]["order_id"] == "11"


def test_tp_desaparece_sin_caida_de_qty_es_cancelacion():
    p = _plan()
    prev = [_tp(105, 11), _sl(p.sl_price, 99)]
    curr = [_sl(p.sl_price, 99)]
    evs = detect_transitions(p, _armed(p), prev, curr, prev_qty=1.0, curr_qty=1.0)  # qty igual
    assert not any(e["tipo"] == "RUNG_FILLED" for e in evs)   # cancelación, no fill


def test_rung_idempotente_por_order_id():
    p = _plan()
    prev = [_tp(105, 11)]
    curr = []
    st = _armed(p, consumed_order_ids=frozenset({"11"}))
    evs = detect_transitions(p, st, prev, curr, prev_qty=1.0, curr_qty=0.5)
    assert not any(e["tipo"] == "RUNG_FILLED" for e in evs)   # ya consumido


def test_sl_cambia_a_entry_es_sl_moved():
    p = _plan()
    prev = [_sl(p.sl_price, 99)]
    curr = [_sl(p.entry_price, 99)]
    evs = detect_transitions(p, _armed(p), prev, curr, prev_qty=1.0, curr_qty=1.0)
    sl = [e for e in evs if e["tipo"] == "SL_MOVED"]
    assert len(sl) == 1 and sl[0]["nuevo_sl"] == p.entry_price


def test_qty_cero_es_stop_hit():
    p = _plan()
    evs = detect_transitions(p, _armed(p), [_sl(p.sl_price, 99)], [], prev_qty=1.0, curr_qty=0.0)
    assert any(e["tipo"] == "STOP_HIT" for e in evs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_instrument_tracker.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'instrument.tracker'`

- [ ] **Step 3: Write minimal implementation**

```python
# instrument/tracker.py
"""Tracker en vivo del instrumento (F3a) — puro, sin red, sin DB.

Deriva eventos de transición de los snapshots de observed_orders + qty y avanza
la máquina de F1. HONESTO sobre la resolución: un TP que desaparece pudo llenarse
(qty baja) o cancelarse (qty igual); la ambigüedad no se inventa. Idempotente por
order_id. Spec §5. NO emite alertas, NO instruye — solo deriva hechos."""
from __future__ import annotations

from instrument.lifecycle import LifecycleState, step

_TOL = 0.005   # 0.5%: proximidad de precio observado ↔ nivel del plan
_EPS = 1e-9


def _close(a: float, b: float) -> bool:
    return b != 0 and abs(a - b) / abs(b) <= _TOL


def detect_transitions(plan, state: LifecycleState, prev_observed: list[dict],
                       curr_observed: list[dict], prev_qty: float,
                       curr_qty: float) -> list[dict]:
    """Snapshots de observed_orders (prev/curr) + qty → eventos para step(). Puro."""
    proc = "observado"
    events: list[dict] = []
    curr_ids = {o["order_id"] for o in curr_observed}
    qty_dropped = curr_qty < prev_qty - _EPS

    # 1. RUNG_FILLED: TP que estaba y ya no, matchea un rung no-lleno, + qty bajó.
    if qty_dropped:
        for o in prev_observed:
            oid = str(o["order_id"])
            if o.get("kind") != "TP" or o["order_id"] in curr_ids:
                continue
            if oid in state.consumed_order_ids:
                continue
            for i, r in enumerate(plan.rungs):
                if i in state.rungs_llenos:
                    continue
                if _close(o["price"], r.tp_price):
                    events.append({"tipo": "RUNG_FILLED", "order_id": oid,
                                   "rung_index": i, "procedencia": proc})
                    break

    # 2. SL_MOVED: el SL observado cambió de precio entre snapshots.
    prev_sl = next((o for o in prev_observed if o.get("kind") == "SL"), None)
    curr_sl = next((o for o in curr_observed if o.get("kind") == "SL"), None)
    if prev_sl and curr_sl and not _close(prev_sl["price"], curr_sl["price"]):
        events.append({"tipo": "SL_MOVED", "nuevo_sl": float(curr_sl["price"]),
                       "procedencia": proc})

    # 3. Cierre: qty → 0.
    if curr_qty <= 1e-8:
        events.append({"tipo": "STOP_HIT", "procedencia": proc})

    return events
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_instrument_tracker.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add instrument/tracker.py tests/test_instrument_tracker.py
git commit -m "feat(instrument): detect_transitions — eventos desde snapshots de observed_orders (idempotente, honesto)"
```

---

### Task 2: `advance_live` — avanzar la máquina + detectar cierre

**Files:**
- Modify: `instrument/tracker.py`
- Test: `tests/test_instrument_tracker.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_instrument_tracker.py — añadir
from instrument.tracker import advance_live


def test_advance_live_aplica_rung_y_avanza():
    p = _plan()
    prev = [_tp(105, 11), _sl(p.sl_price, 99)]
    curr = [_sl(p.sl_price, 99)]
    new, events = advance_live(p, _armed(p), prev, curr, prev_qty=1.0, curr_qty=0.5)
    assert 0 in new.rungs_llenos
    assert "11" in new.consumed_order_ids
    assert any(e["tipo"] == "RUNG_FILLED" for e in events)
    assert new.fase == "RUNNING"


def test_advance_live_cierre_lleva_a_closed():
    p = _plan()
    new, events = advance_live(p, _armed(p), [_sl(p.sl_price, 99)], [],
                               prev_qty=1.0, curr_qty=0.0)
    assert new.fase == "CLOSED"
    assert new.close_reason in ("SL_HIT", "BE_HIT")


def test_advance_live_sin_cambios_no_avanza():
    p = _plan()
    obs = [_sl(p.sl_price, 99)]
    new, events = advance_live(p, _armed(p), obs, obs, prev_qty=1.0, curr_qty=1.0)
    assert events == []
    assert new.fase == "CONFIRMED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_instrument_tracker.py -k advance_live -v`
Expected: FAIL `ImportError: cannot import name 'advance_live'`

- [ ] **Step 3: Write minimal implementation**

```python
# instrument/tracker.py — añadir al final
def advance_live(plan, state: LifecycleState, prev_observed: list[dict],
                 curr_observed: list[dict], prev_qty: float,
                 curr_qty: float) -> tuple[LifecycleState, list[dict]]:
    """Detecta transiciones y avanza la máquina. Devuelve (estado_nuevo, eventos).
    Puro: compone detect_transitions + step de F1. Spec §6."""
    events = detect_transitions(plan, state, prev_observed, curr_observed,
                                prev_qty, curr_qty)
    for e in events:
        state = step(state, e, plan)
        if state.fase == "CLOSED":
            break
    return state, events
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_instrument_tracker.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add instrument/tracker.py tests/test_instrument_tracker.py
git commit -m "feat(instrument): advance_live — compone detección + máquina de F1 → estado nuevo + eventos"
```

---

### Task 3: domicilio `lifecycle_states` — serialización + tabla + helpers

**Files:**
- Create: `db/lifecycle_states.py`
- Modify: `db/schema.py` (migración idempotente, patrón de `_migrate_conduct_episodes`)
- Test: `tests/test_lifecycle_states.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lifecycle_states.py
"""Tests del domicilio lifecycle_states (instrumento F3a). Spec §3."""
import sqlite3

from instrument.plan import derive_plan
from instrument.lifecycle import LifecycleState
from db.lifecycle_states import (
    plan_to_json, plan_from_json, state_to_row, state_from_row,
    db_put_state, db_get_active_state,
)


def _z(tipo, bajo, alto, centro):
    return {"tipo": tipo, "precio_bajo": bajo, "precio_alto": alto,
            "centro": centro, "toques": 3, "confluencia_redondo": []}


def _plan():
    return derive_plan([_z("soporte", 94, 96, 95), _z("resistencia", 104, 106, 105)],
                       entry_price=100.0)


def test_plan_json_roundtrip():
    p = _plan()
    p2 = plan_from_json(plan_to_json(p))
    assert p2.entry_price == p.entry_price
    assert [r.tp_price for r in p2.rungs] == [r.tp_price for r in p.rungs]
    assert p2.sl_price == p.sl_price and p2.runner_frac == p.runner_frac


def test_state_row_roundtrip_preserva_frozensets():
    s = LifecycleState(plan_id=0, fase="RUNNING",
                       rungs_llenos=frozenset({0}), consumed_order_ids=frozenset({"11"}),
                       sl_actual=99.0, be_movido=True, size_restante_frac=0.5)
    s2 = state_from_row(state_to_row(s))
    assert s2.rungs_llenos == frozenset({0})
    assert s2.consumed_order_ids == frozenset({"11"})
    assert s2.be_movido is True and s2.fase == "RUNNING"


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE lifecycle_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT, position_id INTEGER,
            symbol TEXT NOT NULL, tenant_id INTEGER NOT NULL,
            estado_vivo TEXT NOT NULL CHECK (estado_vivo IN ('activo','cerrado','incierto')),
            plan_json TEXT NOT NULL, entry_price REAL NOT NULL, qty_original REAL,
            fase TEXT NOT NULL, rungs_llenos_json TEXT NOT NULL,
            consumed_orders_json TEXT NOT NULL, sl_actual REAL, be_movido INTEGER NOT NULL,
            size_restante_frac REAL, events_json TEXT NOT NULL DEFAULT '[]',
            prev_observed_json TEXT, prev_qty REAL,
            confirmed_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE (tenant_id, symbol, confirmed_at))""")
    return con


def test_put_y_get_active():
    con = _con()
    p = _plan()
    s = LifecycleState(plan_id=0, fase="CONFIRMED", sl_actual=p.sl_price)
    db_put_state(con, position_id=7, symbol="BTCUSDT", tenant_id=2, estado_vivo="activo",
                 plan=p, state=s, entry_price=100.0, qty_original=1.0, events=[],
                 prev_observed=[], prev_qty=1.0, confirmed_at="2026-06-13T00:00:00+00:00",
                 updated_at="2026-06-13T00:00:00+00:00")
    row = db_get_active_state(con, tenant_id=2, symbol="BTCUSDT")
    assert row is not None and row["symbol"] == "BTCUSDT" and row["estado_vivo"] == "activo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_lifecycle_states.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'db.lifecycle_states'`

- [ ] **Step 3: Write minimal implementation**

Create `db/lifecycle_states.py`:

```python
"""Domicilio del estado vivo del plan (instrumento F3a) — serialización + SQL.

Serializa Plan/LifecycleState ↔ JSON (frozensets ↔ listas) y persiste una fila
por plan activo. Helpers SQL puros (reciben `con`). Spec §3."""
from __future__ import annotations

import json
import sqlite3

from instrument.plan import Plan, Rung
from instrument.lifecycle import LifecycleState


def plan_to_json(plan: Plan) -> str:
    return json.dumps({
        "entry_price": plan.entry_price, "entry_zone": plan.entry_zone,
        "sl_price": plan.sl_price, "runner_frac": plan.runner_frac,
        "rungs": [{"tp_price": r.tp_price, "size_frac": r.size_frac,
                   "zona_origen": r.zona_origen} for r in plan.rungs],
    })


def plan_from_json(s: str) -> Plan:
    d = json.loads(s)
    rungs = tuple(Rung(tp_price=r["tp_price"], size_frac=r["size_frac"],
                       zona_origen=r["zona_origen"]) for r in d["rungs"])
    return Plan(entry_price=d["entry_price"], entry_zone=d["entry_zone"],
                sl_price=d["sl_price"], rungs=rungs, runner_frac=d["runner_frac"])


def state_to_row(state: LifecycleState) -> dict:
    return {
        "fase": state.fase,
        "rungs_llenos_json": json.dumps(sorted(state.rungs_llenos)),
        "consumed_orders_json": json.dumps(sorted(state.consumed_order_ids)),
        "sl_actual": state.sl_actual, "be_movido": int(state.be_movido),
        "size_restante_frac": state.size_restante_frac,
    }


def state_from_row(row) -> LifecycleState:
    g = row.__getitem__ if hasattr(row, "__getitem__") else row.get
    return LifecycleState(
        plan_id=0, fase=g("fase"),
        rungs_llenos=frozenset(json.loads(g("rungs_llenos_json"))),
        consumed_order_ids=frozenset(json.loads(g("consumed_orders_json"))),
        sl_actual=g("sl_actual"), be_movido=bool(g("be_movido")),
        size_restante_frac=g("size_restante_frac"),
        close_reason=(g("close_reason") if _has(row, "close_reason") else None),
    )


def _has(row, key) -> bool:
    try:
        row[key]
        return True
    except (KeyError, IndexError):
        return False


def db_put_state(con: sqlite3.Connection, *, position_id, symbol, tenant_id, estado_vivo,
                 plan, state, entry_price, qty_original, events, prev_observed, prev_qty,
                 confirmed_at, updated_at) -> None:
    r = state_to_row(state)
    con.execute(
        """INSERT INTO lifecycle_states
           (position_id, symbol, tenant_id, estado_vivo, plan_json, entry_price,
            qty_original, fase, rungs_llenos_json, consumed_orders_json, sl_actual,
            be_movido, size_restante_frac, events_json, prev_observed_json, prev_qty,
            confirmed_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (position_id, symbol, tenant_id, estado_vivo, plan_to_json(plan), entry_price,
         qty_original, r["fase"], r["rungs_llenos_json"], r["consumed_orders_json"],
         r["sl_actual"], r["be_movido"], r["size_restante_frac"], json.dumps(events),
         json.dumps(prev_observed), prev_qty, confirmed_at, updated_at))


def db_get_active_state(con: sqlite3.Connection, *, tenant_id: int, symbol: str):
    cur = con.execute(
        "SELECT * FROM lifecycle_states WHERE tenant_id=? AND symbol=? "
        "AND estado_vivo IN ('activo','incierto') ORDER BY confirmed_at DESC LIMIT 1",
        (tenant_id, symbol))
    return cur.fetchone()


def db_list_active(con: sqlite3.Connection, *, tenant_id: int) -> list:
    cur = con.execute(
        "SELECT * FROM lifecycle_states WHERE tenant_id=? AND estado_vivo IN ('activo','incierto')",
        (tenant_id,))
    return cur.fetchall()


def db_update_state(con: sqlite3.Connection, *, row_id, estado_vivo, state, events,
                    prev_observed, prev_qty, updated_at) -> None:
    r = state_to_row(state)
    con.execute(
        """UPDATE lifecycle_states SET estado_vivo=?, fase=?, rungs_llenos_json=?,
               consumed_orders_json=?, sl_actual=?, be_movido=?, size_restante_frac=?,
               events_json=?, prev_observed_json=?, prev_qty=?, updated_at=?
           WHERE id=?""",
        (estado_vivo, r["fase"], r["rungs_llenos_json"], r["consumed_orders_json"],
         r["sl_actual"], r["be_movido"], r["size_restante_frac"], json.dumps(events),
         json.dumps(prev_observed), prev_qty, updated_at, row_id))
```

> **Nota:** `state_from_row` no reconstruye `close_reason` salvo que la fila lo traiga; el domicilio no tiene columna `close_reason` (la fase CLOSED + `estado_vivo='cerrado'` lo cubren). El parámetro `close_reason` queda None al rehidratar para el tracker, que solo avanza estados activos. Si el quality review pide persistir `close_reason`, es una columna trivial.

Then add the migration to `db/schema.py` (mirror `_migrate_conduct_episodes`, ~line 1846). New function `_migrate_lifecycle_states`:

```python
def _migrate_lifecycle_states(con: sqlite3.Connection) -> None:
    """Tabla lifecycle_states — domicilio del estado vivo del plan (instrumento F3a).

    Una fila por plan activo del operador: el Plan confirmado (la ley) + la
    LifecycleState incremental + el último snapshot observado (para el delta del
    detector). Escrita por el gate y el tracker; read-only sobre positions.
    Idempotente: CREATE TABLE IF NOT EXISTS.

    Spec: docs/superpowers/specs/es/2026-06-13-instrumento-fase3a-lazo-vivo-design.md §3.
    """
    con.execute(
        """CREATE TABLE IF NOT EXISTS lifecycle_states (
               id                   INTEGER PRIMARY KEY AUTOINCREMENT,
               position_id          INTEGER,
               symbol               TEXT    NOT NULL,
               tenant_id            INTEGER NOT NULL,
               estado_vivo          TEXT    NOT NULL CHECK (estado_vivo IN ('activo','cerrado','incierto')),
               plan_json            TEXT    NOT NULL,
               entry_price          REAL    NOT NULL,
               qty_original         REAL,
               fase                 TEXT    NOT NULL,
               rungs_llenos_json    TEXT    NOT NULL,
               consumed_orders_json TEXT    NOT NULL,
               sl_actual            REAL,
               be_movido            INTEGER NOT NULL,
               size_restante_frac   REAL,
               events_json          TEXT    NOT NULL DEFAULT '[]',
               prev_observed_json   TEXT,
               prev_qty             REAL,
               confirmed_at         TEXT    NOT NULL,
               updated_at           TEXT    NOT NULL,
               UNIQUE (tenant_id, symbol, confirmed_at)
           )"""
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_lifecycle_states_tenant "
        "ON lifecycle_states(tenant_id, estado_vivo)"
    )
    log.info("_migrate_lifecycle_states: lifecycle_states table + index ensured.")
```

Wire its call in its own `with transaction()` block right after the `_migrate_conduct_episodes` block (mirror that block's style):

```python
    # lifecycle_states: domicilio del estado vivo del plan (instrumento F3a).
    with transaction() as con_ls:
        _migrate_lifecycle_states(con_ls)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_lifecycle_states.py -v`
Expected: PASS (3 passed)

Then verify the migration applies: `python -m pytest tests/ -m "not network" -n auto -q -k "schema or migration or lifecycle_states"` → green.

- [ ] **Step 5: Commit**

```bash
git add db/lifecycle_states.py db/schema.py tests/test_lifecycle_states.py
git commit -m "feat(db): domicilio lifecycle_states — serialización Plan/estado + tabla + helpers"
```

---

### Task 4: gate — `GET /plan/derive/{symbol}` + `POST /plan/confirm`

**Files:**
- Create: `api/plan.py`
- Test: `tests/test_plan_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan_api.py
"""Tests de los endpoints del plan vivo (instrumento F3a). Spec §4/§6."""
import os
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.plan import router


def _app():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _zonas():
    return [{"tipo": "soporte", "precio_bajo": 94, "precio_alto": 96, "centro": 95,
             "toques": 3, "confluencia_redondo": []},
            {"tipo": "resistencia", "precio_bajo": 104, "precio_alto": 106, "centro": 105,
             "toques": 3, "confluencia_redondo": []}]


def test_derive_devuelve_plan_sin_persistir():
    with patch("api.plan._zonas_now", return_value=_zonas()):
        r = _app().get("/plan/derive/BTCUSDT?entry_price=100")
    assert r.status_code == 200
    body = r.json()
    assert body["entry"] == 100.0
    assert [rg["tp_price"] for rg in body["rungs"]] == [105.0]
    assert body["sl_plan"] == 94.0 * (1 - 0.01)


def test_confirm_crea_la_fila(monkeypatch, tmp_path):
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(tmp_path / "d.db"))
    os.environ["MIGRATE_QTY_ALLOW_BULK_QUARANTINE"] = "1"
    try:
        from db.schema import init_db
        init_db()
    finally:
        os.environ.pop("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", None)
    with patch("api.plan._zonas_now", return_value=_zonas()), \
         patch("api.plan._caller_tenant", return_value=2):
        r = _app().post("/plan/confirm", json={"symbol": "BTCUSDT", "entry_price": 100.0})
    assert r.status_code == 200 and r.json()["estado_vivo"] == "activo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plan_api.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'api.plan'`

- [ ] **Step 3: Write minimal implementation**

```python
# api/plan.py
"""API del plan vivo (instrumento F3a) — gate (derive/confirm) + vista (pull).

PULL-ONLY: ningún endpoint emite push ni instrucción. Read-only sobre positions;
escribe solo lifecycle_states. La red (D.1) corre fuera de tx. Spec §4/§6/§9."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request

from screener.sr_levels import detect_levels
from instrument.plan import derive_plan
from db.lifecycle_states import db_put_state, db_get_active_state
from db.transaction import snapshot_connection, transaction

log = logging.getLogger("api.plan")
router = APIRouter(tags=["plan"])


def _zonas_now(symbol: str) -> list[dict]:
    """Zonas de D.1 con velas diarias hasta ahora (red, fuera de tx). Aislado
    para mockear; reutiliza el fetch del endpoint de niveles."""
    from api.levels import _fetch_daily_bars
    return detect_levels(_fetch_daily_bars(symbol))


def _caller_tenant(request: Request) -> int:
    """tenant_id del caller (mismo mecanismo que el resto de la API per-tenant)."""
    return int(getattr(request.state, "tenant_id", 0) or 0)


def _plan_payload(plan) -> dict:
    return {"entry": plan.entry_price,
            "sl_plan": plan.sl_price,
            "rungs": [{"tp_price": r.tp_price, "size_frac": r.size_frac} for r in plan.rungs],
            "runner_frac": plan.runner_frac,
            "entry_zone": plan.entry_zone}


@router.get("/plan/derive/{symbol}", summary="Deriva el plan desde D.1 (NO persiste)")
def derive(symbol: str, entry_price: float = Query(...)) -> dict:
    """El operador revisa el plan antes de confirmarlo. NO escribe nada."""
    zonas = _zonas_now(symbol.upper())
    return _plan_payload(derive_plan(zonas, entry_price))


@router.post("/plan/confirm", summary="Confirma el plan revisado → crea la fila viva")
def confirm(payload: dict, request: Request) -> dict:
    """El operador confirma en frío (su juicio + fundamentales entran aquí). Crea
    la fila lifecycle_states. La red (D.1) corre fuera de la tx corta del insert."""
    symbol = str(payload["symbol"]).upper()
    entry_price = float(payload["entry_price"])
    position_id = payload.get("position_id")
    tenant_id = _caller_tenant(request)

    zonas = _zonas_now(symbol)                       # red, fuera de tx
    plan = derive_plan(zonas, entry_price)
    from instrument.lifecycle import LifecycleState
    state = LifecycleState(plan_id=0, fase="CONFIRMED", sl_actual=plan.sl_price)
    now = datetime.now(timezone.utc).isoformat()

    with transaction() as con:
        db_put_state(con, position_id=position_id, symbol=symbol, tenant_id=tenant_id,
                     estado_vivo="activo", plan=plan, state=state, entry_price=entry_price,
                     qty_original=None, events=[], prev_observed=[], prev_qty=None,
                     confirmed_at=now, updated_at=now)
    return {"symbol": symbol, "estado_vivo": "activo", "plan": _plan_payload(plan)}
```

> **Nota sobre `_caller_tenant`:** el test lo mockea. En runtime debe derivar el tenant del request igual que el resto de la API per-tenant (revisá cómo `api/positions.py` u otro endpoint per-tenant obtiene `tenant_id` del request/JWT y replicá ese mecanismo exacto en `_caller_tenant` en vez del placeholder `request.state.tenant_id`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_plan_api.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add api/plan.py tests/test_plan_api.py
git commit -m "feat(api): gate del plan vivo — GET /plan/derive (preview) + POST /plan/confirm"
```

---

### Task 5: vista `GET /plan/{symbol}` (pull, anti-imperativo) + registrar router

**Files:**
- Modify: `api/plan.py`
- Modify: `btc_api.py` (registrar el router, junto a los demás)
- Test: `tests/test_plan_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan_api.py — añadir
from api.plan import construir_hechos


def test_hechos_no_contienen_imperativos():
    # los hechos afirman lo que ES, nunca instruyen.
    hechos = construir_hechos(rungs_llenos=[0], be_movido=True, estado_vivo="activo",
                              sl_actual=100.0, sl_plan=93.06)
    texto = " ".join(hechos).lower()
    for imperativo in ("mové", "movete", "cerrá", "vendé", "comprá", "mueve", "cierra"):
        assert imperativo not in texto


def test_hechos_reportan_tp1_y_be():
    hechos = construir_hechos(rungs_llenos=[0], be_movido=True, estado_vivo="activo",
                              sl_actual=100.0, sl_plan=93.06)
    texto = " ".join(hechos).lower()
    assert "tp1" in texto and "break-even" in texto


def test_vista_router_registrado():
    import btc_api
    rutas = {getattr(r, "path", None) for r in btc_api.app.routes}
    assert "/plan/{symbol}" in rutas
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plan_api.py -k "hechos or router" -v`
Expected: FAIL (`construir_hechos` no existe / ruta no registrada)

- [ ] **Step 3: Write minimal implementation**

En `api/plan.py`, añadir el constructor de hechos y la vista:

```python
def construir_hechos(*, rungs_llenos: list, be_movido: bool, estado_vivo: str,
                     sl_actual, sl_plan) -> list[str]:
    """HECHOS de 𝓕ₜ — lo que ES verdad. NUNCA instrucciones (Axiom-0, spec §0).
    Sin imperativos: el instrumento queda fuera del término que mide."""
    hechos: list[str] = []
    if estado_vivo == "incierto":
        hechos.append("transición sin confirmar — revisá en Binance")
    for i in sorted(rungs_llenos):
        hechos.append(f"TP{i + 1} se llenó")
    if be_movido:
        hechos.append("tu SL está en break-even")
    elif sl_actual is not None and sl_plan is not None:
        if sl_actual <= sl_plan * (1 + 1e-9):
            hechos.append("tu SL sigue debajo de la zona")
        else:
            hechos.append("tu SL está por encima del nivel del plan")
    return hechos


@router.get("/plan/{symbol}", summary="Estado vivo del plan (pull, solo hechos)")
def vista(symbol: str, request: Request) -> dict:
    """PULL: el estado vivo. Hechos, nunca instrucciones. Si no hay plan activo,
    estado_vivo None (la UI muestra 'sin plan')."""
    symbol = symbol.upper()
    tenant_id = _caller_tenant(request)
    with snapshot_connection() as con:
        con.row_factory = __import__("sqlite3").Row
        row = db_get_active_state(con, tenant_id=tenant_id, symbol=symbol)
    if row is None:
        return {"symbol": symbol, "estado_vivo": None}
    import json
    from db.lifecycle_states import plan_from_json
    plan = plan_from_json(row["plan_json"])
    rungs_llenos = json.loads(row["rungs_llenos_json"])
    hechos = construir_hechos(rungs_llenos=rungs_llenos, be_movido=bool(row["be_movido"]),
                              estado_vivo=row["estado_vivo"], sl_actual=row["sl_actual"],
                              sl_plan=plan.sl_price)
    return {
        "symbol": symbol, "estado_vivo": row["estado_vivo"],
        "plan": _plan_payload(plan),
        "realidad": {"fase": row["fase"], "rungs_llenos": rungs_llenos,
                     "sl_actual": row["sl_actual"], "be_movido": bool(row["be_movido"]),
                     "size_restante_frac": row["size_restante_frac"]},
        "hechos": hechos,
    }
```

En `btc_api.py`, tras `from api.levels import router as levels_router`:
```python
from api.plan import router as plan_router
```
Y tras `app.include_router(levels_router)`:
```python
app.include_router(plan_router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_plan_api.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add api/plan.py btc_api.py tests/test_plan_api.py
git commit -m "feat(api): vista pull del plan vivo (solo hechos, anti-imperativo) + registrar router"
```

---

### Task 6: conducta al cierre — `finalize_conduct` (puro)

**Files:**
- Modify: `instrument/tracker.py`
- Test: `tests/test_instrument_tracker.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_instrument_tracker.py — añadir
from instrument.tracker import finalize_conduct


def test_finalize_conduct_al_cierre():
    p = _plan()
    # secuencia de eventos vivos: TP1, SL→BE, STOP_HIT
    events = [
        {"tipo": "RUNG_FILLED", "order_id": "11", "rung_index": 0, "procedencia": "observado"},
        {"tipo": "SL_MOVED", "nuevo_sl": p.entry_price, "procedencia": "observado"},
        {"tipo": "STOP_HIT", "procedencia": "observado"},
    ]
    from instrument.lifecycle import LifecycleState, step
    st = LifecycleState(plan_id=0, fase="CONFIRMED", sl_actual=p.sl_price)
    for e in events:
        st = step(st, e, p)
    c = finalize_conduct(p, events, st, entry_price=100.0,
                         entry_ts="2026-06-10T00:00:00+00:00",
                         exit_ts="2026-06-12T00:00:00+00:00")
    assert c["adherencia_be"] is True
    assert c["rungs_honrados"] == 1
    assert c["procedencia"] == "observado"
    assert "hold_hours" in c
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_instrument_tracker.py -k finalize -v`
Expected: FAIL `ImportError: cannot import name 'finalize_conduct'`

- [ ] **Step 3: Write minimal implementation**

```python
# instrument/tracker.py — añadir al final
from instrument.conduct import compute_conduct


def finalize_conduct(plan, events: list[dict], final_state, *, entry_price: float,
                     entry_ts: str, exit_ts: str) -> dict:
    """Conducta al cierre con el libro de fills vivo (la comparación que F2
    difirió). Campo por campo contra el plan, procedencia 'observado'. NO PnL.
    Spec §7."""
    return compute_conduct(plan, events, final_state, entry_price=entry_price,
                           entry_ts=entry_ts, exit_ts=exit_ts, procedencia="observado")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_instrument_tracker.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add instrument/tracker.py tests/test_instrument_tracker.py
git commit -m "feat(instrument): finalize_conduct — conducta al cierre con fills vivos (sin PnL)"
```

---

### Task 7: el hook de I/O — correr el tracker tras el sync

**Files:**
- Modify: `tools/sync_binance_spot.py` (añadir el paso del tracker tras el reconcile)
- Test: `network`-marcado (smoke); la lógica pura ya está cubierta por Tasks 1/2/6

**Context:** Read `tools/sync_binance_spot.py::sync_tenant` (~line 47). It runs the read-only reconcile + applies `observed_orders` outside any tx, then short tx writes. ADD a step AFTER that: for each active `lifecycle_states` row of the tenant, read the position's current qty (read-only) + the fresh `observed_orders` for that symbol, call `advance_live`, and persist via `db_update_state`. When `advance_live` returns a CLOSED state, call `finalize_conduct` and persist the episode via `db_put_episode` (from `db/conduct_episodes.py`), and set `estado_vivo='cerrado'`. ALL read-only on positions; the only writes are `lifecycle_states` + `conduct_episodes`. Network (Binance reads) stays OUTSIDE any transaction.

- [ ] **Step 1: Write the wiring**

Add a function `track_live(tenant_id)` to `tools/sync_binance_spot.py` and call it at the end of `sync_tenant` (after the reconcile writes). Use the existing helpers to read `observed_orders` for a symbol (read how `apply_observed_orders` / the v0.3 read path queries them, and mirror the read). Structure:

```python
def track_live(tenant_id: int) -> dict:
    """Tras el sync: avanza el estado vivo de cada plan activo desde los
    observed_orders frescos + la qty real. Read-only sobre positions; escribe
    lifecycle_states + (al cierre) conduct_episodes. Sin push, sin PositionClosure."""
    import json
    import sqlite3
    from datetime import datetime, timezone

    from db.transaction import snapshot_connection, transaction
    from db.lifecycle_states import (
        db_list_active, db_update_state, plan_from_json, state_from_row,
    )
    from db.conduct_episodes import db_put_episode
    from instrument.tracker import advance_live, finalize_conduct

    now = datetime.now(timezone.utc).isoformat()
    with snapshot_connection() as con:
        con.row_factory = sqlite3.Row
        activos = [dict(r) for r in db_list_active(con, tenant_id=tenant_id)]

    avanzados = cerrados = 0
    for row in activos:
        symbol = row["symbol"]
        with snapshot_connection() as con:
            con.row_factory = sqlite3.Row
            pos = con.execute(
                "SELECT qty FROM positions WHERE symbol=? AND tenant_id=? "
                "AND status='open' AND control_domain='EXTERNAL' LIMIT 1",
                (symbol, tenant_id)).fetchone()
            obs_rows = con.execute(
                "SELECT kind, price, qty, order_id FROM observed_orders "
                "WHERE tenant_id=? AND symbol=?", (tenant_id, symbol)).fetchall()
        if pos is None:
            continue
        curr_qty = float(pos["qty"] or 0.0)
        curr_observed = [dict(o) for o in obs_rows]
        plan = plan_from_json(row["plan_json"])
        state = state_from_row(row)
        prev_observed = json.loads(row["prev_observed_json"] or "[]")
        prev_qty = row["prev_qty"] if row["prev_qty"] is not None else curr_qty
        prev_events = json.loads(row["events_json"] or "[]")

        new_state, new_events = advance_live(plan, state, prev_observed, curr_observed,
                                             prev_qty, curr_qty)
        all_events = prev_events + new_events
        if new_state.fase == "CLOSED":
            conduct = finalize_conduct(plan, all_events, new_state,
                                       entry_price=row["entry_price"],
                                       entry_ts=row["confirmed_at"], exit_ts=now)
            with transaction() as con:
                db_put_episode(con, position_id=row["position_id"], symbol=symbol,
                               tenant_id=tenant_id, entry_ts=row["confirmed_at"],
                               exit_ts=now, conduct=conduct, plan_json=row["plan_json"],
                               reproduced=True, created_ts=now)
                db_update_state(con, row_id=row["id"], estado_vivo="cerrado",
                                state=new_state, events=all_events,
                                prev_observed=curr_observed, prev_qty=curr_qty, updated_at=now)
            cerrados += 1
        else:
            with transaction() as con:
                db_update_state(con, row_id=row["id"], estado_vivo=row["estado_vivo"],
                                state=new_state, events=all_events,
                                prev_observed=curr_observed, prev_qty=curr_qty, updated_at=now)
            avanzados += 1
    return {"avanzados": avanzados, "cerrados": cerrados}
```

Then call `track_live(tenant_id)` at the end of `sync_tenant` (after the reconcile block) and include its result in `sync_tenant`'s returned report dict (e.g. `report["track_live"] = track_live(tenant_id)`). Read the end of `sync_tenant` and wire it in following the existing return-dict shape.

- [ ] **Step 2: Add a `network`-marked smoke test**

```python
# tests/test_instrument_tracker.py — añadir
import pytest


@pytest.mark.network
def test_track_live_smoke():
    # corre sobre el tenant real; no asierta valores vivos, solo que no revienta.
    from tools.sync_binance_spot import track_live
    res = track_live(2)
    assert "avanzados" in res and "cerrados" in res
```

- [ ] **Step 3: Run the pure tests + fast gate**

Run: `python -m pytest tests/test_instrument_tracker.py tests/test_lifecycle_states.py tests/test_plan_api.py -m "not network" -v`
Expected: green.

Run: `python -m pytest tests/ -m "not network" -n auto -q`
Expected: no regressions.

- [ ] **Step 4: Commit**

```bash
git add tools/sync_binance_spot.py tests/test_instrument_tracker.py
git commit -m "feat(instrument): track_live — el tracker corre tras el sync (read-only, conducta al cierre)"
```

---

## Verificación final

- [ ] **Puros + DB + endpoints:** `python -m pytest tests/test_instrument_tracker.py tests/test_lifecycle_states.py tests/test_plan_api.py -m "not network" -v` → todo verde.
- [ ] **Gate rápido (CI):** `python -m pytest tests/ -m "not network" -n auto -q` → sin regresiones.
- [ ] **Anti-imperativo:** confirmar que el test `test_hechos_no_contienen_imperativos` pasa — la vista nunca instruye.
- [ ] **Frontera dura:** grep de `api/plan.py`, `instrument/tracker.py`, `tools/sync_binance_spot.py` (el bloque nuevo) → cero `PositionClosure`, cero `UPDATE positions`, cero push/notify. Solo escribe `lifecycle_states` + `conduct_episodes`.
- [ ] **Vivo (deliberado, red + DB):** con el API corriendo, `POST /plan/confirm` un símbolo de papá, correr el sync, y `GET /plan/{symbol}` → ver el estado vivo con hechos. Ese es el entregable de F3a: el instrumento acompañando una posición real, en pull, sin instruir.
