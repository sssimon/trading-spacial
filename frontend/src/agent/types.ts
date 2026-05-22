// ============================================================
// agent/types.ts — typed events on the wire from
// POST /agent/conversations/{id}/turn.
//
// Mirrors the closed-enum frame types serialized in
// api/agent/streaming.py. The hook (useAgentStream) switches on
// `type` to update local state.
//
// Pre-reg §6.2. Phase 2B of epic #400.
// ============================================================

/**
 * UI-level tool-call status chip rendered inline below an assistant
 * bubble. Exported so future surfaces don't redefine the same shape
 * (PR #405 review nit — single source of truth for the chip state enum).
 */
export interface ToolChip {
  tool:   string;
  status: 'pending' | 'ok' | 'error';
}

// ── Epic #428 history rehydration types ───────────────────────────────
//
// These mirror the pydantic models in api/agent/history.py. The H.3
// REST endpoints return them; the sidebar (H.4) lists them; the
// rehydration hook (H.5) replays them into the dock transcript.

export interface ConversationSummary {
  conversation_id: string;
  title:           string | null;
  surface:         string;
  first_ts:        string;
  last_ts:         string;
  message_count:   number;
  pinned:          boolean;
}

export interface ConversationListResponse {
  conversations: ConversationSummary[];
  total:         number;
  limit:         number;
  offset:        number;
}

/**
 * Proposal record on the REST rehydration response. Distinct from
 * ProposalChip below: this shape has NO `signed_payload` (the HMAC
 * was never persisted past minutes) and the `state` enum uses
 * 'stale' for "rehydrated, never confirmed, TTL not yet passed" —
 * see api/agent/history.py::ProposalRecord docstring + PR #434
 * review issue #6. The rehydration hook converts this to ProposalChip
 * before pushing into the transcript.
 */
export interface ProposalRecord {
  proposal_id: string;
  action:      string;
  args:        Record<string, unknown>;
  expires_at:  string;
  summary:     string;
  state:       'stale' | 'ok' | 'expired' | 'error' | 'conflict';
}

export interface MessageRecord {
  role:       'user' | 'assistant';
  ts:         string;
  content:    string;
  reasoning?: string | null;
  tool_chips: ToolChip[];
  proposals:  ProposalRecord[];
}

export interface ConversationDetailResponse {
  conversation_id: string;
  title:           string | null;
  surface:         string;
  pinned:          boolean;
  messages:        MessageRecord[];
}

export interface AgentTextDelta {
  type: 'text_delta';
  text: string;
}

/**
 * Fase 3a of the multi-provider epic — streaming chain-of-thought
 * chunks emitted by DeepSeek-R1 (and any future reasoning model).
 * Comes through as a separate event channel from `text_delta` so the
 * frontend can render the reasoning in a collapsible panel without
 * mixing it into the assistant's text bubble. Treat as user-visible
 * (the operator that expands the panel sees the model's reasoning).
 */
export interface AgentReasoningDelta {
  type: 'reasoning_delta';
  text: string;
}

export interface AgentToolUseStart {
  type:  'tool_use_start';
  tool:  string;
}

export interface AgentToolUseResult {
  type:    'tool_use_result';
  tool:    string;
  status:  'ok' | 'error';
}

export interface AgentMessageEnd {
  type:        'message_end';
  usage:       {
    input_tokens:                number;
    output_tokens:               number;
    cache_read_input_tokens:     number;
    cache_creation_input_tokens: number;
  };
  stop_reason: string;
  cost_usd:    number;
}

export interface AgentErrorEvent {
  type:          'error';
  reason:        string;   // closed enum from backend; UI shows user_message verbatim
  user_message:  string;
}

/**
 * Phase 5: periodic heartbeat. Emitted by the server when the upstream
 * model + tool pipeline stays silent for ~30s so intermediate proxies
 * (nginx, cloudflare) don't kill the connection as idle. The hook
 * IGNORES this frame entirely — it's a TCP / SSE keepalive only, no
 * UI effect.
 */
export interface AgentKeepaliveEvent {
  type: 'keepalive';
}

/**
 * Phase 3 of epic #400 — emitted when a propose_* tool ran and the
 * server signed a side-effect envelope. The frontend echoes
 * `signed_payload` back verbatim to POST /agent/proposals/{id}/confirm
 * on user click. The model never sees the signed_payload.
 *
 * `summary` is the user-facing one-line description ("Cerrar BTCUSDT
 * LONG #42 a 51,000"). The action/args are exposed for UI grouping
 * and labels but the wire authority is the signed_payload.
 */
export interface AgentProposalEvent {
  type:           'proposal';
  proposal_id:    string;
  signed_payload: string;
  action:         'close_position' | 'reactivate_symbol' | 'apply_tune';
  args:           Record<string, unknown>;
  expires_at:     string;
  summary:        string;
}

export type AgentStreamEvent =
  | AgentTextDelta
  | AgentReasoningDelta
  | AgentToolUseStart
  | AgentToolUseResult
  | AgentProposalEvent
  | AgentMessageEnd
  | AgentErrorEvent
  | AgentKeepaliveEvent;

/**
 * UI-side state of a proposal attached to an assistant message.
 * The hook moves a proposal through:
 *   pending → in_flight → ok | expired | drift | error
 * Terminal states keep the row visible (button disabled) so the user
 * sees the outcome without a toast.
 */
export type ProposalState =
  | 'pending'    // live SSE: actionable, signed_payload is hot
  | 'in_flight'  // confirm POST in progress
  | 'ok'         // confirmed
  | 'expired'    // TTL passed without confirm
  | 'drift'      // confirmed but server state had changed
  | 'error'      // confirm failed
  | 'stale';     // #428 H.3 REST rehydration: never-confirmed, TTL not yet up, but signed_payload is gone — not actionable

export interface ProposalChip {
  proposal_id:    string;
  signed_payload: string;
  action:         AgentProposalEvent['action'];
  args:           AgentProposalEvent['args'];
  expires_at:     string;
  summary:        string;
  state:          ProposalState;
}

// Wire shape of the messages we send in the body. Mirrors
// _AgentMessage in api/agent/router.py.
export interface AgentApiMessage {
  role:    'user' | 'assistant';
  content: string;
}

export type AgentSurface =
  | 'dock'
  | 'symbol_detail'
  | 'kill_switch'
  | 'autotune'
  | 'historial';

export interface AgentContextHints {
  symbol?:      string;
  position_id?: number;
  tune_id?:     number;
}

export interface AgentTurnRequest {
  surface:        AgentSurface;
  messages:       AgentApiMessage[];
  context_hints?: AgentContextHints;
  model?:         string;
}
