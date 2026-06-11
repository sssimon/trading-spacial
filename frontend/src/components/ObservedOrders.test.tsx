import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ObservedOrdersList } from './ObservedOrders';
import type { ObservedOrder } from '../types';

const sl: ObservedOrder = {
  symbol: 'BTCUSDT', kind: 'SL', price: 50000, qty: 0.5,
  pct_holding: 0.25, order_id: 1, oco_group: 33,
  observed_at: '2026-06-11T12:00:00+00:00',
};
const tp: ObservedOrder = { ...sl, kind: 'TP', price: 75000, order_id: 2 };

describe('ObservedOrdersList', () => {
  it('muestra cada orden con su porcentaje', () => {
    render(<ObservedOrdersList orders={[sl, tp]} />);
    expect(screen.getByText(/SL/)).toBeInTheDocument();
    expect(screen.getAllByText(/25%/u, { exact: false }).length).toBeGreaterThan(0);
    expect(screen.getByText(/TP/)).toBeInTheDocument();
  });

  it('badge "sin stop" cuando no hay ninguna orden SL', () => {
    render(<ObservedOrdersList orders={[tp]} />);
    expect(screen.getByText('sin stop')).toBeInTheDocument();
  });

  it('sin badge cuando hay SL', () => {
    render(<ObservedOrdersList orders={[sl, tp]} />);
    expect(screen.queryByText('sin stop')).toBeNull();
  });

  it('pct desconocido no muestra porcentaje', () => {
    render(<ObservedOrdersList orders={[{ ...sl, pct_holding: null }]} />);
    expect(screen.queryByText(/%/)).toBeNull();
  });
});
