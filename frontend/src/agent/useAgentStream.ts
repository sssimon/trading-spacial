// ============================================================
// useAgentStream.ts — React hook over streamAgentTurn.
//
// Manages: rolling local transcript (assistant text accumulates from
// text_delta events), in-flight indicator, error surfacing as a final
// assistant message (so the user sees the friendly fallback inline,
// not as a toast), and tool-use status chips.
//
// Phase 2B of epic #400.
// ============================================================

import { useCallback, useRef, useState } from 'react';

import {
  AgentStreamError,
  newConversationId,
  streamAgentTurn,
} from './client';
import type {
  AgentApiMessage,
  AgentContextHints,
  AgentStreamEvent,
  AgentSurface,
} from './types';

export interface ChatMsg {
  role:    'user' | 'assistant';
  text:    string;
  // Inline tool-use chips that render below the bubble while the turn
  // is in flight. The hook clears this on the next user turn.
  tool_chips?: Array<{ tool: string; status: 'pending' | 'ok' | 'error' }>;
}

export interface UseAgentStreamOptions {
  surface: AgentSurface;
}

export interface UseAgentStreamReturn {
  msgs:           ChatMsg[];
  loading:        boolean;
  sendTurn:       (text: string, hints?: AgentContextHints) => Promise<void>;
  resetConversation: () => void;
}

/**
 * Send one user turn, stream the assistant response into the local
 * transcript. The hook keeps the conversation id stable across turns
 * until `resetConversation()` is called (e.g. when the user clicks
 * "nueva conversación" after a `conversation_cap_reached` event).
 */
export function useAgentStream(opts: UseAgentStreamOptions): UseAgentStreamReturn {
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [loading, setLoading] = useState(false);
  const conversationIdRef = useRef<string>(newConversationId());

  const resetConversation = useCallback(() => {
    conversationIdRef.current = newConversationId();
    setMsgs([]);
  }, []);

  const sendTurn = useCallback(
    async (text: string, hints?: AgentContextHints) => {
      if (!text.trim() || loading) return;
      // Snapshot the transcript-up-to-now and append the user turn +
      // an empty assistant placeholder atomically. The placeholder is
      // what the streaming text appends to.
      let prevMsgs: ChatMsg[] = [];
      setMsgs((cur) => {
        prevMsgs = cur;
        return [
          ...cur,
          { role: 'user', text },
          { role: 'assistant', text: '', tool_chips: [] },
        ];
      });
      setLoading(true);

      // Build the API request from the snapshot we just took. The
      // backend rebuilds the system prompt server-side; we only ship
      // the user/assistant transcript.
      const apiMessages: AgentApiMessage[] = [
        ...prevMsgs.map((m) => ({ role: m.role, content: m.text })),
        { role: 'user' as const, content: text },
      ];

      try {
        for await (const ev of streamAgentTurn({
          conversation_id: conversationIdRef.current,
          surface:         opts.surface,
          messages:        apiMessages,
          context_hints:   hints,
        })) {
          applyEvent(ev, setMsgs, resetConversation);
        }
      } catch (err) {
        const userMsg =
          err instanceof AgentStreamError && err.reason === 'agent_disabled'
            ? 'El copiloto no está disponible en este momento.'
            : 'El copiloto está saturado, intenta en unos segundos.';
        setMsgs((cur) => {
          const updated = [...cur];
          const last = updated[updated.length - 1];
          if (last && last.role === 'assistant') {
            updated[updated.length - 1] = { ...last, text: userMsg, tool_chips: [] };
          } else {
            updated.push({ role: 'assistant', text: userMsg });
          }
          return updated;
        });
      } finally {
        setLoading(false);
      }
    },
    [loading, opts.surface, resetConversation],
  );

  return { msgs, loading, sendTurn, resetConversation };
}

// ── pure-ish reducer for one event ─────────────────────────────────────

function applyEvent(
  ev: AgentStreamEvent,
  setMsgs: React.Dispatch<React.SetStateAction<ChatMsg[]>>,
  resetConversation: () => void,
) {
  switch (ev.type) {
    case 'text_delta':
      setMsgs((cur) => {
        const updated = [...cur];
        const last = updated[updated.length - 1];
        if (last && last.role === 'assistant') {
          updated[updated.length - 1] = { ...last, text: last.text + ev.text };
        }
        return updated;
      });
      break;

    case 'tool_use_start':
      setMsgs((cur) => {
        const updated = [...cur];
        const last = updated[updated.length - 1];
        if (last && last.role === 'assistant') {
          updated[updated.length - 1] = {
            ...last,
            tool_chips: [
              ...(last.tool_chips ?? []),
              { tool: ev.tool, status: 'pending' },
            ],
          };
        }
        return updated;
      });
      break;

    case 'tool_use_result':
      setMsgs((cur) => {
        const updated = [...cur];
        const last = updated[updated.length - 1];
        if (last && last.role === 'assistant') {
          const chips = (last.tool_chips ?? []).map((c) =>
            c.tool === ev.tool && c.status === 'pending'
              ? { ...c, status: ev.status }
              : c,
          );
          updated[updated.length - 1] = { ...last, tool_chips: chips };
        }
        return updated;
      });
      break;

    case 'message_end':
      // No-op for the transcript — the streamed text is already in place.
      // Future Phase 5: surface cost_usd / cache hit info to a debug
      // panel for the operator. For now we just let the backend audit
      // the row and move on.
      break;

    case 'error':
      // Replace the placeholder (which is "" so far on a fast-error
      // path) with the friendly user_message. The closed-enum reason
      // is ignored by the user-facing UI; it lives in the audit row.
      setMsgs((cur) => {
        const updated = [...cur];
        const last = updated[updated.length - 1];
        if (last && last.role === 'assistant') {
          updated[updated.length - 1] = { ...last, text: ev.user_message, tool_chips: [] };
        } else {
          updated.push({ role: 'assistant', text: ev.user_message });
        }
        return updated;
      });
      if (ev.reason === 'conversation_cap_reached') {
        // Auto-spin a fresh conversation id so the next user turn
        // doesn't immediately hit the cap again. The user still has
        // to click "send" to use it.
        resetConversation();
      }
      break;
  }
}
