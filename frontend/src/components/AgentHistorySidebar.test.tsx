import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import AgentHistorySidebar from './AgentHistorySidebar';
import * as agentClient from '../agent/client';
import type { ConversationSummary } from '../agent/types';

function _conv(overrides: Partial<ConversationSummary> = {}): ConversationSummary {
  return {
    conversation_id: 'conv-1',
    title:           'que opinas de PENDLE?',
    surface:         'dock',
    first_ts:        '2026-05-22T08:00:00Z',
    last_ts:         '2026-05-22T09:00:00Z',
    message_count:   4,
    pinned:          false,
    ...overrides,
  };
}

describe('AgentHistorySidebar', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders nothing when closed', () => {
    vi.spyOn(agentClient, 'listConversations').mockResolvedValue({
      conversations: [_conv()], total: 1, limit: 20, offset: 0,
    });
    const { container } = render(
      <AgentHistorySidebar open={false} onClose={() => {}} />,
    );
    expect(container.querySelector('aside')).toBeNull();
  });

  it('fetches and renders conversations on open', async () => {
    vi.spyOn(agentClient, 'listConversations').mockResolvedValue({
      conversations: [_conv({ conversation_id: 'c1', title: 'primera' }),
                      _conv({ conversation_id: 'c2', title: 'segunda' })],
      total: 2, limit: 20, offset: 0,
    });

    render(<AgentHistorySidebar open={true} onClose={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText('primera')).toBeInTheDocument();
    });
    expect(screen.getByText('segunda')).toBeInTheDocument();
  });

  it('shows empty state when no conversations', async () => {
    vi.spyOn(agentClient, 'listConversations').mockResolvedValue({
      conversations: [], total: 0, limit: 20, offset: 0,
    });
    render(<AgentHistorySidebar open={true} onClose={() => {}} />);
    await waitFor(() => {
      expect(screen.getByText(/no tienes conversaciones todav/i)).toBeInTheDocument();
    });
  });

  it('shows error state on API failure', async () => {
    vi.spyOn(agentClient, 'listConversations').mockRejectedValue(
      new Error('500 server error'),
    );
    render(<AgentHistorySidebar open={true} onClose={() => {}} />);
    await waitFor(() => {
      expect(screen.getByText(/no se pudo cargar/i)).toBeInTheDocument();
    });
  });

  it('renders fallback title for null-title conversations', async () => {
    vi.spyOn(agentClient, 'listConversations').mockResolvedValue({
      conversations: [_conv({ title: null })], total: 1, limit: 20, offset: 0,
    });
    render(<AgentHistorySidebar open={true} onClose={() => {}} />);
    await waitFor(() => {
      expect(screen.getByText(/sin t.tulo/i)).toBeInTheDocument();
    });
  });

  it('clicking an item invokes onSelectConversation + closes', async () => {
    vi.spyOn(agentClient, 'listConversations').mockResolvedValue({
      conversations: [_conv({ conversation_id: 'pick-me', title: 'elígeme' })],
      total: 1, limit: 20, offset: 0,
    });
    const onSelect = vi.fn();
    const onClose = vi.fn();
    render(
      <AgentHistorySidebar
        open={true}
        onClose={onClose}
        onSelectConversation={onSelect}
      />,
    );
    const button = await screen.findByRole('button', { name: /abrir conversaci.n el.geme/i });
    await userEvent.click(button);
    expect(onSelect).toHaveBeenCalledWith('pick-me');
    expect(onClose).toHaveBeenCalled();
  });

  it('clicking pin toggles + calls API', async () => {
    vi.spyOn(agentClient, 'listConversations').mockResolvedValue({
      conversations: [_conv({ conversation_id: 'pin-c', pinned: false })],
      total: 1, limit: 20, offset: 0,
    });
    const togglePinSpy = vi.spyOn(agentClient, 'togglePinConversation').mockResolvedValue({
      ok: true, pinned: true,
    });

    render(<AgentHistorySidebar open={true} onClose={() => {}} />);
    const pinBtn = await screen.findByRole('button', { name: /^fijar$/i });
    await userEvent.click(pinBtn);
    expect(togglePinSpy).toHaveBeenCalledWith('pin-c');
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /desfijar/i })).toBeInTheDocument();
    });
  });

  it('clicking delete removes item optimistically + calls API', async () => {
    vi.spyOn(agentClient, 'listConversations').mockResolvedValue({
      conversations: [_conv({ conversation_id: 'kill-me', title: 'mátame' })],
      total: 1, limit: 20, offset: 0,
    });
    const delSpy = vi.spyOn(agentClient, 'deleteConversation').mockResolvedValue({
      ok: true,
    });

    render(<AgentHistorySidebar open={true} onClose={() => {}} />);
    await screen.findByText('mátame');
    const delBtn = screen.getByRole('button', { name: /^borrar$/i });
    await userEvent.click(delBtn);
    expect(delSpy).toHaveBeenCalledWith('kill-me');
    await waitFor(() => {
      expect(screen.queryByText('mátame')).not.toBeInTheDocument();
    });
  });

  it('delete failure reverts the optimistic removal', async () => {
    vi.spyOn(agentClient, 'listConversations').mockResolvedValue({
      conversations: [_conv({ conversation_id: 'oops', title: 'survivor' })],
      total: 1, limit: 20, offset: 0,
    });
    vi.spyOn(agentClient, 'deleteConversation').mockRejectedValue(new Error('network'));

    render(<AgentHistorySidebar open={true} onClose={() => {}} />);
    await screen.findByText('survivor');
    await userEvent.click(screen.getByRole('button', { name: /^borrar$/i }));
    // After the rejection settles, the item should reappear
    await waitFor(() => {
      expect(screen.getByText('survivor')).toBeInTheDocument();
    });
  });

  it('search input passes q to listConversations (debounced)', async () => {
    const listSpy = vi.spyOn(agentClient, 'listConversations').mockResolvedValue({
      conversations: [], total: 0, limit: 20, offset: 0,
    });
    render(<AgentHistorySidebar open={true} onClose={() => {}} />);
    // First call on open: no q
    await waitFor(() => expect(listSpy).toHaveBeenCalledTimes(1));
    expect(listSpy.mock.calls[0][0]).toMatchObject({ limit: 20 });
    expect(listSpy.mock.calls[0][0]?.q).toBeUndefined();

    const searchInput = screen.getByLabelText(/buscar/i);
    await userEvent.type(searchInput, 'PENDLE');
    // Debounce 200ms — wait for the search-triggered call
    await waitFor(
      () => expect(listSpy.mock.calls.some(([a]) => a?.q === 'PENDLE')).toBe(true),
      { timeout: 1000 },
    );
  });
});
