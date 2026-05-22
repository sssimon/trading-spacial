"""Per-tenant conversation history read endpoints (#428 H.3).

Reads from the H.1 tables (agent_messages + agent_conversation_meta) for
the sidebar + transcript rehydration on the frontend. Soft delete + pin
toggle round out the CRUD surface. All endpoints are JWT-gated, derive
tenant_id from `get_current_tenant_id` (never from request body / query /
header / path), and gate reads through the retention TTL.

Pre-reg: docs/superpowers/specs/es/2026-05-22-conversation-history-pre-reg.md
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from api.agent.audit import RETENTION_DAYS
from auth.dependencies import get_current_tenant_id
from db.connection import get_db

log = logging.getLogger("api.agent.history")

router = APIRouter(tags=["agent-history"])


# ── Response models ────────────────────────────────────────────────────


class ConversationSummary(BaseModel):
    """One row in the sidebar list."""
    conversation_id: str
    title:           Optional[str]
    surface:         str
    last_ts:         str
    first_ts:        str
    message_count:   int
    pinned:          bool


class ConversationListResponse(BaseModel):
    conversations: list[ConversationSummary]
    total:         int
    limit:         int
    offset:        int


class ToolChipRecord(BaseModel):
    tool:   str
    status: Literal["pending", "ok", "error"]


class ProposalRecord(BaseModel):
    """Rehydrated proposal shape. `signed_payload` is intentionally
    absent — the HMAC TTL is minutes and the token is never persisted.
    `state` is derived at read time from agent_side_effects (where the
    confirm lives) or from expires_at (if never confirmed)."""
    proposal_id: str
    action:      str
    args:        dict[str, Any]
    expires_at:  str
    summary:     str
    state:       Literal["pending", "ok", "expired", "error", "conflict"]


class MessageRecord(BaseModel):
    role:       Literal["user", "assistant"]
    ts:         str
    content:    str
    reasoning:  Optional[str] = None
    tool_chips: list[ToolChipRecord] = Field(default_factory=list)
    proposals:  list[ProposalRecord] = Field(default_factory=list)


class ConversationDetailResponse(BaseModel):
    conversation_id: str
    title:           Optional[str]
    surface:         str
    pinned:          bool
    messages:        list[MessageRecord]


# ── Helpers ────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _escape_like(s: str) -> str:
    """Escape LIKE wildcards (% and _) so user input is treated as
    literal text. Pairs with `ESCAPE '\\'` in the SQL clause."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _derive_proposal_state(con: sqlite3.Connection, proposal_id: str,
                            original_expires_at: str) -> str:
    """Reconstruct the proposal's terminal state at read time.

    Three cases:
      1. A row exists in `agent_side_effects` keyed by `idempotency_key`
         = proposal_id → the user confirmed it at some point. Return
         the recorded `result` (ok / expired / conflict / error).
      2. No side-effect row, original `expires_at` is in the past →
         the proposal expired naturally without confirmation. Return
         'expired'.
      3. No side-effect row, expires_at still in the future → still
         actionable in principle, but the rehydrated UI WON'T have a
         valid `signed_payload` (we never persist it). Return 'pending'
         and let the frontend render it as a non-actionable chip.
    """
    row = con.execute(
        "SELECT result FROM agent_side_effects WHERE idempotency_key = ?",
        (proposal_id,),
    ).fetchone()
    if row is not None:
        result = row[0] or "error"
        if result in ("ok", "expired", "conflict", "error"):
            return result
        return "error"
    try:
        if datetime.fromisoformat(original_expires_at) < datetime.now(timezone.utc):
            return "expired"
    except (TypeError, ValueError):
        pass
    return "pending"


# ── GET /agent/conversations ───────────────────────────────────────────


@router.get(
    "/agent/conversations",
    response_model=ConversationListResponse,
    summary="List recent conversations for the current tenant",
)
def list_conversations(
    tenant_id: int = Depends(get_current_tenant_id),
    limit:  int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    surface: Optional[Literal["dock", "symbol_detail", "brief", "kill_switch", "autotune", "historial"]] = Query(default=None),
    q:       Optional[str] = Query(default=None, max_length=200),
):
    """Sidebar list. Pinned first, then last_ts DESC. Hides expired
    conversations via the retention filter.

    Query params:
      - limit / offset: standard pagination, default 20, cap 100.
      - surface: optional filter ('dock' / 'symbol_detail' / 'brief').
      - q: substring search across title + message content; LIKE-escaped
        so % and _ are literals. Empty / whitespace-only q is ignored.

    Pre-reg D.7: tenant_id from JWT, never from query/body.
    """
    now = _now_iso()
    con = get_db()
    try:
        # Parameters live in two buckets so the order in execute() lines
        # up with the placeholder order in the final SQL: JOIN clause
        # comes BEFORE WHERE, so any `?` in the JOIN must be earlier in
        # the param tuple than the WHERE params. Easy to get wrong if
        # you append-as-you-go to one list.
        join_params: list[Any] = []
        where_params: list[Any] = [tenant_id, now]
        where_parts = [
            "m.tenant_id = ?",
            "m.expires_at > ?",
        ]

        if surface:
            where_parts.append("m.surface = ?")
            where_params.append(surface)

        join_clause = ""
        if q and q.strip():
            pattern = f"%{_escape_like(q.strip())}%"
            join_clause = (
                " LEFT JOIN agent_messages msg "
                "ON msg.conversation_id = m.conversation_id "
                "AND msg.tenant_id = m.tenant_id "
                "AND msg.expires_at > ? "
            )
            join_params.append(now)
            where_parts.append(
                "(m.title LIKE ? ESCAPE '\\' OR msg.content LIKE ? ESCAPE '\\')"
            )
            where_params.append(pattern)
            where_params.append(pattern)

        where_sql = " AND ".join(where_parts)
        all_filter_params = (*join_params, *where_params)

        rows = con.execute(
            f"""SELECT DISTINCT m.conversation_id, m.title, m.surface,
                       m.first_ts, m.last_ts, m.message_count, m.pinned
                FROM agent_conversation_meta m
                {join_clause}
                WHERE {where_sql}
                ORDER BY m.pinned DESC, m.last_ts DESC
                LIMIT ? OFFSET ?""",
            (*all_filter_params, limit, offset),
        ).fetchall()

        total = con.execute(
            f"""SELECT COUNT(DISTINCT m.conversation_id)
                FROM agent_conversation_meta m
                {join_clause}
                WHERE {where_sql}""",
            all_filter_params,
        ).fetchone()[0]

        conversations = [
            ConversationSummary(
                conversation_id=r["conversation_id"],
                title=r["title"],
                surface=r["surface"],
                first_ts=r["first_ts"],
                last_ts=r["last_ts"],
                message_count=r["message_count"],
                pinned=bool(r["pinned"]),
            )
            for r in rows
        ]
        return ConversationListResponse(
            conversations=conversations,
            total=int(total),
            limit=limit,
            offset=offset,
        )
    finally:
        con.close()


# ── GET /agent/conversations/{id}/messages ─────────────────────────────


_CONVERSATION_ID_PATTERN = r"^[A-Za-z0-9_\-]+$"


@router.get(
    "/agent/conversations/{conversation_id}/messages",
    response_model=ConversationDetailResponse,
    summary="Fetch the full transcript for one conversation",
)
def get_messages(
    conversation_id: str = Path(
        ..., min_length=1, max_length=128, pattern=_CONVERSATION_ID_PATTERN,
    ),
    tenant_id: int = Depends(get_current_tenant_id),
):
    """Hydrate a conversation's full transcript. 404 if the conversation
    doesn't exist OR belongs to a different tenant — same response shape
    either way so existence isn't leaked across tenants.

    `proposals[].state` is reconstructed from agent_side_effects; the
    signed_payload is never persisted and therefore never returned.
    """
    now = _now_iso()
    con = get_db()
    try:
        meta = con.execute(
            "SELECT conversation_id, tenant_id, title, surface, pinned, expires_at "
            "FROM agent_conversation_meta WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()

        if meta is None or meta["tenant_id"] != tenant_id or meta["expires_at"] <= now:
            # Same 404 for "doesn't exist" and "exists but not yours / expired".
            # No information leak across tenants.
            raise HTTPException(status_code=404, detail="conversation_not_found")

        rows = con.execute(
            "SELECT role, ts, content, reasoning, tool_chips_json, proposals_json "
            "FROM agent_messages "
            "WHERE conversation_id = ? AND tenant_id = ? AND expires_at > ? "
            "ORDER BY ts ASC, id ASC",
            (conversation_id, tenant_id, now),
        ).fetchall()

        messages: list[MessageRecord] = []
        for r in rows:
            chips_raw = json.loads(r["tool_chips_json"]) if r["tool_chips_json"] else []
            chips = [ToolChipRecord(**c) for c in chips_raw]

            proposals_raw = json.loads(r["proposals_json"]) if r["proposals_json"] else []
            proposals: list[ProposalRecord] = []
            for p in proposals_raw:
                state = _derive_proposal_state(con, p["proposal_id"], p["expires_at"])
                proposals.append(ProposalRecord(
                    proposal_id=p["proposal_id"],
                    action=p["action"],
                    args=p["args"],
                    expires_at=p["expires_at"],
                    summary=p["summary"],
                    state=state,
                ))

            messages.append(MessageRecord(
                role=r["role"],
                ts=r["ts"],
                content=r["content"],
                reasoning=r["reasoning"],
                tool_chips=chips,
                proposals=proposals,
            ))

        return ConversationDetailResponse(
            conversation_id=meta["conversation_id"],
            title=meta["title"],
            surface=meta["surface"],
            pinned=bool(meta["pinned"]),
            messages=messages,
        )
    finally:
        con.close()


# ── DELETE /agent/conversations/{id} — soft delete ─────────────────────


class DeleteResponse(BaseModel):
    ok: bool


@router.delete(
    "/agent/conversations/{conversation_id}",
    response_model=DeleteResponse,
    summary="Soft-delete a conversation (sets expires_at=now)",
)
def delete_conversation(
    conversation_id: str = Path(
        ..., min_length=1, max_length=128, pattern=_CONVERSATION_ID_PATTERN,
    ),
    tenant_id: int = Depends(get_current_tenant_id),
):
    """Soft delete via `expires_at = NOW`. Cleanup-on-read filters hide
    the rows from subsequent GETs. Pre-reg D.5.

    IDOR: the UPDATE WHERE clause carries `tenant_id = ?`, so attacker's
    DELETE on another tenant's conversation_id affects zero rows and
    returns 404 — no observable difference from "doesn't exist".
    """
    now = _now_iso()
    con = get_db()
    try:
        cursor = con.execute(
            "UPDATE agent_conversation_meta SET expires_at = ? "
            "WHERE conversation_id = ? AND tenant_id = ? AND expires_at > ?",
            (now, conversation_id, tenant_id, now),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="conversation_not_found")

        # Also soft-delete the message rows so the GET endpoints don't
        # return them under any future surface (e.g. cross-conversation
        # search). Two-step UPDATE; both run inside the same transaction.
        con.execute(
            "UPDATE agent_messages SET expires_at = ? "
            "WHERE conversation_id = ? AND tenant_id = ?",
            (now, conversation_id, tenant_id),
        )
        con.commit()
        return DeleteResponse(ok=True)
    finally:
        con.close()


# ── POST /agent/conversations/{id}/pin — toggle ────────────────────────


class PinResponse(BaseModel):
    ok:     bool
    pinned: bool


@router.post(
    "/agent/conversations/{conversation_id}/pin",
    response_model=PinResponse,
    summary="Toggle the pinned flag on a conversation",
)
def toggle_pin(
    conversation_id: str = Path(
        ..., min_length=1, max_length=128, pattern=_CONVERSATION_ID_PATTERN,
    ),
    tenant_id: int = Depends(get_current_tenant_id),
):
    """Toggle pinned (0 → 1, 1 → 0). IDOR-safe: UPDATE WHERE tenant_id;
    rowcount = 0 → 404 with the same body as 'doesn't exist'."""
    now = _now_iso()
    con = get_db()
    try:
        cursor = con.execute(
            "UPDATE agent_conversation_meta SET pinned = 1 - pinned "
            "WHERE conversation_id = ? AND tenant_id = ? AND expires_at > ?",
            (conversation_id, tenant_id, now),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="conversation_not_found")
        con.commit()
        new_state = con.execute(
            "SELECT pinned FROM agent_conversation_meta WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        return PinResponse(ok=True, pinned=bool(new_state["pinned"]))
    finally:
        con.close()
