"""Phase 2 conversation core — agentic loop tests.

Covers:

  - Simple text response → run_turn yields TextDelta + MessageEnd.
  - Tool use: the model emits a tool_use, the loop dispatches it,
    appends the assistant message ONCE and the tool_result in ONE
    user message (the wire-format invariant from pre-reg §6.1).
  - Multi-tool: two tool_use blocks in one turn → both dispatched,
    both results collected into ONE user message.
  - Hallucination guard at the dispatch layer: position_ids and
    symbols the model "invents" return not_found via the handler;
    we verify the loop surfaces that as is_error:true on the
    tool_result block.
  - Cache verification: usage.cache_read_input_tokens is propagated
    from the response into the MessageEnd.usage payload (the prompt
    cache hit-rate target in spec §14 reads from this field).
  - max_tool_hops: a model that loops indefinitely with tool_use
    gets stopped after MAX_TOOL_HOPS and emits an error event.
  - Conversation cap: messages longer than the cap → conversation_cap_reached.
  - Graceful failures: upstream exception → error event with friendly
    user_message, no 500 propagated.
  - Cost computation: matches the published per-1M pricing.

All tests use `FakeAnthropicClient` from tests/_fakes.py.
"""
from __future__ import annotations

import json

import pytest

from tests._fakes import FakeAnthropicClient, FakeTurnBuilder


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    yield db_path


async def _collect(events_iter):
    """Drain the async iterator into a list."""
    out = []
    async for ev in events_iter:
        out.append(ev)
    return out


# ── Simple text turn ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_run_turn_simple_text(tmp_db):
    from api.agent.loop import run_turn, TextDelta, MessageEnd
    c = FakeAnthropicClient()
    c.queue_turn(
        FakeTurnBuilder()
        .text("Tu portafolio está ")
        .text("en NORMAL.")
        .end_turn()
        .usage(input_tokens=120, output_tokens=10)
        .build()
    )
    msgs = [{"role": "user", "content": "¿cómo va el portafolio?"}]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))
    text_chunks = [e.text for e in events if isinstance(e, TextDelta)]
    ends = [e for e in events if isinstance(e, MessageEnd)]
    assert "".join(text_chunks) == "Tu portafolio está en NORMAL."
    assert len(ends) == 1
    assert ends[0].stop_reason == "end_turn"
    assert ends[0].usage["input_tokens"] == 120


# ── Tool dispatch (single tool) — wire-format invariant ────────────────


@pytest.mark.anyio
async def test_run_turn_dispatches_single_tool(tmp_db, monkeypatch):
    """The model emits propose tool_use → loop dispatches → appends
    assistant message ONCE → appends tool_result in ONE user message
    → second model turn returns text → MessageEnd."""
    from api.agent.loop import run_turn, TextDelta, ToolUseStart, ToolUseResult, MessageEnd
    from api.agent.tools import handlers as h

    captured: dict = {}

    def _stub_get_positions(*, tenant_id):
        captured["tenant_id"] = tenant_id
        return {"positions": [{"id": 1, "symbol": "BTC"}]}

    monkeypatch.setitem(h.TOOL_HANDLERS, "get_positions", _stub_get_positions)

    c = FakeAnthropicClient()
    # Turn 1: text + tool_use(get_positions) + stop_reason=tool_use
    c.queue_turn(
        FakeTurnBuilder()
        .text("Déjame consultar... ")
        .tool_use("get_positions", {}, tool_use_id="toolu_abc")
        .stop_tool_use()
        .build()
    )
    # Turn 2 (after tool dispatch): final text + end_turn
    c.queue_turn(
        FakeTurnBuilder()
        .text("Tienes 1 posición abierta de BTC.")
        .end_turn()
        .build()
    )

    msgs = [{"role": "user", "content": "qué posiciones tengo"}]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=42,
    ))

    # The handler was called with the JWT tenant_id, not whatever the
    # model might have sent (it can't — tenant_id isn't a tool input).
    assert captured["tenant_id"] == 42

    # Loop emitted: tool_use_start + tool_use_result + final text + end.
    starts = [e for e in events if isinstance(e, ToolUseStart)]
    results = [e for e in events if isinstance(e, ToolUseResult)]
    ends = [e for e in events if isinstance(e, MessageEnd)]
    assert [s.tool for s in starts] == ["get_positions"]
    assert [r.status for r in results] == ["ok"]
    assert len(ends) == 1

    # Wire-format invariant (pre-reg §6.1):
    #   - exactly ONE assistant message appended,
    #   - exactly ONE user message with tool_results follows.
    # The third entry onwards depends on the loop's second turn output;
    # we check the first three slots.
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assistant_content = msgs[1]["content"]
    assert isinstance(assistant_content, list)
    assert any(b["type"] == "tool_use" for b in assistant_content)
    assert msgs[2]["role"] == "user"
    user_content = msgs[2]["content"]
    assert isinstance(user_content, list)
    assert all(b["type"] == "tool_result" for b in user_content)
    assert len(user_content) == 1


# ── Multi-tool dispatch ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_run_turn_dispatches_multiple_tools_in_one_user_message(tmp_db, monkeypatch):
    """Two tool_use blocks in one model turn → both dispatched → BOTH
    tool_results in ONE user message (NOT split — the API rejects split
    tool_results)."""
    from api.agent.loop import run_turn
    from api.agent.tools import handlers as h

    def _stub_portfolio(*, tenant_id):
        return {"open_positions_count": 2}

    def _stub_positions(*, tenant_id):
        return {"positions": []}

    monkeypatch.setitem(h.TOOL_HANDLERS, "get_portfolio_overview", _stub_portfolio)
    monkeypatch.setitem(h.TOOL_HANDLERS, "get_positions", _stub_positions)

    c = FakeAnthropicClient()
    c.queue_turn(
        FakeTurnBuilder()
        .tool_use("get_portfolio_overview", {}, tool_use_id="toolu_1")
        .tool_use("get_positions", {}, tool_use_id="toolu_2")
        .stop_tool_use()
        .build()
    )
    c.queue_turn(
        FakeTurnBuilder()
        .text("Tienes 2 posiciones, 0 abiertas.")
        .end_turn()
        .build()
    )

    msgs = [{"role": "user", "content": "estado completo"}]
    await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))

    # After the loop runs: msgs[0]=user, msgs[1]=assistant (with TWO
    # tool_use blocks), msgs[2]=user (with TWO tool_result blocks
    # bundled together), msgs[3]=assistant (final text).
    assistant_content = msgs[1]["content"]
    tool_uses = [b for b in assistant_content if b["type"] == "tool_use"]
    assert len(tool_uses) == 2

    user_content = msgs[2]["content"]
    tool_results = [b for b in user_content if b["type"] == "tool_result"]
    assert len(tool_results) == 2
    assert {tr["tool_use_id"] for tr in tool_results} == {"toolu_1", "toolu_2"}


# ── Hallucinated position_id (defense-in-depth at dispatch) ────────────


@pytest.mark.anyio
async def test_run_turn_does_not_misdetect_error_on_legitimate_payload(tmp_db, monkeypatch):
    """PR #404 review issue 2: a tool can legitimately return a payload
    that mentions the substring "error" (e.g. an exit_reason value of
    "liquidation_error" or a state name of "no_error_state"). The
    previous `'"error"' in result_json` substring check produced a
    false positive here, telling the model the call failed when it
    didn't. Switched to JSON-parse + top-level "error" key check.
    """
    from api.agent.loop import run_turn, ToolUseResult
    from api.agent.tools import handlers as h

    monkeypatch.setitem(
        h.TOOL_HANDLERS, "get_recent_signals",
        lambda **kw: {"signals": [{"symbol": "BTC", "estado": "no_error_state"}]},
    )

    c = FakeAnthropicClient()
    c.queue_turn(
        FakeTurnBuilder()
        .tool_use("get_recent_signals", {})
        .stop_tool_use()
        .build()
    )
    c.queue_turn(FakeTurnBuilder().text("ok").end_turn().build())

    msgs = [{"role": "user", "content": "señales recientes"}]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))
    results = [e for e in events if isinstance(e, ToolUseResult)]
    # Substring match would have flagged this as an error; the JSON-parse
    # check correctly recognizes it as a normal payload.
    assert results == [ToolUseResult(tool="get_recent_signals", status="ok")]


@pytest.mark.anyio
async def test_run_turn_handles_hallucinated_position_id(tmp_db):
    """The model invents a position_id; the real handler returns
    not_found; the loop surfaces is_error:true on the tool_result so
    the model self-corrects on the next turn."""
    from api.agent.loop import run_turn, ToolUseResult

    c = FakeAnthropicClient()
    c.queue_turn(
        FakeTurnBuilder()
        .tool_use("get_position_detail", {"position_id": 9999})
        .stop_tool_use()
        .build()
    )
    c.queue_turn(
        FakeTurnBuilder()
        .text("Esa posición no existe.")
        .end_turn()
        .build()
    )

    msgs = [{"role": "user", "content": "detalles posición 9999"}]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))

    results = [e for e in events if isinstance(e, ToolUseResult)]
    assert results[0].status == "error"  # not_found → status=error

    # And the tool_result block carries is_error:true so the model knows.
    user_content = msgs[2]["content"]
    assert user_content[0]["is_error"] is True
    parsed = json.loads(user_content[0]["content"])
    assert parsed == {"error": "not_found"}


# ── Cache verification ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_run_turn_propagates_cache_read_tokens(tmp_db):
    """The MessageEnd event carries cache_read_input_tokens from the
    SDK's usage — this is the field the spec §14 cache-hit-rate target
    reads from."""
    from api.agent.loop import run_turn, MessageEnd

    c = FakeAnthropicClient()
    c.queue_turn(
        FakeTurnBuilder()
        .text("hola")
        .end_turn()
        .usage(input_tokens=10, output_tokens=2, cache_read=850, cache_creation=0)
        .build()
    )
    msgs = [{"role": "user", "content": "hi"}]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))
    end = next(e for e in events if isinstance(e, MessageEnd))
    assert end.usage["cache_read_input_tokens"] == 850
    assert end.usage["cache_creation_input_tokens"] == 0
    # Cost: 10 in × $3/1M + 850 cache_read × $3/1M × 0.1 + 2 out × $15/1M
    #     = 30e-6 + 255e-6 + 30e-6 = 315e-6
    assert end.cost_usd == pytest.approx(0.000315, abs=1e-7)


# ── Max tool hops ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_run_turn_max_tool_hops_emits_error(tmp_db, monkeypatch):
    """If the model keeps emitting tool_use indefinitely, the loop stops
    after MAX_TOOL_HOPS and yields an error event."""
    from api.agent.loop import run_turn, ErrorEvent, MAX_TOOL_HOPS
    from api.agent.tools import handlers as h

    monkeypatch.setitem(h.TOOL_HANDLERS, "get_positions", lambda **kw: {"positions": []})

    c = FakeAnthropicClient()
    # Queue MAX_TOOL_HOPS + 1 turns all emitting tool_use.
    for _ in range(MAX_TOOL_HOPS + 1):
        c.queue_turn(
            FakeTurnBuilder()
            .tool_use("get_positions", {}, tool_use_id=f"toolu_{_}")
            .stop_tool_use()
            .build()
        )

    msgs = [{"role": "user", "content": "loop forever"}]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert errors and errors[-1].reason == "too_many_tool_hops"


# ── Conversation cap ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_run_turn_conversation_cap_short_circuits(tmp_db):
    """messages > MAX_TURNS_PER_CONVERSATION → conversation_cap_reached
    error event immediately, without opening a stream."""
    from api.agent.loop import run_turn, ErrorEvent, MAX_TURNS_PER_CONVERSATION

    c = FakeAnthropicClient()  # no turns queued — proves we don't open one

    msgs = [
        {"role": "user", "content": f"msg {i}"}
        for i in range(MAX_TURNS_PER_CONVERSATION + 1)
    ]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1
    assert errors[0].reason == "conversation_cap_reached"
    # No stream was opened — proven by the fake having zero calls.
    assert c.calls == []


# ── Graceful upstream failures ─────────────────────────────────────────


@pytest.mark.anyio
async def test_run_turn_upstream_429_emits_error_no_raise(tmp_db):
    """A 429 from Anthropic → error event with friendly user_message.
    NO uncaught exception propagated to the HTTP layer."""
    from api.agent.loop import run_turn, ErrorEvent

    c = FakeAnthropicClient()
    c.queue_error(RuntimeError("rate-limited 429"))

    msgs = [{"role": "user", "content": "hola"}]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert errors and errors[0].reason == "upstream"
    # And the user-facing text is the friendly one (no env-var leak,
    # no stack trace).
    assert "saturado" in errors[0].user_message.lower() or \
           "intenta" in errors[0].user_message.lower()


# ── ReasoningDelta translation (Fase 3a of multi-provider epic) ────────


@pytest.mark.anyio
async def test_run_turn_forwards_reasoning_delta_as_loop_event(tmp_db):
    """When a provider yields LLMReasoningDelta in its stream, the
    loop translates it into a ReasoningDelta LoopEvent. The frontend
    receives the SSE `reasoning_delta` frame separately from
    `text_delta` so it can render the reasoning in a collapsible
    panel without contaminating the assistant's text bubble.
    """
    from api.agent.loop import (
        ReasoningDelta, TextDelta, MessageEnd, run_turn,
    )
    from api.agent.providers.base import (
        LLMReasoningDelta, LLMStreamEnd, LLMTextDelta, SyntheticTextBlock,
    )
    from api.agent.providers.anthropic_adapter import AnthropicProvider

    # Minimal in-test provider that yields a controlled LLMEvent sequence.
    # We extend AnthropicProvider (so format_* methods are wired) but
    # override stream() to yield exactly what we want.
    class _ReasoningProvider(AnthropicProvider):
        name = "anthropic"

        def has_api_key(self):
            return True

        async def stream(self, *, model, system_blocks, messages, tools, max_tokens):
            yield LLMReasoningDelta(text="Pienso ")
            yield LLMReasoningDelta(text="que esto...")
            yield LLMTextDelta(text="Respuesta final.")
            yield LLMStreamEnd(
                stop_reason="end_turn",
                usage={"input_tokens": 50, "output_tokens": 10,
                       "cache_read_input_tokens": 0,
                       "cache_creation_input_tokens": 0},
                content=[SyntheticTextBlock(text="Respuesta final.")],
            )

    provider = _ReasoningProvider()

    msgs = [{"role": "user", "content": "razona"}]
    events = []
    async for ev in run_turn(
        client=provider, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ):
        events.append(ev)

    reasoning = [e for e in events if isinstance(e, ReasoningDelta)]
    text = [e for e in events if isinstance(e, TextDelta)]
    ends = [e for e in events if isinstance(e, MessageEnd)]

    # Reasoning and text are distinct event streams.
    assert "".join(r.text for r in reasoning) == "Pienso que esto..."
    assert "".join(t.text for t in text) == "Respuesta final."
    assert len(ends) == 1
    # Reasoning text NEVER leaks into the assistant's text channel.
    assert "Pienso" not in "".join(t.text for t in text)


def test_streaming_serializes_reasoning_delta_as_sse_frame():
    """sse_serialize converts ReasoningDelta LoopEvent → SSE frame with
    `type: reasoning_delta`. The frontend's useAgentStream switch
    handles this frame (Fase 3a frontend wiring)."""
    import asyncio
    from api.agent.loop import ReasoningDelta, MessageEnd
    from api.agent.streaming import sse_serialize

    async def _events():
        yield ReasoningDelta(text="chain ")
        yield ReasoningDelta(text="of thought")
        yield MessageEnd(
            usage={"input_tokens": 0, "output_tokens": 0,
                   "cache_read_input_tokens": 0,
                   "cache_creation_input_tokens": 0},
            stop_reason="end_turn", cost_usd=0.0,
        )

    async def _drive():
        frames = []
        async for f in sse_serialize(_events(), keepalive_seconds=10.0):
            frames.append(f)
        return frames

    frames = asyncio.run(_drive())
    rendered = b"".join(frames).decode("utf-8")
    # Two reasoning_delta frames + one message_end.
    assert rendered.count('"type": "reasoning_delta"') == 2
    assert '"text": "chain "' in rendered
    assert '"text": "of thought"' in rendered
    # AND no text_delta frame (reasoning is its own channel).
    assert '"type": "text_delta"' not in rendered


# ── Cost model ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_message_end_cost_and_usage_sum_across_hops(tmp_db, monkeypatch):
    """A multi-hop turn (model calls a tool → gets result → emits text)
    triggers TWO Anthropic API calls. MessageEnd.cost_usd MUST be the
    SUM of both hops' costs, not just the last hop's. Same for usage.

    Without this invariant:
      - quota counters undercharge by 30-60% on multi-hop turns,
      - circuit breaker (which sums cost_usd over 24h) trips slower
        than the actual spend warrants,
      - operator metrics undercount turn cost.

    PR #408 review fix.
    """
    from api.agent.loop import (
        run_turn, MessageEnd, _estimate_cost_usd,
    )
    from api.agent.tools import handlers as h

    def _stub_positions(*, tenant_id):
        return {"positions": []}
    monkeypatch.setitem(h.TOOL_HANDLERS, "get_positions", _stub_positions)

    c = FakeAnthropicClient()
    # Hop 1: tool_use(get_positions) with usage 100 in / 50 out.
    c.queue_turn(
        FakeTurnBuilder()
        .tool_use("get_positions", {}, tool_use_id="toolu_1")
        .stop_tool_use()
        .usage(input_tokens=100, output_tokens=50)
        .build()
    )
    # Hop 2: final text with usage 200 in / 100 out.
    c.queue_turn(
        FakeTurnBuilder()
        .text("Listo, no tienes posiciones abiertas.")
        .end_turn()
        .usage(input_tokens=200, output_tokens=100)
        .build()
    )

    msgs = [{"role": "user", "content": "qué posiciones tengo"}]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))

    ends = [e for e in events if isinstance(e, MessageEnd)]
    assert len(ends) == 1
    end = ends[0]

    # Expected: usage is the per-field SUM across both hops.
    assert end.usage["input_tokens"]  == 300   # 100 + 200
    assert end.usage["output_tokens"] == 150   # 50  + 100

    # Expected: cost is the SUM of per-hop costs (not just the last).
    hop1 = _estimate_cost_usd("claude-sonnet-4-6",
                               {"input_tokens": 100, "output_tokens": 50})
    hop2 = _estimate_cost_usd("claude-sonnet-4-6",
                               {"input_tokens": 200, "output_tokens": 100})
    expected_total = hop1 + hop2
    assert end.cost_usd == pytest.approx(expected_total)
    # And explicitly NOT the last-hop-only undercharge that the bug
    # produced — guards against a regression that silently flips back.
    assert end.cost_usd != pytest.approx(hop2)
    assert end.cost_usd > hop2  # accumulated value strictly larger


def test_cost_estimation_matches_published_pricing():
    """The cost formula uses the published per-1M USD pricing for
    Sonnet 4.6 / Haiku 4.5 / Opus 4.7. If the cached pricing in
    api/agent/loop.py drifts vs Anthropic's published rates, this
    test catches it."""
    from api.agent.loop import _estimate_cost_usd
    # Sonnet 4.6: $3/1M input, $15/1M output. 1M input + 1M output = $18.
    cost = _estimate_cost_usd(
        "claude-sonnet-4-6",
        {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    assert cost == pytest.approx(18.0)
    # Haiku 4.5: $1/1M input, $5/1M output.
    cost = _estimate_cost_usd(
        "claude-haiku-4-5",
        {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    assert cost == pytest.approx(6.0)
    # Opus 4.7: $5/1M input, $25/1M output.
    cost = _estimate_cost_usd(
        "claude-opus-4-7",
        {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    assert cost == pytest.approx(30.0)


# ── System prompt blocks are cache-control'd ───────────────────────────


def test_system_blocks_carry_cache_control_after_anthropic_formatting():
    """Phase 1 of the multi-provider epic split this invariant: the
    cache_control:ephemeral wrapping moved out of build_system_blocks
    (now provider-neutral, returns list[str]) and into the AnthropicProvider's
    format_system_blocks. The end-to-end wire shape that goes to the
    Anthropic Messages API still has the 4 cache_control'd text blocks.

    This test verifies the full pipeline: build_system_blocks →
    AnthropicProvider.format_system_blocks → wire shape with
    cache_control on each of the 4 blocks. Other providers (DeepSeek,
    Phase 2) have their own formatting tests in their own files.
    """
    from api.agent.prompts import build_system_blocks
    from api.agent.providers.anthropic_adapter import AnthropicProvider

    raw_blocks = build_system_blocks("dock")
    assert isinstance(raw_blocks, list)
    assert len(raw_blocks) == 4
    assert all(isinstance(b, str) for b in raw_blocks)

    wire_blocks = AnthropicProvider().format_system_blocks(raw_blocks)
    assert len(wire_blocks) == 4
    for b in wire_blocks:
        assert b["type"] == "text"
        assert b["cache_control"] == {"type": "ephemeral"}


def test_system_blocks_are_deterministic_across_calls():
    """Same surface → byte-identical output across calls. If this fails,
    the cache prefix shifts on every turn and the spec §14 cache-hit-rate
    target ≥70% is unreachable. This invariant is provider-neutral —
    determinism of the raw text drives BOTH Anthropic's cache_control
    breakpoints AND DeepSeek's auto-prefix cache."""
    from api.agent.prompts import build_system_blocks
    a = build_system_blocks("dock")
    b = build_system_blocks("dock")
    assert a == b
    # And: dock vs symbol_detail differ only on block 4.
    c = build_system_blocks("symbol_detail")
    assert a[0] == c[0]  # persona
    assert a[2] == c[2]  # invariants
    assert a[3] != c[3]  # surface micro-prompt differs


# ── anyio fixture (project does not have a project-wide config) ────────


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ── E2E: POST /agent/conversations/{id}/turn via TestClient ────────────


@pytest.fixture
def authed_client(tmp_path, monkeypatch):
    """TestClient with the agent dependencies overridden: tenant_id=1,
    api-key-check bypassed, and a `_resolve_provider_for_model` stub
    that routes by model prefix.

    Fase 3b PR #415 review fix: provider resolution moved to per-request
    in the router. Tests can no longer just inject a single FakeProvider
    via dependency_overrides — they must route by model id. This fixture
    sets up a smart resolver:

      - `claude-*` model IDs → return the shared FakeAnthropicProvider
        (the one yielded to the test so it can `.queue_turn(...)` it).
      - `deepseek-*` model IDs → return a FakeDeepSeekProvider also
        constructed by the fixture (accessible via the `ds_fake`
        attribute the fixture yields).

    Backward compat: most existing tests use the surface default (which
    is now deepseek-chat post-Fase-3b). The smart resolver routes to
    the DS fake by default. Tests that explicitly pass model="claude-*"
    via body.model route to the Anthropic fake. Tests assert on
    `fake_client.calls` for Anthropic and on `ds_fake.calls` for DS.
    """
    import btc_api
    from fastapi.testclient import TestClient
    from auth.dependencies import get_current_tenant_id
    from api.agent.clients import get_anthropic_client
    from api.agent import router as _router

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-test-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY",  "sk-ds-fake-test-key")

    from tests._fakes import FakeDeepSeekProvider
    fake_client = FakeAnthropicClient()
    ds_fake = FakeDeepSeekProvider()
    # Attach the DS fake as an attribute so tests that need it can
    # reach it (`fake.ds_fake`) without changing the 2-tuple yield
    # shape that the 7 existing call sites destructure as
    # `client, fake = authed_client`.
    fake_client.ds_fake = ds_fake  # type: ignore[attr-defined]

    def _smart_resolve(model: str):
        if model.startswith("claude-"):
            return fake_client
        if model.startswith("deepseek-"):
            return ds_fake
        from api.agent.providers.registry import UnknownProviderError
        raise UnknownProviderError(f"no fake for model {model!r}")

    monkeypatch.setattr(_router, "_resolve_provider_for_model", _smart_resolve)
    btc_api.app.dependency_overrides[get_current_tenant_id] = lambda: 1
    # Bypass the status-gate dep (no key check, no breaker check). Tests
    # that exercise the gate's failure modes override this themselves.
    btc_api.app.dependency_overrides[get_anthropic_client] = lambda: True
    try:
        yield TestClient(btc_api.app), fake_client
    finally:
        btc_api.app.dependency_overrides.pop(get_current_tenant_id, None)
        btc_api.app.dependency_overrides.pop(get_anthropic_client, None)


def test_endpoint_streams_text_deltas_as_sse_frames(authed_client):
    client, fake = authed_client
    fake.queue_turn(
        FakeTurnBuilder()
        .text("Hola ").text("mundo")
        .end_turn()
        .usage(input_tokens=10, output_tokens=2)
        .build()
    )
    # Fase 3b PR #415 review fix: defaults now route to DS. We queue
    # on the Anthropic fake (`fake`), so force Anthropic routing via
    # explicit model override. Decouples this test from default
    # migrations — the invariant under test (SSE wire format) is
    # provider-agnostic.
    resp = client.post(
        "/agent/conversations/test-conv-1/turn",
        json={
            "surface":  "dock",
            "model":    "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "saluda"}],
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers.get("x-accel-buffering") == "no"

    body = resp.text
    # Parse the SSE frames — each starts with `data: ` and ends with `\n\n`.
    frames = [
        line[len("data: "):]
        for line in body.split("\n\n")
        if line.startswith("data: ")
    ]
    parsed = [json.loads(f) for f in frames]
    types = [p["type"] for p in parsed]

    assert "text_delta" in types
    assert types[-1] == "message_end"
    text_deltas = [p["text"] for p in parsed if p["type"] == "text_delta"]
    assert "".join(text_deltas) == "Hola mundo"
    end = next(p for p in parsed if p["type"] == "message_end")
    assert end["stop_reason"] == "end_turn"
    assert end["usage"]["input_tokens"] == 10


def test_endpoint_503_when_status_disabled(authed_client, monkeypatch):
    """If get_agent_status reports disabled (operator flipped the flag),
    the endpoint must 503 without invoking the model — even though the
    fake client is wired up."""
    client, fake = authed_client
    import api.agent.config as agent_cfg
    monkeypatch.setattr(
        agent_cfg, "load_config",
        lambda: {"agent": {"enabled": False}},
    )
    resp = client.post(
        "/agent/conversations/test-conv-2/turn",
        json={
            "surface":  "dock",
            "messages": [{"role": "user", "content": "hola"}],
        },
    )
    assert resp.status_code == 503
    assert resp.json() == {"detail": "agent_disabled"}
    # And the fake was never invoked — proves we short-circuited cleanly.
    assert fake.calls == []


def test_endpoint_400_when_model_not_allowed(authed_client):
    client, _fake = authed_client
    resp = client.post(
        "/agent/conversations/test-conv-3/turn",
        json={
            "surface":  "dock",
            "messages": [{"role": "user", "content": "hola"}],
            "model":    "gpt-4",   # not in the allowlist
        },
    )
    assert resp.status_code == 400
    assert resp.json() == {"detail": "model_not_allowed"}


# ── Audit writes — per-turn row in agent_conversations ─────────────────


def test_endpoint_audits_each_completed_turn(authed_client):
    """Every MessageEnd produces a row in agent_conversations with the
    turn's usage + cost + tenant_id + surface + conversation_id."""
    import btc_api
    client, fake = authed_client

    fake.queue_turn(
        FakeTurnBuilder()
        .text("Tu portafolio está en NORMAL.")
        .end_turn()
        .usage(input_tokens=150, output_tokens=12, cache_read=120)
        .build()
    )
    # Fase 3b of multi-provider epic: defaults migrated to DS, so we
    # explicitly request claude-sonnet-4-6 via the per-turn override
    # to match what the FakeAnthropicProvider (injected via fixture)
    # can actually cost. Decouples the test from default migrations
    # — the audit invariant tested here (one row per turn, correct
    # tenant/surface/cost shape) is provider-agnostic.
    resp = client.post(
        "/agent/conversations/test-conv-audit-1/turn",
        json={
            "surface":  "kill_switch",
            "model":    "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "estado"}],
        },
    )
    assert resp.status_code == 200
    # Consume the body so the StreamingResponse closes and the audit
    # wrapper runs the persistence step.
    _ = resp.text

    con = btc_api.get_db()
    try:
        rows = con.execute(
            "SELECT tenant_id, surface, conversation_id, role, model, "
            "input_tokens, output_tokens, cache_read_input_tokens, "
            "cost_usd, refused "
            "FROM agent_conversations WHERE conversation_id = ?",
            ("test-conv-audit-1",),
        ).fetchall()
    finally:
        con.close()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["tenant_id"] == 1
    assert row["surface"] == "kill_switch"
    assert row["role"] == "assistant"
    assert row["model"] == "claude-sonnet-4-6"
    assert row["input_tokens"] == 150
    assert row["output_tokens"] == 12
    assert row["cache_read_input_tokens"] == 120
    assert row["cost_usd"] is not None and row["cost_usd"] > 0
    assert row["refused"] == 0


def test_endpoint_audits_error_turns(authed_client):
    """Error events also produce an audit row (role='error', the reason
    is the content_summary). Lets us track error rate post-rollout."""
    import btc_api
    client, fake = authed_client

    fake.queue_error(RuntimeError("simulated 429"))
    resp = client.post(
        "/agent/conversations/test-conv-audit-err/turn",
        json={
            "surface":  "dock",
            "messages": [{"role": "user", "content": "hola"}],
        },
    )
    assert resp.status_code == 200
    _ = resp.text

    con = btc_api.get_db()
    try:
        rows = con.execute(
            "SELECT role, content_json, refused FROM agent_conversations "
            "WHERE conversation_id = ?",
            ("test-conv-audit-err",),
        ).fetchall()
    finally:
        con.close()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["role"] == "error"
    # content_json carries the reason enum value, JSON-encoded.
    assert json.loads(row["content_json"]) == "upstream"
    assert row["refused"] == 0  # upstream is not a refusal


def test_endpoint_503_when_api_key_missing_via_direct_curl(authed_client, monkeypatch):
    """PR #404 review issue 1 (BLOCKER): FastAPI resolves Depends() BEFORE
    the handler body. Without the status guard inside get_anthropic_client,
    a direct POST with the default provider's API key missing would 500
    with a stack trace — re-leaking the env-var name that #381 closed.

    Fase 3b of multi-provider epic: default migrated to DeepSeek, so the
    test deletes DEEPSEEK_API_KEY (the default's key per §2.7) to trip
    the disabled path.

    This test reverts the dependency_overrides on get_anthropic_client so
    the real resolver runs, then unsets the env var. Must 503 with the
    closed-enum reason, NOT 500.
    """
    import btc_api
    from api.agent.clients import get_anthropic_client
    client, _fake = authed_client
    # Revert the test fake so the real resolver runs.
    btc_api.app.dependency_overrides.pop(get_anthropic_client, None)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    resp = client.post(
        "/agent/conversations/bypass-test/turn",
        json={"surface": "dock", "messages": [{"role": "user", "content": "x"}]},
    )
    assert resp.status_code == 503
    assert resp.json() == {"detail": "agent_disabled"}
    # And the body must not leak the env-var name on this path either.
    for forbidden in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", ".env", "restart"):
        assert forbidden not in resp.text, (
            f"/agent/conversations bypass-test leaked {forbidden!r}: {resp.text!r}"
        )


def test_override_model_routes_to_correct_provider_post_migration(authed_client):
    """PR #415 review CRITICAL BUG fix: provider must be resolved
    per-REQUEST based on the active model id, not per-DEFAULT-SURFACE.

    Pre-fix scenario (Fase 3b without this fix):
      1. Default surface 'dock' resolves to 'deepseek-chat' (DS).
      2. Operator POSTs body.model='claude-opus-4-7' (override).
      3. Depends(get_anthropic_client) resolved DS adapter (default).
      4. run_turn(client=DS, model='claude-opus-4-7') called.
      5. DS adapter sent claude-opus-4-7 to api.deepseek.com → 400 → user sees
         friendly fallback "El copiloto está saturado...".
      6. Override path silently broken.

    Post-fix: the handler calls _resolve_provider_for_model(model) AFTER
    determining the model (default or override). This test asserts the
    Anthropic fake was used when body.model='claude-sonnet-4-6' on a
    dock surface (whose default is deepseek-chat). Without the fix,
    the DS fake would have been used and the assertion below would
    not match.
    """
    client, fake = authed_client
    fake.queue_turn(
        FakeTurnBuilder()
        .text("Anthropic respondió.")
        .end_turn()
        .usage(input_tokens=10, output_tokens=5)
        .build()
    )
    # Surface defaults to deepseek-chat but we OVERRIDE with claude.
    resp = client.post(
        "/agent/conversations/override-route-test/turn",
        json={
            "surface":  "dock",
            "model":    "claude-sonnet-4-6",  # override the DS default
            "messages": [{"role": "user", "content": "x"}],
        },
    )
    assert resp.status_code == 200
    _ = resp.text  # drain

    # The Anthropic fake's stream() was called — calls list has the
    # request kwargs we just sent. The DS fake's stream() was NOT
    # called — its calls list stays empty.
    assert len(fake.calls) == 1
    assert fake.calls[0]["model"] == "claude-sonnet-4-6"
    assert len(fake.ds_fake.calls) == 0


def test_default_model_routes_to_ds_provider_post_migration(authed_client):
    """Companion to the override test: when NO model override is
    given, the dock default 'deepseek-chat' resolves to the DS fake.
    If this test fails, the default migration in Fase 3b didn't take
    effect end-to-end (the loop is still seeing claude-sonnet-4-6
    somehow)."""
    client, fake = authed_client
    fake.ds_fake.queue_turn(
        FakeTurnBuilder()
        .text("DeepSeek respondió.")
        .end_turn()
        .usage(input_tokens=10, output_tokens=5)
        .build()
    )
    resp = client.post(
        "/agent/conversations/default-route-test/turn",
        json={
            "surface":  "dock",   # no model override
            "messages": [{"role": "user", "content": "x"}],
        },
    )
    assert resp.status_code == 200
    _ = resp.text

    assert len(fake.ds_fake.calls) == 1
    assert fake.ds_fake.calls[0]["model"] == "deepseek-chat"
    # And the Anthropic fake never ran.
    assert len(fake.calls) == 0


def test_endpoint_400_when_conversation_id_invalid(authed_client):
    """PR #404 review issue 3: conversation_id must be alphanumeric +
    _-, max 128 chars. Blocks pollution and minor DoS."""
    client, fake = authed_client
    # No queue — request should 422 on path validation before hitting the loop.
    # Note: control characters (\n, \r, \0) are rejected by httpx URL
    # validation before they reach the server, so we don't test them
    # at the endpoint level — that's a separate layer of defense.
    cases = [
        "a" * 200,           # too long
        "with/slash",        # path separator
        "with space",        # whitespace (httpx encodes; FastAPI Path() rejects)
        "",                  # empty
    ]
    for bad in cases:
        resp = client.post(
            f"/agent/conversations/{bad}/turn",
            json={"surface": "dock", "messages": [{"role": "user", "content": "x"}]},
        )
        # FastAPI returns 422 on path validation failures; 404 if the path
        # itself doesn't match the route (empty string case). Either is
        # acceptable — what matters is we never reach the loop.
        assert resp.status_code in (404, 422), (
            f"conversation_id={bad!r} should be rejected, got {resp.status_code}"
        )
    assert fake.calls == []  # zero turns opened across all attempts


def test_endpoint_audits_isolated_per_tenant(authed_client, monkeypatch):
    """Two turns from two tenants → two rows, each scoped to its tenant.
    Multi-tenant invariant for the audit row itself (pre-reg §8)."""
    import btc_api
    from auth.dependencies import get_current_tenant_id

    # First turn as tenant=1. Fase 3b PR #415 review fix: explicit
    # claude model override so the smart resolver routes to the
    # Anthropic fake we just queued on (the test pre-dates DS defaults).
    client, fake = authed_client
    fake.queue_turn(
        FakeTurnBuilder().text("turn-1").end_turn().usage(input_tokens=10).build()
    )
    resp = client.post(
        "/agent/conversations/iso-conv/turn",
        json={
            "surface":  "dock",
            "model":    "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "msg-1"}],
        },
    )
    assert resp.status_code == 200
    _ = resp.text

    # Swap the override to tenant=2 for the second turn.
    btc_api.app.dependency_overrides[get_current_tenant_id] = lambda: 2
    fake.queue_turn(
        FakeTurnBuilder().text("turn-2").end_turn().usage(input_tokens=20).build()
    )
    resp = client.post(
        "/agent/conversations/iso-conv-tenant2/turn",
        json={
            "surface":  "dock",
            "model":    "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "msg-2"}],
        },
    )
    assert resp.status_code == 200
    _ = resp.text

    con = btc_api.get_db()
    try:
        rows = con.execute(
            "SELECT tenant_id, conversation_id, input_tokens "
            "FROM agent_conversations ORDER BY id ASC"
        ).fetchall()
    finally:
        con.close()
    assert len(rows) == 2
    assert dict(rows[0])["tenant_id"] == 1
    assert dict(rows[0])["conversation_id"] == "iso-conv"
    assert dict(rows[0])["input_tokens"] == 10
    assert dict(rows[1])["tenant_id"] == 2
    assert dict(rows[1])["conversation_id"] == "iso-conv-tenant2"
    assert dict(rows[1])["input_tokens"] == 20
