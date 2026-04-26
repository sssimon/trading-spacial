import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import PortfolioPanel from './PortfolioPanel';
import type { DashboardPortfolioState } from '../types';

const baseState: DashboardPortfolioState = {
  tier: 'NORMAL',
  dd_pct: -0.021,
  peak_equity: 12450,
  current_equity: 12189,
  concurrent_failures: 1,
  recent_transitions: [],
};

describe('PortfolioPanel', () => {
  it('renders tier and DD%', () => {
    render(<PortfolioPanel portfolio={baseState} />);
    expect(screen.getByText('NORMAL')).toBeInTheDocument();
    expect(screen.getByText(/-2\.1%/)).toBeInTheDocument();
  });

  it('renders concurrent failures count', () => {
    render(<PortfolioPanel portfolio={baseState} />);
    expect(screen.getByText('1')).toBeInTheDocument();
  });

  it('renders recent transitions when present', () => {
    const withTransitions: DashboardPortfolioState = {
      ...baseState,
      recent_transitions: [
        { from_tier: 'WARNED', to_tier: 'NORMAL', reason: 'recovered',
          dd_pct: -0.01, concurrent: 1, ts: '2026-04-25T00:00:00Z' },
      ],
    };
    render(<PortfolioPanel portfolio={withTransitions} />);
    expect(screen.getByText(/recovered/)).toBeInTheDocument();
  });
});
