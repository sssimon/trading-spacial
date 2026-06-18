# IdeaChart — velas desde fuente única + estado de carga + etiquetas sin amontonar

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unificar la fuente de velas del IdeaChart (vienen de `/levels` en lugar de un fetch separado a `/ohlcv`), eliminar el silencio vacío del estado de carga, y evitar que las etiquetas de paredes se apilen.

**Architecture:** El handler `/levels/{symbol}` ya llama `_fetch_daily_bars` internamente — ese resultado se retorna ahora en `candles` dentro del payload. El `IdeaChart` deja de auto-fetchear; recibe velas vía la prop `levels` que el padre ya cargaba. Un segundo `useEffect` reacciona a `levels?.candles` para setear datos en la serie. Estados de carga/error cubren el área del gráfico con texto honesto. Las etiquetas de paredes se filtran por colisión de coordenadas Y.

**Tech Stack:** Python / FastAPI (backend), React / TypeScript / Lightweight Charts (frontend), Vitest (unit tests), pytest (backend tests), Playwright (E2E).

---

## File Map

| Archivo | Acción | Qué cambia |
|---|---|---|
| `api/levels.py` | Modify | Agrega `candles` al payload de la respuesta `ok` |
| `tests/test_levels_api.py` | Modify | Nuevo test `test_payload_includes_candles` |
| `frontend/src/types.ts` | Modify | Agrega `candles?` a `SrLevels` |
| `frontend/src/components/valles/idea/IdeaChart.tsx` | Modify | Elimina fetch propio; consume `levels.candles`; agrega estados de carga/error; colisión de etiquetas |
| `frontend/src/components/valles/idea/idea.module.css` | Modify | Agrega clase `.idea-chart-state` para el overlay de estado |
| `frontend/src/components/valles/idea/IdeaChart.test.tsx` | Modify | Elimina mock de `getOhlcv`; agrega caso con `candles`; agrega caso sin `candles` → empty state |

---

### Task 1: Backend — `candles` en el payload de `/levels`

**Files:**
- Modify: `api/levels.py` (línea 87-94, el bloque del payload `ok`)

- [ ] **Step 1: Leer el handler actual para ubicar la línea exacta**

El payload `ok` está en la línea ~89-94 de `api/levels.py`:
```python
payload = {"symbol": symbol, "estado": "ok",
           "generated_at": generated_at,
           "price_live": price, "zonas": zonas,
           "ubicacion": locate_price(price, zonas)}
```

- [ ] **Step 2: Modificar el payload para incluir `candles`**

Reemplaza ese bloque en `api/levels.py`:

```python
    candles_lc = [
        {"time": b["open_time"] // 1000, "open": b["open"],
         "high": b["high"], "low": b["low"], "close": b["close"]}
        for b in bars
    ]
    payload = {"symbol": symbol, "estado": "ok",
               "generated_at": generated_at,
               "price_live": price, "zonas": zonas,
               "ubicacion": locate_price(price, zonas),
               "candles": candles_lc}
```

No toques ningún otro bloque — el branch `no_disponible` no necesita `candles`.

- [ ] **Step 3: Verificar manualmente el nuevo campo**

Ejecuta (sin red real, usando el test existente como referencia):
```bash
python -m pytest tests/test_levels_api.py::test_payload_ok -q
```
Expected: PASS (el campo `candles` no rompe los asserts existentes — solo agrega un campo nuevo).

- [ ] **Step 4: Commit intermedio**

```bash
git add api/levels.py
git commit -m "feat(api): /levels retorna candles en shape lightweight-charts"
```

---

### Task 2: Backend test — assert del campo `candles`

**Files:**
- Modify: `tests/test_levels_api.py`

- [ ] **Step 1: Escribir el test fallido**

Agrega al final de `tests/test_levels_api.py`, ANTES de `test_router_registrado_en_la_app`:

```python
def test_payload_includes_candles():
    """El payload ok debe incluir 'candles' con la forma lightweight-charts
    (time en segundos = open_time // 1000, open/high/low/close float)."""
    bars = _bars()
    with patch("api.levels._fetch_daily_bars", return_value=bars), \
         patch("api.levels._fetch_live_price", return_value=100.0):
        r = _app().get("/levels/BTCUSDT")
    assert r.status_code == 200
    body = r.json()
    assert "candles" in body, "falta el campo 'candles' en el payload ok"
    assert len(body["candles"]) == len(bars)
    first = body["candles"][0]
    for key in ("time", "open", "high", "low", "close"):
        assert key in first, f"falta key '{key}' en candle[0]"
    # time debe ser segundos (open_time // 1000); _bars() usa open_time=0
    assert first["time"] == 0
    # no_disponible no debe tener candles (o si los tiene, son [])
    with patch("api.levels._fetch_daily_bars",
               side_effect=BinanceUnavailable("klines HTTP 503")):
        r2 = _app().get("/levels/BTCUSDT")
    body2 = r2.json()
    assert body2.get("candles", []) == []
```

- [ ] **Step 2: Ejecutar en rojo (antes de la implementación del Task 1)**

Si ejecutas Tasks en orden, ya hiciste el Task 1 primero. Confirma que el test ahora pasa:
```bash
python -m pytest tests/test_levels_api.py -q
```
Expected: todos los tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_levels_api.py
git commit -m "test(api): assert candles en payload ok de /levels"
```

---

### Task 3: Frontend types — `candles?` en `SrLevels`

**Files:**
- Modify: `frontend/src/types.ts` (línea 565-572, la interfaz `SrLevels`)

- [ ] **Step 1: Agregar el campo a la interfaz**

La interfaz `SrLevels` en `types.ts` está alrededor de la línea 565:
```typescript
export interface SrLevels {
  symbol:       string;
  estado:       'ok' | 'no_disponible';
  generated_at: string | null;
  price_live:   number | null;
  zonas:        SrZona[];
  ubicacion:    SrUbicacion;
}
```

Añade el campo `candles?` al final de la interfaz:
```typescript
export interface SrLevels {
  symbol:       string;
  estado:       'ok' | 'no_disponible';
  generated_at: string | null;
  price_live:   number | null;
  zonas:        SrZona[];
  ubicacion:    SrUbicacion;
  candles?: { time: number; open: number; high: number; low: number; close: number }[];
}
```

- [ ] **Step 2: Verificar que tsc no se queje**

```bash
cd frontend && npx tsc --noEmit
```
Expected: salida limpia (sin errores).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types.ts
git commit -m "feat(types): SrLevels.candles opcional — shape lightweight-charts"
```

---

### Task 4: CSS — clase `.idea-chart-state` para overlay de estado

**Files:**
- Modify: `frontend/src/components/valles/idea/idea.module.css`

- [ ] **Step 1: Agregar la clase al final del bloque de muros (después de `.idea-wall--sup`)**

Busca la sección `/* ── MUROS S/R ──` y agrega DESPUÉS del bloque `.idea-wall--sup` (alrededor de línea 147), ANTES de `/* ── IDEA VIEW`:

```css
/* ── ESTADO DEL GRÁFICO (cargando / sin datos) ─────────── */
.idea-chart-state {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--sans, sans-serif);
  font-size: 13.5px;
  color: var(--ink-3, #9A9080);
  pointer-events: none;
  text-align: center;
  padding: 16px;
}
```

- [ ] **Step 2: Verificar que tsc sigue limpio**

```bash
cd frontend && npx tsc --noEmit
```
Expected: salida limpia.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/valles/idea/idea.module.css
git commit -m "style(idea): clase idea-chart-state para overlay cargando/sin datos"
```

---

### Task 5: IdeaChart.tsx — remover auto-fetch, consumir `levels.candles`, estados, colisión

**Files:**
- Modify: `frontend/src/components/valles/idea/IdeaChart.tsx`

Esta es la tarea más grande. La hacemos en sub-pasos para que cada cambio sea atómico y verificable.

#### Sub-tarea 5a: Remover el import de `getOhlcv` y el fetch

- [ ] **Step 1: Eliminar el import de `getOhlcv`**

En `IdeaChart.tsx` línea 14:
```typescript
import { getOhlcv } from '../../../api';
```
Elimina esa línea completa. El archivo debe quedar con los imports:
```typescript
import React, { useEffect, useRef, useState } from 'react';
import { createChart, type IChartApi, type ISeriesApi } from 'lightweight-charts';
import type { ValleyEval, SrLevels, PlanDerived } from '../../../types';
import { type LiveState } from '../jugada/overlays';
import { buildLayers, LAYER_KEYS, type LayerVisibility, DEFAULT_LAYERS } from './chartLayers';
import { formatPrice } from '../../../utils';
import juStyles from '../jugada/jugada.module.css';
import styles from './idea.module.css';
```

- [ ] **Step 2: Eliminar el bloque `getOhlcv(...).then(...).catch(...)` del useEffect de montaje**

Dentro del useEffect de montaje (el que tiene la dependencia `[symbol, height]`), borra completamente este bloque (líneas ~107-125 del original):

```typescript
    getOhlcv(symbol, '1d', 180).then((res) => {
      if (chartRef.current !== chart) return;
      series.setData(
        res.candles.map((c) => ({
          time:  c.time as never,
          open:  c.open,
          high:  c.high,
          low:   c.low,
          close: c.close,
        })),
      );
      chart.timeScale().fitContent();
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          applySize(chart);
          force((n) => n + 1);
        }),
      );
    }).catch(() => { /* sin datos: gráfico vacío pero montado */ });
```

Deja el resto del useEffect intacto (ResizeObserver, subscribeVisibleLogicalRangeChange, cleanup).

- [ ] **Step 3: Verificar tsc después de la eliminación**

```bash
cd frontend && npx tsc --noEmit
```
Expected: limpio.

#### Sub-tarea 5b: Agregar el useEffect que consume `levels.candles`

- [ ] **Step 4: Agregar un segundo useEffect después del primero (de montaje)**

Justo después del primer `useEffect` (el que cierra con `}, [symbol, height]);`) y ANTES del bloque `// ── GEOMETRÍA ──`, inserta:

```typescript
  // ── DATOS DEL GRÁFICO (desde levels.candles) ──────────
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;
    const candles = levels?.candles;
    if (!candles?.length) return;
    series.setData(
      candles.map((c) => ({
        time:  c.time as never,
        open:  c.open,
        high:  c.high,
        low:   c.low,
        close: c.close,
      })),
    );
    chart.timeScale().fitContent();
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        if (!chartRef.current) return;
        const container = wrapRef.current;
        if (!container) return;
        const W = Math.round(container.clientWidth);
        const H = Math.round(container.clientHeight);
        if (W < 1 || H < 1) return;
        chartRef.current.resize(W - 1, H - 1, true);
        chartRef.current.resize(W, H, true);
        force((n) => n + 1);
      }),
    );
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [levels?.candles]);
```

- [ ] **Step 5: Verificar tsc**

```bash
cd frontend && npx tsc --noEmit
```
Expected: limpio.

#### Sub-tarea 5c: Agregar estado de carga/error en el render

- [ ] **Step 6: Agregar la detección del estado y el overlay**

En el bloque `return (...)` de `IdeaChart`, dentro del `<div className={juStyles['ju-chart']} ...>`, DESPUÉS del `<div ref={wrapRef} .../>` (línea ~167) y ANTES de la `<div className={styles['idea-legend']} ...>`, agrega el overlay de estado:

```tsx
      {/* ── ESTADO DEL GRÁFICO ── */}
      {levels == null && (
        <div className={styles['idea-chart-state']}>
          Cargando las velas…
        </div>
      )}
      {levels != null && (levels.estado === 'no_disponible' || !levels.candles?.length) && (
        <div className={styles['idea-chart-state']}>
          No se pudieron cargar las velas de esta moneda.
        </div>
      )}
```

- [ ] **Step 7: Verificar tsc**

```bash
cd frontend && npx tsc --noEmit
```
Expected: limpio.

#### Sub-tarea 5d: Colisión de etiquetas en paredes

- [ ] **Step 8: Agregar lógica de colisión al render de paredes**

El bloque actual de paredes (alrededor de la línea 213) es:
```tsx
        {layers.paredes && m.paredes.walls.map((w, i) => {
          const wy = Y(w.centro);
          const esRes = w.tipo === 'resistencia';
          return (
            <div key={i} ...>
              <span className={styles['idea-wall__rule']} />
              <span className={styles['idea-wall__tag']}>
                {esRes ? 'techo' : 'piso'} · ${formatPrice(w.centro)} · {w.toques} toques
              </span>
            </div>
          );
        })}
```

Reemplaza ese bloque completo con:

```tsx
        {layers.paredes && (() => {
          // Colisión: renderizamos TODAS las líneas pero suprimimos la etiqueta
          // cuando su Y cae a <18px de la etiqueta anterior ya dibujada.
          const sorted = [...m.paredes.walls].sort((a, b) => {
            const ya = Y(a.centro) ?? Infinity;
            const yb = Y(b.centro) ?? Infinity;
            return ya - yb;
          });
          let lastLabelY: number | null = null;
          return sorted.map((w, i) => {
            const wy = Y(w.centro);
            const esRes = w.tipo === 'resistencia';
            const showLabel =
              wy != null &&
              (lastLabelY == null || Math.abs(wy - lastLabelY) >= 18);
            if (showLabel && wy != null) lastLabelY = wy;
            return (
              <div
                key={i}
                className={[
                  styles['idea-wall'],
                  esRes ? styles['idea-wall--res'] : styles['idea-wall--sup'],
                ].join(' ')}
                style={{ top: wy ?? undefined }}
              >
                <span className={styles['idea-wall__rule']} />
                {showLabel && (
                  <span className={styles['idea-wall__tag']}>
                    {esRes ? 'techo' : 'piso'} · ${formatPrice(w.centro)} · {w.toques} toques
                  </span>
                )}
              </div>
            );
          });
        })()}
```

- [ ] **Step 9: Verificar tsc completo**

```bash
cd frontend && npx tsc --noEmit
```
Expected: limpio.

- [ ] **Step 10: Commit de IdeaChart**

```bash
git add frontend/src/components/valles/idea/IdeaChart.tsx
git commit -m "feat(idea): velas desde levels.candles + estado carga + des-amontonar paredes"
```

---

### Task 6: Tests unitarios de IdeaChart — actualizar y agregar casos

**Files:**
- Modify: `frontend/src/components/valles/idea/IdeaChart.test.tsx`

- [ ] **Step 1: Reemplazar el mock de `getOhlcv` por un mock del módulo `api` vacío**

El test actual tiene:
```typescript
vi.mock('../../../api', () => ({
  getOhlcv: async () => ({ candles: [], volumes: [] }),
}));
```

Dado que `IdeaChart` ya no importa `getOhlcv`, el mock puede eliminarse completamente. Si hay otros imports de `../../../api` en el archivo de test, deja solo los que se usen. En este caso el archivo solo mockeaba `getOhlcv` para ese componente — elimina el bloque `vi.mock('../../../api', ...)` entero.

- [ ] **Step 2: Agregar `candles` al fixture `levels` existente**

El objeto `levels` en el test (alrededor de la línea 56) debe extenderse con el campo `candles`:

```typescript
const levels: SrLevels = {
  symbol:       'ADAUSDT',
  estado:       'ok',
  generated_at: null,
  price_live:   0.42,
  zonas: [
    {
      tipo:                'resistencia',
      centro:              0.448,
      precio_bajo:         0.445,
      precio_alto:         0.451,
      toques:              3,
      confluencia_redondo: [],
    },
    {
      tipo:                'soporte',
      centro:              0.385,
      precio_bajo:         0.382,
      precio_alto:         0.388,
      toques:              4,
      confluencia_redondo: [],
    },
  ],
  ubicacion: { dentro_de: null, techo: null, piso: null },
  candles: [
    { time: 1700000000, open: 0.40, high: 0.42, low: 0.39, close: 0.41 },
    { time: 1700086400, open: 0.41, high: 0.43, low: 0.40, close: 0.42 },
    { time: 1700172800, open: 0.42, high: 0.45, low: 0.41, close: 0.44 },
  ],
};
```

- [ ] **Step 3: Obtener la referencia al mock de `setData` en el mock de `lightweight-charts`**

En el mock de `lightweight-charts`, la serie retorna `setData: vi.fn()`. Para poder assertar llamadas a `setData`, necesitamos una referencia capturable. Actualiza el mock para exponer la función:

```typescript
// Referencia al mock de setData para assertar llamadas
const mockSetData = vi.fn();

vi.mock('lightweight-charts', () => ({
  createChart: () => ({
    addCandlestickSeries: () => ({
      setData:              mockSetData,
      applyOptions:         vi.fn(),
      priceToCoordinate:    (p: number) => p * 1000,
    }),
    timeScale: () => ({
      fitContent:                           vi.fn(),
      subscribeVisibleLogicalRangeChange:   vi.fn(),
      unsubscribeVisibleLogicalRangeChange: vi.fn(),
    }),
    applyOptions: vi.fn(),
    resize:       vi.fn(),
    remove:       vi.fn(),
  }),
}));
```

- [ ] **Step 4: Agregar el test de `setData` con candles**

Dentro del `describe('IdeaChart', ...)`, agrega un nuevo `it` después de los existentes:

```typescript
  it('llama setData con las velas de levels.candles cuando están presentes', async () => {
    mockSetData.mockClear();
    render(
      <IdeaChart
        symbol="ADAUSDT"
        vida={vida}
        levels={levels}
        plan={plan}
        live={0.4205}
      />,
    );
    // El segundo useEffect se dispara tras el montaje; en jsdom es síncrono
    // (requestAnimationFrame es no-op en jsdom, no bloquea el assert)
    // Pero useEffect corre asíncronamente — necesitamos waitFor
    const { waitFor } = await import('@testing-library/react');
    await waitFor(() => {
      expect(mockSetData).toHaveBeenCalled();
    });
    const callArg = mockSetData.mock.calls[mockSetData.mock.calls.length - 1][0];
    expect(Array.isArray(callArg)).toBe(true);
    expect(callArg).toHaveLength(3);
    expect(callArg[0]).toMatchObject({ open: 0.40, high: 0.42, low: 0.39, close: 0.41 });
  });
```

- [ ] **Step 5: Agregar el test de empty state cuando `levels` no tiene candles**

```typescript
  it('muestra el estado "No se pudieron cargar" cuando levels no tiene candles', () => {
    const levelsVacias: SrLevels = {
      ...levels,
      candles: [],
    };
    render(
      <IdeaChart
        symbol="ADAUSDT"
        vida={vida}
        levels={levelsVacias}
        plan={null}
        live={0.4205}
      />,
    );
    expect(
      screen.getByText(/No se pudieron cargar las velas de esta moneda/i),
    ).toBeTruthy();
  });

  it('muestra "Cargando las velas" cuando levels es null', () => {
    render(
      <IdeaChart
        symbol="ADAUSDT"
        vida={vida}
        levels={null}
        plan={null}
        live={0.4205}
      />,
    );
    expect(
      screen.getByText(/Cargando las velas/i),
    ).toBeTruthy();
  });
```

- [ ] **Step 6: Ejecutar el suite de Vitest**

```bash
cd frontend && npx vitest run src/components/valles/idea/IdeaChart.test.tsx
```
Expected: todos los tests PASS (incluyendo los 3 ya existentes + los 3 nuevos).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/valles/idea/IdeaChart.test.tsx
git commit -m "test(idea): elimina mock getOhlcv; agrega casos candles + empty state"
```

---

### Task 7: Verificación final — tsc + vitest + pytest

- [ ] **Step 1: TypeScript limpio**

```bash
cd frontend && npx tsc --noEmit
```
Expected: sin errores.

- [ ] **Step 2: Vitest completo**

```bash
cd frontend && npx vitest run
```
Expected: todos los tests PASS.

- [ ] **Step 3: pytest del módulo levels**

```bash
python -m pytest tests/test_levels_api.py -q
```
Expected: todos los tests PASS (incluyendo el nuevo `test_payload_includes_candles`).

- [ ] **Step 4: Gate rápido backend (sin network)**

```bash
python -m pytest tests/ -m "not network" -n auto -q
```
Expected: PASS (el campo `candles` es puramente aditivo — no rompe nada).

---

### Task 8: E2E Playwright — verificar que el chart muestra velas reales

**Objetivo:** Confirmar que tras el fix el gráfico pasa de un canvas vacío a uno con velas reales.

#### Pre-condición: el E2E requiere un stack en vivo

El spec de Playwright (`frontend/e2e/valles-idea.spec.ts`) necesita:
1. Backend en `:8001` con bypass de auth activo
2. Frontend en `:5174` apuntando al backend en `:8001`

#### Setup del backend para el E2E

- [ ] **Step 1: Crear script de arranque del backend para E2E**

Crea (solo si no existe) `scripts/e2e_backend.py` en la raíz del repo:

```python
"""
Arranca el backend en :8001 con el bypass de auth activo para E2E.
Uso: python scripts/e2e_backend.py
"""
import os
import sys

# El middleware de bypass requiere que 'pytest' esté en sys.modules
# (triple guarda interna para que no sea activable en prod por accidente).
import types
sys.modules.setdefault('pytest', types.ModuleType('pytest'))

os.environ['AUTH_TEST_BYPASS_ALLOWED'] = '1'
os.environ['AUTH_TEST_BYPASS_ROLE'] = 'admin'

import uvicorn
uvicorn.run('btc_api:app', host='127.0.0.1', port=8001, log_level='warning')
```

- [ ] **Step 2: Arrancar el backend E2E en background**

En una terminal separada (o proceso de background):
```bash
python scripts/e2e_backend.py
```
Espera hasta que veas el log `Application startup complete` o similar.

- [ ] **Step 3: Arrancar el frontend en background apuntando al backend E2E**

En otra terminal:
```bash
cd frontend && VITE_API_PROXY_TARGET=http://localhost:8001 npm run dev -- --port 5174
```
Espera hasta que veas `Local: http://localhost:5174/`.

- [ ] **Step 4: Actualizar el E2E spec para verificar el fix (no `/ohlcv`, sino `candles` en `/levels`)**

El spec actual (`valles-idea.spec.ts`) rastreaba `/ohlcv`. Dado que el chart ya no hace ese request, el spec necesita actualizarse: rastrear `/levels` en su lugar y verificar el campo `candles`. Actualiza las variables de seguimiento y los asserts finales.

Reemplaza las variables `ohlcvStatus`, `ohlcvUrl`, `ohlcvResponseBody`, `candleCount` por:

```typescript
  let levelsStatus  = -1;
  let levelsUrl     = '';
  let levelsCandleCount = -1;
  let levelsHasCandles  = false;
```

Actualiza el handler `page.on('response', ...)` — elimina el bloque `if (url.includes('/ohlcv'))` y actualiza el bloque de `/levels`:

```typescript
    if (url.includes('/levels')) {
      levelsStatus = status;
      levelsUrl = url;
      try {
        const body = await res.json();
        const candles = body?.candles;
        levelsHasCandles = Array.isArray(candles) && candles.length > 0;
        levelsCandleCount = Array.isArray(candles) ? candles.length : -1;
      } catch {
        levelsCandleCount = -2;
      }
    }
```

Actualiza el EVIDENCE BLOCK para reportar `/levels` en lugar de `/ohlcv`:
```typescript
  console.log(`\n--- /levels ---`);
  console.log(`levels URL:         ${levelsUrl || '(no request)'}`);
  console.log(`levels status:      ${levelsStatus}`);
  console.log(`levels hasCandles:  ${levelsHasCandles}`);
  console.log(`levels candleCount: ${levelsCandleCount}`);
```

Reemplaza el assert final de `ohlcvUrl` por:
```typescript
  expect(
    levelsUrl,
    'IdeaChart debería haber disparado un /levels request',
  ).not.toBe('');

  expect(
    levelsHasCandles,
    `/levels debe retornar candles cuando TRXUSDT está disponible en Binance`,
  ).toBe(true);
```

Y agrega la medición de pixels no-transparentes del canvas:

```typescript
  // Pixel count del canvas — el chart con velas reales tiene >>129 pixels activos
  const pixelCount = await page.evaluate(() => {
    const canvas = document.querySelector('canvas') as HTMLCanvasElement | null;
    if (!canvas) return 0;
    const ctx = canvas.getContext('2d');
    if (!ctx) return 0;
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    let nonTransparent = 0;
    for (let i = 3; i < data.length; i += 4) {
      if (data[i] > 10) nonTransparent++;
    }
    return nonTransparent;
  });
  console.log(`Canvas non-transparent pixels: ${pixelCount}`);
  expect(pixelCount, 'El canvas debe tener velas reales (>500 pixels no-transparentes)').toBeGreaterThan(500);
```

Además, toma un screenshot nombrado `idea-trx-fixed.png`:
```typescript
  await page.screenshot({ path: path.join(ARTIFACTS, 'idea-trx-fixed.png'), fullPage: false });
```

- [ ] **Step 5: Ejecutar el E2E**

```bash
cd frontend && npx playwright test e2e/valles-idea.spec.ts
```
Expected: PASS. El EVIDENCE BLOCK debe mostrar:
- `levels hasCandles: true`
- `levels candleCount: > 0`
- `Canvas non-transparent pixels: > 500`
- Screenshot en `frontend/e2e/artifacts/idea-trx-fixed.png`

- [ ] **Step 6: Bajar los servidores**

Cierra el proceso del backend y el del frontend que iniciaste en los Steps 2-3.

- [ ] **Step 7: Commit final con todo el trabajo de E2E**

```bash
git add frontend/e2e/valles-idea.spec.ts scripts/e2e_backend.py
git commit -m "test(e2e): valles-idea spec actualizado para fix de candles desde /levels"
```

---

### Task 9: Commit de integración y reporte

- [ ] **Step 1: Verificar el estado del repo**

```bash
git log --oneline -8
git status
```
Expected: working tree limpio; los commits del plan ya están en el historial.

- [ ] **Step 2: Commit de integración si hay archivos sueltos**

Si quedaron archivos sin commitear (e.g., por cambios de última hora):
```bash
git add -A api/levels.py tests/test_levels_api.py frontend/src
git commit -m "fix(idea): velas desde /levels (fuente diaria unica) + estado de carga + des-amontonar paredes (chart vacio)"
```

- [ ] **Step 3: Reportar**

El reporte final debe incluir:
- Estado de cada check: tsc, vitest, pytest, E2E
- SHA base (HEAD antes del primer commit del plan) y nuevo HEAD
- Path del screenshot `frontend/e2e/artifacts/idea-trx-fixed.png`
- Pixel count del canvas
- Archivos modificados

---

## Self-Review

### Spec coverage

| Requisito del spec | Task que lo cubre |
|---|---|
| Backend `/levels` retorna `candles` en lightweight-charts shape | Task 1 |
| Test backend: assert campo `candles` con len N y shape `{time,open,high,low,close}` | Task 2 |
| `SrLevels.candles?` en tipos TypeScript | Task 3 |
| Eliminar `getOhlcv` import y fetch de `IdeaChart` | Task 5a |
| Segundo `useEffect` keyed en `levels.candles` que llama `series.setData` + `fitContent` + doble rAF | Task 5b |
| Overlay "Cargando las velas…" cuando `levels == null` | Task 5c |
| Overlay "No se pudieron cargar las velas de esta moneda." cuando `!levels.candles?.length` | Task 5c |
| Colisión de etiquetas: todas las líneas, skip etiqueta si Y a <18px de la anterior | Task 5d |
| CSS para el overlay de estado | Task 4 |
| Eliminar mock `getOhlcv` del test | Task 6 Step 1 |
| Agregar `candles` al fixture `levels` del test | Task 6 Step 2 |
| Test: `setData` es llamado con las velas | Task 6 Steps 3-4 |
| Test: empty state cuando `candles: []` | Task 6 Step 5 |
| Test: "Cargando" cuando `levels == null` | Task 6 Step 5 |
| Verificación tsc + vitest + pytest | Task 7 |
| E2E: `levels` retorna `candles`, canvas tiene >500 px no-transparentes, screenshot `idea-trx-fixed.png` | Task 8 |

### Placeholder scan

Ningún paso dice "implementa aquí" ni "similar a task N". Todos los steps tienen código completo. No hay TODOs.

### Type consistency

- `SrLevels.candles` definida en Task 3; usada en Task 5b como `levels?.candles` y en Task 5c como `levels.candles?.length`. Consistente.
- `mockSetData` declarada en Task 6 Step 3 como `vi.fn()`; usada en Task 6 Steps 4-5 como `mockSetData.mock.calls`. Consistente.
- El shape `{ time, open, high, low, close }` coincide entre backend (Task 1), types (Task 3), y test fixture (Task 6 Step 2). Consistente.
- La variable `applySize` en Task 5b se recrea inline (doble rAF) porque es una closure del primer useEffect que no está en scope del segundo. Correcto.
