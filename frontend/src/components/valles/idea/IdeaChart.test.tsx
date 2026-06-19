// IdeaChart.test.tsx — smoke tests del gráfico unificado idea.
// Mockea lightweight-charts (sin canvas real).
// Verifica leyenda clicable (3 entradas), toggle de capas,
// que setData se llame con levels.candles, y estados de carga/error.

import { describe, it, expect, vi, beforeAll } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';

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

// Referencia al mock de setData para assertar llamadas
const mockSetData = vi.fn();

// Mock de lightweight-charts: serie con priceToCoordinate trivial
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

import { IdeaChart } from './IdeaChart';
import type { ValleyEval, SrLevels, PlanDerived } from '../../../types';

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
  ubicacion: { dentro_de: null, techo: null, piso: null },
  candles: [
    { time: 1700000000, open: 0.40, high: 0.42, low: 0.39, close: 0.41 },
    { time: 1700086400, open: 0.41, high: 0.43, low: 0.40, close: 0.42 },
    { time: 1700172800, open: 0.42, high: 0.45, low: 0.41, close: 0.44 },
  ],
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
    await waitFor(() => {
      expect(mockSetData).toHaveBeenCalled();
    });
    const callArg = mockSetData.mock.calls[mockSetData.mock.calls.length - 1][0];
    expect(Array.isArray(callArg)).toBe(true);
    expect(callArg).toHaveLength(3);
    expect(callArg[0]).toMatchObject({ open: 0.40, high: 0.42, low: 0.39, close: 0.41 });
  });

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
});
