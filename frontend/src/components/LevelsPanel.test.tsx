import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { LevelsPanel } from './LevelsPanel';
import type { SrLevels } from '../types';

const ok: SrLevels = {
  symbol: 'BTCUSDT', estado: 'ok', generated_at: '2026-06-12T00:00:00+00:00',
  price_live: 67230,
  zonas: [
    { tipo: 'soporte', precio_bajo: 64800, precio_alto: 65400, centro: 65100, toques: 3, confluencia_redondo: [65000] },
    { tipo: 'resistencia', precio_bajo: 69000, precio_alto: 69200, centro: 69100, toques: 4, confluencia_redondo: [69000] },
  ],
  ubicacion: { dentro_de: null, techo: { centro: 69100, dist_pct: 2.78 }, piso: { centro: 65100, dist_pct: -3.17 } },
};

describe('LevelsPanel', () => {
  it('muestra las zonas como bandas con toques', () => {
    render(<LevelsPanel levels={ok} loading={false} />);
    expect(screen.getByText(/Resistencias/i)).toBeInTheDocument();
    expect(screen.getByText(/Soportes/i)).toBeInTheDocument();
    expect(screen.getByText(/3 toques/)).toBeInTheDocument();
  });

  it('distingue "no disponible"', () => {
    const nd: SrLevels = {
      symbol: 'BTCUSDT', estado: 'no_disponible', generated_at: null,
      price_live: null, zonas: [], ubicacion: { dentro_de: null, techo: null, piso: null },
    };
    render(<LevelsPanel levels={nd} loading={false} />);
    expect(screen.getByText(/sin datos/i)).toBeInTheDocument();
  });

  it('no muestra ningún texto de recomendación / veredicto', () => {
    const { container } = render(<LevelsPanel levels={ok} loading={false} />);
    expect(/recomend|comprar|vender|señal|signal|buy|sell|score|veredicto/i
      .test(container.textContent ?? '')).toBe(false);
  });

  it('muestra la ubicación "dentro de zona" cuando el precio está en una banda', () => {
    const dentro: SrLevels = {
      symbol: 'BTCUSDT', estado: 'ok', generated_at: '2026-06-12T00:00:00+00:00',
      price_live: 65100,
      zonas: [
        { tipo: 'soporte', precio_bajo: 64800, precio_alto: 65400, centro: 65100, toques: 3, confluencia_redondo: [65000] },
      ],
      ubicacion: {
        dentro_de: { tipo: 'soporte', precio_bajo: 64800, precio_alto: 65400, centro: 65100, toques: 3, confluencia_redondo: [65000] },
        techo: null, piso: null,
      },
    };
    render(<LevelsPanel levels={dentro} loading={false} />);
    expect(screen.getByText(/dentro de la zona/i)).toBeInTheDocument();
  });
});
