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
