// ============================================================
// agent/client.ts — SSE client for /agent/conversations/{id}/turn.
//
// The browser's built-in EventSource only supports GET requests with
// no custom headers — it cannot send our POST body or honor the JWT
// cookie auth on cross-route navigation. We use fetch() + a
// ReadableStream reader to parse the `text/event-stream` body
// manually. Same wire format the server emits.
//
// Pre-reg §6.2. Phase 2B of epic #400.
// ============================================================

import type {
  AgentStreamEvent,
  AgentTurnRequest,
} from './types';

const BASE_URL = '/api';

function readCsrfCookie(): string {
  const m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : '';
}

export interface StreamTurnOptions extends AgentTurnRequest {
  conversation_id: string;
  signal?:         AbortSignal;
}

export class AgentStreamError extends Error {
  status: number;
  reason: string;
  constructor(status: number, reason: string, message?: string) {
    super(message ?? `agent stream failed: ${status} ${reason}`);
    this.status = status;
    this.reason = reason;
  }
}

/**
 * Stream a single agent turn. Returns an async iterable of typed
 * events; the caller consumes with `for await (const ev of ...)`.
 *
 * The wire is `data: {json}\n\n` frames. We buffer partial chunks and
 * emit one event per frame; the final partial chunk after the stream
 * closes is dropped (it's always empty when the server flushes properly).
 *
 * Throws AgentStreamError on non-2xx HTTP responses. Network errors
 * propagate as fetch's TypeError; the hook catches both and renders a
 * friendly fallback message.
 */
export async function* streamAgentTurn(
  opts: StreamTurnOptions,
): AsyncIterable<AgentStreamEvent> {
  const { conversation_id, signal, ...body } = opts;
  const path = `${BASE_URL}/agent/conversations/${encodeURIComponent(conversation_id)}/turn`;

  const headers: Record<string, string> = {
    'Content-Type':     'application/json',
    'Accept':           'text/event-stream',
    // Mirrors the request<>() wrapper in api.ts — CSRF cookie required
    // on non-safe methods for the AuthMiddleware.
    'X-CSRF-Token':     readCsrfCookie(),
  };

  const resp = await fetch(path, {
    method:      'POST',
    credentials: 'include',
    headers,
    body:        JSON.stringify(body),
    signal,
  });

  if (!resp.ok || !resp.body) {
    // Try to parse the JSON error body for the closed-enum reason.
    let reason = `http_${resp.status}`;
    try {
      const errBody = await resp.json();
      if (typeof errBody?.detail === 'string') reason = errBody.detail;
    } catch {
      // ignore parse failure — keep generic reason
    }
    throw new AgentStreamError(resp.status, reason);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buf = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // SSE frame separator is \n\n. Each frame has lines like
      // "data: {json}". We only emit one event per frame; the server
      // never multi-lines a single event today.
      let sep = buf.indexOf('\n\n');
      while (sep !== -1) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        sep = buf.indexOf('\n\n');
        if (!frame.startsWith('data: ')) continue;
        const json = frame.slice('data: '.length).trim();
        if (!json) continue;
        try {
          yield JSON.parse(json) as AgentStreamEvent;
        } catch (e) {
          // A malformed frame is a server bug — log it but don't kill
          // the iteration. The next frame may be parseable.
          if (typeof console !== 'undefined') {
            console.warn('agent stream: malformed frame', json, e);
          }
        }
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // releaseLock fails on cancelled streams; ignore.
    }
  }
}

/**
 * Generate a short, URL-safe conversation id. The backend validates the
 * shape (alphanumeric + _-, max 128 chars) so we keep this conservative.
 */
export function newConversationId(): string {
  // 8 bytes of crypto-random → 16 hex chars. Good enough for an id
  // that scopes a chat session. The backend persists it verbatim.
  const buf = new Uint8Array(8);
  crypto.getRandomValues(buf);
  return Array.from(buf, (b) => b.toString(16).padStart(2, '0')).join('');
}

// ── Phase 3: proposal confirm ────────────────────────────────────────

/**
 * Result of POST /agent/proposals/{id}/confirm. The frontend uses the
 * `result` enum to drive the UI chip into a terminal state. The wire
 * shape mirrors api/agent/router.py's response model.
 */
export interface ConfirmProposalResult {
  ok:           boolean;
  result:       'ok' | 'state_drift' | 'expired' | 'error';
  http_status?: number;
  idempotent?:  boolean;
}

/**
 * Confirm a signed proposal. The signed_payload is the opaque token
 * received in the `proposal` SSE frame — we echo it back to the server
 * untouched so the HMAC verifies.
 */
export async function confirmAgentProposal(args: {
  proposal_id:    string;
  signed_payload: string;
  signal?:        AbortSignal;
}): Promise<ConfirmProposalResult> {
  const path = `${BASE_URL}/agent/proposals/${encodeURIComponent(args.proposal_id)}/confirm`;
  const resp = await fetch(path, {
    method:      'POST',
    credentials: 'include',
    headers: {
      'Content-Type':  'application/json',
      'X-CSRF-Token':  readCsrfCookie(),
    },
    body:        JSON.stringify({ signed_payload: args.signed_payload }),
    signal:      args.signal,
  });

  if (resp.status === 200) {
    return (await resp.json()) as ConfirmProposalResult;
  }

  // Closed-enum mapping. 409 → state_drift, 410 → expired,
  // anything else → error. We DO read `detail` to keep the http_status
  // visible for debugging, but the UI surfaces only the bucket.
  let detail: string | undefined;
  try {
    const body = await resp.json();
    if (typeof body?.detail === 'string') detail = body.detail;
  } catch {
    /* non-json error body — ignore */
  }
  let bucket: ConfirmProposalResult['result'] = 'error';
  if (resp.status === 409 || detail === 'state_drift') bucket = 'state_drift';
  else if (resp.status === 410 || detail === 'expired') bucket = 'expired';
  return { ok: false, result: bucket, http_status: resp.status };
}
