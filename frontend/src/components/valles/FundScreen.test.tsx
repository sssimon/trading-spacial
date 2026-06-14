// FundScreen.test.tsx
import { render, screen } from '@testing-library/react';
import { it, expect, vi } from 'vitest';
import { FundScreen } from './FundScreen';
import type { AsyncState } from './useValleyBundle';
import type { Dossier } from '../../types';

const st = (o: Partial<AsyncState<Dossier>>): AsyncState<Dossier> => ({ data: null, loading: false, error: false, ...o });

it('opaco: "no se encontró" con la misma fuerza (no se suaviza a error)', () => {
  render(<FundScreen symbol="ZBCUSDT" state={st({ data: { symbol: 'ZBCUSDT', equipo: [], equipo_identificado: false, presencia: {}, actividad: {}, financiacion: [], hitos: [], estado_general: 'opaco', no_encontrado_en: ['equipo','github'], generated_at: null } })} onRefresh={vi.fn()} />);
  expect(screen.getByText(/no se encontró quién está detrás/i)).toBeInTheDocument();
});

it('rastreable: cada miembro renderiza su fuente (candado anti-alucinación)', () => {
  render(<FundScreen symbol="ADAUSDT" onRefresh={vi.fn()} state={st({ data: {
    symbol: 'ADAUSDT', equipo_identificado: true, estado_general: 'rastreable', no_encontrado_en: [],
    equipo: [{ nombre: 'Charles H.', rol: 'Fundador', enlaces: [], fuente: 'https://x.com/iohk' }],
    presencia: { github: { url: 'https://github.com/input-output-hk', activo: 'si', fuente: null } },
    actividad: {}, financiacion: [], hitos: [], generated_at: '2026-06-10T00:00:00Z',
  } })} />);
  expect(screen.getByText(/Charles H\./i)).toBeInTheDocument();
  expect(screen.getAllByRole('link', { name: /fuente/i })[0]).toHaveAttribute('href', 'https://x.com/iohk');
});

it('error → botón refrescar', () => {
  render(<FundScreen symbol="ADAUSDT" state={st({ error: true })} onRefresh={vi.fn()} />);
  expect(screen.getByText(/no se pudo averiguar/i)).toBeInTheDocument();
});
