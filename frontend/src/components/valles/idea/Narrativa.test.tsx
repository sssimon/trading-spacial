// Narrativa.test.tsx — TDD para el componente Narrativa
// Tres bloques anclados: vida, paredes, jugada.
// Verifica headings, costura, tuteo venezolano, y empty-states sin crash.

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Narrativa } from './Narrativa';
import type { ValleyEval, SrLevels, PlanDerived } from '../../../types';

// ── fixtures ──────────────────────────────────────────────────────────────────

const vida: ValleyEval = {
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

// ── suite: con datos completos ─────────────────────────────────────────────────

describe('Narrativa — con datos', () => {
  it('muestra los 3 headings de bloque', () => {
    render(<Narrativa vida={vida} levels={levels} plan={plan} />);
    expect(screen.getByRole('heading', { name: /¿Está viva\?/i })).toBeTruthy();
    expect(screen.getByRole('heading', { name: /¿Dónde está entre sus paredes\?/i })).toBeTruthy();
    expect(screen.getByRole('heading', { name: /Si decides entrar/i })).toBeTruthy();
  });

  it('aparece la costura obligatoria "la decisión es tuya"', () => {
    render(<Narrativa vida={vida} levels={levels} plan={plan} />);
    // La costura aparece en el bloque vida (AC7) y en el bloque jugada.
    const costuras = screen.getAllByText(/la decisión es tuya/i);
    expect(costuras.length).toBeGreaterThan(0);
  });

  it('usa tuteo venezolano — "decides" y NO "decidís"', () => {
    render(<Narrativa vida={vida} levels={levels} plan={plan} />);
    // "Si decides entrar" → tuteo
    expect(screen.getAllByText(/decides/i).length).toBeGreaterThan(0);
    // Voseo NO debe aparecer
    expect(screen.queryByText(/decidís/i)).toBeNull();
  });

  it('muestra la posición en rango y la costura AC7', () => {
    render(<Narrativa vida={vida} levels={levels} plan={plan} />);
    expect(screen.getByText(/parte baja de su rango/i)).toBeTruthy();
    expect(screen.getByText(/no le ganó al azar/i)).toBeTruthy();
    expect(screen.getByText(/no le ganó al azar/i).textContent).toMatch(/9\.92%.*12\.54%/);
  });

  it('doctrina: sin "en valle" ni "franja angosta" en el bloque vida', () => {
    render(<Narrativa vida={vida} levels={levels} plan={plan} />);
    expect(screen.queryByText(/en valle|franja angosta/i)).toBeNull();
  });

  it('muestra el techo del precio', () => {
    render(<Narrativa vida={vida} levels={levels} plan={plan} />);
    // El techo a 0.448 debe mencionarse
    expect(screen.getByText(/techo/i)).toBeTruthy();
  });

  it('menciona la zona de entrada de la jugada', () => {
    render(<Narrativa vida={vida} levels={levels} plan={plan} />);
    // La zona de entrada 0.412–0.426 debe aparecer formateada
    const texto = document.body.textContent ?? '';
    expect(texto).toMatch(/0\.412/);
    expect(texto).toMatch(/0\.426/);
  });

  it('los 3 bloques tienen ids anclados correctos', () => {
    render(<Narrativa vida={vida} levels={levels} plan={plan} />);
    expect(document.getElementById('idea-vida')).not.toBeNull();
    expect(document.getElementById('idea-paredes')).not.toBeNull();
    expect(document.getElementById('idea-jugada')).not.toBeNull();
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
    // Debe haber algún texto de empty-state sin ser un número inventado
    const body = document.body.textContent ?? '';
    // No debe haber precios inventados (ningún "$" seguido de dígitos)
    const fakePrices = body.match(/\$\d+/g);
    expect(fakePrices).toBeNull();
  });
});
