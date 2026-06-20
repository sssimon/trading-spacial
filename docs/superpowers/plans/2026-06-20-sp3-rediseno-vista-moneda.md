# SP3 — Rediseño de la vista "idea de moneda" (implementación 1:1 del handoff) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar 1:1 el rediseño del equipo de diseño para la vista per-coin de Valles — el régimen como marco persistente que enmarca la idea-de-moneda, la banda de rango-30d + marcador en el gráfico, y el pulido de jerarquía/microcopy — sin tocar backend, doctrina ni contrato.

**Architecture:** Port del mockup React (en `docs/superpowers/handoffs/sp3/`) al código real (TypeScript + CSS modules + tipos/hooks reales). El mockup es la **fuente de verdad visual y de comportamiento**. El `IdeaChart` real ya usa lightweight-charts con capa de anotaciones HTML sincronizada — la banda/marcador se añaden como overlays más, no se reescribe el gráfico. El marco de régimen (`RegimeFrame`/`RegimeStrip`) es la versión per-coin de la cabecera; la lista (PickScreen) conserva el `AltSeasonHeader` actual.

**Tech Stack:** React + TypeScript, CSS modules, lightweight-charts, vitest.

**Handoff (fuente de verdad, ya en el repo):** `docs/superpowers/handoffs/sp3/` — `README.md`, `sp3-ideaview.jsx` (RegimeFrame/RegimeStrip + IdeaView + narrativa + PlayNow + Dossier), `sp3-chart.jsx` (banda/marcador Pieza 2), `sp3-warm.css` (tokens + estilos, fuente visual), `sp3-data.jsx` (formas de datos 1:1 con el contrato).

**Branch:** `feat/sp3-rediseno-vista-moneda` (crear desde `origin/main`).

## Global Constraints

- **Cero backend nuevo.** Todos los datos existen (régimen `/alt-season`, vida `/valley-eval`, paredes `/levels` con `candles`, jugada `/plan`, dossier `/dossier`). `range30` (banda) se computa en el cliente del min/max de las últimas 30 `candles`.
- **Decisión #3 (a):** en la rama "viva pero NO candidata", el copy de Vida NO muestra `pos_in_30d_range` con número (el backend real no lo emite ahí). Copy: *"Está viva, pero hoy cotiza en la parte alta de su rango de 30d, así que no entra en este filtro."* (sin "posición N%").
- **Microcopy `◆ prop` aceptado:** implementar las etiquetas en lenguaje natural del mockup (p.ej. componentes del régimen "amplitud (alts sobre su media 50d)", "alts vs BTC · 30 días", "dominancia BTC"). El chip `◆ prop` del mockup NO se renderiza (era marca de revisión).
- **Doctrina (no-negociable):** texto `/*VERBATIM*/` del mockup intocable (costura, AC7 con `9.92% vs 12.54%`, frase del régimen). El régimen ENMARCA pero NO modula color/orden/énfasis/texto de la moneda. Cero `valle`/`va a subir`/`señal`/`fuertes`/`débil`/`tiene jugada`. `git grep` debe seguir dando cero de esas frases en `frontend/src`.
- **Accesibilidad (lector mayor):** cuerpo ≥18px, secundarios ≥16px, targets ≥48px, contraste AA (hex del mockup ya AA), no-solo-color (todo estado lleva microcopy), foco visible, respeta `prefers-reduced-motion`.
- **Tuteo venezolano** en TODO el copy (el handoff renderiza tuteo; las NOTAS del README están en voseo — NO copiar esas a copy de producto).
- **Gate:** `cd frontend && npx vitest run && npx tsc --noEmit` verde; el gate backend `python -m pytest tests/ -m "not network" -n auto -q` no debe regresionar (este SP3 no toca Python).

---

## Task 1: Tokens del tema cálido (global)

**Files:**
- Create: `frontend/src/styles/sp3-warm.css` (tokens globales) — o el archivo de tema global que el proyecto ya importe (verificar `frontend/src/main.tsx`/`index.css`).
- Modify: el punto de import global (`frontend/src/main.tsx` o `index.css`) para cargar los tokens.
- Source: `docs/superpowers/handoffs/sp3/sp3-warm.css` (bloque `:root`, líneas 8-48).

**Interfaces:**
- Produces: variables CSS globales `--paper`, `--paper-2`, `--card`, `--ink`..`--ink-4`, `--clay`/`--clay-deep`/`--clay-soft`/`--clay-tint`, `--sage*`, `--ochre*`, `--slate`, `--down*`, `--serif`, `--sans`, `--read`, `--ease`.

- [ ] **Step 1: Verificar el tema actual y el punto de import.**

Run: `git -C "$(pwd)" grep -nE "index.css|\.css'|--nbc-|:root" -- frontend/src/main.tsx frontend/src/index.css 2>/dev/null; ls frontend/src/*.css`
Lee cómo se cargan hoy los estilos globales (la Valles cálida ya tiene tokens `--nbc-*` / warm — ver memoria `mercado-warm-redesign`). Decide: añadir los tokens SP3 sin colisionar con los existentes (los del mockup son la fuente AA; si un token existe con otro valor, el SP3 manda para la vista per-coin pero NO rompas el resto de la app — si hay colisión real de nombre, prefija con `--sp3-` y úsalo solo en los módulos SP3).

- [ ] **Step 2: Crear `frontend/src/styles/sp3-warm.css`** con el bloque `:root { ... }` de `docs/superpowers/handoffs/sp3/sp3-warm.css` (líneas 8-48), verbatim (los hex ya están corregidos AA). Importa también las fuentes (`Source Serif 4`, `Instrument Sans`) si el proyecto no las carga ya (verifica `index.html`).

- [ ] **Step 3: Importar los tokens globalmente** en el entry (`frontend/src/main.tsx`): `import './styles/sp3-warm.css';` (después del reset/tema base para que SP3 tenga prioridad en su scope).

- [ ] **Step 4: Verificar build.**

Run: `cd frontend && npx tsc --noEmit && npx vitest run` → sin errores, suite sin regresión (los tokens nuevos no rompen nada existente).

- [ ] **Step 5: Commit.**

```bash
git add frontend/src/styles/sp3-warm.css frontend/src/main.tsx
git commit -m "feat(valles-sp3): tokens del tema cálido (paleta AA, fuentes editoriales)"
```

---

## Task 2: `RegimeFrame` + `RegimeStrip` (marco de régimen per-coin)

**Files:**
- Create: `frontend/src/components/valles/regime/RegimeFrame.tsx`
- Create: `frontend/src/components/valles/regime/regime.module.css`
- Create: `frontend/src/components/valles/regime/RegimeFrame.test.tsx`
- Source: `docs/superpowers/handoffs/sp3/sp3-ideaview.jsx` (líneas 16-134: `Fresh`, `RegimeFresh`, `Component`, `RegimeFrame`, `RegimeStrip`) + `sp3-warm.css` (clases `.rf*`, `.rf-strip*`, `.fr*`, `.fr-dead*`).

**Interfaces:**
- Consumes: `RegimeSnapshot` de `../../../types` (campos: `regime.estado` `'alts'|'mixto'|'btc'`, `regime.componentes.{breadth50,outperf_30d,dominancia_btc}` con `valor/lean/estado`, `regime.votos.{alts,neutral,btc,vivos}`, `frescura`). Fetch vía `getAltSeason()` de `../../../api`.
- Produces: `export function RegimeFrame({ regime }: { regime: RegimeSnapshot | null })`, `export function RegimeStrip({ regime }: { regime: RegimeSnapshot | null })`, y un átomo `Fresh` reutilizable.

- [ ] **Step 1: Escribir los tests (vitest).**

`frontend/src/components/valles/regime/RegimeFrame.test.tsx`:
```tsx
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { RegimeFrame, RegimeStrip } from './RegimeFrame';
import type { RegimeSnapshot } from '../../../types';

const snap = (over: Partial<RegimeSnapshot['regime']> = {}, fr = 'fresco'): RegimeSnapshot => ({
  generated_at: '2026-06-19T08:30:00+00:00',
  coverage: { universe: 218, evaluated: 214, complete: false },
  dominancia_fetch: { ok: true, fetched_at: null, source: 'coingecko/global' },
  regime: {
    estado: 'alts',
    componentes: {
      breadth50: { valor: 0.63, lean: 'alts', estado: 'fresco', n: 213 },
      outperf_30d: { valor: 0.082, lean: 'alts', estado: 'fresco' },
      dominancia_btc: { valor: 0.555, lean: 'neutral', estado: 'fresco' },
    },
    votos: { alts: 2, neutral: 1, btc: 0, vivos: 3 },
    n_alts_evaluadas: 213,
    ...over,
  } as never,
  frescura: { estado: fr as never, edad_seg: 1820, generated_at: null, umbral_seg: 43200 },
});

describe('RegimeFrame', () => {
  it('muestra la inclinación, los 3 componentes y la frase verbatim', () => {
    render(<RegimeFrame regime={snap()} />);
    expect(screen.getByText(/inclinación del mercado/i)).toBeTruthy();
    expect(screen.getByText(/amplitud/i)).toBeTruthy();
    expect(screen.getByText(/dominancia BTC/i)).toBeTruthy();
    expect(screen.getByText(/no la moneda que elijas/i)).toBeTruthy();
  });
  it('régimen muerto → ausencia honesta, no datos viejos', () => {
    const dead = { ...snap(), regime: null, frescura: { estado: 'muerto', edad_seg: null, generated_at: null, umbral_seg: 43200 } } as never;
    render(<RegimeFrame regime={dead} />);
    expect(screen.getByText(/no está disponible|caída/i)).toBeTruthy();
  });
  it('componente caído → "sin dato", el resto vive', () => {
    render(<RegimeFrame regime={snap({ componentes: { breadth50: { valor: 0.61, lean: 'alts', estado: 'fresco', n: 209 }, outperf_30d: { valor: 0.07, lean: 'alts', estado: 'fresco' }, dominancia_btc: { valor: null, lean: null, estado: 'muerto' } } } as never)} />);
    expect(screen.getByText(/sin dato/i)).toBeTruthy();
  });
  it('RegimeStrip lleva la nota doctrinal "no la valida"', () => {
    render(<RegimeStrip regime={snap()} />);
    expect(screen.getByText(/no la valida/i)).toBeTruthy();
  });
  it('doctrina: sin lenguaje de veredicto', () => {
    const { container } = render(<RegimeFrame regime={snap()} />);
    expect(/compra|vende|señal|fuertes|débil/i.test(container.textContent ?? '')).toBe(false);
  });
});
```

- [ ] **Step 2: Correr para verque fallan.**

Run: `cd frontend && npx vitest run src/components/valles/regime/RegimeFrame.test.tsx` → FAIL (no existe el módulo).

- [ ] **Step 3: Portar el componente.**

Crea `RegimeFrame.tsx` portando `sp3-ideaview.jsx` líneas 16-134 a TypeScript:
- Tipar todo con `RegimeSnapshot` real (NO los globals del mockup). Reemplaza `window.sp3edad`/`sp3pct1` por helpers locales (o de `../../../utils`): un `edad(seg)` (ver `sp3-data.jsx:33-38`) y formateo de %.
- `Component`, `RegimeFresh`, `Fresh`, `RegimeFrame`, `RegimeStrip` tal como el mockup. Conserva el texto `/*VERBATIM*/` (la frase del régimen) intacto. Conserva las etiquetas de componente en lenguaje natural (microcopy aceptado): `"amplitud (alts sobre su media 50d)"`, `"alts vs BTC · 30 días"`, `"dominancia BTC"`.
- Clases: usa el CSS module (`regime.module.css`) — mapea `.rf` → `styles.rf`, etc. (o usa `:global` si prefieres clases planas; el resto del proyecto usa modules con `styles['kebab-name']`).

Crea `regime.module.css` portando de `sp3-warm.css` todas las reglas `.rf*`, `.rf-strip*`, `.fr*`, `.fr-dead*` (la franja sticky usa `position: sticky`).

- [ ] **Step 4: Correr para verque pasan.**

Run: `cd frontend && npx vitest run src/components/valles/regime/RegimeFrame.test.tsx && npx tsc --noEmit` → 5 tests verdes, tsc limpio.

- [ ] **Step 5: Commit.**

```bash
git add frontend/src/components/valles/regime/
git commit -m "feat(valles-sp3): RegimeFrame + RegimeStrip — el régimen como marco persistente"
```

---

## Task 3: Narrativa rewrite (Vida · Paredes · Jugada) + costura AC7

**Files:**
- Modify (rewrite): `frontend/src/components/valles/idea/Narrativa.tsx`
- Modify: `frontend/src/components/valles/idea/idea.module.css` (clases `.nb*`, `.seam*`, `.seam--ac7`, `.seam__evi*`)
- Modify: `frontend/src/components/valles/idea/Narrativa.test.tsx`
- Source: `sp3-ideaview.jsx` (líneas 144-248: `VidaBlock`, `ParedesBlock`, `JugadaBlock`, helper `toquesDe`) + `sp3-warm.css` (`.nb`, `.seam`).

**Interfaces:**
- Consumes: `ValleyEval`, `SrLevels`, `PlanDerived`/`PlanLive` reales. (El mockup pasa `plan` con forma `{estado_vivo, plan: PlanDerived}` — mapea: el bloque jugada usa `plan` (PlanDerived) directo del `useJugada().derived`.)
- Produces: `<Narrativa vida levels plan />` con los 3 bloques.

- [ ] **Step 1: Actualizar los tests (vitest).**

En `Narrativa.test.tsx` (preservando los asserts de doctrina de SP2):
- Candidata → asserta `/parte baja de su rango/i`, la costura AC7 con los números: `expect(screen.getByText(/no le ganó al azar/i).textContent).toMatch(/9\.92%.*12\.54%/)`, y `expect(screen.queryByText(/en valle|franja angosta/i)).toBeNull()`.
- **No candidata pero viva (decisión #3a):** asserta `/parte alta de su rango/i` y que NO aparece "posición" con número: `expect(screen.queryByText(/posición\s*\d/i)).toBeNull()`.
- No candidata muerta → asserta que lista las razones de muerte.
- Migra el fixture `vida` a los campos reales (pos_in_30d_range, rsi14, pct_vs_sma20, etc.).

- [ ] **Step 2: Correr → FAIL.** `cd frontend && npx vitest run src/components/valles/idea/Narrativa.test.tsx`.

- [ ] **Step 3: Portar Narrativa.tsx.**

Reescribe `Narrativa.tsx` portando `VidaBlock`/`ParedesBlock`/`JugadaBlock` del mockup a TS con tipos reales:
- **VidaBlock candidata:** copy del mockup (posición, SMA20, RSI) + la costura `.seam--ac7` con el texto `/*VERBATIM AC7*/` y las dos filas de evidencia (`9.92%` recomendadas · `12.54%` azar). VERBATIM, intocable.
- **VidaBlock no-candidata-viva (decisión #3a):** copy SIN número de posición → *"No está en la parte baja de su rango ahora. Está viva, pero hoy cotiza en la parte alta de su rango de 30d, así que no entra en este filtro."*
- **VidaBlock no-candidata-muerta:** lista `razones_muerte` con `RAZONES_MUERTE` (mapa legible — portar de `sp3-data.jsx:188-193`; o reusar el del proyecto si existe).
- **ParedesBlock / JugadaBlock:** copy del mockup; la costura del bloque jugada es `/*VERBATIM*/` "Esto sale de tus niveles · la decisión es tuya."
- Reemplaza `window.$`/`pct1` por `formatPrice`/helper de %.

Porta a `idea.module.css` las reglas `.nb*`, `.seam`, `.seam--ac7`, `.seam__evi*` de `sp3-warm.css`.

- [ ] **Step 4: Correr → PASS.** `cd frontend && npx vitest run src/components/valles/idea/Narrativa.test.tsx && npx tsc --noEmit`.

- [ ] **Step 5: Commit.**

```bash
git add frontend/src/components/valles/idea/Narrativa.tsx frontend/src/components/valles/idea/Narrativa.test.tsx frontend/src/components/valles/idea/idea.module.css
git commit -m "feat(valles-sp3): narrativa rediseñada — costura AC7 con evidencia + no-candidata sin pos%"
```

---

## Task 4: `IdeaChart` — banda de rango-30d + marcador + paredes slate

**Files:**
- Modify: `frontend/src/components/valles/idea/chartLayers.ts` (computar `range30` de las candles; modelo de la banda)
- Modify: `frontend/src/components/valles/idea/IdeaChart.tsx` (overlays banda/marcador/caps; paredes a banda slate rellena; leyenda)
- Modify: `frontend/src/components/valles/idea/idea.module.css` (clases `.ch-range*`, `.ch-mark*`, `.ch-wall__band`, etc.)
- Modify: `frontend/src/components/valles/idea/chartLayers.test.ts`
- Source: `sp3-chart.jsx` (banda Pieza 2: líneas 152-173; paredes slate: 116-129; leyenda 98-108) + `sp3-warm.css` (`.ch*`, `.ch-range*`, `.ch-mark*`, `.ch-wall*`).

**Interfaces:**
- Consumes: `levels.candles` (reales), `vida.pos_in_30d_range`, `live` (precio). El `IdeaChart` real ya monta lightweight-charts + capa de anotaciones HTML vía `Y(p)=series.priceToCoordinate(p)`.
- Produces: en `chartLayers.buildLayers`, el modelo `vida` gana `range30: { lo: number; hi: number } | null` (min low / max high de las últimas 30 candles) y `pos: number | null`; el marcador usa `live`.

- [ ] **Step 1: Actualizar `chartLayers.test.ts`** — asserta que `m.vida.range30` se computa de las candles (min/max de las últimas 30) y que `m.vida.pos` refleja `pos_in_30d_range`. (El `IdeaChart` recibe `levels` con `candles`; en el test, pasa candles sintéticas y verifica `range30`.)

- [ ] **Step 2: Correr → FAIL.**

- [ ] **Step 3: Implementar.**

(a) `chartLayers.ts`: en `buildLayers`, computar `range30` del `levels.candles` (últimas 30: `lo=min(low)`, `hi=max(high)`) — NO de un campo `range30` del backend (no existe). El modelo `vida` lleva `{ pos, range30, vivoStamp }`.
(b) `IdeaChart.tsx` (capa de anotaciones, junto a paredes/jugada existentes):
- **Banda 30d** (gateada por `layers.vida`, solo si `range30 && vivo`): un div `.idea-range` con `top=Y(range30.hi)`, `height=Y(range30.lo)-Y(range30.hi)`, rectángulo **punteado arcilla**, con caps "techo del rango 30d · $X" / "piso del rango 30d · $X". Horizontalmente, opcional: limitar a las últimas 30 velas vía `chart.timeScale().timeToCoordinate(candles[N-30].time)` como `left`; si es complejo, banda a ancho completo del plot (acordar en review — el README la quiere sobre las últimas 30, pero a ancho completo es aceptable para v1; documentar la decisión).
- **Marcador**: punto + línea en `top=Y(live)`, tag "pos N% · ahora $X" (con la anti-colisión `showLabel` ya existente).
- **Paredes**: cambiar de regla horizontal a **banda slate rellena** (`top=Y(precio_alto)`, `height=Y(precio_bajo)-Y(precio_alto)`) — clase `.idea-wall__band` (rellena), distinta de la banda 30d (punteada arcilla). Mantener la etiqueta techo/piso·$·toques con anti-colisión.
- **Leyenda**: cambiar `LAYER_LABELS.vida` de `'Vida (¿viva? · posición)'` a `'Vida · rango 30d'`.
(c) `idea.module.css`: portar `.ch-range`, `.ch-range__cap`, `.ch-mark`, `.ch-mark__dot/__line/__tag`, `.ch-wall__band` de `sp3-warm.css`. Banda 30d = borde punteado `--clay`; paredes = relleno `--slate` translúcido.

- [ ] **Step 4: Correr → PASS** + verificación visual.

Run: `cd frontend && npx vitest run src/components/valles/idea/chartLayers.test.ts && npx tsc --noEmit`.
Verificación visual (manual, e2e Playwright opcional — ver `e2e-playwright-harness`): la banda punteada arcilla aparece sobre el rango, el marcador en el precio, las paredes como bandas slate, sin colisión de etiquetas.

- [ ] **Step 5: Commit.**

```bash
git add frontend/src/components/valles/idea/chartLayers.ts frontend/src/components/valles/idea/IdeaChart.tsx frontend/src/components/valles/idea/idea.module.css frontend/src/components/valles/idea/chartLayers.test.ts
git commit -m "feat(valles-sp3): gráfico — banda de rango 30d + marcador (Pieza 2) + paredes slate"
```

---

## Task 5: `IdeaView` restructure (el marco `.iv__frame` que ensambla todo)

**Files:**
- Modify (rewrite): `frontend/src/components/valles/idea/IdeaView.tsx`
- Modify: `frontend/src/components/valles/idea/idea.module.css` (clases `.iv*`, `.iv-frame`, `.iv-body`, `.iv-nav*`, `.iv-head*`, `.iv-sec*`, `.play*`, `.dos*`, `.news__empty`, `.iv-foot*`)
- Modify: `frontend/src/components/valles/idea/IdeaView.test.tsx`
- Source: `sp3-ideaview.jsx` (líneas 250-462: `PlayNow`, `Dossier`, `NAV`, `IdeaView`) + `sp3-warm.css` (`.iv*`, `.play*`, `.dos*`).

**Interfaces:**
- Consumes: `useValleyBundle(symbol)` (→ `vida`, `niveles`, `dossier`), `useJugada(symbol, livePrice)` (→ `derived`, `live`, `conducta`), `getAltSeason()` para el régimen (fetch en IdeaView, o subir a un hook). `RegimeFrame`/`RegimeStrip` (Task 2), `IdeaChart` (Task 4), `Narrativa` (Task 3).
- Produces: la vista per-coin enmarcada.

- [ ] **Step 1: Actualizar `IdeaView.test.tsx`** — render con bundle mockeado: asserta que aparece el marco de régimen (`/inclinación del mercado/i`), la cabecera de la moneda (nombre + símbolo + precio "último cierre"), la nav (Vida·Paredes·Jugada·Quién·Noticias), "Tu jugada ahora", "Quién está detrás", el vacío de noticias, y el footer "Mirar otra moneda". Mockea `getAltSeason`, `useValleyBundle`, `useJugada`.

- [ ] **Step 2: Correr → FAIL.**

- [ ] **Step 3: Reescribir IdeaView.tsx** portando el mockup:
- Estructura: `.iv > .iv__frame > (RegimeFrame, RegimeStrip-sticky, .iv__body > (nav, head, chart-wide, narrativa, PlayNow, Dossier, Noticias, footer))`.
- **Régimen:** fetch `/alt-season` (useEffect o un hook `useRegime`) → `RegimeFrame` + `RegimeStrip`. (El `RegimeStrip` sticky persiste al scroll.)
- **Cabecera moneda:** eyebrow (nombre·símbolo·frescura de la lectura), `<h1>` nombre, precio "$X · último cierre".
- **Gráfico:** `<IdeaChart .../>` con el manejo de no-disponible (Binance caído → callout honesto, sin campos en blanco).
- **Narrativa:** `<Narrativa vida levels plan />`.
- **PlayNow:** portar el lifecycle del mockup (`play` = `live` (PlanLive) + estado fijada local; cerrado usa `conducta`). Mapea: `play.estado_vivo` = `live.data?.estado_vivo`; `_fijada`/`_recienFijada` = estado local (`fijada`/`recienFijada` con `confirmPlan`); cerrado → `conducta.data` (titular + campos). Conserva el copy del mockup.
- **Dossier:** portar `Dossier` del mockup (loading / no_disponible / opaco / rastreable) consumiendo `bundle.dossier`. (Equivale al `DossierBody` actual — reusar/reescribir según el mockup.)
- **Noticias:** vacío honesto "Las noticias de esta moneda aún no están conectadas."
- **Footer:** "← Mirar otra moneda" → `onRestart`.
- Porta a `idea.module.css` todas las clases `.iv*`, `.play*`, `.dos*`, `.news__empty` de `sp3-warm.css`. La franja `.iv-strip`/RegimeStrip es `position: sticky; top: 0`.

- [ ] **Step 4: Correr → PASS.** `cd frontend && npx vitest run src/components/valles/idea/IdeaView.test.tsx && npx tsc --noEmit`.

- [ ] **Step 5: Commit.**

```bash
git add frontend/src/components/valles/idea/IdeaView.tsx frontend/src/components/valles/idea/IdeaView.test.tsx frontend/src/components/valles/idea/idea.module.css
git commit -m "feat(valles-sp3): IdeaView enmarcada — régimen persistente + 8 secciones rediseñadas"
```

---

## Task 6: Cableado en `ValleysFlow` + barrido de doctrina + gate final

**Files:**
- Modify: `frontend/src/components/valles/ValleysFlow.tsx`
- Modify: cualquier `.test.tsx` de valles cuyo fixture/assert rompa por la reestructura.
- Source: `sp3-ideaview.jsx` (cómo IdeaView se auto-enmarca).

**Interfaces:**
- Consumes: `IdeaView` (auto-enmarcado, Task 5), `AltSeasonHeader` (existente), `PickScreen` (existente).

- [ ] **Step 1: Ajustar `ValleysFlow.tsx`.**

Hoy renderiza `<AltSeasonHeader/>` siempre y luego PickScreen o IdeaView. Cambiar: el régimen de la vista per-coin ahora vive DENTRO de `IdeaView` (su marco). Entonces:
- Cuando NO hay símbolo (lista): `<AltSeasonHeader/>` + `<PickScreen/>` (igual que hoy — SP3 NO rediseña la lista).
- Cuando hay símbolo (idea-de-moneda): `<IdeaView/>` solo (que ya trae su `RegimeFrame` adentro). NO renderizar `AltSeasonHeader` arriba (evita doble cabecera de régimen).

- [ ] **Step 2: Barrido de tests + doctrina.**

Run:
```bash
cd frontend && npx vitest run && npx tsc --noEmit
git -C "$(pwd)/.." grep -nE "en valle|franja angosta|tiene jugada|pct_rango|semanas_consolidando" -- frontend/src
```
Migra cualquier fixture/assert que rompa. El `git grep` debe dar **cero** (salvo asserts de doctrina que verifican AUSENCIA, como en `Narrativa.test`). Arregla hasta que vitest + tsc estén verdes.

- [ ] **Step 3: Chequeo de accesibilidad (manual).**

Verifica en los estilos portados: cuerpo ≥18px (`.iv { font-size: 18px }`), targets de botón ≥48px (CTA, leyenda, "mirar otra moneda"), foco visible (`:focus-visible` 3px `--clay-deep`), `@media (prefers-reduced-motion)` respetado. Anota en el PR cualquier ajuste.

- [ ] **Step 4: Gate completo.**

Run:
```bash
cd frontend && npx vitest run && npx tsc --noEmit && npx eslint src --max-warnings=0 || true
python -m pytest tests/ -m "not network" -n auto -q
```
Frontend verde (vitest + tsc); backend sin regresión (SP3 no toca Python; debe seguir igual salvo el flake de auth ortogonal).

- [ ] **Step 5: Commit + mex log.**

```bash
git add frontend/src
mex log "feat: SP3 — rediseño de la vista idea-de-moneda (régimen como marco + banda 30d), 1:1 del handoff de diseño"
git commit -m "feat(valles-sp3): cablear IdeaView enmarcada en ValleysFlow + barrido de doctrina"
```

---

## Notas de ejecución

- **Fuente de verdad visual:** `docs/superpowers/handoffs/sp3/` — ante cualquier duda de layout/estilo/copy, el mockup manda. Lee el componente del mockup ANTES de portar.
- **Verbatim:** los bloques `/*VERBATIM*/` del mockup (costura, AC7 con 9.92%/12.54%, frase del régimen) se copian textual; no se reescriben ni se mueven a letra chica.
- **CSS modules:** el proyecto usa `styles['kebab-name']`. Al portar `sp3-warm.css` (clases planas), mapea a clases de módulo. Si una clase es global (tokens `:root`), va en `frontend/src/styles/sp3-warm.css` (Task 1).
- **Doctrina automática:** el régimen NUNCA tiñe la moneda (Task 5/6). El `git grep` de frases prohibidas (Task 6) es el candado.
- **Fuera de alcance:** rediseño de la lista/PickScreen; 4ª lente; umbrales ajustables; lente-momentum (POST-SHIP). El backend no se toca.
- **Reconciliaciones ya decididas:** `range30` se computa del cliente (Task 4); la rama no-candidata-viva NO muestra pos% (Task 3, decisión #3a); microcopy `◆ prop` aceptado (etiquetas naturales).
