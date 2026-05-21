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

  it('save sends notify_channels body when user changed chat_id, masked token preserved server-side', async () => {
    vi.spyOn(api, 'getPreferences').mockResolvedValue({
      tenant_id: 1, symbol_filter: null, min_score: 4,
      notify_channels: { telegram_bot_token: '123456789:****gH12', telegram_chat_id: '987' },
    });
    const updateSpy = vi.spyOn(api, 'putPreferences').mockResolvedValue({
      ok: true,
      preferences: { tenant_id: 1, symbol_filter: null, min_score: 4, notify_channels: null },
    });

    render(<ConnectionsPanel open={true} onClose={() => {}} />);
    const chatInput = await screen.findByLabelText(/chat id/i);
    await userEvent.clear(chatInput);
    await userEvent.type(chatInput, '111');
    await userEvent.click(screen.getByRole('button', { name: /guardar/i }));

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith({
        notify_channels: {
          telegram_bot_token: '123456789:****gH12',  // unchanged, server-side preserve
          telegram_chat_id:   '111',
        },
      });
    });
  });

  it('save sends plain token when user pastes a new one', async () => {
    vi.spyOn(api, 'getPreferences').mockResolvedValue({
      tenant_id: 1, symbol_filter: null, min_score: 4,
      notify_channels: { telegram_bot_token: '123456789:****gH12', telegram_chat_id: '987' },
    });
    const updateSpy = vi.spyOn(api, 'putPreferences').mockResolvedValue({
      ok: true,
      preferences: { tenant_id: 1, symbol_filter: null, min_score: 4, notify_channels: null },
    });

    render(<ConnectionsPanel open={true} onClose={() => {}} />);
    const tokenInput = await screen.findByLabelText(/bot token/i);
    await userEvent.clear(tokenInput);
    await userEvent.type(tokenInput, '999:NEW_PLAIN_TOKEN_VALUE');
    await userEvent.click(screen.getByRole('button', { name: /guardar/i }));

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith({
        notify_channels: {
          telegram_bot_token: '999:NEW_PLAIN_TOKEN_VALUE',
          telegram_chat_id:   '987',
        },
      });
    });
  });

  it('"Probar envío" disabled when there are unsaved changes', async () => {
    vi.spyOn(api, 'getPreferences').mockResolvedValue({
      tenant_id: 1, symbol_filter: null, min_score: 4,
      notify_channels: { telegram_bot_token: '123:****abcd', telegram_chat_id: '987' },
    });

    render(<ConnectionsPanel open={true} onClose={() => {}} />);
    await screen.findByLabelText(/bot token/i);

    const probarBtn = screen.getByRole('button', { name: /probar env/i });
    expect(probarBtn).not.toBeDisabled();  // initially clean

    await userEvent.type(screen.getByLabelText(/chat id/i), '5');
    expect(probarBtn).toBeDisabled();  // dirty
  });

  it('"Probar envío" shows ok on success', async () => {
    vi.spyOn(api, 'getPreferences').mockResolvedValue({
      tenant_id: 1, symbol_filter: null, min_score: 4,
      notify_channels: { telegram_bot_token: 'x:y', telegram_chat_id: 'z' },
    });
    vi.spyOn(api, 'testPreferencesDelivery').mockResolvedValue({
      ok: true,
      receipts: [{ channel: 'telegram', status: 'ok', error: null }],
      reason: null,
    });

    render(<ConnectionsPanel open={true} onClose={() => {}} />);
    await screen.findByLabelText(/bot token/i);
    await userEvent.click(screen.getByRole('button', { name: /probar env/i }));

    await waitFor(() => {
      expect(screen.getByText(/enviado/i)).toBeInTheDocument();
    });
  });

  it('"Probar envío" shows error on failure', async () => {
    vi.spyOn(api, 'getPreferences').mockResolvedValue({
      tenant_id: 1, symbol_filter: null, min_score: 4,
      notify_channels: { telegram_bot_token: 'x:y', telegram_chat_id: 'z' },
    });
    vi.spyOn(api, 'testPreferencesDelivery').mockResolvedValue({
      ok: false,
      receipts: [{ channel: 'telegram', status: 'failed', error: 'HTTP 401: Unauthorized' }],
      reason: null,
    });

    render(<ConnectionsPanel open={true} onClose={() => {}} />);
    await screen.findByLabelText(/bot token/i);
    await userEvent.click(screen.getByRole('button', { name: /probar env/i }));

    await waitFor(() => {
      expect(screen.getByText(/Unauthorized/)).toBeInTheDocument();
    });
  });

  it('"Probar envío" shows no_telegram_configured hint', async () => {
    vi.spyOn(api, 'getPreferences').mockResolvedValue({
      tenant_id: 1, symbol_filter: null, min_score: 4, notify_channels: null,
    });
    vi.spyOn(api, 'testPreferencesDelivery').mockResolvedValue({
      ok: false, receipts: [], reason: 'no_telegram_configured',
    });

    render(<ConnectionsPanel open={true} onClose={() => {}} />);
    await screen.findByLabelText(/bot token/i);
    await userEvent.click(screen.getByRole('button', { name: /probar env/i }));

    await waitFor(() => {
      expect(screen.getByText(/configur.*token.*chat/i)).toBeInTheDocument();
    });
  });

  it('"Eliminar credenciales" sends notify_channels: null', async () => {
    vi.spyOn(api, 'getPreferences').mockResolvedValue({
      tenant_id: 1, symbol_filter: null, min_score: 4,
      notify_channels: { telegram_bot_token: 'x:y', telegram_chat_id: 'z' },
    });
    const updateSpy = vi.spyOn(api, 'putPreferences').mockResolvedValue({
      ok: true, preferences: { tenant_id: 1, symbol_filter: null, min_score: 4, notify_channels: null },
    });

    render(<ConnectionsPanel open={true} onClose={() => {}} />);
    await screen.findByLabelText(/bot token/i);
    await userEvent.click(screen.getByRole('button', { name: /eliminar credenciales/i }));

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith({ notify_channels: null });
    });
  });
});
