// PickScreen.test.tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { it, expect, vi } from 'vitest';
import { PickScreen } from './PickScreen';
import type { ValleySnapshot } from '../../types';

const snap = (over: Partial<ValleySnapshot> = {}): ValleySnapshot => ({
  generated_at: '2026-06-14T10:00:00Z',
  coverage: { universe: 200, evaluated: 180, complete: true },
  candidates: [
    { symbol: 'ADAUSDT', price: 0.45, pos_in_30d_range: 0.12, rsi14: 38, pct_vs_sma20: -6, pct_vs_sma50: -9, consol_30d: 40, vol_ratio: 0.7, drawdown_from_90h: -35, volumen_usd_dia: 1e7, distancia_ath_pct: 0.7, razones_vida: [] },
    { symbol: 'XLMUSDT', price: 0.11, pos_in_30d_range: 0.12, rsi14: 38, pct_vs_sma20: -6, pct_vs_sma50: -9, consol_30d: 40, vol_ratio: 0.7, drawdown_from_90h: -35, volumen_usd_dia: 5e6, distancia_ath_pct: 0.8, razones_vida: [] },
  ],
  frescura: { estado: 'fresco', edad_seg: 1800, generated_at: '2026-06-14T10:00:00Z', umbral_seg: 43200 },
  ...over,
});

it('lista las candidatas reales y dispara onPick con el símbolo', async () => {
  const onPick = vi.fn();
  render(<PickScreen snapshot={snap()} onPick={onPick} />);
  expect(screen.getByText('Cardano')).toBeInTheDocument();
  await userEvent.click(screen.getByText('Cardano'));
  expect(onPick).toHaveBeenCalledWith('ADAUSDT');
});

it('distingue "el screener nunca corrió" de "corrió y no halló"', () => {
  const { rerender } = render(<PickScreen snapshot={snap({ candidates: [], coverage: { universe: 0, evaluated: 0, complete: false }, frescura: { estado: 'muerto', edad_seg: null, generated_at: null, umbral_seg: 43200 } })} onPick={vi.fn()} />);
  expect(screen.getAllByText(/aún no ha completado un ciclo|todavía no corrió/i).length).toBeGreaterThan(0);
  rerender(<PickScreen snapshot={snap({ candidates: [], coverage: { universe: 200, evaluated: 200, complete: true } })} onPick={vi.fn()} />);
  expect(screen.getByText(/ninguna en la parte baja/i)).toBeInTheDocument();
});

it('el buscador agrega USDT y dispara onPick', async () => {
  const onPick = vi.fn();
  render(<PickScreen snapshot={snap()} onPick={onPick} />);
  const input = screen.getByPlaceholderText(/escribe su símbolo/i);
  await userEvent.type(input, 'sol{Enter}');
  expect(onPick).toHaveBeenCalledWith('SOLUSDT');
});
