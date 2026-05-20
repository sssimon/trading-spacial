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

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, AsyncIterator, Optional

from api.agent.prompts import build_system_blocks
from api.agent.providers.base import (
    LLMStreamEnd,
    LLMTextDelta,
    LLMToolUseStart,
)
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
class ProposalEvent:
    """Side-effect proposal emitted by a propose_* tool. Carries the
    HMAC-signed payload that the frontend must echo back on confirm.
    The model itself never sees the signed_payload — the loop strips
    the `_proposal` envelope from the tool_result before handing it
    back to the model. Pre-reg §10.
    """
    proposal_id:    str
    signed_payload: str
    action:         str
    args:           dict
    expires_at:     str
    summary:        str


@dataclass(frozen=True)
class MessageEnd:
    usage: dict            # input_tokens / output_tokens / cache_read / cache_creation
    stop_reason: str
    cost_usd: float


@dataclass(frozen=True)
class ErrorEvent:
    reason: str            # "upstream" | "too_many_tool_hops" | "conversation_cap_reached"
    user_message: str


LoopEvent = (
    TextDelta | ToolUseStart | ToolUseResult | ProposalEvent
    | MessageEnd | ErrorEvent
)


# ── Tool schema cache ──────────────────────────────────────────────────


@lru_cache(maxsize=16)
def _cached_formatted_tools(surface: str, provider_name: str) -> tuple[dict, ...]:
    """Cache the formatted tools array keyed on (surface, provider name).

    Phase 1 of the multi-provider epic: this replaces the previous
    `_build_anthropic_tools(surface)` cache which assumed only one
    provider. Now keyed on (surface, provider_name) because different
    providers emit different wire shapes from the same ToolSpec.

    `maxsize=16` covers 5 surfaces × up to 3 providers + headroom.

    Returns a tuple so callers can't reassign elements. NOTE: inner
    dicts remain mutable; callers MUST treat as read-only — mutating
    any nested schema corrupts the cache for every subsequent request.
    """
    from api.agent.providers.anthropic_adapter import AnthropicProvider
    specs = tools_for_surface(surface)
    if provider_name == "anthropic":
        # Construct a no-client adapter just for formatting. format_tools
        # doesn't need the SDK; this avoids pulling in anthropic just to
        # serialize a JSON schema.
        return tuple(AnthropicProvider().format_tools(specs))
    # Phase 2 of the multi-provider epic adds:
    # if provider_name == "deepseek":
    #     return tuple(DeepSeekProvider().format_tools(specs))
    raise ValueError(
        f"unknown provider name for tool formatting: {provider_name!r}"
    )


# Backward-compat shim: a couple of older tests imported
# `_build_anthropic_tools` directly. The Phase 1 refactor split it into
# `_cached_formatted_tools(surface, provider_name)`. Tests that still
# use the old name get the Anthropic-shape result by default.
def _build_anthropic_tools(surface: str) -> tuple[dict, ...]:
    return _cached_formatted_tools(surface, "anthropic")


# ── Cost calculator (compat shim) ──────────────────────────────────────
#
# Phase 1 of the multi-provider epic: pricing tables moved to each
# provider's module (api/agent/providers/anthropic_adapter.py:MODEL_PRICING
# for Anthropic). The loop no longer owns the numbers.
#
# `_estimate_cost_usd` remains exported as a backward-compat shim because
# test_agent_loop.py::test_cost_estimation_matches_published_pricing
# imports it directly. The shim dispatches to whichever provider claims
# the model — by prefix match against ALLOWED_MODELS.


def _estimate_cost_usd(model: str, usage: dict) -> float:
    """Compute the USD cost for one hop's `usage`. Dispatches to the
    provider that owns the model via the registry.

    Returns 0.0 if no provider claims the model (silent fallback —
    matches pre-refactor behavior for unknown model ids).
    """
    from api.agent.providers.anthropic_adapter import AnthropicProvider
    if model.startswith("claude-"):
        return AnthropicProvider().estimate_cost(model, usage)
    # Phase 2 adds: if model.startswith("deepseek-"): ...
    return 0.0


# ── Loop ────────────────────────────────────────────────────────────────


async def run_turn(
    *,
    client: Any,
    model: str,
    surface: str,
    messages: list[dict],
    tenant_id: int,
    conversation_id: str = "",
    max_tokens: int = 4096,
) -> AsyncIterator[LoopEvent]:
    """Drive one user turn through the model + tool loop. Yields typed
    events as the model streams and tools fire.

    Mutates `messages` in place (appending the assistant message and any
    tool_result user messages). The caller owns the conversation state
    across turns; this function only handles one user-turn → final-text
    cycle.

    Note on `client`: as of Phase 1 of the multi-provider epic, the
    `client` parameter is actually an `LLMProvider` (not the
    anthropic.AsyncAnthropic SDK client anymore). The parameter name is
    preserved to minimize churn across the 13+ test call sites; a future
    PR may rename. The provider's `stream(...)` method yields LLMEvent
    instances which this loop translates to LoopEvent.

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

    provider = client  # local alias for readability — see docstring
    raw_blocks = build_system_blocks(surface)
    system_blocks = provider.format_system_blocks(raw_blocks)
    tools = list(_cached_formatted_tools(surface, provider.name))
    hops = 0

    # PR #408 review fix: accumulate usage + cost across all hops of the
    # turn. The model bills per API call — a multi-hop turn fires N+1
    # calls (N tool_use intermediate + 1 final text). The original
    # implementation emitted only the final hop's cost in MessageEnd,
    # undercharging the tenant quota by 30-60% on turns with multiple
    # tool calls. Accumulating here keeps quota + breaker honest.
    total_usage = {
        "input_tokens":                0,
        "output_tokens":               0,
        "cache_read_input_tokens":     0,
        "cache_creation_input_tokens": 0,
    }
    total_cost_usd = 0.0

    while True:
        # Open a fresh stream for each model turn in the agentic loop.
        # The provider yields LLMEvent dataclasses; we translate to the
        # LoopEvent vocabulary (TextDelta / ToolUseStart / MessageEnd).
        hop_usage: dict = {}
        final_stop_reason: str = ""
        final_content: list = []
        try:
            async for ev in provider.stream(
                model=model,
                system_blocks=system_blocks,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
            ):
                if isinstance(ev, LLMTextDelta):
                    yield TextDelta(text=ev.text)
                elif isinstance(ev, LLMToolUseStart):
                    yield ToolUseStart(tool=ev.name)
                elif isinstance(ev, LLMStreamEnd):
                    hop_usage = ev.usage
                    final_stop_reason = ev.stop_reason
                    final_content = ev.content
                # Other LLMEvent types (LLMReasoningDelta, LLMToolUseEnd)
                # are silently dropped in Phase 1. Phase 3 of the
                # multi-provider epic wires LLMReasoningDelta to a new
                # LoopEvent + SSE frame.
        except Exception as e:  # noqa: BLE001
            log.warning("agent loop upstream error: %s", e, exc_info=True)
            yield ErrorEvent(
                reason="upstream",
                user_message=(
                    "El copiloto está saturado, intenta de nuevo en unos segundos."
                ),
            )
            return

        # Accumulate this hop's usage + cost into the per-turn totals.
        # MessageEnd (below, on the final hop) emits the sum so audit /
        # quota / breaker see the true cost — not just the last call.
        for k in total_usage:
            total_usage[k] += int(hop_usage.get(k, 0) or 0)
        total_cost_usd += provider.estimate_cost(model, hop_usage)

        if final_stop_reason != "tool_use":
            yield MessageEnd(
                usage=total_usage,
                stop_reason=final_stop_reason,
                cost_usd=total_cost_usd,
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
            "content": provider.blocks_to_api_shape(final_content),
        })

        # Collect tool_results for ALL tool_use blocks in this turn into
        # ONE user message. The API rejects a turn that splits
        # tool_results across multiple user messages.
        tool_uses = [b for b in final_content if b.type == "tool_use"]
        tool_results = []
        for tu in tool_uses:
            result_json = dispatch_tool(
                tu.name or "", tu.input or {},
                tenant_id=tenant_id,
                conversation_id=conversation_id,
            )
            # is_error: structural detection (PR #404 review issue 2).
            # The previous `'"error"' in result_json` substring match
            # produced false positives on legitimate payloads carrying
            # the string "error" inside a value (e.g. an enum like
            # "no_error_state" or an exit_reason "liquidation_error").
            # Parse the JSON and treat a top-level dict with an "error"
            # key as an error response; parse failure is treated as
            # error too (defensive).
            try:
                parsed = json.loads(result_json)
                is_error = isinstance(parsed, dict) and "error" in parsed
            except json.JSONDecodeError:
                is_error = True
                parsed = None
            yield ToolUseResult(
                tool=tu.name or "",
                status=("error" if is_error else "ok"),
            )

            # Phase 3 (#400): if a propose_* tool returned a `_proposal`
            # envelope, yield a ProposalEvent so the frontend can render
            # the amber confirm button. Strip the envelope from the
            # tool_result content the model sees — the signed_payload
            # NEVER flows through model context (pre-reg §10.1).
            content_for_model = result_json
            if not is_error and isinstance(parsed, dict) and "_proposal" in parsed:
                env = parsed["_proposal"]
                yield ProposalEvent(
                    proposal_id=env["proposal_id"],
                    signed_payload=env["signed_payload"],
                    action=env["action"],
                    args=env["args"],
                    expires_at=env["expires_at"],
                    summary=env["summary"],
                )
                # Rebuild the content without the _proposal envelope.
                model_visible = {k: v for k, v in parsed.items() if k != "_proposal"}
                content_for_model = json.dumps(model_visible, default=str)

            tool_results.append({
                "type":          "tool_result",
                "tool_use_id":   tu.id,
                "content":       content_for_model,
                "is_error":      is_error,
            })
        messages.append({"role": "user", "content": tool_results})

        # Loop back: send the tool_results to the model.


# Phase 1 of the multi-provider epic: `_blocks_to_api_shape` moved to
# `AnthropicProvider.blocks_to_api_shape`. The loop now delegates via
# `provider.blocks_to_api_shape(content)` so each provider owns the
# coercion from its content-block shape back to wire-dict form.
