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

  it('accumulates reasoning_delta into reasoning channel, never into text', async () => {
    // Fase 3a of multi-provider epic: DeepSeek-R1 emits reasoning_delta
    // events streaming the chain-of-thought. The hook must:
    //   1. Accumulate into msg.reasoning, NOT msg.text
    //   2. Keep text streaming working in parallel (interleaved)
    //   3. NEVER mix the two channels
    stubFetchOnce(sseReadable([
      { type: 'reasoning_delta', text: 'Veo que ' },
      { type: 'reasoning_delta', text: 'el WR20 ' },
      { type: 'text_delta', text: 'Recomiendo ' },
      { type: 'reasoning_delta', text: 'está alto.' },
      { type: 'text_delta', text: 'mantener.' },
      {
        type: 'message_end',
        usage: { input_tokens: 50, output_tokens: 100, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
        stop_reason: 'end_turn',
        cost_usd: 0.0001,
      },
    ]));
    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));

    await act(async () => {
      await result.current.sendTurn('razona');
    });

    const asst = result.current.msgs[1];
    expect(asst.role).toBe('assistant');
    // Text channel: only the final answer (no reasoning leaked in).
    expect(asst.text).toBe('Recomiendo mantener.');
    // Reasoning channel: full chain-of-thought, in arrival order.
    expect(asst.reasoning).toBe('Veo que el WR20 está alto.');
    // Cross-channel pollution check.
    expect(asst.text).not.toContain('Veo');
    expect(asst.reasoning).not.toContain('Recomiendo');
  });

  it('accumulates reasoning across multi-hop turns into a single channel', async () => {
    // PR #414 review pickup 2: a multi-hop turn with reasoning before
    // each tool_use should accumulate ALL reasoning chunks into ONE
    // msg.reasoning field (plain concat, no separator).
    //
    // If a future PR wants per-hop separation (msg.reasoning_per_hop:
    // string[]), this test should fail and be updated deliberately.
    // For now, the contract is "one flat string per assistant message".
    stubFetchOnce(sseReadable([
      // Hop 1 reasoning
      { type: 'reasoning_delta', text: 'Primero ' },
      { type: 'reasoning_delta', text: 'necesito ver las posiciones. ' },
      { type: 'tool_use_start', tool: 'get_positions' },
      { type: 'tool_use_result', tool: 'get_positions', status: 'ok' },
      // Hop 2 reasoning (after tool result)
      { type: 'reasoning_delta', text: 'Veo BTCUSDT en verde. ' },
      { type: 'reasoning_delta', text: 'Recomiendo mantener.' },
      { type: 'text_delta', text: 'Mantén tu posición de BTC.' },
      {
        type: 'message_end',
        usage: { input_tokens: 200, output_tokens: 50, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
        stop_reason: 'end_turn',
        cost_usd: 0.0005,
      },
    ]));
    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));

    await act(async () => {
      await result.current.sendTurn('analiza');
    });

    const asst = result.current.msgs[1];
    expect(asst.role).toBe('assistant');
    // Text channel only has the final answer.
    expect(asst.text).toBe('Mantén tu posición de BTC.');
    // Reasoning channel: all chunks concatenated in arrival order,
    // no separators between hops. Operator sees the full chain-of-
    // thought as one continuous block.
    expect(asst.reasoning).toBe(
      'Primero necesito ver las posiciones. Veo BTCUSDT en verde. Recomiendo mantener.'
    );
    // Tool chip rendered (provider exercised the dispatch path).
    expect(asst.tool_chips).toHaveLength(1);
    expect(asst.tool_chips![0]).toEqual({ tool: 'get_positions', status: 'ok' });
  });

  it('does not initialize reasoning when no reasoning_delta arrives', async () => {
    // For non-reasoning models (Anthropic, deepseek-chat), the message
    // never gets a reasoning field. The UI conditional `if (reasoning &&
    // reasoning.length > 0)` keeps the panel hidden.
    stubFetchOnce(sseReadable([
      { type: 'text_delta', text: 'Hola mundo' },
      {
        type: 'message_end',
        usage: { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
        stop_reason: 'end_turn',
        cost_usd: 0,
      },
    ]));
    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));

    await act(async () => {
      await result.current.sendTurn('hi');
    });

    expect(result.current.msgs[1].text).toBe('Hola mundo');
    expect(result.current.msgs[1].reasoning).toBeUndefined();
  });

  it('treats keepalive frames as a no-op (Phase 5 heartbeat ignored)', async () => {
    // Two keepalive frames interleaved with text — the hook should
    // skip them silently and accumulate text as if they weren't there.
    // No console warnings, no message corruption.
    stubFetchOnce(sseReadable([
      { type: 'text_delta', text: 'Hola ' },
      { type: 'keepalive' },
      { type: 'text_delta', text: 'mundo' },
      { type: 'keepalive' },
      {
        type: 'message_end',
        usage: { input_tokens: 5, output_tokens: 2, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
        stop_reason: 'end_turn',
        cost_usd: 0.0001,
      },
    ]));
    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));

    await act(async () => {
      await result.current.sendTurn('saluda');
    });

    expect(result.current.msgs).toHaveLength(2);
    expect(result.current.msgs[1].role).toBe('assistant');
    expect(result.current.msgs[1].text).toBe('Hola mundo');
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

  // ── Phase 3 of #400 — signed-proposal flow ─────────────────────────

  it('attaches a proposal chip to the assistant bubble on a proposal event', async () => {
    stubFetchOnce(sseReadable([
      { type: 'text_delta', text: 'Listo, te dejo el confirm.' },
      {
        type:           'proposal',
        proposal_id:    'prop_abc123',
        signed_payload: 'mac.payload',
        action:         'close_position',
        args:           { position_id: 7, exit_price: 51000 },
        expires_at:     '2030-01-01T00:00:00+00:00',
        summary:        'Cerrar BTCUSDT LONG #7 a 51,000',
      },
      {
        type: 'message_end',
        usage: { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
        stop_reason: 'end_turn', cost_usd: 0,
      },
    ]));
    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));

    await act(async () => {
      await result.current.sendTurn('cierra #7');
    });

    const asst = result.current.msgs[1];
    expect(asst.proposals).toHaveLength(1);
    expect(asst.proposals![0]).toMatchObject({
      proposal_id:    'prop_abc123',
      signed_payload: 'mac.payload',
      action:         'close_position',
      summary:        'Cerrar BTCUSDT LONG #7 a 51,000',
      state:          'pending',
    });
  });

  it('confirmProposal transitions pending → ok on 200, sending signed_payload verbatim', async () => {
    // Turn 1 fetch: stream with a proposal event.
    const streamWithProposal = sseReadable([
      {
        type:           'proposal',
        proposal_id:    'prop_xyz',
        signed_payload: 'opaque.token.value',
        action:         'close_position',
        args:           { position_id: 1, exit_price: 100 },
        expires_at:     '2030-01-01T00:00:00+00:00',
        summary:        'Cerrar #1 a 100',
      },
      {
        type: 'message_end',
        usage: { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
        stop_reason: 'end_turn', cost_usd: 0,
      },
    ]);

    let confirmBody: any = null;
    let confirmUrl = '';
    // @ts-expect-error — overriding global
    global.fetch = vi.fn().mockImplementation((url: string, init: RequestInit) => {
      if (url.endsWith('/turn')) {
        return Promise.resolve(new Response(streamWithProposal, {
          status: 200, headers: { 'Content-Type': 'text/event-stream' },
        }));
      }
      // The confirm POST.
      confirmUrl = url;
      confirmBody = JSON.parse(init.body as string);
      return Promise.resolve(new Response(
        JSON.stringify({ ok: true, result: 'ok', idempotent: false }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ));
    });

    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));
    await act(async () => {
      await result.current.sendTurn('cierra');
    });

    // pre-condition
    expect(result.current.msgs[1].proposals![0].state).toBe('pending');

    await act(async () => {
      await result.current.confirmProposal('prop_xyz');
    });

    // The confirm POST hit /api/agent/proposals/prop_xyz/confirm with the
    // exact signed_payload from the SSE frame.
    expect(confirmUrl).toContain('/api/agent/proposals/prop_xyz/confirm');
    expect(confirmBody).toEqual({ signed_payload: 'opaque.token.value' });

    // And the UI chip landed in the ok terminal state.
    expect(result.current.msgs[1].proposals![0].state).toBe('ok');
  });

  it('confirmProposal lands in drift on 409', async () => {
    const stream = sseReadable([
      {
        type:           'proposal',
        proposal_id:    'prop_drift',
        signed_payload: 'm.p',
        action:         'close_position',
        args:           { position_id: 1, exit_price: 1 },
        expires_at:     '2030-01-01T00:00:00+00:00',
        summary:        'x',
      },
      { type: 'message_end', usage: { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }, stop_reason: 'end_turn', cost_usd: 0 },
    ]);
    // @ts-expect-error — overriding global
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.endsWith('/turn')) {
        return Promise.resolve(new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }));
      }
      return Promise.resolve(new Response(
        JSON.stringify({ detail: 'state_drift' }),
        { status: 409, headers: { 'Content-Type': 'application/json' } },
      ));
    });

    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));
    await act(async () => { await result.current.sendTurn('cierra'); });
    await act(async () => { await result.current.confirmProposal('prop_drift'); });

    expect(result.current.msgs[1].proposals![0].state).toBe('drift');
  });

  it('confirmProposal lands in expired on 410', async () => {
    const stream = sseReadable([
      {
        type:           'proposal',
        proposal_id:    'prop_exp',
        signed_payload: 'm.p',
        action:         'close_position',
        args:           { position_id: 1, exit_price: 1 },
        expires_at:     '2020-01-01T00:00:00+00:00',
        summary:        'x',
      },
      { type: 'message_end', usage: { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }, stop_reason: 'end_turn', cost_usd: 0 },
    ]);
    // @ts-expect-error — overriding global
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.endsWith('/turn')) {
        return Promise.resolve(new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }));
      }
      return Promise.resolve(new Response(
        JSON.stringify({ detail: 'expired' }),
        { status: 410, headers: { 'Content-Type': 'application/json' } },
      ));
    });

    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));
    await act(async () => { await result.current.sendTurn('cierra'); });
    await act(async () => { await result.current.confirmProposal('prop_exp'); });

    expect(result.current.msgs[1].proposals![0].state).toBe('expired');
  });

  it('confirmProposal is a no-op when called after a terminal state', async () => {
    // After ok, a second confirmProposal must NOT issue another POST.
    const stream = sseReadable([
      {
        type:           'proposal',
        proposal_id:    'prop_once',
        signed_payload: 'm.p',
        action:         'close_position',
        args:           { position_id: 1, exit_price: 1 },
        expires_at:     '2030-01-01T00:00:00+00:00',
        summary:        'x',
      },
      { type: 'message_end', usage: { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }, stop_reason: 'end_turn', cost_usd: 0 },
    ]);
    const fetchCalls: string[] = [];
    // @ts-expect-error — overriding global
    global.fetch = vi.fn().mockImplementation((url: string) => {
      fetchCalls.push(url);
      if (url.endsWith('/turn')) {
        return Promise.resolve(new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }));
      }
      return Promise.resolve(new Response(
        JSON.stringify({ ok: true, result: 'ok' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ));
    });

    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));
    await act(async () => { await result.current.sendTurn('cierra'); });
    await act(async () => { await result.current.confirmProposal('prop_once'); });
    expect(result.current.msgs[1].proposals![0].state).toBe('ok');
    const callsAfterFirst = fetchCalls.length;

    // Second click — hook must short-circuit (state != 'pending').
    await act(async () => { await result.current.confirmProposal('prop_once'); });
    expect(fetchCalls.length).toBe(callsAfterFirst);  // no new POST
    expect(result.current.msgs[1].proposals![0].state).toBe('ok');
  });

  it('confirmProposal is a no-op for an unknown proposal_id', async () => {
    // No stream needed — we never reach the agent at all.
    let calls = 0;
    // @ts-expect-error — overriding global
    global.fetch = vi.fn().mockImplementation(() => { calls += 1; return Promise.resolve(new Response('')); });

    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));
    await act(async () => {
      await result.current.confirmProposal('prop_does_not_exist');
    });
    expect(calls).toBe(0);
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

  // ── H.5: loadConversation hydration ────────────────────────────────

  function stubHistoryJson(body: object, status = 200) {
    // @ts-expect-error — overriding global for the test
    global.fetch = vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  }

  it('loadConversation replaces msgs with the hydrated transcript', async () => {
    stubHistoryJson({
      conversation_id: 'conv-hydrate',
      title:           'past chat',
      surface:         'dock',
      pinned:          false,
      messages: [
        { role: 'user',      ts: '2026-05-22T08:00:00Z', content: 'hola',
          reasoning: null, tool_chips: [], proposals: [] },
        { role: 'assistant', ts: '2026-05-22T08:00:01Z', content: 'qué tal',
          reasoning: '<think>razonamiento</think>',
          tool_chips: [{ tool: 'get_positions', status: 'ok' }],
          proposals: [] },
      ],
    });

    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));
    await act(async () => {
      await result.current.loadConversation('conv-hydrate');
    });

    expect(result.current.msgs).toHaveLength(2);
    expect(result.current.msgs[0]).toMatchObject({ role: 'user', text: 'hola' });
    expect(result.current.msgs[1]).toMatchObject({
      role:      'assistant',
      text:      'qué tal',
      reasoning: '<think>razonamiento</think>',
    });
    expect(result.current.msgs[1].tool_chips).toEqual([
      { tool: 'get_positions', status: 'ok' },
    ]);
  });

  it('loadConversation aligns conversation_id so the next sendTurn continues it', async () => {
    // Hydrate first
    stubHistoryJson({
      conversation_id: 'continue-me',
      title: 'old chat', surface: 'dock', pinned: false,
      messages: [
        { role: 'user',      ts: '2026-05-22T08:00:00Z', content: 'q1',
          reasoning: null, tool_chips: [], proposals: [] },
        { role: 'assistant', ts: '2026-05-22T08:00:01Z', content: 'a1',
          reasoning: null, tool_chips: [], proposals: [] },
      ],
    });
    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));
    await act(async () => {
      await result.current.loadConversation('continue-me');
    });

    // Now stub the streaming POST and capture the URL the hook hits.
    const fetchSpy = vi.fn().mockResolvedValueOnce(
      new Response(
        sseReadable([
          { type: 'text_delta', text: 'ok' },
          {
            type: 'message_end',
            usage: { input_tokens: 0, output_tokens: 0,
                     cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
            stop_reason: 'end_turn', cost_usd: 0,
          },
        ]),
        { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
      ),
    );
    // @ts-expect-error — overriding global for the test
    global.fetch = fetchSpy;

    await act(async () => {
      await result.current.sendTurn('q2');
    });

    // The streaming POST URL must reference the hydrated conversation_id,
    // NOT a fresh UUID — the continuation invariant.
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const calledUrl = fetchSpy.mock.calls[0][0] as string;
    expect(calledUrl).toContain('/agent/conversations/continue-me/turn');
  });

  it('loadConversation maps stale ProposalRecord to ProposalChip without signed_payload', async () => {
    stubHistoryJson({
      conversation_id: 'conv-with-proposal',
      title: 't', surface: 'dock', pinned: false,
      messages: [
        { role: 'user', ts: '2026-05-22T08:00:00Z', content: 'cerra btc',
          reasoning: null, tool_chips: [], proposals: [] },
        { role: 'assistant', ts: '2026-05-22T08:00:01Z',
          content: 'propongo cerrar',
          reasoning: null, tool_chips: [], proposals: [
            { proposal_id: 'prop-x',
              action: 'close_position',
              args: { position_id: 42 },
              expires_at: '2026-08-22T08:00:00Z',
              summary: 'Cerrar #42',
              state: 'stale' },
          ] },
      ],
    });

    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));
    await act(async () => {
      await result.current.loadConversation('conv-with-proposal');
    });

    const assistant = result.current.msgs[1];
    expect(assistant.proposals).toHaveLength(1);
    const chip = assistant.proposals![0];
    expect(chip.state).toBe('stale');
    expect(chip.proposal_id).toBe('prop-x');
    // signed_payload is intentionally empty (REST never persists it);
    // the dock disables the confirm button for any non-'pending' state.
    expect(chip.signed_payload).toBe('');
    expect(chip.action).toBe('close_position');
    expect(chip.summary).toBe('Cerrar #42');
  });

  it('loadConversation is a no-op during an in-flight sendTurn (#436 review #1)', async () => {
    // Start a sendTurn that won't complete until we release it.
    let releaseStream: () => void = () => {};
    const streamPromise = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    const blockedStream = new ReadableStream<Uint8Array>({
      async start(controller) {
        controller.enqueue(encode('data: ' + JSON.stringify({
          type: 'text_delta', text: 'partial',
        }) + '\n\n'));
        await streamPromise;
        controller.enqueue(encode('data: ' + JSON.stringify({
          type: 'message_end',
          usage: { input_tokens: 0, output_tokens: 0,
                   cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
          stop_reason: 'end_turn', cost_usd: 0,
        }) + '\n\n'));
        controller.close();
      },
    });
    // @ts-expect-error — overriding global for the test
    global.fetch = vi.fn().mockResolvedValueOnce(new Response(blockedStream, {
      status: 200, headers: { 'Content-Type': 'text/event-stream' },
    }));

    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));
    let sendPromise: Promise<void>;
    act(() => { sendPromise = result.current.sendTurn('hi'); });
    await waitFor(() => expect(result.current.loading).toBe(true));

    // Attempt to hydrate while loading=true. Should refuse silently.
    const historySpy = vi.fn();
    // @ts-expect-error — overriding global
    global.fetch = historySpy;
    await act(async () => {
      await result.current.loadConversation('would-corrupt');
    });
    // The history endpoint was NEVER called because the gate fired
    // before any fetch.
    expect(historySpy).not.toHaveBeenCalled();
    expect(result.current.hydrating).toBe(false);

    // Let the streaming turn finish cleanly.
    releaseStream();
    await act(async () => { await sendPromise!; });
  });

  it('sendTurn is a no-op while a loadConversation is hydrating (#436 review #1)', async () => {
    // Block the history fetch's response until we release it.
    let releaseFetch: (value: Response) => void = () => {};
    const blockedFetchPromise = new Promise<Response>((resolve) => {
      releaseFetch = resolve;
    });
    // @ts-expect-error — overriding global
    global.fetch = vi.fn().mockReturnValueOnce(blockedFetchPromise);

    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));
    let loadPromise: Promise<void>;
    act(() => { loadPromise = result.current.loadConversation('slow-load'); });
    await waitFor(() => expect(result.current.hydrating).toBe(true));

    // Try to send during the hydration. Must no-op.
    const sendSpy = vi.fn();
    // @ts-expect-error — overriding global (would be used by sendTurn fetch)
    global.fetch = sendSpy;
    await act(async () => {
      await result.current.sendTurn('mid-hydration');
    });
    expect(sendSpy).not.toHaveBeenCalled();
    expect(result.current.msgs).toEqual([]);  // no user message appended

    // Release the history fetch.
    releaseFetch(new Response(JSON.stringify({
      conversation_id: 'slow-load', title: 't',
      surface: 'dock', pinned: false,
      messages: [{ role: 'user', ts: 'T', content: 'preloaded',
                   reasoning: null, tool_chips: [], proposals: [] }],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    await act(async () => { await loadPromise!; });
    expect(result.current.msgs).toHaveLength(1);
    expect(result.current.msgs[0].text).toBe('preloaded');
  });

  it('rapid loadConversation invocations: the latest one wins (#436 review #1)', async () => {
    // First load takes a while; second load fires before it returns.
    let releaseFirst: (value: Response) => void = () => {};
    const firstFetch = new Promise<Response>((resolve) => {
      releaseFirst = resolve;
    });
    const fetchMock = vi.fn()
      .mockReturnValueOnce(firstFetch)
      .mockResolvedValueOnce(new Response(JSON.stringify({
        conversation_id: 'B', title: 'segunda', surface: 'dock', pinned: false,
        messages: [{ role: 'user', ts: 'T', content: 'desde B',
                     reasoning: null, tool_chips: [], proposals: [] }],
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    // @ts-expect-error — overriding global
    global.fetch = fetchMock;

    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));
    let firstPromise: Promise<void>;
    let secondPromise: Promise<void>;
    act(() => { firstPromise = result.current.loadConversation('A'); });
    act(() => { secondPromise = result.current.loadConversation('B'); });
    // Second one resolves first (its promise is already-resolved); the
    // first one's resolution must be discarded by the token check.
    await act(async () => { await secondPromise!; });
    expect(result.current.msgs[0].text).toBe('desde B');

    // Now release the first — it must NOT clobber B's state.
    releaseFirst(new Response(JSON.stringify({
      conversation_id: 'A', title: 'primera', surface: 'dock', pinned: false,
      messages: [{ role: 'user', ts: 'T', content: 'desde A',
                   reasoning: null, tool_chips: [], proposals: [] }],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    await act(async () => { await firstPromise!; });
    // B's content is still in place — A's late arrival was preempted.
    expect(result.current.msgs[0].text).toBe('desde B');
  });

  it('unknown proposal action is filtered out + warned (#436 review #3)', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    stubHistoryJson({
      conversation_id: 'conv-unknown-action',
      title: 't', surface: 'dock', pinned: false,
      messages: [
        { role: 'assistant', ts: 'T', content: 'algo',
          reasoning: null, tool_chips: [],
          proposals: [
            { proposal_id: 'p-good', action: 'close_position',
              args: {}, expires_at: 'T2', summary: 'cerrar', state: 'stale' },
            { proposal_id: 'p-bad',  action: 'future_unknown_action',
              args: {}, expires_at: 'T2', summary: 'hmm', state: 'stale' },
          ] },
      ],
    });

    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));
    await act(async () => {
      await result.current.loadConversation('conv-unknown-action');
    });

    const proposals = result.current.msgs[0].proposals;
    expect(proposals).toHaveLength(1);
    expect(proposals![0].proposal_id).toBe('p-good');
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining('unknown proposal action'),
      'future_unknown_action',
      'p-bad',
    );
    warnSpy.mockRestore();
  });

  it('loadConversation failure leaves the existing transcript intact', async () => {
    // Seed the hook with a turn so msgs is non-empty.
    stubFetchOnce(sseReadable([
      { type: 'text_delta', text: 'pre-existing' },
      {
        type: 'message_end',
        usage: { input_tokens: 1, output_tokens: 1,
                 cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
        stop_reason: 'end_turn', cost_usd: 0,
      },
    ]));
    const { result } = renderHook(() => useAgentStream({ surface: 'dock' }));
    await act(async () => { await result.current.sendTurn('hola'); });
    const before = result.current.msgs;

    // Now stub a failed history load.
    // @ts-expect-error — overriding global for the test
    global.fetch = vi.fn().mockResolvedValueOnce(
      new Response('{"detail":"boom"}', {
        status: 500, headers: { 'Content-Type': 'application/json' },
      }),
    );
    await act(async () => {
      await result.current.loadConversation('bad-conv');
    });

    // Transcript untouched on failure (best-effort hydration).
    expect(result.current.msgs).toEqual(before);
  });
});
