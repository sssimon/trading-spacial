import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import Sparkline from './Sparkline';

describe('Sparkline', () => {
  it('renders 20 cells', () => {
    const { container } = render(
      <Sparkline outcomes={Array(20).fill(null)} />,
    );
    const cells = container.querySelectorAll('.ks-spark-cell');
    expect(cells).toHaveLength(20);
  });

  it('applies win class to W cells and loss class to L cells', () => {
    const outcomes: Array<'W' | 'L' | null> = [
      ...Array(15).fill(null),
      'W', 'W', 'L', 'L', 'W',
    ];
    const { container } = render(<Sparkline outcomes={outcomes} />);
    const cells = container.querySelectorAll('.ks-spark-cell');
    expect(cells[15].className).toContain('ks-spark-win');
    expect(cells[17].className).toContain('ks-spark-loss');
    expect(cells[19].className).toContain('ks-spark-win');
  });

  it('aria-label summarizes wins/losses/empty count', () => {
    const outcomes: Array<'W' | 'L' | null> = [
      ...Array(10).fill(null),
      'W', 'W', 'W', 'W', 'W', 'L', 'L', 'L', 'W', 'W',
    ];
    render(<Sparkline outcomes={outcomes} />);
    const root = screen.getByLabelText(/wins/i);
    expect(root).toBeInTheDocument();
    // Should mention 7 wins, 3 losses, 10 sin datos
    expect(root.getAttribute('aria-label')).toMatch(/7.*wins/i);
    expect(root.getAttribute('aria-label')).toMatch(/3.*loss/i);
  });
});
