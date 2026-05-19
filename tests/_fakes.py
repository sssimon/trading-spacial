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


class FakeMessages:
    """Mirror of `client.messages.*`. Phase 2 only uses `stream(**kwargs)`."""

    def __init__(self, client: "FakeAnthropicClient"):
        self._client = client

    def stream(self, **kwargs):
        # Record the kwargs so tests can assert what the loop sent
        # (model, system layout, tools, messages, etc).
        self._client.calls.append(kwargs)
        return self._client._next_stream()


class FakeAnthropicClient:
    """Drop-in replacement for `anthropic.AsyncAnthropic` in tests.

    Each test queues one FakeStream per expected turn. If the loop opens
    more streams than queued, the fake raises a loud error — silent
    fall-through to "default response" is the kind of bug we won't catch.
    """

    def __init__(self):
        self.messages = FakeMessages(self)
        self.calls: list[dict] = []
        self._queued: list[FakeStream] = []

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
                "FakeAnthropicClient: no queued turns. The loop opened "
                "more streams than the test queued — likely a runaway "
                "tool-use cycle. Inspect self.calls to see what was sent."
            )
        return self._queued.pop(0)


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
