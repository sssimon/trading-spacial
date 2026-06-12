import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ProjectDossier } from './ProjectDossier';
import type { Dossier } from '../types';

const rastreable: Dossier = {
  symbol: 'ADAUSDT', estado_general: 'rastreable', equipo_identificado: true,
  equipo: [{ nombre: 'Charles Hoskinson', rol: 'CEO', enlaces: [], fuente: 'https://cardano.org' }],
  presencia: { sitio_web: { url: 'https://cardano.org', activo: 'si', fuente: 'https://cardano.org' } },
  actividad: {}, financiacion: [], hitos: [], no_encontrado_en: [],
  generated_at: '2026-06-12T00:00:00+00:00',
};

describe('ProjectDossier', () => {
  it('lista los hechos con su enlace fuente', () => {
    render(<ProjectDossier dossier={rastreable} loading={false} />);
    expect(screen.getByText('Charles Hoskinson')).toBeInTheDocument();
    const link = screen.getAllByRole('link').find((a) => a.getAttribute('href') === 'https://cardano.org');
    expect(link).toBeTruthy();   // cada hecho ancla a su fuente
  });

  it('muestra badge "opaco" con lo que no se encontró', () => {
    const opaco: Dossier = {
      symbol: 'XYZUSDT', estado_general: 'opaco', equipo_identificado: false,
      equipo: [], presencia: {}, actividad: {}, financiacion: [], hitos: [],
      no_encontrado_en: ['equipo', 'presencia', 'actividad'],
      generated_at: '2026-06-12T00:00:00+00:00',
    };
    render(<ProjectDossier dossier={opaco} loading={false} />);
    expect(screen.getByText(/opaco/i)).toBeInTheDocument();
    expect(screen.getByText(/equipo/)).toBeInTheDocument();   // qué se buscó y faltó
  });

  it('distingue "no disponible" de "opaco"', () => {
    const nd: Dossier = {
      symbol: 'XYZUSDT', estado_general: 'no_disponible', equipo_identificado: false,
      equipo: [], presencia: {}, actividad: {}, financiacion: [], hitos: [],
      no_encontrado_en: [], generated_at: null,
    };
    render(<ProjectDossier dossier={nd} loading={false} />);
    expect(screen.getByText(/no disponible|no se pudo/i)).toBeInTheDocument();
  });

  it('no muestra ningún texto de recomendación / score', () => {
    const { container } = render(<ProjectDossier dossier={rastreable} loading={false} />);
    expect(/recomend|comprar|potencial|score|veredicto/i.test(container.textContent ?? '')).toBe(false);
  });
});
