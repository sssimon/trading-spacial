"""Per-turn audit writes to agent_conversations.

Phase 2 of epic #400. Every completed model turn (MessageEnd) AND every
errored turn (ErrorEvent) emits one row in `agent_conversations`. The
write is best-effort and fail-tolerant: a DB hiccup must NOT kill the
streaming response that's already on the wire to the user.

Pre-reg §9. Shape of the row mirrors the schema in db/schema.py.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from db.connection import get_db

log = logging.getLogger("api.agent.audit")


def record_turn(
    *,
    tenant_id: int,
    surface: str,
    conversation_id: str,
    role: str,                              # "assistant" | "error"
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    latency_ms: Optional[int] = None,
    cost_usd: Optional[float] = None,
    content_summary: Optional[str] = None,  # redacted; never the raw text
    refused: bool = False,
) -> None:
    """Persist one row to agent_conversations.

    Failures here are logged but never raised — an audit miss is annoying;
    a 500 to the user is worse. The streaming response is already on
    the wire when this is called.
    """
    try:
        con = get_db()
        try:
            con.execute(
                """INSERT INTO agent_conversations
                   (tenant_id, surface, conversation_id, ts, role, model,
                    input_tokens, output_tokens,
                    cache_read_input_tokens, cache_creation_input_tokens,
                    latency_ms, cost_usd, content_json, refused)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tenant_id, surface, conversation_id,
                    datetime.now(timezone.utc).isoformat(),
                    role, model,
                    int(input_tokens or 0), int(output_tokens or 0),
                    int(cache_read_input_tokens or 0),
                    int(cache_creation_input_tokens or 0),
                    latency_ms,
                    cost_usd,
                    json.dumps(content_summary) if content_summary else None,
                    1 if refused else 0,
                ),
            )
            con.commit()
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        log.warning(
            "record_turn failed for tenant=%s conv=%s — audit row dropped",
            tenant_id, conversation_id, exc_info=True,
        )


class TurnAuditWrapper:
    """Wrap a loop-events async iterator to persist a row on
    MessageEnd / ErrorEvent. Yields events unchanged.

    Usage in the endpoint:

        wrapped = TurnAuditWrapper(
            run_turn(client=..., ...),
            tenant_id=tenant_id, surface=surface,
            conversation_id=conversation_id, model=model,
        )
        return StreamingResponse(sse_serialize(wrapped), ...)
    """

    def __init__(
        self,
        events,
        *,
        tenant_id: int,
        surface: str,
        conversation_id: str,
        model: str,
    ):
        self._events = events
        self._tenant_id = tenant_id
        self._surface = surface
        self._conversation_id = conversation_id
        self._model = model
        self._t_start = time.monotonic()

    def __aiter__(self):
        return self

    async def __anext__(self):
        # Import here to avoid circular import: loop.py imports prompts,
        # prompts depend on registry; this module is imported from
        # router.py which sees them all.
        from api.agent.loop import ErrorEvent, MessageEnd

        try:
            ev = await self._events.__anext__()
        except StopAsyncIteration:
            raise

        if isinstance(ev, MessageEnd):
            latency_ms = int((time.monotonic() - self._t_start) * 1000)
            record_turn(
                tenant_id=self._tenant_id,
                surface=self._surface,
                conversation_id=self._conversation_id,
                role="assistant",
                model=self._model,
                input_tokens=ev.usage.get("input_tokens", 0),
                output_tokens=ev.usage.get("output_tokens", 0),
                cache_read_input_tokens=ev.usage.get("cache_read_input_tokens", 0),
                cache_creation_input_tokens=ev.usage.get("cache_creation_input_tokens", 0),
                latency_ms=latency_ms,
                cost_usd=ev.cost_usd,
                refused=False,
            )
        elif isinstance(ev, ErrorEvent):
            latency_ms = int((time.monotonic() - self._t_start) * 1000)
            record_turn(
                tenant_id=self._tenant_id,
                surface=self._surface,
                conversation_id=self._conversation_id,
                role="error",
                model=self._model,
                latency_ms=latency_ms,
                # We don't store the user_message verbatim (it's a fixed
                # friendly string, not signal worth indexing); the reason
                # enum is the meaningful field.
                content_summary=ev.reason,
                refused=(ev.reason == "too_many_tool_hops"),
            )
        return ev
