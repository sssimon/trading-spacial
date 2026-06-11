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
    expect(screen.getAllByText(/25%/u)).toHaveLength(2);
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

  // --- OCO pairing tests ---

  it('par OCO: dos órdenes con oco_group 33 quedan dentro de un único .ocoPair', () => {
    render(<ObservedOrdersList orders={[sl, tp]} />);
    const pairs = screen.getAllByTestId('oco-pair');
    expect(pairs).toHaveLength(1);
    const pair = pairs[0];
    expect(pair).toHaveTextContent(/SL/);
    expect(pair).toHaveTextContent(/TP/);
    // SL aparece antes que TP dentro del wrapper
    const chips = pair.querySelectorAll('span');
    expect(chips[0].textContent).toMatch(/SL/);
    expect(chips[1].textContent).toMatch(/TP/);
  });

  it('orden suelta (oco_group null) no genera ningún wrapper oco-pair', () => {
    const loose: ObservedOrder = { ...sl, oco_group: null, order_id: 99 };
    render(<ObservedOrdersList orders={[loose]} />);
    expect(screen.queryAllByTestId('oco-pair')).toHaveLength(0);
    expect(screen.getByText(/SL/)).toBeInTheDocument();
  });

  it('mixto: un par OCO + un SL suelto → exactamente 1 pair wrapper y el chip suelto fuera', () => {
    const looseSl: ObservedOrder = { ...sl, oco_group: null, order_id: 99 };
    render(<ObservedOrdersList orders={[sl, tp, looseSl]} />);
    const pairs = screen.getAllByTestId('oco-pair');
    expect(pairs).toHaveLength(1);
    // El chip suelto está en el documento pero NO dentro del wrapper
    const slTexts = screen.getAllByText(/SL/);
    // Hay dos chips SL en total (el del par y el suelto)
    expect(slTexts).toHaveLength(2);
    // El wrapper sólo contiene un chip SL (el del par)
    const slInsidePair = pairs[0].querySelectorAll('span');
    const slChipsInPair = Array.from(slInsidePair).filter((el) => el.textContent?.match(/SL/));
    expect(slChipsInPair).toHaveLength(1);
  });
});
