"""Per-turn audit writes to agent_conversations.

Phase 2 of epic #400. Every completed model turn (MessageEnd) AND every
errored turn (ErrorEvent) emits one row in `agent_conversations`. The
write is best-effort and fail-tolerant: a DB hiccup must NOT kill the
streaming response that's already on the wire to the user.

Pre-reg §9. Shape of the row mirrors the schema in db/schema.py.

Epic #428 H.2 extends this module: in addition to the audit row, every
terminal event also persists the raw user/assistant content into
`agent_messages` + upserts `agent_conversation_meta`. That tier feeds
the per-tenant history sidebar (H.3..H.5). Pre-reg
docs/superpowers/specs/es/2026-05-22-conversation-history-pre-reg.md.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from db.connection import get_db

log = logging.getLogger("api.agent.audit")


# ── Epic #428 history constants ────────────────────────────────────────
#
# RETENTION_DAYS controls how long a turn's content remains visible in
# the sidebar / re-hydration endpoint. The audit table (agent_conversations)
# has its OWN lifecycle and is NOT subject to this — operator-facing
# /agent/metrics needs the full audit history regardless.
#
# TITLE_MAX_CHARS — pre-reg D.3 sets the first 80 chars of the first
# user message as the sidebar title (no LLM-summary in v1).
RETENTION_DAYS = 90
TITLE_MAX_CHARS = 80


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
        with get_db() as con:
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
    except Exception:  # noqa: BLE001
        log.warning(
            "record_turn failed for tenant=%s conv=%s — audit row dropped",
            tenant_id, conversation_id, exc_info=True,
        )


def _retention_expiry_iso(now: Optional[datetime] = None) -> str:
    """ISO timestamp RETENTION_DAYS in the future (computed-on-write).

    Pre-reg D.2: cleanup-on-read filters `expires_at <= NOW` so a row
    written today expires on day 91. Computing at write time keeps the
    WHERE clause cheap (no per-row arithmetic).
    """
    base = now or datetime.now(timezone.utc)
    return (base + timedelta(days=RETENTION_DAYS)).isoformat()


def _derive_title(user_message: Optional[str]) -> Optional[str]:
    """First TITLE_MAX_CHARS of the user message, ellipsis-suffixed if
    truncated. Returns None if the message is empty/None — meta row goes
    in with NULL title and the sidebar shows a fallback placeholder."""
    if not user_message:
        return None
    s = user_message.strip()
    if not s:
        return None
    if len(s) <= TITLE_MAX_CHARS:
        return s
    return s[: TITLE_MAX_CHARS - 1] + "…"


def _terminalize_chips(chips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Coerce any 'pending' chip to 'error' before persisting.

    Without this, a turn that ended via ErrorEvent or client-cancel would
    persist a chip with status='pending' that the rehydrated UI would
    render as a perpetual spinner. The tool didn't return a result, so
    treat it as error for display purposes — the audit table has the
    actual closed-enum reason if an operator needs to investigate.
    """
    out: list[dict[str, Any]] = []
    for c in chips:
        if c.get("status") == "pending":
            c = {**c, "status": "error"}
        out.append(c)
    return out


def record_history(
    *,
    tenant_id: int,
    surface: str,
    conversation_id: str,
    user_message: Optional[str],
    assistant_text: str,
    assistant_reasoning: Optional[str],
    tool_chips: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
) -> None:
    """Persist one turn's content to agent_messages + upsert the meta row.

    Writes:
      - 1 row in `agent_messages` with role='user' if user_message is set.
      - 1 row in `agent_messages` with role='assistant' (always — even if
        the turn errored, the assistant content is the friendly error
        text so the rehydrated transcript stays coherent).
      - 1 UPSERT into `agent_conversation_meta` keyed by conversation_id:
        INSERT on first turn (carrying the title derived from the user
        message); UPDATE on subsequent turns (refresh last_ts +
        message_count, preserve title).

    Fail-tolerant: any DB error swallows + logs warning. The streaming
    response is already on the wire when this is called; raising here
    would surface as a 500 on a request whose body has already been
    flushed. Mirror of the discipline in record_turn.

    Note on signed_payload omission: the proposal envelopes stored here
    deliberately drop the `signed_payload` field. The HMAC has a short
    TTL (minutes); persisting the token across 90 days would be storing
    an expired credential. H.3 reconstructs the proposal state by joining
    against agent_side_effects on confirm.
    """
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        expires_at = _retention_expiry_iso()
        chips_persisted = _terminalize_chips(tool_chips)
        proposals_persisted = [
            {k: v for k, v in p.items() if k != "signed_payload"}
            for p in proposals
        ]
        rows_inserted = 0

        with get_db() as con:
            # Cross-tenant guard (PR #433 review fix). The H.1 schema
            # makes conversation_id the sole PK on agent_conversation_meta,
            # so two tenants writing the same conversation_id collide.
            # Without this check, an attacker who learned a victim's
            # UUID could mutate the victim's meta row via the UPSERT
            # (bumping last_ts / message_count / expires_at) and
            # leave orphan agent_messages rows tagged with their own
            # tenant_id. UUID guessability is low, but UUIDs leak
            # through logs / screenshots / sharing — defense in depth.
            #
            # Two layers:
            #   1. This SELECT short-circuits the common case (sequential
            #      writes). Abort fail-quiet — same discipline as the
            #      outer try/except — and log so an operator can spot
            #      attempts in webhook.log.
            #   2. The DO UPDATE below carries an additional WHERE clause
            #      that makes it a silent no-op if the meta row ends up
            #      owned by a different tenant (covers the race where
            #      the SELECT saw nothing because the victim's INSERT
            #      hadn't committed yet under WAL).
            existing = con.execute(
                "SELECT tenant_id FROM agent_conversation_meta "
                "WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if existing is not None and existing[0] != tenant_id:
                log.warning(
                    "record_history: refusing cross-tenant write — "
                    "conversation_id=%s owned by tenant=%s, attempted by "
                    "tenant=%s", conversation_id, existing[0], tenant_id,
                )
                return

            if user_message:
                con.execute(
                    """INSERT INTO agent_messages
                       (tenant_id, conversation_id, ts, role, content,
                        reasoning, tool_chips_json, proposals_json, expires_at)
                       VALUES (?, ?, ?, 'user', ?, NULL, NULL, NULL, ?)""",
                    (tenant_id, conversation_id, now_iso, user_message, expires_at),
                )
                rows_inserted += 1

            con.execute(
                """INSERT INTO agent_messages
                   (tenant_id, conversation_id, ts, role, content,
                    reasoning, tool_chips_json, proposals_json, expires_at)
                   VALUES (?, ?, ?, 'assistant', ?, ?, ?, ?, ?)""",
                (
                    tenant_id, conversation_id, now_iso, assistant_text,
                    assistant_reasoning,
                    json.dumps(chips_persisted) if chips_persisted else None,
                    json.dumps(proposals_persisted) if proposals_persisted else None,
                    expires_at,
                ),
            )
            rows_inserted += 1

            con.execute(
                """INSERT INTO agent_conversation_meta
                   (conversation_id, tenant_id, title, surface,
                    first_ts, last_ts, message_count, pinned, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                   ON CONFLICT(conversation_id) DO UPDATE SET
                       last_ts = excluded.last_ts,
                       message_count = message_count + excluded.message_count,
                       expires_at = excluded.expires_at
                   WHERE agent_conversation_meta.tenant_id = excluded.tenant_id""",
                (
                    conversation_id, tenant_id, _derive_title(user_message),
                    surface, now_iso, now_iso, rows_inserted, expires_at,
                ),
            )
            con.commit()
    except Exception:  # noqa: BLE001
        log.warning(
            "record_history failed for tenant=%s conv=%s — history rows dropped",
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
        user_message_text: Optional[str] = None,
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

        # Epic #428 H.2: accumulate per-turn content so a single
        # record_history() call at the terminal event can persist the
        # full assistant message + reasoning + tool chips + proposals.
        # The user message arrives via constructor (router pulls it
        # from body.messages[-1]); the assistant side is built up from
        # the streamed events. Pre-reg §write path.
        self._user_message_text = user_message_text
        self._assistant_text: str = ""
        self._assistant_reasoning: Optional[str] = None
        self._tool_chips: list[dict[str, Any]] = []
        self._proposals: list[dict[str, Any]] = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        # Import here to avoid circular import: loop.py imports prompts,
        # prompts depend on registry; this module is imported from
        # router.py which sees them all.
        from api.agent.loop import (
            ErrorEvent,
            MessageEnd,
            ProposalEvent,
            ReasoningDelta,
            TextDelta,
            ToolUseResult,
            ToolUseStart,
        )

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

        # Epic #428 H.2: accumulate streamed content into per-turn state
        # so the terminal event can persist everything atomically. These
        # branches mirror useAgentStream.applyEvent on the frontend so
        # the rehydrated DOM is byte-equivalent to what the user saw
        # live (modulo terminalized chip states).
        if isinstance(ev, TextDelta):
            self._assistant_text += ev.text
        elif isinstance(ev, ReasoningDelta):
            self._assistant_reasoning = (self._assistant_reasoning or "") + ev.text
        elif isinstance(ev, ToolUseStart):
            self._tool_chips.append({"tool": ev.tool, "status": "pending"})
        elif isinstance(ev, ToolUseResult):
            # Match the first pending chip with the same tool name and
            # close it out — mirror of the frontend logic in
            # useAgentStream.ts case 'tool_use_result'.
            for chip in self._tool_chips:
                if chip["tool"] == ev.tool and chip["status"] == "pending":
                    chip["status"] = ev.status
                    break
        elif isinstance(ev, ProposalEvent):
            self._proposals.append({
                "proposal_id": ev.proposal_id,
                "action":      ev.action,
                "args":        ev.args,
                "expires_at":  ev.expires_at,
                "summary":     ev.summary,
                # signed_payload deliberately omitted from history persist
                # (TTL minutes; useless on rehydrate). See record_history
                # docstring.
            })

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
            # Epic #428 H.2: persist the user-visible history alongside
            # the audit row. Same fail-quiet discipline; the streaming
            # response is already on the wire.
            self._record_history(self._assistant_text)
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
            # Epic #428 H.2: persist the user-visible history even on
            # error. The assistant content is the friendly error message
            # so the rehydrated transcript shows what the user saw on
            # their screen (not the raw closed-enum reason).
            self._record_history(ev.user_message)
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
            # Epic #428 H.2: persist whatever assistant content
            # accumulated before cancel + the user message. The
            # rehydrated transcript shows the partial answer the user
            # saw before disconnecting.
            self._record_history(self._assistant_text)
            self._terminal_recorded = True
        except Exception:  # noqa: BLE001
            log.warning(
                "TurnAuditWrapper._record_cancelled failed for tenant=%s conv=%s",
                self._tenant_id, self._conversation_id, exc_info=True,
            )

    def _record_history(self, assistant_text_final: str) -> None:
        """Delegate to the module-level record_history with the wrapper's
        accumulated state. Centralizes the call so MessageEnd / ErrorEvent
        / cancelled all share the same fail-quiet semantics + payload
        shape."""
        record_history(
            tenant_id=self._tenant_id,
            surface=self._surface,
            conversation_id=self._conversation_id,
            user_message=self._user_message_text,
            assistant_text=assistant_text_final,
            assistant_reasoning=self._assistant_reasoning,
            tool_chips=self._tool_chips,
            proposals=self._proposals,
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
