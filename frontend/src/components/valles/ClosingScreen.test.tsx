// ClosingScreen.test.tsx
import { render, screen } from '@testing-library/react';
import { it, expect, vi } from 'vitest';
import { ClosingScreen } from './ClosingScreen';

const bundle = {
  vida: { data: { symbol: 'ADAUSDT', estado: 'ok', candidata: true }, loading: false, error: false },
  niveles: { data: { symbol: 'ADAUSDT', estado: 'ok', zonas: [], ubicacion: { dentro_de: null, techo: null, piso: null }, price_live: 1, generated_at: null }, loading: false, error: false },
  dossier: { data: { estado_general: 'opaco' }, loading: false, error: false },
} as never;

it('no escribe la cuarta línea: textContent sin compra/buena/score/recomendación/veredicto', () => {
  const { container } = render(<ClosingScreen symbol="ADAUSDT" bundle={bundle} onAsk={vi.fn()} onRestart={vi.fn()} />);
  expect(container.textContent ?? '').not.toMatch(/compra|buena|score|recomend|veredicto|potencial/i);
});

it('muestra las 3 columnas por separado', () => {
  render(<ClosingScreen symbol="ADAUSDT" bundle={bundle} onAsk={vi.fn()} onRestart={vi.fn()} />);
  expect(screen.getByText('Viva y tranquila')).toBeInTheDocument();
  expect(screen.getByText('Sin paredes claras')).toBeInTheDocument();
  expect(screen.getByText('Sin rastro público')).toBeInTheDocument();
});
