# El Instrumento — Fase 3b (la tarjeta de selección) · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Componer A (vida+rango on-demand) + D.1 (niveles) + C (dossier) en una tarjeta por moneda, en el frontend, con costuras visibles y cero veredicto compuesto.

**Architecture:** Un endpoint backend mínimo `GET /valley-eval/{symbol}` (reusa el fetch de D.1 + `evaluate_symbol` puro) y una tarjeta React `CoinCard` que llama los tres endpoints en paralelo, cada bloque independiente (dossier lazy), sin score ni badge agregado. Reutiliza `LevelsPanel` y `ProjectDossier`.

**Tech Stack:** FastAPI + pytest (backend); React + TypeScript + Vitest (frontend).

**Spec:** `docs/superpowers/specs/es/2026-06-13-instrumento-fase3b-tarjeta-design.md`.

**Branch:** `feat/instrumento-fase3b-tarjeta` (ya creada).

**Restricción central:** la tarjeta EXHIBE, no firma — cero veredicto compuesto (test anti-veredicto). Read-only, red fuera de tx, sin caché.

---

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `api/valleys.py` (modificar) | Añadir `GET /valley-eval/{symbol}` (A on-demand). Router ya registrado. |
| `frontend/src/types.ts` (modificar) | Tipo `ValleyEval`. |
| `frontend/src/api.ts` (modificar) | Cliente `getValleyEval`. |
| `frontend/src/components/CoinCard.tsx` (crear) + `.module.css` | La tarjeta compuesta (3 bloques, dossier lazy, anti-veredicto). |
| `frontend/src/components/ValleysView.tsx` (modificar) | Input de símbolo → abre la `CoinCard`. |
| `tests/test_valley_eval_api.py`, `CoinCard.test.tsx` (crear) | Tests. |

---

### Task 1: `GET /valley-eval/{symbol}` — A on-demand

**Files:**
- Modify: `api/valleys.py`
- Test: `tests/test_valley_eval_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_valley_eval_api.py
"""Tests del endpoint A on-demand GET /valley-eval/{symbol} (instrumento F3b). Spec §2."""
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.valleys import router
from api.levels import BinanceUnavailable


def _app():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_candidata_true_devuelve_hechos():
    cand = {"symbol": "ADAUSDT", "price": 0.42, "pct_rango": 0.18,
            "semanas_consolidando": 9, "vol_percentil": 0.22,
            "volumen_usd_dia": 3_000_000, "distancia_ath_pct": 0.86, "razones_vida": []}
    with patch("api.valleys._fetch_daily_bars", return_value=[{}] * 130), \
         patch("api.valleys.evaluate_symbol", return_value=cand):
        r = _app().get("/valley-eval/ADAUSDT")
    body = r.json()
    assert body["estado"] == "ok" and body["candidata"] is True
    assert body["pct_rango"] == 0.18


def test_no_candidata_reporta_razones():
    with patch("api.valleys._fetch_daily_bars", return_value=[{}] * 130), \
         patch("api.valleys.evaluate_symbol", return_value=None), \
         patch("api.valleys.classify_liveness", return_value=(False, ["volumen_bajo_piso"])):
        r = _app().get("/valley-eval/XYZUSDT")
    body = r.json()
    assert body["candidata"] is False
    assert body["vivo"] is False
    assert body["razones_muerte"] == ["volumen_bajo_piso"]


def test_red_caida_es_no_disponible_sin_500():
    with patch("api.valleys._fetch_daily_bars", side_effect=BinanceUnavailable("klines HTTP 503")):
        r = _app().get("/valley-eval/ADAUSDT")
    assert r.status_code == 200 and r.json()["estado"] == "no_disponible"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_valley_eval_api.py -v`
Expected: FAIL (la ruta `/valley-eval/{symbol}` no existe → 404)

- [ ] **Step 3: Write minimal implementation**

En `api/valleys.py`, añadir los imports al tope (tras los existentes):

```python
import requests

from api.levels import _fetch_daily_bars, BinanceUnavailable
from screener.valley_filter import evaluate_symbol, classify_liveness
```

Y el endpoint (tras `get_valley_candidates`):

```python
@router.get("/valley-eval/{symbol}", summary="Evalúa vida + rango de UNA moneda (A on-demand)")
def get_valley_eval(symbol: str) -> dict:
    """A para un símbolo arbitrario: fetch de velas (reusa D.1) + evaluate_symbol
    (puro). Devuelve los hechos si está VIVA y EN RANGO; si no, reporta POR QUÉ
    (razones de liveness — hechos, no juicio de atractivo). no_disponible si la
    red falla. Read-only, red fuera de tx, sin caché. Spec §2."""
    symbol = symbol.upper()[:20]
    try:
        bars = _fetch_daily_bars(symbol)
    except (requests.RequestException, BinanceUnavailable) as e:
        log.warning("VALLEY_EVAL_NO_DISPONIBLE symbol=%s causa=%s", symbol, e)
        return {"symbol": symbol, "estado": "no_disponible"}
    cand = evaluate_symbol(symbol, bars)
    if cand is None:
        vivo, razones = classify_liveness(bars)
        return {"symbol": symbol, "estado": "ok", "candidata": False,
                "vivo": vivo, "razones_muerte": razones}
    return {"symbol": symbol, "estado": "ok", "candidata": True, **cand}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_valley_eval_api.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add api/valleys.py tests/test_valley_eval_api.py
git commit -m "feat(api): GET /valley-eval/{symbol} — A on-demand (hechos de vida/rango, no_disponible sin 500)"
```

---

### Task 2: frontend — tipo `ValleyEval` + cliente `getValleyEval`

**Files:**
- Modify: `frontend/src/types.ts`, `frontend/src/api.ts`
- Test: `frontend/src/api.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/api.test.ts — añadir
import { describe, it, expect, vi, afterEach } from 'vitest';
import { getValleyEval } from './api';

describe('getValleyEval', () => {
  afterEach(() => vi.restoreAllMocks());
  it('pide GET /valley-eval/:symbol', async () => {
    const payload = { symbol: 'ADAUSDT', estado: 'ok', candidata: true, pct_rango: 0.18 };
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    const res = await getValleyEval('ADAUSDT');
    expect(res.candidata).toBe(true);
    expect(spy.mock.calls[0][0]).toContain('/valley-eval/ADAUSDT');
  });
});
```

> Si `api.test.ts` ya usa otro patrón de mock, seguilo; lo esencial es verificar la URL `/valley-eval/{symbol}`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/api.test.ts`
Expected: FAIL (`getValleyEval` no exportado)

- [ ] **Step 3: Write minimal implementation**

En `frontend/src/types.ts`, al final:

```typescript
// ---- F3b: A on-demand (vida + rango de una moneda). Spec §2 ----
export interface ValleyEval {
  symbol:                string;
  estado:                'ok' | 'no_disponible';
  candidata?:            boolean;
  vivo?:                 boolean;
  razones_muerte?:       string[];
  // presentes solo si candidata=true (los hechos de A):
  price?:                number;
  pct_rango?:            number;
  semanas_consolidando?: number;
  vol_percentil?:        number;
  volumen_usd_dia?:      number;
  distancia_ath_pct?:    number;
  razones_vida?:         string[];
}
```

En `frontend/src/api.ts`, al final:

```typescript
// ---- F3b A on-demand — GET /valley-eval/:symbol ----
export function getValleyEval(symbol: string) {
  return request<import('./types').ValleyEval>(`/valley-eval/${symbol}`);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/api.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts frontend/src/api.test.ts
git commit -m "feat(fe): tipo ValleyEval + cliente getValleyEval"
```

---

### Task 3: `CoinCard` — la tarjeta compuesta (costuras visibles)

**Files:**
- Create: `frontend/src/components/CoinCard.tsx`, `frontend/src/components/CoinCard.module.css`
- Test: `frontend/src/components/CoinCard.test.tsx`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/components/CoinCard.test.tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi, afterEach } from 'vitest';
import { CoinCard } from './CoinCard';
import * as api from '../api';

afterEach(() => vi.restoreAllMocks());

const _levels = { symbol: 'SOLUSDT', estado: 'ok', generated_at: '2026-06-13T00:00:00+00:00',
  price_live: 142, zonas: [], ubicacion: { dentro_de: null, techo: null, piso: null } };
const _dossier = { symbol: 'SOLUSDT', estado_general: 'rastreable', equipo_identificado: true,
  equipo: [], presencia: {}, actividad: {}, financiacion: [], hitos: [], no_encontrado_en: [],
  generated_at: '2026-06-13T00:00:00+00:00' };
const _vidaOk = { symbol: 'SOLUSDT', estado: 'ok', candidata: true, pct_rango: 0.18,
  semanas_consolidando: 9, volumen_usd_dia: 3_000_000 };

function _mockAll(vida: unknown) {
  vi.spyOn(api, 'getValleyEval').mockResolvedValue(vida as never);
  vi.spyOn(api, 'getLevels').mockResolvedValue(_levels as never);
  vi.spyOn(api, 'getDossier').mockResolvedValue(_dossier as never);
}

describe('CoinCard', () => {
  it('pinta los tres bloques (Vida, Niveles, Fundamentales)', async () => {
    _mockAll(_vidaOk);
    render(<CoinCard symbol="SOLUSDT" />);
    expect(await screen.findByText(/Vida/)).toBeInTheDocument();
    expect(screen.getByText(/Niveles/)).toBeInTheDocument();
    expect(screen.getByText(/Fundamentales/)).toBeInTheDocument();
  });

  it('un bloque no_disponible degrada solo (los otros siguen)', async () => {
    _mockAll({ symbol: 'SOLUSDT', estado: 'no_disponible' });
    render(<CoinCard symbol="SOLUSDT" />);
    expect(await screen.findByText(/Niveles/)).toBeInTheDocument();   // los otros bloques siguen
    expect(screen.getByText(/Sin datos/)).toBeInTheDocument();        // el bloque vida caído
  });

  it('no emite veredicto compuesto (anti-veredicto)', async () => {
    _mockAll(_vidaOk);
    const { container } = render(<CoinCard symbol="SOLUSDT" />);
    await screen.findByText(/Vida/);
    expect(/comprá|comprar|buena|score|recomend|veredicto|potencial/i
      .test(container.textContent ?? '')).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/CoinCard.test.tsx`
Expected: FAIL (`CoinCard` no existe)

- [ ] **Step 3: Write minimal implementation**

```typescript
// frontend/src/components/CoinCard.tsx
// ============================================================
// CoinCard — la tarjeta de selección compuesta (F3b).
// Tres bloques YUXTAPUESTOS (A vida · D.1 niveles · C dossier),
// cada uno con su propio estado/frescura. La tarjeta EXHIBE, no
// firma: cero veredicto compuesto, cero score. Spec §3.
// ============================================================
import React, { useEffect, useState } from 'react';
import type { ValleyEval, SrLevels, Dossier } from '../types';
import { getValleyEval, getLevels, getDossier } from '../api';
import { LevelsPanel } from './LevelsPanel';
import { ProjectDossier } from './ProjectDossier';
import styles from './CoinCard.module.css';

const VidaBlock: React.FC<{ ev: ValleyEval | null; loading: boolean }> = ({ ev, loading }) => {
  if (loading) return <div className={styles.empty}>Evaluando…</div>;
  if (!ev || ev.estado === 'no_disponible') return <div className={styles.empty}>Sin datos</div>;
  if (ev.candidata) {
    return (
      <div className={styles.row}>
        viva · en rango {((ev.pct_rango ?? 0) * 100).toFixed(0)}% ·{' '}
        {ev.semanas_consolidando} semanas ·{' '}
        ${Math.round(ev.volumen_usd_dia ?? 0).toLocaleString('en-US')}/día
      </div>
    );
  }
  return (
    <div className={styles.row}>
      {ev.vivo ? 'viva, fuera de rango' : 'no candidata'}
      {ev.razones_muerte && ev.razones_muerte.length > 0
        ? ` — ${ev.razones_muerte.join(', ')}` : ''}
    </div>
  );
};

export const CoinCard: React.FC<{ symbol: string }> = ({ symbol }) => {
  const [vida, setVida] = useState<ValleyEval | null>(null);
  const [vidaLoading, setVidaLoading] = useState(true);
  const [levels, setLevels] = useState<SrLevels | null>(null);
  const [levelsLoading, setLevelsLoading] = useState(true);
  const [dossier, setDossier] = useState<Dossier | null>(null);
  const [dossierLoading, setDossierLoading] = useState(true);

  useEffect(() => {
    setVida(null); setVidaLoading(true);
    setLevels(null); setLevelsLoading(true);
    setDossier(null); setDossierLoading(true);
    // Tres llamadas INDEPENDIENTES en paralelo; cada bloque pinta cuando llega.
    getValleyEval(symbol).then(setVida).finally(() => setVidaLoading(false));
    getLevels(symbol).then(setLevels).finally(() => setLevelsLoading(false));
    getDossier(symbol).then(setDossier).finally(() => setDossierLoading(false));  // lazy: Exa lento
  }, [symbol]);

  return (
    <div className={styles.card}>
      <header className={styles.head}>{symbol.replace('USDT', '')}</header>

      <section className={styles.sec}>
        <h4>Vida</h4>
        <VidaBlock ev={vida} loading={vidaLoading} />
      </section>

      <section className={styles.sec}>
        <h4>Niveles</h4>
        {(levels || levelsLoading)
          ? <LevelsPanel levels={levels!} loading={levelsLoading} />
          : <div className={styles.empty}>Sin datos</div>}
      </section>

      <section className={styles.sec}>
        <h4>Fundamentales</h4>
        {(dossier || dossierLoading)
          ? <ProjectDossier dossier={dossier!} loading={dossierLoading} />
          : <div className={styles.empty}>Sin datos</div>}
      </section>
    </div>
  );
};
```

```css
/* frontend/src/components/CoinCard.module.css */
.card { border: 1px solid var(--border, #2a2a2a); border-radius: 8px; padding: 12px; margin-top: 12px; }
.head { font-weight: 600; font-size: 1.1em; margin-bottom: 8px; }
/* secciones VISUALMENTE DISTINTAS — las costuras son la honestidad (sin fusión) */
.sec { border-top: 1px solid var(--border, #2a2a2a); padding: 8px 0; }
.sec h4 { margin: 0 0 4px; font-size: 0.85em; opacity: 0.7; text-transform: uppercase; letter-spacing: 0.04em; }
.row { font-variant-numeric: tabular-nums; }
.empty { opacity: 0.7; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/CoinCard.test.tsx`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CoinCard.tsx frontend/src/components/CoinCard.module.css frontend/src/components/CoinCard.test.tsx
git commit -m "feat(fe): CoinCard — tarjeta compuesta A+C+D.1 (costuras visibles, anti-veredicto)"
```

---

### Task 4: input de símbolo en `ValleysView` → abre la tarjeta

**Files:**
- Modify: `frontend/src/components/ValleysView.tsx`, `frontend/src/components/ValleysView.test.tsx`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/components/ValleysView.test.tsx — añadir dentro del describe
it('ofrece un input de símbolo para abrir la tarjeta', () => {
  render(<ValleysView snapshot={snap} loading={false} />);
  expect(screen.getByPlaceholderText(/símbolo/i)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /ver tarjeta/i })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/ValleysView.test.tsx`
Expected: FAIL (no hay input de símbolo)

- [ ] **Step 3: Write minimal implementation**

En `frontend/src/components/ValleysView.tsx`:
- Añadir el import: `import { CoinCard } from './CoinCard';`
- Añadir estado tras los `useState` existentes:
```typescript
  const [cardInput, setCardInput] = useState('');
  const [cardSymbol, setCardSymbol] = useState('');
```
- Dentro del `<div className={styles.wrap}>`, ANTES de la tabla, añadir el buscador + la tarjeta:
```tsx
      <form
        className={styles.buscador}
        onSubmit={(e) => { e.preventDefault(); setCardSymbol(cardInput.trim().toUpperCase()); }}
      >
        <input
          value={cardInput}
          onChange={(e) => setCardInput(e.target.value)}
          placeholder="Símbolo (ej. SOLUSDT)"
        />
        <button type="submit">Ver tarjeta</button>
      </form>
      {cardSymbol && <CoinCard symbol={cardSymbol} />}
```

(`cardSymbol` arranca vacío → la `CoinCard` NO se monta hasta que el operador busca, así que no dispara fetches en el render inicial de los tests existentes.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/ValleysView.test.tsx`
Expected: PASS (los existentes + el nuevo; el test "no badge de compra ni señal" sigue pasando — "Ver tarjeta"/"Símbolo" no matchean su regex)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ValleysView.tsx frontend/src/components/ValleysView.test.tsx
git commit -m "feat(fe): input de símbolo en la Vista Valles → abre la CoinCard"
```

---

## Verificación final

- [ ] **Backend:** `python -m pytest tests/test_valley_eval_api.py -v` → 3 verde.
- [ ] **Gate rápido (CI):** `python -m pytest tests/ -m "not network" -n auto -q` → sin regresiones.
- [ ] **Frontend:** `cd frontend && npm test` → todo verde (incl. `CoinCard`, `getValleyEval`, el input de `ValleysView`).
- [ ] **Anti-veredicto:** confirmar que `CoinCard.test.tsx` pasa — la tarjeta no contiene texto de compra/score/recomendación.
- [ ] **Humo manual (opcional, red):** con el API corriendo, teclear un símbolo en la Vista Valles → ver los tres bloques (Vida/Niveles/Fundamentales) pintar, el dossier llegando después.
