# Valles — Rediseño Cálido Guiado · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar la vista densa de Valles por el recorrido guiado cálido del handoff de diseño (5 pantallas, una cosa por pantalla) para el usuario final ~70 años, montado sobre los 4 endpoints reales que YA existen, preservando la doctrina "exhibe hechos, nunca un veredicto".

**Architecture:** Un orquestador `ValleysFlow` (máquina de pasos + chrome cálido scopeado bajo `.vwRoot` + stepper + nav + copiloto mock) monta 5 pantallas. Un hook `useValleyBundle(symbol)` dispara 3 fetches independientes (Vida/Niveles/Dossier) con loading/error por lente y symbol-guard contra la stale-response race — reemplazando la función `bundle()` del prototipo que fabricaba datos (violación del no-negociable #8). El tema cálido se confina a un subárbol; el chrome dark del trading no se toca.

**Tech Stack:** Vite · React 18 · TypeScript estricto (`noUnusedLocals`/`noUnusedParameters` rompen el build) · CSS Modules · Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/es/2026-06-14-valles-rediseno-calido-design.md` (aprobado).

**Source del port (handoff):** `valles-flow.jsx` (lógica) + `valles-warm.css` (tema). El paquete vive fuera del repo; el implementador lo tiene en el zip de diseño. Las líneas citadas (`valles-flow.jsx:NNN`) refieren a ese archivo.

---

## Decisiones cerradas (de §2 del spec)

- **D1** tema cálido AISLADO scopeado (`.vwRoot`), convive con el dark. **D2** pixel-perfect literal EXCEPTO copy (voseo→tuteo) y los 4 puntos doctrinales. **D3** solo recorrido simple. **D4** eliminar la tabla densa. **D5** móvil en v1. **D6** corregir las 4 colisiones doctrina↔literal. **D7** copiloto MOCK en v1.
- **Reconciliación de ambigüedad del spec §5.3/§5.4:** por D2+D4 se PORTAN las pantallas cálidas pixel-perfect (viz edificio de Niveles, `vw-people`/`vw-channels` de Fund) en vez de reusar `LevelsPanel`/`ProjectDossier`. Se carga a FundScreen el patrón `<Fuente>` de `ProjectDossier` (§4.4). `LevelsPanel`, `ProjectDossier`, `ValleysView`, `CoinCard` quedan muertos → se eliminan (Task 15). Solo se reusa `FreshnessTag`.

---

## Las 4 correcciones doctrinales (D6) — se aplican durante el port, NO después

1. **Tagline** `vw-brand__tag` "dónde operar y dónde no" (`valles-flow.jsx:514`) → texto de solo-hechos.
2. **Color de juicio** sage/ochre en Vida/Niveles → iconos y bandas neutros; sage/ochre SOLO para frescura.
3. **Copy de continuidad** Niveles ("rebotó"/"apoyada en un piso") → solo el pasado de las velas.
4. **Fuente por hecho** en FundScreen (hoy ausente) → `<Fuente>` por miembro/canal.

---

## File Structure

**Crear** (todo bajo `frontend/src/components/valles/`):

| Archivo | Responsabilidad |
|---|---|
| `useValleyBundle.ts` | Hook: 3 fetches independientes Vida/Niveles/Dossier, loading/error por lente, symbol-guard, refresh del dossier. |
| `valles.module.css` | Tema cálido scopeado bajo `.vwRoot` (tokens + clases `vw*` portadas de `valles-warm.css`, con correcciones §4/§7). |
| `atoms.tsx` | Átomos compartidos: `Eyebrow`, `Retry`, `VerNumeros`, `Callout`, `Loading`. |
| `PickScreen.tsx` | Pantalla 0 — la foto del screener real. |
| `VidaScreen.tsx` | Pantalla 1 — ¿está viva? (4 ramas). |
| `NivelesScreen.tsx` | Pantalla 2 — ¿dónde está el precio? (viz edificio, 4 ramas). |
| `FundScreen.tsx` | Pantalla 3 — ¿quién está detrás? (vw-people/channels + Fuente, 4 ramas). |
| `ClosingScreen.tsx` | Pantalla 4 — cierre, 3 columnas SEPARADAS. |
| `Copilot.tsx` | Dock + FAB, mock canned (rechazo ampliado). |
| `ValleysFlow.tsx` | Orquestador: pasos, persistencia, stepper, nav, monta pantallas. Props `{ snapshot, loading }`. |
| `recap.ts` | Helpers puros de los 3 textos-recap del cierre (testeables aislados). |

**Modificar:** `frontend/src/App.tsx` (3 líneas), `frontend/src/components/BottomNav.tsx` (item móvil), `frontend/src/components/FreshnessTag.tsx` (sin cambios de copy — no tiene voseo), `frontend/src/components/atoms/RailIcon.tsx` (si se añade icono propio; si no, reusar `history`).

**Eliminar:** `ValleysView.tsx` (+ `.module.css` + `.test.tsx`), `CoinCard.tsx` (+ `.module.css` + `.test.tsx`), `LevelsPanel.tsx` (+ `.module.css` + `.test.tsx`), `ProjectDossier.tsx` (+ `.module.css` + `.test.tsx`).

**Convención de clases CSS:** las clases `vw-foo__bar` de `valles-warm.css` se portan camelCased: `vw-foo__bar` → `.vwFooBar`, referenciadas como `styles.vwFooBar`. Los tokens (`--paper`, `--clay`…) se re-declaran bajo `.vwRoot` (no `:root`).

---

## Fase 0 — Andamiaje + capa de datos honesta

### Task 1: `useValleyBundle` — los 3 fetches independientes (mata `bundle()`)

**Files:**
- Create: `frontend/src/components/valles/useValleyBundle.ts`
- Test: `frontend/src/components/valles/useValleyBundle.test.tsx`

- [ ] **Step 1: Escribir el test que falla**

```tsx
// useValleyBundle.test.tsx
import { renderHook, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useValleyBundle } from './useValleyBundle';
import * as api from '../../api';

vi.mock('../../api');

const VIDA = { symbol: 'ADAUSDT', estado: 'ok', candidata: true, pct_rango: 0.12, semanas_consolidando: 6, vol_percentil: 0.2, price: 0.45, volumen_usd_dia: 1e7, razones_vida: [] };
const LVL  = { symbol: 'ADAUSDT', estado: 'ok', generated_at: null, price_live: 0.45, zonas: [], ubicacion: { dentro_de: null, techo: null, piso: null } };
const DOS  = { symbol: 'ADAUSDT', equipo: [], equipo_identificado: false, presencia: {}, actividad: {}, financiacion: [], hitos: [], estado_general: 'opaco', no_encontrado_en: [], generated_at: null };

beforeEach(() => {
  vi.mocked(api.getValleyEval).mockResolvedValue(VIDA as never);
  vi.mocked(api.getLevels).mockResolvedValue(LVL as never);
  vi.mocked(api.getDossier).mockResolvedValue(DOS as never);
});

it('arranca las 3 lentes en loading y resuelve cada una por separado', async () => {
  const { result } = renderHook(() => useValleyBundle('ADAUSDT'));
  expect(result.current.vida.loading).toBe(true);
  expect(result.current.niveles.loading).toBe(true);
  expect(result.current.dossier.loading).toBe(true);
  await waitFor(() => expect(result.current.vida.data).toEqual(VIDA));
  expect(result.current.niveles.data).toEqual(LVL);
  expect(result.current.dossier.data).toEqual(DOS);
});

it('marca error (no loading, no data) cuando un fetch falla', async () => {
  vi.mocked(api.getLevels).mockRejectedValue(new Error('429'));
  const { result } = renderHook(() => useValleyBundle('ADAUSDT'));
  await waitFor(() => expect(result.current.niveles.loading).toBe(false));
  expect(result.current.niveles.error).toBe(true);
  expect(result.current.niveles.data).toBeNull();
});

it('ignora la respuesta tardía del símbolo anterior (symbol-guard)', async () => {
  let resolveA: (v: unknown) => void = () => {};
  vi.mocked(api.getValleyEval).mockImplementationOnce(() => new Promise((r) => { resolveA = r; }));
  const { result, rerender } = renderHook(({ s }) => useValleyBundle(s), { initialProps: { s: 'AAAUSDT' } });
  rerender({ s: 'BBBUSDT' });
  await waitFor(() => expect(result.current.vida.data).toMatchObject({ symbol: 'ADAUSDT' })); // B resolvió
  act(() => resolveA({ symbol: 'AAAUSDT', estado: 'ok', candidata: false })); // A tardía
  expect(result.current.vida.data?.symbol).not.toBe('AAAUSDT'); // descartada
});

it('refreshDossier vuelve a pedir el dossier con refresh=true, sin tocar vida/niveles', async () => {
  const { result } = renderHook(() => useValleyBundle('ADAUSDT'));
  await waitFor(() => expect(result.current.dossier.data).toEqual(DOS));
  vi.mocked(api.getValleyEval).mockClear();
  act(() => result.current.refreshDossier());
  await waitFor(() => expect(api.getDossier).toHaveBeenLastCalledWith('ADAUSDT', true));
  expect(api.getValleyEval).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Correr el test para verlo fallar**

Run: `cd frontend && npx vitest run src/components/valles/useValleyBundle.test.tsx`
Expected: FAIL — "Cannot find module './useValleyBundle'".

- [ ] **Step 3: Implementar el hook**

```ts
// useValleyBundle.ts
import { useEffect, useRef, useState } from 'react';
import type { ValleyEval, SrLevels, Dossier } from '../../types';
import { getValleyEval, getLevels, getDossier } from '../../api';

export interface AsyncState<T> { data: T | null; loading: boolean; error: boolean; }
const loadingState = <T,>(): AsyncState<T> => ({ data: null, loading: true, error: false });

export interface ValleyBundle {
  vida:     AsyncState<ValleyEval>;
  niveles:  AsyncState<SrLevels>;
  dossier:  AsyncState<Dossier>;
  refreshDossier: () => void;
}

export function useValleyBundle(symbol: string): ValleyBundle {
  const [vida, setVida]       = useState<AsyncState<ValleyEval>>(loadingState);
  const [niveles, setNiveles] = useState<AsyncState<SrLevels>>(loadingState);
  const [dossier, setDossier] = useState<AsyncState<Dossier>>(loadingState);
  const [refreshN, setRefreshN] = useState(0);
  const forceRef = useRef(false);

  // Vida + Niveles: re-fetch solo cuando cambia el símbolo.
  useEffect(() => {
    if (!symbol) return;
    let active = true;
    setVida(loadingState()); setNiveles(loadingState());
    getValleyEval(symbol)
      .then((d) => { if (active) setVida({ data: d, loading: false, error: false }); })
      .catch(() => { if (active) setVida({ data: null, loading: false, error: true }); });
    getLevels(symbol)
      .then((d) => { if (active) setNiveles({ data: d, loading: false, error: false }); })
      .catch(() => { if (active) setNiveles({ data: null, loading: false, error: true }); });
    return () => { active = false; };
  }, [symbol]);

  // Dossier: re-fetch al cambiar el símbolo y al pedir refresh. `force` se consume
  // del ref, así un cambio de símbolo nunca dispara la regeneración cara de Exa.
  useEffect(() => {
    if (!symbol) return;
    let active = true;
    const force = forceRef.current; forceRef.current = false;
    setDossier(loadingState());
    getDossier(symbol, force)
      .then((d) => { if (active) setDossier({ data: d, loading: false, error: false }); })
      .catch(() => { if (active) setDossier({ data: null, loading: false, error: true }); });
    return () => { active = false; };
  }, [symbol, refreshN]);

  return { vida, niveles, dossier, refreshDossier: () => { forceRef.current = true; setRefreshN((n) => n + 1); } };
}
```

- [ ] **Step 4: Correr el test para verlo pasar**

Run: `cd frontend && npx vitest run src/components/valles/useValleyBundle.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/valles/useValleyBundle.ts frontend/src/components/valles/useValleyBundle.test.tsx
git commit -m "feat(valles): useValleyBundle — 3 fetches independientes con symbol-guard (reemplaza bundle())"
```

---

### Task 2: `valles.module.css` — tema cálido scopeado bajo `.vwRoot`

**Files:**
- Create: `frontend/src/components/valles/valles.module.css`

> No es TDD (es CSS). El gate es: importarlo NO repinta el resto de la app (verificado en Task 11 al montar).

- [ ] **Step 1: Crear el archivo con el bloque de tokens scopeado + las correcciones de contraste (§7)**

Portar TODO `valles-warm.css` aplicando estas transformaciones mecánicas:
1. El bloque `:root { --paper… }` → `.vwRoot { … }` (abajo, ya con los hex corregidos de §7).
2. **Borrar** las reglas bare-element `html, body { … }`, `* { … }`, `::selection { … }` (repintan toda la app). El `background`/`color`/`font` del paper se aplican a `.vwRoot` en su lugar.
3. Cada clase `.vw-foo__bar` → `.vwFooBar` (camelCase), anidada conceptualmente bajo `.vwRoot` (en CSS module basta el prefijo; el scoping real lo da el wrapper `.vwRoot` en el DOM + que estas clases solo se usan dentro de él).
4. Aplicar las correcciones §4.2 (color de juicio) y §7 (a11y) marcadas abajo.

```css
/* valles.module.css — tema cálido de Valles, CONFINADO a .vwRoot.
   Portado de valles-warm.css. NO declarar nada en :root ni en html/body/*. */

.vwRoot {
  /* tokens (hex de contraste ya corregidos · §7) */
  --paper: #F4F0E8; --paper-2: #EFE9DD;
  --card: #FBF9F4; --card-edge: #E4DCCC; --card-edge-2: #D8CDB8;
  --ink: #2A2722; --ink-2: #5C564A;
  --ink-3: #6E6757;   /* era #8A8270 (3.35:1, reprueba) → ~4.6:1 */
  --ink-4: #6F6856;   /* era #ABA08A (2.27:1, reprueba) → ~4.6:1 */
  --clay: #B8542E; --clay-deep: #9A4424; --clay-soft: #EDD9CC; --clay-tint: #F3E6DC;
  --sage: #5E7048; --sage-soft: #E2E6D4; --sage-tint: #ECEFE2;
  --ochre: #8A5E1C;   /* era #A9772A (3.45:1, reprueba; es el color de "atención") → ~5:1 */
  --ochre-soft: #EFE0C4; --ochre-tint: #F2E8D4;
  --slate: #4C5A66;
  --serif: 'Source Serif 4', Georgia, 'Times New Roman', serif;
  --sans: 'Instrument Sans', ui-sans-serif, system-ui, -apple-system, sans-serif;
  --maxw: 660px;
  --ease: cubic-bezier(0.22, 1, 0.36, 1);

  background: var(--paper);
  color: var(--ink);
  font-family: var(--sans);
  min-height: 100%;
}

/* … aquí van TODAS las clases vw* portadas camelCased desde valles-warm.css … */
/* Reglas de corrección obligatorias durante el port: */

/* §4.2 — color de juicio FUERA de Vida/Niveles. Iconos de respuesta neutros: */
.vwAnswerIcon { color: var(--slate); }            /* era sage/ochre según rama */
/* NO portar .vwAnswerIconSage / .vwAnswerIconOchre como verde/ámbar de juicio. */
/* §4.2 — bandas de niveles en un mismo neutro (no piso=verde / techo=ámbar): */
.vwFloorPiso, .vwFloorTecho { color: var(--ink-2); border-color: var(--card-edge-2); }
/* sage/ochre se reservan EXCLUSIVAMENTE para frescura (clases .vwFresh*). */

/* §7 — texto-clay legible: usar clay-deep para texto, clay solo fondo/borde */
.vwEyebrowCoin, .vwFactB, .vwCloseIcon { color: var(--clay-deep); }

/* §7 — táctil ≥48px */
.vwBtn { min-height: 48px; }
.vwDockClose { min-width: 48px; min-height: 48px; display: grid; place-items: center; }
.vwSugg, .vwCalloutRetry, .vwChannel, .vwMoreToggle, .vwStep { min-height: 48px; }
.vwFab { width: 56px; height: 56px; }

/* §7 — cuerpo ≥18px (presbicia) */
.vwAnswerSay, .vwPickLead, .vwCloseBody, .vwFact { font-size: 18px; }
.vwCalloutSub, .vwRecapQ, .vwBubble, .vwPersonRole { font-size: 16px; }
.vwTag { font-size: 12px; }

/* §7 — foco visible (no existe hoy) */
.vwRoot :where(button, a, input, [tabindex]):focus-visible {
  outline: 3px solid var(--clay-deep); outline-offset: 2px;
}

/* §7 — botón "Atrás" deshabilitado VISIBLE-inactivo (no opacity:0) */
.vwBtn:disabled { opacity: 0.4; cursor: default; }

/* §7 — reduced-motion global dentro del árbol cálido */
@media (prefers-reduced-motion: reduce) {
  .vwRoot *, .vwRoot *::before, .vwRoot *::after {
    animation-duration: 0.001ms !important; transition-duration: 0.001ms !important;
  }
  .vwCand, .vwCoin, .vwFab, .vwMoreCaret { transform: none !important; }
}

/* §7 — stepper: labels SIEMPRE visibles (no display:none <720px) */
.vwStepLabel { display: inline; }
```

> **Nota de port:** el implementador copia el resto de las reglas de layout de `valles-warm.css`
> (`vwScreen`, `vwBand`, `vwBuilding`, `vwHere`, `vwPeople`, `vwChannels`, `vwCallout`, `vwCand`,
> `vwRecap`, `vwDock`, `vwStage`, `vwNav`, `vwTop`, `vwSteps`…) camelCased, sin tocar geometría
> (pixel-perfect D2). Lo único que cambia respecto al original son los bloques de corrección de arriba.

- [ ] **Step 2: Verificar que tsc/build no rompe por el import**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS (CSS modules no afectan tsc; este paso confirma que no se introdujo un import roto).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/valles/valles.module.css
git commit -m "feat(valles): tema cálido scopeado a .vwRoot (correcciones contraste/táctil/motion §7)"
```

---

### Task 3: Átomos compartidos (`Eyebrow`, `Retry`, `VerNumeros`, `Callout`, `Loading`)

**Files:**
- Create: `frontend/src/components/valles/atoms.tsx`
- Test: `frontend/src/components/valles/atoms.test.tsx`

- [ ] **Step 1: Test que falla**

```tsx
// atoms.test.tsx
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { Eyebrow, Loading, Callout } from './atoms';

it('Eyebrow muestra el nombre humano y el símbolo', () => {
  render(<Eyebrow symbol="ADAUSDT" />);
  expect(screen.getByText('Cardano')).toBeInTheDocument();
  expect(screen.getByText('ADAUSDT')).toBeInTheDocument();
});

it('Loading anuncia que está cargando (rama distinta de error)', () => {
  render(<Loading label="Revisando…" />);
  expect(screen.getByText('Revisando…')).toBeInTheDocument();
});

it('Callout renderiza título y subtítulo', () => {
  render(<Callout tone="mute" icon="?" title="No se pudo" sub="Es la herramienta." />);
  expect(screen.getByText('No se pudo')).toBeInTheDocument();
});
```

- [ ] **Step 2: Correr — falla** (`vitest run src/components/valles/atoms.test.tsx`).

- [ ] **Step 3: Implementar los átomos**

```tsx
// atoms.tsx
import React, { useState } from 'react';
import type { Frescura } from '../../types';
import { FreshnessTag } from '../FreshnessTag';
import styles from './valles.module.css';

const NAMES: Record<string, string> = {
  ADAUSDT: 'Cardano', XLMUSDT: 'Stellar', RUNEUSDT: 'THORChain', PENDLEUSDT: 'Pendle',
  JUPUSDT: 'Jupiter', UNIUSDT: 'Uniswap', INJUSDT: 'Injective', GMXUSDT: 'GMX',
  BTCUSDT: 'Bitcoin', PYTHUSDT: 'Pyth',
};
export const humanName = (s: string): string => NAMES[s] ?? s.replace('USDT', '');

export const Eyebrow: React.FC<{ symbol: string; frescura?: Frescura }> = ({ symbol, frescura }) => (
  <div className={styles.vwEyebrow}>
    <span className={styles.vwEyebrowCoin}>{humanName(symbol)}</span>
    <span className={styles.vwEyebrowSym}>{symbol}</span>
    {frescura && <FreshnessTag frescura={frescura} />}
  </div>
);

export const Retry: React.FC<{ onClick: () => void }> = ({ onClick }) => (
  <button className={styles.vwCalloutRetry} onClick={onClick}>↻ Intentar de nuevo</button>
);

export const Loading: React.FC<{ label: string }> = ({ label }) => (
  <div className={`${styles.vwCallout} ${styles.vwCalloutMute}`} aria-busy="true">
    <span className={styles.vwCalloutIcon} aria-hidden="true">⧖</span>
    <div><div className={styles.vwCalloutTitle}>{label}</div></div>
  </div>
);

export const Callout: React.FC<{
  tone: 'mute' | 'ochre'; icon: string; title: string; sub?: React.ReactNode; children?: React.ReactNode;
}> = ({ tone, icon, title, sub, children }) => (
  <div className={`${styles.vwCallout} ${tone === 'ochre' ? styles.vwCalloutOchre : styles.vwCalloutMute}`}>
    <span className={styles.vwCalloutIcon} aria-hidden="true">{icon}</span>
    <div>
      <div className={styles.vwCalloutTitle}>{title}</div>
      {sub && <div className={styles.vwCalloutSub}>{sub}</div>}
      {children}
    </div>
  </div>
);

export const VerNumeros: React.FC<{ items: { k: string; v: string; note?: string }[] }> = ({ items }) => {
  const [open, setOpen] = useState(false);
  if (items.length === 0) return null;
  return (
    <div className={styles.vwMore}>
      <button className={styles.vwMoreToggle} onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className={`${styles.vwMoreCaret} ${open ? styles.vwMoreCaretOpen : ''}`} aria-hidden="true">▸</span>
        {open ? 'Ocultar los números' : 'Ver los números'}
      </button>
      {open && (
        <div className={styles.vwMorePanel}>
          {items.map((it, i) => (
            <div className={styles.vwNum} key={i}>
              <div className={styles.vwNumK}>{it.k}</div>
              <div className={styles.vwNumV}>{it.v}</div>
              {it.note && <div className={styles.vwNumNote}>{it.note}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
```

> **Voz (§8):** todo el copy de los átomos ya está en tuteo ("Intentar de nuevo", "Ver los números").

- [ ] **Step 4: Correr — pasa.**

- [ ] **Step 5: Commit** — `git add frontend/src/components/valles/atoms.tsx frontend/src/components/valles/atoms.test.tsx && git commit -m "feat(valles): átomos cálidos (Eyebrow/Retry/VerNumeros/Callout/Loading)"`

---

## Fase 1 — PickScreen real

### Task 4: `PickScreen` sobre el `ValleySnapshot` real

**Files:**
- Create: `frontend/src/components/valles/PickScreen.tsx`
- Test: `frontend/src/components/valles/PickScreen.test.tsx`

- [ ] **Step 1: Test que falla**

```tsx
// PickScreen.test.tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { PickScreen } from './PickScreen';
import type { ValleySnapshot } from '../../types';

const snap = (over: Partial<ValleySnapshot> = {}): ValleySnapshot => ({
  generated_at: '2026-06-14T10:00:00Z',
  coverage: { universe: 200, evaluated: 180, complete: true },
  candidates: [
    { symbol: 'ADAUSDT', price: 0.45, pct_rango: 0.12, semanas_consolidando: 6, vol_percentil: 0.2, volumen_usd_dia: 1e7, distancia_ath_pct: 0.7, razones_vida: [] },
    { symbol: 'XLMUSDT', price: 0.11, pct_rango: 0.09, semanas_consolidando: 8, vol_percentil: 0.3, volumen_usd_dia: 5e6, distancia_ath_pct: 0.8, razones_vida: [] },
  ],
  frescura: { estado: 'fresco', edad_seg: 1800, generated_at: '2026-06-14T10:00:00Z', umbral_seg: 43200 },
  ...over,
});

it('lista las candidatas reales y dispara onPick con el símbolo', async () => {
  const onPick = vi.fn();
  render(<PickScreen snapshot={snap()} onPick={onPick} />);
  expect(screen.getByText('Cardano')).toBeInTheDocument();
  await userEvent.click(screen.getByText('Cardano'));
  expect(onPick).toHaveBeenCalledWith('ADAUSDT');
});

it('distingue "el screener nunca corrió" de "corrió y no halló"', () => {
  const { rerender } = render(<PickScreen snapshot={snap({ candidates: [], coverage: { universe: 0, evaluated: 0, complete: false }, frescura: { estado: 'muerto', edad_seg: null, generated_at: null, umbral_seg: 43200 } })} onPick={vi.fn()} />);
  expect(screen.getByText(/aún no ha completado un ciclo|todavía no corrió/i)).toBeInTheDocument();
  rerender(<PickScreen snapshot={snap({ candidates: [], coverage: { universe: 200, evaluated: 200, complete: true } })} onPick={vi.fn()} />);
  expect(screen.getByText(/ninguna moneda en valle/i)).toBeInTheDocument();
});

it('el buscador agrega USDT y dispara onPick', async () => {
  const onPick = vi.fn();
  render(<PickScreen snapshot={snap()} onPick={onPick} />);
  const input = screen.getByPlaceholderText(/escribe su símbolo/i);
  await userEvent.type(input, 'sol{Enter}');
  expect(onPick).toHaveBeenCalledWith('SOLUSDT');
});
```

- [ ] **Step 2: Correr — falla.**

- [ ] **Step 3: Implementar** (port de `valles-flow.jsx:105-153`, bindeado al snapshot real, copy en tuteo §8, titular sobre la frescura)

```tsx
// PickScreen.tsx
import React, { useState } from 'react';
import type { ValleySnapshot } from '../../types';
import { FreshnessTag } from '../FreshnessTag';
import { humanName } from './atoms';
import { formatPrice } from '../../utils';
import styles from './valles.module.css';

export const PickScreen: React.FC<{ snapshot: ValleySnapshot; onPick: (sym: string) => void }> = ({ snapshot, onPick }) => {
  const [q, setQ] = useState('');
  const { candidates, coverage, frescura } = snapshot;

  return (
    <div className={styles.vwScreen}>
      {/* Titular POR ENCIMA del semáforo de frescura (§5.1) */}
      <h1 className={styles.vwPickQ}>
        {candidates.length > 0
          ? `Hoy hay ${candidates.length} ${candidates.length === 1 ? 'moneda' : 'monedas'} en valle.`
          : coverage.complete
            ? 'Hoy ninguna moneda en valle.'
            : 'El screener todavía no corrió.'}
      </h1>
      <div className={styles.vwEyebrow}>{frescura && <FreshnessTag frescura={frescura} />}</div>

      {candidates.length > 0 && (
        <p className={styles.vwPickLead}>
          Son las que ahora mismo se mueven poco y siguen vivas — el filtro que hace Valles,
          mecánico, no un consejo. Elige una para mirarla de cerca con las tres lentes.
        </p>
      )}

      <div className={styles.vwCands}>
        {candidates.map((c) => (
          <button key={c.symbol} className={styles.vwCand} onClick={() => onPick(c.symbol)}>
            <div className={styles.vwCandId}>
              <div className={styles.vwCandName}>{humanName(c.symbol)}</div>
              <div className={styles.vwCandSym}>{c.symbol}</div>
            </div>
            <div className={styles.vwCandFact}>
              <span className={styles.vwCandTag} aria-hidden="true">● en valle</span>
              se mueve un <b>{(c.pct_rango * 100).toFixed(1)}%</b> · <b>{c.semanas_consolidando} semanas</b> quieta
            </div>
            <div className={styles.vwCandPrice}>${formatPrice(c.price)}</div>
            <div className={styles.vwCandGo} aria-hidden="true">→</div>
          </button>
        ))}
      </div>

      <div className={styles.vwEntryMeta}>
        <span>Se miraron <b>{coverage.evaluated}</b> de {coverage.universe} monedas del universo.</span>
        <span className={styles.vwEntrySep} />
        <span>Ordenadas por volumen — no por preferencia.</span>
      </div>

      <div className={styles.vwEntrySearch}>
        <div className={styles.vwEntrySearchLabel}>¿Buscas otra que no está en la lista?</div>
        <div className={styles.vwPickSearch}>
          <span aria-hidden="true">⌕</span>
          <input
            placeholder="escribe su símbolo (ej. SOLUSDT)"
            value={q}
            onChange={(e) => setQ(e.target.value.toUpperCase())}
            onKeyDown={(e) => { if (e.key === 'Enter' && q.trim()) onPick(q.endsWith('USDT') ? q : `${q}USDT`); }}
          />
        </div>
      </div>
    </div>
  );
};
```

> Orden neutral: el backend ya devuelve `candidates` por `volumen_usd_dia` desc; **no reordenar**.

- [ ] **Step 4: Correr — pasa.**
- [ ] **Step 5: Commit** — `git commit -m "feat(valles): PickScreen sobre snapshot real (3 ejes de estado, orden neutral, tuteo)"`

---

## Fase 2 — Las 3 lentes (el grueso)

### Task 5: `VidaScreen` (4 ramas, icono neutro)

**Files:**
- Create: `frontend/src/components/valles/VidaScreen.tsx`
- Test: `frontend/src/components/valles/VidaScreen.test.tsx`

- [ ] **Step 1: Test que falla**

```tsx
// VidaScreen.test.tsx
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { VidaScreen } from './VidaScreen';
import type { AsyncState } from './useValleyBundle';
import type { ValleyEval } from '../../types';

const st = (over: Partial<AsyncState<ValleyEval>>): AsyncState<ValleyEval> => ({ data: null, loading: false, error: false, ...over });

it('rama cargando: muestra loading, NO "falló"', () => {
  render(<VidaScreen symbol="ADAUSDT" state={st({ loading: true })} frescura={undefined} />);
  expect(screen.queryByText(/no se pudo|falló/i)).not.toBeInTheDocument();
  expect(screen.getByText(/revisando/i)).toBeInTheDocument();
});

it('rama error: "problema de la herramienta"', () => {
  render(<VidaScreen symbol="ADAUSDT" state={st({ error: true })} frescura={undefined} />);
  expect(screen.getByText(/no se pudo revisar/i)).toBeInTheDocument();
});

it('rama no-candidata: lista razones_muerte sin crashear si es undefined', () => {
  render(<VidaScreen symbol="ADAUSDT" state={st({ data: { symbol: 'ADAUSDT', estado: 'ok', candidata: false, vivo: false } })} frescura={undefined} />);
  expect(screen.getByText(/muy quieta/i)).toBeInTheDocument();
});

it('rama candidata: muestra rango, semanas y quietud derivada', () => {
  render(<VidaScreen symbol="ADAUSDT" frescura={undefined}
    state={st({ data: { symbol: 'ADAUSDT', estado: 'ok', candidata: true, pct_rango: 0.12, semanas_consolidando: 6, vol_percentil: 0.2, price: 0.45, volumen_usd_dia: 1e7 } })} />);
  expect(screen.getByText(/viva y tranquila/i)).toBeInTheDocument();
  expect(screen.getByText(/80%/)).toBeInTheDocument(); // 100 - round(0.2*100)
});
```

- [ ] **Step 2: Correr — falla.**

- [ ] **Step 3: Implementar** (port de `valles-flow.jsx:158-218` + 4ª rama loading + `razones_muerte ?? []` + icono neutro §4.2)

```tsx
// VidaScreen.tsx
import React from 'react';
import type { Frescura, ValleyEval } from '../../types';
import type { AsyncState } from './useValleyBundle';
import { Eyebrow, Callout, Loading, VerNumeros } from './atoms';
import styles from './valles.module.css';

const RAZONES_MUERTE: Record<string, string> = {
  volumen_bajo_piso: 'El volumen está por debajo del piso que se pide.',
  volumen_agonizante: 'El volumen viene cayendo hasta casi apagarse.',
  velas_planas: 'Las velas están casi planas — apenas se mueve.',
  historia_insuficiente: 'No hay suficiente historia para evaluarla.',
};

export const VidaScreen: React.FC<{ symbol: string; state: AsyncState<ValleyEval>; frescura?: Frescura }> = ({ symbol, state, frescura }) => {
  const { data, loading, error } = state;
  let answer: React.ReactNode;

  if (loading) {
    answer = <Loading label="Revisando si está viva…" />;
  } else if (error || !data || data.estado === 'no_disponible') {
    answer = <Callout tone="mute" icon="?" title="No se pudo revisar ahora" sub="Es un problema de la herramienta, no de la moneda." />;
  } else if (data.candidata === false) {
    answer = (
      <div className={styles.vwAnswer}>
        <div className={styles.vwAnswerRow}>
          <div className={styles.vwAnswerIcon} aria-hidden="true">▱</div>
          <div>
            <div className={styles.vwAnswerLead}>Está muy quieta</div>
            <div className={styles.vwAnswerSay}>Casi no se mueve, así que no entra en este tipo de análisis. Esto fue lo que se vio:</div>
          </div>
        </div>
        <ul className={styles.vwFacts}>
          {(data.razones_muerte ?? []).map((r) => (
            <li className={styles.vwFact} key={r}><span className={styles.vwFactB} aria-hidden="true">—</span>{RAZONES_MUERTE[r] ?? r}</li>
          ))}
        </ul>
      </div>
    );
  } else {
    const quietud = 100 - Math.round((data.vol_percentil ?? 0) * 100);
    answer = (
      <div className={styles.vwAnswer}>
        <div className={styles.vwAnswerRow}>
          <div className={styles.vwAnswerIcon} aria-hidden="true">∿</div>
          <div>
            <div className={styles.vwAnswerLead}>Está viva y tranquila</div>
            <div className={styles.vwAnswerSay}>Se mueve dentro de una franja angosta, sin pegar saltos. A eso se le dice estar <b>“en valle”</b>.</div>
          </div>
        </div>
        <ul className={styles.vwFacts}>
          <li className={styles.vwFact}><span className={styles.vwFactB} aria-hidden="true">●</span>Se mueve poco: su franja es de un <b>{((data.pct_rango ?? 0) * 100).toFixed(1)}%</b> de su precio.</li>
          <li className={styles.vwFact}><span className={styles.vwFactB} aria-hidden="true">●</span>Lleva <b>{data.semanas_consolidando} semanas</b> sin salirse de esa franja.</li>
          <li className={styles.vwFact}><span className={styles.vwFactB} aria-hidden="true">●</span>Hoy está más quieta que el <b>{quietud}%</b> de su último año.</li>
        </ul>
        <VerNumeros items={[
          { k: 'Ancho de la franja', v: `${((data.pct_rango ?? 0) * 100).toFixed(1)}%`, note: 'de su precio' },
          { k: 'Semanas consolidando', v: `${data.semanas_consolidando} sem`, note: 'sin salir de la banda' },
          { k: 'Volatilidad (percentil)', v: `p${Math.round((data.vol_percentil ?? 0) * 100)}`, note: 'en su propio año' },
        ]} />
      </div>
    );
  }

  return (
    <div className={styles.vwScreen}>
      <Eyebrow symbol={symbol} frescura={frescura} />
      <h2 className={styles.vwQuestion}>¿Está viva la moneda?</h2>
      {answer}
    </div>
  );
};
```

> Frescura heredada de la foto (§5.2): el `frescura` que recibe es `snapshot.frescura` (hasta 12h);
> el copy no implica que la lectura de Vida sea de "hace 5 min". `vw-band` (la franja con "hoy" al
> 50% fijo) se OMITE del port por fingir precisión que no tiene (§7 honestidad del dibujo); se
> conservan los hechos en viñetas y `VerNumeros`.

- [ ] **Step 4: Correr — pasa.**
- [ ] **Step 5: Commit** — `git commit -m "feat(valles): VidaScreen 4 ramas sobre ValleyEval real, icono neutro"`

---

### Task 6: `NivelesScreen` (viz edificio, 4 ramas, copy de continuidad corregido §4.3)

**Files:**
- Create: `frontend/src/components/valles/NivelesScreen.tsx`
- Test: `frontend/src/components/valles/NivelesScreen.test.tsx`

- [ ] **Step 1: Test que falla**

```tsx
// NivelesScreen.test.tsx
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { NivelesScreen } from './NivelesScreen';
import type { AsyncState } from './useValleyBundle';
import type { SrLevels } from '../../types';

const st = (o: Partial<AsyncState<SrLevels>>): AsyncState<SrLevels> => ({ data: null, loading: false, error: false, ...o });

it('error y no_disponible NO se colapsan en "sin paredes"', () => {
  render(<NivelesScreen symbol="ADAUSDT" state={st({ error: true })} />);
  expect(screen.getByText(/no se pudo/i)).toBeInTheDocument();
  expect(screen.queryByText(/no hay paredes/i)).not.toBeInTheDocument();
});

it('zonas vacías: "todavía no hay paredes claras"', () => {
  render(<NivelesScreen symbol="ADAUSDT" state={st({ data: { symbol: 'ADAUSDT', estado: 'ok', generated_at: null, price_live: 0.45, zonas: [], ubicacion: { dentro_de: null, techo: null, piso: null } } })} />);
  expect(screen.getByText(/todavía no hay paredes claras/i)).toBeInTheDocument();
});

it('en un piso: describe el PASADO de las velas, no predice ("rebotó/aguantará")', () => {
  render(<NivelesScreen symbol="ADAUSDT" state={st({ data: {
    symbol: 'ADAUSDT', estado: 'ok', generated_at: null, price_live: 0.45,
    zonas: [{ tipo: 'soporte', precio_bajo: 0.4, precio_alto: 0.44, centro: 0.42, toques: 3, confluencia_redondo: [] }],
    ubicacion: { dentro_de: { tipo: 'soporte', precio_bajo: 0.4, precio_alto: 0.44, centro: 0.42, toques: 3, confluencia_redondo: [] }, techo: { centro: 0.5, dist_pct: 11.1 }, piso: { centro: 0.42, dist_pct: -6.6 } },
  } })} />);
  expect(screen.getByText(/ya giró ahí 3 veces/i)).toBeInTheDocument();
  expect(screen.queryByText(/rebotó|aguantará|apoyada/i)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Correr — falla.**

- [ ] **Step 3: Implementar** (port de `valles-flow.jsx:223-307`, `dist_pct` ya firmado, floors neutros §4.2, copy §4.3)

```tsx
// NivelesScreen.tsx
import React from 'react';
import type { SrLevels } from '../../types';
import type { AsyncState } from './useValleyBundle';
import { Eyebrow, Callout, Loading, VerNumeros } from './atoms';
import { formatPrice } from '../../utils';
import styles from './valles.module.css';

const px = (n: number | null | undefined) => (n == null ? '—' : formatPrice(n));

export const NivelesScreen: React.FC<{ symbol: string; state: AsyncState<SrLevels> }> = ({ symbol, state }) => {
  const { data, loading, error } = state;
  let answer: React.ReactNode;

  if (loading) {
    answer = <Loading label="Calculando dónde está el precio…" />;
  } else if (error || !data || data.estado === 'no_disponible') {
    answer = <Callout tone="mute" icon="?" title="No se pudo revisar ahora" sub="Prueba de nuevo en un momento." />;
  } else if (data.zonas.length === 0) {
    answer = (
      <Callout tone="ochre" icon="▢" title="Todavía no hay paredes claras"
        sub={<>El precio no giró suficientes veces en ningún lugar como para marcar una pared.{data.price_live != null && <> Hoy vale <b>${px(data.price_live)}</b>.</>}</>} />
    );
  } else {
    const u = data.ubicacion;
    const inside = u.dentro_de;
    let lead: string, say: React.ReactNode, ratio: number;
    if (inside && inside.tipo === 'soporte') {
      lead = 'El precio está sobre un piso'; ratio = 0.7;
      say = <>Es un piso donde el precio <b>ya giró ahí {inside.toques} veces</b> antes — un hecho del gráfico.</>;
    } else if (inside && inside.tipo === 'resistencia') {
      lead = 'El precio está contra un techo'; ratio = 0.3;
      say = <>Es un techo donde el precio <b>ya giró ahí {inside.toques} veces</b> antes — un hecho del gráfico.</>;
    } else {
      lead = 'Está en el medio';
      const t = u.techo ? u.techo.dist_pct : 1, p = u.piso ? Math.abs(u.piso.dist_pct) : 1;
      ratio = t / (t + p);
      say = <>No está pegado a ninguna pared: queda entre un techo arriba y un piso abajo.</>;
    }
    const hereTop = 58 + Math.max(0.08, Math.min(0.92, ratio)) * (280 - 116);
    answer = (
      <div className={styles.vwAnswer}>
        <div className={styles.vwAnswerRow}>
          <div className={styles.vwAnswerIcon} aria-hidden="true">⌖</div>
          <div><div className={styles.vwAnswerLead}>{lead}</div><div className={styles.vwAnswerSay}>{say}</div></div>
        </div>
        <div className={styles.vwLevels}>
          <div className={styles.vwBuilding}>
            {u.techo && (
              <div className={`${styles.vwFloor} ${styles.vwFloorTecho}`}>
                <span className={styles.vwFloorArrow} aria-hidden="true">↑</span>
                <span className={styles.vwFloorLbl}>Techo · pared de arriba<b>${px(u.techo.centro)}</b></span>
              </div>
            )}
            <div className={styles.vwHere} style={{ top: `${hereTop}px` }}>
              <span className={styles.vwHereDot} aria-hidden="true" /><span className={styles.vwHereLine} aria-hidden="true" />
              <span className={styles.vwHereTag}>estás acá · ${px(data.price_live)}</span>
            </div>
            {u.piso && (
              <div className={`${styles.vwFloor} ${styles.vwFloorPiso}`}>
                <span className={styles.vwFloorArrow} aria-hidden="true">↓</span>
                <span className={styles.vwFloorLbl}>Piso · pared de abajo<b>${px(u.piso.centro)}</b></span>
              </div>
            )}
          </div>
          <div className={styles.vwLevelsRead}>
            {u.techo && <div><div className={styles.vwDistK}>El techo más cercano está</div><div className={styles.vwDistV}>{u.techo.dist_pct.toFixed(1)}% más arriba</div></div>}
            {u.piso && <div><div className={styles.vwDistK}>El piso más cercano está</div><div className={styles.vwDistV}>{Math.abs(u.piso.dist_pct).toFixed(1)}% más abajo</div></div>}
          </div>
        </div>
        <VerNumeros items={data.zonas.map((z) => ({
          k: `${z.tipo === 'resistencia' ? 'Techo' : 'Piso'} · $${px(z.centro)}`,
          v: `${z.toques} toques`,
          note: `banda $${px(z.precio_bajo)}–$${px(z.precio_alto)}`,
        }))} />
      </div>
    );
  }

  return (
    <div className={styles.vwScreen}>
      <Eyebrow symbol={symbol} />
      <h2 className={styles.vwQuestion}>¿Dónde está el precio ahora?</h2>
      {answer}
      <div className={styles.vwNeutral}>
        Las paredes son lugares donde el precio <b>ya giró antes</b> — son hechos del gráfico.
        No son una señal de comprar ni de vender.
      </div>
    </div>
  );
};
```

> `dist_pct` ya viene firmado y a 2 decimales del backend (`SrRef`); no recalcular. `vwDistV` en
> neutro (sin `--techo`/`--piso` de color de juicio, §4.2).

- [ ] **Step 4: Correr — pasa.**
- [ ] **Step 5: Commit** — `git commit -m "feat(valles): NivelesScreen viz edificio, 4 ramas, copy de continuidad corregido (D.1 §1)"`

---

### Task 7: `FundScreen` (vw-people/channels + `<Fuente>` por hecho §4.4)

**Files:**
- Create: `frontend/src/components/valles/FundScreen.tsx`
- Test: `frontend/src/components/valles/FundScreen.test.tsx`

- [ ] **Step 1: Test que falla**

```tsx
// FundScreen.test.tsx
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { FundScreen } from './FundScreen';
import type { AsyncState } from './useValleyBundle';
import type { Dossier } from '../../types';

const st = (o: Partial<AsyncState<Dossier>>): AsyncState<Dossier> => ({ data: null, loading: false, error: false, ...o });

it('opaco: "no se encontró" con la misma fuerza (no se suaviza a error)', () => {
  render(<FundScreen symbol="ZBCUSDT" state={st({ data: { symbol: 'ZBCUSDT', equipo: [], equipo_identificado: false, presencia: {}, actividad: {}, financiacion: [], hitos: [], estado_general: 'opaco', no_encontrado_en: ['equipo','github'], generated_at: null } })} onRefresh={vi.fn()} />);
  expect(screen.getByText(/no se encontró quién está detrás/i)).toBeInTheDocument();
});

it('rastreable: cada miembro renderiza su fuente (candado anti-alucinación)', () => {
  render(<FundScreen symbol="ADAUSDT" onRefresh={vi.fn()} state={st({ data: {
    symbol: 'ADAUSDT', equipo_identificado: true, estado_general: 'rastreable', no_encontrado_en: [],
    equipo: [{ nombre: 'Charles H.', rol: 'Fundador', enlaces: [], fuente: 'https://x.com/iohk' }],
    presencia: { github: { url: 'https://github.com/input-output-hk', activo: 'si', fuente: null } },
    actividad: {}, financiacion: [], hitos: [], generated_at: '2026-06-10T00:00:00Z',
  } })} />);
  expect(screen.getByText('Charles H.')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: /fuente/i })).toHaveAttribute('href', 'https://x.com/iohk');
});

it('error → botón refrescar', () => {
  render(<FundScreen symbol="ADAUSDT" state={st({ error: true })} onRefresh={vi.fn()} />);
  expect(screen.getByText(/no se pudo averiguar/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Correr — falla.**

- [ ] **Step 3: Implementar** (port de `valles-flow.jsx:314-382` + `<Fuente>` por hecho + rama loading + frescura en opaco)

```tsx
// FundScreen.tsx
import React from 'react';
import type { Dossier } from '../../types';
import type { AsyncState } from './useValleyBundle';
import { Eyebrow, Callout, Loading, Retry } from './atoms';
import { FreshnessTag } from '../FreshnessTag';
import styles from './valles.module.css';

const CH_LABEL: Record<string, string> = {
  sitio_web: 'Sitio web', github: 'GitHub', twitter: 'Redes (X)', telegram_discord: 'Telegram', whitepaper: 'Documento técnico',
};

// §4.4 — cada hecho ancla a su fuente verificable (patrón traído de ProjectDossier).
const Fuente: React.FC<{ url: string | null }> = ({ url }) =>
  url ? <a className={styles.vwSrc} href={url} target="_blank" rel="noreferrer">fuente</a> : null;

export const FundScreen: React.FC<{ symbol: string; state: AsyncState<Dossier>; onRefresh: () => void }> = ({ symbol, state, onRefresh }) => {
  const { data, loading, error } = state;
  let answer: React.ReactNode;

  if (loading) {
    answer = <Loading label="Buscando quién está detrás…" />;
  } else if (error || !data || data.estado_general === 'no_disponible') {
    answer = (
      <Callout tone="mute" icon="×" title="No se pudo averiguar ahora" sub="Falló la búsqueda — es un problema de la herramienta, no del proyecto.">
        <Retry onClick={onRefresh} />
      </Callout>
    );
  } else if (data.estado_general === 'opaco') {
    answer = (
      <Callout tone="ochre" icon="◍" title="No se encontró quién está detrás"
        sub={<>Se buscó equipo, presencia y actividad pública, y <b>no apareció nada</b>. Eso es un dato sobre el proyecto — no es una falla de la herramienta.{data.no_encontrado_en.length > 0 && <> No se halló en: {data.no_encontrado_en.join(', ')}.</>}</>} />
    );
  } else {
    answer = (
      <div className={styles.vwAnswer}>
        <div className={styles.vwAnswerRow}>
          <div className={styles.vwAnswerIcon} aria-hidden="true">☻</div>
          <div>
            <div className={styles.vwAnswerLead}>Se sabe quién está detrás</div>
            <div className={styles.vwAnswerSay}>Hay nombres y canales públicos, y cada dato se puede comprobar en su fuente.</div>
          </div>
        </div>
        {data.equipo.length > 0 && (
          <div className={styles.vwPeople}>
            {data.equipo.map((m, i) => (
              <div className={styles.vwPerson} key={i}>
                <div className={styles.vwPersonFace} aria-hidden="true">☻</div>
                <div>
                  <div className={styles.vwPersonName}>{m.nombre}{m.rol ? ` · ${m.rol}` : ''}</div>
                  <Fuente url={m.fuente} />
                </div>
              </div>
            ))}
          </div>
        )}
        <div className={styles.vwChannels}>
          {Object.entries(data.presencia).map(([k, c]) => (
            <div className={styles.vwChannel} key={k}>
              <span className={`${styles.vwChannelDot} ${styles[`vwChannelDot_${c.activo}`]}`} aria-hidden="true" />
              <span>{CH_LABEL[k] ?? k.replace(/_/g, ' ')} · <span className={styles.vwChannelState}>{c.activo === 'si' ? 'activo' : c.activo === 'no' ? 'inactivo' : 'sin confirmar'}</span></span>
              <Fuente url={c.url ?? c.fuente} />
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className={styles.vwScreen}>
      <Eyebrow symbol={symbol} />
      <h2 className={styles.vwQuestion}>¿Quién está detrás del proyecto?</h2>
      {answer}
      {/* §5.4 — frescura exhibida también cuando hay dato (rastreable Y opaco) */}
      {data && data.frescura && data.estado_general !== 'no_disponible' && (
        <div className={styles.vwFundFresh}><FreshnessTag frescura={data.frescura} /></div>
      )}
    </div>
  );
};
```

> Dots de canal con microcopy ("activo/inactivo/sin confirmar"), no solo color (§7 M3). El dot
> "no" usa gris (ink-4 corregido), no `--clay` de marca.

- [ ] **Step 4: Correr — pasa.**
- [ ] **Step 5: Commit** — `git commit -m "feat(valles): FundScreen con Fuente por hecho (§4.4) + frescura en opaco, 4 ramas"`

---

## Fase 3 — Cierre + navegación + copiloto mock

### Task 8: `recap.ts` + `ClosingScreen` (3 columnas SEPARADAS, sin cuarta línea)

**Files:**
- Create: `frontend/src/components/valles/recap.ts`, `frontend/src/components/valles/ClosingScreen.tsx`
- Test: `frontend/src/components/valles/recap.test.ts`, `frontend/src/components/valles/ClosingScreen.test.tsx`

- [ ] **Step 1: Test de los helpers puros (falla)**

```ts
// recap.test.ts
import { describe, it, expect } from 'vitest';
import { vidaRecap, nivelesRecap, dossierRecap } from './recap';

it('vidaRecap distingue viva / muy quieta / sin dato', () => {
  expect(vidaRecap({ symbol: 'X', estado: 'ok', candidata: true } as never)).toBe('Viva y tranquila');
  expect(vidaRecap({ symbol: 'X', estado: 'ok', candidata: false } as never)).toBe('Muy quieta');
  expect(vidaRecap(null)).toBe('—');
});

it('dossierRecap: rastreable / opaco / sin dato', () => {
  expect(dossierRecap({ estado_general: 'rastreable' } as never)).toBe('Se sabe quién');
  expect(dossierRecap({ estado_general: 'opaco' } as never)).toBe('Sin rastro público');
  expect(dossierRecap(null)).toBe('—');
});
```

- [ ] **Step 2: Correr — falla.**

- [ ] **Step 3: Implementar `recap.ts`**

```ts
// recap.ts
import type { ValleyEval, SrLevels, Dossier } from '../../types';

export const vidaRecap = (v: ValleyEval | null): string =>
  !v ? '—' : v.candidata === false ? 'Muy quieta' : v.estado === 'no_disponible' ? '—' : 'Viva y tranquila';

export const nivelesRecap = (n: SrLevels | null): string => {
  if (!n || n.estado === 'no_disponible') return '—';
  if (n.zonas.length === 0) return 'Sin paredes claras';
  const d = n.ubicacion.dentro_de;
  return d ? (d.tipo === 'soporte' ? 'En un piso' : 'En un techo') : 'En el medio';
};

export const dossierRecap = (d: Dossier | null): string =>
  !d ? '—' : d.estado_general === 'rastreable' ? 'Se sabe quién' : d.estado_general === 'opaco' ? 'Sin rastro público' : '—';
```

- [ ] **Step 4: Test de `ClosingScreen` — verifica el gate anti-veredicto (falla primero)**

```tsx
// ClosingScreen.test.tsx
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ClosingScreen } from './ClosingScreen';

const bundle = {
  vida: { data: { symbol: 'ADAUSDT', estado: 'ok', candidata: true }, loading: false, error: false },
  niveles: { data: { symbol: 'ADAUSDT', estado: 'ok', zonas: [], ubicacion: { dentro_de: null, techo: null, piso: null }, price_live: 1, generated_at: null }, loading: false, error: false },
  dossier: { data: { estado_general: 'opaco' }, loading: false, error: false },
} as never;

it('no escribe la cuarta línea: textContent sin compra/buena/score/recomendación/veredicto', () => {
  const { container } = render(<ClosingScreen symbol="ADAUSDT" bundle={bundle} onAsk={vi.fn()} onRestart={vi.fn()} />);
  expect(container.textContent ?? '').not.toMatch(/compra|buena|score|recomend|veredicto|potencial/i);
});

it('muestra las 3 columnas por separado', () => {
  render(<ClosingScreen symbol="ADAUSDT" bundle={bundle} onAsk={vi.fn()} onRestart={vi.fn()} />);
  expect(screen.getByText('Viva y tranquila')).toBeInTheDocument();
  expect(screen.getByText('Sin paredes claras')).toBeInTheDocument();
  expect(screen.getByText('Sin rastro público')).toBeInTheDocument();
});
```

- [ ] **Step 5: Implementar `ClosingScreen`** (port `valles-flow.jsx:387-414`, tuteo, 3 columnas)

```tsx
// ClosingScreen.tsx
import React from 'react';
import type { ValleyBundle } from './useValleyBundle';
import { humanName } from './atoms';
import { vidaRecap, nivelesRecap, dossierRecap } from './recap';
import styles from './valles.module.css';

export const ClosingScreen: React.FC<{ symbol: string; bundle: ValleyBundle; onAsk: () => void; onRestart: () => void }> = ({ symbol, bundle, onAsk, onRestart }) => (
  <div className={styles.vwScreen}>
    <div className={styles.vwCloseIcon} aria-hidden="true">⌖</div>
    <h2 className={styles.vwCloseTitle}>Estas tres cosas son hechos. La decisión es tuya.</h2>
    <p className={styles.vwCloseBody}>
      Valles te mostró, por separado, si <b>{humanName(symbol)}</b> está viva, dónde está su precio
      y quién está detrás. No suma las tres en una nota ni te dice si comprar — eso lo decides tú,
      con tu propio criterio.
    </p>
    <div className={styles.vwCloseRecap}>
      <div className={styles.vwRecap}><span className={styles.vwRecapN}>1</span><span className={styles.vwRecapQ}>¿Está viva?</span><span className={styles.vwRecapA}>{vidaRecap(bundle.vida.data)}</span></div>
      <div className={styles.vwRecap}><span className={styles.vwRecapN}>2</span><span className={styles.vwRecapQ}>¿Dónde está el precio?</span><span className={styles.vwRecapA}>{nivelesRecap(bundle.niveles.data)}</span></div>
      <div className={styles.vwRecap}><span className={styles.vwRecapN}>3</span><span className={styles.vwRecapQ}>¿Quién está detrás?</span><span className={styles.vwRecapA}>{dossierRecap(bundle.dossier.data)}</span></div>
    </div>
    <div className={styles.vwCloseActions}>
      <button className={`${styles.vwBtn} ${styles.vwBtnOutline}`} onClick={onAsk}>◈ Preguntarle al copiloto</button>
      <button className={`${styles.vwBtn} ${styles.vwBtnGhost}`} onClick={onRestart}>Mirar otra moneda</button>
    </div>
  </div>
);
```

- [ ] **Step 6: Correr ambos test — pasan.**
- [ ] **Step 7: Commit** — `git commit -m "feat(valles): ClosingScreen 3 columnas separadas + gate anti-veredicto (F3b)"`

---

### Task 9: `Copilot` mock (rechazo ampliado §5.6)

**Files:**
- Create: `frontend/src/components/valles/Copilot.tsx`
- Test: `frontend/src/components/valles/Copilot.test.tsx`

- [ ] **Step 1: Test que falla — el rechazo cubre las frases que hoy se escapan**

```tsx
// Copilot.test.tsx
import { describe, it, expect } from 'vitest';
import { canned } from './Copilot';

it.each(['¿en cuál entro?', '¿vale la pena ADA?', '¿qué harías tú?', 'should I buy', '¿cuál compro?', '¿cuánto pongo?'])(
  'rechaza intent de decisión/sizing: "%s"', (q) => {
    expect(canned(q).refusal).toBe(true);
  });

it('responde con hecho (sin refusal) a "¿qué es en valle?"', () => {
  expect(canned('¿qué es en valle?').refusal).toBeFalsy();
});
```

- [ ] **Step 2: Correr — falla.**

- [ ] **Step 3: Implementar** (port de `valles-flow.jsx:419-464`, `canned` exportada y testeable, keywords ampliadas, tuteo)

```tsx
// Copilot.tsx
import React, { useEffect, useRef, useState } from 'react';
import styles from './valles.module.css';

export interface CannedReply { refusal?: boolean; tag: string; html: React.ReactNode; }

const DECISION = /(en cu[aá]l|cu[aá]l (compr|entr|conv)|vale la pena|qu[eé] har[ií]as|should i (buy|enter)|recomiend|me conviene)/i;
const SIZING   = /(cu[aá]nto|pongo|apost|tama[ñn]o|how much)/i;
const VERDICT  = /(compr|conviene|mejor|vend|buena|mala)/i;

export function canned(qRaw: string): CannedReply {
  const q = (qRaw || '').toLowerCase();
  if (SIZING.test(q)) return { refusal: true, tag: 'no te dice cuánto', html: <>El tamaño lo decides tú, a propósito. Valles no te dice cuánto poner — solo te muestra los hechos para que decidas con tu criterio.</> };
  if (DECISION.test(q) || VERDICT.test(q)) return { refusal: true, tag: 'no decide', html: <>No te digo si comprar ni cuál es “la mejor”. No existe un puntaje de calidad: la herramienta muestra hechos y el veredicto es tuyo.</> };
  if (q.includes('valle') || q.includes('quiet') || q.includes('viva')) return { tag: 'fact', html: <>“En valle” quiere decir que la moneda <b>se mueve poco</b>, dentro de una franja angosta, durante varias semanas. Es una descripción del gráfico, no un consejo.</> };
  if (q.includes('viej') || q.includes('fresc') || q.includes('ranci') || q.includes('actual')) return { tag: 'fact', html: <>Cada lectura te dice su edad. Si algo es de hace varios días te aviso que pudo cambiar.</> };
  return { tag: 'fact', html: <>Te leo hechos: si está viva, dónde está el precio respecto a sus paredes, y quién está detrás con su fuente. Pregúntame por cualquiera.</> };
}

const SUGG = ['¿Qué quiere decir “en valle”?', '¿Está vieja la info?', '¿Cuál conviene comprar?', '¿Cuánto pongo?'];
type Msg = { role: 'user' | 'assistant'; html: React.ReactNode; tag?: string; refusal?: boolean };

export const Copilot: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const [msgs, setMsgs] = useState<Msg[]>([{ role: 'assistant', tag: 'fact', html: <>Te leo los hechos de las tres lentes. No predigo, no rankeo, y no te digo cuánto poner.</> }]);
  const [input, setInput] = useState('');
  const scroll = useRef<HTMLDivElement>(null);
  useEffect(() => { if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; }, [msgs]);
  const send = (t: string) => { const s = t.trim(); if (!s) return; setMsgs((p) => [...p, { role: 'user', html: s }, { role: 'assistant', ...canned(s) }]); setInput(''); };
  return (
    <>
      <div className={styles.vwScrim} onClick={onClose} />
      <aside className={styles.vwDock} role="dialog" aria-label="Copiloto de Valles">
        <header className={styles.vwDockHd}>
          <div className={styles.vwDockAvatar} aria-hidden="true">◈</div>
          <div><div className={styles.vwDockName}>Copiloto · Valles</div><div className={styles.vwDockSub}>exhibe los hechos · no decide</div></div>
          <button className={styles.vwDockClose} onClick={onClose} aria-label="Cerrar">×</button>
        </header>
        <div className={styles.vwDockScroll} ref={scroll}>
          {msgs.map((m, i) => (
            <div className={`${styles.vwMsg} ${m.role === 'user' ? styles.vwMsgUser : ''}`} key={i}>
              {m.role === 'assistant' && m.tag && <span className={`${styles.vwTag} ${m.tag === 'fact' ? styles.vwTagFact : ''}`}>{m.tag}</span>}
              <div className={`${styles.vwBubble} ${m.refusal ? styles.vwBubbleRefusal : ''}`}>{m.html}</div>
            </div>
          ))}
        </div>
        <div className={styles.vwDockSugg}>{SUGG.map((s, i) => <button key={i} className={styles.vwSugg} onClick={() => send(s)}>{s}</button>)}</div>
        <form className={styles.vwDockInput} onSubmit={(e) => { e.preventDefault(); send(input); }}>
          <input placeholder="pregunta en tus palabras…" value={input} onChange={(e) => setInput(e.target.value)} />
          <button className={styles.vwDockSend} type="submit" disabled={!input.trim()} aria-label="Enviar">↑</button>
        </form>
      </aside>
    </>
  );
};
```

- [ ] **Step 4: Correr — pasa.**
- [ ] **Step 5: Commit** — `git commit -m "feat(valles): Copilot mock con rechazo ampliado (decisión/sizing/veredicto), tuteo"`

---

### Task 10: `ValleysFlow` — orquestador (stepper "Paso X de Y", nav, persistencia, tagline §4.1)

**Files:**
- Create: `frontend/src/components/valles/ValleysFlow.tsx`
- Test: `frontend/src/components/valles/ValleysFlow.test.tsx`

- [ ] **Step 1: Test que falla**

```tsx
// ValleysFlow.test.tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ValleysFlow } from './ValleysFlow';
import * as api from '../../api';
import type { ValleySnapshot } from '../../types';

vi.mock('../../api');
beforeEach(() => {
  localStorage.clear();
  vi.mocked(api.getValleyEval).mockResolvedValue({ symbol: 'ADAUSDT', estado: 'ok', candidata: true, pct_rango: 0.1, semanas_consolidando: 5, vol_percentil: 0.2 } as never);
  vi.mocked(api.getLevels).mockResolvedValue({ symbol: 'ADAUSDT', estado: 'ok', generated_at: null, price_live: 1, zonas: [], ubicacion: { dentro_de: null, techo: null, piso: null } } as never);
  vi.mocked(api.getDossier).mockResolvedValue({ symbol: 'ADAUSDT', equipo: [], equipo_identificado: false, presencia: {}, actividad: {}, financiacion: [], hitos: [], estado_general: 'opaco', no_encontrado_en: [], generated_at: null } as never);
});

const snap: ValleySnapshot = {
  generated_at: '2026-06-14T10:00:00Z', coverage: { universe: 10, evaluated: 10, complete: true },
  candidates: [{ symbol: 'ADAUSDT', price: 0.45, pct_rango: 0.12, semanas_consolidando: 6, vol_percentil: 0.2, volumen_usd_dia: 1e7, distancia_ath_pct: 0.7, razones_vida: [] }],
  frescura: { estado: 'fresco', edad_seg: 1800, generated_at: '2026-06-14T10:00:00Z', umbral_seg: 43200 },
};

it('el tagline NO emite veredicto de operabilidad (§4.1)', () => {
  const { container } = render(<ValleysFlow snapshot={snap} loading={false} />);
  expect(container.textContent ?? '').not.toMatch(/d[oó]nde operar/i);
});

it('al elegir una moneda avanza a Vida y el stepper dice "Paso 1 de 3"', async () => {
  render(<ValleysFlow snapshot={snap} loading={false} />);
  await userEvent.click(screen.getByText('Cardano'));
  expect(await screen.findByText('¿Está viva la moneda?')).toBeInTheDocument();
  expect(screen.getByText(/paso 1 de 3/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Correr — falla.**

- [ ] **Step 3: Implementar el orquestador** (port de `valles-flow.jsx:466-553`, tagline §4.1, "Paso X de Y", labels siempre visibles, keydown solo montado)

```tsx
// ValleysFlow.tsx
import React, { useCallback, useEffect, useState } from 'react';
import type { ValleySnapshot } from '../../types';
import { useValleyBundle } from './useValleyBundle';
import { PickScreen } from './PickScreen';
import { VidaScreen } from './VidaScreen';
import { NivelesScreen } from './NivelesScreen';
import { FundScreen } from './FundScreen';
import { ClosingScreen } from './ClosingScreen';
import { Copilot } from './Copilot';
import styles from './valles.module.css';

const STEPS = ['pick', 'vida', 'niveles', 'fund', 'cierre'] as const;
type Step = typeof STEPS[number];
const LENS: { key: Step; label: string }[] = [
  { key: 'vida', label: 'Vida' }, { key: 'niveles', label: 'Niveles' }, { key: 'fund', label: 'Quién' },
];

export const ValleysFlow: React.FC<{ snapshot: ValleySnapshot; loading: boolean }> = ({ snapshot, loading }) => {
  const [sym, setSym] = useState<string>(() => localStorage.getItem('vw_sym') ?? '');
  const [step, setStep] = useState<number>(() => Number(localStorage.getItem('vw_step') ?? 0));
  const [dock, setDock] = useState(false);

  useEffect(() => { localStorage.setItem('vw_step', String(step)); }, [step]);
  useEffect(() => { if (sym) localStorage.setItem('vw_sym', sym); }, [sym]);

  const bundle = useValleyBundle(sym);
  const go = useCallback((n: number | ((s: number) => number)) =>
    setStep((s) => Math.max(0, Math.min(STEPS.length - 1, typeof n === 'function' ? n(s) : n))), []);

  // keydown SOLO mientras este componente está montado (se limpia al desmontar la tab)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (dock) return;
      if (e.key === 'ArrowRight' && step > 0 && step < STEPS.length - 1) go((s) => s + 1);
      if (e.key === 'ArrowLeft' && step > 1) go((s) => s - 1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [step, dock, go]);

  const pick = (s: string) => { setSym(s); go(1); };
  const restart = () => { setSym(''); go(0); localStorage.removeItem('vw_sym'); };

  const cur: Step = STEPS[step];
  let screen: React.ReactNode;
  if (cur === 'pick' || !sym) screen = <PickScreen snapshot={snapshot} onPick={pick} />;
  else if (cur === 'vida') screen = <VidaScreen symbol={sym} state={bundle.vida} frescura={snapshot.frescura} />;
  else if (cur === 'niveles') screen = <NivelesScreen symbol={sym} state={bundle.niveles} />;
  else if (cur === 'fund') screen = <FundScreen symbol={sym} state={bundle.dossier} onRefresh={bundle.refreshDossier} />;
  else screen = <ClosingScreen symbol={sym} bundle={bundle} onAsk={() => setDock(true)} onRestart={restart} />;

  const lensIdx = cur === 'cierre' ? 3 : ({ vida: 0, niveles: 1, fund: 2 } as Record<string, number>)[cur];

  return (
    <div className={styles.vwRoot}>
      <div className={styles.vw}>
        <div className={styles.vwTop}>
          <div className={styles.vwBrand}>
            <span className={styles.vwBrandMark} aria-hidden="true">V</span>
            <span className={styles.vwBrandName}>Valles</span>
            {/* §4.1 — promesa de solo-hechos, no veredicto de operabilidad */}
            <span className={styles.vwBrandTag}>los hechos, lente por lente — la decisión es tuya</span>
          </div>
          {step > 0 && (
            <div className={styles.vwSteps} role="group" aria-label={`Paso ${lensIdx + 1} de ${LENS.length}`}>
              <span className={styles.vwStepCount}>Paso {Math.min(lensIdx + 1, LENS.length)} de {LENS.length}</span>
              {LENS.map((l, i) => (
                <React.Fragment key={l.key}>
                  {i > 0 && <span className={styles.vwStepSep} aria-hidden="true" />}
                  <button
                    className={`${styles.vwStep} ${lensIdx === i ? styles.vwStepActive : ''} ${lensIdx > i ? styles.vwStepDone : ''}`}
                    onClick={() => sym && go(i + 1)} disabled={!sym}
                    aria-current={lensIdx === i ? 'step' : undefined}
                  >
                    <span className={styles.vwStepN} aria-hidden="true">{lensIdx > i ? '✓' : i + 1}</span>
                    <span className={styles.vwStepLabel}>{l.label}</span>
                  </button>
                </React.Fragment>
              ))}
            </div>
          )}
        </div>

        <div className={styles.vwStage}>
          {loading && cur === 'pick' ? <div className={styles.vwScreen}><p className={styles.vwPickLead}>Cargando la foto…</p></div>
            : React.isValidElement(screen) ? React.cloneElement(screen, { key: cur + sym }) : screen}
        </div>

        {step > 0 && cur !== 'pick' && (
          <div className={styles.vwNav}>
            <div className={styles.vwNavInner}>
              <button className={`${styles.vwBtn} ${styles.vwBtnGhost}`} onClick={() => go((s) => s - 1)} disabled={step <= 1}>← Atrás</button>
              {cur !== 'cierre'
                ? <button className={`${styles.vwBtn} ${styles.vwBtnPrimary}`} onClick={() => go((s) => s + 1)}>{cur === 'fund' ? 'Cerrar el recorrido' : 'Siguiente'} →</button>
                : <button className={`${styles.vwBtn} ${styles.vwBtnPrimary}`} onClick={restart}>Mirar otra moneda →</button>}
            </div>
          </div>
        )}

        {!dock && step >= 1 && <button className={styles.vwFab} onClick={() => setDock(true)} aria-label="Preguntar al copiloto">◈</button>}
        {dock && <Copilot onClose={() => setDock(false)} />}
      </div>
    </div>
  );
};
```

- [ ] **Step 4: Correr — pasa.**
- [ ] **Step 5: Commit** — `git commit -m "feat(valles): ValleysFlow orquestador (tagline solo-hechos, Paso X de Y, keydown montado)"`

---

## Fase 4 — Cableado + limpieza + a11y

### Task 11: Montar en `App.tsx` + cablear `loading` real (eliminar `loading={false}`)

**Files:**
- Modify: `frontend/src/App.tsx` (líneas 81, 165-169, 222-226, 762-764)

- [ ] **Step 1: Cambiar el import (línea 81)**

```diff
-import { ValleysView } from './components/ValleysView';
+import { ValleysFlow } from './components/valles/ValleysFlow';
```

- [ ] **Step 2: Añadir el estado de loading junto al snapshot (≈línea 165)**

```diff
 const [valleys, setValleys] = useState<ValleySnapshot>({
   generated_at: null, coverage: { universe: 0, evaluated: 0, complete: false }, candidates: [],
 });
+const [valleysLoading, setValleysLoading] = useState(false);
```

- [ ] **Step 3: Cablear loading real en el useEffect (≈línea 222-226)**

```diff
 useEffect(() => {
   if (mainTab === 'valles') {
-    getValleyCandidates().then(setValleys).catch(() => {});
+    setValleysLoading(true);
+    getValleyCandidates().then(setValleys).catch(() => {}).finally(() => setValleysLoading(false));
   }
 }, [mainTab]);
```

- [ ] **Step 4: Reemplazar el render (≈línea 762-764)**

```diff
 {mainTab === 'valles' && (
-  <ValleysView snapshot={valleys} loading={false} />
+  <ValleysFlow snapshot={valleys} loading={valleysLoading} />
 )}
```

- [ ] **Step 5: Verificar typecheck + arranque**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: PASS. (Si `tsc` se queja de `ValleysView` sin usar en otro lado, es porque aún no se eliminó — se hace en Task 14.)

- [ ] **Step 6: Commit** — `git commit -m "feat(valles): montar ValleysFlow en App, cablear loading real (quita loading={false})"`

---

### Task 12: Móvil — `valles` en `BottomNav` (D5)

**Files:**
- Modify: `frontend/src/components/BottomNav.tsx`

- [ ] **Step 1: Test que falla**

```tsx
// añadir a frontend/src/components/__tests__ o junto al componente: BottomNav.test.tsx
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import BottomNav from './BottomNav';

it('incluye el item Valles en el nav móvil', () => {
  render(<BottomNav active="mercado" counts={{ market: 0, positions: 0, killswitch: 0 }} onSelect={vi.fn()} />);
  expect(screen.getByText('Valles')).toBeInTheDocument();
});
```

- [ ] **Step 2: Correr — falla.**

- [ ] **Step 3: Añadir el item** (reusa el icono `history`, como `LeftRail`)

```diff
 const items: BNavItem[] = [
   { id: 'mercado',     label: 'Mercado',    icon: 'mercado',    count: counts.market },
   { id: 'posiciones',  label: 'Posiciones', icon: 'positions',  count: counts.positions },
   { id: 'kill-switch', label: 'Kill-sw.',   icon: 'killswitch', count: counts.killswitch },
+  { id: 'valles',      label: 'Valles',     icon: 'history' },
   { id: 'menu',        label: 'Más',        icon: 'config' },
 ];
```

- [ ] **Step 4: Correr — pasa.** Verificar responsive manual (`npm run dev`, viewport ≤768px): el recorrido es 1 columna; confirmar labels del stepper visibles, `vwBuilding` legible, dock a ancho completo, táctil ≥48px.

- [ ] **Step 5: Commit** — `git commit -m "feat(valles): valles en BottomNav (acceso móvil, D5)"`

---

### Task 13: Pasada de a11y + voz en los componentes reusados

**Files:**
- Modify: `frontend/src/components/FreshnessTag.tsx` (revisar — no tiene voseo; confirmar texto claro)

> Los voseos de `LevelsPanel`/`ProjectDossier` se van con la eliminación (Task 14); no hace falta
> corregirlos. `FreshnessTag` se conserva: su copy ("sin foto — el screener aún no ha completado un
> ciclo", "foto hace N · rancia") ya está en tuteo neutral. Verificar que el `<span>` muerto/rancio
> cruza AA dentro de `.vwRoot` (el color de la clase `.muerto`/`.rancio` de FreshnessTag.module.css
> debe ser legible sobre paper — si usa hex propios, alinearlos con `--ochre`/`--ink-2` corregidos).

- [ ] **Step 1:** Revisar `FreshnessTag.module.css`; si usa hex que reprueban AA sobre `--paper`, ajustarlos a los corregidos (§7). Correr `npx vitest run src/components/FreshnessTag.test.tsx`. Commit si hubo cambio: `git commit -m "fix(valles): FreshnessTag legible sobre tema cálido (AA)"`.

---

### Task 14: Eliminar el código muerto (vista densa, D4)

**Files:**
- Delete: `ValleysView.tsx`/`.module.css`/`.test.tsx`, `CoinCard.tsx`/`.module.css`/`.test.tsx`, `LevelsPanel.tsx`/`.module.css`/`.test.tsx`, `ProjectDossier.tsx`/`.module.css`/`.test.tsx`

- [ ] **Step 1: Confirmar que nada los importa ya** (App.tsx ya migrado en Task 11)

Run: `cd frontend && rg "ValleysView|CoinCard|LevelsPanel|ProjectDossier" src --type ts --type tsx`
Expected: solo aparecen en los propios archivos a borrar (y posibles imports cruzados entre ellos).

- [ ] **Step 2: Borrar los archivos**

```bash
cd frontend/src/components
git rm ValleysView.tsx ValleysView.module.css ValleysView.test.tsx \
       CoinCard.tsx CoinCard.module.css CoinCard.test.tsx \
       LevelsPanel.tsx LevelsPanel.module.css LevelsPanel.test.tsx \
       ProjectDossier.tsx ProjectDossier.module.css ProjectDossier.test.tsx
```

- [ ] **Step 3: Verificar build verde** (atrapa cualquier import huérfano, `noUnusedLocals`)

Run: `cd frontend && npx tsc --noEmit && npm run build && npx vitest run`
Expected: PASS, sin referencias rotas.

- [ ] **Step 4: Commit** — `git commit -m "refactor(valles): eliminar la vista densa (ValleysView/CoinCard/LevelsPanel/ProjectDossier) — D4"`

---

## Fase 5 — Cierre: copy final + verificación de doctrina

### Task 15: Pasada `solace-wren` del microcopy + gate de doctrina completo

**Files:**
- Modify: los `*.tsx` de `valles/` (solo strings de copy)
- Create: `frontend/src/components/valles/doctrine.test.tsx`

- [ ] **Step 1: Invocar la skill `solace-wren`** sobre todo el copy del recorrido (descripción sin aliento; calidez sin hype; sin AI slop). Aplicar los ajustes de texto que devuelva. (Esta es la pasada de microcopy del §8.)

- [ ] **Step 2: Test de doctrina agregado (gate §11) — falla primero si algún componente filtra veredicto**

```tsx
// doctrine.test.tsx — el textContent de cada pantalla NO emite veredicto.
import { render } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ValleysFlow } from './ValleysFlow';
import * as api from '../../api';
import type { ValleySnapshot } from '../../types';

vi.mock('../../api');
const FORBIDDEN = /compra|c[oó]mpr|buena|score|recomend|veredicto|potencial|d[oó]nde operar/i;
const snap: ValleySnapshot = {
  generated_at: '2026-06-14T10:00:00Z', coverage: { universe: 5, evaluated: 5, complete: true },
  candidates: [{ symbol: 'ADAUSDT', price: 0.45, pct_rango: 0.12, semanas_consolidando: 6, vol_percentil: 0.2, volumen_usd_dia: 1e7, distancia_ath_pct: 0.7, razones_vida: [] }],
  frescura: { estado: 'fresco', edad_seg: 1800, generated_at: '2026-06-14T10:00:00Z', umbral_seg: 43200 },
};
beforeEach(() => {
  vi.mocked(api.getValleyEval).mockResolvedValue({ symbol: 'ADAUSDT', estado: 'ok', candidata: true, pct_rango: 0.1, semanas_consolidando: 5, vol_percentil: 0.2 } as never);
  vi.mocked(api.getLevels).mockResolvedValue({ symbol: 'ADAUSDT', estado: 'ok', generated_at: null, price_live: 1, zonas: [], ubicacion: { dentro_de: null, techo: null, piso: null } } as never);
  vi.mocked(api.getDossier).mockResolvedValue({ symbol: 'ADAUSDT', equipo: [], equipo_identificado: false, presencia: {}, actividad: {}, financiacion: [], hitos: [], estado_general: 'opaco', no_encontrado_en: [], generated_at: null } as never);
});

it('el chrome del flujo (pick) no emite veredicto', () => {
  const { container } = render(<ValleysFlow snapshot={snap} loading={false} />);
  // las SUGG del copiloto incluyen "comprar" a propósito (son preguntas que se RECHAZAN),
  // así que este gate corre sobre el chrome del flujo, no sobre el dock abierto.
  expect(container.textContent ?? '').not.toMatch(FORBIDDEN);
});
```

> Nota: las sugerencias del copiloto ("¿Cuál conviene comprar?") contienen la palabra a propósito —
> son las preguntas que el mock RECHAZA. El gate de doctrina corre sobre el flujo con el dock cerrado.

- [ ] **Step 3: Correr toda la suite del frontend**

Run: `cd frontend && npx vitest run && npx tsc --noEmit && npm run build`
Expected: PASS.

- [ ] **Step 4: Verificar que el chrome dark no se repintó** (montaje real)

Run: `cd frontend && npm run dev` → navegar a otra tab (Mercado): sigue dark. Entrar a Valles: cálido, confinado. Volver a Mercado: dark intacto.

- [ ] **Step 5: Commit** — `git commit -m "feat(valles): copy final (solace-wren) + gate de doctrina sobre el flujo"`

---

## Self-Review (hecho por el autor del plan)

**1. Cobertura del spec:**
- §3.1 `useValleyBundle` (mata bundle, 4ª rama, symbol-guard, refresh) → Task 1. ✓
- §3.2 montaje App + loading real → Task 11. ✓
- §4.1 tagline → Task 10. §4.2 color de juicio → Task 2 (CSS) + Tasks 5/6 (iconos neutros). §4.3 copy continuidad → Task 6. §4.4 fuentes → Task 7. ✓
- §5.1-5.6 pantallas → Tasks 4-10. ✓
- §6 tema scopeado `.vwRoot` → Task 2. ✓
- §7 a11y (contraste/táctil/cuerpo/motion/foco) → Task 2 + atoms; FreshnessTag → Task 13. ✓
- §8 voseo→tuteo → aplicado en cada port + Task 15 (solace-wren). ✓
- §9 móvil BottomNav → Task 12. ✓
- §11 gate (anti-veredicto, estados ortogonales, sin color de juicio) → Tasks 8/15 + ramas en 5/6/7. ✓
- D4 eliminar vista densa → Task 14. ✓

**2. Placeholders:** sin "TBD/TODO". Las dos delegaciones explícitas (port verbatim de las clases de layout de `valles-warm.css` en Task 2; pasada de `solace-wren` en Task 15) tienen reglas/fuente concretas, no son huecos.

**3. Consistencia de tipos:** `AsyncState<T>` y `ValleyBundle` (Task 1) se consumen idénticos en VidaScreen/NivelesScreen/FundScreen (`state={bundle.vida|niveles|dossier}`) y ClosingScreen (`bundle={bundle}`). `canned()` exportada (Task 9) testeada en Task 9. `humanName` exportada de `atoms.tsx` se usa en PickScreen/ClosingScreen. Clases `styles.vwXxx` siguen la convención camelCase del header.

---

## Riesgos al ejecutar

- **`noUnusedLocals`/`noUnusedParameters`** rompen el BUILD (no warning): limpiar imports/aliases muertos al portar (ej. `confluencia_redondo` si no se usa).
- **Clases CSS faltantes:** si el port de `valles-warm.css` omite una clase que un screen referencia, no falla en tsc (CSS modules no se type-checkean por defecto) — se ve como estilo ausente en `npm run dev`. Verificar visualmente cada pantalla.
- **El gate de doctrina** (Task 15) corre con el dock cerrado a propósito (las SUGG del copiloto contienen "comprar" como preguntas-a-rechazar).
