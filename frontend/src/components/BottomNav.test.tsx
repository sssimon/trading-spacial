import { render, screen } from '@testing-library/react';
import { it, expect, vi } from 'vitest';
import BottomNav from './BottomNav';

it('incluye el item Valles en el nav móvil', () => {
  render(<BottomNav active="mercado" counts={{ market: 0, positions: 0, killswitch: 0 }} onSelect={vi.fn()} />);
  expect(screen.getByText('Valles')).toBeInTheDocument();
});
