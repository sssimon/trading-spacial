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
    reasoning_tokens: Optional[int] = None,  # Fase 4: DS-R1 only
    latency_ms: Optional[int] = None,
    cost_usd: Optional[float] = None,
    provider: Optional[str] = None,          # Fase 4: 'anthropic' | 'deepseek' | ...
    content_summary: Optional[str] = None,   # redacted; never the raw text
    refused: bool = False,
) -> None:
    """Persist one row to agent_conversations.

    Failures here are logged but never raised — an audit miss is annoying;
    a 500 to the user is worse. The streaming response is already on
    the wire when this is called.

    Fase 4 of the multi-provider epic added two columns:
      - `provider`: which vendor served the turn ('anthropic' |
        'deepseek' | ...). Resolved from the model id by the caller
        (TurnAuditWrapper does this in __init__). NULL is allowed for
        legacy rows; the backfill in db/schema.py covers them.
      - `reasoning_tokens`: only DeepSeek-R1 populates this — pulled
        from `usage.completion_tokens_details.reasoning_tokens` by the
        DeepSeekProvider adapter. NULL or 0 elsewhere.
    """
    try:
        con = get_db()
        try:
            con.execute(
                """INSERT INTO agent_conversations
                   (tenant_id, surface, conversation_id, ts, role, model,
                    provider,
                    input_tokens, output_tokens,
                    cache_read_input_tokens, cache_creation_input_tokens,
                    reasoning_tokens,
                    latency_ms, cost_usd, content_json, refused)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tenant_id, surface, conversation_id,
                    datetime.now(timezone.utc).isoformat(),
                    role, model,
                    provider,
                    int(input_tokens or 0), int(output_tokens or 0),
                    int(cache_read_input_tokens or 0),
                    int(cache_creation_input_tokens or 0),
                    int(reasoning_tokens) if reasoning_tokens is not None else None,
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
        # Fase 4 of the multi-provider epic: derive the provider name
        # from the model prefix once at construction. The audit row
        # carries this so /agent/metrics can break down by provider.
        # Falls back to None for unknown prefixes — the metrics endpoint
        # treats those as a separate bucket.
        self._provider = _provider_for_model(model)
        self._t_start = time.monotonic()
        # PR #405 + Phase 5 pickup: track terminal-event observation so a
        # client disconnect / asyncio cancellation can still record an
        # audit row. Without this, mid-stream client aborts go silent
        # in the audit table — bad for operator debugging.
        self._terminal_recorded = False

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
            # Iterator ended without a MessageEnd / ErrorEvent. This is
            # the cancellation / disconnect path: the loop yielded
            # nothing terminal but the consumer is done. Record a
            # synthetic 'cancelled' error so the audit table reflects
            # the failure mode.
            if not self._terminal_recorded:
                self._record_cancelled()
            raise

        if isinstance(ev, MessageEnd):
            latency_ms = int((time.monotonic() - self._t_start) * 1000)
            # Fase 4: reasoning_tokens is optional in usage — only
            # DS-reasoner populates it. The audit row stores NULL when
            # absent; metrics treat NULL as 0.
            record_turn(
                tenant_id=self._tenant_id,
                surface=self._surface,
                conversation_id=self._conversation_id,
                role="assistant",
                model=self._model,
                provider=self._provider,
                input_tokens=ev.usage.get("input_tokens", 0),
                output_tokens=ev.usage.get("output_tokens", 0),
                cache_read_input_tokens=ev.usage.get("cache_read_input_tokens", 0),
                cache_creation_input_tokens=ev.usage.get("cache_creation_input_tokens", 0),
                reasoning_tokens=ev.usage.get("reasoning_tokens"),
                latency_ms=latency_ms,
                cost_usd=ev.cost_usd,
                refused=False,
            )
            # Charge the tenant's daily/monthly quota AFTER the turn
            # completed successfully — best-effort, swallows exceptions
            # (see api/agent/quotas.py:record_spend docstring).
            from api.agent.quotas import record_spend
            record_spend(self._tenant_id, ev.cost_usd or 0.0)
            self._terminal_recorded = True
        elif isinstance(ev, ErrorEvent):
            latency_ms = int((time.monotonic() - self._t_start) * 1000)
            record_turn(
                tenant_id=self._tenant_id,
                surface=self._surface,
                conversation_id=self._conversation_id,
                role="error",
                model=self._model,
                provider=self._provider,
                latency_ms=latency_ms,
                # We don't store the user_message verbatim (it's a fixed
                # friendly string, not signal worth indexing); the reason
                # enum is the meaningful field.
                content_summary=ev.reason,
                refused=(ev.reason == "too_many_tool_hops"),
            )
            self._terminal_recorded = True
        return ev

    def _record_cancelled(self) -> None:
        """Best-effort audit row for the iterator-ended-early path. Same
        fail-quiet discipline as record_turn — a missed cancellation row
        is annoying, raising here would surface as a 500 on a request
        that's already torn down."""
        try:
            latency_ms = int((time.monotonic() - self._t_start) * 1000)
            record_turn(
                tenant_id=self._tenant_id,
                surface=self._surface,
                conversation_id=self._conversation_id,
                role="error",
                model=self._model,
                provider=self._provider,
                latency_ms=latency_ms,
                content_summary="cancelled",
                refused=False,
            )
            self._terminal_recorded = True
        except Exception:  # noqa: BLE001
            log.warning(
                "TurnAuditWrapper._record_cancelled failed for tenant=%s conv=%s",
                self._tenant_id, self._conversation_id, exc_info=True,
            )

    @staticmethod
    def _resolve_provider_static(model: str) -> Optional[str]:
        # Static accessor so tests can verify the same logic without
        # instantiating the wrapper. Mirrors the helper at module
        # bottom; same fallback semantics (None on unknown).
        return _provider_for_model(model)

    async def aclose(self) -> None:
        """Called by FastAPI's StreamingResponse when the client
        disconnects mid-stream. Forwards close to the underlying iterator
        AND records a cancellation row if no terminal event fired."""
        if not self._terminal_recorded:
            self._record_cancelled()
        inner_close = getattr(self._events, "aclose", None)
        if inner_close is not None:
            try:
                await inner_close()
            except Exception:  # noqa: BLE001
                log.warning("inner loop aclose failed", exc_info=True)


def _provider_for_model(model: str) -> Optional[str]:
    """Fase 4 of the multi-provider epic: derive provider name from a
    model id. Used by TurnAuditWrapper to populate
    agent_conversations.provider so /agent/metrics can break down by
    provider.

    Returns None for unknown prefixes — metrics treat that as a
    separate bucket so the operator notices the orphan rather than
    silently bucketing it.

    Mirrors PROVIDER_NAME_BY_PREFIX in api/agent/providers/registry.py.
    Adding a new provider requires updating BOTH the registry and this
    helper. We deliberately do NOT import the registry here (it pulls
    in the anthropic SDK lazy-load chain) — that's a per-module
    layering decision, not a typo.
    """
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith("deepseek-"):
        return "deepseek"
    return None
