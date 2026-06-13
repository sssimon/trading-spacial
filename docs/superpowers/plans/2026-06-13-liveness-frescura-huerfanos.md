# Liveness operacional — revivir huérfanos + frescura como contrato · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Revivir los dos huérfanos (screener de valles + sync de Binance) como threads del lifespan, exponer la frescura como un tipo inconstruible sin marca temporal aplicado a los readers que mienten, y armar el gate de liveness + inventario.

**Architecture:** Un tipo puro `LiveSnapshot` (`freshness.py`, raíz) que clasifica fresco/rancio/muerto e inyecta la frescura en toda respuesta. Dos loops nuevos (`screener_loop`, `sync_loop`) en `scanner/runtime.py`, arrancados por `start_scanner_thread`, gestionados por `_managed_threads`/`stop_managed_threads` (mismo patrón que el scanner). El gate vive en CLAUDE.md + un inventario enumerado.

**Tech Stack:** Python 3.12 (`dataclasses`, `threading`), FastAPI, pytest; React + TypeScript + Vitest (frontend).

**Spec:** `docs/superpowers/specs/es/2026-06-13-liveness-frescura-huerfanos-design.md`.

**Branch:** `feat/liveness-frescura-huerfanos` (ya creada).

---

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `freshness.py` (crear) | `LiveSnapshot` + `classify_freshness` (puro). |
| `api/valleys.py` (modificar) | Envolver `/valley-candidates` en `LiveSnapshot`. |
| `api/dossier.py` (modificar) | Envolver `/dossier/{symbol}` en `LiveSnapshot`. |
| `db/binance_credentials.py` (modificar) | `db_list_active_credential_tenants`. |
| `tools/run_valley_screener.py` (modificar) | `regenerate()` (build + write, DRY entre main y el loop). |
| `scanner/runtime.py` (modificar) | `screener_loop`, `sync_loop`, arrancados en `start_scanner_thread`. |
| `frontend/src/types.ts`, `frontend/src/components/FreshnessTag.tsx` (crear/mod), `ValleysView.tsx` (mod) | Mostrar la frescura. |
| `CLAUDE.md` (modificar) + `docs/superpowers/inventario-estado-vivo.md` (crear) | El gate + el inventario. |

---

### Task 1: `freshness.py` — el tipo `LiveSnapshot`

**Files:**
- Create: `freshness.py`
- Test: `tests/test_freshness.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_freshness.py
"""Tests del tipo de frescura (liveness operacional). Puro. Spec §2."""
from datetime import datetime, timedelta, timezone

from freshness import LiveSnapshot, classify_freshness


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hace(horas):
    return (datetime.now(timezone.utc) - timedelta(hours=horas)).isoformat()


def test_muerto_si_nunca_generado():
    assert LiveSnapshot(payload={}, generated_at=None, umbral_seg=3600).estado == "muerto"


def test_fresco_si_reciente():
    assert LiveSnapshot(payload={}, generated_at=_now(), umbral_seg=3600).estado == "fresco"


def test_rancio_si_viejo():
    assert LiveSnapshot(payload={}, generated_at=_hace(2), umbral_seg=3600).estado == "rancio"


def test_no_parseable_es_muerto():
    assert LiveSnapshot(payload={}, generated_at="basura", umbral_seg=3600).estado == "muerto"


def test_to_response_siempre_inyecta_frescura():
    r = LiveSnapshot(payload={"a": 1}, generated_at=None, umbral_seg=3600).to_response()
    assert r["a"] == 1
    assert r["frescura"]["estado"] == "muerto"
    assert r["frescura"]["generated_at"] is None


def test_classify_freshness_atajo():
    assert classify_freshness(_now(), 3600) == "fresco"
    assert classify_freshness(None, 3600) == "muerto"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_freshness.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'freshness'`

- [ ] **Step 3: Write minimal implementation**

```python
# freshness.py
"""Frescura de estado vivo como TIPO (no un helper opcional). Liveness operacional.

LiveSnapshot envuelve un payload con su marca temporal OBLIGATORIA y clasifica
fresco/rancio/muerto. `to_response` SIEMPRE inyecta la frescura — el payload no se
puede emitir sin ella (la frescura vive en el CONTRATO, no en la disciplina del
lector). Eje SNAPSHOT, distinto de screener.valley_filter.classify_liveness
(liveness de SÍMBOLO sobre velas). Puro: sin red, sin DB. Spec §2."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def _edad_seg(generated_at: str) -> float | None:
    """Antigüedad en segundos de un ISO-8601 (tolera Z/offset/naive), o None si
    no parsea."""
    try:
        ts = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds()


@dataclass(frozen=True)
class LiveSnapshot:
    """Estado vivo + su marca temporal obligatoria. Inconstruible sin generated_at
    (aunque sea None explícito); to_response siempre emite la frescura."""
    payload: dict
    generated_at: str | None
    umbral_seg: float

    @property
    def estado(self) -> str:
        """'fresco' | 'rancio' | 'muerto'."""
        if not self.generated_at:
            return "muerto"
        edad = _edad_seg(self.generated_at)
        if edad is None:
            return "muerto"
        return "rancio" if edad > self.umbral_seg else "fresco"

    def to_response(self) -> dict:
        edad = _edad_seg(self.generated_at) if self.generated_at else None
        return {**self.payload, "frescura": {
            "estado": self.estado, "edad_seg": edad,
            "generated_at": self.generated_at, "umbral_seg": self.umbral_seg}}


def classify_freshness(generated_at: str | None, umbral_seg: float) -> str:
    """Atajo funcional. NO confundir con classify_liveness (eje símbolo)."""
    return LiveSnapshot(payload={}, generated_at=generated_at, umbral_seg=umbral_seg).estado
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_freshness.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add freshness.py tests/test_freshness.py
git commit -m "feat(freshness): LiveSnapshot — frescura como tipo inconstruible sin marca temporal"
```

---

### Task 2: aplicar `LiveSnapshot` a `/valley-candidates` y `/dossier`

**Files:**
- Modify: `api/valleys.py`, `api/dossier.py`
- Test: `tests/test_valleys_api.py` (existe), `tests/test_dossier_api.py` (existe)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_valleys_api.py — añadir (lee cómo el test arma el TestClient / _OUTPUT)
import os
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.valleys import router


def _app():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_valles_sin_foto_es_muerto_no_vacio_mudo(tmp_path):
    # archivo ausente → frescura.estado == 'muerto' (no un _EMPTY mudo)
    with patch("api.valleys._OUTPUT", str(tmp_path / "nope.json")):
        r = _app().get("/valley-candidates")
    assert r.status_code == 200
    assert r.json()["frescura"]["estado"] == "muerto"


def test_valles_foto_vieja_es_rancia(tmp_path):
    from datetime import datetime, timedelta, timezone
    import json
    viejo = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    p = tmp_path / "foto.json"
    p.write_text(json.dumps({"generated_at": viejo,
                             "coverage": {"universe": 1, "evaluated": 1, "complete": True},
                             "candidates": []}), encoding="utf-8")
    with patch("api.valleys._OUTPUT", str(p)):
        r = _app().get("/valley-candidates")
    assert r.json()["frescura"]["estado"] == "rancio"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_valleys_api.py -k "muerto or rancia" -v`
Expected: FAIL (la respuesta no tiene la clave `frescura`)

- [ ] **Step 3: Write minimal implementation**

En `api/valleys.py`, añadir el import y la constante y envolver el return:

```python
from freshness import LiveSnapshot

FRESCURA_VALLES_SEG = 43200   # 12h = 2× la cadencia del screener (spec §5)
```

Reescribir `get_valley_candidates`:

```python
@router.get("/valley-candidates", summary="Candidatas del screener de valles (vivas + en rango)")
def get_valley_candidates() -> dict:
    """Devuelve la foto del screener con su FRESCURA en el contrato. Archivo
    ausente → estado 'muerto' (el screener no ha corrido), distinto de una foto
    vieja → 'rancio'. No más vacío mudo."""
    if not os.path.exists(_OUTPUT):
        return LiveSnapshot(payload=dict(_EMPTY), generated_at=None,
                            umbral_seg=FRESCURA_VALLES_SEG).to_response()
    try:
        with open(_OUTPUT, encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.error("VALLEY_SNAPSHOT_UNREADABLE causa=%s", e)
        snap = dict(_EMPTY)
    return LiveSnapshot(payload=snap, generated_at=snap.get("generated_at"),
                        umbral_seg=FRESCURA_VALLES_SEG).to_response()
```

En `api/dossier.py`, envolver el payload final del endpoint (los dos paths — caché-hit y generado — devuelven un dict con `generated_at`). Añadir:

```python
from freshness import LiveSnapshot

FRESCURA_DOSSIER_SEG = 7 * 24 * 3600   # = el TTL del dossier (spec §5)
```

En `get_dossier`, en CADA `return` que devuelve el payload del dossier (caché-hit y generado), envolver: en vez de `return json.loads(cached["dossier_json"])` y `return payload`, hacer `return LiveSnapshot(payload=<el dict>, generated_at=<el dict>.get("generated_at"), umbral_seg=FRESCURA_DOSSIER_SEG).to_response()`. (Leé el archivo y aplicá el wrap a ambos returns que sirven el dossier; el `no_disponible` también lleva generated_at → su frescura saldrá 'muerto'/'rancio' coherente.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_valleys_api.py tests/test_dossier_api.py -v`
Expected: PASS (los nuevos + los existentes; si un test viejo del dossier asercionaba la forma exacta sin `frescura`, ajustalo para tolerar la clave nueva — la `frescura` es aditiva).

- [ ] **Step 5: Commit**

```bash
git add api/valleys.py api/dossier.py tests/test_valleys_api.py
git commit -m "feat(api): frescura en el contrato de /valley-candidates y /dossier (muerto≠rancio≠fresco)"
```

---

### Task 3: enumerar tenants con credencial Binance ACTIVE

**Files:**
- Modify: `db/binance_credentials.py`
- Test: `tests/test_binance_credentials.py` (si existe; si no, crear `tests/test_active_credential_tenants.py`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_active_credential_tenants.py
"""Test del enumerador de tenants con credencial Binance ACTIVE (liveness). Spec §1."""
import sqlite3

from db.binance_credentials import db_list_active_credential_tenants


def _con():
    con = sqlite3.connect(":memory:")
    con.execute("""CREATE TABLE binance_credentials (
        tenant_id INTEGER PRIMARY KEY, status TEXT NOT NULL)""")
    con.execute("INSERT INTO binance_credentials VALUES (2, 'ACTIVE')")
    con.execute("INSERT INTO binance_credentials VALUES (3, 'REVOKED')")
    con.execute("INSERT INTO binance_credentials VALUES (4, 'ACTIVE')")
    return con


def test_lista_solo_active():
    assert sorted(db_list_active_credential_tenants(_con())) == [2, 4]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_active_credential_tenants.py -v`
Expected: FAIL `ImportError: cannot import name 'db_list_active_credential_tenants'`

- [ ] **Step 3: Write minimal implementation**

En `db/binance_credentials.py`, añadir (helper SQL puro, recibe `con`):

```python
def db_list_active_credential_tenants(con: sqlite3.Connection) -> list[int]:
    """tenant_ids con credencial Binance ACTIVE (el sync_loop itera estos). Puro."""
    cur = con.execute("SELECT tenant_id FROM binance_credentials WHERE status='ACTIVE'")
    return [int(r[0]) for r in cur.fetchall()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_active_credential_tenants.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/binance_credentials.py tests/test_active_credential_tenants.py
git commit -m "feat(db): db_list_active_credential_tenants — el sync_loop itera estos"
```

---

### Task 4: `screener_loop` + `sync_loop` (threads del lifespan)

**Files:**
- Modify: `tools/run_valley_screener.py` (añadir `regenerate()`), `scanner/runtime.py` (los loops + arrancarlos)
- Test: `tests/test_liveness_loops.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_liveness_loops.py
"""Tests de los loops de liveness (screener/sync). Spec §1. Los ciclos son
testeables; el loop respeta stop_event (no spin infinito)."""
import threading
from unittest.mock import patch

from scanner.runtime import _screener_cycle, _sync_cycle, screener_loop


def test_screener_cycle_invoca_regenerate():
    with patch("scanner.runtime._regenerate_screener") as gen:
        _screener_cycle()
    assert gen.call_count == 1


def test_screener_cycle_fail_soft():
    # una excepción en el ciclo NO propaga (el loop debe seguir vivo)
    with patch("scanner.runtime._regenerate_screener", side_effect=RuntimeError("boom")):
        _screener_cycle()   # no debe lanzar


def test_sync_cycle_itera_tenants_active():
    with patch("scanner.runtime._active_tenants", return_value=[2, 4]), \
         patch("scanner.runtime._sync_one") as sync_one:
        _sync_cycle(threading.Event())
    assert {c.args[0] for c in sync_one.call_args_list} == {2, 4}


def test_sync_cycle_un_tenant_falla_no_tumba_el_resto():
    def _one(tid):
        if tid == 2:
            raise RuntimeError("rate ban")
    with patch("scanner.runtime._active_tenants", return_value=[2, 4]), \
         patch("scanner.runtime._sync_one", side_effect=_one) as sync_one:
        _sync_cycle(threading.Event())   # no lanza
    assert sync_one.call_count == 2   # siguió con el 4


def test_loop_respeta_stop_event():
    # con el stop_event ya seteado, el loop sale de inmediato (no spin)
    ev = threading.Event(); ev.set()
    with patch("scanner.runtime._screener_cycle") as cyc:
        screener_loop(stop_event=ev)
    assert cyc.call_count == 0   # salió antes de cualquier ciclo
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_liveness_loops.py -v`
Expected: FAIL `ImportError` (las funciones no existen)

- [ ] **Step 3: Write minimal implementation**

En `tools/run_valley_screener.py`, extraer la regeneración (DRY entre `main` y el loop):

```python
def regenerate(*, pause_s: float = 0.05) -> dict:
    """build_snapshot + escribe el JSON. Usado por main() y por screener_loop."""
    snap = build_snapshot(pause_s=pause_s)
    os.makedirs(os.path.dirname(_OUTPUT), exist_ok=True)
    with open(_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2, ensure_ascii=False)
    return snap
```
Y que `main()` llame `snap = regenerate()` en vez de duplicar build+write (dejá el `print` de cobertura).

En `scanner/runtime.py`, añadir las cadencias junto a `SCAN_INTERVAL_SEC` y los loops:

```python
SCREENER_INTERVAL_SEC = 21600   # 6h (spec §5)
SYNC_INTERVAL_SEC = 300         # 5min (spec §5)


def _regenerate_screener() -> None:
    from tools.run_valley_screener import regenerate  # noqa: PLC0415
    snap = regenerate()
    log.info("screener_loop: %d candidatas", len(snap.get("candidates", [])))


def _screener_cycle() -> None:
    try:
        _regenerate_screener()
    except Exception as e:  # noqa: BLE001 — fail-soft: un ciclo malo no mata el thread
        log.warning("screener_loop ciclo falló: %s", e)


def screener_loop(stop_event: threading.Event | None = None) -> None:
    """Regenera la foto del screener cada SCREENER_INTERVAL_SEC. Spec §1."""
    if stop_event is None:
        stop_event = threading.Event()
    from api.config import load_config  # noqa: PLC0415
    interval = load_config().get("screener_interval_sec", SCREENER_INTERVAL_SEC)
    while not stop_event.is_set():
        _screener_cycle()
        stop_event.wait(interval)   # sleep inter-ciclo interrumpible


def _active_tenants() -> list[int]:
    from db.transaction import snapshot_connection  # noqa: PLC0415
    from db.binance_credentials import db_list_active_credential_tenants  # noqa: PLC0415
    with snapshot_connection() as con:
        return db_list_active_credential_tenants(con)


def _sync_one(tenant_id: int) -> None:
    from tools.sync_binance_spot import sync_tenant  # noqa: PLC0415
    sync_tenant(tenant_id)


def _sync_cycle(stop_event: threading.Event) -> None:
    try:
        tenants = _active_tenants()
    except Exception as e:  # noqa: BLE001
        log.warning("sync_loop: no se pudo listar tenants: %s", e)
        return
    for tid in tenants:
        if stop_event.is_set():
            break
        try:
            _sync_one(tid)
        except Exception as e:  # noqa: BLE001 — un tenant no tumba al resto
            log.warning("sync_loop tenant=%s falló: %s", tid, e)


def sync_loop(stop_event: threading.Event | None = None) -> None:
    """Sincroniza Binance de cada tenant ACTIVE cada SYNC_INTERVAL_SEC (alimenta
    observed_orders + el track_live de F3a). Spec §1."""
    if stop_event is None:
        stop_event = threading.Event()
    from api.config import load_config  # noqa: PLC0415
    interval = load_config().get("sync_interval_sec", SYNC_INTERVAL_SEC)
    while not stop_event.is_set():
        _sync_cycle(stop_event)
        stop_event.wait(interval)
```

Verificá que `threading` y `log` ya estén importados en `scanner/runtime.py` (lo están).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_liveness_loops.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Arrancar los loops en `start_scanner_thread`**

En `scanner/runtime.py::start_scanner_thread`, tras el bloque que arranca `calibrator_thread` (líneas ~453-461), añadir dos threads más, registrados en `_managed_threads` con el `_thread_stop_event` compartido (mismo patrón):

```python
    screener_thread = threading.Thread(
        target=screener_loop, name="screener_loop",
        kwargs={"stop_event": _thread_stop_event}, daemon=True,
    )
    screener_thread.start()
    _managed_threads.append(screener_thread)

    sync_thread = threading.Thread(
        target=sync_loop, name="sync_loop",
        kwargs={"stop_event": _thread_stop_event}, daemon=True,
    )
    sync_thread.start()
    _managed_threads.append(sync_thread)
```

Esto los hace arrancar en el lifespan (junto al scanner) y los junta `stop_managed_threads` en el teardown — sin cron externo, sin threads colgados.

- [ ] **Step 6: Verify + commit**

Run: `python -m pytest tests/test_liveness_loops.py -v` → 5 green.
Run: `python -m pytest tests/ -m "not network" -n auto -q` → sin regresiones (el teardown del lifespan junta los dos threads nuevos; confirmá que no aparecen tests de leak de threads fallando).

```bash
git add tools/run_valley_screener.py scanner/runtime.py tests/test_liveness_loops.py
git commit -m "feat(scanner): screener_loop + sync_loop como threads del lifespan (revive los huérfanos)"
```

---

### Task 5: frontend — `FreshnessTag` + mostrarlo en la Vista Valles

**Files:**
- Modify: `frontend/src/types.ts` (añadir `frescura` a `ValleySnapshot`), create `frontend/src/components/FreshnessTag.tsx` + `.module.css`
- Modify: `frontend/src/components/ValleysView.tsx`
- Test: `frontend/src/components/FreshnessTag.test.tsx`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/components/FreshnessTag.test.tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { FreshnessTag } from './FreshnessTag';

describe('FreshnessTag', () => {
  it('muerto: dice que no ha corrido', () => {
    render(<FreshnessTag frescura={{ estado: 'muerto', edad_seg: null, generated_at: null }} />);
    expect(screen.getByText(/no ha corrido|sin/i)).toBeInTheDocument();
  });
  it('rancio: muestra la antigüedad', () => {
    render(<FreshnessTag frescura={{ estado: 'rancio', edad_seg: 172800, generated_at: 'x' }} />);
    expect(screen.getByText(/rancia|rancio|hace/i)).toBeInTheDocument();
  });
  it('fresco: discreto', () => {
    const { container } = render(<FreshnessTag frescura={{ estado: 'fresco', edad_seg: 60, generated_at: 'x' }} />);
    expect(container.textContent).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/FreshnessTag.test.tsx`
Expected: FAIL (`FreshnessTag` no existe)

- [ ] **Step 3: Write minimal implementation**

En `frontend/src/types.ts`, añadir el tipo de frescura y agregarlo a `ValleySnapshot`:

```typescript
export interface Frescura {
  estado:       'fresco' | 'rancio' | 'muerto';
  edad_seg:     number | null;
  generated_at: string | null;
}
```
Y agregá `frescura?: Frescura;` a la interfaz `ValleySnapshot`.

Create `frontend/src/components/FreshnessTag.tsx`:

```typescript
// ============================================================
// FreshnessTag — la frescura del dato, honesta. fresco/rancio/
// muerto. Liveness operacional: el dato dice su edad; el sistema
// no finge frescura. Spec §3.
// ============================================================
import React from 'react';
import type { Frescura } from '../types';
import styles from './FreshnessTag.module.css';

function _hace(edad_seg: number | null): string {
  if (edad_seg == null) return '';
  const h = edad_seg / 3600;
  if (h < 1) return `hace ${Math.round(edad_seg / 60)} min`;
  if (h < 48) return `hace ${Math.round(h)} h`;
  return `hace ${Math.round(h / 24)} días`;
}

export const FreshnessTag: React.FC<{ frescura?: Frescura }> = ({ frescura }) => {
  if (!frescura) return null;
  if (frescura.estado === 'muerto') {
    return <span className={`${styles.tag} ${styles.muerto}`}>sin foto — el screener no ha corrido</span>;
  }
  if (frescura.estado === 'rancio') {
    return <span className={`${styles.tag} ${styles.rancio}`}>foto {_hace(frescura.edad_seg)} · rancia</span>;
  }
  return <span className={`${styles.tag} ${styles.fresco}`}>foto {_hace(frescura.edad_seg)}</span>;
};
```

```css
/* frontend/src/components/FreshnessTag.module.css */
.tag { font-size: 0.8em; padding: 1px 6px; border-radius: 4px; }
.fresco { opacity: 0.6; }
.rancio { color: #c79a3a; }
.muerto { color: #c0564b; }
```

En `frontend/src/components/ValleysView.tsx`, importá `FreshnessTag` y mostralo en el bloque `meta` (junto a la cobertura) usando `snapshot.frescura`:
```tsx
import { FreshnessTag } from './FreshnessTag';
// dentro del div .meta, añadir:
        <FreshnessTag frescura={snapshot.frescura} />
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/FreshnessTag.test.tsx` → 3 green.
Run: `cd frontend && npm test` → todo verde (incl. ValleysView, que ahora renderiza el tag).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/FreshnessTag.tsx frontend/src/components/FreshnessTag.module.css frontend/src/components/ValleysView.tsx frontend/src/components/FreshnessTag.test.tsx
git commit -m "feat(fe): FreshnessTag — la Vista Valles muestra fresco/rancio/muerto (no finge frescura)"
```

---

### Task 6: el gate de liveness (CLAUDE.md) + el inventario enumerado

**Files:**
- Modify: `CLAUDE.md` (añadir el no-negociable)
- Create: `docs/superpowers/inventario-estado-vivo.md`

- [ ] **Step 1: Crear el inventario**

Create `docs/superpowers/inventario-estado-vivo.md`:

```markdown
# Inventario de estado vivo (liveness operacional)

Lista CERRADA de todo reader de estado vivo que cruza una frontera de proceso
(writer ≠ reader en el tiempo). Cada entrada está **migrada** (usa `LiveSnapshot`
+ tiene owner de frescura), **respira-vía-scanner** (alimentada por un thread del
lifespan; deuda: aún sin el tipo `LiveSnapshot`), o **deuda #N** (pendiente).

Spec: `docs/superpowers/specs/es/2026-06-13-liveness-frescura-huerfanos-design.md`.

| Reader | Writer | Owner de frescura en prod | Frescura en contrato | Estado |
|---|---|---|---|---|
| `GET /valley-candidates` | `tools.run_valley_screener.regenerate` | `screener_loop` (lifespan, 6h) | `LiveSnapshot` | migrado |
| `GET /dossier/{symbol}` | `research.dossier.build_dossier_live` | on-request (auto-cura) | `LiveSnapshot` | migrado |
| `observed_orders` + F3a `track_live` | `tools.sync_binance_spot.sync_tenant` | `sync_loop` (lifespan, 5min) | estado en DB (`updated_at`) | migrado (latido); deuda: sin `LiveSnapshot` en el reader |
| `symbols_status.json` | `update_symbols_json` | `scanner_loop` (lifespan) | trae `updated_at` | respira-vía-scanner; deuda: sin `LiveSnapshot` |
| `equity` | computado on-read | n/a (vivo por consulta) | n/a | respira |
| `kill_switch state` | `health_monitor_loop` | lifespan | observability | respira-vía-scanner; deuda: sin `LiveSnapshot` |

**Patrón ARMADO, no cerrado:** los 3 órganos con frontera-de-proceso-y-snapshot
están migrados; los que respiran vía scanner están nombrados como deuda y se
migran al tocarlos (el gate lo fuerza).
```

- [ ] **Step 2: Añadir el gate a CLAUDE.md**

En `CLAUDE.md`, en la sección `## Non-Negotiables`, añadir un punto nuevo:

```markdown
8. **Toda pieza con estado vivo cruzando una frontera de proceso declara su owner de frescura y emite su frescura en el contrato.** Si el writer y el reader no son el mismo acto temporal (un snapshot/archivo/caché que algo regenera), la pieza DEBE: (a) tener un owner de frescura nombrado — quién la corre en prod y con qué cadencia (un thread del lifespan en `scanner/runtime.py`, no un comando manual); y (b) emitir su estado vivo vía `freshness.LiveSnapshot` (la frescura `fresco/rancio/muerto` en el contrato, nunca un vacío mudo que enmascara la muerte). Una pieza nueva sin owner de frescura o sin `LiveSnapshot` no mergea. El registro vive en [[docs/superpowers/inventario-estado-vivo.md]]; tocar un reader no-migrado de esa lista sin migrarlo es una violación del gate. Raíz: la frescura es una propiedad SEMÁNTICA del dato, no un cron que rezás. See the liveness spec `docs/superpowers/specs/es/2026-06-13-liveness-frescura-huerfanos-design.md`.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/superpowers/inventario-estado-vivo.md
git commit -m "docs: gate de liveness en CLAUDE.md + inventario enumerado de estado vivo (patrón armado)"
```

---

## Verificación final

- [ ] **Backend:** `python -m pytest tests/test_freshness.py tests/test_valleys_api.py tests/test_dossier_api.py tests/test_active_credential_tenants.py tests/test_liveness_loops.py -v` → todo verde.
- [ ] **Gate rápido (CI):** `python -m pytest tests/ -m "not network" -n auto -q` → sin regresiones (especialmente los tests de teardown/leak de threads del lifespan, que ahora juntan 5 threads).
- [ ] **Frontend:** `cd frontend && npm test` → verde (incl. `FreshnessTag`).
- [ ] **Vivo (deliberado, en prod tras deploy):** al arrancar el contenedor, los logs muestran `screener_loop` y `sync_loop` arrancando; a los pocos minutos la pestaña Valles se puebla sola y `frescura.estado` es `fresco`; el tracker de F3a avanza con cada sync. Ese es el entregable: los huérfanos laten, y ningún reader finge frescura.
```
