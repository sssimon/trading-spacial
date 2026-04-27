import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import KillSwitchSymbolCard from './KillSwitchSymbolCard';
import type { DashboardSymbolState } from '../types';

const baseState: DashboardSymbolState = {
  symbol: 'BTC',
  state: 'NORMAL',
  state_since: '2026-04-20T00:00:00Z',
  manual_override: false,
  metrics: {
    trades_count_total: 50,
    win_rate_20_trades: 0.55,
    win_rate_10_trades: 0.5,
    pnl_30d: 420,
    months_negative_consecutive: 0,
    probation_trades_remaining: null,
    paused_days_at_entry: null,
  },
  last_transition: null,
  sparkline_20: Array(20).fill('W'),
  next_conditions: 'Saludable — sin alertas activas.',
};

describe('KillSwitchSymbolCard', () => {
  it('renders symbol name and tier badge', () => {
    render(<KillSwitchSymbolCard state={baseState} />);
    expect(screen.getByText('BTC')).toBeInTheDocument();
    expect(screen.getByText('NORMAL')).toBeInTheDocument();
  });

  it('renders next_conditions text', () => {
    render(<KillSwitchSymbolCard state={baseState} />);
    expect(screen.getByText(/Saludable/)).toBeInTheDocument();
  });

  it('renders last transition reason when present', () => {
    const withTransition: DashboardSymbolState = {
      ...baseState,
      state: 'PROBATION',
      last_transition: {
        from_state: 'PAUSED',
        to_state: 'PROBATION',
        reason: 'reactivated_manual',
        ts: '2026-04-25T00:00:00Z',
      },
    };
    render(<KillSwitchSymbolCard state={withTransition} />);
    expect(screen.getByText(/reactivated_manual/)).toBeInTheDocument();
  });

  it('renders metrics: WR, pnl, trades', () => {
    render(<KillSwitchSymbolCard state={baseState} />);
    expect(screen.getByText('55%')).toBeInTheDocument();
    expect(screen.getByText(/420/)).toBeInTheDocument();
    expect(screen.getByText('50')).toBeInTheDocument();
  });
});
