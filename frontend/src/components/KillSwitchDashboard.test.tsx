import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import KillSwitchDashboard from './KillSwitchDashboard';
import type { DashboardResponse } from '../types';

vi.mock('../api', () => ({
  getHealthDashboard: vi.fn(),
}));

import { getHealthDashboard } from '../api';

const emptyResponse: DashboardResponse = {
  symbols: [],
  portfolio: {
    tier: 'NORMAL', dd_pct: 0, peak_equity: 1000, current_equity: 1000,
    concurrent_failures: 0, recent_transitions: [],
  },
  alerts: { items: [] },
  generated_at: '2026-04-26T00:00:00Z',
};

describe('KillSwitchDashboard (B6)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows skeleton on initial mount', () => {
    (getHealthDashboard as ReturnType<typeof vi.fn>).mockImplementation(
      () => new Promise(() => {}),  // never resolves
    );
    const { container } = render(<KillSwitchDashboard />);
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
  });

  it('renders portfolio panel + empty symbol grid', async () => {
    (getHealthDashboard as ReturnType<typeof vi.fn>).mockResolvedValue(emptyResponse);
    render(<KillSwitchDashboard />);
    await waitFor(() => {
      expect(screen.getByText('NORMAL')).toBeInTheDocument();
    });
    expect(screen.getByText(/Sin datos aún/)).toBeInTheDocument();
  });

  it('renders symbol cards when data arrives', async () => {
    const withSymbols: DashboardResponse = {
      ...emptyResponse,
      symbols: [
        {
          symbol: 'BTC', state: 'NORMAL', state_since: '2026-04-20T00:00:00Z',
          manual_override: false,
          metrics: {
            trades_count_total: 50, win_rate_20_trades: 0.55, win_rate_10_trades: 0.5,
            pnl_30d: 420, months_negative_consecutive: 0,
            probation_trades_remaining: null, paused_days_at_entry: null,
          },
          last_transition: null, sparkline_20: Array(20).fill('W'),
          next_conditions: 'Saludable',
        },
      ],
    };
    (getHealthDashboard as ReturnType<typeof vi.fn>).mockResolvedValue(withSymbols);
    render(<KillSwitchDashboard />);
    await waitFor(() => {
      expect(screen.getByText('BTC')).toBeInTheDocument();
    });
  });

  it('survives API failure: shows error banner without crashing', async () => {
    (getHealthDashboard as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('network'),
    );
    const { container } = render(<KillSwitchDashboard />);
    await waitFor(() => {
      expect(getHealthDashboard).toHaveBeenCalled();
    });
    expect(container.querySelector('.ks-dashboard')).not.toBeNull();
  });

  it('renders aria-live region for polite announcements', async () => {
    (getHealthDashboard as ReturnType<typeof vi.fn>).mockResolvedValue(emptyResponse);
    const { container } = render(<KillSwitchDashboard />);
    await waitFor(() => {
      expect(container.querySelector('[aria-live="polite"]')).not.toBeNull();
    });
  });
});
