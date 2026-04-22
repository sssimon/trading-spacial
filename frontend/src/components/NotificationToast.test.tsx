import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import NotificationToast from './NotificationToast';

// Mock the API module so components don't hit the network.
vi.mock('../api', () => ({
  getNotifications: vi.fn(),
  markNotificationRead: vi.fn(),
}));

import { getNotifications } from '../api';

const fakeNotif = (overrides: Partial<Record<string, unknown>> = {}) => ({
  id: 1,
  event_type: 'health',
  event_key: 'health:BTC:PAUSED',
  priority: 'warning',
  payload_json: JSON.stringify({ symbol: 'BTC', from_state: 'REDUCED', to_state: 'PAUSED' }),
  channels_sent: 'telegram',
  delivery_status: 'ok',
  sent_at: '2026-04-22T12:00:00+00:00',
  read_at: null,
  error_log: null,
  ...overrides,
});

describe('NotificationToast', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders nothing when there are no unread warnings', async () => {
    (getNotifications as ReturnType<typeof vi.fn>).mockResolvedValue({ notifications: [] });
    const { container } = render(<NotificationToast />);
    await waitFor(() => {
      // Let the initial load resolve.
      expect(getNotifications).toHaveBeenCalled();
    });
    expect(container.querySelector('.toast-stack')).toBeNull();
  });

  it('renders a toast for each unread warning/critical event', async () => {
    (getNotifications as ReturnType<typeof vi.fn>).mockResolvedValue({
      notifications: [
        fakeNotif({ id: 1, priority: 'warning' }),
        fakeNotif({ id: 2, priority: 'critical', event_type: 'infra',
                     payload_json: JSON.stringify({ component: 'scanner', message: 'crashed' }) }),
      ],
    });
    render(<NotificationToast />);
    const toasts = await screen.findAllByTestId('notification-toast');
    expect(toasts).toHaveLength(2);
  });

  it('does NOT render info-priority notifications', async () => {
    (getNotifications as ReturnType<typeof vi.fn>).mockResolvedValue({
      notifications: [
        fakeNotif({ id: 1, priority: 'info', event_type: 'signal' }),
      ],
    });
    const { container } = render(<NotificationToast />);
    await waitFor(() => { expect(getNotifications).toHaveBeenCalled(); });
    expect(container.querySelector('[data-testid="notification-toast"]')).toBeNull();
  });

  it('caps at 3 toasts', async () => {
    const many = Array.from({ length: 10 }, (_, i) =>
      fakeNotif({ id: i + 1, priority: 'warning' }),
    );
    (getNotifications as ReturnType<typeof vi.fn>).mockResolvedValue({ notifications: many });
    render(<NotificationToast />);
    const toasts = await screen.findAllByTestId('notification-toast');
    expect(toasts).toHaveLength(3);
  });

  it('survives API failure without crashing', async () => {
    (getNotifications as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network'));
    const { container } = render(<NotificationToast />);
    await waitFor(() => { expect(getNotifications).toHaveBeenCalled(); });
    expect(container.querySelector('.toast-stack')).toBeNull();
  });
});
