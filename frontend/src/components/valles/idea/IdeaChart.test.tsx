// IdeaChart.test.tsx — smoke tests del gráfico unificado idea.
// Mockea lightweight-charts (sin canvas real) y getOhlcv (sin red).
// Verifica leyenda clicable (3 entradas) y toggle de capas.

import { describe, it, expect, vi, beforeAll } from 'vitest';
import { render, fireEvent, screen } from '@testing-library/react';

// jsdom no implementa ResizeObserver — mock global mínimo
beforeAll(() => {
  if (typeof globalThis.ResizeObserver === 'undefined') {
    globalThis.ResizeObserver = class ResizeObserver {
      observe()    {}
      unobserve()  {}
      disconnect() {}
    };
  }
});

// Mock de lightweight-charts: serie con priceToCoordinate trivial
vi.mock('lightweight-charts', () => ({
  createChart: () => ({
    addCandlestickSeries: () => ({
      setData:              vi.fn(),
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

// Mock de api: sin red
vi.mock('../../../api', () => ({
  getOhlcv: async () => ({ candles: [], volumes: [] }),
}));

import { IdeaChart } from './IdeaChart';
import type { ValleyEval, SrLevels, PlanDerived } from '../../../types';

const vida: ValleyEval = {
  symbol:               'ADAUSDT',
  estado:               'ok',
  candidata:            true,
  vivo:                 true,
  pct_rango:            0.08,
  semanas_consolidando: 6,
  volumen_usd_dia:      820000,
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
  ubicacion: { dentro_de: null, techo: null, piso: null },
};

const plan: PlanDerived = {
  entry:      0.419,
  sl_plan:    0.385,
  sl_piso:    null,
  entry_zone: {
    centro:      0.419,
    precio_bajo: 0.412,
    precio_alto: 0.426,
    toques:      5,
  },
  rungs: [
    {
      tp_price:  0.448,
      size_frac: 0.5,
      zona: {
        centro:      0.448,
        precio_bajo: 0.445,
        precio_alto: 0.451,
        toques:      2,
      },
    },
  ],
  runner_frac: 0.05,
};

describe('IdeaChart', () => {
  it('la leyenda muestra las 3 entradas (Vida / Paredes / La jugada)', () => {
    render(
      <IdeaChart
        symbol="ADAUSDT"
        vida={vida}
        levels={levels}
        plan={plan}
        live={0.4205}
      />,
    );
    const items = screen.getAllByRole('button', { name: /Vida|Paredes|La jugada/i });
    expect(items).toHaveLength(3);
    // Todos empiezan activos
    items.forEach((btn) => {
      expect(btn.getAttribute('aria-pressed')).toBe('true');
    });
  });

  it('renderiza una línea de resistencia con "techo" y el precio', () => {
    render(
      <IdeaChart
        symbol="ADAUSDT"
        vida={vida}
        levels={levels}
        plan={plan}
        live={0.4205}
      />,
    );
    // El tag del muro combina "techo · $precio · N toques" en un span;
    // buscamos por matcher flexible que encuentre el texto en cualquier nodo
    const wallTag = screen.getByText((content) =>
      content.includes('techo') && content.includes('0.4480'),
    );
    expect(wallTag).toBeTruthy();
  });

  it('al hacer click en "Paredes" el botón pasa a aria-pressed=false y desaparecen los muros', () => {
    render(
      <IdeaChart
        symbol="ADAUSDT"
        vida={vida}
        levels={levels}
        plan={plan}
        live={0.4205}
      />,
    );
    const paredesBtn = screen.getByRole('button', { name: /Paredes/i });

    // Antes del click: activo + etiqueta de muro visible
    expect(paredesBtn.getAttribute('aria-pressed')).toBe('true');
    expect(
      screen.queryByText((c) => c.includes('techo') && c.includes('0.4480')),
    ).not.toBeNull();

    // Click: desactivar
    fireEvent.click(paredesBtn);

    // Después: inactivo + etiqueta de muro desaparece
    expect(paredesBtn.getAttribute('aria-pressed')).toBe('false');
    expect(
      screen.queryByText((c) => c.includes('techo') && c.includes('0.4480')),
    ).toBeNull();
  });
});
