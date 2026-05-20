"""Fase 2 of the multi-provider epic — DeepSeek adapter tests.

Layered like test_provider_registry.py:

  1. UNIT TESTS for DeepSeekProvider methods that don't need HTTP
     (supports_model, has_api_key, format_system_blocks, format_tools,
     to_assistant_message, to_tool_result_messages, estimate_cost).

  2. STREAM PARSING TESTS for DeepSeekProvider.stream() with a mocked
     httpx response. Exercises:
       - Text content delta accumulation
       - Tool_calls delta accumulation across chunks
       - finish_reason="tool_calls" → stop_reason="tool_use" mapping
       - Synthesized SyntheticTextBlock + SyntheticToolUseBlock in
         LLMStreamEnd.content

  3. E2E PARITY TESTS driving run_turn through FakeDeepSeekProvider.
     Same scenarios as test_agent_loop.py's Anthropic path, but with
     deepseek-chat. Locks the contract that the loop is provider-
     agnostic above the protocol layer.

  4. WIRE-SHAPE VERIFICATION: when a DS turn dispatches tools, the
     messages list mutated by the loop has the correct DS shape
     (role=tool messages, assistant tool_calls). The hallucination
     guard's grounding scan works for both shapes (verified separately
     in test_agent_hallucination.py).
"""
from __future__ import annotations

import json

import pytest

from tests._fakes import FakeDeepSeekProvider, FakeTurnBuilder


# ── 1. Unit tests ────────────────────────────────────────────────


def test_deepseek_supports_only_deepseek_models():
    from api.agent.providers.deepseek_adapter import DeepSeekProvider
    p = DeepSeekProvider()
    assert p.supports_model("deepseek-chat") is True
    assert p.supports_model("deepseek-reasoner") is True
    assert p.supports_model("claude-sonnet-4-6") is False
    assert p.supports_model("gpt-5") is False


def test_deepseek_has_api_key_reads_env(monkeypatch):
    from api.agent.providers.deepseek_adapter import DeepSeekProvider
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert DeepSeekProvider().has_api_key() is False
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake")
    assert DeepSeekProvider().has_api_key() is True


def test_deepseek_format_system_blocks_concatenates_into_single_block():
    """DeepSeek doesn't support per-block cache_control. We collapse
    the 4 logical blocks into ONE system message with double-newline
    separators so the wire prefix is a single contiguous string —
    maximizes auto-cache hit."""
    from api.agent.providers.deepseek_adapter import DeepSeekProvider
    out = DeepSeekProvider().format_system_blocks(
        ["persona text", "tool docs text", "invariants text", "surface text"]
    )
    assert len(out) == 1
    msg = out[0]
    assert msg["role"] == "system"
    expected = "persona text\n\ntool docs text\n\ninvariants text\n\nsurface text"
    assert msg["content"] == expected


def test_deepseek_format_system_blocks_empty():
    """No blocks → no system messages. Defensive — the caller would
    normally pass 4 blocks but we don't want to emit a stub on an
    empty input."""
    from api.agent.providers.deepseek_adapter import DeepSeekProvider
    assert DeepSeekProvider().format_system_blocks([]) == []


def test_deepseek_format_tools_emits_openai_shape():
    """OpenAI/DeepSeek shape: top-level type=function + nested function
    object with name/description/parameters."""
    from api.agent.providers.deepseek_adapter import DeepSeekProvider
    from api.agent.tools.registry import tools_for_surface

    specs = tools_for_surface("dock")
    out = DeepSeekProvider().format_tools(specs)
    assert len(out) == len(specs)
    for tool in out:
        assert tool["type"] == "function"
        assert set(tool["function"].keys()) == {"name", "description", "parameters"}
        # Title stripping (cache determinism).
        _assert_no_title(tool["function"]["parameters"])


def _assert_no_title(node):
    if isinstance(node, dict):
        assert "title" not in node, f"unstripped title: {node}"
        for v in node.values():
            _assert_no_title(v)
    elif isinstance(node, list):
        for v in node:
            _assert_no_title(v)


def test_deepseek_estimate_cost_matches_published_pricing():
    """deepseek-chat: $0.27/1M in, $1.10/1M out. 1M in + 1M out = $1.37."""
    from api.agent.providers.deepseek_adapter import DeepSeekProvider
    cost = DeepSeekProvider().estimate_cost(
        "deepseek-chat",
        {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    assert cost == pytest.approx(1.37)


def test_deepseek_estimate_cost_ignores_cache_breakdown():
    """DS doesn't report cache stats — our estimate treats all input
    as fresh. Documented overestimate vs DS Console (spec §6 risk)."""
    from api.agent.providers.deepseek_adapter import DeepSeekProvider
    # Even if the caller passes cache_read tokens, they're ignored.
    cost_no_cache = DeepSeekProvider().estimate_cost(
        "deepseek-chat",
        {"input_tokens": 1_000_000, "output_tokens": 0},
    )
    cost_with_cache = DeepSeekProvider().estimate_cost(
        "deepseek-chat",
        {"input_tokens": 1_000_000, "output_tokens": 0,
         "cache_read_input_tokens": 500_000, "cache_creation_input_tokens": 0},
    )
    # Identical — cache_read tokens don't get any discount in our calc.
    assert cost_no_cache == cost_with_cache
    assert cost_no_cache == pytest.approx(0.27)


def test_deepseek_estimate_cost_returns_zero_for_unknown_model():
    from api.agent.providers.deepseek_adapter import DeepSeekProvider
    assert DeepSeekProvider().estimate_cost("deepseek-future", {}) == 0.0


# ── 2. Message shape translation ─────────────────────────────────


def test_deepseek_to_assistant_message_with_only_text():
    from api.agent.providers.base import LLMStreamEnd, SyntheticTextBlock
    from api.agent.providers.deepseek_adapter import DeepSeekProvider

    se = LLMStreamEnd(
        stop_reason="end_turn",
        usage={"input_tokens": 0, "output_tokens": 0,
               "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        content=[SyntheticTextBlock(text="Hola mundo")],
    )
    out = DeepSeekProvider().to_assistant_message(se)
    assert out == {"role": "assistant", "content": "Hola mundo"}
    # No tool_calls key when there are no tool_use blocks.
    assert "tool_calls" not in out


def test_deepseek_to_assistant_message_with_tool_use():
    from api.agent.providers.base import (
        LLMStreamEnd, SyntheticTextBlock, SyntheticToolUseBlock,
    )
    from api.agent.providers.deepseek_adapter import DeepSeekProvider

    se = LLMStreamEnd(
        stop_reason="tool_use",
        usage={"input_tokens": 0, "output_tokens": 0,
               "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        content=[
            SyntheticTextBlock(text="Voy a mirar tus posiciones"),
            SyntheticToolUseBlock(id="call_1", name="get_positions", input={"window": "7d"}),
        ],
    )
    out = DeepSeekProvider().to_assistant_message(se)
    assert out["role"] == "assistant"
    assert out["content"] == "Voy a mirar tus posiciones"
    assert len(out["tool_calls"]) == 1
    tc = out["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "get_positions"
    # DS expects arguments as a STRING (JSON-encoded).
    assert isinstance(tc["function"]["arguments"], str)
    assert json.loads(tc["function"]["arguments"]) == {"window": "7d"}


def test_deepseek_to_tool_result_messages_emits_one_per_tool():
    from api.agent.providers.base import SyntheticToolUseBlock
    from api.agent.providers.deepseek_adapter import DeepSeekProvider

    tu1 = SyntheticToolUseBlock(id="call_1", name="get_positions", input={})
    tu2 = SyntheticToolUseBlock(id="call_2", name="get_kill_switch_state", input={})
    out = DeepSeekProvider().to_tool_result_messages([
        (tu1, '{"positions": []}', False),
        (tu2, '{"portfolio_tier": "NORMAL"}', False),
    ])
    # DS emits N tool messages — NOT one user message with N blocks.
    assert len(out) == 2
    assert out[0] == {
        "role": "tool", "tool_call_id": "call_1",
        "content": '{"positions": []}',
    }
    assert out[1] == {
        "role": "tool", "tool_call_id": "call_2",
        "content": '{"portfolio_tier": "NORMAL"}',
    }


# ── 3. Registry + protocol ───────────────────────────────────────


def test_deepseek_provider_class_resolves_via_registry():
    from api.agent.providers.registry import get_provider_class_for_name
    from api.agent.providers.deepseek_adapter import DeepSeekProvider
    assert get_provider_class_for_name("deepseek") is DeepSeekProvider


def test_deepseek_model_resolves_via_class_helper():
    from api.agent.providers.registry import get_provider_class_for_model
    from api.agent.providers.deepseek_adapter import DeepSeekProvider
    assert get_provider_class_for_model("deepseek-chat") is DeepSeekProvider


def test_estimate_cost_dispatches_deepseek_model_via_loop_shim():
    """The loop's _estimate_cost_usd shim uses the registry to find
    the right adapter. A deepseek-chat model id should land in DS
    pricing, not Anthropic's."""
    from api.agent.loop import _estimate_cost_usd

    cost_ds = _estimate_cost_usd(
        "deepseek-chat",
        {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    # $1.37 (DS pricing) — significantly lower than the $18 Anthropic
    # Sonnet number locked in test_cost_estimation_matches_published_pricing.
    assert cost_ds == pytest.approx(1.37)


def test_deepseek_provider_conforms_to_llmprovider_protocol():
    """Same structural protocol check we have for AnthropicProvider —
    every public method on LLMProvider exists on DeepSeekProvider."""
    from api.agent.providers.base import LLMProvider
    from api.agent.providers.deepseek_adapter import DeepSeekProvider

    required = [m for m in dir(LLMProvider) if not m.startswith("_")]
    p = DeepSeekProvider()
    for method_name in required:
        assert hasattr(p, method_name), (
            f"DeepSeekProvider missing {method_name!r} required by LLMProvider"
        )


# ── 4. SSE parsing — direct against DeepSeekProvider.stream() ───
#
# The FakeDeepSeekProvider bypasses the SSE accumulation logic entirely
# (it translates FakeTurnBuilder events directly to LLMEvents). These
# tests exercise the REAL DeepSeekProvider.stream() by mocking httpx
# to return a pre-canonicalized SSE chunk list. The DS wire is what's
# under test here, not our internal contract.
#
# PR #413 review pickup (prerequisite for Fase 3): without these
# tests, the first real DS turn in production would be the first
# test of the SSE accumulator. The cases covered match the failure
# modes the reviewer enumerated:
#   - tool_calls fragmented across N chunks (arguments string assembly)
#   - 2 tool_calls in parallel (per-index bucketing)
#   - text + tool_use interleaved
#   - finish_reason early vs at-end ordering


class _FakeHTTPXResponse:
    """Minimal mock of httpx.Response in the streaming context. Supports
    .status_code + async aiter_lines() returning the queued SSE lines."""

    def __init__(self, *, status_code: int, lines: list[str]):
        self.status_code = status_code
        self._lines = lines

    async def aread(self) -> bytes:
        # Only called on non-200 path.
        return b'{"error": "test"}'

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamCM:
    """async-with context manager for httpx.AsyncClient.stream()."""

    def __init__(self, response: _FakeHTTPXResponse):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *a):
        return None


class _FakeHTTPXClient:
    """async-with context manager replacing httpx.AsyncClient. Returns
    a stream() method that itself returns _FakeStreamCM."""

    def __init__(self, response: _FakeHTTPXResponse, *,
                 calls_recorder: list | None = None):
        self._response = response
        self._calls_recorder = calls_recorder if calls_recorder is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    def stream(self, method: str, url: str, *, json=None, headers=None):
        self._calls_recorder.append({
            "method": method, "url": url, "json": json, "headers": headers,
        })
        return _FakeStreamCM(self._response)


def _install_fake_httpx(monkeypatch, *, lines: list[str], status_code: int = 200):
    """Monkeypatch httpx.AsyncClient with a fake that returns the given
    SSE lines. Returns the calls_recorder list so tests can inspect
    what was actually sent on the wire."""
    import httpx
    response = _FakeHTTPXResponse(status_code=status_code, lines=lines)
    calls_recorder: list = []
    # httpx.AsyncClient(timeout=...) returns the client; we need a
    # factory that captures the timeout but returns our fake.
    def _factory(*a, **kw):
        return _FakeHTTPXClient(response, calls_recorder=calls_recorder)
    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    return calls_recorder


@pytest.mark.anyio
async def test_deepseek_stream_text_only(monkeypatch):
    """Two text chunks + finish → LLMTextDelta x 2 + LLMStreamEnd with
    stop_reason='end_turn' (DS sends 'stop')."""
    from api.agent.providers.deepseek_adapter import DeepSeekProvider
    from api.agent.providers.base import (
        LLMStreamEnd, LLMTextDelta, SyntheticTextBlock,
    )

    lines = [
        'data: {"choices":[{"delta":{"content":"Hola "}}]}',
        'data: {"choices":[{"delta":{"content":"mundo"}}]}',
        'data: {"choices":[{"finish_reason":"stop"}],"usage":{"prompt_tokens":50,"completion_tokens":10}}',
        'data: [DONE]',
    ]
    _install_fake_httpx(monkeypatch, lines=lines)

    p = DeepSeekProvider(api_key="sk-fake")
    events = []
    async for ev in p.stream(
        model="deepseek-chat", system_blocks=[], messages=[],
        tools=[], max_tokens=4096,
    ):
        events.append(ev)

    text_deltas = [e for e in events if isinstance(e, LLMTextDelta)]
    ends = [e for e in events if isinstance(e, LLMStreamEnd)]
    assert "".join(d.text for d in text_deltas) == "Hola mundo"
    assert len(ends) == 1
    end = ends[0]
    assert end.stop_reason == "stop"
    assert end.usage["input_tokens"] == 50
    assert end.usage["output_tokens"] == 10
    assert len(end.content) == 1
    assert isinstance(end.content[0], SyntheticTextBlock)
    assert end.content[0].text == "Hola mundo"


@pytest.mark.anyio
async def test_deepseek_stream_accumulates_tool_call_fragmented_arguments(monkeypatch):
    """DS streams tool_calls.function.arguments across N chunks. The
    adapter accumulates them into a single SyntheticToolUseBlock with
    the parsed input dict. This is the highest-risk path of the SSE
    parser — PR #413 review prerequisite."""
    from api.agent.providers.deepseek_adapter import DeepSeekProvider
    from api.agent.providers.base import (
        LLMStreamEnd, LLMToolUseStart, SyntheticToolUseBlock,
    )

    lines = [
        # First delta: id + name, empty args
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"get_positions","arguments":""}}]}}]}',
        # Subsequent deltas: args string grows
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"win"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"dow\\":\\"7d\\"}"}}]}}]}',
        # Finish + usage
        'data: {"choices":[{"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":120,"completion_tokens":30}}',
        'data: [DONE]',
    ]
    _install_fake_httpx(monkeypatch, lines=lines)

    p = DeepSeekProvider(api_key="sk-fake")
    events = []
    async for ev in p.stream(
        model="deepseek-chat", system_blocks=[], messages=[],
        tools=[], max_tokens=4096,
    ):
        events.append(ev)

    starts = [e for e in events if isinstance(e, LLMToolUseStart)]
    ends = [e for e in events if isinstance(e, LLMStreamEnd)]
    assert len(starts) == 1
    assert starts[0].id == "call_1"
    assert starts[0].name == "get_positions"
    assert len(ends) == 1
    end = ends[0]
    # finish_reason=tool_calls normalizes to stop_reason=tool_use for
    # the loop's vocabulary.
    assert end.stop_reason == "tool_use"
    # Content has ONE SyntheticToolUseBlock with PARSED input dict.
    tool_blocks = [b for b in end.content if isinstance(b, SyntheticToolUseBlock)]
    assert len(tool_blocks) == 1
    assert tool_blocks[0].id == "call_1"
    assert tool_blocks[0].name == "get_positions"
    assert tool_blocks[0].input == {"window": "7d"}


@pytest.mark.anyio
async def test_deepseek_stream_parallel_tool_calls_bucketed_by_index(monkeypatch):
    """DS can emit multiple tool_calls in parallel. The adapter buckets
    by `index` so their arguments don't blend into one another. Each
    bucket synthesizes its own SyntheticToolUseBlock."""
    from api.agent.providers.deepseek_adapter import DeepSeekProvider
    from api.agent.providers.base import (
        LLMStreamEnd, LLMToolUseStart, SyntheticToolUseBlock,
    )

    lines = [
        # Both tool_calls start in different deltas (real DS behavior)
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_a","type":"function","function":{"name":"get_positions","arguments":""}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":1,"id":"call_b","type":"function","function":{"name":"get_kill_switch_state","arguments":""}}]}}]}',
        # Args for both, interleaved
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{}"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":1,"function":{"arguments":"{}"}}]}}]}',
        'data: {"choices":[{"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":80,"completion_tokens":20}}',
        'data: [DONE]',
    ]
    _install_fake_httpx(monkeypatch, lines=lines)

    p = DeepSeekProvider(api_key="sk-fake")
    events = []
    async for ev in p.stream(
        model="deepseek-chat", system_blocks=[], messages=[],
        tools=[], max_tokens=4096,
    ):
        events.append(ev)

    starts = [e for e in events if isinstance(e, LLMToolUseStart)]
    # Order: started in chunk-arrival order (a then b).
    assert [s.id for s in starts] == ["call_a", "call_b"]
    assert [s.name for s in starts] == ["get_positions", "get_kill_switch_state"]

    end = next(e for e in events if isinstance(e, LLMStreamEnd))
    tool_blocks = [b for b in end.content if isinstance(b, SyntheticToolUseBlock)]
    # Bucketed by index → 2 distinct blocks, NOT merged.
    assert len(tool_blocks) == 2
    by_id = {b.id: b for b in tool_blocks}
    assert by_id["call_a"].name == "get_positions"
    assert by_id["call_a"].input == {}
    assert by_id["call_b"].name == "get_kill_switch_state"
    assert by_id["call_b"].input == {}


@pytest.mark.anyio
async def test_deepseek_stream_malformed_tool_args_swallows_with_warn(monkeypatch, caplog):
    """If DS streams arguments that don't parse as JSON (provider bug,
    truncated stream, etc), the adapter logs a warning + synthesizes
    the block with input={}. Doesn't crash the stream."""
    from api.agent.providers.deepseek_adapter import DeepSeekProvider
    from api.agent.providers.base import LLMStreamEnd, SyntheticToolUseBlock

    lines = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_bad","function":{"name":"get_positions","arguments":""}}]}}]}',
        # Truncated / malformed JSON — never closes the brace
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{unclosed"}}]}}]}',
        'data: {"choices":[{"finish_reason":"tool_calls"}]}',
        'data: [DONE]',
    ]
    _install_fake_httpx(monkeypatch, lines=lines)

    p = DeepSeekProvider(api_key="sk-fake")
    events = []
    async for ev in p.stream(
        model="deepseek-chat", system_blocks=[], messages=[],
        tools=[], max_tokens=4096,
    ):
        events.append(ev)

    end = next(e for e in events if isinstance(e, LLMStreamEnd))
    tool_blocks = [b for b in end.content if isinstance(b, SyntheticToolUseBlock)]
    assert len(tool_blocks) == 1
    # Defensive: malformed args → empty dict, not a crash.
    assert tool_blocks[0].input == {}
    # Log captured the warn (DS bug debuggability).
    assert any("not parseable JSON" in r.message for r in caplog.records)


@pytest.mark.anyio
async def test_deepseek_stream_non_200_status_raises(monkeypatch):
    """A 429 / 503 / 4xx from DS surfaces as RuntimeError. The loop's
    catch-all converts to ErrorEvent reason='upstream' (verified by
    the existing test_upstream_mid_stream_drop_emits_friendly_error
    pattern in test_agent_graceful_failures.py)."""
    from api.agent.providers.deepseek_adapter import DeepSeekProvider

    _install_fake_httpx(monkeypatch, lines=[], status_code=429)

    p = DeepSeekProvider(api_key="sk-fake")
    with pytest.raises(RuntimeError, match="429"):
        async for _ in p.stream(
            model="deepseek-chat", system_blocks=[], messages=[],
            tools=[], max_tokens=4096,
        ):
            pass


@pytest.mark.anyio
async def test_deepseek_stream_sends_correct_wire_body(monkeypatch):
    """Sanity check on the request body the adapter sends to DS.
    Verifies: model echoed, messages prepended with system blocks,
    tools passed through, stream=True, Authorization header set."""
    from api.agent.providers.deepseek_adapter import DeepSeekProvider

    lines = [
        'data: {"choices":[{"delta":{"content":"ok"}}]}',
        'data: {"choices":[{"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1}}',
        'data: [DONE]',
    ]
    calls = _install_fake_httpx(monkeypatch, lines=lines)

    p = DeepSeekProvider(api_key="sk-test-key")
    async for _ in p.stream(
        model="deepseek-chat",
        system_blocks=[{"role": "system", "content": "you are X"}],
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "f"}}],
        max_tokens=2048,
    ):
        pass

    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "POST"
    assert "/chat/completions" in call["url"]
    assert call["headers"]["Authorization"] == "Bearer sk-test-key"
    body = call["json"]
    assert body["model"] == "deepseek-chat"
    assert body["max_tokens"] == 2048
    assert body["stream"] is True
    # System blocks prepended to messages — first item is system,
    # second is the user msg.
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"


# ── 5. E2E through run_turn with FakeDeepSeekProvider ───────────


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
    out = []
    async for ev in events_iter:
        out.append(ev)
    return out


@pytest.mark.anyio
async def test_deepseek_simple_text_turn(tmp_db):
    """A text-only turn with deepseek-chat completes end-to-end:
    TextDelta events + MessageEnd with DS cost calculated."""
    from api.agent.loop import run_turn, TextDelta, MessageEnd

    c = FakeDeepSeekProvider()
    c.queue_turn(
        FakeTurnBuilder()
        .text("Hola ").text("desde DeepSeek")
        .end_turn()
        .usage(input_tokens=100, output_tokens=20)
        .build()
    )

    msgs = [{"role": "user", "content": "saluda"}]
    events = await _collect(run_turn(
        client=c, model="deepseek-chat", surface="dock",
        messages=msgs, tenant_id=1,
    ))

    text_deltas = [e for e in events if isinstance(e, TextDelta)]
    ends = [e for e in events if isinstance(e, MessageEnd)]
    assert "".join(d.text for d in text_deltas) == "Hola desde DeepSeek"
    assert len(ends) == 1
    # Cost: 100 in × $0.27/1M + 20 out × $1.10/1M = $0.000049
    assert ends[0].cost_usd == pytest.approx(
        (100 * 0.27 + 20 * 1.10) / 1_000_000.0, rel=1e-3,
    )
    # DS doesn't report cache stats; both fields are 0.
    assert ends[0].usage["cache_read_input_tokens"] == 0


@pytest.mark.anyio
async def test_deepseek_tool_use_turn_appends_role_tool_messages(tmp_db, monkeypatch):
    """A tool_use turn through deepseek-chat: the loop appends an
    assistant message with `tool_calls` + N `role=tool` messages
    (one per tool dispatched). NOT one user message with tool_result
    blocks — that's Anthropic shape.
    """
    from api.agent.loop import run_turn, ToolUseStart, ToolUseResult, MessageEnd
    from api.agent.tools import handlers as h

    captured = {}

    def _stub_get_positions(*, tenant_id):
        captured["tenant_id"] = tenant_id
        return {"positions": [{"id": 7, "symbol": "BTCUSDT"}]}
    monkeypatch.setitem(h.TOOL_HANDLERS, "get_positions", _stub_get_positions)

    c = FakeDeepSeekProvider()
    # Hop 1: tool_use
    c.queue_turn(
        FakeTurnBuilder()
        .tool_use("get_positions", {}, tool_use_id="call_1")
        .stop_tool_use()
        .usage(input_tokens=50, output_tokens=10)
        .build()
    )
    # Hop 2: final text
    c.queue_turn(
        FakeTurnBuilder()
        .text("Tu posición #7 de BTC sigue abierta.")
        .end_turn()
        .usage(input_tokens=80, output_tokens=20)
        .build()
    )

    msgs = [{"role": "user", "content": "qué posiciones tengo"}]
    events = await _collect(run_turn(
        client=c, model="deepseek-chat", surface="dock",
        messages=msgs, tenant_id=42,
    ))

    starts = [e for e in events if isinstance(e, ToolUseStart)]
    results = [e for e in events if isinstance(e, ToolUseResult)]
    ends = [e for e in events if isinstance(e, MessageEnd)]
    assert [s.tool for s in starts] == ["get_positions"]
    assert [r.status for r in results] == ["ok"]
    assert len(ends) == 1
    # Cost sums across both hops. estimate_cost rounds each hop to
    # 6 decimals before the loop sums; the test mirrors that to avoid
    # float epsilon mismatch (sum-of-rounded != round-of-sum).
    h1 = round((50 * 0.27 + 10 * 1.10) / 1_000_000.0, 6)
    h2 = round((80 * 0.27 + 20 * 1.10) / 1_000_000.0, 6)
    assert ends[0].cost_usd == pytest.approx(h1 + h2, abs=1e-7)
    # Tenant binding worked.
    assert captured["tenant_id"] == 42

    # Wire-shape verification: messages array has DeepSeek shape now.
    # [0] = initial user
    # [1] = assistant with tool_calls (string content + list tool_calls)
    # [2] = role=tool message with result (ONE message per tool)
    # [3] = assistant with final text
    assert msgs[1]["role"] == "assistant"
    assert "tool_calls" in msgs[1]
    assert len(msgs[1]["tool_calls"]) == 1
    assert msgs[1]["tool_calls"][0]["function"]["name"] == "get_positions"

    assert msgs[2]["role"] == "tool"
    assert msgs[2]["tool_call_id"] == "call_1"
    # The result content is a JSON string (NOT a list of blocks like
    # Anthropic does).
    assert isinstance(msgs[2]["content"], str)
    parsed_result = json.loads(msgs[2]["content"])
    assert parsed_result["positions"][0]["id"] == 7
