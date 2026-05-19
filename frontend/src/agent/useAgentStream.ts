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

import { useCallback, useEffect, useRef, useState } from 'react';

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
  ToolChip,
} from './types';

export interface ChatMsg {
  role:    'user' | 'assistant';
  text:    string;
  // Inline tool-use chips that render below the bubble while the turn
  // is in flight. The hook clears this on the next user turn.
  tool_chips?: ToolChip[];
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
  // Parallel ref kept in sync with msgs so sendTurn can read the latest
  // transcript without depending on closure freshness across renders.
  // Phase 2B review issue 4: avoids the StrictMode double-invocation
  // trap of assigning into a closed-over variable from inside a
  // functional setState. Verified by test_send_turn_second_turn_carries_full_transcript.
  const msgsRef = useRef<ChatMsg[]>([]);
  useEffect(() => {
    msgsRef.current = msgs;
  }, [msgs]);

  const resetConversation = useCallback(() => {
    conversationIdRef.current = newConversationId();
    setMsgs([]);
    msgsRef.current = [];
  }, []);

  const sendTurn = useCallback(
    async (text: string, hints?: AgentContextHints) => {
      if (!text.trim() || loading) return;
      // Read the transcript from the ref (always fresh across re-renders);
      // build the API messages BEFORE we append the user turn so the
      // request payload mirrors the on-screen state at submit-time.
      const transcriptSoFar = msgsRef.current;
      const apiMessages: AgentApiMessage[] = [
        ...transcriptSoFar.map((m) => ({ role: m.role, content: m.text })),
        { role: 'user' as const, content: text },
      ];

      // Now append the user turn + empty assistant placeholder. The
      // placeholder is what the streaming text appends to.
      setMsgs((cur) => [
        ...cur,
        { role: 'user', text },
        { role: 'assistant', text: '', tool_chips: [] },
      ]);
      setLoading(true);

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
