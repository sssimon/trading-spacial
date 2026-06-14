// atoms.test.tsx
import { render, screen } from '@testing-library/react';
import { it, expect } from 'vitest';
import { Eyebrow, Loading, Callout } from './atoms';

it('Eyebrow muestra el nombre humano y el símbolo', () => {
  render(<Eyebrow symbol="ADAUSDT" />);
  expect(screen.getByText('Cardano')).toBeInTheDocument();
  expect(screen.getByText('ADAUSDT')).toBeInTheDocument();
});

it('Loading anuncia que está cargando (rama distinta de error)', () => {
  render(<Loading label="Revisando…" />);
  expect(screen.getByText('Revisando…')).toBeInTheDocument();
});

it('Callout renderiza título y subtítulo', () => {
  render(<Callout tone="mute" icon="?" title="No se pudo" sub="Es la herramienta." />);
  expect(screen.getByText('No se pudo')).toBeInTheDocument();
});
