"""Phase 5B of epic #400 — §11.8 graceful failures + deferred pickups.

Existing coverage (in test_agent_loop.py):
  - too_many_tool_hops → ErrorEvent with closed-enum reason
  - upstream exception on stream open → ErrorEvent reason='upstream'
  - tool handler returns is_error → block carries is_error:true and
    the model continues

This file adds the gaps:
  - upstream mid-stream drop (the raise happens INSIDE iteration, not
    on enter) → still surfaces as 'upstream', no leak of the inner
    exception class to the user
  - tool handler that RAISES (not returns is_error) — confirms the
    dispatch layer wraps the exception into an is_error tool_result
    without crashing the loop
  - Mid-stream client disconnect via TurnAuditWrapper.aclose() — PR
    #408 deferred pickup. The StopAsyncIteration path was already
    tested; this covers the explicit aclose-mid-stream case.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from tests._fakes import FakeAnthropicClient, FakeTurnBuilder
from db.transaction import transaction


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    yield db_path


async def _collect(events_iter):
    out = []
    async for ev in events_iter:
        out.append(ev)
    return out


# ── Upstream errors ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_upstream_mid_stream_drop_emits_friendly_error(tmp_db):
    """A raise from INSIDE the async iteration (e.g. server hung up
    after sending partial text) must surface as the same `upstream`
    closed-enum reason, NOT leak the inner exception class to the
    user. FakeAnthropicClient.queue_error(..., mid_stream=True)
    simulates this."""
    from api.agent.loop import run_turn, ErrorEvent

    c = FakeAnthropicClient()
    c.queue_error(RuntimeError("simulated connection drop mid-stream"),
                   mid_stream=True)

    msgs = [{"role": "user", "content": "hola"}]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))
    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert errors
    assert errors[0].reason == "upstream"
    # User-facing message is fixed; never quotes the inner exception
    # type / args (would leak implementation detail).
    assert "RuntimeError" not in errors[0].user_message
    assert "simulated" not in errors[0].user_message


@pytest.mark.anyio
async def test_tool_handler_raise_lands_as_is_error_block(tmp_db, monkeypatch):
    """If a tool handler RAISES (not returns an error dict), the
    dispatch layer must wrap the exception so the model sees an
    is_error:true tool_result and keeps going. A naive loop that
    propagates the exception would abort the whole turn with a 500;
    we lock it as a graceful path instead."""
    from api.agent.loop import run_turn
    from api.agent.tools import handlers as h

    def _exploding_handler(*, tenant_id):
        raise RuntimeError("handler crashed for fun")
    monkeypatch.setitem(h.TOOL_HANDLERS, "get_positions", _exploding_handler)

    c = FakeAnthropicClient()
    c.queue_turn(FakeTurnBuilder()
                  .tool_use("get_positions", {}, tool_use_id="toolu_1")
                  .stop_tool_use().build())
    c.queue_turn(FakeTurnBuilder()
                  .text("No pude leer tus posiciones. ¿Quieres reintentar?")
                  .end_turn().build())

    msgs = [{"role": "user", "content": "qué tengo"}]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))

    # Inspect the tool_result block the loop wrote — must carry
    # is_error:true so the model knows the tool didn't work.
    tool_result_msgs = [
        m for m in msgs
        if m.get("role") == "user"
        and isinstance(m.get("content"), list)
        and m["content"]
        and isinstance(m["content"][0], dict)
        and m["content"][0].get("type") == "tool_result"
    ]
    assert tool_result_msgs, "no tool_result message found"
    block = tool_result_msgs[0]["content"][0]
    assert block.get("is_error") is True
    # The content carries the closed-enum reason, NEVER the raw
    # exception message (leak guard — same as upstream above).
    assert "handler crashed for fun" not in str(block.get("content", ""))


@pytest.mark.anyio
async def test_too_many_tool_hops_emits_refused_error(tmp_db, monkeypatch):
    """A model that keeps emitting tool_use beyond MAX_TOOL_HOPS gets
    cut off with refused=True. Audit row records this distinctly from
    other errors; the operator dashboard can isolate runaway loops."""
    from api.agent.loop import run_turn, ErrorEvent
    from api.agent.tools import handlers as h

    def _stub_positions(*, tenant_id):
        return {"positions": []}
    monkeypatch.setitem(h.TOOL_HANDLERS, "get_positions", _stub_positions)

    c = FakeAnthropicClient()
    # Queue MAX_TOOL_HOPS+2 tool_use turns; loop fires the error after
    # MAX_TOOL_HOPS (defined in api/agent/loop.py).
    from api.agent.loop import MAX_TOOL_HOPS
    for i in range(MAX_TOOL_HOPS + 1):
        c.queue_turn(FakeTurnBuilder()
                      .tool_use("get_positions", {}, tool_use_id=f"toolu_{i}")
                      .stop_tool_use().build())

    msgs = [{"role": "user", "content": "loop forever"}]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))
    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert errors and errors[-1].reason == "too_many_tool_hops"


# ── Mid-stream cancellation ────────────────────────────────────────


def test_audit_wrapper_aclose_mid_stream_records_cancelled(tmp_db):
    """Pre-PR-#408-deferred: a client that disconnects MID-STREAM (after
    consuming some events but before MessageEnd) triggers aclose on
    the wrapper. The wrapper records a synthetic 'cancelled' row so
    operators can see truncated turns in audit. This is the explicit
    test the previous review asked for — the StopAsyncIteration path
    was already covered, this one covers the abrupt aclose."""
    import btc_api
    from api.agent.audit import TurnAuditWrapper
    from api.agent.loop import TextDelta

    async def _slow_events():
        yield TextDelta(text="hola ")
        yield TextDelta(text="¿cómo")
        # Generator stays alive, would yield more if asked. The test
        # closes the wrapper before it does — simulating the user
        # closing their tab mid-stream.
        await asyncio.sleep(10)
        yield TextDelta(text=" estás?")

    wrapper = TurnAuditWrapper(
        _slow_events(),
        tenant_id=1, surface="dock", conversation_id="conv-mid-cancel",
        model="claude-sonnet-4-6",
    )

    async def _drive():
        events_seen: list = []
        # Consume the first two TextDeltas via __anext__ directly.
        events_seen.append(await wrapper.__anext__())
        events_seen.append(await wrapper.__anext__())
        # Simulate client disconnect → FastAPI calls aclose.
        await wrapper.aclose()
        return events_seen

    seen = asyncio.run(_drive())
    assert len(seen) == 2  # consumed two events before aclose

    with transaction() as con:
        row = con.execute(
            "SELECT role, content_json FROM agent_conversations "
            "WHERE conversation_id = 'conv-mid-cancel'",
        ).fetchone()
    assert row is not None
    d = dict(row)
    assert d["role"] == "error"
    assert json.loads(d["content_json"]) == "cancelled"


def test_audit_wrapper_aclose_after_normal_end_is_no_op(tmp_db):
    """Belt-and-suspenders: if MessageEnd already fired (terminal
    event observed) and FastAPI then calls aclose on response
    teardown, the wrapper must NOT double-record. The
    _terminal_recorded flag enforces this. We already test the happy
    path in test_agent_metrics.py; this is the explicit late-aclose
    coverage."""
    import btc_api
    from api.agent.audit import TurnAuditWrapper
    from api.agent.loop import MessageEnd

    async def _normal_events():
        yield MessageEnd(
            usage={"input_tokens": 0, "output_tokens": 0,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            stop_reason="end_turn",
            cost_usd=0.0,
        )

    wrapper = TurnAuditWrapper(
        _normal_events(),
        tenant_id=1, surface="dock", conversation_id="conv-late-aclose",
        model="claude-sonnet-4-6",
    )

    async def _drive():
        async for _ in wrapper:
            pass
        await wrapper.aclose()  # late aclose, AFTER MessageEnd consumed
        await wrapper.aclose()  # even-later double-call

    asyncio.run(_drive())

    with transaction() as con:
        rows = con.execute(
            "SELECT role FROM agent_conversations "
            "WHERE conversation_id = 'conv-late-aclose'",
        ).fetchall()
    assert len(rows) == 1
    assert dict(rows[0])["role"] == "assistant"
