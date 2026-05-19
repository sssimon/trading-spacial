"""SSE serialization of loop events — Phase 2 of epic #400.

Translates the typed events yielded by `api.agent.loop.run_turn` into
Server-Sent Events on the wire. Each frame is a `data: {json}\n\n` line
with a `type` field that the frontend's `useAgentStream` hook switches on.

Pre-reg §6.2. The event-type enum is closed:

    text_delta | tool_use_start | tool_use_result | message_end | error
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import AsyncIterator

from api.agent.loop import (
    ErrorEvent,
    LoopEvent,
    MessageEnd,
    ProposalEvent,
    TextDelta,
    ToolUseResult,
    ToolUseStart,
)

log = logging.getLogger("api.agent.streaming")


def _sse_frame(event_type: str, payload: dict) -> bytes:
    """Format one SSE frame. UTF-8 bytes ready to yield from FastAPI's
    StreamingResponse. We use the `data:`-only form (no `event:` line)
    because the frontend switches on the JSON's `type` field — simpler
    than juggling SSE event types and giving the client a single parse
    surface."""
    payload_with_type = {"type": event_type, **payload}
    return f"data: {json.dumps(payload_with_type)}\n\n".encode("utf-8")


async def sse_serialize(events: AsyncIterator[LoopEvent]) -> AsyncIterator[bytes]:
    """Consume loop events, yield SSE-framed bytes."""
    async for ev in events:
        if isinstance(ev, TextDelta):
            yield _sse_frame("text_delta", {"text": ev.text})
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
        else:
            log.warning("sse_serialize: dropping unknown event type %s", type(ev))
