import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { FreshnessTag } from './FreshnessTag';

describe('FreshnessTag', () => {
  it('muerto: dice que no ha corrido', () => {
    render(<FreshnessTag frescura={{ estado: 'muerto', edad_seg: null, generated_at: null }} />);
    expect(screen.getByText(/no ha corrido|sin/i)).toBeInTheDocument();
  });
  it('rancio: muestra la antigüedad', () => {
    render(<FreshnessTag frescura={{ estado: 'rancio', edad_seg: 172800, generated_at: 'x' }} />);
    expect(screen.getByText(/rancia|rancio|hace/i)).toBeInTheDocument();
  });
  it('fresco: discreto', () => {
    const { container } = render(<FreshnessTag frescura={{ estado: 'fresco', edad_seg: 60, generated_at: 'x' }} />);
    expect(container.textContent).toBeTruthy();
  });
  it('sin frescura: no renderiza nada', () => {
    const { container } = render(<FreshnessTag frescura={undefined} />);
    expect(container.textContent).toBe('');
  });
});
