# Valles — Vista única "idea de moneda" — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar el wizard de 4 pasos de Valles por una sola vista por moneda — un gráfico de velas unificado (3 capas conmutables vía leyenda clicable) + narrativa descriptiva + Quién + sección de Noticias (empty-state), estilo "idea de moneda" honesta.

**Architecture:** Un nuevo `IdeaChart` (reusa el patrón de montaje de `LaJugadaChart` + `overlays.ts`) dibuja sobre las velas tres grupos de overlays — Vida (banda de consolidación), Paredes (todas las zonas S/R), Jugada (entry/stop/escalera/runner) — cada grupo prendido/apagado por una leyenda clicable. Un nuevo `IdeaView` orquesta `useValleyBundle` + `useJugada`, compone el gráfico + la narrativa en bloques + Quién (dossier) + Noticias (stub). `ValleysFlow` se reestructura: Pick → IdeaView (sin wizard).

**Tech Stack:** React 18 + TypeScript + Vite + vitest + `lightweight-charts ^4.2.0`. CSS modules cálidos. Sin backend nuevo (todos los endpoints existen).

**Spec:** `docs/superpowers/specs/es/2026-06-17-kickoff-valles-vista-unica-design.md`. **Doctrina:** descriptivo, no firma; costura visible obligatoria; tuteo venezolano; lector mayor (≥18px, ≥48px, AA).

---

## Contexto de código existente (reusar, no rehacer)

- `frontend/src/components/valles/jugada/overlays.ts` → `buildOverlays({plan, live, state})` + `OverlayModel`, `LiveState`. Geometría pura de la jugada.
- `frontend/src/components/valles/jugada/LaJugadaChart.tsx` → patrón de montaje lightweight-charts (createChart warm + getOhlcv + ResizeObserver + capa `.ju-chart__ann` posicionada con `series.priceToCoordinate`). **Es el molde de `IdeaChart`.**
- `frontend/src/components/valles/jugada/jugada.module.css` → clases `ju-chart*`, `ju-ann*`, etc.
- `frontend/src/components/valles/useValleyBundle.ts` → `useValleyBundle(sym)` → `{ vida: AsyncState<ValleyEval>, niveles: AsyncState<SrLevels>, dossier: AsyncState<Dossier>, refreshDossier }`. `AsyncState<T> = {data:T|null; loading:boolean; error:string|null}`.
- `frontend/src/components/valles/jugada/useJugada.ts` → `useJugada(symbol, livePrice)` → `{ derived, live, conducta }`.
- Tipos (`frontend/src/types.ts`): `ValleyEval` (`{symbol, estado, candidata, vivo, price?, pct_rango, semanas_consolidando, vol_percentil, volumen_usd_dia, distancia_ath_pct, razones_vida, razones_muerte, generated_at}`), `SrLevels` (`{estado, price_live, zonas:[{tipo:'soporte'|'resistencia', precio_bajo, precio_alto, centro, toques}], ubicacion:{dentro_de?, techo?, piso?}, generated_at}`), `PlanDerived`, `PlanLive`.
- Pantallas actuales a subsumir: `VidaScreen`, `NivelesScreen`, `FundScreen`, `ClosingScreen`, `jugada/JugadaLens` + las 5 pantallas jugada. `PickScreen` se conserva. `ValleysFlow` se reestructura.
- `frontend/src/utils.ts` → `formatPrice`. `atoms.tsx` → `Eyebrow`, `humanName`.

## File Structure

**Crear:**
- `frontend/src/components/valles/idea/chartLayers.ts` — geometría pura de las 3 capas (vida band, walls, jugada) + tipos de leyenda. Testeable sin DOM.
- `frontend/src/components/valles/idea/IdeaChart.tsx` — el gráfico unificado + leyenda clicable.
- `frontend/src/components/valles/idea/IdeaView.tsx` — orquestador de la vista única.
- `frontend/src/components/valles/idea/Narrativa.tsx` — los bloques descriptivos (vida/paredes/jugada).
- `frontend/src/components/valles/idea/NoticiasSection.tsx` — sección Noticias en empty-state (track 2).
- `frontend/src/components/valles/idea/idea.module.css` — estilos de la vista (reusa tokens de `.vwRoot`; importa/compone con `jugada.module.css` para el gráfico).
- Tests: `chartLayers.test.ts`, `IdeaChart.test.tsx`, `IdeaView.test.tsx`.

**Modificar:**
- `frontend/src/components/valles/ValleysFlow.tsx` — Pick → IdeaView; quitar wizard.

**Borrar (tras la reestructuración, en su tarea):** `VidaScreen.tsx`, `NivelesScreen.tsx`, `FundScreen.tsx`, `ClosingScreen.tsx`, `jugada/JugadaLens.tsx` + las 5 pantallas jugada y su test, si quedan sin consumidor (verificar con grep antes de borrar). `LaJugadaChart.tsx`/`overlays.ts` se reusan desde `idea/` o se mueven; no borrar `overlays.ts` (lo usa `chartLayers`).

---

## Phase 1 — El gráfico unificado

### Task 1: Geometría pura de las 3 capas (`chartLayers.ts`)

**Files:** Create `frontend/src/components/valles/idea/chartLayers.ts`; Test `frontend/src/components/valles/idea/chartLayers.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// chartLayers.test.ts
import { describe, it, expect } from 'vitest';
import { buildLayers, LAYER_KEYS } from './chartLayers';
import type { ValleyEval, SrLevels, PlanDerived } from '../../../types';

const vida = { pct_rango: 0.18, semanas_consolidando: 12, volumen_usd_dia: 4_000_000, vivo: true } as ValleyEval;
const levels = { estado: 'ok', price_live: 0.42, ubicacion: {}, zonas: [
  { tipo: 'soporte', precio_bajo: 0.388, precio_alto: 0.398, centro: 0.392, toques: 5 },
  { tipo: 'resistencia', precio_bajo: 0.445, precio_alto: 0.451, centro: 0.448, toques: 2 },
] } as unknown as SrLevels;
const plan = { entry: 0.419, sl_plan: 0.385, sl_piso: null, entry_zone: { centro:0.419, precio_bajo:0.412, precio_alto:0.426, toques:5 }, rungs: [{ tp_price:0.448, size_frac:0.5, zona:{centro:0.448,precio_bajo:0.445,precio_alto:0.451,toques:2} }], runner_frac: 0.05 } as PlanDerived;

describe('buildLayers', () => {
  it('expone las 3 capas con claves estables', () => {
    expect(LAYER_KEYS).toEqual(['vida', 'paredes', 'jugada']);
  });
  it('paredes incluye TODAS las zonas de levels (no solo las de la jugada)', () => {
    const m = buildLayers({ vida, levels, plan, live: 0.42, state: null });
    expect(m.paredes.walls).toHaveLength(2);
    expect(m.paredes.walls.find(w => w.tipo === 'resistencia')!.toques).toBe(2);
  });
  it('vida expone la banda de consolidación como rango de precio', () => {
    const m = buildLayers({ vida, levels, plan, live: 0.42, state: null });
    expect(m.vida.band).not.toBeNull();
    expect(m.vida.band!.low).toBeLessThan(m.vida.band!.high);
    expect(m.vida.semanas).toBe(12);
  });
  it('jugada reusa buildOverlays (zona banda + escalera)', () => {
    const m = buildLayers({ vida, levels, plan, live: 0.42, state: null });
    expect(m.jugada.zone).not.toBeNull();
    expect(m.jugada.rungs).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run → FAIL.** `cd frontend && npx vitest run src/components/valles/idea/chartLayers.test.ts`

- [ ] **Step 3: Implement**

```typescript
// chartLayers.ts
import type { ValleyEval, SrLevels, PlanDerived } from '../../../types';
import { buildOverlays, type LiveState, type OverlayModel } from '../jugada/overlays';

export const LAYER_KEYS = ['vida', 'paredes', 'jugada'] as const;
export type LayerKey = typeof LAYER_KEYS[number];
export type LayerVisibility = Record<LayerKey, boolean>;
export const DEFAULT_LAYERS: LayerVisibility = { vida: true, paredes: true, jugada: true };

export interface Wall { tipo: 'soporte' | 'resistencia'; centro: number; low: number; high: number; toques: number; }
export interface LayersModel {
  vida: { band: { low: number; high: number } | null; semanas: number; vivoStamp: string };
  paredes: { walls: Wall[]; price: number | null };
  jugada: OverlayModel;
}

export function buildLayers(args: {
  vida: ValleyEval; levels: SrLevels; plan: PlanDerived; live: number; state: LiveState | null;
}): LayersModel {
  const { vida, levels, plan, live, state } = args;
  // Banda de consolidación (Vida): centrada en el precio vivo, ancho = pct_rango.
  const band = vida?.pct_rango != null && live
    ? { low: live * (1 - vida.pct_rango / 2), high: live * (1 + vida.pct_rango / 2) }
    : null;
  const walls: Wall[] = (levels?.zonas ?? []).map((z) => ({
    tipo: z.tipo, centro: z.centro, low: z.precio_bajo, high: z.precio_alto, toques: z.toques,
  }));
  return {
    vida: { band, semanas: vida?.semanas_consolidando ?? 0,
            vivoStamp: vida?.vivo ? `viva · vol $${Math.round((vida.volumen_usd_dia ?? 0) / 1000)}k/día` : 'sin actividad' },
    paredes: { walls, price: levels?.price_live ?? null },
    jugada: buildOverlays({ plan, live, state }),
  };
}
```

- [ ] **Step 4: Run → PASS (4 tests).** **Step 5: Commit**

```bash
git add frontend/src/components/valles/idea/chartLayers.ts frontend/src/components/valles/idea/chartLayers.test.ts
git commit -m "feat(idea): geometria pura de las 3 capas (vida/paredes/jugada)"
```

### Task 2: `IdeaChart.tsx` — gráfico unificado + leyenda clicable

**Files:** Create `frontend/src/components/valles/idea/IdeaChart.tsx`, `idea.module.css`; Test `IdeaChart.test.tsx`

Reusa el armazón de `LaJugadaChart.tsx` (léelo): `createChart` warm + `getOhlcv('1d',180)` + `ResizeObserver` + cleanup + capa de anotaciones posicionada con `series.priceToCoordinate`. Sobre eso:
- Estado de capas: `const [layers, setLayers] = useState<LayerVisibility>(DEFAULT_LAYERS)`.
- Dibuja, condicionado a `layers.vida/paredes/jugada`:
  - **Vida**: la banda de consolidación (`m.vida.band`) como banda salvia muy tenue + un sello `m.vida.vivoStamp`.
  - **Paredes**: cada `m.paredes.walls` como línea horizontal etiquetada (`techo/piso · $centro · N toques`), arcilla; y el precio (`m.paredes.price`) como tag.
  - **Jugada**: los overlays existentes (zona, stop/BE, escalera, runner, precio vivo, hueco) — reusa el render de `LaJugadaChart` (zona banda, `ju-ann-*`).
- **Leyenda clicable** (`idea-legend`, dentro del gráfico): una entrada por capa (`Vida (el valle)`, `Paredes`, `La jugada`), cada una un `<button>` que hace `setLayers(l => ({...l, [k]: !l[k]}))`. Activa = color cálido de la capa; inactiva = atenuada (`aria-pressed`).
- Props: `{ symbol: string; vida: ValleyEval; levels: SrLevels; plan: PlanDerived | null; live: number; state?: LiveState | null; height?: number }`. Si `plan` es null, la capa jugada queda vacía (leyenda jugada deshabilitada).

- [ ] **Step 1: Test (mock lightweight-charts + api, como LaJugadaChart.test.tsx)** — render con vida+levels+plan mock; assert: la leyenda muestra las 3 entradas; al hacer click en "Paredes" su botón queda `aria-pressed="false"` y las líneas de pared desaparecen del DOM de anotaciones. Mock `getOhlcv`→{candles:[]}, `priceToCoordinate: p=>p*1000`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** IdeaChart.tsx (reusando el patrón de LaJugadaChart, con los 3 grupos de overlays gated por `layers` y la leyenda). Añade a `idea.module.css` las clases `idea-legend`, `idea-legend__item`, `idea-wall`, `idea-vida-band`, `idea-vida-stamp` (tonos: paredes arcilla, vida salvia tenue, jugada como ya está). Reusa clases `ju-ann-*` de `jugada.module.css` para la capa jugada (impórtalo).
- [ ] **Step 4: Run → PASS.** **Step 5: Commit** `feat(idea): IdeaChart — 3 capas + leyenda clicable`

---

## Phase 2 — La vista

### Task 3: `Narrativa.tsx` — bloques descriptivos

**Files:** Create `frontend/src/components/valles/idea/Narrativa.tsx`

Bloques anclados, copy tuteo, reusando la redacción de las pantallas actuales (lee `VidaScreen.tsx`, `NivelesScreen.tsx`, `jugada/JugadaDerivada.tsx` para el copy):
- **¿Está viva?** — de `ValleyEval`: vivo/valle, semanas en rango, volumen. ("Lleva {semanas} semanas moviéndose en una franja estrecha…").
- **¿Dónde está entre sus paredes?** — de `SrLevels.ubicacion`: techo a X%, piso a Y%, "ya rebotó N veces".
- **Si decides entrar, la jugada** — de `PlanDerived`: zona de entrada (rango), stop, escalera, runner; cierra con la costura "esto sale de tus niveles · la decisión es tuya".
Props: `{ vida: ValleyEval | null; levels: SrLevels | null; plan: PlanDerived | null }`. Cada bloque maneja su data faltante con un empty honesto. Toda la info, en prosa digerible.

- [ ] Step 1: render test — con mock vida/levels/plan, assert que aparecen los 3 encabezados y la costura "la decisión es tuya"; sin voseo.
- [ ] Step 2: FAIL → Step 3: implement → Step 4: PASS → Step 5: Commit `feat(idea): narrativa descriptiva en bloques (tuteo)`

### Task 4: `NoticiasSection.tsx` — empty-state (track 2)

**Files:** Create `frontend/src/components/valles/idea/NoticiasSection.tsx`

Sección "Lo último que se dijo" con empty-state honesto: encabezado + texto "Aún no traemos las noticias de esta moneda." (placeholder del track 2). Estructura lista para 5 ítems `{titular, fuente, url, fecha}` cuando exista el backend, pero v1 renderiza solo el empty-state. Tuteo. NO inventar noticias ni sentimiento.

- [ ] Step 1: render test — assert empty-state copy, sin ítems falsos. Step 2-5: implement + commit `feat(idea): seccion Noticias (empty-state, track 2)`

### Task 5: `IdeaView.tsx` — orquestador

**Files:** Create `frontend/src/components/valles/idea/IdeaView.tsx`; Test `IdeaView.test.tsx`

```
Props: { symbol: string }
```
- `const bundle = useValleyBundle(symbol);`
- `const livePrice = bundle.niveles.data?.price_live ?? bundle.vida.data?.price ?? null;`
- `const { derived, live } = useJugada(symbol, livePrice);`
- Layout (columna leíble, scroll, navegación anclada con un índice pegajoso `Vida · Paredes · Jugada · Quién · Noticias`):
  1. `<Eyebrow symbol={symbol} />` + título de la moneda.
  2. `<IdeaChart symbol vida={bundle.vida.data} levels={bundle.niveles.data} plan={derived.data} live={livePrice ?? 0} state={liveStateFrom(live.data)} />` (solo cuando vida+levels cargaron; loading honesto si no).
  3. `<Narrativa vida levels plan={derived.data} />`.
  4. **Jugada interactiva (lifecycle):** si `live.data?.estado_vivo` activo/incierto → un bloque compacto "en curso" (reusa el copy de `JugadaLive`); si derivada con rungs → un botón "Fijar esta jugada" que llama `confirmPlan` (reusa de `api.ts`); si cerrada → bloque conducta compacto. Mantener el ciclo accesible SIN re-montar otro gráfico (el gráfico es el de arriba). Reusa `useJugada` + `confirmPlan`.
  5. `<section Quién>` — reusa el contenido de `FundScreen` (dossier de `bundle.dossier`, `onRefresh={bundle.refreshDossier}`).
  6. `<NoticiasSection symbol={symbol} />`.
  7. Pie: botón "Mirar otra moneda" (lo que hacía el Cierre).
- `liveStateFrom(planLive)` helper: mapea `PlanLive.realidad` → `LiveState | null` (igual que en JugadaLens).

- [ ] Step 1: Test — render con hooks mockeados (vi.mock useValleyBundle/useJugada/api); assert: aparece el chart (mock), los bloques de narrativa, la sección Quién y la de Noticias, y el índice anclado. Step 2: FAIL. Step 3: implement (idea.module.css: layout de columna, índice pegajoso, secciones). Step 4: PASS + `tsc`. Step 5: Commit `feat(idea): IdeaView — vista unica (chart + narrativa + quien + noticias)`

---

## Phase 3 — Reestructurar el flujo

### Task 6: `ValleysFlow.tsx` → Pick → IdeaView

**Files:** Modify `frontend/src/components/valles/ValleysFlow.tsx`; luego borrar pantallas huérfanas.

- [ ] **Step 1:** Reescribir `ValleysFlow` para 2 estados: `pick` (sin `sym`) y `idea` (con `sym`). Quitar `STEPS`/`LENS`/stepper/nav del wizard y el keydown de flechas. Render: si no hay `sym` → `<PickScreen snapshot onPick={(s)=>setSym(s)} />`; si hay `sym` → `<IdeaView symbol={sym} />` + el FAB/Copilot existente. El botón "Mirar otra moneda" de IdeaView hace `setSym('')`. Mantener `localStorage vw_sym`.
- [ ] **Step 2:** `grep -rn "VidaScreen\|NivelesScreen\|FundScreen\|ClosingScreen\|JugadaLens\|JugadaDerivada\|JugadaGate\|JugadaLive\|JugadaConducta\|JugadaSinJugada\|LaJugadaChart" frontend/src` — confirmar que solo `idea/` (y los borrados) las referencian. Borrar las pantallas sin consumidor: `VidaScreen/NivelesScreen/FundScreen/ClosingScreen` y `jugada/JugadaLens` + las 5 pantallas jugada + sus tests, SOLO si no quedan referenciadas (extraer a `idea/` el copy que se reusó antes de borrar). NO borrar `jugada/overlays.ts`, `jugada/LaJugadaChart.tsx` (si `IdeaChart` lo reusa) ni `jugada/jugada.module.css`.
- [ ] **Step 3:** `cd frontend && npx tsc --noEmit` → limpio (arreglar imports rotos por los borrados). `npx vitest run` → verde (actualizar/eliminar tests de las pantallas borradas; el test del stepper de ValleysFlow ya no aplica — reescribirlo o quitarlo).
- [ ] **Step 4: Commit** `refactor(valles): Pick -> IdeaView, retira el wizard de 4 pasos`

---

## Phase 4 — Verificación

### Task 7: Gate completo
- [ ] `cd frontend && npx tsc --noEmit` → 0 errores.
- [ ] `cd frontend && npx vitest run` → verde.
- [ ] `cd frontend && npm run build` → build limpio.
- [ ] Handoff a Samuel para cotejo visual (`npm run dev` → Valles → elegir moneda → la idea): verificar la leyenda clicable (prender/apagar Vida/Paredes/Jugada), la narrativa con toda la info, Quién, Noticias empty-state, y que NO se lea como "compra esto". Tuteo en todo.

---

## Self-Review (cobertura del spec)

- Vista única reemplaza wizard ✔ T6. Gráfico unificado 3 capas ✔ T1/T2. Leyenda clicable (decisión A/C) ✔ T2. Narrativa con toda la info digerible ✔ T3. Quién ✔ T5. Noticias empty-state (track 2) ✔ T4. Pick entrada / Cierre pie (B) ✔ T5/T6. Sin backend nuevo ✔. Doctrina/costura ✔ T3. Tuteo/lector mayor ✔ transversal.
- **Riesgo anotado:** el lifecycle de la jugada (gate/live/conducta) se compacta dentro de IdeaView (T5) reusando `useJugada`/`confirmPlan` en vez de las pantallas full-screen; si en el cotejo visual se siente que pierde la guía, se reabre como sub-decisión (no bloquea v1).
- **Cotejo visual (F) es de Samuel** — no se puede afirmar 1:1 sin su ojo.
```
