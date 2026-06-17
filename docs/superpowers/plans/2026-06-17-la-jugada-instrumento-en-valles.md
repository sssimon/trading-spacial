# La Jugada — el Instrumento en el flujo de Valles — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrar 1:1 el handoff de diseño "La Jugada" como cuarta lente del flujo cálido de Valles, con su ciclo completo (derivar → fijar → en curso → conducta al cierre), montando el gráfico de velas con `lightweight-charts` (igual que el resto de la app) más la capa de overlays del diseño.

**Architecture:** El backend ya tiene el Instrumento (`api/plan.py`: `/plan/derive`, `/plan/confirm`, `/plan/{symbol}`). Se enriquece su contrato (metadata de paredes + frescura `LiveSnapshot` + lectura de conducta) y se construye la UI cálida en `frontend/src/components/valles/jugada/`, enchufada en `ValleysFlow` después de Niveles. El gráfico reusa el patrón de `ChartCanvas` (`SymbolDetail.tsx`): `createChart` + `getOhlcv` + `ResizeObserver` + `cssVar`, con tema cálido y una capa HTML de anotaciones posicionada con `series.priceToCoordinate()`.

**Tech Stack:** FastAPI + pytest (backend); React 18 + TypeScript + Vite + vitest + `lightweight-charts ^4.2.0` (frontend). CSS modules cálidos scoped a `.vwRoot` (tokens papel/arcilla/salvia/ocre, Source Serif 4 + Instrument Sans).

**Fuentes 1:1:** handoff extraído en `C:\Users\simon\.claude\uploads\a526b7ee-fc37-4ba1-84e9-7acc5ee2f4ac\extracted\` — archivos load-bearing: `jugada-chart.jsx`, `jugada-candles.jsx`, `jugada-ladder.jsx`, `jugada-screens.jsx`, `jugada-chart-app.jsx`, `data-jugada.jsx`, `jugada-warm.css`, `jugada-chart.css`. Kickoff: `docs/superpowers/specs/es/2026-06-17-kickoff-instrumento-screener-design.md`.

**Regla de copy (transversal a TODO el plan):** el handoff viene en voseo argentino; se implementa en **tuteo venezolano**. Tabla de remapeo obligatoria:

| Voseo (handoff) | Tuteo (implementar) |
|---|---|
| si decidís entrar | si decides entrar |
| hasta dónde aguantás | hasta dónde aguantas |
| salís por partes | sales por partes |
| lo que dejás corriendo | lo que dejas corriendo |
| fijá la jugada / fijás | fija la jugada / fijas |
| revisá en Binance | revisa en Binance |
| podés cerrar / hacés / mirás | puedes cerrar / haces / miras |
| ¿honraste tu plan? | (igual — pretérito, no cambia) |
| tu stop sube a break-even | (igual) |

Cualquier string nuevo que el handoff traiga en voseo se remapea con esta tabla al portarlo. El microcopy final puede pulirse luego con `solace-wren`; este plan implementa la versión tuteo directa.

---

## File Structure

**Backend (modificar):**
- `api/plan.py` — enriquecer `_plan_payload` (metadata de paredes), envolver `vista()` en `LiveSnapshot`, añadir `GET /plan/{symbol}/conducta`.
- `db/lifecycle_states.py` — asegurar que `db_get_active_state` devuelve `updated_at` (para la frescura).
- `db/conduct_episodes.py` — helper de lectura `db_get_latest_episode` (si no existe).

**Backend (tests):**
- `tests/test_plan_api.py` — contrato enriquecido, frescura, conducta.

**Frontend (crear):**
- `frontend/src/components/valles/jugada/types.ts` — tipos locales de la jugada (o extender `frontend/src/types.ts`).
- `frontend/src/components/valles/jugada/useJugada.ts` — hook que orquesta derive/live/conduct.
- `frontend/src/components/valles/jugada/LaJugadaChart.tsx` — gráfico de velas + overlays.
- `frontend/src/components/valles/jugada/overlays.ts` — cálculo puro de geometría de overlays (testeable sin DOM).
- `frontend/src/components/valles/jugada/JugadaDerivada.tsx`
- `frontend/src/components/valles/jugada/JugadaGate.tsx` (gate + fijada)
- `frontend/src/components/valles/jugada/JugadaLive.tsx`
- `frontend/src/components/valles/jugada/JugadaConducta.tsx`
- `frontend/src/components/valles/jugada/JugadaSinJugada.tsx`
- `frontend/src/components/valles/jugada/jugada.module.css` — port de `jugada-warm.css` + `jugada-chart.css` (scoped).

**Frontend (modificar):**
- `frontend/src/api.ts` — fetchers `getPlanDerive`, `confirmPlan`, `getPlanLive`, `getPlanConducta`.
- `frontend/src/types.ts` — tipos del contrato `/plan`.
- `frontend/src/components/valles/ValleysFlow.tsx` — añadir paso `jugada` (4ta lente).
- `frontend/src/components/valles/PickScreen.tsx` — marca "tiene jugada".

**Frontend (tests):**
- `frontend/src/components/valles/jugada/overlays.test.ts`
- `frontend/src/components/valles/jugada/useJugada.test.ts`
- `frontend/src/api.plan.test.ts`

---

## Phase A — Backend: contrato para 1:1 + frescura #8

### Task A1: Enriquecer `_plan_payload` con la metadata de paredes que el diseño dibuja

El diseño renderiza por peldaño "techo · N toques" y para el SL "debajo del piso de $Y". Hoy `_plan_payload` (`api/plan.py:41-46`) descarta `zona_origen` y devuelve `sl_plan` como float pelado. Hay que exponer la metadata sin romper los campos actuales (aditivo).

**Files:**
- Modify: `api/plan.py:41-46`
- Test: `tests/test_plan_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan_api.py
from instrument.plan import derive_plan
from api.plan import _plan_payload

def _zonas():
    return [
        {"tipo": "soporte", "precio_bajo": 0.388, "precio_alto": 0.398, "centro": 0.392, "toques": 5},
        {"tipo": "resistencia", "precio_bajo": 0.445, "precio_alto": 0.451, "centro": 0.448, "toques": 2},
        {"tipo": "resistencia", "precio_bajo": 0.470, "precio_alto": 0.478, "centro": 0.474, "toques": 4},
    ]

def test_plan_payload_incluye_metadata_de_paredes():
    plan = derive_plan(_zonas(), 0.419)
    p = _plan_payload(plan)
    # rungs llevan la pared de origen (centro + toques) para "techo · N toques"
    assert p["rungs"][0]["zona"]["toques"] == 2
    assert p["rungs"][0]["zona"]["centro"] == 0.448
    # el SL expone el piso que lo fijó, para "debajo del piso de $Y"
    assert p["sl_piso"]["centro"] == 0.392
    assert p["sl_piso"]["precio_bajo"] == 0.388
    # campos legacy intactos
    assert p["sl_plan"] == plan.sl_price
    assert p["entry"] == plan.entry_price
    assert p["entry_zone"]["toques"] == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plan_api.py::test_plan_payload_incluye_metadata_de_paredes -v`
Expected: FAIL (`KeyError: 'zona'` / `'sl_piso'`).

- [ ] **Step 3: Implement**

```python
# api/plan.py — reemplazar _plan_payload
def _zona_meta(z: dict | None) -> dict | None:
    if not z:
        return None
    return {"centro": z.get("centro"), "precio_bajo": z.get("precio_bajo"),
            "precio_alto": z.get("precio_alto"), "toques": z.get("toques")}


def _plan_payload(plan) -> dict:
    # `zona_origen` lo pone derive_plan en cada Rung; el soporte del SL se reconstruye
    # del rung-base si existe, si no queda None (escalera sin soporte → SL = entry).
    sl_piso = None
    if plan.entry_zone is not None and plan.sl_price < plan.entry_price:
        # el SL se fijó bajo un soporte; entry_zone es el soporte donde se sienta el entry
        sl_piso = _zona_meta(plan.entry_zone)
    return {"entry": plan.entry_price,
            "sl_plan": plan.sl_price,
            "sl_piso": sl_piso,
            "rungs": [{"tp_price": r.tp_price, "size_frac": r.size_frac,
                       "zona": _zona_meta(r.zona_origen)} for r in plan.rungs],
            "runner_frac": plan.runner_frac,
            "entry_zone": plan.entry_zone}
```

> Nota de fidelidad: `derive_plan` fija el SL desde `soporte` (el soporte inmediato bajo el entry, `plan.py:45-47,55`), que NO siempre es `entry_zone`. Si querés el piso EXACTO del SL (no el de la zona de entrada), extendé `Plan` con un campo `sl_zona: dict|None` en `instrument/plan.py` (guardar `soporte` en `derive_plan`) y usalo acá. Para v1, `entry_zone` como aproximación del piso es aceptable cuando coinciden; si el test de arriba exige el piso real, hacé la extensión de `Plan` primero. **Decisión de implementación: extender `Plan.sl_zona`** — es lo correcto para 1:1. Ajustá el test y `_plan_payload` para leer `plan.sl_zona`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_plan_api.py::test_plan_payload_incluye_metadata_de_paredes -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/plan.py instrument/plan.py tests/test_plan_api.py
git commit -m "feat(plan): enriquecer payload con metadata de paredes (toques/piso) para La Jugada 1:1"
```

### Task A2: Envolver `GET /plan/{symbol}` en `LiveSnapshot` (No-Negociable #8)

La vista viva debe emitir su frescura en el contrato. `LiveSnapshot(payload, generated_at, umbral_seg).to_response()` añade `frescura:{estado,edad_seg,generated_at,umbral_seg}` (`freshness.py:44-48`). El `generated_at` es el `updated_at` de la fila.

**Files:**
- Modify: `api/plan.py:115-136` (`vista`), `db/lifecycle_states.py` (`db_get_active_state` debe devolver `updated_at`)
- Test: `tests/test_plan_api.py`

- [ ] **Step 1: Verify `db_get_active_state` returns `updated_at`**

Run: `grep -n "updated_at" db/lifecycle_states.py`
Si la `SELECT` de `db_get_active_state` no incluye `updated_at`, añadilo a la lista de columnas seleccionadas y al dict devuelto.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_plan_api.py
def test_vista_emite_frescura_en_el_contrato(monkeypatch):
    # fila viva fresca → frescura.estado == 'fresco'; sin fila → estado_vivo None sin frescura obligatoria
    from datetime import datetime, timezone
    import api.plan as plan_api

    now = datetime.now(timezone.utc).isoformat()
    fake_row = {
        "plan_json": '{"entry_price":0.419,"entry_zone":null,"sl_price":0.385,"rungs":[],"runner_frac":0.05}',
        "rungs_llenos_json": "[]", "be_movido": 0, "estado_vivo": "activo",
        "sl_actual": 0.385, "fase": "CONFIRMED", "size_restante_frac": 1.0, "updated_at": now,
    }
    monkeypatch.setattr(plan_api, "db_get_active_state", lambda con, **kw: fake_row)
    monkeypatch.setattr(plan_api, "snapshot_connection", lambda: __import__("contextlib").nullcontext(None))
    out = plan_api.vista("ADAUSDT", tenant_id=1)
    assert out["frescura"]["estado"] == "fresco"
    assert out["frescura"]["generated_at"] == now
    assert out["estado_vivo"] == "activo"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_plan_api.py::test_vista_emite_frescura_en_el_contrato -v`
Expected: FAIL (`KeyError: 'frescura'`).

- [ ] **Step 4: Implement**

```python
# api/plan.py — al inicio del módulo
from freshness import LiveSnapshot
PLAN_FRESCURA_UMBRAL_SEG = 900.0   # 3× la cadencia de track_live (5 min); coherente con scanner

# api/plan.py — reescribir el return final de vista() (líneas 129-136)
    payload = {
        "symbol": symbol, "estado_vivo": row["estado_vivo"],
        "plan": _plan_payload(plan),
        "realidad": {"fase": row["fase"], "rungs_llenos": rungs_llenos,
                     "sl_actual": row["sl_actual"], "be_movido": bool(row["be_movido"]),
                     "size_restante_frac": row["size_restante_frac"]},
        "hechos": hechos,
    }
    return LiveSnapshot(payload=payload, generated_at=row["updated_at"],
                        umbral_seg=PLAN_FRESCURA_UMBRAL_SEG).to_response()
```

> El caso `row is None` (sin plan activo, `api/plan.py:122-123`) sigue devolviendo `{"symbol", "estado_vivo": None}` sin frescura: no hay estado vivo que clasificar.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_plan_api.py::test_vista_emite_frescura_en_el_contrato -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/plan.py db/lifecycle_states.py tests/test_plan_api.py
git commit -m "feat(plan): vista viva emite frescura via LiveSnapshot (No-Negociable #8)"
```

### Task A3: `GET /plan/{symbol}/conducta` — lectura de conducta al cierre

La pantalla de conducta lee el `EpisodioDeConducción` del último plan cerrado (`conduct_episodes`). Hechos de conducta, sin PnL.

**Files:**
- Modify: `api/plan.py` (nuevo endpoint), `db/conduct_episodes.py` (helper de lectura si falta)
- Test: `tests/test_plan_api.py`

- [ ] **Step 1: Verify read helper exists**

Run: `grep -n "def db_get" db/conduct_episodes.py`
Si no hay un getter por `tenant_id`+`symbol`, añadí `db_get_latest_episode(con, *, tenant_id, symbol) -> dict | None` que haga `SELECT ... FROM conduct_episodes WHERE tenant_id=? AND symbol=? ORDER BY closed_at DESC LIMIT 1`.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_plan_api.py
def test_conducta_devuelve_campos_sin_pnl(monkeypatch):
    import api.plan as plan_api
    fake_ep = {"symbol": "ADAUSDT", "entry_en_zona": 1, "sl_respetado": 1,
               "adherencia_be": 1, "rungs_honrados": 1, "cierre_en_plan": 0,
               "hold_hours": 51.0, "closed_at": "2026-06-17T00:00:00+00:00"}
    monkeypatch.setattr(plan_api, "db_get_latest_episode", lambda con, **kw: fake_ep, raising=False)
    monkeypatch.setattr(plan_api, "snapshot_connection", lambda: __import__("contextlib").nullcontext(None))
    out = plan_api.conducta("ADAUSDT", tenant_id=1)
    assert out["estado_vivo"] == "cerrado"
    assert "pnl" not in out and "pnl_usd" not in out
    assert any(c["k"] == "Entraste en la zona" and c["ok"] == "si" for c in out["campos"])
    assert any(c["k"] == "Cerraste según el plan" and c["ok"] == "no" for c in out["campos"])
```

- [ ] **Step 3: Implement**

```python
# api/plan.py
from db.conduct_episodes import db_get_latest_episode

_CONDUCT_FIELDS = [
    ("entry_en_zona", "Entraste en la zona"),
    ("sl_respetado", "Respetaste el stop"),
    ("adherencia_be", "Moviste a break-even"),
    ("rungs_honrados", "Honraste los peldaños"),
    ("cierre_en_plan", "Cerraste según el plan"),
]

@router.get("/plan/{symbol}/conducta", summary="Lectura de conducta del último cierre (sin PnL)")
def conducta(symbol: str, tenant_id: int = Depends(get_current_tenant_id)) -> dict:
    symbol = symbol.upper()[:20]
    with snapshot_connection() as con:
        ep = db_get_latest_episode(con, tenant_id=tenant_id, symbol=symbol)
    if ep is None:
        return {"symbol": symbol, "estado_vivo": None}
    campos = [{"k": label, "ok": ("si" if ep[key] else "no")} for key, label in _CONDUCT_FIELDS]
    campos.append({"k": "Cuánto aguantaste", "ok": "dato",
                   "v": f"{round(ep['hold_hours'])} h"})
    honrada = all(ep[k] for k, _ in _CONDUCT_FIELDS)
    return {"symbol": symbol, "estado_vivo": "cerrado",
            "titular": "Honraste el plan que aprobaste." if honrada
                       else "Esta vez te saliste del plan. Sin reproche — solo el espejo.",
            "campos": campos}
```

- [ ] **Step 4: Run test; Step 5: Commit**

Run: `python -m pytest tests/test_plan_api.py -v`
```bash
git add api/plan.py db/conduct_episodes.py tests/test_plan_api.py
git commit -m "feat(plan): GET /plan/{symbol}/conducta — lectura de conducta sin PnL"
```

---

## Phase B — Frontend: tipos + fetchers

### Task B1: Tipos del contrato `/plan`

**Files:**
- Modify: `frontend/src/types.ts` (añadir al final)

- [ ] **Step 1: Add types**

```typescript
// frontend/src/types.ts — La Jugada / Instrumento
export interface PlanZonaMeta { centro: number; precio_bajo: number; precio_alto: number; toques: number; }
export interface PlanRung { tp_price: number; size_frac: number; zona: PlanZonaMeta | null; }
export interface PlanDerived {
  symbol?: string;
  entry: number;
  sl_plan: number;
  sl_piso: PlanZonaMeta | null;
  rungs: PlanRung[];
  runner_frac: number;
  entry_zone: PlanZonaMeta | null;
}
export interface PlanFrescura { estado: 'fresco' | 'rancio' | 'muerto'; edad_seg: number | null; generated_at: string | null; umbral_seg: number; }
export interface PlanLive {
  symbol: string;
  estado_vivo: 'activo' | 'incierto' | 'cerrado' | null;
  plan?: PlanDerived;
  realidad?: { fase: string; rungs_llenos: number[]; sl_actual: number | null; be_movido: boolean; size_restante_frac: number | null; };
  hechos?: string[];
  frescura?: PlanFrescura;
}
export interface PlanConductaField { k: string; ok: 'si' | 'no' | 'dato'; v?: string; }
export interface PlanConducta { symbol: string; estado_vivo: 'cerrado' | null; titular?: string; campos?: PlanConductaField[]; }
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no nuevos errores.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types.ts
git commit -m "feat(valles): tipos del contrato /plan (La Jugada)"
```

### Task B2: Fetchers en `api.ts`

Mirror del patrón existente (`getLevels` en `api.ts:427`, `getOhlcv` en `api.ts:184`, POST como `updateConfig` en `api.ts:203`).

**Files:**
- Modify: `frontend/src/api.ts`
- Test: `frontend/src/api.plan.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/api.plan.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as api from './api';

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true, status: 200, json: async () => ({ entry: 0.419, sl_plan: 0.385, sl_piso: null, rungs: [], runner_frac: 0.05, entry_zone: null }),
  })));
});

describe('plan fetchers', () => {
  it('getPlanDerive pega a /plan/derive con entry_price', async () => {
    await api.getPlanDerive('ADAUSDT', 0.4205);
    const url = (fetch as any).mock.calls[0][0] as string;
    expect(url).toContain('/plan/derive/ADAUSDT');
    expect(url).toContain('entry_price=0.4205');
  });
});
```

- [ ] **Step 2: Run; verify fails**

Run: `cd frontend && npx vitest run src/api.plan.test.ts`
Expected: FAIL (`getPlanDerive is not a function`).

- [ ] **Step 3: Implement**

```typescript
// frontend/src/api.ts — bloque "La Jugada / Instrumento — /plan"
import type { PlanDerived, PlanLive, PlanConducta } from './types';

export function getPlanDerive(symbol: string, entryPrice: number) {
  return request<PlanDerived>(`/plan/derive/${symbol}?entry_price=${entryPrice}`);
}
export function getPlanLive(symbol: string) {
  return request<PlanLive>(`/plan/${symbol}`);
}
export function getPlanConducta(symbol: string) {
  return request<PlanConducta>(`/plan/${symbol}/conducta`);
}
export function confirmPlan(symbol: string, entryPrice: number, positionId?: number) {
  return request<{ symbol: string; estado_vivo: string; plan: PlanDerived }>('/plan/confirm', {
    method: 'POST',
    body: JSON.stringify({ symbol, entry_price: entryPrice, position_id: positionId ?? null }),
  });
}
```

> **Verificar auth de `confirmPlan`:** `POST /plan/confirm` usa `verify_api_key` (`api/plan.py:80`), no la cookie de sesión. Revisá cómo otros writes (p.ej. el dock copiloto / posiciones) pasan la API key en `request()` (header `X-API-Key` o equivalente) y replicá el mecanismo. Si `request()` ya inyecta CSRF+key para no-GET, no hace falta nada extra.

- [ ] **Step 4: Run; Step 5: Commit**

Run: `cd frontend && npx vitest run src/api.plan.test.ts` → PASS
```bash
git add frontend/src/api.ts frontend/src/api.plan.test.ts
git commit -m "feat(valles): fetchers /plan (derive/confirm/live/conducta)"
```

---

## Phase C — El gráfico (lightweight-charts + overlays)

### Task C1: Geometría pura de overlays (testeable sin DOM)

Separá el cálculo precio→coordenada y la forma de cada overlay en un módulo puro (lo que en el handoff vive dentro de `recompute()` de `jugada-chart.jsx`). Esto se testea sin canvas.

**Files:**
- Create: `frontend/src/components/valles/jugada/overlays.ts`
- Test: `frontend/src/components/valles/jugada/overlays.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// overlays.test.ts
import { describe, it, expect } from 'vitest';
import { buildOverlays } from './overlays';
import type { PlanDerived } from '../../../types';

const plan: PlanDerived = {
  entry: 0.419, sl_plan: 0.385, sl_piso: { centro: 0.392, precio_bajo: 0.388, precio_alto: 0.398, toques: 5 },
  entry_zone: { centro: 0.419, precio_bajo: 0.412, precio_alto: 0.426, toques: 5 },
  rungs: [
    { tp_price: 0.448, size_frac: 0.50, zona: { centro: 0.448, precio_bajo: 0.445, precio_alto: 0.451, toques: 2 } },
    { tp_price: 0.474, size_frac: 0.20, zona: { centro: 0.474, precio_bajo: 0.470, precio_alto: 0.478, toques: 4 } },
  ],
  runner_frac: 0.05,
};

describe('buildOverlays', () => {
  it('zona de entrada es banda (no línea): top != bottom', () => {
    const o = buildOverlays({ plan, live: 0.4205, state: null });
    expect(o.zone).toBeTruthy();
    expect(o.zone!.priceLow).toBe(0.412);
    expect(o.zone!.priceHigh).toBe(0.426);
  });
  it('precio dentro de la zona → live.fuera = false; arriba → true', () => {
    expect(buildOverlays({ plan, live: 0.4205, state: null }).live.fuera).toBe(false);
    expect(buildOverlays({ plan, live: 0.4400, state: null }).live.fuera).toBe(true);
  });
  it('rung lleno cuando state.rungs_llenos lo incluye; BE mueve el stop', () => {
    const o = buildOverlays({ plan, live: 0.45, state: { rungs_llenos: [0], be_movido: true, sl_actual: 0.419 } });
    expect(o.rungs[0].filled).toBe(true);
    expect(o.rungs[1].filled).toBe(false);
    expect(o.stop.be).toBe(true);
    expect(o.stop.price).toBe(0.419);
  });
  it('escalera corta (1 rung) marca gap honesto', () => {
    const corto = { ...plan, rungs: [plan.rungs[0]] };
    expect(buildOverlays({ plan: corto, live: 0.4205, state: null }).gap).toBe(true);
  });
});
```

- [ ] **Step 2: Run; verify fails.** Run: `cd frontend && npx vitest run src/components/valles/jugada/overlays.test.ts` → FAIL.

- [ ] **Step 3: Implement** (portar la lógica de `jugada-chart.jsx::recompute` a datos reales)

```typescript
// overlays.ts
import type { PlanDerived } from '../../../types';

export interface LiveState { rungs_llenos: number[]; be_movido: boolean; sl_actual: number | null; }
export interface OverlayModel {
  zone: { priceLow: number; priceHigh: number; toques: number } | null;
  stop: { price: number; be: boolean; piso: number | null };
  rungs: { price: number; sizeFrac: number; toques: number | null; filled: boolean }[];
  runner: { frac: number; fromPrice: number } | null;
  live: { price: number; fuera: 'arriba' | 'abajo' | false };
  gap: boolean;  // escalera corta: no hay más techos
}

export function buildOverlays(args: { plan: PlanDerived; live: number; state: LiveState | null }): OverlayModel {
  const { plan, live, state } = args;
  const z = plan.entry_zone;
  let fuera: 'arriba' | 'abajo' | false = false;
  if (z) { if (live > z.precio_alto) fuera = 'arriba'; else if (live < z.precio_bajo) fuera = 'abajo'; }
  const llenos = state?.rungs_llenos ?? [];
  const topRung = plan.rungs.length ? plan.rungs[plan.rungs.length - 1].tp_price : plan.entry;
  return {
    zone: z ? { priceLow: z.precio_bajo, priceHigh: z.precio_alto, toques: z.toques } : null,
    stop: { price: state?.be_movido ? (state.sl_actual ?? plan.sl_plan) : plan.sl_plan,
            be: !!state?.be_movido, piso: plan.sl_piso?.centro ?? null },
    rungs: plan.rungs.map((r, i) => ({ price: r.tp_price, sizeFrac: r.size_frac,
            toques: r.zona?.toques ?? null, filled: llenos.includes(i) })),
    runner: plan.runner_frac > 0 ? { frac: plan.runner_frac, fromPrice: topRung } : null,
    live: { price: live, fuera },
    gap: plan.rungs.length === 1,
  };
}
```

- [ ] **Step 4: Run → PASS. Step 5: Commit**

```bash
git add frontend/src/components/valles/jugada/overlays.ts frontend/src/components/valles/jugada/overlays.test.ts
git commit -m "feat(jugada): geometria pura de overlays (zona/stop/escalera/runner/gap)"
```

### Task C2: CSS cálido del gráfico y las pantallas

Port verbatim de `jugada-warm.css` + `jugada-chart.css` (del handoff) a un único módulo, scoped, reusando los tokens cálidos ya existentes de Valles.

**Files:**
- Create: `frontend/src/components/valles/jugada/jugada.module.css`

- [ ] **Step 1: Port**

Transformaciones al copiar `extracted/jugada-warm.css` + `extracted/jugada-chart.css`:
1. Concatenar ambos en `jugada.module.css`.
2. Eliminar la redefinición de tokens `--paper/--clay/--sage/--ochre/...` si ya están en `valles.module.css`/`warm-tokens.css`; si el módulo está aislado, mantenerlos (no filtran fuera del scope CSS-module).
3. Prefijar/anidar todo bajo el scope de Valles (las clases ya son `ju-*`; en CSS-modules quedan locales automáticamente — no hace falta `.vwRoot` manual salvo para heredar tokens; importar el módulo y usar `styles['ju-chart']`).
4. NO tocar valores de color, tamaños, easings, hatching: es 1:1 visual.

- [ ] **Step 2: Verify build picks it up** (se valida al usarlo en C3). Commit:

```bash
git add frontend/src/components/valles/jugada/jugada.module.css
git commit -m "feat(jugada): CSS calido del grafico y pantallas (port 1:1)"
```

### Task C3: `LaJugadaChart.tsx` — velas reales + capa de overlays

Montaje idéntico al `ChartCanvas` de `SymbolDetail.tsx:214-346` (createChart + getOhlcv + ResizeObserver + cssVar + cleanup), tema cálido del handoff (`jugada-chart.jsx:40-56`), y la capa `.ju-chart__ann` posicionada con `series.priceToCoordinate()`.

**Files:**
- Create: `frontend/src/components/valles/jugada/LaJugadaChart.tsx`

- [ ] **Step 1: Implement el armazón del gráfico**

```tsx
// LaJugadaChart.tsx
import React, { useEffect, useRef, useState } from 'react';
import { createChart, type IChartApi, type ISeriesApi } from 'lightweight-charts';
import { getOhlcv } from '../../../api';
import type { PlanDerived } from '../../../types';
import { buildOverlays, type LiveState } from './overlays';
import styles from './jugada.module.css';

export const LaJugadaChart: React.FC<{
  symbol: string; plan: PlanDerived; live: number; state?: LiveState | null;
  phaseLabel: string; height?: number;
}> = ({ symbol, plan, live, state = null, phaseLabel, height = 420 }) => {
  const wrap = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const [, force] = useState(0);   // re-render para recolocar overlays

  useEffect(() => {
    if (!wrap.current) return;
    const chart = createChart(wrap.current, {
      layout: { background: { type: 'solid' as never, color: 'transparent' }, textColor: '#8A8270',
                fontFamily: "'Instrument Sans', sans-serif", fontSize: 11 },
      grid: { vertLines: { visible: false }, horzLines: { color: 'rgba(228,220,204,0.55)' } },
      rightPriceScale: { borderColor: '#E4DCCC', scaleMargins: { top: 0.07, bottom: 0.07 }, entireTextOnly: true },
      timeScale: { visible: false, borderVisible: false },
      crosshair: { mode: 0 as never },
      handleScroll: false, handleScale: false,
      width: wrap.current.clientWidth, height,
    });
    const series = chart.addCandlestickSeries({
      upColor: '#DAD0BD', downColor: '#9E947F', borderUpColor: '#A89A82', borderDownColor: '#6F6657',
      wickUpColor: '#A89A82', wickDownColor: '#7C7263', priceLineVisible: false, lastValueVisible: false,
      priceFormat: { type: 'price', precision: 4, minMove: 0.0001 },
    });
    chartRef.current = chart; seriesRef.current = series;

    getOhlcv(symbol, '1d', 180).then((res) => {
      series.setData(res.candles.map((c) => ({ time: c.time as never, open: c.open, high: c.high, low: c.low, close: c.close })));
      chart.timeScale().fitContent();
      force((n) => n + 1);
    });

    const ro = new ResizeObserver(() => { chart.applyOptions({ width: wrap.current!.clientWidth }); force((n) => n + 1); });
    ro.observe(wrap.current);
    const onRange = () => force((n) => n + 1);
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRange);
    return () => { ro.disconnect(); chart.remove(); chartRef.current = null; seriesRef.current = null; };
  }, [symbol, height]);

  // posición de cada overlay vía priceToCoordinate
  const s = seriesRef.current;
  const y = (p: number) => (s ? s.priceToCoordinate(p) : null);
  const ov = buildOverlays({ plan, live, state });

  return (
    <div className={styles['ju-chart']} style={{ height }}>
      <div ref={wrap} className={styles['ju-chart__canvas']} />
      <div className={styles['ju-chart__legend']}>{symbol} · diario · paredes de D.1</div>
      <div className={styles['ju-chart__phase']}>{phaseLabel}</div>
      <div className={styles['ju-chart__ann']}>
        {/* zona de entrada (banda) */}
        {ov.zone && y(ov.zone.priceHigh) != null && y(ov.zone.priceLow) != null && (
          <div className={styles['ju-ann-zone']}
               style={{ top: y(ov.zone.priceHigh)!, height: Math.max(2, y(ov.zone.priceLow)! - y(ov.zone.priceHigh)!) }}>
            <span className={styles['ju-ann-zone__lbl']}>ZONA DE ENTRADA<b>${ov.zone.priceLow}–${ov.zone.priceHigh}</b></span>
          </div>
        )}
        {/* runner */}
        {ov.runner && y(ov.runner.fromPrice) != null && (
          <div className={styles['ju-ann-runner']} style={{ top: 0, height: y(ov.runner.fromPrice)! }}>
            <span>runner · {Math.round(ov.runner.frac * 100)}% abierto ↑</span>
          </div>
        )}
        {/* escalera */}
        {ov.rungs.map((r, i) => y(r.price) != null && (
          <div key={i} className={`${styles['ju-ann-line']} ${styles['ju-ann--rung']} ${r.filled ? styles['is-filled'] : ''}`} style={{ top: y(r.price)! }}>
            <span className={styles['ju-ann-tag']}>{r.filled ? 'llena' : `salida ${i + 1}`} · ${r.price} · {Math.round(r.sizeFrac * 100)}%{r.toques != null ? ` · techo ${r.toques} toques` : ''}</span>
          </div>
        ))}
        {/* stop / break-even */}
        {y(ov.stop.price) != null && (
          <div className={`${styles['ju-ann-line']} ${ov.stop.be ? styles['ju-ann--be'] : styles['ju-ann--stop']}`} style={{ top: y(ov.stop.price)! }}>
            <span className={styles['ju-ann-tag']}>{ov.stop.be ? 'break-even' : 'stop'} · ${ov.stop.price}</span>
          </div>
        )}
        {/* precio vivo */}
        {y(ov.live.price) != null && (
          <div className={`${styles['ju-ann-live']} ${ov.live.fuera ? styles['ju-ann-live--fuera'] : ''}`} style={{ top: y(ov.live.price)! }}>
            {ov.live.fuera ? 'precio de ahora' : 'ahora'} ${ov.live.price}
          </div>
        )}
        {/* hueco honesto */}
        {ov.gap && (
          <div className={styles['ju-chart__gap']}>Arriba del primer techo no hay más paredes claras. La escalera queda corta — no se inventan techos.</div>
        )}
      </div>
    </div>
  );
};
```

> Notas de fidelidad: (a) el `time` real viene de `/ohlcv` (`OhlcvCandle.time`), no de las velas mock; los overlays se posicionan por PRECIO, así calzan con cualquier set real. (b) Mantené el workaround de "jiggle" de `jugada-chart.jsx:28-35` solo si ves el bitmap sin refrescar. (c) `crosshair.mode: 0` y `handleScroll/Scale:false` = lámina editorial, no terminal (1:1 con el handoff).

- [ ] **Step 2: Smoke test (jsdom mock de lightweight-charts)**

```tsx
// LaJugadaChart.test.tsx
import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
vi.mock('lightweight-charts', () => ({
  createChart: () => ({
    addCandlestickSeries: () => ({ setData: vi.fn(), priceToCoordinate: (p: number) => p * 1000 }),
    timeScale: () => ({ fitContent: vi.fn(), subscribeVisibleLogicalRangeChange: vi.fn() }),
    applyOptions: vi.fn(), remove: vi.fn(),
  }),
}));
vi.mock('../../../api', () => ({ getOhlcv: async () => ({ candles: [], volumes: [] }) }));
import { LaJugadaChart } from './LaJugadaChart';
const plan = { entry: 0.419, sl_plan: 0.385, sl_piso: null, entry_zone: { centro: 0.419, precio_bajo: 0.412, precio_alto: 0.426, toques: 5 }, rungs: [{ tp_price: 0.448, size_frac: 0.5, zona: { centro: 0.448, precio_bajo: 0.445, precio_alto: 0.451, toques: 2 } }], runner_frac: 0.05 };
describe('LaJugadaChart', () => {
  it('renderiza la banda de zona de entrada', async () => {
    const { findByText } = render(<LaJugadaChart symbol="ADAUSDT" plan={plan as never} live={0.4205} phaseLabel="derivada" />);
    expect(await findByText(/ZONA DE ENTRADA/)).toBeTruthy();
  });
});
```

Run: `cd frontend && npx vitest run src/components/valles/jugada/LaJugadaChart.test.tsx` → PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/valles/jugada/LaJugadaChart.tsx frontend/src/components/valles/jugada/LaJugadaChart.test.tsx
git commit -m "feat(jugada): grafico de velas (lightweight-charts) + capa de overlays 1:1"
```

---

## Phase D — Las pantallas (port de jugada-screens.jsx, tuteo)

> Cada pantalla porta el markup + clases de `extracted/jugada-screens.jsx` (componentes `JuDerivada/JuGate/JuGateFijada/JuLive/JuConducta/JuSinJugada`), reemplaza `window.*` por imports ES, los datos mock por props del hook (Task E1), e **inserta el copy en tuteo** (tabla de remapeo del header). El gráfico/escalera usa `LaJugadaChart` (Task C3) en vez de `SalidaLadder` (la lámina de divs queda descartada: el gráfico canónico es el de velas — requisito del usuario "montado como en la app").

### Task D1: `JugadaDerivada.tsx`
**Files:** Create `frontend/src/components/valles/jugada/JugadaDerivada.tsx`
- [ ] Portar `JuDerivada` (`jugada-screens.jsx`): eyebrow "calculado al precio de ahora", h2 "Si decides entrar, así se sale por partes.", `<LaJugadaChart phaseLabel="derivada · al precio de ahora" .../>`, 4 hechos (zona/stop/escalera/runner) y procedencia — todos en tuteo (§7.2 de la spec). Estados: dentro de zona, fuera de zona (precio ocre), escalera corta.
- [ ] Smoke test: render con `plan` mock → aparece "Si decides entrar".
- [ ] Commit: `feat(jugada): pantalla derivada (tuteo)`

### Task D2: `JugadaGate.tsx` (gate + fijada)
**Files:** Create `frontend/src/components/valles/jugada/JugadaGate.tsx`
- [ ] Portar `JuGate` + `JuGateFijada`: h2 "Fija la jugada, en frío.", mini-escalera de chips, disclaimer ocre "Confirmar no ejecuta…", botón "Fijar esta jugada" → llama `confirmPlan(sym, entry)`; al éxito muestra estado "Jugada fijada. Desde acá se sigue en vivo." Tuteo (§7.3-7.4).
- [ ] Test: click en "Fijar esta jugada" llama `confirmPlan` (mock) con `(symbol, entry)`.
- [ ] Commit: `feat(jugada): gate de confirmar + fijada (tuteo)`

### Task D3: `JugadaLive.tsx`
**Files:** Create `frontend/src/components/valles/jugada/JugadaLive.tsx`
- [ ] Portar `JuLive`: header con `FreshTag` leyendo `live.frescura.estado` (fresco salvia / rancio ocre) + edad; `<LaJugadaChart state={...} phaseLabel="en curso"/>`; hechos vivos desde `live.hechos[]` (el backend ya los trae — render directo, sin recomponer); avisos incierto/rancio. "◔ solo lectura · sin avisos". Tuteo (§7.5).
- [ ] Test: con `frescura.estado='rancio'` renderiza el aviso "puede haber cambiado".
- [ ] Commit: `feat(jugada): pantalla en curso con frescura (tuteo)`

### Task D4: `JugadaConducta.tsx`
**Files:** Create `frontend/src/components/valles/jugada/JugadaConducta.tsx`
- [ ] Portar `JuConducta`: h2 "¿Honraste tu plan?", titular desde `conducta.titular`, campos desde `conducta.campos[]` (`ok:'si'`→salvia ✓, `'no'`→ocre ○, `'dato'`→tinta ·), bloque espejo "Es un espejo, no un juez… sin PnL". Tuteo (§7.6).
- [ ] Test: campo con `ok:'no'` renderiza marca ○ y NO aparece ningún "$"/PnL.
- [ ] Commit: `feat(jugada): lectura de conducta sin PnL (tuteo)`

### Task D5: `JugadaSinJugada.tsx`
**Files:** Create `frontend/src/components/valles/jugada/JugadaSinJugada.tsx`
- [ ] Portar `JuSinJugada`: "Esta moneda no da estructura para una jugada." + "Sin paredes claras, no hay salida que escalonar" + procedencia. Tuteo (§7.7).
- [ ] Commit: `feat(jugada): estado vacio honesto (tuteo)`

---

## Phase E — Integración en el flujo

### Task E1: `useJugada.ts` — hook que orquesta derive/live/conducta

**Files:** Create `frontend/src/components/valles/jugada/useJugada.ts`; Test `useJugada.test.ts`

- [ ] **Step 1: Test (estado de carga + ramas)**

```typescript
// useJugada.test.ts — verifica que deriva al precio vivo (price_live de niveles)
import { renderHook, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
vi.mock('../../../api', () => ({
  getPlanDerive: vi.fn(async () => ({ entry: 0.42, sl_plan: 0.385, sl_piso: null, entry_zone: null, rungs: [], runner_frac: 0.05 })),
  getPlanLive: vi.fn(async () => ({ symbol: 'ADAUSDT', estado_vivo: null })),
  getPlanConducta: vi.fn(async () => ({ symbol: 'ADAUSDT', estado_vivo: null })),
}));
import * as api from '../../../api';
import { useJugada } from './useJugada';
describe('useJugada', () => {
  it('deriva al precio vivo recibido', async () => {
    const { result } = renderHook(() => useJugada('ADAUSDT', 0.4205));
    await waitFor(() => expect(result.current.derived.data).toBeTruthy());
    expect(api.getPlanDerive).toHaveBeenCalledWith('ADAUSDT', 0.4205);
  });
});
```

- [ ] **Step 2: Implement** — hook con tres `AsyncState` (derived, live, conducta), reusando el patrón de `useValleyBundle` (`AsyncState<T> = {data,loading,error}`). `derived` se pide con `getPlanDerive(symbol, livePrice)`; `live` con `getPlanLive`; `conducta` con `getPlanConducta`. `livePrice` viene del caller (de `bundle.niveles.data.price_live`).

- [ ] **Step 3: Run → PASS. Commit:** `feat(jugada): hook useJugada (derive/live/conducta)`

### Task E2: Enchufar la 4ta lente en `ValleysFlow.tsx`

**Files:** Modify `frontend/src/components/valles/ValleysFlow.tsx`

- [ ] **Step 1: Cambios exactos**

```tsx
// 1. STEPS (línea 13): añadir 'jugada' tras 'niveles'
const STEPS = ['pick', 'vida', 'niveles', 'jugada', 'fund', 'cierre'] as const;
// 2. LENS (líneas 15-17): añadir "La jugada"
const LENS = [
  { key: 'vida', label: 'Vida' }, { key: 'niveles', label: 'Niveles' },
  { key: 'jugada', label: 'La jugada' }, { key: 'fund', label: 'Quién' },
];
// 3. branch de screen (tras la línea 49, antes de 'fund'):
else if (cur === 'jugada') {
  const live = bundle.niveles.data?.price_live ?? null;
  screen = <JugadaLens symbol={sym} livePrice={live} />;   // wrapper que usa useJugada + las pantallas D1-D5
}
// 4. lensIdx (línea 53): remapear
const lensIdx = cur === 'cierre' ? 4 : ({ vida: 0, niveles: 1, jugada: 2, fund: 3 } as Record<string, number>)[cur];
// 5. nav: el botón "Siguiente" desde 'niveles' lleva a 'jugada'; desde 'jugada' a 'fund'.
//    Ajustar el label de 'fund' (la última lente antes de cierre sigue siendo "Cerrar el recorrido").
```

Crear el wrapper `JugadaLens.tsx` (en `jugada/`) que: llama `useJugada(symbol, livePrice)`, y según el estado elige la pantalla — si `live.estado_vivo === 'cerrado'` → `JugadaConducta`; si `'activo'|'incierto'` → `JugadaLive`; si `derived.data` con rungs → `JugadaDerivada` (+ acceso al gate); si `derived` vacío/sin paredes → `JugadaSinJugada`.

- [ ] **Step 2: Typecheck + smoke** Run: `cd frontend && npx tsc --noEmit` → sin errores.
- [ ] **Step 3: Commit:** `feat(valles): La Jugada como 4ta lente del recorrido`

### Task E3: Marca "tiene jugada" en `PickScreen.tsx`

**Files:** Modify `frontend/src/components/valles/PickScreen.tsx`

- [ ] Añadir un chip discreto "tiene jugada" (glyph de 3 barras) en cada candidata, de bajo contraste, **sin números y sin reordenar la lista** (sigue ordenada por liquidez). Por ahora la marca puede ser estática para todas las candidatas con paredes (o derivarse de un campo si el screener lo expone); 1:1 con `JuPickMark` (§7.8). Footer: "La marca no ordena la lista ni la puntúa…".
- [ ] Commit: `feat(jugada): marca discreta 'tiene jugada' en el Pick`

---

## Phase F — Verificación

### Task F1: Gate de frontend
- [ ] Run: `cd frontend && npx tsc --noEmit` → sin errores.
- [ ] Run: `cd frontend && npx vitest run` → todos los tests de la jugada en verde.
- [ ] Run: `cd frontend && npm run build` → build limpio.

### Task F2: Gate de backend
- [ ] Run: `python -m pytest tests/test_plan_api.py -v` → PASS.
- [ ] Run: `python -m pytest tests/ -m "not network" -n auto -q` → sin regresiones.

### Task F3: Verificación visual 1:1 (manual)
- [ ] `cd frontend && npm run dev`; abrir Valles → seleccionar una candidata → avanzar a "La jugada".
- [ ] Comparar contra los HTML del handoff (`La Jugada.html`, `La Jugada · gráfico.html`) abiertos en el navegador: banda de zona, escalera, stop/BE, runner, precio vivo, hueco honesto, gate, en curso (frescura), conducta. Anotar desviaciones y corregir.
- [ ] Verificar tuteo en todo el copy (cero voseo).

---

## Self-Review (cobertura de la spec)

- Gráfico montado como en la app (lightweight-charts) ✔ Task C3 (patrón ChartCanvas).
- Overlays 1:1 (zona banda, stop/BE, escalera, runner, precio vivo, hueco) ✔ Task C1+C3.
- Entrada SIEMPRE rango ✔ overlays.zone (banda) + copy; nunca línea de precio puntual.
- Ciclo completo "todos los juguetes" ✔ Derivada (D1) + Gate/Fijada (D2) + En curso (D3) + Conducta (D4) + Sin jugada (D5).
- 4ta lente tras Niveles ✔ Task E2.
- Marca en el Pick ✔ Task E3.
- Fetchers nuevos ✔ Task B2 (hoy no existían).
- Frescura #8 ✔ Task A2 (LiveSnapshot).
- Conducta sin PnL ✔ Task A3 + D4.
- Tuteo venezolano ✔ tabla de remapeo + Phase D.
- Contrato enriquecido para 1:1 (toques/piso) ✔ Task A1.

**Fuera de alcance (anotado, no silenciado):** la nota de lenguaje (`JuLexNote`) y la lámina de divs (`SalidaLadder`) del handoff NO se implementan — la primera es documentación interna, la segunda queda reemplazada por el gráfico de velas canónico. El pulido final de microcopy con `solace-wren` queda como follow-up.
```
