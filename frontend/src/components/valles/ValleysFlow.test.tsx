// ValleysFlow.test.tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { it, expect, vi, beforeEach } from 'vitest';
import { ValleysFlow } from './ValleysFlow';
import * as api from '../../api';
import type { ValleySnapshot } from '../../types';

vi.mock('../../api');
beforeEach(() => {
  localStorage.clear();
  vi.mocked(api.getValleyEval).mockResolvedValue({ symbol: 'ADAUSDT', estado: 'ok', candidata: true, pct_rango: 0.1, semanas_consolidando: 5, vol_percentil: 0.2 } as never);
  vi.mocked(api.getLevels).mockResolvedValue({ symbol: 'ADAUSDT', estado: 'ok', generated_at: null, price_live: 1, zonas: [], ubicacion: { dentro_de: null, techo: null, piso: null } } as never);
  vi.mocked(api.getDossier).mockResolvedValue({ symbol: 'ADAUSDT', equipo: [], equipo_identificado: false, presencia: {}, actividad: {}, financiacion: [], hitos: [], estado_general: 'opaco', no_encontrado_en: [], generated_at: null } as never);
});

const snap: ValleySnapshot = {
  generated_at: '2026-06-14T10:00:00Z', coverage: { universe: 10, evaluated: 10, complete: true },
  candidates: [{ symbol: 'ADAUSDT', price: 0.45, pct_rango: 0.12, semanas_consolidando: 6, vol_percentil: 0.2, volumen_usd_dia: 1e7, distancia_ath_pct: 0.7, razones_vida: [] }],
  frescura: { estado: 'fresco', edad_seg: 1800, generated_at: '2026-06-14T10:00:00Z', umbral_seg: 43200 },
};

it('el tagline NO emite veredicto de operabilidad (§4.1)', () => {
  const { container } = render(<ValleysFlow snapshot={snap} loading={false} />);
  expect(container.textContent ?? '').not.toMatch(/d[oó]nde operar/i);
});

it('al elegir una moneda avanza a Vida y el stepper dice "Paso 1 de 3"', async () => {
  render(<ValleysFlow snapshot={snap} loading={false} />);
  await userEvent.click(screen.getByText('Cardano'));
  expect(await screen.findByText('¿Está viva la moneda?')).toBeInTheDocument();
  expect(screen.getByText(/paso 1 de 3/i)).toBeInTheDocument();
});
