"""DeepSeek adapter — implements LLMProvider over DeepSeek's
OpenAI-compatible Chat Completions API (https://api.deepseek.com/v1).

Fase 2 of the multi-provider epic. This module owns:
  - Single concatenated system block (DeepSeek has no client-side
    cache_control; the prefix gets auto-cached if stable).
  - OpenAI-shape tool wire format `{type:"function", function:{name,
    description, parameters}}`.
  - SSE streaming via httpx, parsing OpenAI-style delta chunks into
    LLMEvent instances.
  - Synthetic content blocks (SyntheticTextBlock + SyntheticToolUseBlock)
    that pass through the loop's `.type`/`.id`/`.name`/`.input` reads
    just like Anthropic's typed objects.
  - DeepSeek pricing table (V3 only in Fase 2; R1/reasoner in Fase 3).
  - Translation between Anthropic-shape messages (the loop's internal
    lingua franca through Fase 1) and DeepSeek-shape messages on the
    way to the HTTP call (the wire). The loop's messages are in the
    ACTIVE provider's shape — DS messages have role=tool + tool_calls
    on assistant.

Why httpx over the openai SDK:
  - Keeps cost model fully under our control (no per-1M discount
    surprises baked into a client lib).
  - Avoids pulling openai for a non-OpenAI provider; cleaner deps.
  - DeepSeek's stream format is simple enough that 100 lines of
    SSE parsing is straightforward.
  - The R1 reasoner extension in Fase 3 needs `reasoning_content`
    parsing which the openai SDK may or may not surface; doing our
    own parser future-proofs that.

Pricing (Fase 2 spec §2.4):
  - deepseek-chat (V3): $0.27 / $1.10 per 1M tok (in/out)
  - deepseek-reasoner (R1): $0.55 / $2.19 per 1M tok (in/out) — added in Fase 3
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator

from api.agent.providers.base import (
    LLMEvent,
    LLMStreamEnd,
    LLMTextDelta,
    LLMToolUseStart,
    SyntheticTextBlock,
    SyntheticToolUseBlock,
)

log = logging.getLogger("api.agent.providers.deepseek")


# ── Pricing ─────────────────────────────────────────────────────────


# USD per 1M tokens. Cached snapshot 2026-04-29 from DeepSeek's
# published pricing page.
#
# DeepSeek auto-caches stable prefixes but does NOT report cache stats
# in `usage`. The PR #411 review pickup 2 (acknowledged in spec §6):
# our cost estimate treats all input as fresh. The audit ends up
# over-estimating spend vs the DeepSeek Console billing. Conservative
# (safe — breaker trips before real spend reaches the cap); operator
# compares at bake close to calibrate.
#
# Fase 3 adds deepseek-reasoner (R1).
MODEL_PRICING: dict[str, dict[str, float]] = {
    "deepseek-chat":     {"in": 0.27,  "out":  1.10},
    # "deepseek-reasoner": {"in": 0.55,  "out":  2.19},  # Fase 3
}


# ── HTTP client construction ──────────────────────────────────────


DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"


def build_deepseek_client_kwargs() -> dict:
    """Construct kwargs for DeepSeekProvider.__init__ in production.

    Returns a dict with `api_key` from DEEPSEEK_API_KEY env var. The
    registry's factory unpacks this. Tests inject FakeDeepSeekProvider
    directly via dependency_overrides and never hit this path.
    """
    return {
        "api_key": (os.environ.get("DEEPSEEK_API_KEY") or "").strip(),
    }


# ── The adapter ─────────────────────────────────────────────────────


class DeepSeekProvider:
    """LLMProvider implementation over DeepSeek's Chat Completions API.

    Constructed by the registry's factory with an api_key (which may be
    empty — has_api_key() reflects that). For format_*/estimate_cost
    operations, no key is needed (the adapter just serializes shapes).
    """

    name = "deepseek"

    def __init__(self, *, api_key: str = "") -> None:
        self._api_key = api_key

    # ── Discovery ───────────────────────────────────────────────────

    def supports_model(self, model: str) -> bool:
        return model.startswith("deepseek-")

    def has_api_key(self) -> bool:
        """Env-driven (mirrors AnthropicProvider's pattern): the instance
        may have been constructed without a key for cheap operations
        like format_tools/estimate_cost. The status check just wants to
        know "is the vendor's env var configured at all?"."""
        return bool((os.environ.get("DEEPSEEK_API_KEY") or "").strip())

    # ── Wire-format translators ─────────────────────────────────────

    def format_system_blocks(self, blocks: list[str]) -> list[dict]:
        """DeepSeek (OpenAI-shape) accepts EITHER one system message
        with concatenated text OR multiple system messages. We use a
        SINGLE concatenated block — separators between the four logical
        sections are preserved with double-newline. Single block has
        two benefits:
          1. Auto-cache hits more reliably (DS caches the longest
             stable prefix; a single block IS the prefix).
          2. We don't have to teach the loop that DS messages have
             multiple system slots — at the wire level there's just
             one system message.

        The provider-neutral `cache_control` discipline (spec §2.2)
        means the operator doesn't see breakpoints — DS auto-caches
        whatever is stable.
        """
        if not blocks:
            return []
        concatenated = "\n\n".join(blocks)
        return [{"role": "system", "content": concatenated}]

    def format_tools(self, specs: tuple) -> list[dict]:
        """OpenAI / DeepSeek shape:
            {"type": "function",
             "function": {"name": "...", "description": "...", "parameters": {...JSON schema...}}}

        We strip `title` keys from the parameters schema the same way
        AnthropicProvider does — for cache prefix determinism.
        """
        out = []
        for spec in specs:
            schema = spec.schema.model_json_schema()
            _strip_titles(schema)
            out.append({
                "type": "function",
                "function": {
                    "name":        spec.name,
                    "description": spec.description,
                    "parameters":  schema,
                },
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
        """Open one streaming connection against DeepSeek's chat completions.

        Yields:
          - LLMTextDelta on each `delta.content` chunk
          - LLMToolUseStart on first `delta.tool_calls[i]` chunk
            (when name first becomes known)
          - LLMStreamEnd as terminal, with usage + synthesized content blocks

        DeepSeek streams tool_calls as a sequence of partial deltas:
          - First delta has `id` + `function.name` (sometimes empty args)
          - Subsequent deltas accumulate `function.arguments` as a string
            that ultimately parses to JSON
          - When `finish_reason: "tool_calls"` arrives, all tool_calls
            are complete

        We accumulate per-index, parse on completion, synthesize a
        SyntheticToolUseBlock with the parsed input dict.
        """
        import httpx

        if not self._api_key:
            raise RuntimeError(
                "DeepSeekProvider has no API key. The registry factory "
                "must read DEEPSEEK_API_KEY or tests must inject "
                "FakeDeepSeekProvider directly."
            )

        # The DS wire wants `messages` to include the system message(s)
        # as the first element(s). The loop's `messages` array is just
        # the conversation; we prepend the system blocks.
        wire_messages: list[dict] = list(system_blocks) + list(messages)

        body = {
            "model":      model,
            "messages":   wire_messages,
            "tools":      tools,
            "max_tokens": max_tokens,
            "stream":     True,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type":  "application/json",
        }

        # Accumulators for the in-flight stream.
        text_buf = ""
        # tool_calls keyed by index → {id, name, arguments (str)}
        tool_calls: dict[int, dict] = {}
        announced: set[int] = set()  # which indices we already yielded ToolUseStart for
        finish_reason: str | None = None
        prompt_tokens = 0
        completion_tokens = 0

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=120.0)) as client:
            async with client.stream(
                "POST", f"{DEEPSEEK_BASE_URL}/chat/completions",
                json=body, headers=headers,
            ) as resp:
                if resp.status_code != 200:
                    err_body = await resp.aread()
                    raise RuntimeError(
                        f"DeepSeek API returned {resp.status_code}: "
                        f"{err_body[:200]!r}"
                    )
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    payload_str = line[len("data: "):]
                    if payload_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload_str)
                    except json.JSONDecodeError:
                        log.warning("DeepSeek: malformed SSE chunk: %s", payload_str[:100])
                        continue

                    # Usage chunks (DS sends these on the last few chunks).
                    usage = chunk.get("usage")
                    if usage:
                        prompt_tokens = int(usage.get("prompt_tokens") or 0)
                        completion_tokens = int(usage.get("completion_tokens") or 0)

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice0 = choices[0]
                    fr = choice0.get("finish_reason")
                    if fr:
                        finish_reason = fr

                    delta = choice0.get("delta") or {}
                    if "content" in delta and delta["content"] is not None:
                        text_buf += delta["content"]
                        yield LLMTextDelta(text=delta["content"])

                    for tc in (delta.get("tool_calls") or []):
                        idx = int(tc.get("index", 0))
                        bucket = tool_calls.setdefault(idx, {
                            "id": "", "name": "", "arguments": "",
                        })
                        if tc.get("id"):
                            bucket["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            bucket["name"] = fn["name"]
                        if fn.get("arguments"):
                            bucket["arguments"] += fn["arguments"]
                        # Yield ToolUseStart the moment we have both id+name.
                        if (idx not in announced
                                and bucket["id"] and bucket["name"]):
                            announced.add(idx)
                            yield LLMToolUseStart(
                                id=bucket["id"], name=bucket["name"],
                            )

        # Synthesize content blocks for the loop's reading conventions.
        content: list = []
        if text_buf:
            content.append(SyntheticTextBlock(text=text_buf))
        for idx in sorted(tool_calls):
            tc = tool_calls[idx]
            try:
                parsed = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                log.warning(
                    "DeepSeek: tool_call %s arguments not parseable JSON: %s",
                    tc.get("name"), tc.get("arguments", "")[:100],
                )
                parsed = {}
            content.append(SyntheticToolUseBlock(
                id=tc["id"], name=tc["name"], input=parsed,
            ))

        # Normalize finish_reason to the loop's vocabulary. The loop
        # checks `stop_reason != "tool_use"` to decide whether to
        # dispatch and loop. DS uses "tool_calls"; map to "tool_use".
        stop_reason = "tool_use" if finish_reason == "tool_calls" else (
            finish_reason or "end_turn"
        )
        usage_dict = {
            "input_tokens":                prompt_tokens,
            "output_tokens":               completion_tokens,
            # DeepSeek's auto-cache discount is NOT reported in usage.
            # We can't tell from the response how much was cached vs
            # fresh. Cost estimate treats all input as fresh — known
            # over-estimate, documented in spec §6.
            "cache_read_input_tokens":     0,
            "cache_creation_input_tokens": 0,
        }
        yield LLMStreamEnd(
            stop_reason=stop_reason,
            usage=usage_dict,
            content=content,
        )

    # ── Re-send shape (DeepSeek wire) ────────────────────────────────

    def to_assistant_message(self, stream_end) -> dict:
        """OpenAI / DeepSeek shape for the assistant message: a single
        `content` string + an optional `tool_calls` array.

        text blocks → concatenated content string
        tool_use blocks → tool_calls entries with arguments as JSON STRING
        """
        content_text = ""
        tool_calls: list[dict] = []
        for b in stream_end.content:
            if b.type == "text":
                content_text += (b.text or "")
            elif b.type == "tool_use":
                tool_calls.append({
                    "id":   b.id,
                    "type": "function",
                    "function": {
                        "name":      b.name,
                        # DS expects the arguments as a STRING; we
                        # serialize the dict back to JSON for the wire.
                        "arguments": json.dumps(b.input or {}, default=str),
                    },
                })
        msg: dict = {"role": "assistant", "content": content_text}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return msg

    def to_tool_result_messages(
        self, tool_uses_with_results: list[tuple],
    ) -> list[dict]:
        """DeepSeek: ONE tool message per tool_call. The tool_call_id
        field correlates the result back to the assistant's tool_calls
        entry."""
        out: list[dict] = []
        for tu, content, is_error in tool_uses_with_results:
            out.append({
                "role":         "tool",
                "tool_call_id": tu.id,
                "content":      content,
            })
        return out

    # ── Cost ────────────────────────────────────────────────────────

    def estimate_cost(self, model: str, usage: dict) -> float:
        """Per-hop USD cost. DS doesn't report cache breakdown, so
        ALL input tokens are charged as fresh (over-estimate vs real
        billing; documented spec §6 risk + bake-close reconciliation
        in the runbook of Fase 5)."""
        p = MODEL_PRICING.get(model)
        if not p:
            return 0.0
        in_per_m = p["in"]
        out_per_m = p["out"]
        in_tokens = (usage.get("input_tokens") or 0)
        out_tokens = (usage.get("output_tokens") or 0)
        cost = (
            in_tokens * in_per_m + out_tokens * out_per_m
        ) / 1_000_000.0
        return round(cost, 6)


# ── Helpers ─────────────────────────────────────────────────────────


def _strip_titles(node: Any) -> None:
    """Recursively remove `title` keys from a JSON-schema dict tree.
    Same cache-prefix determinism rationale as AnthropicProvider."""
    if isinstance(node, dict):
        node.pop("title", None)
        for v in node.values():
            _strip_titles(v)
    elif isinstance(node, list):
        for v in node:
            _strip_titles(v)
