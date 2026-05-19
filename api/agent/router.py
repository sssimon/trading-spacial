"""Agent API router. Phase 0 owns GET /agent/status only.

The /agent/* surface lands incrementally per the pre-reg's 6-phase plan:

  Phase 0 (this commit):
    GET  /agent/status                              — public, no auth

  Phase 1:
    (no new endpoints — tool registry + audit schema land internally)

  Phase 2:
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

from fastapi import APIRouter

from api.agent.config import get_agent_status

log = logging.getLogger("api.agent.router")

router = APIRouter(tags=["agent"])


@router.get("/agent/status", summary="Public agent feature status")
def get_status():
    """Return whether the copilot is currently available.

    The shape is `{"enabled": bool, "reason": "ok" | "agent_disabled"}`.
    The reason field is a closed enum — see api/agent/config.py for the
    exhaustive list.
    """
    status = get_agent_status()
    return {"enabled": status.enabled, "reason": status.reason}
