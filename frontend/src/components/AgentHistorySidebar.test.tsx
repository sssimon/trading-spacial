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

  it('item body is non-clickable when onSelectConversation is undefined', async () => {
    // PR #435 review issue #6: until H.5 wires rehydration, the parent
    // (AgentDock) does NOT pass onSelectConversation. The item body
    // should render as a plain div so the user doesn't get the
    // "I clicked but nothing happened" experience.
    vi.spyOn(agentClient, 'listConversations').mockResolvedValue({
      conversations: [_conv({ conversation_id: 'nope', title: 'sin handler' })],
      total: 1, limit: 20, offset: 0,
    });
    render(<AgentHistorySidebar open={true} onClose={() => {}} />);
    await screen.findByText('sin handler');
    // Should NOT find a button with the "Abrir conversación" label.
    expect(
      screen.queryByRole('button', { name: /abrir conversaci.n/i }),
    ).toBeNull();
    // Pin + delete buttons still present
    expect(screen.getByRole('button', { name: /^fijar$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^borrar$/i })).toBeInTheDocument();
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

  it('pin failure reverts the optimistic toggle (#435 issue #1)', async () => {
    vi.spyOn(agentClient, 'listConversations').mockResolvedValue({
      conversations: [_conv({ conversation_id: 'pin-fail', pinned: false })],
      total: 1, limit: 20, offset: 0,
    });
    vi.spyOn(agentClient, 'togglePinConversation').mockRejectedValue(
      new Error('network'),
    );

    render(<AgentHistorySidebar open={true} onClose={() => {}} />);
    const pinBtn = await screen.findByRole('button', { name: /^fijar$/i });
    await userEvent.click(pinBtn);
    // After the rejection settles, the chip should be back to "Fijar"
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^fijar$/i })).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: /desfijar/i })).toBeNull();
  });

  it('pressing Escape closes the sidebar', async () => {
    vi.spyOn(agentClient, 'listConversations').mockResolvedValue({
      conversations: [], total: 0, limit: 20, offset: 0,
    });
    const onClose = vi.fn();
    render(<AgentHistorySidebar open={true} onClose={onClose} />);
    await screen.findByLabelText(/buscar/i);
    await userEvent.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalled();
  });

  it('clicking the backdrop closes the sidebar', async () => {
    vi.spyOn(agentClient, 'listConversations').mockResolvedValue({
      conversations: [], total: 0, limit: 20, offset: 0,
    });
    const onClose = vi.fn();
    const { container } = render(
      <AgentHistorySidebar open={true} onClose={onClose} />,
    );
    await screen.findByLabelText(/buscar/i);
    // backdrop is the aria-hidden div with the backdrop class
    const backdrop = container.querySelector('[aria-hidden="true"]');
    expect(backdrop).not.toBeNull();
    await userEvent.click(backdrop!);
    expect(onClose).toHaveBeenCalled();
  });

  it('preserves the API ordering (pinned first then last_ts DESC)', async () => {
    // The H.3 endpoint guarantees this ordering; the component must
    // render rows in the order the API returned.
    vi.spyOn(agentClient, 'listConversations').mockResolvedValue({
      conversations: [
        _conv({ conversation_id: 'pin-old', title: 'fijada antigua',
                pinned: true, last_ts: '2026-05-20T08:00:00Z' }),
        _conv({ conversation_id: 'fresh', title: 'reciente sin fijar',
                pinned: false, last_ts: '2026-05-22T09:00:00Z' }),
        _conv({ conversation_id: 'older', title: 'vieja sin fijar',
                pinned: false, last_ts: '2026-05-21T09:00:00Z' }),
      ],
      total: 3, limit: 20, offset: 0,
    });
    render(<AgentHistorySidebar open={true} onClose={() => {}} />);
    await screen.findByText('fijada antigua');
    const titles = ['fijada antigua', 'reciente sin fijar', 'vieja sin fijar']
      .map((t) => screen.getByText(t));
    // Render order matches API order: pin first, then by last_ts DESC
    expect(titles[0].compareDocumentPosition(titles[1])
           & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(titles[1].compareDocumentPosition(titles[2])
           & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('clearing the search input re-fires the list call without q', async () => {
    const listSpy = vi.spyOn(agentClient, 'listConversations').mockResolvedValue({
      conversations: [], total: 0, limit: 20, offset: 0,
    });
    render(<AgentHistorySidebar open={true} onClose={() => {}} />);
    const searchInput = await screen.findByLabelText(/buscar/i);

    await userEvent.type(searchInput, 'X');
    await waitFor(
      () => expect(listSpy.mock.calls.some(([a]) => a?.q === 'X')).toBe(true),
      { timeout: 1000 },
    );

    await userEvent.clear(searchInput);
    // After clearing, the most recent call should NOT carry q
    await waitFor(
      () => {
        const last = listSpy.mock.calls[listSpy.mock.calls.length - 1];
        expect(last?.[0]?.q).toBeUndefined();
      },
      { timeout: 1000 },
    );
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
