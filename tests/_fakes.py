"""Test doubles: FakeProvider implements ProviderAdapter deterministically."""
import time
from typing import Any


class FakeProvider:
    """Deterministic provider for tests. Records calls. Responds from pre-seeded data."""

    def __init__(self, name: str = "fake"):
        self.name = name
        self.rate_limit_per_min = 100_000
        self.calls: list[tuple[str, str, int, int]] = []
        self.bars_by_key: dict[tuple[str, str], list] = {}
        self.raise_by_key: dict[tuple[str, str], Exception] = {}
        self.healthy: bool = True

    def set_bars(self, symbol: str, timeframe: str, bars: list):
        """Seed with a list of Bar instances ordered by open_time ascending."""
        self.bars_by_key[(symbol, timeframe)] = bars

    def set_error(self, symbol: str, timeframe: str, exc: Exception):
        self.raise_by_key[(symbol, timeframe)] = exc

    def clear_errors(self):
        self.raise_by_key.clear()

    def fetch_klines(self, symbol: str, timeframe: str, start_ms: int, end_ms: int):
        self.calls.append((symbol, timeframe, start_ms, end_ms))
        if (symbol, timeframe) in self.raise_by_key:
            raise self.raise_by_key[(symbol, timeframe)]
        all_bars = self.bars_by_key.get((symbol, timeframe), [])
        return [b for b in all_bars if start_ms <= b.open_time <= end_ms]

    def is_healthy(self) -> bool:
        return self.healthy


def make_bar(symbol: str, timeframe: str, open_time: int, price: float = 100.0, **overrides):
    """Factory for test Bar instances. Imports locally so tests can run before Bar exists."""
    from data.providers.base import Bar
    defaults = dict(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        open=price,
        high=price * 1.01,
        low=price * 0.99,
        close=price,
        volume=1000.0,
        provider="fake",
        fetched_at=int(time.time() * 1000),
    )
    defaults.update(overrides)
    return Bar(**defaults)


# ─────────────────────────────────────────────────────────────────────────
# FakeAnthropicClient — drop-in for `anthropic.AsyncAnthropic` in tests
# ─────────────────────────────────────────────────────────────────────────
#
# Reproduces the streaming surface of the anthropic Python SDK (>=0.40)
# that the Phase 2 agentic loop depends on. Built FIRST in PR 2A (pre-reg
# §12.7 — Buffers internos) so the SSE event contract is frozen before
# the loop starts depending on it.
#
# Why a hand-rolled fake instead of unittest.mock:
#
#   - SDK's messages.stream() is an async context manager that yields a
#     typed iterator of events; mocking that surface with AsyncMock makes
#     test bodies unreadable and silently drifts on minor SDK bumps.
#   - A focused fake forces us to think about which event types the loop
#     actually reacts to — that's the correctness surface.
#
# Pin: anthropic >= 0.40 (see requirements.txt). When the pin bumps, run
# the suite against the live model under @pytest.mark.live to catch
# silent drift before merging the bump (pre-reg §12.7).

import json as _json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FakeContentBlock:
    """A content block in the final assistant message."""
    type: str                           # "text" | "tool_use"
    text: Optional[str] = None
    id: Optional[str] = None
    name: Optional[str] = None
    input: Optional[dict] = None


@dataclass
class FakeUsage:
    """Mirror of `response.usage` — fields the cache verification test reads."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class FakeFinalMessage:
    """What stream.get_final_message() returns."""
    content: list = field(default_factory=list)
    stop_reason: str = "end_turn"
    usage: FakeUsage = field(default_factory=FakeUsage)
    model: str = "claude-sonnet-4-6"


@dataclass
class FakeDelta:
    type: str                           # "text_delta" | "input_json_delta"
    text: Optional[str] = None
    partial_json: Optional[str] = None


@dataclass
class FakeContentBlockMeta:
    type: str                           # "text" | "tool_use"
    name: Optional[str] = None
    id: Optional[str] = None


@dataclass
class FakeEvent:
    """Event yielded by the stream iterator — mirrors `event.type`,
    `event.delta`, `event.content_block`."""
    type: str
    delta: Optional[FakeDelta] = None
    content_block: Optional[FakeContentBlockMeta] = None
    index: int = 0


class FakeTurnBuilder:
    """Fluent builder for one turn's events + final message.

    Usage:
        events, final = (
            FakeTurnBuilder()
            .text("Hola ").text("mundo")
            .end_turn()
            .usage(input_tokens=100, cache_read=80)
            .build()
        )
    """

    def __init__(self):
        self._events: list[FakeEvent] = []
        self._content: list[FakeContentBlock] = []
        self._index = 0
        self._stop_reason: Optional[str] = None
        self._usage = FakeUsage()
        self._block_open = False

    def text(self, chunk: str) -> "FakeTurnBuilder":
        """Append a text chunk. Multiple chunks accumulate into one text block."""
        if not self._content or self._content[-1].type != "text":
            self._close_open_block()
            self._events.append(FakeEvent(
                type="content_block_start",
                index=self._index,
                content_block=FakeContentBlockMeta(type="text"),
            ))
            self._content.append(FakeContentBlock(type="text", text=""))
            self._block_open = True
        self._events.append(FakeEvent(
            type="content_block_delta",
            index=self._index,
            delta=FakeDelta(type="text_delta", text=chunk),
        ))
        self._content[-1].text = (self._content[-1].text or "") + chunk
        return self

    def tool_use(
        self,
        name: str,
        input: dict,
        tool_use_id: str = "toolu_fake_001",
    ) -> "FakeTurnBuilder":
        """Emit a tool_use block. Closes any open text block first."""
        self._close_open_block()
        self._events.append(FakeEvent(
            type="content_block_start",
            index=self._index,
            content_block=FakeContentBlockMeta(
                type="tool_use", name=name, id=tool_use_id,
            ),
        ))
        # SDK streams tool inputs as input_json_delta chunks; we send the
        # whole JSON as one chunk. The loop accumulates either way.
        self._events.append(FakeEvent(
            type="content_block_delta",
            index=self._index,
            delta=FakeDelta(type="input_json_delta", partial_json=_json.dumps(input)),
        ))
        self._events.append(FakeEvent(type="content_block_stop", index=self._index))
        self._index += 1
        self._content.append(FakeContentBlock(
            type="tool_use", name=name, id=tool_use_id, input=input,
        ))
        return self

    def end_turn(self) -> "FakeTurnBuilder":
        self._close_open_block()
        self._stop_reason = "end_turn"
        self._events.append(FakeEvent(type="message_delta"))
        self._events.append(FakeEvent(type="message_stop"))
        return self

    def stop_tool_use(self) -> "FakeTurnBuilder":
        """Close with stop_reason='tool_use' so the loop dispatches the tool."""
        self._close_open_block()
        self._stop_reason = "tool_use"
        self._events.append(FakeEvent(type="message_delta"))
        self._events.append(FakeEvent(type="message_stop"))
        return self

    def max_tokens(self) -> "FakeTurnBuilder":
        self._close_open_block()
        self._stop_reason = "max_tokens"
        self._events.append(FakeEvent(type="message_delta"))
        self._events.append(FakeEvent(type="message_stop"))
        return self

    def usage(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read: int = 0,
        cache_creation: int = 0,
    ) -> "FakeTurnBuilder":
        self._usage = FakeUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_creation,
        )
        return self

    def _close_open_block(self) -> None:
        if self._block_open:
            self._events.append(FakeEvent(type="content_block_stop", index=self._index))
            self._index += 1
            self._block_open = False

    def build(self) -> tuple[list, "FakeFinalMessage"]:
        if self._stop_reason is None:
            raise RuntimeError(
                "FakeTurnBuilder: call end_turn() / stop_tool_use() / max_tokens() before build()"
            )
        return self._events, FakeFinalMessage(
            content=self._content,
            stop_reason=self._stop_reason,
            usage=self._usage,
        )


class FakeStream:
    """Async context manager + iterator that mirrors the SDK's
    MessageStreamManager. Works with both `async with` and `async for`,
    plus `await stream.get_final_message()`."""

    def __init__(
        self,
        events: list,
        final: "FakeFinalMessage",
        raise_on_enter: Optional[Exception] = None,
        raise_mid_stream: Optional[Exception] = None,
    ):
        self._events = events
        self._final = final
        self._raise_on_enter = raise_on_enter
        self._raise_mid_stream = raise_mid_stream
        self._pos = 0

    async def __aenter__(self):
        if self._raise_on_enter is not None:
            raise self._raise_on_enter
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        # Fire mid-stream errors halfway through the event list.
        if self._raise_mid_stream is not None and self._pos == max(1, len(self._events) // 2):
            raise self._raise_mid_stream
        if self._pos >= len(self._events):
            raise StopAsyncIteration
        ev = self._events[self._pos]
        self._pos += 1
        return ev

    async def get_final_message(self):
        return self._final


class FakeAnthropicProvider:
    """Test double that satisfies the LLMProvider protocol.

    Phase 1 of the multi-provider epic. Previously this was
    `FakeAnthropicClient` — a mock of `anthropic.AsyncAnthropic`. With
    the loop now consuming an LLMProvider directly (no more
    `client.messages.stream(...)` indirection), the fake implements the
    protocol's `stream(...)` method directly and yields LLMEvent
    instances on the wire.

    The Anthropic-shape event TYPES (content_block_start, etc) still
    live inside the builder pattern below — they remain the natural way
    to express "the model emits text + tool_use" — but this provider
    translates them to LLMEvent before yielding. That keeps the test
    DX identical while making the fake provider-shaped.

    Backward-compat: `FakeAnthropicClient` is an alias preserved at the
    bottom of this module so existing test imports keep working.
    """

    name = "anthropic"

    def __init__(self) -> None:
        # Recording surface: each call to stream() appends its kwargs
        # here. Tests assert against this list to verify what the loop
        # actually sent (model id, tools list, message history, etc).
        self.calls: list[dict] = []
        self._queued: list[FakeStream] = []
        # PR #412 review pickup 3: cache the real adapter once at
        # construction time. format_system_blocks / format_tools /
        # blocks_to_api_shape / estimate_cost all delegate to it — no
        # need to re-instantiate per call. Constructing AnthropicProvider
        # without a client is cheap (no SDK import), but the readability
        # win is real: the fake declaratively says "I am Anthropic-
        # shaped for wire concerns, custom for stream()".
        from api.agent.providers.anthropic_adapter import AnthropicProvider
        self._real_adapter = AnthropicProvider()

    # ── Test queueing API (unchanged from FakeAnthropicClient) ──────

    def queue_turn(self, built: tuple[list, "FakeFinalMessage"]) -> None:
        events, final = built
        self._queued.append(FakeStream(events=events, final=final))

    def queue_error(self, exc: Exception, *, mid_stream: bool = False) -> None:
        """Queue an error. By default the error fires when the stream is
        opened (mimics a 429 at request time). Pass mid_stream=True to
        fire halfway through event iteration (mimics a dropped connection)."""
        if mid_stream:
            events, final = (
                FakeTurnBuilder()
                .text("partial chunk").text(" before drop")
                .end_turn()
                .build()
            )
            self._queued.append(FakeStream(
                events=events, final=final, raise_mid_stream=exc,
            ))
        else:
            self._queued.append(FakeStream(
                events=[],
                final=FakeFinalMessage(),
                raise_on_enter=exc,
            ))

    def _next_stream(self) -> FakeStream:
        if not self._queued:
            raise RuntimeError(
                "FakeAnthropicProvider: no queued turns. The loop opened "
                "more streams than the test queued — likely a runaway "
                "tool-use cycle. Inspect self.calls to see what was sent."
            )
        return self._queued.pop(0)

    # ── LLMProvider protocol ──────────────────────────────────────

    def supports_model(self, model: str) -> bool:
        return model.startswith("claude-")

    def has_api_key(self) -> bool:
        return True

    def format_system_blocks(self, blocks: list[str]) -> list[dict]:
        # Byte-identical wire shape — cache verification tests assert
        # the cache_control:ephemeral wrapping that the real adapter
        # produces.
        return self._real_adapter.format_system_blocks(blocks)

    def format_tools(self, specs: tuple) -> list[dict]:
        return self._real_adapter.format_tools(specs)

    def blocks_to_api_shape(self, blocks: list) -> list[dict]:
        return self._real_adapter.blocks_to_api_shape(blocks)

    def to_assistant_message(self, stream_end) -> dict:
        return self._real_adapter.to_assistant_message(stream_end)

    def to_tool_result_messages(
        self, tool_uses_with_results: list[tuple],
    ) -> list[dict]:
        return self._real_adapter.to_tool_result_messages(tool_uses_with_results)

    def estimate_cost(self, model: str, usage: dict) -> float:
        return self._real_adapter.estimate_cost(model, usage)

    async def stream(
        self,
        *,
        model: str,
        system_blocks: list[dict],
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
    ):
        """Yield LLMEvent instances translated from the next queued
        FakeStream's Anthropic-shape events. Records the call kwargs
        on `self.calls` so tests can assert against the wire shape.
        """
        # Lazy import — keeps test collection cheap when this module
        # is touched but `stream` is never invoked.
        from api.agent.providers.base import (
            LLMStreamEnd, LLMTextDelta, LLMToolUseStart,
        )

        self.calls.append({
            "model":         model,
            "system_blocks": system_blocks,
            "messages":      messages,
            "tools":         tools,
            "max_tokens":    max_tokens,
        })
        fake_stream = self._next_stream()
        async with fake_stream as s:
            async for ev in s:
                if ev.type == "content_block_start":
                    cb = ev.content_block
                    if cb is not None and cb.type == "tool_use":
                        yield LLMToolUseStart(
                            id=cb.id or "", name=cb.name or "",
                        )
                elif ev.type == "content_block_delta":
                    d = ev.delta
                    if d is not None and d.type == "text_delta" and d.text:
                        yield LLMTextDelta(text=d.text)
            final = await s.get_final_message()
        usage_dict = {
            "input_tokens":                getattr(final.usage, "input_tokens", 0) or 0,
            "output_tokens":               getattr(final.usage, "output_tokens", 0) or 0,
            "cache_read_input_tokens":     getattr(final.usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(final.usage, "cache_creation_input_tokens", 0) or 0,
        }
        yield LLMStreamEnd(
            stop_reason=final.stop_reason,
            usage=usage_dict,
            content=list(final.content),
        )


# Backward-compat alias for existing tests that import FakeAnthropicClient.
# The two names refer to the same class; new tests should use the
# *Provider name to make the LLM-abstraction explicit.
FakeAnthropicClient = FakeAnthropicProvider


# ── FakeDeepSeekProvider (Fase 2 of the multi-provider epic) ────────


class FakeDeepSeekProvider:
    """Test double for DeepSeekProvider. Reuses the FakeTurnBuilder DX
    (queue_turn / queue_error / .calls list) so tests describe one
    model turn in the same vocabulary regardless of provider.

    The fake's stream() translates the builder's Anthropic-shape events
    to LLMEvents — identical translation logic to FakeAnthropicProvider.
    Where the providers diverge is in:
      - format_system_blocks (single concatenated block vs 4 cache_control'd)
      - format_tools (OpenAI function shape vs Anthropic input_schema)
      - to_assistant_message (content string + tool_calls vs blocks list)
      - to_tool_result_messages (N role=tool messages vs 1 user with blocks)
      - estimate_cost (DS pricing)

    All those delegations route to the REAL DeepSeekProvider via
    self._real_adapter (same pattern as FakeAnthropicProvider). Tests
    that exercise the wire shape get byte-identical output to what
    production would send.

    The REAL DeepSeek SSE parsing logic (httpx + tool_calls accumulation)
    is NOT exercised through this fake — that's tested directly against
    DeepSeekProvider with a synthetic httpx mock in
    tests/test_provider_deepseek.py (Fase 2).
    """

    name = "deepseek"

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._queued: list[FakeStream] = []
        # Construct a no-key adapter for wire-shape methods. has_api_key
        # is env-driven, so this works as long as DEEPSEEK_API_KEY is
        # set in the test env (most tests don't need it because they
        # bypass the registry and inject this fake directly).
        from api.agent.providers.deepseek_adapter import DeepSeekProvider
        self._real_adapter = DeepSeekProvider()

    # ── Test queueing API (mirrors FakeAnthropicProvider) ──────────

    def queue_turn(self, built: tuple[list, "FakeFinalMessage"]) -> None:
        events, final = built
        self._queued.append(FakeStream(events=events, final=final))

    def queue_error(self, exc: Exception, *, mid_stream: bool = False) -> None:
        if mid_stream:
            events, final = (
                FakeTurnBuilder()
                .text("partial chunk").text(" before drop")
                .end_turn()
                .build()
            )
            self._queued.append(FakeStream(
                events=events, final=final, raise_mid_stream=exc,
            ))
        else:
            self._queued.append(FakeStream(
                events=[],
                final=FakeFinalMessage(),
                raise_on_enter=exc,
            ))

    def _next_stream(self) -> FakeStream:
        if not self._queued:
            raise RuntimeError(
                "FakeDeepSeekProvider: no queued turns. The loop opened "
                "more streams than the test queued — likely a runaway "
                "tool-use cycle. Inspect self.calls to see what was sent."
            )
        return self._queued.pop(0)

    # ── LLMProvider protocol ──────────────────────────────────────

    def supports_model(self, model: str) -> bool:
        return model.startswith("deepseek-")

    def has_api_key(self) -> bool:
        return True

    def format_system_blocks(self, blocks: list[str]) -> list[dict]:
        return self._real_adapter.format_system_blocks(blocks)

    def format_tools(self, specs: tuple) -> list[dict]:
        return self._real_adapter.format_tools(specs)

    def to_assistant_message(self, stream_end) -> dict:
        return self._real_adapter.to_assistant_message(stream_end)

    def to_tool_result_messages(
        self, tool_uses_with_results: list[tuple],
    ) -> list[dict]:
        return self._real_adapter.to_tool_result_messages(tool_uses_with_results)

    def estimate_cost(self, model: str, usage: dict) -> float:
        return self._real_adapter.estimate_cost(model, usage)

    async def stream(
        self,
        *,
        model: str,
        system_blocks: list[dict],
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
    ):
        """Translate the builder's Anthropic-shape events to LLMEvents.
        Identical translation as FakeAnthropicProvider — the fakes
        agree on the internal LLMEvent contract; only the WIRE shapes
        differ, and those are exercised by the format_* / to_* methods.

        The final block list yielded in LLMStreamEnd.content carries
        the builder's typed objects (FakeContentBlock with .type/.text/
        .id/.name/.input attributes). The loop reads attributes
        generically, so this works without DS-specific synthesis.
        """
        from api.agent.providers.base import (
            LLMStreamEnd, LLMTextDelta, LLMToolUseStart,
        )

        self.calls.append({
            "model":         model,
            "system_blocks": system_blocks,
            "messages":      messages,
            "tools":         tools,
            "max_tokens":    max_tokens,
        })
        fake_stream = self._next_stream()
        async with fake_stream as s:
            async for ev in s:
                if ev.type == "content_block_start":
                    cb = ev.content_block
                    if cb is not None and cb.type == "tool_use":
                        yield LLMToolUseStart(
                            id=cb.id or "", name=cb.name or "",
                        )
                elif ev.type == "content_block_delta":
                    d = ev.delta
                    if d is not None and d.type == "text_delta" and d.text:
                        yield LLMTextDelta(text=d.text)
            final = await s.get_final_message()
        usage_dict = {
            "input_tokens":                getattr(final.usage, "input_tokens", 0) or 0,
            "output_tokens":               getattr(final.usage, "output_tokens", 0) or 0,
            "cache_read_input_tokens":     0,  # DS doesn't report cache stats
            "cache_creation_input_tokens": 0,
        }
        yield LLMStreamEnd(
            stop_reason=final.stop_reason,
            usage=usage_dict,
            content=list(final.content),
        )


# Cheap self-test at import — failures here surface as ImportError at
# test collection, not as confusing failures inside a test body.
def _self_test_fake_anthropic_client() -> None:
    events, final = (
        FakeTurnBuilder()
        .text("Hola ").text("mundo")
        .tool_use("get_positions", {}, tool_use_id="toolu_1")
        .stop_tool_use()
        .usage(input_tokens=10, output_tokens=5, cache_read=8)
        .build()
    )
    # Stream order: text_start, 2x text_delta, text_stop, tool_use_start,
    # input_json_delta, tool_use_stop, message_delta, message_stop.
    type_seq = [e.type for e in events]
    assert type_seq[0] == "content_block_start"
    assert type_seq.count("content_block_delta") == 3  # 2 text + 1 tool input
    assert type_seq[-1] == "message_stop"
    assert final.stop_reason == "tool_use"
    assert final.usage.cache_read_input_tokens == 8
    assert len(final.content) == 2
    assert final.content[0].type == "text"
    assert final.content[1].type == "tool_use"


_self_test_fake_anthropic_client()
