import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import AlertsStrip from './AlertsStrip';
import type { DashboardAlertSummary } from '../types';

describe('AlertsStrip', () => {
  it('renders nothing when items empty', () => {
    const empty: DashboardAlertSummary = { items: [] };
    const { container } = render(<AlertsStrip alerts={empty} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders alert item text', () => {
    const alerts: DashboardAlertSummary = {
      items: [
        { kind: 'symbol_failures', text: '3 símbolos en ALERT', severity: 'warning', ts: '2026-04-26T12:00:00Z' },
      ],
    };
    render(<AlertsStrip alerts={alerts} />);
    expect(screen.getByText('3 símbolos en ALERT')).toBeInTheDocument();
  });

  it('applies severity class', () => {
    const alerts: DashboardAlertSummary = {
      items: [
        { kind: 'portfolio_dd', text: 'Critical', severity: 'critical', ts: '2026-04-26T12:00:00Z' },
      ],
    };
    const { container } = render(<AlertsStrip alerts={alerts} />);
    expect(container.querySelector('.ks-alert-critical')).not.toBeNull();
  });
});
