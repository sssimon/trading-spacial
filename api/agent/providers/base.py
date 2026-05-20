"""LLMProvider protocol + the internal event types adapters yield.

Two layers of events flow through the agent system:

  LLMEvent (this module): what the provider adapter yields per stream
  chunk. Provider-agnostic — TextDelta is TextDelta whether it came
  from Anthropic or DeepSeek. The loop consumes LLMEvents.

  LoopEvent (api/agent/loop.py): what the loop yields to the streaming
  layer. Provider-agnostic AND server-side-aware — includes
  ToolUseResult (after the loop dispatches and gets the result),
  ProposalEvent (after the loop detects an _proposal envelope), and
  MessageEnd (after the loop sums multi-hop usage).

The split is intentional: the provider doesn't invent proposals
(server-side concern, fired post-dispatch); the loop doesn't format
wire (provider-side concern, lives in the adapter).

Pre-reg §3.2 + §3.3 of the multi-provider spec.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol


# ── Synthetic content blocks ─────────────────────────────────────────


# Adapters that don't get typed objects from their SDK (e.g. DeepSeek,
# which is OpenAI-shape JSON) synthesize block instances of these
# dataclasses and put them in LLMStreamEnd.content. The loop reads
# `.type` and dispatches generically; `to_assistant_message` knows how
# to serialize them back out to its wire shape. The Anthropic adapter
# uses the SDK's TextBlock/ToolUseBlock instances directly — those
# happen to expose the same attribute names, so the loop's reads
# work either way.


@dataclass(frozen=True)
class SyntheticTextBlock:
    text: str
    type: str = "text"


@dataclass(frozen=True)
class SyntheticToolUseBlock:
    id: str
    name: str
    input: dict
    type: str = "tool_use"


# ── LLMEvent closed enum ─────────────────────────────────────────────


@dataclass(frozen=True)
class LLMTextDelta:
    """Streaming text chunk from the model's final content."""
    text: str


@dataclass(frozen=True)
class LLMToolUseStart:
    """The model started emitting a tool_use block. Adapter yields this
    when the tool name first becomes known (Anthropic's
    content_block_start with type=tool_use)."""
    id: str          # tool_use_id from the wire (toolu_* in Anthropic)
    name: str        # tool name


@dataclass(frozen=True)
class LLMToolUseEnd:
    """The model finished emitting a tool_use block. `input` is the
    fully parsed kwarg dict (adapter accumulates the JSON delta stream
    and parses at the end)."""
    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class LLMReasoningDelta:
    """Streaming chunk of reasoning content. Today only DeepSeek-R1
    emits this; the loop in Phase 1 doesn't act on it (Phase 3 wires
    it into a new SSE event type for the frontend). Kept in the
    closed enum from Phase 1 so adapters don't have to grow it later
    and risk breaking parity."""
    text: str


@dataclass(frozen=True)
class LLMStreamEnd:
    """The model finished the turn (either to emit tool_use, hit max
    tokens, or to deliver final text). `content` is the assistant
    message's raw content blocks (used by the loop to re-append on
    multi-hop turns and to walk tool_use blocks for dispatch)."""
    stop_reason: str  # "end_turn" | "tool_use" | "max_tokens" | ...
    usage: dict       # {input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens}
    content: list     # list of SDK content blocks (or fake equivalents)


LLMEvent = (
    LLMTextDelta
    | LLMToolUseStart
    | LLMToolUseEnd
    | LLMReasoningDelta
    | LLMStreamEnd
)


# ── The provider protocol ────────────────────────────────────────────


class LLMProvider(Protocol):
    """The interface the loop consumes. Each concrete adapter (Anthropic,
    DeepSeek, etc.) implements all methods.

    `name` is the canonical provider name ("anthropic" | "deepseek" | ...).
    It's persisted in the audit table's `provider` column (added in
    Fase 4 of the multi-provider epic) so per-provider telemetry works.
    """

    name: str

    def supports_model(self, model: str) -> bool:
        """True if this provider should be used for `model`. Used by
        the registry's tie-breaker — usually delegated to a prefix
        match against `name`."""
        ...

    def has_api_key(self) -> bool:
        """True if the provider's API key is configured (env var set,
        non-empty). Read by api/agent/config.get_agent_status to decide
        whether the default provider can serve traffic."""
        ...

    def format_system_blocks(self, blocks: list[str]) -> list[dict]:
        """Convert provider-neutral text blocks into the wire shape the
        provider expects. Anthropic adds cache_control:ephemeral to each
        block; DeepSeek emits a single concatenated text block.

        Pre-reg §2.2 (cache strategy divergent).
        """
        ...

    def format_tools(self, specs: tuple) -> list[dict]:
        """Convert a tuple of ToolSpec into the provider's tool-array
        wire shape.

          Anthropic: [{name, description, input_schema}, ...]
          OpenAI/DeepSeek: [{type:"function", function:{name, description, parameters}}, ...]

        The `specs` argument is a tuple (not list) so it's hashable —
        the caller may wrap this call in an lru_cache keyed on
        (surface, provider.name).
        """
        ...

    def stream(
        self,
        *,
        model: str,
        system_blocks: list[dict],
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
    ) -> AsyncIterator[LLMEvent]:
        """Open a streaming connection. Yields LLMEvent instances until
        the model emits an LLMStreamEnd. The caller is responsible for
        consuming the stream fully (or closing on cancel).

        The implementation handles SDK-specific event types internally
        and only emits the protocol's LLMEvent dataclasses on the wire.
        """
        ...

    def to_assistant_message(self, stream_end: "LLMStreamEnd") -> dict:
        """Build the full assistant message dict to append to the
        conversation history for the next hop.

        Anthropic shape:
            {"role": "assistant",
             "content": [{"type": "text", "text": "..."},
                         {"type": "tool_use", "id": "...", "name": "...", "input": {...}}]}

        DeepSeek shape:
            {"role": "assistant",
             "content": "<all text concatenated>",
             "tool_calls": [{"id": "...", "type": "function",
                             "function": {"name": "...", "arguments": "<json string>"}}]}

        The loop appends the return value directly to `messages` —
        provider owns the full shape.
        """
        ...

    def to_tool_result_messages(
        self, tool_uses_with_results: list[tuple],
    ) -> list[dict]:
        """Build the message(s) to append after dispatching tools.

        `tool_uses_with_results` is a list of `(tool_use_block, content_string,
        is_error)` tuples — one per dispatched tool in the hop.

        Anthropic returns ONE user message with a list of tool_result
        content blocks (the API rejects splitting across messages).

        DeepSeek (OpenAI-shape) returns N messages with `role="tool"`,
        one per tool_call, each with the tool_call_id reference.
        """
        ...

    def estimate_cost(self, model: str, usage: dict) -> float:
        """Compute USD cost for one hop, given the wire-reported usage.

        Each provider owns its pricing table (Anthropic in
        api/agent/providers/anthropic_adapter.py, DeepSeek in its own
        module). The loop sums across hops and emits the total on
        MessageEnd. Pre-reg §2.4 (cost model per-provider).
        """
        ...
