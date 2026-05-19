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
from api.agent.proposals import (
    ACTION_APPLY_TUNE,
    ACTION_CLOSE_POSITION,
    ACTION_REACTIVATE_SYMBOL,
    ProposalError,
    is_expired,
    load_proposal_row,
    mark_proposal_result,
    verify_proposal,
)
from api.agent.streaming import sse_serialize
from auth.dependencies import get_current_tenant_id, get_current_user
from auth.models import User

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
        conversation_id=conversation_id,
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


# ── /agent/proposals/{id}/confirm (Phase 3) ────────────────────────────


class _ConfirmProposalRequest(BaseModel):
    """Body of POST /agent/proposals/{id}/confirm.

    `signed_payload` is the opaque token the frontend received in the
    `proposal` SSE event. The server verifies the HMAC, checks the TTL
    + ownership + idempotency, then invokes the downstream handler.
    """
    signed_payload: str = Field(..., min_length=20, max_length=4096)


@router.post(
    "/agent/proposals/{proposal_id}/confirm",
    summary="Confirm a signed proposal emitted by a propose_* tool",
)
async def post_confirm_proposal(
    proposal_id: str = Path(
        ..., min_length=1, max_length=128, pattern=r"^prop_[A-Za-z0-9_\-]+$",
    ),
    body: _ConfirmProposalRequest = ...,  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
):
    """Verify HMAC + TTL + ownership + idempotency, then run the downstream
    action with the user's session. Pre-reg §10.

    Failure modes (all return a closed-enum `detail`, never leak):
      - signature_mismatch       — HMAC failed; possible tampering.
      - unsupported_version      — envelope v != 1.
      - invalid_payload          — token shape wrong.
      - not_found                — proposal_id absent from DB.
      - tenant_mismatch          — caller's tenant ≠ signed tenant.
      - expired                  — TTL passed.
      - already_consumed         — confirm already ran (idempotent return).
      - state_drift              — TOCTOU re-check failed (position closed,
                                   symbol no longer PAUSED, tune already
                                   applied, etc).
      - role_required            — non-admin tried to execute a global
                                   action (apply_tune / reactivate_symbol).
    """
    tenant_id = user.id
    # 1. Status gate (same as the turn endpoint).
    status = get_agent_status()
    if not status.enabled:
        raise HTTPException(status_code=503, detail=status.reason)

    # 2. Verify the HMAC + parse the payload.
    try:
        payload = verify_proposal(body.signed_payload)
    except ProposalError as e:
        raise HTTPException(status_code=400, detail=e.reason)

    if payload.get("proposal_id") != proposal_id:
        raise HTTPException(status_code=400, detail="proposal_id_mismatch")

    # 3. Tenant check — the JWT-resolved tenant_id MUST match the signed one.
    if int(payload.get("tenant_id", -1)) != tenant_id:
        # Closed-enum reason. NEVER reveal whose tenant signed it; this is
        # indistinguishable from a not_found for an audit attacker.
        raise HTTPException(status_code=404, detail="not_found")

    # 4. Load the persisted row + check idempotency.
    row = load_proposal_row(proposal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not_found")

    # If a previous confirm already ran, return that result (idempotent).
    # The UNIQUE constraint on idempotency_key at the DB layer is the
    # safety net; this app-level check returns the cached result instead
    # of trying to re-execute the action.
    if row.get("result") is not None:
        return {
            "ok": (row["result"] == "ok"),
            "result": row["result"],
            "http_status": row.get("http_status"),
            "idempotent": True,
        }

    # 5. TTL check (uses the DB column, not the signed payload — the
    # confirm endpoint trusts the DB row above the wire).
    if is_expired(row):
        mark_proposal_result(proposal_id=proposal_id, result="expired", http_status=410)
        raise HTTPException(status_code=410, detail="expired")

    # 6. TOCTOU re-check + downstream dispatch.
    action = row["action"]
    import json as _json
    args = _json.loads(row["args_json"] or "{}")
    try:
        result = _execute_proposed_action(
            action=action, args=args, tenant_id=tenant_id, user_role=user.role,
        )
    except HTTPException as he:
        # 403 (role) is a different terminal state from 409 (drift).
        # Persist with distinguishable result enum so audit can tell them
        # apart; the http_status column also carries the canonical code.
        if he.status_code == 403:
            result_enum = "role_required"
        elif he.status_code == 409:
            result_enum = "state_drift"
        else:
            result_enum = "error"
        mark_proposal_result(
            proposal_id=proposal_id,
            result=result_enum,
            http_status=he.status_code,
        )
        raise

    mark_proposal_result(proposal_id=proposal_id, result="ok", http_status=200)
    return {"ok": True, "result": "ok", "action_result": result, "idempotent": False}


# Actions whose downstream mutates GLOBAL state (config.json shared
# across all tenants, or symbol_health.state shared across all tenants).
# These require role=admin at the confirm boundary — propose still runs
# for any authenticated user (the model can reason about them), but the
# actual mutation is gated. Per pre-reg §10.2 + review of PR #406.
_ADMIN_ONLY_ACTIONS: frozenset[str] = frozenset({
    ACTION_REACTIVATE_SYMBOL,
    ACTION_APPLY_TUNE,
})


def _execute_proposed_action(
    *, action: str, args: dict, tenant_id: int, user_role: str,
) -> dict:
    """Dispatch the downstream action with tenant_id bound. Each action
    re-checks current state (TOCTOU) before mutating. Raises HTTPException
    on conflict so the caller can persist the right `result` enum.

    Role gate: global-scope actions (reactivate_symbol, apply_tune) require
    user_role == 'admin'. close_position is per-tenant and runs for any
    authenticated owner.
    """
    if action in _ADMIN_ONLY_ACTIONS and user_role != "admin":
        # Closed-enum detail. The model-facing summary doesn't reveal
        # which role; the operator can read http_status=403 + result=
        # 'role_required' in audit.
        raise HTTPException(status_code=403, detail="role_required")
    if action == ACTION_CLOSE_POSITION:
        from db.positions import db_close_position
        import btc_api
        position_id = int(args["position_id"])
        exit_price = float(args["exit_price"])
        # TOCTOU guards (both have to fire — db_close_position itself
        # only filters by id+tenant, NOT by status, and would happily
        # overwrite the exit_reason of a position already closed via
        # SL/TP/TIME_LIMIT between propose and confirm):
        #   1. row must still exist for this tenant (IDOR null pattern)
        #   2. row must still be `open` (not closed by another flow)
        con = btc_api.get_db()
        try:
            current = con.execute(
                "SELECT status FROM positions WHERE id=? AND tenant_id=?",
                (position_id, tenant_id),
            ).fetchone()
        finally:
            con.close()
        if current is None or dict(current).get("status") != "open":
            raise HTTPException(status_code=409, detail="state_drift")
        pos = db_close_position(
            position_id, exit_price, "MANUAL_AGENT",
            tenant_id=tenant_id,
        )
        if pos is None:
            raise HTTPException(status_code=409, detail="state_drift")
        return {"position": pos}

    if action == ACTION_REACTIVATE_SYMBOL:
        from health import get_symbol_state, reactivate_symbol
        from api.config import load_config
        symbol = str(args["symbol"]).upper()
        reason = str(args.get("reason", "manual_override_via_copilot"))
        current = get_symbol_state(symbol)
        if current != "PAUSED":
            raise HTTPException(status_code=409, detail="state_drift")
        reactivate_symbol(symbol, reason=reason, cfg=load_config())
        return {"symbol": symbol, "state": get_symbol_state(symbol)}

    if action == ACTION_APPLY_TUNE:
        from api.tune import tune_apply, tune_latest
        tune_id = int(args["tune_id"])
        latest = tune_latest()
        if not latest or int(latest.get("id", 0)) != tune_id or latest.get("applied"):
            raise HTTPException(status_code=409, detail="state_drift")
        applied = tune_apply()
        return {"tune_id": tune_id, "applied": applied}

    # Unknown action — closed enum, should never happen because the
    # propose handlers wrote it.
    raise HTTPException(status_code=400, detail="unknown_action")
