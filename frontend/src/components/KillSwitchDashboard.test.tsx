import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import KillSwitchDashboard from './KillSwitchDashboard';

vi.mock('../api', () => ({
  getKillSwitchCurrentState: vi.fn(),
}));

import { getKillSwitchCurrentState } from '../api';

describe('KillSwitchDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows portfolio tier NORMAL when no symbols reported', async () => {
    (getKillSwitchCurrentState as ReturnType<typeof vi.fn>).mockResolvedValue({
      symbols: {},
      portfolio: { tier: 'NORMAL', concurrent_failures: 0 },
    });
    render(<KillSwitchDashboard />);
    await waitFor(() => {
      expect(getKillSwitchCurrentState).toHaveBeenCalled();
    });
    expect(screen.getByText(/Portfolio/i)).toBeInTheDocument();
    expect(screen.getByText('NORMAL')).toBeInTheDocument();
  });

  it('renders per-symbol tier cards for each symbol', async () => {
    (getKillSwitchCurrentState as ReturnType<typeof vi.fn>).mockResolvedValue({
      symbols: {
        BTCUSDT: {
          symbol: 'BTCUSDT', per_symbol_tier: 'NORMAL', portfolio_tier: 'NORMAL',
          size_factor: 1.0, skip: false, velocity_active: false,
          ts: '2026-04-23T12:00:00Z', reasons_json: '{}',
        },
        ETHUSDT: {
          symbol: 'ETHUSDT', per_symbol_tier: 'ALERT', portfolio_tier: 'NORMAL',
          size_factor: 1.0, skip: false, velocity_active: false,
          ts: '2026-04-23T12:00:00Z', reasons_json: '{}',
        },
      },
      portfolio: { tier: 'NORMAL', concurrent_failures: 1 },
    });
    render(<KillSwitchDashboard />);
    await waitFor(() => {
      expect(screen.getByText('BTCUSDT')).toBeInTheDocument();
      expect(screen.getByText('ETHUSDT')).toBeInTheDocument();
    });
  });

  it('shows portfolio WARNED when threshold reached', async () => {
    (getKillSwitchCurrentState as ReturnType<typeof vi.fn>).mockResolvedValue({
      symbols: {},
      portfolio: { tier: 'WARNED', concurrent_failures: 3 },
    });
    render(<KillSwitchDashboard />);
    await waitFor(() => {
      expect(screen.getByText('WARNED')).toBeInTheDocument();
    });
  });

  it('survives API failure without crashing', async () => {
    (getKillSwitchCurrentState as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('network'),
    );
    const { container } = render(<KillSwitchDashboard />);
    await waitFor(() => {
      expect(getKillSwitchCurrentState).toHaveBeenCalled();
    });
    expect(container.querySelector('.ks-dashboard')).not.toBeNull();
  });
});
