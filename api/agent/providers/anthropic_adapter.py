"""Anthropic adapter — wraps anthropic.AsyncAnthropic in the LLMProvider
contract. Phase 1 of the multi-provider epic.

This module owns:
  - The Anthropic SDK import (lazy)
  - `cache_control: ephemeral` injection into system blocks
  - Anthropic-shape tool schema (`{name, description, input_schema}`)
  - Anthropic event type translation (content_block_start /
    content_block_delta / message_delta / message_stop → LLMEvent)
  - Anthropic pricing table + cost calculation
  - Coercion of SDK typed content blocks back to dict shape for
    re-sending in the next turn's `messages` array

Phase 1 is BEHAVIORALLY IDEMPOTENT — every Anthropic test that passed
before this refactor must still pass after, byte-identical wire output.
The translation here is a relocation of code that was previously in
api/agent/loop.py + api/agent/prompts/system.py, not a redesign.

Pre-reg §3 of docs/superpowers/specs/es/2026-05-20-multi-provider-copilot-pre-reg.md
"""
from __future__ import annotations

import logging
import os
from typing import Any, AsyncIterator

from api.agent.providers.base import (
    LLMEvent,
    LLMStreamEnd,
    LLMTextDelta,
    LLMToolUseEnd,
    LLMToolUseStart,
)

log = logging.getLogger("api.agent.providers.anthropic")


# ── Pricing ─────────────────────────────────────────────────────────


# USD per 1M tokens. Cached snapshot 2026-04-29:
#   - claude-opus-4-7   — $5.00 in / $25.00 out, 1M ctx
#   - claude-sonnet-4-6 — $3.00 in / $15.00 out, 1M ctx
#   - claude-haiku-4-5  — $1.00 in / $5.00  out, 200K ctx
#
# Cache reads are billed at ~0.1× input; cache writes (creation) at
# ~1.25× input. The cost calc below applies those multipliers.
#
# Phase 1 relocation: this used to live in api/agent/loop.py as
# `_MODEL_PRICING`. Moved here per the multi-provider spec — each
# provider owns its own pricing table.
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-7":   {"in": 5.00, "out": 25.00},
    "claude-sonnet-4-6": {"in": 3.00, "out": 15.00},
    "claude-haiku-4-5":  {"in": 1.00, "out":  5.00},
}


# ── The adapter ─────────────────────────────────────────────────────


class AnthropicProvider:
    """LLMProvider implementation wrapping anthropic.AsyncAnthropic.

    Constructed lazily by the registry; the actual SDK import is
    deferred until `stream()` runs, so tests can use a fake provider
    without anthropic installed in the test environment.
    """

    name = "anthropic"

    def __init__(self, *, client: Any | None = None) -> None:
        """`client` is an already-constructed AsyncAnthropic instance.
        Tests inject a FakeAnthropicClient that mirrors the SDK's
        `messages.stream(...)` surface. Production resolves the real
        client via the registry's factory."""
        self._client = client

    # ── Discovery ───────────────────────────────────────────────────

    def supports_model(self, model: str) -> bool:
        return model.startswith("claude-")

    def has_api_key(self) -> bool:
        return bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip())

    # ── Wire-format translators ─────────────────────────────────────

    def format_system_blocks(self, blocks: list[str]) -> list[dict]:
        """Each block becomes a `text` block with cache_control:ephemeral.
        Anthropic accepts up to 4 cache_control breakpoints; the caller
        is responsible for not exceeding that (build_system_blocks emits
        exactly 4 today)."""
        return [
            {
                "type":          "text",
                "text":          block,
                "cache_control": {"type": "ephemeral"},
            }
            for block in blocks
        ]

    def format_tools(self, specs: tuple) -> list[dict]:
        """Convert a tuple of ToolSpec into Anthropic's tools array.

        Anthropic shape: `[{name, description, input_schema}, ...]`.
        Strips `title` keys from the JSON schema for cache determinism
        (Pydantic injects them inferred from field names; removing them
        keeps the bytes stable across pydantic versions).
        """
        out = []
        for spec in specs:
            schema = spec.schema.model_json_schema()
            _strip_titles(schema)
            out.append({
                "name":         spec.name,
                "description":  spec.description,
                "input_schema": schema,
            })
        return out

    # ── Streaming ───────────────────────────────────────────────────

    async def stream(
        self,
        *,
        model: str,
        system_blocks: list[dict],
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
    ) -> AsyncIterator[LLMEvent]:
        """Open one streaming connection against `anthropic.messages.stream`.

        Yields:
          - LLMTextDelta for each text chunk
          - LLMToolUseStart when a tool_use block opens
          - LLMStreamEnd as the terminal event with usage + content

        Phase 1 note: we don't yield LLMToolUseEnd today — the loop
        reads tool_use blocks off LLMStreamEnd.content (matches the
        pre-refactor behavior). Phase 2's DeepSeek adapter MUST emit
        LLMToolUseEnd since DS streams tool_calls differently.
        """
        if self._client is None:
            raise RuntimeError(
                "AnthropicProvider used without a client. The registry "
                "factory must construct one with the SDK or tests must "
                "inject a fake."
            )

        async with self._client.messages.stream(
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
                        yield LLMToolUseStart(
                            id=cb.id or "",
                            name=cb.name or "",
                        )
                elif event.type == "content_block_delta":
                    d = event.delta
                    if d is not None and d.type == "text_delta" and d.text:
                        yield LLMTextDelta(text=d.text)
            final = await stream.get_final_message()

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

    # ── Re-send shape ───────────────────────────────────────────────

    def blocks_to_api_shape(self, blocks: list) -> list[dict]:
        """Coerce the SDK's typed content blocks back to the dict form
        the API accepts in the next request's `messages` array.

        Mirrors the pre-refactor `_blocks_to_api_shape` in loop.py."""
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

    # ── Cost ────────────────────────────────────────────────────────

    def estimate_cost(self, model: str, usage: dict) -> float:
        """Per-hop USD cost. The loop sums across hops; this returns
        the cost of a SINGLE hop given its `usage` dict.

        Cache discount applies here (read at 0.1×, creation at 1.25×).
        """
        p = MODEL_PRICING.get(model)
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


# ── Helpers ─────────────────────────────────────────────────────────


def _strip_titles(node: Any) -> None:
    """Recursively remove `title` keys from a JSON-schema dict tree.
    Cache-prefix determinism — Pydantic injects titles, we strip them
    so the bytes don't shift across Pydantic versions. Pre-reg §7.5.
    """
    if isinstance(node, dict):
        node.pop("title", None)
        for v in node.values():
            _strip_titles(v)
    elif isinstance(node, list):
        for v in node:
            _strip_titles(v)


def build_anthropic_client() -> Any:
    """Construct an AsyncAnthropic against ANTHROPIC_API_KEY. Imported
    lazily so the dependency isn't loaded until a real Anthropic call
    is in flight.

    Called from the registry's factory; tests bypass this by injecting
    a fake provider directly.
    """
    from anthropic import AsyncAnthropic  # noqa: PLC0415
    return AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())
