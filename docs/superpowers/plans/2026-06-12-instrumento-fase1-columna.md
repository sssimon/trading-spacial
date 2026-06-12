# El Instrumento — Fase 1 (la columna, falsada contra lo real) · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir la columna pura del instrumento (derivación del plan desde D.1 + máquina de estados del lifecycle + campos de conducta) y falsarla contra las posiciones reales ya cerradas del operador — todo read-only, sin tocar el path de cierre.

**Architecture:** Tres módulos puros (`instrument/plan.py`, `instrument/lifecycle.py`, `instrument/conduct.py`) sin red ni DB, hermanos de `screener/sr_levels.py`. Una tabla nueva `conduct_episodes` (ledger de conducta, escrita solo por el arnés) con helpers SQL. Un arnés `tools/lifecycle_falsifier.py` que lee posiciones cerradas reales, reconstruye las zonas de D.1 al momento de la entrada, deriva el plan, re-juega la máquina, computa la conducta, persiste el episodio y reporta honestamente qué pudo reproducir.

**Tech Stack:** Python 3.12 (`dataclasses`, stdlib), sqlite3 vía los helpers del proyecto, `screener.sr_levels` (D.1), `data/providers/binance.py` para velas históricas. pytest.

**Spec:** `docs/superpowers/specs/es/2026-06-12-instrumento-lifecycle-conducta-design.md` (Fase 1 = §3 F1, §4–§8).

**Branch:** `feat/instrumento-lifecycle-conducta` (ya creada).

**Frontera dura (toda la Fase 1):** cero escritura a `positions.status`, cero `PositionClosure`, cero vivo/`CONDUCTING`. El reductor y la derivación son puros. La única escritura es a la tabla NUEVA `conduct_episodes`.

---

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `instrument/__init__.py` (crear) | Paquete. |
| `instrument/plan.py` (crear) | `derive_plan(zonas, entry_price) → Plan`. Puro. Dataclasses `Plan`, `Rung`. |
| `instrument/lifecycle.py` (crear) | `LifecycleState` + `step(state, event, plan) → state`. Reductor puro, idempotente por `order_id`. |
| `instrument/conduct.py` (crear) | `compute_conduct(plan, events, final_state, ctx) → dict`. Puro. Campos `i`. |
| `db/conduct_episodes.py` (crear) | Helpers SQL puros (reciben `con`): `db_put_episode`, `db_get_episodes`. |
| `db/schema.py` (modificar) | Migración idempotente: tabla `conduct_episodes`. |
| `tools/lifecycle_falsifier.py` (crear) | Arnés read-only sobre posiciones reales. I/O de red aquí (no en los módulos puros). |
| `tests/test_instrument_plan.py`, `tests/test_instrument_lifecycle.py`, `tests/test_instrument_conduct.py`, `tests/test_conduct_episodes.py`, `tests/test_lifecycle_falsifier.py` (crear) | Tests. |

---

### Task 1: `instrument/plan.py` — derivación del plan

**Files:**
- Create: `instrument/__init__.py` (vacío), `instrument/plan.py`
- Test: `tests/test_instrument_plan.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_instrument_plan.py
"""Tests de derive_plan (instrumento, Fase 1). Puro: sin red, sin DB. Spec §4."""
from instrument.plan import derive_plan, Plan, Rung


def _z(tipo, bajo, alto, centro):
    return {"tipo": tipo, "precio_bajo": bajo, "precio_alto": alto,
            "centro": centro, "toques": 3, "confluencia_redondo": []}


def _zonas_4_resistencias():
    # entry 100: soporte abajo, 4 resistencias arriba
    return [
        _z("soporte", 94, 96, 95),
        _z("resistencia", 104, 106, 105),
        _z("resistencia", 109, 111, 110),
        _z("resistencia", 114, 116, 115),
        _z("resistencia", 119, 121, 120),
    ]


def test_sl_bajo_el_soporte_con_margen():
    p = derive_plan(_zonas_4_resistencias(), entry_price=100.0)
    # SL = precio_bajo del soporte (94) menos 1% de margen
    assert p.sl_price == 94.0 * (1 - 0.01)


def test_escalera_son_las_resistencias_ascendentes_cap_4():
    p = derive_plan(_zonas_4_resistencias(), entry_price=100.0)
    assert [r.tp_price for r in p.rungs] == [105.0, 110.0, 115.0, 120.0]


def test_tamanos_frontloaded_tp1_min_50_y_suman_uno_con_runner():
    p = derive_plan(_zonas_4_resistencias(), entry_price=100.0)
    assert p.rungs[0].size_frac >= 0.50
    total = sum(r.size_frac for r in p.rungs) + p.runner_frac
    assert abs(total - 1.0) < 1e-9


def test_menos_de_4_resistencias_trunca_y_renormaliza():
    zonas = [_z("soporte", 94, 96, 95),
             _z("resistencia", 104, 106, 105),
             _z("resistencia", 109, 111, 110)]
    p = derive_plan(zonas, entry_price=100.0)
    assert len(p.rungs) == 2
    total = sum(r.size_frac for r in p.rungs) + p.runner_frac
    assert abs(total - 1.0) < 1e-9
    assert p.rungs[0].size_frac >= 0.50


def test_runner_desactivado_reparte_todo_en_la_escalera():
    p = derive_plan(_zonas_4_resistencias(), entry_price=100.0, runner_on=False)
    assert p.runner_frac == 0.0
    assert abs(sum(r.size_frac for r in p.rungs) - 1.0) < 1e-9


def test_entry_zone_es_el_soporte_que_contiene_al_entry():
    zonas = [_z("soporte", 99, 101, 100), _z("resistencia", 104, 106, 105)]
    p = derive_plan(zonas, entry_price=100.0)
    assert p.entry_zone is not None
    assert p.entry_zone["centro"] == 100.0


def test_sin_resistencias_todo_es_runner():
    zonas = [_z("soporte", 94, 96, 95)]
    p = derive_plan(zonas, entry_price=100.0)
    assert p.rungs == []
    assert p.runner_frac == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_instrument_plan.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'instrument'`

- [ ] **Step 3: Write minimal implementation**

Create `instrument/__init__.py` (empty file). Then `instrument/plan.py`:

```python
"""Derivación del plan del instrumento (Fase 1) — puro, sin red, sin DB.

Dado las zonas de D.1 (screener.sr_levels.detect_levels) + el precio de entrada,
produce un Plan inmutable: SL bajo el soporte, escalera de TPs en las resistencias,
runner OPEN TARGET, regla break-even tras TP1. El plan es DISCIPLINADO, no afirma
rentabilidad (spec §1, §4). Hermano de screener/sr_levels.py."""
from __future__ import annotations

from dataclasses import dataclass

# ── Constantes de arranque (calibrables, spec §4) ───────────────────────────
MAX_RUNGS = 4
SL_MARGIN_PCT = 0.01               # colchón bajo el borde del soporte
RUNNER_ON = True                   # reserva la fracción OPEN TARGET
SIZE_SCHEDULE = [0.50, 0.20, 0.15, 0.10]  # front-loaded; el resto va al runner
RUNNER_FRAC = 0.05


@dataclass(frozen=True)
class Rung:
    tp_price: float
    size_frac: float
    zona_origen: dict


@dataclass(frozen=True)
class Plan:
    entry_price: float
    entry_zone: dict | None
    sl_price: float
    rungs: list   # list[Rung], ascendente por tp_price
    runner_frac: float


def derive_plan(zonas: list[dict], entry_price: float, *,
                runner_on: bool = RUNNER_ON) -> Plan:
    """Deriva el Plan desde las zonas de D.1. Las resistencias sobre el entry son
    los TPs (cap MAX_RUNGS); el soporte inmediato fija el SL; los tamaños van
    front-loaded con TP1 ≥ 50%; el runner queda abierto (spec §4)."""
    resistencias = sorted(
        [z for z in zonas if z["tipo"] == "resistencia" and z["centro"] > entry_price],
        key=lambda z: z["centro"],
    )[:MAX_RUNGS]

    soportes_abajo = [z for z in zonas
                      if z["tipo"] == "soporte" and z["precio_alto"] < entry_price]
    soporte = max(soportes_abajo, key=lambda z: z["centro"]) if soportes_abajo else None

    entry_zone = next(
        (z for z in zonas if z["tipo"] == "soporte"
         and z["precio_bajo"] <= entry_price <= z["precio_alto"]),
        None,
    )

    base = soporte["precio_bajo"] if soporte is not None else entry_price
    sl_price = base * (1 - SL_MARGIN_PCT)

    runner = RUNNER_FRAC if runner_on else 0.0
    n = len(resistencias)
    if n == 0:
        return Plan(entry_price=entry_price, entry_zone=entry_zone, sl_price=sl_price,
                    rungs=[], runner_frac=(1.0 if runner_on else 0.0))

    fracs = SIZE_SCHEDULE[:n]
    total = sum(fracs)
    scaled = [f / total * (1 - runner) for f in fracs]
    rungs = [Rung(tp_price=z["centro"], size_frac=s, zona_origen=z)
             for z, s in zip(resistencias, scaled)]
    return Plan(entry_price=entry_price, entry_zone=entry_zone, sl_price=sl_price,
                rungs=rungs, runner_frac=runner)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_instrument_plan.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add instrument/__init__.py instrument/plan.py tests/test_instrument_plan.py
git commit -m "feat(instrument): derive_plan — escalera de TPs en resistencias D.1 + SL bajo soporte + runner"
```

---

### Task 2: `instrument/lifecycle.py` — máquina de estados pura

**Files:**
- Create: `instrument/lifecycle.py`
- Test: `tests/test_instrument_lifecycle.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_instrument_lifecycle.py
"""Tests del reductor del lifecycle (instrumento, Fase 1). Puro. Spec §5."""
from instrument.lifecycle import LifecycleState, step
from instrument.plan import derive_plan


def _z(tipo, bajo, alto, centro):
    return {"tipo": tipo, "precio_bajo": bajo, "precio_alto": alto,
            "centro": centro, "toques": 3, "confluencia_redondo": []}


def _plan():
    zonas = [_z("soporte", 94, 96, 95),
             _z("resistencia", 104, 106, 105),
             _z("resistencia", 109, 111, 110)]
    return derive_plan(zonas, entry_price=100.0)


def _s0():
    return LifecycleState(plan_id=1)


def test_confirmar_pasa_a_confirmed():
    s = step(_s0(), {"tipo": "PLAN_CONFIRMED", "procedencia": "declarado"}, _plan())
    assert s.fase == "CONFIRMED"


def test_rung_filled_marca_y_resta_size():
    p = _plan()
    s = step(_s0(), {"tipo": "PLAN_CONFIRMED", "procedencia": "observado"}, p)
    s = step(s, {"tipo": "RUNG_FILLED", "order_id": "A", "rung_index": 0,
                 "procedencia": "observado"}, p)
    assert s.fase == "RUNNING"
    assert 0 in s.rungs_llenos
    assert "A" in s.consumed_order_ids
    assert abs(s.size_restante_frac - (1.0 - p.rungs[0].size_frac)) < 1e-9


def test_rung_filled_es_idempotente_por_order_id():
    p = _plan()
    s = step(_s0(), {"tipo": "PLAN_CONFIRMED", "procedencia": "observado"}, p)
    e = {"tipo": "RUNG_FILLED", "order_id": "A", "rung_index": 0, "procedencia": "observado"}
    s1 = step(s, e, p)
    s2 = step(s1, e, p)   # mismo order_id otra vez
    assert s2.size_restante_frac == s1.size_restante_frac   # no se contó dos veces
    assert s2.rungs_llenos == s1.rungs_llenos


def test_sl_movido_a_entry_marca_break_even():
    p = _plan()
    s = step(_s0(), {"tipo": "SL_MOVED", "nuevo_sl": p.entry_price,
                     "procedencia": "observado"}, p)
    assert s.be_movido is True


def test_stop_hit_tras_be_cierra_como_be_hit():
    p = _plan()
    s = step(_s0(), {"tipo": "SL_MOVED", "nuevo_sl": p.entry_price, "procedencia": "observado"}, p)
    s = step(s, {"tipo": "STOP_HIT", "procedencia": "observado"}, p)
    assert s.fase == "CLOSED"
    assert s.close_reason == "BE_HIT"


def test_stop_hit_sin_be_cierra_como_sl_hit():
    p = _plan()
    s = step(_s0(), {"tipo": "STOP_HIT", "procedencia": "observado"}, p)
    assert s.close_reason == "SL_HIT"


def test_manual_exit_cierra_fuera_de_plan():
    s = step(_s0(), {"tipo": "MANUAL_EXIT", "procedencia": "declarado"}, _plan())
    assert s.fase == "CLOSED" and s.close_reason == "MANUAL"


def test_eventos_tras_closed_son_noop():
    p = _plan()
    s = step(_s0(), {"tipo": "MANUAL_EXIT", "procedencia": "declarado"}, p)
    s2 = step(s, {"tipo": "RUNG_FILLED", "order_id": "B", "rung_index": 0,
                  "procedencia": "observado"}, p)
    assert s2 == s   # terminal
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_instrument_lifecycle.py -v`
Expected: FAIL `ImportError: cannot import name 'LifecycleState'`

- [ ] **Step 3: Write minimal implementation**

```python
# instrument/lifecycle.py
"""Máquina de estados del lifecycle (instrumento, Fase 1) — reductor PURO.

step(estado, evento, plan) → estado. Idempotente por order_id (un RUNG_FILLED
repetido es no-op). NO llama a Binance, NO escribe positions.status, NO toca
PositionClosure — el → CLOSED es del estado del PLAN, no del cierre real de la
posición (spec §5). Cada evento lleva procedencia 'observado'|'declarado'."""
from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class LifecycleState:
    plan_id: int
    fase: str = "PLANNED"                       # PLANNED|CONFIRMED|RUNNING|CLOSED
    rungs_llenos: frozenset = field(default_factory=frozenset)
    consumed_order_ids: frozenset = field(default_factory=frozenset)
    sl_actual: float = 0.0
    be_movido: bool = False
    size_restante_frac: float = 1.0
    close_reason: str | None = None


def step(state: LifecycleState, event: dict, plan) -> LifecycleState:
    """Aplica un evento. Estado terminal CLOSED: todo evento es no-op."""
    if state.fase == "CLOSED":
        return state
    tipo = event["tipo"]

    if tipo == "PLAN_CONFIRMED":
        return state if state.fase != "PLANNED" else replace(state, fase="CONFIRMED")

    if tipo == "RUNG_FILLED":
        oid = event["order_id"]
        if oid in state.consumed_order_ids:
            return state   # idempotencia por order_id
        i = event["rung_index"]
        frac = plan.rungs[i].size_frac if 0 <= i < len(plan.rungs) else 0.0
        return replace(
            state, fase="RUNNING",
            rungs_llenos=state.rungs_llenos | {i},
            consumed_order_ids=state.consumed_order_ids | {oid},
            size_restante_frac=max(0.0, state.size_restante_frac - frac),
        )

    if tipo == "SL_MOVED":
        nuevo = event["nuevo_sl"]
        return replace(state, sl_actual=nuevo,
                       be_movido=state.be_movido or (nuevo == plan.entry_price))

    if tipo == "STOP_HIT":
        return replace(state, fase="CLOSED",
                       close_reason="BE_HIT" if state.be_movido else "SL_HIT")

    if tipo == "MANUAL_EXIT":
        return replace(state, fase="CLOSED", close_reason="MANUAL")

    if tipo == "POSITION_GONE":
        return replace(state, fase="CLOSED", close_reason="RECONCILED")

    return state   # evento desconocido: ignorado
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_instrument_lifecycle.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add instrument/lifecycle.py tests/test_instrument_lifecycle.py
git commit -m "feat(instrument): reductor puro del lifecycle (idempotente por order_id, → CLOSED del plan)"
```

---

### Task 3: `instrument/conduct.py` — campos de conducta `i`

**Files:**
- Create: `instrument/conduct.py`
- Test: `tests/test_instrument_conduct.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_instrument_conduct.py
"""Tests de compute_conduct (instrumento, Fase 1). Puro. Spec §7.

La conducta es INDEPENDIENTE del PnL: mide adherencia al plan, no si ganó."""
from instrument.plan import derive_plan
from instrument.lifecycle import LifecycleState, step
from instrument.conduct import compute_conduct


def _z(tipo, bajo, alto, centro):
    return {"tipo": tipo, "precio_bajo": bajo, "precio_alto": alto,
            "centro": centro, "toques": 3, "confluencia_redondo": []}


def _plan(entry=100.0):
    zonas = [_z("soporte", 99, 101, 100), _z("resistencia", 104, 106, 105),
             _z("resistencia", 109, 111, 110)]
    return derive_plan(zonas, entry_price=entry)


def _replay(plan, events):
    s = LifecycleState(plan_id=1)
    for e in events:
        s = step(s, e, plan)
    return s


def test_conducta_perfecta_aguanto_y_movio_be():
    p = _plan()
    events = [
        {"tipo": "PLAN_CONFIRMED", "procedencia": "observado"},
        {"tipo": "RUNG_FILLED", "order_id": "A", "rung_index": 0, "procedencia": "observado"},
        {"tipo": "SL_MOVED", "nuevo_sl": p.entry_price, "procedencia": "observado"},
        {"tipo": "RUNG_FILLED", "order_id": "B", "rung_index": 1, "procedencia": "observado"},
        {"tipo": "STOP_HIT", "procedencia": "observado"},
    ]
    fs = _replay(p, events)
    c = compute_conduct(p, events, fs, entry_price=100.0,
                        entry_ts="2026-01-01T00:00:00+00:00",
                        exit_ts="2026-01-03T00:00:00+00:00", procedencia="observado")
    assert c["adherencia_be"] is True
    assert c["rungs_honrados"] == 2
    assert c["cierre_en_plan"] is True
    assert c["sl_respetado"] is True
    assert c["hold_hours"] == 48.0
    assert c["procedencia"] == "observado"


def test_panico_salida_unica_antes_de_tp1():
    p = _plan()
    events = [
        {"tipo": "PLAN_CONFIRMED", "procedencia": "declarado"},
        {"tipo": "MANUAL_EXIT", "procedencia": "declarado"},
    ]
    fs = _replay(p, events)
    c = compute_conduct(p, events, fs, entry_price=100.0,
                        entry_ts="2026-01-01T00:00:00+00:00",
                        exit_ts="2026-01-01T02:00:00+00:00", procedencia="declarado")
    assert c["cierre_en_plan"] is False      # MANUAL fuera de plan
    assert c["rungs_honrados"] == 0
    assert c["escalono"] is False            # salida única sin escalonar
    assert c["adherencia_be"] is None        # TP1 nunca tocado → N/A


def test_ensanchar_sl_marca_sl_no_respetado():
    p = _plan()
    events = [
        {"tipo": "PLAN_CONFIRMED", "procedencia": "observado"},
        {"tipo": "SL_MOVED", "nuevo_sl": p.sl_price - 5.0, "procedencia": "observado"},  # ensancha
        {"tipo": "STOP_HIT", "procedencia": "observado"},
    ]
    fs = _replay(p, events)
    c = compute_conduct(p, events, fs, entry_price=100.0,
                        entry_ts="2026-01-01T00:00:00+00:00",
                        exit_ts="2026-01-01T05:00:00+00:00", procedencia="observado")
    assert c["sl_respetado"] is False


def test_entry_fuera_de_zona():
    p = _plan(entry=103.0)   # entry sobre el soporte [99,101] → fuera
    events = [{"tipo": "PLAN_CONFIRMED", "procedencia": "observado"},
              {"tipo": "MANUAL_EXIT", "procedencia": "observado"}]
    fs = _replay(p, events)
    c = compute_conduct(p, events, fs, entry_price=103.0,
                        entry_ts="2026-01-01T00:00:00+00:00",
                        exit_ts="2026-01-01T01:00:00+00:00", procedencia="observado")
    assert c["entry_en_zona"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_instrument_conduct.py -v`
Expected: FAIL `ImportError: cannot import name 'compute_conduct'`

- [ ] **Step 3: Write minimal implementation**

```python
# instrument/conduct.py
"""Campos de conducta `i` del instrumento (Fase 1) — puro. Spec §7.

Compara la secuencia realizada contra el plan confirmado. La medición es
INDEPENDIENTE del PnL: mide si honraste la ley que aprobaste, no si ganaste.
SIN score único (no-mezcla de tipos, INV-7). Cada episodio lleva procedencia."""
from __future__ import annotations

from datetime import datetime


def _hours_between(a_iso: str, b_iso: str) -> float:
    a = datetime.fromisoformat(a_iso.replace("Z", "+00:00"))
    b = datetime.fromisoformat(b_iso.replace("Z", "+00:00"))
    return (b - a).total_seconds() / 3600.0


def compute_conduct(plan, events: list[dict], final_state, *, entry_price: float,
                    entry_ts: str, exit_ts: str, procedencia: str) -> dict:
    """Deriva los campos de conducta del episodio cerrado (spec §7)."""
    entry_en_zona = (plan.entry_zone is not None
                     and plan.entry_zone["precio_bajo"] <= entry_price <= plan.entry_zone["precio_alto"])

    # sl_respetado: ¿algún SL_MOVED ensanchó el SL por debajo del plan?
    sl_widened = any(e["tipo"] == "SL_MOVED" and e["nuevo_sl"] < plan.sl_price
                     for e in events)
    sl_respetado = not sl_widened

    tp1_filled = 0 in final_state.rungs_llenos
    adherencia_be = bool(final_state.be_movido) if tp1_filled else None  # N/A si TP1 nunca tocó

    rungs_honrados = len(final_state.rungs_llenos)
    cierre_en_plan = final_state.close_reason != "MANUAL"
    # escalonó: cerró por TP/SL/runner, o tocó al menos un rung. Pánico = MANUAL sin rungs.
    escalono = cierre_en_plan or rungs_honrados > 0
    hold_hours = _hours_between(entry_ts, exit_ts)

    return {
        "entry_en_zona": entry_en_zona,
        "sl_respetado": sl_respetado,
        "adherencia_be": adherencia_be,
        "rungs_honrados": rungs_honrados,
        "escalono": escalono,
        "cierre_en_plan": cierre_en_plan,
        "hold_hours": hold_hours,
        "close_reason": final_state.close_reason,
        "procedencia": procedencia,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_instrument_conduct.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add instrument/conduct.py tests/test_instrument_conduct.py
git commit -m "feat(instrument): campos de conducta i (adherencia BE, sl respetado, escalonó, hold) — sin score"
```

---

### Task 4: tabla `conduct_episodes` + helpers SQL

**Files:**
- Modify: `db/schema.py` (añadir una migración idempotente, siguiendo el patrón de `observed_orders` / `project_dossiers` que ya existen)
- Create: `db/conduct_episodes.py`
- Test: `tests/test_conduct_episodes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_conduct_episodes.py
"""Tests de la tabla conduct_episodes + helpers (instrumento, Fase 1). Spec §8."""
import sqlite3

from db.conduct_episodes import db_put_episode, db_get_episodes


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE conduct_episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER, symbol TEXT NOT NULL, tenant_id INTEGER,
            entry_ts TEXT NOT NULL, exit_ts TEXT,
            procedencia TEXT NOT NULL,
            entry_en_zona INTEGER, sl_respetado INTEGER, adherencia_be INTEGER,
            rungs_honrados INTEGER, cierre_en_plan INTEGER, hold_hours REAL,
            close_reason TEXT, plan_json TEXT, reproduced INTEGER NOT NULL,
            created_ts TEXT NOT NULL
        )""")
    return con


def _conduct():
    return {"entry_en_zona": True, "sl_respetado": True, "adherencia_be": None,
            "rungs_honrados": 2, "cierre_en_plan": True, "hold_hours": 48.0,
            "close_reason": "SL_HIT", "procedencia": "observado"}


def test_put_y_get_roundtrip():
    con = _con()
    db_put_episode(con, position_id=7, symbol="BTCUSDT", tenant_id=2,
                   entry_ts="2026-01-01T00:00:00+00:00", exit_ts="2026-01-03T00:00:00+00:00",
                   conduct=_conduct(), plan_json="{}", reproduced=True,
                   created_ts="2026-06-12T00:00:00+00:00")
    rows = db_get_episodes(con, tenant_id=2)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["rungs_honrados"] == 2
    assert rows[0]["reproduced"] == 1


def test_adherencia_be_none_se_guarda_como_null():
    con = _con()
    db_put_episode(con, position_id=7, symbol="ETHUSDT", tenant_id=2,
                   entry_ts="2026-01-01T00:00:00+00:00", exit_ts=None,
                   conduct=_conduct(), plan_json="{}", reproduced=False,
                   created_ts="2026-06-12T00:00:00+00:00")
    rows = db_get_episodes(con, tenant_id=2)
    assert rows[0]["adherencia_be"] is None
    assert rows[0]["reproduced"] == 0


def test_get_filtra_por_tenant():
    con = _con()
    for t in (2, 3):
        db_put_episode(con, position_id=None, symbol="ADAUSDT", tenant_id=t,
                       entry_ts="2026-01-01T00:00:00+00:00", exit_ts=None,
                       conduct=_conduct(), plan_json="{}", reproduced=True,
                       created_ts="2026-06-12T00:00:00+00:00")
    assert len(db_get_episodes(con, tenant_id=2)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_conduct_episodes.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'db.conduct_episodes'`

- [ ] **Step 3: Write minimal implementation**

Create `db/conduct_episodes.py`:

```python
"""Helpers SQL puros del ledger de conducta (instrumento, Fase 1).

Reciben `con` (no abren transacción propia) — capa de helpers SQL del proyecto.
La tabla conduct_episodes guarda un EpisodioDeConducción REALIZED por posición
falsada: la conducta medida vs. el plan derivado, con su procedencia. Spec §8."""
from __future__ import annotations

import sqlite3


def _b(v):
    """bool|None → int|None para SQLite (preserva NULL)."""
    return None if v is None else int(bool(v))


def db_put_episode(con: sqlite3.Connection, *, position_id, symbol, tenant_id,
                   entry_ts, exit_ts, conduct: dict, plan_json: str,
                   reproduced: bool, created_ts: str) -> None:
    con.execute(
        """INSERT INTO conduct_episodes
           (position_id, symbol, tenant_id, entry_ts, exit_ts, procedencia,
            entry_en_zona, sl_respetado, adherencia_be, rungs_honrados,
            cierre_en_plan, hold_hours, close_reason, plan_json, reproduced, created_ts)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (position_id, symbol, tenant_id, entry_ts, exit_ts, conduct["procedencia"],
         _b(conduct["entry_en_zona"]), _b(conduct["sl_respetado"]),
         _b(conduct["adherencia_be"]), conduct["rungs_honrados"],
         _b(conduct["cierre_en_plan"]), conduct["hold_hours"],
         conduct["close_reason"], plan_json, int(bool(reproduced)), created_ts),
    )


def db_get_episodes(con: sqlite3.Connection, *, tenant_id: int) -> list:
    cur = con.execute(
        "SELECT * FROM conduct_episodes WHERE tenant_id = ? ORDER BY entry_ts",
        (tenant_id,),
    )
    return cur.fetchall()
```

Then in `db/schema.py`, add an idempotent migration. Find the function where tables are created (the one containing `CREATE TABLE IF NOT EXISTS observed_orders`, around line 1804) and add, following the exact same `CREATE TABLE IF NOT EXISTS` style, a new table creation in that same function (or a sibling `_migrate_*` function called from the same place — match the file's convention):

```python
    con.execute("""
        CREATE TABLE IF NOT EXISTS conduct_episodes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id     INTEGER,
            symbol          TEXT    NOT NULL,
            tenant_id       INTEGER,
            entry_ts        TEXT    NOT NULL,
            exit_ts         TEXT,
            procedencia     TEXT    NOT NULL,   -- 'observado' | 'declarado'
            entry_en_zona   INTEGER,
            sl_respetado    INTEGER,
            adherencia_be   INTEGER,            -- NULL si TP1 nunca tocó
            rungs_honrados  INTEGER,
            cierre_en_plan  INTEGER,
            hold_hours      REAL,
            close_reason    TEXT,
            plan_json       TEXT,
            reproduced      INTEGER NOT NULL,   -- ¿el arnés reprodujo esta posición? 0/1
            created_ts      TEXT    NOT NULL
        )
    """)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_conduct_episodes.py -v`
Expected: PASS (3 passed)

Then verify the schema migration applies cleanly:
Run: `python -c "from db.schema import init_db; import btc_api, os; os.environ['MIGRATE_QTY_ALLOW_BULK_QUARANTINE']='1'; btc_api.DB_FILE=':memory:'"` — if the project has a dedicated migration test, run the schema test instead: `python -m pytest tests/ -m "not network" -k "schema or migration" -q`
Expected: no migration errors.

- [ ] **Step 5: Commit**

```bash
git add db/conduct_episodes.py db/schema.py tests/test_conduct_episodes.py
git commit -m "feat(db): tabla conduct_episodes (ledger de conducta REALIZED) + helpers SQL"
```

---

### Task 5: arnés de falsación `tools/lifecycle_falsifier.py`

**Files:**
- Create: `tools/lifecycle_falsifier.py`
- Test: `tests/test_lifecycle_falsifier.py`

**Context for the implementer:** The harness reads real CLOSED positions (read-only), reconstructs D.1 zones as-of the entry date using historical daily klines (`data/providers/binance.py::BinanceAdapter.fetch_klines(symbol, "1d", start_ms, end_ms)` returns `Bar` objects with `.high`/`.low`), derives the plan, synthesizes a minimal event sequence from the recorded envelope (entry/exit/sl/tp/exit_reason — there is NO fill ledger yet, spec §6), replays it, computes conduct, and reports. The pure replay logic (`reproduce_position`) is unit-tested with injected data; the network fetch + DB read live in `main()` and are NOT unit-tested (they are I/O, run deliberately). Keep `reproduce_position` pure so it is testable without network or DB.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lifecycle_falsifier.py
"""Tests del arnés de falsación (instrumento, Fase 1). La pieza pura
reproduce_position se testea sin red ni DB. Spec §6."""
from tools.lifecycle_falsifier import reproduce_position


def _z(tipo, bajo, alto, centro):
    return {"tipo": tipo, "precio_bajo": bajo, "precio_alto": alto,
            "centro": centro, "toques": 3, "confluencia_redondo": []}


def _zonas():
    return [_z("soporte", 94, 96, 95), _z("resistencia", 104, 106, 105),
            _z("resistencia", 109, 111, 110)]


def test_reproduce_cierre_en_tp_dentro_de_tolerancia():
    pos = {"symbol": "BTCUSDT", "entry_price": 100.0,
           "entry_ts": "2026-01-01T00:00:00+00:00", "exit_ts": "2026-01-03T00:00:00+00:00",
           "exit_price": 105.0, "exit_reason": "TP_HIT", "tenant_id": 2, "id": 7}
    res = reproduce_position(pos, _zonas())
    assert res["reproduced"] is True            # 105 == tp_price del rung 0
    assert res["conduct"]["rungs_honrados"] >= 1
    assert res["conduct"]["cierre_en_plan"] is True


def test_reproduce_cierre_en_sl():
    pos = {"symbol": "BTCUSDT", "entry_price": 100.0,
           "entry_ts": "2026-01-01T00:00:00+00:00", "exit_ts": "2026-01-02T00:00:00+00:00",
           "exit_price": 93.06, "exit_reason": "SL_HIT", "tenant_id": 2, "id": 8}
    res = reproduce_position(pos, _zonas())
    assert res["reproduced"] is True            # ~ plan.sl_price (94*0.99 = 93.06)
    assert res["conduct"]["close_reason"] == "SL_HIT"


def test_exit_fuera_de_todo_no_reproducible():
    pos = {"symbol": "BTCUSDT", "entry_price": 100.0,
           "entry_ts": "2026-01-01T00:00:00+00:00", "exit_ts": "2026-01-02T00:00:00+00:00",
           "exit_price": 102.3, "exit_reason": "MANUAL", "tenant_id": 2, "id": 9}
    res = reproduce_position(pos, _zonas())
    assert res["reproduced"] is False           # 102.3 no es un rung ni el SL → MANUAL fuera de plan
    assert res["conduct"]["cierre_en_plan"] is False


def test_sin_zonas_no_reproducible():
    pos = {"symbol": "XYZUSDT", "entry_price": 100.0,
           "entry_ts": "2026-01-01T00:00:00+00:00", "exit_ts": "2026-01-02T00:00:00+00:00",
           "exit_price": 100.0, "exit_reason": "MANUAL", "tenant_id": 2, "id": 10}
    res = reproduce_position(pos, [])
    assert res["reproduced"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_lifecycle_falsifier.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'tools.lifecycle_falsifier'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/lifecycle_falsifier.py
"""Arnés de falsación de la columna del instrumento (Fase 1) — read-only.

Re-juega la máquina de estados sobre las posiciones REALES ya cerradas del
operador y confirma que el plan derivado reproduce el envelope (entry/exit/sl/tp).
Si no reproduce, la máquina está mal (el refutador de Voronov). HONESTO sobre la
resolución: sin libro de fills histórico, falsa el ENVELOPE, no la secuencia
completa (spec §6). reproduce_position es PURO (testeable sin red/DB); la red
(klines históricas) y la lectura/escritura DB viven en main(), I/O deliberado.

Uso: python -m tools.lifecycle_falsifier   (network-marked; corre a propósito)
"""
from __future__ import annotations

import json
import logging

from instrument.plan import derive_plan
from instrument.lifecycle import LifecycleState, step
from instrument.conduct import compute_conduct

log = logging.getLogger("tools.lifecycle_falsifier")

_TP_TOL_PCT = 0.005   # 0.5%: cercanía exit↔rung/SL para considerar "en plan"


def _close(a: float, b: float) -> bool:
    return b != 0 and abs(a - b) / abs(b) <= _TP_TOL_PCT


def reproduce_position(pos: dict, zonas: list[dict]) -> dict:
    """PURO. Dado una posición cerrada real + las zonas de D.1 al entry, deriva
    el plan, sintetiza la secuencia de evento del envelope, la re-juega y computa
    conducta. Devuelve {reproduced: bool, conduct: dict, plan_json: str}."""
    entry = float(pos["entry_price"])
    exit_price = float(pos["exit_price"]) if pos.get("exit_price") is not None else None
    plan = derive_plan(zonas, entry)
    procedencia = "observado"   # F1: posiciones reales del enlace Binance

    events: list[dict] = [{"tipo": "PLAN_CONFIRMED", "procedencia": procedencia}]
    reproduced = False

    if exit_price is not None and _close(exit_price, plan.sl_price):
        events.append({"tipo": "STOP_HIT", "procedencia": procedencia})
        reproduced = True
    else:
        # ¿el exit cae en algún rung? marca como llenos los rungs hasta ese precio.
        hit_idx = None
        for i, r in enumerate(plan.rungs):
            if exit_price is not None and (exit_price >= r.tp_price or _close(exit_price, r.tp_price)):
                hit_idx = i
        if hit_idx is not None:
            for i in range(hit_idx + 1):
                events.append({"tipo": "RUNG_FILLED", "order_id": f"r{i}",
                               "rung_index": i, "procedencia": procedencia})
            events.append({"tipo": "STOP_HIT", "procedencia": procedencia})
            reproduced = True
        else:
            events.append({"tipo": "MANUAL_EXIT", "procedencia": procedencia})
            reproduced = False   # fuera de plan / datos insuficientes

    state = LifecycleState(plan_id=int(pos.get("id") or 0))
    for e in events:
        state = step(state, e, plan)

    conduct = compute_conduct(
        plan, events, state, entry_price=entry,
        entry_ts=pos["entry_ts"], exit_ts=pos.get("exit_ts") or pos["entry_ts"],
        procedencia=procedencia,
    )
    return {"reproduced": reproduced, "conduct": conduct,
            "plan_json": json.dumps({"sl_price": plan.sl_price,
                                     "rungs": [r.tp_price for r in plan.rungs],
                                     "runner_frac": plan.runner_frac})}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_lifecycle_falsifier.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add tools/lifecycle_falsifier.py tests/test_lifecycle_falsifier.py
git commit -m "feat(instrument): arnés de falsación read-only — reproduce_position contra el envelope real"
```

---

### Task 6: `main()` del arnés — leer posiciones reales, reconstruir D.1, persistir, reportar

**Files:**
- Modify: `tools/lifecycle_falsifier.py` (añadir `main()` + helpers de I/O)
- Test: marcado `network` (no corre en el gate rápido)

**Context:** This task wires the pure `reproduce_position` to real data. Read closed positions read-only (use the project's snapshot/read connection — look at how `api/valleys.py` or `db/positions.py` opens a read connection; mirror it). Fetch historical daily bars up to `entry_ts` via `BinanceAdapter.fetch_klines(symbol, "1d", start_ms, end_ms)`, map each `Bar` to the minimal dict `{"high": b.high, "low": b.low}` (D.1's `detect_levels` only reads high/low), run `screener.sr_levels.detect_levels`, call `reproduce_position`, and persist via `db_put_episode` inside a short `transaction()`. Report counts: reproduced vs. insufficient-data.

- [ ] **Step 1: Add `main()` (no new failing unit test — this is I/O wiring; covered by a `network`-marked smoke test)**

Add to `tools/lifecycle_falsifier.py`:

```python
import time
from datetime import datetime, timezone

from data.providers.binance import BinanceAdapter
from screener.sr_levels import detect_levels, LOOKBACK_DAYS
from db.conduct_episodes import db_put_episode
from db.transaction import snapshot_connection, transaction


def _bars_as_of(symbol: str, entry_ts: str) -> list[dict]:
    """Velas diarias hasta entry_ts (reconstruye D.1 al momento de la entrada)."""
    end = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))
    end_ms = int(end.timestamp() * 1000)
    start_ms = end_ms - LOOKBACK_DAYS * 86_400_000
    bars = BinanceAdapter().fetch_klines(symbol, "1d", start_ms, end_ms)
    return [{"high": b.high, "low": b.low} for b in bars]


def _closed_positions(tenant_id: int) -> list[dict]:
    with snapshot_connection() as con:
        con.row_factory = __import__("sqlite3").Row
        rows = con.execute(
            """SELECT id, symbol, entry_price, entry_ts, exit_price, exit_ts,
                      exit_reason, tenant_id
               FROM positions WHERE status='closed' AND tenant_id=?
               ORDER BY entry_ts""", (tenant_id,)).fetchall()
    return [dict(r) for r in rows]


def main(tenant_id: int = 2) -> int:
    logging.basicConfig(level=logging.INFO)
    now = datetime.now(timezone.utc).isoformat()
    positions = _closed_positions(tenant_id)
    reproduced = insuficiente = 0
    for pos in positions:
        try:
            zonas = detect_levels(_bars_as_of(pos["symbol"], pos["entry_ts"]))
        except Exception as e:  # noqa: BLE001 — fallo de red/símbolo = datos insuficientes
            log.warning("FALSIFIER_SKIP symbol=%s causa=%s", pos["symbol"], e)
            insuficiente += 1
            continue
        res = reproduce_position(pos, zonas)
        with transaction() as con:
            db_put_episode(con, position_id=pos["id"], symbol=pos["symbol"],
                           tenant_id=tenant_id, entry_ts=pos["entry_ts"],
                           exit_ts=pos.get("exit_ts"), conduct=res["conduct"],
                           plan_json=res["plan_json"], reproduced=res["reproduced"],
                           created_ts=now)
        reproduced += int(res["reproduced"])
        insuficiente += int(not res["reproduced"])
    print(f"conduct_episodes: {len(positions)} posiciones · "
          f"{reproduced} reproducidas · {insuficiente} fuera de plan/datos insuficientes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Write a `network`-marked smoke test**

```python
# tests/test_lifecycle_falsifier.py — añadir
import pytest


@pytest.mark.network
def test_bars_as_of_devuelve_velas_diarias():
    from tools.lifecycle_falsifier import _bars_as_of
    bars = _bars_as_of("BTCUSDT", "2026-01-01T00:00:00+00:00")
    assert len(bars) > 100
    assert all("high" in b and "low" in b for b in bars)
```

- [ ] **Step 3: Run the unit tests (network test skipped in the fast gate)**

Run: `python -m pytest tests/test_lifecycle_falsifier.py -m "not network" -v`
Expected: PASS (the 4 pure tests; the network one is deselected)

- [ ] **Step 4: Verify the full fast gate has no regressions**

Run: `python -m pytest tests/ -m "not network" -n auto -q`
Expected: green, no regressions.

- [ ] **Step 5: Commit**

```bash
git add tools/lifecycle_falsifier.py tests/test_lifecycle_falsifier.py
git commit -m "feat(instrument): main() del arnés — lee posiciones reales, reconstruye D.1, persiste episodios"
```

---

## Verificación final

- [ ] **Módulos puros:** `python -m pytest tests/test_instrument_plan.py tests/test_instrument_lifecycle.py tests/test_instrument_conduct.py tests/test_conduct_episodes.py tests/test_lifecycle_falsifier.py -m "not network" -v` → todo verde.
- [ ] **Gate rápido (CI):** `python -m pytest tests/ -m "not network" -n auto -q` → sin regresiones.
- [ ] **Falsación real (deliberada, requiere red + DB de prod):** `python -m tools.lifecycle_falsifier` → imprime cuántas de las posiciones reales de papá reprodujo la columna y cuántas quedaron fuera de plan / con datos insuficientes. Este es el entregable de la Fase 1: la columna probada contra lo real.
