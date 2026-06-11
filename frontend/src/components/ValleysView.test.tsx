import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ValleysView } from './ValleysView';
import type { ValleySnapshot } from '../types';

const snap: ValleySnapshot = {
  generated_at: '2026-06-11T12:00:00+00:00',
  coverage: { universe: 210, evaluated: 198, complete: false },
  candidates: [
    { symbol: 'ADAUSDT', price: 0.42, pct_rango: 0.18, semanas_consolidando: 9,
      vol_percentil: 0.22, volumen_usd_dia: 3_000_000, distancia_ath_pct: 0.86, razones_vida: [] },
    { symbol: 'XLMUSDT', price: 0.11, pct_rango: 0.21, semanas_consolidando: 6,
      vol_percentil: 0.31, volumen_usd_dia: 1_500_000, distancia_ath_pct: 0.91, razones_vida: [] },
  ],
};

describe('ValleysView', () => {
  it('lista las candidatas con sus hechos', () => {
    render(<ValleysView snapshot={snap} loading={false} />);
    expect(screen.getByText('ADAUSDT')).toBeInTheDocument();
    expect(screen.getByText('XLMUSDT')).toBeInTheDocument();
  });

  it('muestra cobertura incompleta de forma honesta', () => {
    render(<ValleysView snapshot={snap} loading={false} />);
    expect(screen.getByText(/198\s*\/\s*210/u)).toBeInTheDocument();
  });

  it('no renderiza ningún badge de compra ni señal', () => {
    const { container } = render(<ValleysView snapshot={snap} loading={false} />);
    const txt = container.textContent ?? '';
    expect(/comprar|compra|señal|signal|buy/i.test(txt)).toBe(false);
    expect(container.querySelector('[class*="buy"], [class*="signal"], [class*="senal"]')).toBeNull();
  });

  it('estado sin foto: muestra aviso, no rompe', () => {
    const vacio: ValleySnapshot = {
      generated_at: null, coverage: { universe: 0, evaluated: 0, complete: false }, candidates: [],
    };
    render(<ValleysView snapshot={vacio} loading={false} />);
    expect(screen.getByText(/sin foto|aún no/i)).toBeInTheDocument();
  });
});
