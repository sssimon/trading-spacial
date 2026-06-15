"""SSE serialization of loop events — Phase 2 of epic #400.

Translates the typed events yielded by `api.agent.loop.run_turn` into
Server-Sent Events on the wire. Each frame is a `data: {json}\n\n` line
with a `type` field that the frontend's `useAgentStream` hook switches on.

Pre-reg §6.2. The event-type enum is closed:

    text_delta | tool_use_start | tool_use_result | message_end | error
    proposal | keepalive | reasoning_delta

Phase 5 adds the `keepalive` heartbeat — see sse_serialize() docstring.
Fase 3 of the multi-provider epic adds `reasoning_delta` — streamed
chain-of-thought chunks from DeepSeek-R1 that the frontend renders in
a collapsible panel.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from typing import AsyncIterator

from api.agent.loop import (
    ErrorEvent,
    LoopEvent,
    MessageEnd,
    ProposalEvent,
    ReasoningDelta,
    Refusal,
    TextDelta,
    ToolUseResult,
    ToolUseStart,
)

log = logging.getLogger("api.agent.streaming")


# Default heartbeat cadence. Tunable via the optional `keepalive_seconds`
# kwarg on sse_serialize so tests don't have to actually sleep 30 sec.
# Chosen as a sweet-spot vs the nginx / cloudflare idle defaults:
#   - nginx default `proxy_read_timeout` is 60s
#   - cloudflare's free-tier idle is 100s
# 30s is safely under both; if a tool call spans a few model
# heartbeat-less seconds, the keepalive frame keeps the connection
# alive without flooding the wire.
DEFAULT_KEEPALIVE_SECONDS = 30.0


def _sse_frame(event_type: str, payload: dict) -> bytes:
    """Format one SSE frame. UTF-8 bytes ready to yield from FastAPI's
    StreamingResponse. We use the `data:`-only form (no `event:` line)
    because the frontend switches on the JSON's `type` field — simpler
    than juggling SSE event types and giving the client a single parse
    surface."""
    payload_with_type = {"type": event_type, **payload}
    return f"data: {json.dumps(payload_with_type)}\n\n".encode("utf-8")


async def sse_serialize(
    events: AsyncIterator[LoopEvent],
    *,
    keepalive_seconds: float = DEFAULT_KEEPALIVE_SECONDS,
) -> AsyncIterator[bytes]:
    """Consume loop events, yield SSE-framed bytes.

    Phase 5 of #400: emit a `keepalive` frame whenever the upstream
    iterator stays silent for `keepalive_seconds`. The frame is a
    sentinel — the frontend hook ignores it but the underlying TCP
    write resets the idle timer on every intermediate proxy (nginx,
    cloudflare). Without it, a tool call that takes 45-60s makes the
    stream look dead to the proxy and the user gets a 504.

    Implementation note: naive `wait_for(events.__anext__())` would
    CANCEL the in-flight read on every timeout, leaving the async
    generator in an inconsistent state (the next __anext__ might raise
    or skip the event the generator was about to yield). We instead
    schedule a single Task for the read and wait on it WITHOUT
    cancelling — if the wait times out, the task survives, we yield a
    keepalive, and the next iteration awaits the SAME task. The task
    is only ever consumed once, when it actually completes.
    """
    pending: asyncio.Task | None = None
    while True:
        if pending is None:
            pending = asyncio.create_task(events.__anext__())
        # asyncio.wait does NOT cancel the task on timeout — it just
        # returns (done, pending) sets. We poll until the task lands
        # in `done`; each timeout that doesn't complete the task is a
        # keepalive opportunity.
        done, _ = await asyncio.wait({pending}, timeout=keepalive_seconds)
        if not done:
            # Heartbeat. NOT a terminal frame; the loop continues with
            # the same pending task still in flight.
            yield _sse_frame("keepalive", {})
            continue
        # The task completed. Pull its result and clear the slot.
        completed = pending
        pending = None
        try:
            ev = completed.result()
        except StopAsyncIteration:
            return
        # The `async for` model would have skipped a frame on a
        # StopAsyncIteration; we already handled it above. Below we
        # dispatch on the same closed event type set.
        if isinstance(ev, TextDelta):
            yield _sse_frame("text_delta", {"text": ev.text})
        elif isinstance(ev, ReasoningDelta):
            # Fase 3 of the multi-provider epic: streaming chain-of-
            # thought chunks from DeepSeek-R1. Frontend renders these
            # in a collapsible panel separate from the assistant's
            # final text bubble. SSE frame keeps the same `text` shape
            # as text_delta so the hook's accumulation pattern is
            # symmetric.
            yield _sse_frame("reasoning_delta", {"text": ev.text})
        elif isinstance(ev, ToolUseStart):
            yield _sse_frame("tool_use_start", {"tool": ev.tool})
        elif isinstance(ev, ToolUseResult):
            yield _sse_frame("tool_use_result", {
                "tool":   ev.tool,
                "status": ev.status,
            })
        elif isinstance(ev, ProposalEvent):
            # Phase 3: the frontend renders an amber confirm button.
            # signed_payload is opaque on the wire — frontend echoes it
            # back to POST /agent/proposals/{id}/confirm.
            yield _sse_frame("proposal", {
                "proposal_id":    ev.proposal_id,
                "signed_payload": ev.signed_payload,
                "action":         ev.action,
                "args":           ev.args,
                "expires_at":     ev.expires_at,
                "summary":        ev.summary,
            })
        elif isinstance(ev, MessageEnd):
            yield _sse_frame("message_end", {
                "usage":       ev.usage,
                "stop_reason": ev.stop_reason,
                "cost_usd":    ev.cost_usd,
            })
        elif isinstance(ev, ErrorEvent):
            yield _sse_frame("error", {
                "reason":       ev.reason,
                "user_message": ev.user_message,
            })
        elif isinstance(ev, Refusal):
            yield _sse_frame("refusal", {"user_message": ev.user_message})
        else:
            log.warning("sse_serialize: dropping unknown event type %s", type(ev))
