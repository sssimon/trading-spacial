// Narrativa.test.tsx — TDD para el componente Narrativa (SP3 rewrite)
// Tres bloques: vida, paredes, jugada.
// Verifica headings, costura AC7 con evidencia, tuteo venezolano,
// decisión #3a (no-candidata-viva SIN número de posición),
// y empty-states sin crash.

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Narrativa } from './Narrativa';
import type { ValleyEval, SrLevels, PlanDerived } from '../../../types';

// ── fixtures ──────────────────────────────────────────────────────────────────

const vidaCandidata: ValleyEval = {
  symbol:            'ADAUSDT',
  estado:            'ok',
  candidata:         true,
  vivo:              true,
  pos_in_30d_range:  0.12,
  rsi14:             38,
  pct_vs_sma20:      -6,
  pct_vs_sma50:      -9,
  consol_30d:        40,
  vol_ratio:         0.7,
  drawdown_from_90h: -35,
  volumen_usd_dia:   820000,
};

// No candidata pero viva — decisión #3a: el backend NO emite pos_in_30d_range aquí
const vidaNoCandidataViva: ValleyEval = {
  symbol:    'INJUSDT',
  estado:    'ok',
  candidata: false,
  vivo:      true,
  // pos_in_30d_range intencionalmente omitida (no la emite el backend en esta rama)
  rsi14:     55,
};

// No candidata y NO viva
const vidaNoCandidataMuerta: ValleyEval = {
  symbol:          'INJUSDT',
  estado:          'ok',
  candidata:       false,
  vivo:            false,
  razones_muerte:  ['rsi_alto', 'encima_sma20'],
};

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
  ubicacion: {
    dentro_de: null,
    techo:     { centro: 0.448, dist_pct: 6.7 },
    piso:      { centro: 0.385, dist_pct: -8.3 },
  },
};

const plan: PlanDerived = {
  entry:      0.419,
  sl_plan:    0.382,
  sl_piso:    { centro: 0.385, precio_bajo: 0.382, precio_alto: 0.388, toques: 4 },
  entry_zone: { centro: 0.419, precio_bajo: 0.412, precio_alto: 0.426, toques: 5 },
  rungs: [
    { tp_price: 0.448, size_frac: 0.50, zona: { centro: 0.448, precio_bajo: 0.445, precio_alto: 0.451, toques: 3 } },
    { tp_price: 0.470, size_frac: 0.45, zona: { centro: 0.470, precio_bajo: 0.468, precio_alto: 0.472, toques: 2 } },
  ],
  runner_frac: 0.05,
};

// ── suite: candidata ──────────────────────────────────────────────────────────

describe('Narrativa — candidata', () => {
  it('muestra los 3 headings de bloque', () => {
    render(<Narrativa vida={vidaCandidata} levels={levels} plan={plan} />);
    expect(screen.getByRole('heading', { name: /¿Está viva\?/i })).toBeTruthy();
    expect(screen.getByRole('heading', { name: /¿Dónde está entre sus paredes\?/i })).toBeTruthy();
    expect(screen.getByRole('heading', { name: /Si decides entrar/i })).toBeTruthy();
  });

  it('muestra "parte baja de su rango"', () => {
    render(<Narrativa vida={vidaCandidata} levels={levels} plan={plan} />);
    expect(screen.getByText(/parte baja de su rango/i)).toBeTruthy();
  });

  it('costura AC7 — "no le ganó al azar" con 9.92% y 12.54%', () => {
    render(<Narrativa vida={vidaCandidata} levels={levels} plan={plan} />);
    const el = screen.getByText(/no le ganó al azar/i);
    expect(el.textContent).toMatch(/9\.92%.*12\.54%/);
  });

  it('doctrina: sin "en valle" ni "franja angosta"', () => {
    render(<Narrativa vida={vidaCandidata} levels={levels} plan={plan} />);
    expect(screen.queryByText(/en valle|franja angosta/i)).toBeNull();
  });

  it('aparece la costura del bloque jugada "la decisión es tuya"', () => {
    render(<Narrativa vida={vidaCandidata} levels={levels} plan={plan} />);
    const costuras = screen.getAllByText(/la decisión es tuya/i);
    expect(costuras.length).toBeGreaterThan(0);
  });

  it('usa tuteo venezolano — "decides" y NO "decidís"', () => {
    render(<Narrativa vida={vidaCandidata} levels={levels} plan={plan} />);
    expect(screen.getAllByText(/decides/i).length).toBeGreaterThan(0);
    expect(screen.queryByText(/decidís/i)).toBeNull();
  });

  it('muestra el techo del precio', () => {
    render(<Narrativa vida={vidaCandidata} levels={levels} plan={plan} />);
    expect(screen.getByText(/techo/i)).toBeTruthy();
  });

  it('menciona la zona de entrada de la jugada', () => {
    render(<Narrativa vida={vidaCandidata} levels={levels} plan={plan} />);
    const texto = document.body.textContent ?? '';
    expect(texto).toMatch(/0\.412/);
    expect(texto).toMatch(/0\.426/);
  });

  it('los 3 bloques tienen ids anclados correctos', () => {
    render(<Narrativa vida={vidaCandidata} levels={levels} plan={plan} />);
    expect(document.getElementById('idea-vida')).not.toBeNull();
    expect(document.getElementById('idea-paredes')).not.toBeNull();
    expect(document.getElementById('idea-jugada')).not.toBeNull();
  });
});

// ── suite: no-candidata-viva (decisión #3a) ───────────────────────────────────

describe('Narrativa — no candidata pero viva (decisión #3a)', () => {
  it('muestra "parte alta de su rango"', () => {
    render(<Narrativa vida={vidaNoCandidataViva} levels={null} plan={null} />);
    expect(screen.getByText(/parte alta de su rango/i)).toBeTruthy();
  });

  it('decisión #3a: NO muestra posición con número (el backend no la emite)', () => {
    render(<Narrativa vida={vidaNoCandidataViva} levels={null} plan={null} />);
    expect(screen.queryByText(/posición\s*\d/i)).toBeNull();
  });

  it('no usa lenguaje de franja/valle', () => {
    render(<Narrativa vida={vidaNoCandidataViva} levels={null} plan={null} />);
    expect(screen.queryByText(/en valle|franja angosta/i)).toBeNull();
  });
});

// ── suite: no-candidata-muerta ────────────────────────────────────────────────

describe('Narrativa — no candidata y muerta', () => {
  it('lista las razones de muerte', () => {
    render(<Narrativa vida={vidaNoCandidataMuerta} levels={null} plan={null} />);
    const body = document.body.textContent ?? '';
    // El mapa RAZONES_MUERTE o las claves raw deben aparecer
    expect(body).toMatch(/rsi_alto|RSI alto|encima_sma20|SMA20/i);
  });

  it('no explota con razones_muerte vacías', () => {
    const sinRazones: ValleyEval = { symbol: 'X', estado: 'ok', candidata: false, vivo: false };
    expect(() => render(<Narrativa vida={sinRazones} levels={null} plan={null} />)).not.toThrow();
  });
});

// ── suite: todo null — empty-states sin crash ─────────────────────────────────

describe('Narrativa — todo null (empty-states)', () => {
  it('no explota con vida=null levels=null plan=null', () => {
    expect(() => render(<Narrativa vida={null} levels={null} plan={null} />)).not.toThrow();
  });

  it('los 3 headings siguen presentes cuando faltan datos', () => {
    render(<Narrativa vida={null} levels={null} plan={null} />);
    expect(screen.getByRole('heading', { name: /¿Está viva\?/i })).toBeTruthy();
    expect(screen.getByRole('heading', { name: /¿Dónde está entre sus paredes\?/i })).toBeTruthy();
    expect(screen.getByRole('heading', { name: /Si decides entrar/i })).toBeTruthy();
  });

  it('muestra empty-states tranquilos, sin fabricar datos', () => {
    render(<Narrativa vida={null} levels={null} plan={null} />);
    const body = document.body.textContent ?? '';
    const fakePrices = body.match(/\$\d+/g);
    expect(fakePrices).toBeNull();
  });
});
