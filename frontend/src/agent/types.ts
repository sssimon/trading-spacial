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

export interface AgentTextDelta {
  type: 'text_delta';
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

export type AgentStreamEvent =
  | AgentTextDelta
  | AgentToolUseStart
  | AgentToolUseResult
  | AgentMessageEnd
  | AgentErrorEvent;

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
