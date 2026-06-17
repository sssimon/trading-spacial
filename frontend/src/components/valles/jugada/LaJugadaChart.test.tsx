// LaJugadaChart.test.tsx — smoke test del gráfico de velas cálido.
// Mockea lightweight-charts (sin canvas real) y getOhlcv (sin red).
// Verifica que el overlay de zona de entrada renderiza correctamente.

import { describe, it, expect, vi, beforeAll } from 'vitest';
import { render } from '@testing-library/react';

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

import { LaJugadaChart } from './LaJugadaChart';
import type { PlanDerived } from '../../../types';

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

describe('LaJugadaChart', () => {
  it('renderiza la banda de zona de entrada', async () => {
    const { findByText } = render(
      <LaJugadaChart
        symbol="ADAUSDT"
        plan={plan}
        live={0.4205}
        phaseLabel="derivada"
      />,
    );
    // El label de la zona de entrada debe aparecer en el DOM
    expect(await findByText(/ZONA DE ENTRADA/)).toBeTruthy();
  });

  it('muestra la píldora de fase', async () => {
    const { findByText } = render(
      <LaJugadaChart
        symbol="ADAUSDT"
        plan={plan}
        live={0.4205}
        phaseLabel="en curso"
      />,
    );
    expect(await findByText('en curso')).toBeTruthy();
  });

  it('muestra la leyenda del símbolo', async () => {
    const { findByText } = render(
      <LaJugadaChart
        symbol="ADAUSDT"
        plan={plan}
        live={0.4205}
        phaseLabel="derivada"
      />,
    );
    expect(await findByText('ADAUSDT')).toBeTruthy();
  });
});
