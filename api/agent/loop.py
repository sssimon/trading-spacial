"""Server-side agentic loop — Phase 2 of epic #400.

The loop yields typed events; the streaming layer in `streaming.py`
translates those into SSE frames for the wire.

Contract (pre-reg §6.1, the corrected pseudocode after PR #401 review):

  - Assistant message appended to `messages` exactly ONCE per model turn,
    carrying ALL content blocks (text + tool_use). Splitting it per
    tool_use is a wire-format error — Anthropic Messages API rejects.

  - Tool results for all tool_use blocks in a turn collected into ONE
    user message with the tool_result content blocks. The API rejects
    a turn that splits tool_results across multiple user messages.

  - MAX_TOOL_HOPS limits the agentic recursion within a single user
    request. Beyond it, the loop yields `error(too_many_tool_hops)` and
    stops; the model can be re-prompted in a new user turn.

Tenant scoping (pre-reg §5, §8):

  - The model never receives `tenant_id` as input on any tool.
  - dispatch_tool() binds tenant_id keyword-only when invoking the
    handler. The model can't override it because it doesn't appear in
    the tool's input_schema.

Failure modes (pre-reg §6.3):

  - Upstream RateLimitError / 5xx → yield `error(reason="upstream")` and
    stop. The HTTP layer retries with backoff; this layer doesn't.
  - Tool handler raises → dispatch_tool serializes the failure as
    `{"error": "..."}` JSON content; the next model turn sees the error
    and self-corrects.
  - Conversation > N turns (default 30, configurable) → yield
    `conversation_cap_reached`; UI forces handoff to a new conversation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

from api.agent.prompts import build_system_blocks
from api.agent.tools.handlers import dispatch_tool
from api.agent.tools.registry import tools_for_surface

log = logging.getLogger("api.agent.loop")


MAX_TOOL_HOPS = 4
MAX_TURNS_PER_CONVERSATION = 30


# ── Event types yielded by the loop ────────────────────────────────────


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolUseStart:
    tool: str


@dataclass(frozen=True)
class ToolUseResult:
    tool: str
    status: str            # "ok" | "error"


@dataclass(frozen=True)
class MessageEnd:
    usage: dict            # input_tokens / output_tokens / cache_read / cache_creation
    stop_reason: str
    cost_usd: float


@dataclass(frozen=True)
class ErrorEvent:
    reason: str            # "upstream" | "too_many_tool_hops" | "conversation_cap_reached"
    user_message: str


LoopEvent = TextDelta | ToolUseStart | ToolUseResult | MessageEnd | ErrorEvent


# ── Tool schema builder (registry → Anthropic tools array) ──────────────


def _build_anthropic_tools(surface: str) -> list[dict]:
    """Convert the per-surface tool subset into the JSON Schema shape the
    Messages API expects."""
    specs = tools_for_surface(surface)
    out = []
    for spec in specs:
        # Pydantic schemas → JSON Schema. We strip `title` keys for cache
        # determinism (Pydantic injects "title" inferred from field
        # names; removing them keeps the bytes stable across pydantic
        # versions). DETERMINISTIC SERIALIZATION is the spine of the
        # prompt cache strategy — pre-reg §7.5 silent invalidators.
        schema = spec.schema.model_json_schema()
        _strip_titles(schema)
        out.append({
            "name": spec.name,
            "description": spec.description,
            "input_schema": schema,
        })
    return out


def _strip_titles(node: Any) -> None:
    """Recursively remove `title` keys from a JSON-schema dict tree."""
    if isinstance(node, dict):
        node.pop("title", None)
        for v in node.values():
            _strip_titles(v)
    elif isinstance(node, list):
        for v in node:
            _strip_titles(v)


# ── Cost model (token counts → USD) ─────────────────────────────────────
#
# Pricing in USD per 1M tokens, copied from the claude-api skill. Update
# this map deliberately when bumping the anthropic SDK pin (pre-reg
# §12.7 — Buffers internos).

_MODEL_PRICING = {
    "claude-opus-4-7":   {"in": 5.00, "out": 25.00},
    "claude-sonnet-4-6": {"in": 3.00, "out": 15.00},
    "claude-haiku-4-5":  {"in": 1.00, "out":  5.00},
}


def _estimate_cost_usd(model: str, usage: dict) -> float:
    """Compute the USD cost of a turn from `response.usage`. Cache reads
    are billed at ~0.1× input; cache writes (creation) at ~1.25×.
    """
    p = _MODEL_PRICING.get(model)
    if not p:
        return 0.0
    in_per_m = p["in"]
    out_per_m = p["out"]
    in_uncached = (usage.get("input_tokens") or 0)
    cache_read = (usage.get("cache_read_input_tokens") or 0)
    cache_creation = (usage.get("cache_creation_input_tokens") or 0)
    out_tokens = (usage.get("output_tokens") or 0)
    cost = (
        in_uncached * in_per_m
        + cache_read * in_per_m * 0.1
        + cache_creation * in_per_m * 1.25
        + out_tokens * out_per_m
    ) / 1_000_000.0
    return round(cost, 6)


# ── Loop ────────────────────────────────────────────────────────────────


async def run_turn(
    *,
    client: Any,
    model: str,
    surface: str,
    messages: list[dict],
    tenant_id: int,
    max_tokens: int = 4096,
) -> AsyncIterator[LoopEvent]:
    """Drive one user turn through the model + tool loop. Yields typed
    events as the model streams and tools fire.

    Mutates `messages` in place (appending the assistant message and any
    tool_result user messages). The caller owns the conversation state
    across turns; this function only handles one user-turn → final-text
    cycle.

    Pre-reg §6.1.
    """
    if len(messages) > MAX_TURNS_PER_CONVERSATION:
        yield ErrorEvent(
            reason="conversation_cap_reached",
            user_message=(
                "Esta conversación llegó al límite de turnos. "
                "Abre una nueva conversación para seguir."
            ),
        )
        return

    system_blocks = build_system_blocks(surface)
    tools = _build_anthropic_tools(surface)
    hops = 0

    while True:
        # Open a fresh stream for each "model turn" in the agentic loop.
        try:
            async with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=system_blocks,
                tools=tools,
                messages=messages,
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_start":
                        cb = event.content_block
                        if cb is not None and cb.type == "tool_use":
                            yield ToolUseStart(tool=cb.name or "")
                    elif event.type == "content_block_delta":
                        d = event.delta
                        if d is not None and d.type == "text_delta" and d.text:
                            yield TextDelta(text=d.text)
                final = await stream.get_final_message()
        except Exception as e:  # noqa: BLE001
            log.warning("agent loop upstream error: %s", e, exc_info=True)
            yield ErrorEvent(
                reason="upstream",
                user_message=(
                    "El copiloto está saturado, intenta de nuevo en unos segundos."
                ),
            )
            return

        usage_dict = {
            "input_tokens":                getattr(final.usage, "input_tokens", 0) or 0,
            "output_tokens":               getattr(final.usage, "output_tokens", 0) or 0,
            "cache_read_input_tokens":     getattr(final.usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(final.usage, "cache_creation_input_tokens", 0) or 0,
        }

        if final.stop_reason != "tool_use":
            yield MessageEnd(
                usage=usage_dict,
                stop_reason=final.stop_reason,
                cost_usd=_estimate_cost_usd(model, usage_dict),
            )
            return

        # stop_reason == "tool_use" — we have one or more tool_use blocks
        # to dispatch. Bump the hop counter; refuse if we've gone too deep.
        hops += 1
        if hops > MAX_TOOL_HOPS:
            yield ErrorEvent(
                reason="too_many_tool_hops",
                user_message=(
                    "No pude completar la consulta — intenta reformularla."
                ),
            )
            return

        # Append the assistant message ONCE (carries all tool_use blocks).
        # See pre-reg §6.1 — splitting per tool_use is a wire-format error.
        messages.append({
            "role": "assistant",
            "content": _blocks_to_api_shape(final.content),
        })

        # Collect tool_results for ALL tool_use blocks in this turn into
        # ONE user message. The API rejects a turn that splits
        # tool_results across multiple user messages.
        tool_uses = [b for b in final.content if b.type == "tool_use"]
        tool_results = []
        for tu in tool_uses:
            result_json = dispatch_tool(
                tu.name or "", tu.input or {}, tenant_id=tenant_id,
            )
            is_error = '"error"' in result_json
            yield ToolUseResult(
                tool=tu.name or "",
                status=("error" if is_error else "ok"),
            )
            tool_results.append({
                "type":          "tool_result",
                "tool_use_id":   tu.id,
                "content":       result_json,
                "is_error":      is_error,
            })
        messages.append({"role": "user", "content": tool_results})

        # Loop back: send the tool_results to the model.


def _blocks_to_api_shape(blocks: list) -> list[dict]:
    """Convert the SDK's typed content blocks (or our fake's) into the
    plain-dict shape the API accepts in the `messages` array.

    The API doesn't accept the typed objects directly when echoed back —
    it wants the raw dict form."""
    out = []
    for b in blocks:
        if b.type == "text":
            out.append({"type": "text", "text": b.text or ""})
        elif b.type == "tool_use":
            out.append({
                "type":  "tool_use",
                "id":    b.id,
                "name":  b.name,
                "input": b.input or {},
            })
    return out
