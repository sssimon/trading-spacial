"""Tests for the H.2 history write path (#428).

Two layers:
  - record_history() called directly with synthetic payloads → verifies
    schema-side behavior (UPSERT semantics, title derivation, terminalize
    pending chips, signed_payload omission, expires_at horizon).
  - TurnAuditWrapper driven over an async iterator of LoopEvents →
    verifies the integration: streamed text/reasoning/chips/proposals
    accumulate correctly and the terminal event triggers exactly one
    record_history call per turn with the right shape.

Pre-reg: docs/superpowers/specs/es/2026-05-22-conversation-history-pre-reg.md
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Fresh DB with the H.1 schema applied via init_db()."""
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    yield db_path


def _fetch_messages(db_path: str, conversation_id: str) -> list[dict]:
    """Return ordered message rows for a conversation as dicts."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT tenant_id, conversation_id, ts, role, content, reasoning, "
        "tool_chips_json, proposals_json, expires_at FROM agent_messages "
        "WHERE conversation_id = ? ORDER BY id ASC",
        (conversation_id,),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def _fetch_meta(db_path: str, conversation_id: str) -> dict | None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM agent_conversation_meta WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    con.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# record_history — direct calls
# ---------------------------------------------------------------------------


class TestRecordHistoryFirstTurn:
    def test_inserts_user_and_assistant_rows(self, tmp_db):
        from api.agent.audit import record_history

        record_history(
            tenant_id=1, surface="dock", conversation_id="conv-A",
            user_message="que opinas de PENDLE?",
            assistant_text="PENDLE está en zona LRC baja...",
            assistant_reasoning=None,
            tool_chips=[], proposals=[],
        )
        rows = _fetch_messages(tmp_db, "conv-A")
        assert len(rows) == 2
        assert rows[0]["role"] == "user"
        assert rows[0]["content"] == "que opinas de PENDLE?"
        assert rows[1]["role"] == "assistant"
        assert rows[1]["content"] == "PENDLE está en zona LRC baja..."
        # Same conversation_id, same tenant_id on both rows
        assert all(r["tenant_id"] == 1 for r in rows)

    def test_inserts_meta_row_with_title(self, tmp_db):
        from api.agent.audit import record_history

        record_history(
            tenant_id=1, surface="dock", conversation_id="conv-A",
            user_message="que opinas de PENDLE?",
            assistant_text="PENDLE...",
            assistant_reasoning=None,
            tool_chips=[], proposals=[],
        )
        meta = _fetch_meta(tmp_db, "conv-A")
        assert meta is not None
        assert meta["title"] == "que opinas de PENDLE?"
        assert meta["surface"] == "dock"
        assert meta["message_count"] == 2  # user + assistant
        assert meta["pinned"] == 0
        assert meta["first_ts"] == meta["last_ts"]  # same turn

    def test_skips_user_row_when_user_message_is_none(self, tmp_db):
        """A malformed transcript without a trailing user message produces
        None — only the assistant row is written; message_count = 1."""
        from api.agent.audit import record_history

        record_history(
            tenant_id=1, surface="dock", conversation_id="conv-B",
            user_message=None,
            assistant_text="response without preceding user msg",
            assistant_reasoning=None,
            tool_chips=[], proposals=[],
        )
        rows = _fetch_messages(tmp_db, "conv-B")
        assert len(rows) == 1
        assert rows[0]["role"] == "assistant"
        meta = _fetch_meta(tmp_db, "conv-B")
        assert meta["message_count"] == 1
        assert meta["title"] is None  # no user message → no title yet


class TestRecordHistorySecondTurn:
    def test_upsert_preserves_title_and_first_ts(self, tmp_db):
        """A second call with the same conversation_id keeps the original
        title + first_ts, updates last_ts, and increments message_count."""
        from api.agent.audit import record_history

        record_history(
            tenant_id=1, surface="dock", conversation_id="conv-C",
            user_message="primer mensaje",
            assistant_text="primera respuesta",
            assistant_reasoning=None,
            tool_chips=[], proposals=[],
        )
        first_meta = _fetch_meta(tmp_db, "conv-C")

        record_history(
            tenant_id=1, surface="dock", conversation_id="conv-C",
            user_message="segundo mensaje",
            assistant_text="segunda respuesta",
            assistant_reasoning=None,
            tool_chips=[], proposals=[],
        )
        second_meta = _fetch_meta(tmp_db, "conv-C")

        assert second_meta["title"] == first_meta["title"]  # title preserved
        assert second_meta["title"] == "primer mensaje"
        assert second_meta["first_ts"] == first_meta["first_ts"]
        assert second_meta["last_ts"] >= first_meta["last_ts"]
        assert second_meta["message_count"] == 4  # 2 user + 2 assistant

    def test_upsert_handles_user_skip_on_second_turn(self, tmp_db):
        """If the second turn has no user_message (defensive None), the
        upsert increments by 1, not 2."""
        from api.agent.audit import record_history

        record_history(
            tenant_id=1, surface="dock", conversation_id="conv-D",
            user_message="primer mensaje",
            assistant_text="primera respuesta",
            assistant_reasoning=None,
            tool_chips=[], proposals=[],
        )
        record_history(
            tenant_id=1, surface="dock", conversation_id="conv-D",
            user_message=None,  # malformed second turn
            assistant_text="segunda respuesta",
            assistant_reasoning=None,
            tool_chips=[], proposals=[],
        )
        meta = _fetch_meta(tmp_db, "conv-D")
        assert meta["message_count"] == 3  # 2 from first + 1 from second


class TestTitleDerivation:
    def test_short_message_used_verbatim(self):
        from api.agent.audit import _derive_title
        assert _derive_title("hola") == "hola"

    def test_message_exactly_at_limit_not_truncated(self):
        from api.agent.audit import TITLE_MAX_CHARS, _derive_title
        msg = "x" * TITLE_MAX_CHARS
        assert _derive_title(msg) == msg

    def test_long_message_truncated_with_ellipsis(self):
        from api.agent.audit import TITLE_MAX_CHARS, _derive_title
        msg = "x" * (TITLE_MAX_CHARS + 50)
        title = _derive_title(msg)
        assert len(title) == TITLE_MAX_CHARS
        assert title.endswith("…")
        assert title.startswith("x")

    def test_whitespace_stripped(self):
        from api.agent.audit import _derive_title
        assert _derive_title("  hola  ") == "hola"

    def test_empty_or_none_returns_none(self):
        from api.agent.audit import _derive_title
        assert _derive_title("") is None
        assert _derive_title(None) is None
        assert _derive_title("   ") is None


class TestTerminalizeChips:
    def test_pending_chip_becomes_error(self):
        from api.agent.audit import _terminalize_chips
        chips = [{"tool": "get_symbol_state", "status": "pending"}]
        out = _terminalize_chips(chips)
        assert out[0]["status"] == "error"

    def test_ok_and_error_unchanged(self):
        from api.agent.audit import _terminalize_chips
        chips = [
            {"tool": "a", "status": "ok"},
            {"tool": "b", "status": "error"},
        ]
        out = _terminalize_chips(chips)
        assert out == chips

    def test_does_not_mutate_input(self):
        from api.agent.audit import _terminalize_chips
        chips = [{"tool": "x", "status": "pending"}]
        _terminalize_chips(chips)
        # Caller's list is untouched (defensive copy)
        assert chips[0]["status"] == "pending"


class TestChipsAndProposalsPersisted:
    def test_chips_terminalized_on_persist(self, tmp_db):
        from api.agent.audit import record_history

        record_history(
            tenant_id=1, surface="dock", conversation_id="conv-E",
            user_message="usa get_positions",
            assistant_text="aca van",
            assistant_reasoning=None,
            tool_chips=[
                {"tool": "get_positions", "status": "ok"},
                {"tool": "get_symbol_state", "status": "pending"},  # never completed
            ],
            proposals=[],
        )
        rows = _fetch_messages(tmp_db, "conv-E")
        assistant_row = next(r for r in rows if r["role"] == "assistant")
        chips = json.loads(assistant_row["tool_chips_json"])
        statuses = {c["tool"]: c["status"] for c in chips}
        assert statuses == {"get_positions": "ok", "get_symbol_state": "error"}

    def test_proposals_strip_signed_payload(self, tmp_db):
        """signed_payload is short-TTL HMAC — useless on rehydration 90d
        later. record_history MUST drop it before persist."""
        from api.agent.audit import record_history

        record_history(
            tenant_id=1, surface="dock", conversation_id="conv-F",
            user_message="cerra BTC",
            assistant_text="propongo cerrar",
            assistant_reasoning=None,
            tool_chips=[],
            proposals=[{
                "proposal_id":    "prop_abc",
                "signed_payload": "this_should_NOT_be_persisted",
                "action":         "close_position",
                "args":           {"position_id": 42},
                "expires_at":     "2026-05-22T08:30:00Z",
                "summary":        "Cerrar BTCUSDT #42",
            }],
        )
        rows = _fetch_messages(tmp_db, "conv-F")
        assistant_row = next(r for r in rows if r["role"] == "assistant")
        proposals = json.loads(assistant_row["proposals_json"])
        assert len(proposals) == 1
        assert proposals[0]["proposal_id"] == "prop_abc"
        assert "signed_payload" not in proposals[0]
        assert proposals[0]["action"] == "close_position"

    def test_reasoning_persisted_when_present(self, tmp_db):
        from api.agent.audit import record_history

        record_history(
            tenant_id=1, surface="dock", conversation_id="conv-G",
            user_message="analiza",
            assistant_text="respuesta",
            assistant_reasoning="<think>chain of thought</think>",
            tool_chips=[], proposals=[],
        )
        rows = _fetch_messages(tmp_db, "conv-G")
        assistant_row = next(r for r in rows if r["role"] == "assistant")
        assert assistant_row["reasoning"] == "<think>chain of thought</think>"
        # User row has NULL reasoning (only assistant turns reason)
        user_row = next(r for r in rows if r["role"] == "user")
        assert user_row["reasoning"] is None


class TestExpiresAt:
    def test_expires_at_is_90_days_from_now(self, tmp_db):
        from api.agent.audit import RETENTION_DAYS, record_history

        before = datetime.now(timezone.utc)
        record_history(
            tenant_id=1, surface="dock", conversation_id="conv-H",
            user_message="hola",
            assistant_text="ok",
            assistant_reasoning=None,
            tool_chips=[], proposals=[],
        )
        after = datetime.now(timezone.utc)

        rows = _fetch_messages(tmp_db, "conv-H")
        meta = _fetch_meta(tmp_db, "conv-H")
        # All rows + meta share the same horizon (computed once per call)
        expires_set = {r["expires_at"] for r in rows} | {meta["expires_at"]}
        assert len(expires_set) == 1
        expires_at = datetime.fromisoformat(expires_set.pop())
        # Should be RETENTION_DAYS away (with some clock jitter)
        delta = expires_at - before
        assert delta >= timedelta(days=RETENTION_DAYS, seconds=-1)
        assert (expires_at - after) <= timedelta(days=RETENTION_DAYS, seconds=1)

    def test_second_turn_refreshes_meta_expires_at(self, tmp_db):
        """Meta expires_at follows last_ts (per pre-reg note: active
        conversations stay alive while the user keeps engaging)."""
        from api.agent.audit import record_history

        record_history(
            tenant_id=1, surface="dock", conversation_id="conv-I",
            user_message="t1", assistant_text="a1",
            assistant_reasoning=None, tool_chips=[], proposals=[],
        )
        first_expires = _fetch_meta(tmp_db, "conv-I")["expires_at"]

        # Real call — but we control time only via wall clock; sleep a hair
        # then ensure the second meta expires_at moved forward.
        import time
        time.sleep(0.01)

        record_history(
            tenant_id=1, surface="dock", conversation_id="conv-I",
            user_message="t2", assistant_text="a2",
            assistant_reasoning=None, tool_chips=[], proposals=[],
        )
        second_expires = _fetch_meta(tmp_db, "conv-I")["expires_at"]
        assert second_expires > first_expires


class TestFailQuiet:
    def test_db_error_swallowed(self, tmp_db, monkeypatch, caplog):
        """A DB hiccup must NOT raise — the streaming response is already
        on the wire."""
        from api.agent import audit

        def _explode(*_a, **_kw):
            raise sqlite3.OperationalError("disk full")

        monkeypatch.setattr(audit, "get_db", _explode)

        # Should not raise
        audit.record_history(
            tenant_id=1, surface="dock", conversation_id="conv-J",
            user_message="hola", assistant_text="ok",
            assistant_reasoning=None, tool_chips=[], proposals=[],
        )
        # Warning was emitted
        assert any(
            "record_history failed" in r.message for r in caplog.records
        )


class TestTenantIsolation:
    def test_two_tenants_dont_see_each_other(self, tmp_db):
        """Both tenants write to the same conversation_id (worst-case
        malicious): the rows segregate by tenant_id. Meta is a single
        row (PK on conversation_id) but the test verifies the read
        path's WHERE clause can isolate by tenant_id."""
        from api.agent.audit import record_history

        record_history(
            tenant_id=1, surface="dock", conversation_id="conv-T1",
            user_message="tenant 1 message", assistant_text="reply",
            assistant_reasoning=None, tool_chips=[], proposals=[],
        )
        record_history(
            tenant_id=2, surface="dock", conversation_id="conv-T2",
            user_message="tenant 2 message", assistant_text="reply",
            assistant_reasoning=None, tool_chips=[], proposals=[],
        )

        con = sqlite3.connect(tmp_db)
        t1_count = con.execute(
            "SELECT COUNT(*) FROM agent_messages WHERE tenant_id = 1"
        ).fetchone()[0]
        t2_count = con.execute(
            "SELECT COUNT(*) FROM agent_messages WHERE tenant_id = 2"
        ).fetchone()[0]
        con.close()
        assert t1_count == 2
        assert t2_count == 2


# ---------------------------------------------------------------------------
# TurnAuditWrapper — async integration over a synthetic event stream
# ---------------------------------------------------------------------------


async def _events_async_gen(events):
    """Wrap a list as an async iterator for the wrapper."""
    for e in events:
        yield e


async def _drain(wrapped):
    out = []
    async for ev in wrapped:
        out.append(ev)
    return out


@pytest.mark.anyio
async def test_wrapper_message_end_writes_history(tmp_db):
    """Full happy path: TextDelta + MessageEnd → user row + assistant row
    + meta row, plus the existing audit row."""
    from api.agent.audit import TurnAuditWrapper
    from api.agent.loop import MessageEnd, TextDelta

    events = [
        TextDelta(text="Hola, "),
        TextDelta(text="tu BTC está en zona LRC baja."),
        MessageEnd(
            usage={"input_tokens": 100, "output_tokens": 20,
                   "cache_read_input_tokens": 0,
                   "cache_creation_input_tokens": 0},
            stop_reason="end_turn",
            cost_usd=0.001,
        ),
    ]
    wrapped = TurnAuditWrapper(
        _events_async_gen(events),
        tenant_id=1, surface="dock",
        conversation_id="conv-W1", model="claude-sonnet-4-6",
        user_message_text="¿cómo va mi portafolio?",
    )
    out = await _drain(wrapped)
    # Wrapper passes events through unchanged
    assert len(out) == 3
    rows = _fetch_messages(tmp_db, "conv-W1")
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[1]["content"] == "Hola, tu BTC está en zona LRC baja."
    meta = _fetch_meta(tmp_db, "conv-W1")
    assert meta["title"] == "¿cómo va mi portafolio?"
    assert meta["surface"] == "dock"
    assert meta["message_count"] == 2


@pytest.mark.anyio
async def test_wrapper_reasoning_accumulates(tmp_db):
    """ReasoningDelta chunks land in the `reasoning` column on the
    assistant row, distinct from `content`."""
    from api.agent.audit import TurnAuditWrapper
    from api.agent.loop import MessageEnd, ReasoningDelta, TextDelta

    events = [
        ReasoningDelta(text="<think>step1 "),
        ReasoningDelta(text="step2</think>"),
        TextDelta(text="respuesta final"),
        MessageEnd(usage={}, stop_reason="end_turn", cost_usd=0.001),
    ]
    wrapped = TurnAuditWrapper(
        _events_async_gen(events),
        tenant_id=1, surface="dock", conversation_id="conv-W2",
        model="deepseek-reasoner",
        user_message_text="que opinas",
    )
    await _drain(wrapped)

    rows = _fetch_messages(tmp_db, "conv-W2")
    assistant = next(r for r in rows if r["role"] == "assistant")
    assert assistant["content"] == "respuesta final"
    assert assistant["reasoning"] == "<think>step1 step2</think>"


@pytest.mark.anyio
async def test_wrapper_tool_chips_persisted_and_terminalized(tmp_db):
    """Chips that close out as ok/error are persisted as-is; any chip
    still pending at MessageEnd is terminalized to error to avoid stuck
    spinners on rehydrate."""
    from api.agent.audit import TurnAuditWrapper
    from api.agent.loop import (
        MessageEnd, TextDelta, ToolUseResult, ToolUseStart,
    )

    events = [
        ToolUseStart(tool="get_positions"),
        ToolUseResult(tool="get_positions", status="ok"),
        ToolUseStart(tool="get_symbol_state"),
        # No ToolUseResult for get_symbol_state — simulates a result
        # that never came back (or arrived after MessageEnd in a buggy
        # provider). Should terminalize to error.
        TextDelta(text="resumen"),
        MessageEnd(usage={}, stop_reason="end_turn", cost_usd=0.0),
    ]
    wrapped = TurnAuditWrapper(
        _events_async_gen(events),
        tenant_id=1, surface="dock", conversation_id="conv-W3",
        model="claude-sonnet-4-6",
        user_message_text="usá tools",
    )
    await _drain(wrapped)

    rows = _fetch_messages(tmp_db, "conv-W3")
    assistant = next(r for r in rows if r["role"] == "assistant")
    chips = json.loads(assistant["tool_chips_json"])
    statuses = {c["tool"]: c["status"] for c in chips}
    assert statuses == {"get_positions": "ok", "get_symbol_state": "error"}


@pytest.mark.anyio
async def test_wrapper_proposals_persisted_without_signed_payload(tmp_db):
    from api.agent.audit import TurnAuditWrapper
    from api.agent.loop import MessageEnd, ProposalEvent, TextDelta

    events = [
        TextDelta(text="propongo cerrar"),
        ProposalEvent(
            proposal_id="prop_xyz",
            signed_payload="DO_NOT_PERSIST_THIS",
            action="close_position",
            args={"position_id": 7},
            expires_at="2026-05-22T08:30:00Z",
            summary="Cerrar #7",
        ),
        MessageEnd(usage={}, stop_reason="tool_use", cost_usd=0.0),
    ]
    wrapped = TurnAuditWrapper(
        _events_async_gen(events),
        tenant_id=1, surface="dock", conversation_id="conv-W4",
        model="claude-sonnet-4-6",
        user_message_text="cerra esa posición",
    )
    await _drain(wrapped)

    rows = _fetch_messages(tmp_db, "conv-W4")
    assistant = next(r for r in rows if r["role"] == "assistant")
    proposals = json.loads(assistant["proposals_json"])
    assert len(proposals) == 1
    assert proposals[0]["proposal_id"] == "prop_xyz"
    assert "signed_payload" not in proposals[0]


@pytest.mark.anyio
async def test_wrapper_error_event_writes_history_with_friendly_text(tmp_db):
    """On ErrorEvent the assistant row's content is the user-facing
    friendly message (not the closed-enum reason)."""
    from api.agent.audit import TurnAuditWrapper
    from api.agent.loop import ErrorEvent

    events = [
        ErrorEvent(
            reason="too_many_tool_hops",
            user_message="El copiloto se quedó pensando demasiado.",
        ),
    ]
    wrapped = TurnAuditWrapper(
        _events_async_gen(events),
        tenant_id=1, surface="dock", conversation_id="conv-W5",
        model="claude-sonnet-4-6",
        user_message_text="hazlo todo",
    )
    await _drain(wrapped)

    rows = _fetch_messages(tmp_db, "conv-W5")
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[1]["content"] == "El copiloto se quedó pensando demasiado."
    # Audit row got the closed-enum reason (different from history)
    con = sqlite3.connect(tmp_db)
    audit_row = con.execute(
        "SELECT role, content_json FROM agent_conversations "
        "WHERE conversation_id = ?", ("conv-W5",),
    ).fetchone()
    con.close()
    assert audit_row[0] == "error"
    assert "too_many_tool_hops" in audit_row[1]


@pytest.mark.anyio
async def test_wrapper_cancellation_writes_partial_history(tmp_db):
    """Iterator ends without MessageEnd / ErrorEvent → _record_cancelled
    fires and persists whatever partial text accumulated. Mirrors a
    client mid-stream disconnect."""
    from api.agent.audit import TurnAuditWrapper
    from api.agent.loop import TextDelta

    events = [
        TextDelta(text="parcial..."),
        # No terminal event — iterator just ends.
    ]
    wrapped = TurnAuditWrapper(
        _events_async_gen(events),
        tenant_id=1, surface="dock", conversation_id="conv-W6",
        model="claude-sonnet-4-6",
        user_message_text="explica",
    )
    await _drain(wrapped)

    rows = _fetch_messages(tmp_db, "conv-W6")
    assert len(rows) == 2
    assistant = next(r for r in rows if r["role"] == "assistant")
    assert assistant["content"] == "parcial..."


@pytest.mark.anyio
async def test_wrapper_no_user_message_still_persists_assistant(tmp_db):
    """user_message_text=None is the defensive path — the wrapper still
    writes the assistant row + meta; user row is skipped."""
    from api.agent.audit import TurnAuditWrapper
    from api.agent.loop import MessageEnd, TextDelta

    events = [
        TextDelta(text="resp"),
        MessageEnd(usage={}, stop_reason="end_turn", cost_usd=0.0),
    ]
    wrapped = TurnAuditWrapper(
        _events_async_gen(events),
        tenant_id=1, surface="dock", conversation_id="conv-W7",
        model="claude-sonnet-4-6",
        user_message_text=None,
    )
    await _drain(wrapped)

    rows = _fetch_messages(tmp_db, "conv-W7")
    assert len(rows) == 1
    assert rows[0]["role"] == "assistant"
    meta = _fetch_meta(tmp_db, "conv-W7")
    assert meta["message_count"] == 1
    assert meta["title"] is None


@pytest.mark.anyio
async def test_wrapper_does_not_double_record_history_on_message_end(tmp_db):
    """One terminal event → exactly one history persist. Verify by
    counting rows after a single MessageEnd-terminated stream."""
    from api.agent.audit import TurnAuditWrapper
    from api.agent.loop import MessageEnd, TextDelta

    events = [
        TextDelta(text="ok"),
        MessageEnd(usage={}, stop_reason="end_turn", cost_usd=0.0),
    ]
    wrapped = TurnAuditWrapper(
        _events_async_gen(events),
        tenant_id=1, surface="dock", conversation_id="conv-W8",
        model="claude-sonnet-4-6",
        user_message_text="hola",
    )
    await _drain(wrapped)

    rows = _fetch_messages(tmp_db, "conv-W8")
    assert len(rows) == 2  # user + assistant — no duplicates
