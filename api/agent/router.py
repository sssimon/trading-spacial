"""Agent API router.

  Phase 0:
    GET  /agent/status                              — public, no auth

  Phase 2 (this commit):
    POST /agent/conversations/{conversation_id}/turn   — SSE streaming,
                                                         JWT-authenticated

  Phase 3:
    POST /agent/proposals/{proposal_id}/confirm     — JWT-authenticated

  Phase 5:
    GET  /agent/metrics                             — admin role required

GET /agent/status is intentionally unauthenticated: the frontend reads it
on initial load (before login completes in some flows) to decide whether
to render the copilot UI at all. Per pre-reg §3.3 / §4.4 / §13.5, the
response body NEVER leaks env-var names, .env paths, or operator-only
configuration detail.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.agent.audit import TurnAuditWrapper
from api.agent.clients import get_anthropic_client
from api.agent.config import get_agent_status
from api.agent.loop import run_turn
from api.agent.streaming import sse_serialize
from auth.dependencies import get_current_tenant_id

log = logging.getLogger("api.agent.router")

router = APIRouter(tags=["agent"])


# ── /agent/status (Phase 0) ────────────────────────────────────────────


@router.get("/agent/status", summary="Public agent feature status")
def get_status():
    """Return whether the copilot is currently available.

    The shape is `{"enabled": bool, "reason": "ok" | "agent_disabled"}`.
    The reason field is a closed enum — see api/agent/config.py for the
    exhaustive list.
    """
    status = get_agent_status()
    return {"enabled": status.enabled, "reason": status.reason}


# ── /agent/conversations/{id}/turn (Phase 2) ───────────────────────────


_SURFACE_MODEL_DEFAULTS: dict[str, str] = {
    "dock":          "claude-sonnet-4-6",
    "symbol_detail": "claude-haiku-4-5",
    "kill_switch":   "claude-sonnet-4-6",
    "autotune":      "claude-sonnet-4-6",
    "historial":     "claude-haiku-4-5",
}


class _AgentMessage(BaseModel):
    role:    Literal["user", "assistant"]
    content: str


class _AgentContextHints(BaseModel):
    symbol:      Optional[str] = None
    position_id: Optional[int] = None
    tune_id:     Optional[int] = None


class _AgentTurnRequest(BaseModel):
    surface:        Literal["dock", "symbol_detail", "kill_switch", "autotune", "historial"]
    messages:       list[_AgentMessage] = Field(..., min_length=1)
    context_hints:  Optional[_AgentContextHints] = None
    # Optional model override (e.g. when the user clicks "análisis profundo"
    # which flips the next turn to Opus). Server-side allowlist enforced.
    model:          Optional[str] = None


_ALLOWED_MODELS: frozenset[str] = frozenset({
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "claude-opus-4-7",
})


@router.post(
    "/agent/conversations/{conversation_id}/turn",
    summary="Stream one agent turn (SSE)",
)
async def post_agent_turn(
    # Validated path param (PR #404 review issue 3): UUID / nanoid /
    # other ascii-safe ids only, max 128 chars. Blocks pollution
    # (whitespace, control chars, /, etc.) and minor DoS via huge ids
    # being persisted to agent_conversations.
    conversation_id: str = Path(
        ..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_\-]+$",
    ),
    body: _AgentTurnRequest = ...,  # noqa: B008
    tenant_id: int = Depends(get_current_tenant_id),
    client = Depends(get_anthropic_client),  # noqa: B008
):
    """Stream a single user-turn through the model + tool loop.

    Wire shape: `text/event-stream`. Frames are JSON objects with a
    `type` field; see api/agent/streaming.py for the closed enum
    (text_delta / tool_use_start / tool_use_result / message_end / error).

    Pre-reg §4.4. Auth: cookie JWT → tenant_id. The model never sees
    tenant_id; every tool call is bound server-side. The Anthropic
    client is injected via Depends so tests override with a fake.
    """
    status = get_agent_status()
    if not status.enabled:
        raise HTTPException(status_code=503, detail=status.reason)

    model = body.model or _SURFACE_MODEL_DEFAULTS[body.surface]
    if model not in _ALLOWED_MODELS:
        raise HTTPException(status_code=400, detail="model_not_allowed")

    # Convert the request's messages into the API's `messages` array.
    # The frontend owns the transcript and resends it every turn (same
    # pattern as the legacy /agent/chat that this endpoint will replace).
    messages: list[dict] = [
        {"role": m.role, "content": m.content} for m in body.messages
    ]

    loop_events = run_turn(
        client=client,
        model=model,
        surface=body.surface,
        messages=messages,
        tenant_id=tenant_id,
    )
    audited = TurnAuditWrapper(
        loop_events,
        tenant_id=tenant_id,
        surface=body.surface,
        conversation_id=conversation_id,
        model=model,
    )
    sse_bytes = sse_serialize(audited)

    # X-Accel-Buffering: no → nginx must not buffer the response. Without
    # this header, every SSE frame piles up until nginx's 8KB buffer fills,
    # killing the streaming UX. See pre-reg §6 (failure modes table).
    return StreamingResponse(
        sse_bytes,
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":        "keep-alive",
        },
    )
