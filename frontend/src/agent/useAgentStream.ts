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
  confirmAgentProposal,
  getConversationMessages,
  newConversationId,
  streamAgentTurn,
} from './client';
import type {
  AgentApiMessage,
  AgentContextHints,
  AgentProposalEvent,
  AgentStreamEvent,
  AgentSurface,
  MessageRecord,
  ProposalChip,
  ProposalRecord,
  ProposalState,
  ToolChip,
} from './types';

export interface ChatMsg {
  role:    'user' | 'assistant';
  text:    string;
  // Inline tool-use chips that render below the bubble while the turn
  // is in flight. The hook clears this on the next user turn.
  tool_chips?: ToolChip[];
  // Phase 3: signed proposal envelopes attached to this assistant
  // message. The dock renders an amber confirm button per chip.
  proposals?:  ProposalChip[];
  // Fase 3a of the multi-provider epic: streaming chain-of-thought
  // text from DeepSeek-R1. Kept distinct from `text` so the UI can
  // render it in a collapsible panel that the user opts into. Default
  // closed — most users want the answer, not the chain-of-thought.
  reasoning?: string;
}

export interface UseAgentStreamOptions {
  surface: AgentSurface;
}

export interface GreetingResult {
  ok:   boolean;
  text: string;
}

export interface UseAgentStreamReturn {
  msgs:              ChatMsg[];
  loading:           boolean;
  /** True while loadConversation's fetch is in flight. The dock gates
   *  its input on (loading || hydrating) so a 2-second history fetch
   *  doesn't let the user fire a sendTurn into a transcript that's
   *  about to be replaced. PR #436 review issue #2. */
  hydrating:         boolean;
  sendTurn:          (text: string, hints?: AgentContextHints) => Promise<void>;
  resetConversation: () => void;
  confirmProposal:   (proposal_id: string) => Promise<void>;
  /** H.5 rehydration: pull a past conversation from the REST history
   *  endpoint, replace the local transcript with its messages, and
   *  point conversationIdRef at the loaded id so the next sendTurn
   *  continues the conversation instead of starting a new one. */
  loadConversation:  (conversation_id: string) => Promise<void>;
  /** #528 follow-up: fire a one-shot turn whose response NEVER enters
   *  the rolling transcript and NEVER carries into the next sendTurn's
   *  apiMessages. Used for the proactive greeting bubble of the
   *  SymbolDetail copilot — the operator never typed anything to elicit
   *  it, so it must not appear as a phantom assistant turn in the chat
   *  history. Uses a fresh conversation_id per call. text_delta events
   *  accumulate and stream via `onUpdate`. Tool calls, proposals, and
   *  reasoning channels are intentionally ignored — greeting is text. */
  streamGreeting:    (
    prompt:   string,
    hints:    AgentContextHints | undefined,
    onUpdate: (text: string) => void,
  ) => Promise<GreetingResult>;
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
  const [hydrating, setHydrating] = useState(false);
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

  // PR #436 review issue #1: refs mirror loading / hydrating so the
  // gating in loadConversation + sendTurn can read fresh state across
  // callback re-creations without forcing dep arrays to track them
  // (which would cause the callback to re-create on every state flip).
  const loadingRef   = useRef(false);
  const hydratingRef = useRef(false);
  useEffect(() => { loadingRef.current   = loading;   }, [loading]);
  useEffect(() => { hydratingRef.current = hydrating; }, [hydrating]);

  // Load preemption token: each loadConversation invocation increments
  // this ref before the fetch and re-checks after; if a newer load
  // started in the interim, the stale fetch's result is discarded.
  const loadTokenRef = useRef(0);

  const resetConversation = useCallback(() => {
    conversationIdRef.current = newConversationId();
    setMsgs([]);
    msgsRef.current = [];
  }, []);

  const sendTurn = useCallback(
    async (text: string, hints?: AgentContextHints) => {
      // PR #436 review issue #1: hydratingRef.current short-circuits a
      // type-and-Enter that races with a sidebar hydration fetch. Without
      // it, the sendTurn captures the pre-hydration msgs + conversationId
      // and the SSE bleed into the hydrated transcript when the fetch
      // resolves mid-stream.
      if (!text.trim() || loading || hydratingRef.current) return;
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

  const confirmProposal = useCallback(async (proposal_id: string) => {
    // Find the proposal across all messages (Phase 3: one proposal per
    // tool call, but the loop could in theory emit several in one turn).
    let found: ProposalChip | null = null;
    for (const m of msgsRef.current) {
      const p = m.proposals?.find((q) => q.proposal_id === proposal_id);
      if (p) {
        found = p;
        break;
      }
    }
    if (!found || found.state !== 'pending') return;

    setMsgs((cur) => updateProposalState(cur, proposal_id, 'in_flight'));

    try {
      const res = await confirmAgentProposal({
        proposal_id,
        signed_payload: found.signed_payload,
      });
      const nextState: ProposalState =
        res.result === 'ok'          ? 'ok'      :
        res.result === 'expired'     ? 'expired' :
        res.result === 'state_drift' ? 'drift'   :
        'error';
      setMsgs((cur) => updateProposalState(cur, proposal_id, nextState));
    } catch {
      // Network / fetch-level failure. Drop the chip into the error
      // terminal state — the user can re-ask the model to try again,
      // which produces a fresh signed envelope.
      setMsgs((cur) => updateProposalState(cur, proposal_id, 'error'));
    }
  }, []);

  const loadConversation = useCallback(async (conversation_id: string) => {
    // PR #436 review issue #1: refuse to hydrate during an in-flight
    // turn. Otherwise the resolving SSE text_delta events would
    // continue to append to msgs[length-1] of the just-replaced
    // hydrated transcript, mixing two unrelated conversations.
    if (loadingRef.current) return;

    // Preemption token: any newer loadConversation call invalidates this
    // one's setMsgs at resolve time. Without it, two clicks in quick
    // succession on rows A then B would race and either order could win.
    const token = ++loadTokenRef.current;
    setHydrating(true);
    try {
      const detail = await getConversationMessages(conversation_id);
      if (token !== loadTokenRef.current) return;   // newer load preempted
      const hydrated: ChatMsg[] = detail.messages.map(_recordToChatMsg);
      conversationIdRef.current = conversation_id;
      setMsgs(hydrated);
      msgsRef.current = hydrated;
    } catch (err) {
      if (typeof console !== 'undefined') {
        console.warn('loadConversation failed', conversation_id, err);
      }
    } finally {
      // Only clear hydrating if we're still the latest token — otherwise
      // a newer load is still in flight and should keep the flag set.
      if (token === loadTokenRef.current) setHydrating(false);
    }
  }, []);

  const streamGreeting = useCallback(
    async (
      prompt: string,
      hints: AgentContextHints | undefined,
      onUpdate: (text: string) => void,
    ): Promise<GreetingResult> => {
      // Fresh conversation id — greeting must NOT inherit or contaminate
      // the operator's rolling conversation. This call is fire-and-collect:
      // text accumulates locally, never enters `msgs`, never carries into
      // the next sendTurn's apiMessages.
      const greetingConvId = newConversationId();
      const apiMessages: AgentApiMessage[] = [
        { role: 'user' as const, content: prompt },
      ];
      let accumulated = '';
      try {
        for await (const ev of streamAgentTurn({
          conversation_id: greetingConvId,
          surface:         opts.surface,
          messages:        apiMessages,
          context_hints:   hints,
        })) {
          if (ev.type === 'text_delta') {
            accumulated += ev.text;
            onUpdate(accumulated);
          } else if (ev.type === 'error') {
            // Treat any backend error as a failed enrichment — the caller
            // falls back to the template greeting.
            return { ok: false, text: accumulated };
          }
          // tool_use_*, proposal, reasoning_delta, keepalive, message_end:
          // intentionally ignored. Greeting is text; proposals/tools would
          // be a UX surprise in a bubble the operator never asked for.
        }
        return { ok: accumulated.length > 0, text: accumulated };
      } catch {
        // Network / stream-level failure → fallback to template.
        return { ok: false, text: accumulated };
      }
    },
    [opts.surface],
  );

  return {
    msgs, loading, hydrating, sendTurn, resetConversation, confirmProposal,
    loadConversation, streamGreeting,
  };
}

// ── REST → transcript shape ─────────────────────────────────────────────

function _recordToChatMsg(rec: MessageRecord): ChatMsg {
  return {
    role:       rec.role,
    text:       rec.content,
    reasoning:  rec.reasoning ?? undefined,
    tool_chips: rec.tool_chips ?? [],
    proposals:  (rec.proposals ?? [])
      .map(_proposalRecordToChipOrNull)
      .filter((c): c is ProposalChip => c !== null),
  };
}

const _KNOWN_PROPOSAL_ACTIONS: ReadonlySet<AgentProposalEvent['action']> =
  new Set(['close_position', 'reactivate_symbol', 'apply_tune']);

function _proposalRecordToChipOrNull(p: ProposalRecord): ProposalChip | null {
  // PR #436 review issue #3: ProposalRecord.action is `string` (open)
  // → ProposalChip.action is a closed Literal. A naive `as` cast would
  // lie if the backend adds a new action without bumping frontend
  // types. Filter unknown actions out + log a warning so the gap is
  // visible in dev/staging telemetry. The dock just won't show a chip
  // for that proposal — better than rendering a button that switches
  // on the closed enum and silently misses the case.
  if (!_KNOWN_PROPOSAL_ACTIONS.has(p.action as AgentProposalEvent['action'])) {
    if (typeof console !== 'undefined') {
      console.warn(
        'loadConversation: unknown proposal action — chip skipped',
        p.action, p.proposal_id,
      );
    }
    return null;
  }

  // The REST shape doesn't carry signed_payload (HMAC TTL minutes vs
  // 90-day retention — it'd be storing an expired credential). The
  // confirm button is disabled for every state except 'pending', and
  // REST rehydration never returns 'pending' (it returns 'stale' for
  // the never-confirmed-not-yet-expired case — pre-reg D.6 + #434
  // review issue #6). So signed_payload is unreachable from the UI
  // and we put an empty string. If a future code path tries to use
  // it, the HMAC verify on the backend will reject — defense in depth.
  return {
    proposal_id:    p.proposal_id,
    signed_payload: '',
    action:         p.action as AgentProposalEvent['action'],
    args:           p.args,
    expires_at:     p.expires_at,
    summary:        p.summary,
    state:          p.state,
  };
}

/**
 * Replace one proposal's state across the transcript. Pure (returns a
 * new array) so it composes with setMsgs.
 */
function updateProposalState(
  cur: ChatMsg[],
  proposal_id: string,
  next: ProposalState,
): ChatMsg[] {
  return cur.map((m) => {
    if (!m.proposals?.length) return m;
    const idx = m.proposals.findIndex((p) => p.proposal_id === proposal_id);
    if (idx < 0) return m;
    const updated = [...m.proposals];
    updated[idx] = { ...updated[idx], state: next };
    return { ...m, proposals: updated };
  });
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

    case 'reasoning_delta':
      // Fase 3a of the multi-provider epic: accumulate into the
      // `reasoning` field on the same assistant message. NEVER appends
      // to `text` — the two channels are kept distinct so the UI can
      // render the reasoning in a collapsible panel without
      // contaminating the assistant's text bubble.
      setMsgs((cur) => {
        const updated = [...cur];
        const last = updated[updated.length - 1];
        if (last && last.role === 'assistant') {
          updated[updated.length - 1] = {
            ...last,
            reasoning: (last.reasoning ?? '') + ev.text,
          };
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

    case 'proposal':
      // Phase 3: a propose_* tool ran. Attach the signed envelope to
      // the current assistant message. The dock will render an amber
      // confirm button driven by the proposal's state.
      setMsgs((cur) => {
        const updated = [...cur];
        const last = updated[updated.length - 1];
        if (last && last.role === 'assistant') {
          const chip: ProposalChip = {
            proposal_id:    ev.proposal_id,
            signed_payload: ev.signed_payload,
            action:         ev.action,
            args:           ev.args,
            expires_at:     ev.expires_at,
            summary:        ev.summary,
            state:          'pending',
          };
          updated[updated.length - 1] = {
            ...last,
            proposals: [...(last.proposals ?? []), chip],
          };
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

    case 'keepalive':
      // Phase 5: TCP heartbeat from the server during long tool calls.
      // Purely a proxy-keepalive signal — no UI effect.
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
