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
