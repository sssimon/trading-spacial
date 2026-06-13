# El Instrumento — Fase 2 (simulador determinista refutador) · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir el simulador determinista que corre el plan derivado en piloto automático sobre velas diarias, alimenta la máquina de estados de F1, y reporta paridad contra el envelope de las posiciones reales — sin PnL.

**Architecture:** Una pieza nueva pura (`instrument/simulate.py`: `resolve_fills` aislado + `simulate_plan`) que reutiliza `instrument/lifecycle.py::step` y `instrument/plan.py::derive_plan`; una transición nueva `SIM_END` en el reductor de F1; y un arnés `tools/plan_simulator.py` (I/O: posiciones reales + frames diarios + comparador de paridad puro). Regla de cierre conservadora SL-antes-que-TP sobre velas diarias.

**Tech Stack:** Python 3.12 (`dataclasses`), pytest, `backtest.py::get_cached_data` para frames, reutiliza F1.

**Spec:** `docs/superpowers/specs/es/2026-06-13-instrumento-fase2-simulador-design.md`.

**Branch:** `feat/instrumento-fase2-simulador` (ya creada).

**Frontera dura:** sin PnL, sin tabla nueva, sin `PositionClosure`, sin escritura a `positions.status`. `resolve_fills`/`simulate_plan` puras.

---

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `instrument/lifecycle.py` (modificar) | Añadir la transición `SIM_END → CLOSED`. |
| `instrument/simulate.py` (crear) | `resolve_fills(plan, state, candle)` (regla pura) + `simulate_plan(plan, candles)` (caminata pura). |
| `tools/plan_simulator.py` (crear) | `check_parity(sim_state, pos, plan)` (puro) + `main()` (I/O: posiciones reales, frames diarios, reporte). |
| `tests/test_instrument_lifecycle.py` (modificar) | Test de `SIM_END`. |
| `tests/test_instrument_simulate.py` (crear) | Tests de `resolve_fills` + `simulate_plan`. |
| `tests/test_plan_simulator.py` (crear) | Tests de `check_parity` + smoke `network`-marcado. |

---

### Task 1: transición `SIM_END` en el reductor de F1

**Files:**
- Modify: `instrument/lifecycle.py` (añadir un bloque antes del `return state` final)
- Test: `tests/test_instrument_lifecycle.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_instrument_lifecycle.py — añadir
def test_sim_end_cierra_como_sim_end():
    s = step(_s0(), {"tipo": "SIM_END", "procedencia": "observado"}, _plan())
    assert s.fase == "CLOSED" and s.close_reason == "SIM_END"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_instrument_lifecycle.py::test_sim_end_cierra_como_sim_end -v`
Expected: FAIL (SIM_END no transiciona; `close_reason` queda None)

- [ ] **Step 3: Write minimal implementation**

En `instrument/lifecycle.py`, dentro de `step`, justo ANTES del `return state` final (el comentario `# evento desconocido: ignorado`), insertar:

```python
    if tipo == "SIM_END":
        return replace(state, fase="CLOSED", close_reason="SIM_END")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_instrument_lifecycle.py -v`
Expected: PASS (todos, incl. el nuevo)

- [ ] **Step 5: Commit**

```bash
git add instrument/lifecycle.py tests/test_instrument_lifecycle.py
git commit -m "feat(instrument): transición SIM_END → CLOSED (la posición sigue abierta al fin de los datos)"
```

---

### Task 2: `resolve_fills` — la regla de cierre determinista (pura)

**Files:**
- Create: `instrument/simulate.py`
- Test: `tests/test_instrument_simulate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_instrument_simulate.py
"""Tests del simulador determinista (instrumento F2). Puro: sin red, sin DB. Spec §3/§4."""
from instrument.plan import derive_plan
from instrument.lifecycle import LifecycleState
from instrument.simulate import resolve_fills


def _z(tipo, bajo, alto, centro):
    return {"tipo": tipo, "precio_bajo": bajo, "precio_alto": alto,
            "centro": centro, "toques": 3, "confluencia_redondo": []}


def _plan():
    # entry 100: soporte [94,96] → sl=93.06; resistencias en 105 y 110.
    zonas = [_z("soporte", 94, 96, 95),
             _z("resistencia", 104, 106, 105),
             _z("resistencia", 109, 111, 110)]
    return derive_plan(zonas, entry_price=100.0)


def _armed(p, **kw):
    # estado CONFIRMED con el SL del plan armado (como lo deja simulate_plan).
    return LifecycleState(plan_id=0, fase="CONFIRMED", sl_actual=p.sl_price, **kw)


def test_doble_toque_sl_primero():
    p = _plan()
    candle = {"open": 100, "high": 106, "low": 90, "close": 95}  # toca TP1 y SL
    evs = resolve_fills(p, _armed(p), candle)
    assert [e["tipo"] for e in evs] == ["STOP_HIT"]   # pesimista: SL gana


def test_solo_tp1_dispara_rung_y_be():
    p = _plan()
    candle = {"open": 100, "high": 106, "low": 99, "close": 105}
    evs = resolve_fills(p, _armed(p), candle)
    tipos = [e["tipo"] for e in evs]
    assert evs[0]["tipo"] == "RUNG_FILLED" and evs[0]["rung_index"] == 0
    assert "SL_MOVED" in tipos   # BE tras TP1
    assert next(e for e in evs if e["tipo"] == "SL_MOVED")["nuevo_sl"] == p.entry_price


def test_ambos_rungs_en_una_vela_en_orden():
    p = _plan()
    candle = {"open": 100, "high": 112, "low": 99, "close": 111}
    rungs = [e for e in resolve_fills(p, _armed(p), candle) if e["tipo"] == "RUNG_FILLED"]
    assert [e["rung_index"] for e in rungs] == [0, 1]


def test_nada_dispara_lista_vacia():
    p = _plan()
    candle = {"open": 100, "high": 101, "low": 99, "close": 100}
    assert resolve_fills(p, _armed(p), candle) == []


def test_rung_ya_lleno_no_se_reemite():
    p = _plan()
    candle = {"open": 100, "high": 106, "low": 99, "close": 105}
    evs = resolve_fills(p, _armed(p, rungs_llenos=frozenset({0})), candle)
    assert all(e.get("rung_index") != 0 for e in evs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_instrument_simulate.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'instrument.simulate'`

- [ ] **Step 3: Write minimal implementation**

```python
# instrument/simulate.py
"""Simulador determinista del plan (instrumento F2) — puro, sin red, sin DB.

Corre el plan derivado en piloto automático sobre velas diarias, generando los
eventos que alimenta la MISMA máquina de estados de F1. Regla de cierre
CONSERVADORA: si una vela toca TP y SL, el SL gana (pesimista, cota inferior
auditable — roster unánime A, spec §1). `resolve_fills` está AISLADO tras una
firma estable para que el swap futuro a intradía sea un cambio de implementación.
Spec §3/§4."""
from __future__ import annotations

from dataclasses import replace

from instrument.lifecycle import LifecycleState, step


def resolve_fills(plan, state: LifecycleState, candle: dict) -> list[dict]:
    """Eventos que dispara una vela diaria dado el estado actual. Puro.

    1. SL primero (pesimista): si el SL está armado y la vela lo toca → STOP_HIT.
    2. Si no: cada rung no-lleno con high ≥ tp_price (orden ascendente) → RUNG_FILLED;
       tras llenarse el rung 0 → SL_MOVED a entry (regla BE del plan)."""
    low = float(candle["low"])
    high = float(candle["high"])

    if state.sl_actual > 0 and low <= state.sl_actual:
        return [{"tipo": "STOP_HIT", "procedencia": "observado"}]

    events: list[dict] = []
    rung0 = False
    for i, r in enumerate(plan.rungs):
        if i in state.rungs_llenos:
            continue
        if high >= r.tp_price:
            events.append({"tipo": "RUNG_FILLED", "order_id": f"sim-r{i}",
                           "rung_index": i, "procedencia": "observado"})
            if i == 0:
                rung0 = True
    if rung0:
        events.append({"tipo": "SL_MOVED", "nuevo_sl": plan.entry_price,
                       "procedencia": "observado"})
    return events
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_instrument_simulate.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add instrument/simulate.py tests/test_instrument_simulate.py
git commit -m "feat(instrument): resolve_fills — regla de cierre determinista diaria SL-antes-que-TP"
```

---

### Task 3: `simulate_plan` — la caminata (pura)

**Files:**
- Modify: `instrument/simulate.py`
- Test: `tests/test_instrument_simulate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_instrument_simulate.py — añadir
from instrument.simulate import simulate_plan


def test_simula_tp1_luego_be_cierra_be_hit():
    p = _plan()
    candles = [
        {"open": 100, "high": 106, "low": 99, "close": 105},   # TP1 fill → SL a BE(100)
        {"open": 105, "high": 107, "low": 99, "close": 100},   # low 99 < BE 100 → STOP_HIT
    ]
    events, st = simulate_plan(p, candles)
    assert st.fase == "CLOSED" and st.close_reason == "BE_HIT"
    assert events[0]["tipo"] == "PLAN_CONFIRMED"


def test_simula_solo_cae_cierra_sl_hit():
    p = _plan()
    candles = [{"open": 100, "high": 101, "low": 90, "close": 92}]  # low < sl 93.06
    _, st = simulate_plan(p, candles)
    assert st.close_reason == "SL_HIT"


def test_simula_sube_toda_la_escalera_sim_end():
    p = _plan()
    candles = [{"open": 100, "high": 112, "low": 99, "close": 111}]  # ambos rungs, sin cerrar
    _, st = simulate_plan(p, candles)
    assert st.rungs_llenos == frozenset({0, 1})
    assert st.close_reason == "SIM_END"   # runner abierto, se acaban las velas


def test_simula_nunca_toca_nada_sim_end():
    p = _plan()
    candles = [{"open": 100, "high": 101, "low": 99, "close": 100} for _ in range(3)]
    _, st = simulate_plan(p, candles)
    assert st.close_reason == "SIM_END"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_instrument_simulate.py -k simula -v`
Expected: FAIL `ImportError: cannot import name 'simulate_plan'`

- [ ] **Step 3: Write minimal implementation**

En `instrument/simulate.py`, añadir tras `resolve_fills`:

```python
def simulate_plan(plan, candles: list[dict]) -> tuple[list[dict], LifecycleState]:
    """Recorre las velas diarias desde la entrada; cada vela → resolve_fills → step.
    Para al CLOSED o, si se agotan las velas sin cerrar, emite SIM_END (divergencia
    honesta: el plan habría aguantado más que los datos disponibles). Puro. Spec §4."""
    confirm = {"tipo": "PLAN_CONFIRMED", "procedencia": "observado"}
    state = step(LifecycleState(plan_id=0), confirm, plan)   # PLANNED → CONFIRMED
    state = replace(state, sl_actual=plan.sl_price)           # arma el SL del plan
    events: list[dict] = [confirm]

    for candle in candles:
        for e in resolve_fills(plan, state, candle):
            events.append(e)
            state = step(state, e, plan)
            if state.fase == "CLOSED":
                break
        if state.fase == "CLOSED":
            break

    if state.fase != "CLOSED":
        sim_end = {"tipo": "SIM_END", "procedencia": "observado"}
        events.append(sim_end)
        state = step(state, sim_end, plan)
    return events, state
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_instrument_simulate.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add instrument/simulate.py tests/test_instrument_simulate.py
git commit -m "feat(instrument): simulate_plan — caminata determinista sobre velas → REALIZED o SIM_END"
```

---

### Task 4: arnés `tools/plan_simulator.py` — paridad + reporte

**Files:**
- Create: `tools/plan_simulator.py`
- Test: `tests/test_plan_simulator.py`

**Context:** `check_parity` es PURO (testeable sin red/DB). `main()` reutiliza, de `tools/lifecycle_falsifier.py`, los helpers `_closed_positions` (filtro BNC-12: `origin IN ('SIGNAL','OPERATOR')`) y `_bars_as_of` (reconstruye D.1 al entry) — impórtalos, no los dupliques. Los frames diarios hacia adelante vienen de `backtest.py::get_cached_data(symbol, "1d", entry_ts)` (devuelve un `pandas.DataFrame` con índice de tiempo y columnas `open/high/low/close/volume`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan_simulator.py
"""Tests del arnés del simulador F2 (instrumento). check_parity es puro. Spec §5."""
from instrument.plan import derive_plan
from instrument.lifecycle import LifecycleState
from tools.plan_simulator import check_parity


def _z(tipo, bajo, alto, centro):
    return {"tipo": tipo, "precio_bajo": bajo, "precio_alto": alto,
            "centro": centro, "toques": 3, "confluencia_redondo": []}


def _plan():
    zonas = [_z("soporte", 94, 96, 95), _z("resistencia", 104, 106, 105)]
    return derive_plan(zonas, entry_price=100.0)


def test_parity_ambos_sl():
    st = LifecycleState(plan_id=0, fase="CLOSED", close_reason="SL_HIT")
    assert check_parity(st, {"exit_reason": "SL_HIT"}, _plan())["parity"] is True


def test_parity_real_tp_sim_toco_rung():
    st = LifecycleState(plan_id=0, fase="CLOSED", close_reason="SIM_END",
                        rungs_llenos=frozenset({0}))
    assert check_parity(st, {"exit_reason": "TP_HIT"}, _plan())["parity"] is True


def test_divergencia_real_tp_sim_sl():
    st = LifecycleState(plan_id=0, fase="CLOSED", close_reason="SL_HIT")
    r = check_parity(st, {"exit_reason": "TP_HIT"}, _plan())
    assert r["parity"] is False
    assert "TP" in r["motivo"] and "SL" in r["motivo"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plan_simulator.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'tools.plan_simulator'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/plan_simulator.py
"""Arnés del simulador determinista F2 (instrumento) — refutador, read-only.

Corre simulate_plan sobre las posiciones REALES cerradas (filtro BNC-12) con
frames diarios, y verifica PARIDAD contra el envelope real. Reporta tres cubos:
máquina legal, paridad, divergencias. SIN PnL, SIN tabla nueva. check_parity es
PURO; la red (frames) y la lectura DB viven en main(). Spec §5.

Uso: python -m tools.plan_simulator   (network-marked; corre a propósito)
"""
from __future__ import annotations

import logging
from datetime import datetime

from instrument.plan import derive_plan
from instrument.simulate import simulate_plan
from screener.sr_levels import detect_levels
from tools.lifecycle_falsifier import _bars_as_of, _closed_positions
from backtest import get_cached_data

log = logging.getLogger("tools.plan_simulator")


def check_parity(sim_state, pos: dict, plan) -> dict:
    """PURO. ¿El cierre del sim corresponde directionalmente al cierre real?
    real SL ↔ sim SL_HIT/BE_HIT; real TP ↔ sim tocó al menos un rung. Spec §5."""
    real = (pos.get("exit_reason") or "").upper()
    sim = sim_state.close_reason or ""
    real_sl = "SL" in real
    real_tp = "TP" in real
    sim_sl = sim in ("SL_HIT", "BE_HIT")
    sim_toco_rung = bool(sim_state.rungs_llenos)
    if real_sl and sim_sl:
        return {"parity": True, "motivo": "ambos SL"}
    if real_tp and sim_toco_rung:
        return {"parity": True, "motivo": "ambos tocaron TP"}
    return {"parity": False, "motivo": f"real={real or '?'} sim={sim or '?'}"}


def _forward_candles(symbol: str, entry_ts: str, exit_ts: str | None) -> list[dict]:
    """Velas diarias en [entry, exit], orden ascendente. I/O (red)."""
    start = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))
    df = get_cached_data(symbol, "1d", start)
    if df.empty:
        return []
    # Normaliza el índice a naive UTC para comparar con timestamps parseados.
    idx = df.index
    if getattr(idx, "tz", None) is not None:
        df = df.set_index(idx.tz_convert("UTC").tz_localize(None))
    df = df[df.index >= start.replace(tzinfo=None)]
    if exit_ts:
        hi = datetime.fromisoformat(exit_ts.replace("Z", "+00:00")).replace(tzinfo=None)
        df = df[df.index <= hi]
    return [{"open": float(r.open), "high": float(r.high),
             "low": float(r.low), "close": float(r.close)} for r in df.itertuples()]


def main(tenant_id: int = 2) -> int:
    logging.basicConfig(level=logging.INFO)
    positions = _closed_positions(tenant_id)
    legal = parity = diverg = 0
    divergencias: list[dict] = []
    for pos in positions:
        try:
            zonas = detect_levels(_bars_as_of(pos["symbol"], pos["entry_ts"]))
            candles = _forward_candles(pos["symbol"], pos["entry_ts"], pos.get("exit_ts"))
        except Exception as e:  # noqa: BLE001 — fallo de red/símbolo = se omite
            log.warning("SIM_SKIP symbol=%s causa=%s", pos["symbol"], e)
            continue
        if not candles:
            continue
        plan = derive_plan(zonas, float(pos["entry_price"]))
        _, st = simulate_plan(plan, candles)
        legal += int(st.fase == "CLOSED")
        res = check_parity(st, pos, plan)
        if res["parity"]:
            parity += 1
        else:
            diverg += 1
            divergencias.append({"symbol": pos["symbol"], "id": pos["id"], **res})
    print(f"plan_simulator: {len(positions)} posiciones · {legal} máquina-legal · "
          f"{parity} paridad · {diverg} divergencias")
    for d in divergencias:
        print(f"  DIVERGENCIA {d['symbol']}#{d['id']}: {d['motivo']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

> **Nota sobre `_forward_candles`:** si en runtime el índice del DataFrame ya viene naive, la rama `tz` se salta sola. Lo esencial: devolver las velas diarias en `[entry, exit]` en orden ascendente. Si `get_cached_data` devuelve los nombres de columna en otro caso (p.ej. mayúsculas), ajustá los accesores `r.open/.high/.low/.close` a lo que exponga `itertuples()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_plan_simulator.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Add a `network`-marked smoke test**

```python
# tests/test_plan_simulator.py — añadir
import pytest


@pytest.mark.network
def test_forward_candles_devuelve_velas():
    from tools.plan_simulator import _forward_candles
    candles = _forward_candles("BTCUSDT", "2025-01-01T00:00:00+00:00",
                               "2025-02-01T00:00:00+00:00")
    assert len(candles) > 5
    assert all({"open", "high", "low", "close"} <= set(c) for c in candles)
```

Run: `python -m pytest tests/test_plan_simulator.py -m "not network" -v`
Expected: PASS (3 puros; el network deseleccionado)

- [ ] **Step 6: Commit**

```bash
git add tools/plan_simulator.py tests/test_plan_simulator.py
git commit -m "feat(instrument): arnés del simulador F2 — paridad vs envelope real + reporte (sin PnL)"
```

---

## Verificación final

- [ ] **Puros:** `python -m pytest tests/test_instrument_lifecycle.py tests/test_instrument_simulate.py tests/test_plan_simulator.py -m "not network" -v` → todo verde.
- [ ] **Gate rápido (CI):** `python -m pytest tests/ -m "not network" -n auto -q` → sin regresiones.
- [ ] **Refutación real (deliberada, requiere red + DB):** `python -m tools.plan_simulator` → imprime cuántas posiciones la máquina cerró legalmente, cuántas tienen paridad con la realidad, y las divergencias a investigar. Ese es el entregable de F2: el segundo refutador corriendo.
