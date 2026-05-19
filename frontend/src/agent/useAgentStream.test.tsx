// ============================================================
// useAgentStream.test.tsx — covers the streaming reducer.
//
// We don't need a full DOM render — we test the hook in isolation by
// stubbing the fetch + ReadableStream pair that streamAgentTurn reads.
// The reducer's job is: accumulate text_deltas into the last assistant
// bubble, attach tool_use chips, surface error events as the assistant
// text, and clear loading on completion.
// ============================================================

import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useAgentStream } from './useAgentStream';

// ── Helpers ────────────────────────────────────────────────────────────


function encode(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}

/** Builds a ReadableStream that emits the supplied SSE frames in order.
 *  Each entry is a single frame's `data: {json}` — we add the terminator. */
function sseReadable(frames: object[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const f of frames) {
        controller.enqueue(encode(`data: ${JSON.stringify(f)}\n\n`));
      }
      controller.close();
    },
  });
}

function stubFetchOnce(stream: ReadableStream<Uint8Array>, status = 200) {
  const resp = new Response(stream, {
    status,
    headers: { 'Content-Type': 'text/event-stream' },
  });
  // @ts-expect-error — overriding global for the test
  global.fetch = vi.fn().mockResolvedValueOnce(resp);
}

function stubFetchError(status: number, detail: string) {
  // @ts-expect-error — overriding global for the test
  global.fetch = vi.fn().mockResolvedValueOnce(
    new Response(JSON.stringify({ detail }), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  );
}


// ── Test environment setup ─────────────────────────────────────────────


beforeEach(() => {
  // jsdom's document.cookie default is empty — CSRF wrapper reads it.
  document.cookie = 'csrf_token=t';
  // Vitest's jsdom env doesn't ship crypto.getRandomValues by default
  // in older versions; ensure it's there for newConversationId().
  if (!globalThis.crypto?.getRandomValues) {
    Object.defineProperty(globalThis, 'crypto', {
      value: {
        getRandomValues: (buf: Uint8Array) => {
          for (let i = 0; i < buf.length; i++) buf[i] = i;
          return buf;
        },
      },
    });
  }
});

afterEach(() => {
  vi.restoreAllMocks();
});


// ── Tests ──────────────────────────────────────────────────────────────


describe('useAgentStream', () => {
  it('accumulates text_delta chunks into the assistant bubble', async () => {
    stubFetchOnce(sseReadable([
      { type: 'text_delta', text: 'Hola ' },
      { type: 'text_delta', text: 'mundo' },
      {
        type: 'message_end',
        usage: { input_tokens: 10, output_tokens: 5, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
        stop_reason: 'end_turn',
        cost_usd: 0.0001,
      },
    ]));
    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));

    await act(async () => {
      await result.current.sendTurn('saluda');
    });

    expect(result.current.loading).toBe(false);
    expect(result.current.msgs).toHaveLength(2);
    expect(result.current.msgs[0]).toEqual({ role: 'user', text: 'saluda' });
    expect(result.current.msgs[1].role).toBe('assistant');
    expect(result.current.msgs[1].text).toBe('Hola mundo');
  });

  it('tracks tool_use_start / tool_use_result chips on the bubble', async () => {
    stubFetchOnce(sseReadable([
      { type: 'tool_use_start', tool: 'get_positions' },
      { type: 'tool_use_result', tool: 'get_positions', status: 'ok' },
      { type: 'text_delta', text: 'Tienes 1 posición.' },
      { type: 'message_end', usage: { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }, stop_reason: 'end_turn', cost_usd: 0 },
    ]));
    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));

    await act(async () => {
      await result.current.sendTurn('qué tengo');
    });

    const asst = result.current.msgs[1];
    expect(asst.tool_chips).toEqual([{ tool: 'get_positions', status: 'ok' }]);
    expect(asst.text).toBe('Tienes 1 posición.');
  });

  it('replaces the placeholder with user_message on an error event', async () => {
    stubFetchOnce(sseReadable([
      { type: 'error', reason: 'upstream', user_message: 'El copiloto está saturado, intenta en unos segundos.' },
    ]));
    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));

    await act(async () => {
      await result.current.sendTurn('hola');
    });

    const asst = result.current.msgs[1];
    expect(asst.text).toBe('El copiloto está saturado, intenta en unos segundos.');
    expect(asst.tool_chips).toEqual([]);
  });

  it('shows a friendly fallback on a non-2xx response without leaking the closed-enum reason verbatim', async () => {
    stubFetchError(503, 'agent_disabled');
    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));

    await act(async () => {
      await result.current.sendTurn('hola');
    });

    const asst = result.current.msgs[1];
    // The hook renders a user-facing translation of the closed-enum
    // reason — the raw reason string never reaches the bubble.
    expect(asst.text).toBe('El copiloto no está disponible en este momento.');
  });

  it('rolls a new conversation id on conversation_cap_reached', async () => {
    // First call returns the cap-reached error.
    const stream1 = sseReadable([
      { type: 'error', reason: 'conversation_cap_reached', user_message: 'cap' },
    ]);
    // Second call should succeed because resetConversation rolled a new id.
    const stream2 = sseReadable([
      { type: 'text_delta', text: 'fresh' },
      { type: 'message_end', usage: { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }, stop_reason: 'end_turn', cost_usd: 0 },
    ]);

    let callCount = 0;
    const idsSent: string[] = [];
    // @ts-expect-error — overriding global
    global.fetch = vi.fn().mockImplementation((url: string) => {
      // url shape: /api/agent/conversations/{id}/turn
      const match = url.match(/conversations\/([^/]+)\/turn/);
      if (match) idsSent.push(match[1]);
      callCount += 1;
      if (callCount === 1) {
        return Promise.resolve(new Response(stream1, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }));
      }
      return Promise.resolve(new Response(stream2, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }));
    });

    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));

    await act(async () => {
      await result.current.sendTurn('msg1');
    });
    await act(async () => {
      await result.current.sendTurn('msg2');
    });

    expect(idsSent).toHaveLength(2);
    // The cap rolled a fresh id, so the second call's id is different.
    expect(idsSent[0]).not.toBe(idsSent[1]);
  });

  it('sends the full prior transcript on the second turn of a conversation', async () => {
    // PR #405 review issue 4: the previous implementation captured
    // prevMsgs inside a functional setState callback. Under StrictMode
    // double-invocation, the assignment could race; in production it
    // worked but the pattern was fragile. The refactor reads from a
    // msgsRef parallel state. This test verifies the second turn
    // carries the first user msg + first assistant msg in its body.

    // First turn — fetch returns a short assistant response.
    const stream1 = sseReadable([
      { type: 'text_delta', text: 'Tienes ' },
      { type: 'text_delta', text: '2 posiciones.' },
      { type: 'message_end', usage: { input_tokens: 10, output_tokens: 4, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }, stop_reason: 'end_turn', cost_usd: 0 },
    ]);
    const stream2 = sseReadable([
      { type: 'text_delta', text: 'ok' },
      { type: 'message_end', usage: { input_tokens: 0, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }, stop_reason: 'end_turn', cost_usd: 0 },
    ]);

    const bodiesSent: any[] = [];
    // @ts-expect-error — overriding global
    global.fetch = vi.fn().mockImplementation((_url: string, init: RequestInit) => {
      bodiesSent.push(JSON.parse(init.body as string));
      const streamForCall = bodiesSent.length === 1 ? stream1 : stream2;
      return Promise.resolve(new Response(streamForCall, {
        status: 200, headers: { 'Content-Type': 'text/event-stream' },
      }));
    });

    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));

    // Turn 1: user asks; assistant streams "Tienes 2 posiciones."
    await act(async () => {
      await result.current.sendTurn('qué posiciones tengo');
    });
    // Turn 2: user follows up. The wire body must carry the FULL prior
    // transcript (turn 1's user + turn 1's assistant), not just the
    // new question.
    await act(async () => {
      await result.current.sendTurn('y cuál vale más');
    });

    expect(bodiesSent).toHaveLength(2);
    // First call: only the first user turn in messages.
    expect(bodiesSent[0].messages).toEqual([
      { role: 'user', content: 'qué posiciones tengo' },
    ]);
    // Second call: prior user + prior assistant (with the accumulated
    // text from the stream) + new user msg.
    expect(bodiesSent[1].messages).toEqual([
      { role: 'user',      content: 'qué posiciones tengo' },
      { role: 'assistant', content: 'Tienes 2 posiciones.' },
      { role: 'user',      content: 'y cuál vale más' },
    ]);
  });

  it('refuses to send while a previous turn is in flight', async () => {
    // Create a stream that we control — the second sendTurn fires before
    // the first resolves.
    let releaseFirst: () => void = () => {};
    const firstStreamPromise = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    const stream1 = new ReadableStream<Uint8Array>({
      async start(controller) {
        await firstStreamPromise;
        controller.enqueue(encode(`data: ${JSON.stringify({
          type: 'message_end',
          usage: { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
          stop_reason: 'end_turn',
          cost_usd: 0,
        })}\n\n`));
        controller.close();
      },
    });
    // @ts-expect-error — overriding global
    global.fetch = vi.fn().mockResolvedValueOnce(
      new Response(stream1, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }),
    );

    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));

    // Don't await — we want sendTurn1 in flight when sendTurn2 fires.
    let firstPromise: Promise<void>;
    act(() => {
      firstPromise = result.current.sendTurn('msg1');
    });
    await waitFor(() => expect(result.current.loading).toBe(true));

    // sendTurn2 should no-op (returns immediately) because loading=true.
    await act(async () => {
      await result.current.sendTurn('msg2');
    });

    // Only one user message in the transcript — the second was rejected.
    const userMsgs = result.current.msgs.filter((m) => m.role === 'user');
    expect(userMsgs.map((m) => m.text)).toEqual(['msg1']);

    // Release the first stream and let it complete cleanly.
    releaseFirst();
    await act(async () => {
      await firstPromise!;
    });
  });
});
