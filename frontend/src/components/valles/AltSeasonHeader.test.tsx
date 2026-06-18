import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { AltSeasonHeader } from './AltSeasonHeader';
import type { RegimeSnapshot } from '../../types';

vi.mock('../../api', () => ({ getAltSeason: vi.fn() }));
import { getAltSeason } from '../../api';

function snap(over: Partial<RegimeSnapshot> = {}): RegimeSnapshot {
  return {
    generated_at: new Date().toISOString(),
    coverage: { universe: 3, evaluated: 3, complete: true },
    dominancia_fetch: { ok: true, fetched_at: null, source: 'coingecko/global' },
    regime: {
      estado: 'alts',
      componentes: {
        breadth50: { valor: 0.62, lean: 'alts', estado: 'fresco', n: 418 },
        outperf_30d: { valor: 0.071, lean: 'alts', estado: 'fresco' },
        dominancia_btc: { valor: 0.539, lean: 'alts', estado: 'fresco' },
      },
      votos: { alts: 3, neutral: 0, btc: 0, vivos: 3 },
      n_alts_evaluadas: 418,
    },
    frescura: { estado: 'fresco', edad_seg: 10, generated_at: null, umbral_seg: 43200 },
    ...over,
  };
}

describe('AltSeasonHeader', () => {
  it('muestra el estado, los 3 componentes y la frase honesta', async () => {
    (getAltSeason as any).mockResolvedValue(snap());
    render(<AltSeasonHeader />);
    await waitFor(() => expect(screen.getByTestId('regime-estado')).toBeInTheDocument());
    expect(screen.getByTestId('regime-estado').textContent).toMatch(/alts/i);
    expect(screen.getByText(/breadth/i)).toBeInTheDocument();
    expect(screen.getByText(/dominancia/i)).toBeInTheDocument();
    expect(screen.getByText(/régimen del mercado/i)).toBeInTheDocument();
  });

  it('muestra la dominancia degradada cuando está muerta', async () => {
    const s = snap();
    s.regime.componentes.dominancia_btc = { valor: null, lean: null, estado: 'muerto' };
    s.dominancia_fetch.ok = false;
    (getAltSeason as any).mockResolvedValue(s);
    render(<AltSeasonHeader />);
    await waitFor(() => expect(screen.getByTestId('dominancia-muerta')).toBeInTheDocument());
  });
});
