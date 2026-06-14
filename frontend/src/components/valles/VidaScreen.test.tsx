// VidaScreen.test.tsx
import { render, screen } from '@testing-library/react';
import { it, expect } from 'vitest';
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
