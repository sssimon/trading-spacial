import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ConnectionsPanel from './ConnectionsPanel';
import * as api from '../api';

describe('ConnectionsPanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders form with masked token from current preferences', async () => {
    vi.spyOn(api, 'getPreferences').mockResolvedValue({
      tenant_id:       1,
      symbol_filter:   null,
      min_score:       4,
      notify_channels: {
        telegram_bot_token: '123456789:****gH12',
        telegram_chat_id:   '987654321',
      },
    });

    render(<ConnectionsPanel open={true} onClose={() => {}} />);

    await waitFor(() => {
      const tokenInput = screen.getByLabelText(/bot token/i) as HTMLInputElement;
      expect(tokenInput.value).toBe('123456789:****gH12');
    });
    const chatInput = screen.getByLabelText(/chat id/i) as HTMLInputElement;
    expect(chatInput.value).toBe('987654321');
    expect(screen.getByText(/token guardado/i)).toBeInTheDocument();
  });
});
