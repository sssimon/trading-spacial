// ValleysFlow.test.tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { it, expect, vi, beforeEach, describe } from 'vitest';
import { ValleysFlow } from './ValleysFlow';
import type { ValleySnapshot } from '../../types';

// Mock IdeaView so we don't have to wire up the full bundle stack
vi.mock('./idea/IdeaView', () => ({
  IdeaView: ({ symbol }: { symbol: string }) => (
    <div data-testid="idea-view" data-symbol={symbol} />
  ),
}));

// Mock PickScreen to capture onPick without rendering the full list UI
vi.mock('./PickScreen', () => ({
  PickScreen: ({ onPick }: { onPick: (s: string) => void }) => (
    <div data-testid="pick-screen">
      <button onClick={() => onPick('ADAUSDT')}>elegir ADAUSDT</button>
    </div>
  ),
}));

vi.mock('./Copilot', () => ({
  Copilot: () => <div data-testid="copilot" />,
}));

vi.mock('./AltSeasonHeader', () => ({
  AltSeasonHeader: () => <div data-testid="alt-season-header-stub" />,
}));

beforeEach(() => {
  localStorage.clear();
});

const snap: ValleySnapshot = {
  generated_at: '2026-06-14T10:00:00Z',
  coverage: { universe: 10, evaluated: 10, complete: true },
  candidates: [
    {
      symbol: 'ADAUSDT', price: 0.45, pos_in_30d_range: 0.12, rsi14: 38,
      pct_vs_sma20: -6, pct_vs_sma50: -9, consol_30d: 40, vol_ratio: 0.7,
      drawdown_from_90h: -35, volumen_usd_dia: 1e7, distancia_ath_pct: 0.7, razones_vida: [],
    },
  ],
  frescura: { estado: 'fresco', edad_seg: 1800, generated_at: '2026-06-14T10:00:00Z', umbral_seg: 43200 },
};

describe('ValleysFlow', () => {
  it('el tagline NO emite veredicto de operabilidad (§4.1)', () => {
    const { container } = render(<ValleysFlow snapshot={snap} loading={false} />);
    expect(container.textContent ?? '').not.toMatch(/d[oó]nde operar/i);
  });

  it('sin símbolo muestra PickScreen', () => {
    render(<ValleysFlow snapshot={snap} loading={false} />);
    expect(screen.getByTestId('pick-screen')).toBeInTheDocument();
    expect(screen.queryByTestId('idea-view')).not.toBeInTheDocument();
  });

  it('sin símbolo y loading muestra placeholder de carga', () => {
    render(<ValleysFlow snapshot={snap} loading={true} />);
    expect(screen.getByText('Cargando la foto…')).toBeInTheDocument();
    expect(screen.queryByTestId('pick-screen')).not.toBeInTheDocument();
  });

  it('al elegir una moneda muestra IdeaView con el símbolo correcto', async () => {
    render(<ValleysFlow snapshot={snap} loading={false} />);
    await userEvent.click(screen.getByText('elegir ADAUSDT'));
    const view = await screen.findByTestId('idea-view');
    expect(view).toBeInTheDocument();
    expect(view).toHaveAttribute('data-symbol', 'ADAUSDT');
    expect(screen.queryByTestId('pick-screen')).not.toBeInTheDocument();
  });
});
