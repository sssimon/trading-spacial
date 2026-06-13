import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi, afterEach } from 'vitest';
import { CoinCard } from './CoinCard';
import * as api from '../api';

afterEach(() => vi.restoreAllMocks());

const _levels = { symbol: 'SOLUSDT', estado: 'ok', generated_at: '2026-06-13T00:00:00+00:00',
  price_live: 142, zonas: [], ubicacion: { dentro_de: null, techo: null, piso: null } };
const _dossier = { symbol: 'SOLUSDT', estado_general: 'rastreable', equipo_identificado: true,
  equipo: [], presencia: {}, actividad: {}, financiacion: [], hitos: [], no_encontrado_en: [],
  generated_at: '2026-06-13T00:00:00+00:00' };
const _vidaOk = { symbol: 'SOLUSDT', estado: 'ok', candidata: true, pct_rango: 0.18,
  semanas_consolidando: 9, volumen_usd_dia: 3_000_000 };

function _mockAll(vida: unknown) {
  vi.spyOn(api, 'getValleyEval').mockResolvedValue(vida as never);
  vi.spyOn(api, 'getLevels').mockResolvedValue(_levels as never);
  vi.spyOn(api, 'getDossier').mockResolvedValue(_dossier as never);
}

describe('CoinCard', () => {
  it('pinta los tres bloques (Vida, Niveles, Fundamentales)', async () => {
    _mockAll(_vidaOk);
    render(<CoinCard symbol="SOLUSDT" />);
    expect(await screen.findByText(/Vida/)).toBeInTheDocument();
    expect(screen.getByText(/Niveles/)).toBeInTheDocument();
    expect(screen.getByText(/Fundamentales/)).toBeInTheDocument();
  });

  it('un bloque no_disponible degrada solo (los otros siguen)', async () => {
    _mockAll({ symbol: 'SOLUSDT', estado: 'no_disponible' });
    render(<CoinCard symbol="SOLUSDT" />);
    expect(await screen.findByText(/Niveles/)).toBeInTheDocument();
    expect(screen.getByText(/Sin datos/)).toBeInTheDocument();
  });

  it('no emite veredicto compuesto (anti-veredicto)', async () => {
    _mockAll(_vidaOk);
    const { container } = render(<CoinCard symbol="SOLUSDT" />);
    await screen.findByText(/Vida/);
    expect(/comprá|comprar|buena|score|recomend|veredicto|potencial/i
      .test(container.textContent ?? '')).toBe(false);
  });
});
