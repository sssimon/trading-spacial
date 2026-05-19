"""Phase 2 conversation core — agentic loop tests.

Covers:

  - Simple text response → run_turn yields TextDelta + MessageEnd.
  - Tool use: the model emits a tool_use, the loop dispatches it,
    appends the assistant message ONCE and the tool_result in ONE
    user message (the wire-format invariant from pre-reg §6.1).
  - Multi-tool: two tool_use blocks in one turn → both dispatched,
    both results collected into ONE user message.
  - Hallucination guard at the dispatch layer: position_ids and
    symbols the model "invents" return not_found via the handler;
    we verify the loop surfaces that as is_error:true on the
    tool_result block.
  - Cache verification: usage.cache_read_input_tokens is propagated
    from the response into the MessageEnd.usage payload (the prompt
    cache hit-rate target in spec §14 reads from this field).
  - max_tool_hops: a model that loops indefinitely with tool_use
    gets stopped after MAX_TOOL_HOPS and emits an error event.
  - Conversation cap: messages longer than the cap → conversation_cap_reached.
  - Graceful failures: upstream exception → error event with friendly
    user_message, no 500 propagated.
  - Cost computation: matches the published per-1M pricing.

All tests use `FakeAnthropicClient` from tests/_fakes.py.
"""
from __future__ import annotations

import json

import pytest

from tests._fakes import FakeAnthropicClient, FakeTurnBuilder


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
    """Drain the async iterator into a list."""
    out = []
    async for ev in events_iter:
        out.append(ev)
    return out


# ── Simple text turn ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_run_turn_simple_text(tmp_db):
    from api.agent.loop import run_turn, TextDelta, MessageEnd
    c = FakeAnthropicClient()
    c.queue_turn(
        FakeTurnBuilder()
        .text("Tu portafolio está ")
        .text("en NORMAL.")
        .end_turn()
        .usage(input_tokens=120, output_tokens=10)
        .build()
    )
    msgs = [{"role": "user", "content": "¿cómo va el portafolio?"}]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))
    text_chunks = [e.text for e in events if isinstance(e, TextDelta)]
    ends = [e for e in events if isinstance(e, MessageEnd)]
    assert "".join(text_chunks) == "Tu portafolio está en NORMAL."
    assert len(ends) == 1
    assert ends[0].stop_reason == "end_turn"
    assert ends[0].usage["input_tokens"] == 120


# ── Tool dispatch (single tool) — wire-format invariant ────────────────


@pytest.mark.anyio
async def test_run_turn_dispatches_single_tool(tmp_db, monkeypatch):
    """The model emits propose tool_use → loop dispatches → appends
    assistant message ONCE → appends tool_result in ONE user message
    → second model turn returns text → MessageEnd."""
    from api.agent.loop import run_turn, TextDelta, ToolUseStart, ToolUseResult, MessageEnd
    from api.agent.tools import handlers as h

    captured: dict = {}

    def _stub_get_positions(*, tenant_id):
        captured["tenant_id"] = tenant_id
        return {"positions": [{"id": 1, "symbol": "BTC"}]}

    monkeypatch.setitem(h.TOOL_HANDLERS, "get_positions", _stub_get_positions)

    c = FakeAnthropicClient()
    # Turn 1: text + tool_use(get_positions) + stop_reason=tool_use
    c.queue_turn(
        FakeTurnBuilder()
        .text("Déjame consultar... ")
        .tool_use("get_positions", {}, tool_use_id="toolu_abc")
        .stop_tool_use()
        .build()
    )
    # Turn 2 (after tool dispatch): final text + end_turn
    c.queue_turn(
        FakeTurnBuilder()
        .text("Tienes 1 posición abierta de BTC.")
        .end_turn()
        .build()
    )

    msgs = [{"role": "user", "content": "qué posiciones tengo"}]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=42,
    ))

    # The handler was called with the JWT tenant_id, not whatever the
    # model might have sent (it can't — tenant_id isn't a tool input).
    assert captured["tenant_id"] == 42

    # Loop emitted: tool_use_start + tool_use_result + final text + end.
    starts = [e for e in events if isinstance(e, ToolUseStart)]
    results = [e for e in events if isinstance(e, ToolUseResult)]
    ends = [e for e in events if isinstance(e, MessageEnd)]
    assert [s.tool for s in starts] == ["get_positions"]
    assert [r.status for r in results] == ["ok"]
    assert len(ends) == 1

    # Wire-format invariant (pre-reg §6.1):
    #   - exactly ONE assistant message appended,
    #   - exactly ONE user message with tool_results follows.
    # The third entry onwards depends on the loop's second turn output;
    # we check the first three slots.
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assistant_content = msgs[1]["content"]
    assert isinstance(assistant_content, list)
    assert any(b["type"] == "tool_use" for b in assistant_content)
    assert msgs[2]["role"] == "user"
    user_content = msgs[2]["content"]
    assert isinstance(user_content, list)
    assert all(b["type"] == "tool_result" for b in user_content)
    assert len(user_content) == 1


# ── Multi-tool dispatch ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_run_turn_dispatches_multiple_tools_in_one_user_message(tmp_db, monkeypatch):
    """Two tool_use blocks in one model turn → both dispatched → BOTH
    tool_results in ONE user message (NOT split — the API rejects split
    tool_results)."""
    from api.agent.loop import run_turn
    from api.agent.tools import handlers as h

    def _stub_portfolio(*, tenant_id):
        return {"open_positions_count": 2}

    def _stub_positions(*, tenant_id):
        return {"positions": []}

    monkeypatch.setitem(h.TOOL_HANDLERS, "get_portfolio_overview", _stub_portfolio)
    monkeypatch.setitem(h.TOOL_HANDLERS, "get_positions", _stub_positions)

    c = FakeAnthropicClient()
    c.queue_turn(
        FakeTurnBuilder()
        .tool_use("get_portfolio_overview", {}, tool_use_id="toolu_1")
        .tool_use("get_positions", {}, tool_use_id="toolu_2")
        .stop_tool_use()
        .build()
    )
    c.queue_turn(
        FakeTurnBuilder()
        .text("Tienes 2 posiciones, 0 abiertas.")
        .end_turn()
        .build()
    )

    msgs = [{"role": "user", "content": "estado completo"}]
    await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))

    # After the loop runs: msgs[0]=user, msgs[1]=assistant (with TWO
    # tool_use blocks), msgs[2]=user (with TWO tool_result blocks
    # bundled together), msgs[3]=assistant (final text).
    assistant_content = msgs[1]["content"]
    tool_uses = [b for b in assistant_content if b["type"] == "tool_use"]
    assert len(tool_uses) == 2

    user_content = msgs[2]["content"]
    tool_results = [b for b in user_content if b["type"] == "tool_result"]
    assert len(tool_results) == 2
    assert {tr["tool_use_id"] for tr in tool_results} == {"toolu_1", "toolu_2"}


# ── Hallucinated position_id (defense-in-depth at dispatch) ────────────


@pytest.mark.anyio
async def test_run_turn_does_not_misdetect_error_on_legitimate_payload(tmp_db, monkeypatch):
    """PR #404 review issue 2: a tool can legitimately return a payload
    that mentions the substring "error" (e.g. an exit_reason value of
    "liquidation_error" or a state name of "no_error_state"). The
    previous `'"error"' in result_json` substring check produced a
    false positive here, telling the model the call failed when it
    didn't. Switched to JSON-parse + top-level "error" key check.
    """
    from api.agent.loop import run_turn, ToolUseResult
    from api.agent.tools import handlers as h

    monkeypatch.setitem(
        h.TOOL_HANDLERS, "get_recent_signals",
        lambda **kw: {"signals": [{"symbol": "BTC", "estado": "no_error_state"}]},
    )

    c = FakeAnthropicClient()
    c.queue_turn(
        FakeTurnBuilder()
        .tool_use("get_recent_signals", {})
        .stop_tool_use()
        .build()
    )
    c.queue_turn(FakeTurnBuilder().text("ok").end_turn().build())

    msgs = [{"role": "user", "content": "señales recientes"}]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))
    results = [e for e in events if isinstance(e, ToolUseResult)]
    # Substring match would have flagged this as an error; the JSON-parse
    # check correctly recognizes it as a normal payload.
    assert results == [ToolUseResult(tool="get_recent_signals", status="ok")]


@pytest.mark.anyio
async def test_run_turn_handles_hallucinated_position_id(tmp_db):
    """The model invents a position_id; the real handler returns
    not_found; the loop surfaces is_error:true on the tool_result so
    the model self-corrects on the next turn."""
    from api.agent.loop import run_turn, ToolUseResult

    c = FakeAnthropicClient()
    c.queue_turn(
        FakeTurnBuilder()
        .tool_use("get_position_detail", {"position_id": 9999})
        .stop_tool_use()
        .build()
    )
    c.queue_turn(
        FakeTurnBuilder()
        .text("Esa posición no existe.")
        .end_turn()
        .build()
    )

    msgs = [{"role": "user", "content": "detalles posición 9999"}]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))

    results = [e for e in events if isinstance(e, ToolUseResult)]
    assert results[0].status == "error"  # not_found → status=error

    # And the tool_result block carries is_error:true so the model knows.
    user_content = msgs[2]["content"]
    assert user_content[0]["is_error"] is True
    parsed = json.loads(user_content[0]["content"])
    assert parsed == {"error": "not_found"}


# ── Cache verification ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_run_turn_propagates_cache_read_tokens(tmp_db):
    """The MessageEnd event carries cache_read_input_tokens from the
    SDK's usage — this is the field the spec §14 cache-hit-rate target
    reads from."""
    from api.agent.loop import run_turn, MessageEnd

    c = FakeAnthropicClient()
    c.queue_turn(
        FakeTurnBuilder()
        .text("hola")
        .end_turn()
        .usage(input_tokens=10, output_tokens=2, cache_read=850, cache_creation=0)
        .build()
    )
    msgs = [{"role": "user", "content": "hi"}]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))
    end = next(e for e in events if isinstance(e, MessageEnd))
    assert end.usage["cache_read_input_tokens"] == 850
    assert end.usage["cache_creation_input_tokens"] == 0
    # Cost: 10 in × $3/1M + 850 cache_read × $3/1M × 0.1 + 2 out × $15/1M
    #     = 30e-6 + 255e-6 + 30e-6 = 315e-6
    assert end.cost_usd == pytest.approx(0.000315, abs=1e-7)


# ── Max tool hops ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_run_turn_max_tool_hops_emits_error(tmp_db, monkeypatch):
    """If the model keeps emitting tool_use indefinitely, the loop stops
    after MAX_TOOL_HOPS and yields an error event."""
    from api.agent.loop import run_turn, ErrorEvent, MAX_TOOL_HOPS
    from api.agent.tools import handlers as h

    monkeypatch.setitem(h.TOOL_HANDLERS, "get_positions", lambda **kw: {"positions": []})

    c = FakeAnthropicClient()
    # Queue MAX_TOOL_HOPS + 1 turns all emitting tool_use.
    for _ in range(MAX_TOOL_HOPS + 1):
        c.queue_turn(
            FakeTurnBuilder()
            .tool_use("get_positions", {}, tool_use_id=f"toolu_{_}")
            .stop_tool_use()
            .build()
        )

    msgs = [{"role": "user", "content": "loop forever"}]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert errors and errors[-1].reason == "too_many_tool_hops"


# ── Conversation cap ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_run_turn_conversation_cap_short_circuits(tmp_db):
    """messages > MAX_TURNS_PER_CONVERSATION → conversation_cap_reached
    error event immediately, without opening a stream."""
    from api.agent.loop import run_turn, ErrorEvent, MAX_TURNS_PER_CONVERSATION

    c = FakeAnthropicClient()  # no turns queued — proves we don't open one

    msgs = [
        {"role": "user", "content": f"msg {i}"}
        for i in range(MAX_TURNS_PER_CONVERSATION + 1)
    ]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1
    assert errors[0].reason == "conversation_cap_reached"
    # No stream was opened — proven by the fake having zero calls.
    assert c.calls == []


# ── Graceful upstream failures ─────────────────────────────────────────


@pytest.mark.anyio
async def test_run_turn_upstream_429_emits_error_no_raise(tmp_db):
    """A 429 from Anthropic → error event with friendly user_message.
    NO uncaught exception propagated to the HTTP layer."""
    from api.agent.loop import run_turn, ErrorEvent

    c = FakeAnthropicClient()
    c.queue_error(RuntimeError("rate-limited 429"))

    msgs = [{"role": "user", "content": "hola"}]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert errors and errors[0].reason == "upstream"
    # And the user-facing text is the friendly one (no env-var leak,
    # no stack trace).
    assert "saturado" in errors[0].user_message.lower() or \
           "intenta" in errors[0].user_message.lower()


# ── Cost model ─────────────────────────────────────────────────────────


def test_cost_estimation_matches_published_pricing():
    """The cost formula uses the published per-1M USD pricing for
    Sonnet 4.6 / Haiku 4.5 / Opus 4.7. If the cached pricing in
    api/agent/loop.py drifts vs Anthropic's published rates, this
    test catches it."""
    from api.agent.loop import _estimate_cost_usd
    # Sonnet 4.6: $3/1M input, $15/1M output. 1M input + 1M output = $18.
    cost = _estimate_cost_usd(
        "claude-sonnet-4-6",
        {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    assert cost == pytest.approx(18.0)
    # Haiku 4.5: $1/1M input, $5/1M output.
    cost = _estimate_cost_usd(
        "claude-haiku-4-5",
        {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    assert cost == pytest.approx(6.0)
    # Opus 4.7: $5/1M input, $25/1M output.
    cost = _estimate_cost_usd(
        "claude-opus-4-7",
        {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    assert cost == pytest.approx(30.0)


# ── System prompt blocks are cache-control'd ───────────────────────────


def test_system_blocks_carry_cache_control():
    """All four blocks must have cache_control: {type: ephemeral} so the
    Anthropic API serves them from cache. Pre-reg §7.5 silent invariant."""
    from api.agent.prompts import build_system_blocks
    blocks = build_system_blocks("dock")
    assert len(blocks) == 4
    for b in blocks:
        assert b["type"] == "text"
        assert b["cache_control"] == {"type": "ephemeral"}


def test_system_blocks_are_deterministic_across_calls():
    """Same surface → byte-identical output across calls. If this fails,
    the cache prefix shifts on every turn and the spec §14 cache-hit-rate
    target ≥70% is unreachable."""
    from api.agent.prompts import build_system_blocks
    a = build_system_blocks("dock")
    b = build_system_blocks("dock")
    assert a == b
    # And: dock vs symbol_detail differ only on block 4.
    c = build_system_blocks("symbol_detail")
    assert a[0] == c[0]  # persona
    assert a[2] == c[2]  # invariants
    assert a[3] != c[3]  # surface micro-prompt differs


# ── anyio fixture (project does not have a project-wide config) ────────


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ── E2E: POST /agent/conversations/{id}/turn via TestClient ────────────


@pytest.fixture
def authed_client(tmp_path, monkeypatch):
    """TestClient with the agent dependencies overridden: tenant_id=1,
    api-key-check bypassed, and the Anthropic client swapped for a
    FakeAnthropicClient that the test mutates."""
    import btc_api
    from fastapi.testclient import TestClient
    from auth.dependencies import get_current_tenant_id
    from api.agent.clients import get_anthropic_client

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-test-key")

    fake_client = FakeAnthropicClient()
    btc_api.app.dependency_overrides[get_current_tenant_id] = lambda: 1
    btc_api.app.dependency_overrides[get_anthropic_client] = lambda: fake_client
    try:
        yield TestClient(btc_api.app), fake_client
    finally:
        btc_api.app.dependency_overrides.pop(get_current_tenant_id, None)
        btc_api.app.dependency_overrides.pop(get_anthropic_client, None)


def test_endpoint_streams_text_deltas_as_sse_frames(authed_client):
    client, fake = authed_client
    fake.queue_turn(
        FakeTurnBuilder()
        .text("Hola ").text("mundo")
        .end_turn()
        .usage(input_tokens=10, output_tokens=2)
        .build()
    )
    resp = client.post(
        "/agent/conversations/test-conv-1/turn",
        json={
            "surface":  "dock",
            "messages": [{"role": "user", "content": "saluda"}],
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers.get("x-accel-buffering") == "no"

    body = resp.text
    # Parse the SSE frames — each starts with `data: ` and ends with `\n\n`.
    frames = [
        line[len("data: "):]
        for line in body.split("\n\n")
        if line.startswith("data: ")
    ]
    parsed = [json.loads(f) for f in frames]
    types = [p["type"] for p in parsed]

    assert "text_delta" in types
    assert types[-1] == "message_end"
    text_deltas = [p["text"] for p in parsed if p["type"] == "text_delta"]
    assert "".join(text_deltas) == "Hola mundo"
    end = next(p for p in parsed if p["type"] == "message_end")
    assert end["stop_reason"] == "end_turn"
    assert end["usage"]["input_tokens"] == 10


def test_endpoint_503_when_status_disabled(authed_client, monkeypatch):
    """If get_agent_status reports disabled (operator flipped the flag),
    the endpoint must 503 without invoking the model — even though the
    fake client is wired up."""
    client, fake = authed_client
    import api.agent.config as agent_cfg
    monkeypatch.setattr(
        agent_cfg, "load_config",
        lambda: {"agent": {"enabled": False}},
    )
    resp = client.post(
        "/agent/conversations/test-conv-2/turn",
        json={
            "surface":  "dock",
            "messages": [{"role": "user", "content": "hola"}],
        },
    )
    assert resp.status_code == 503
    assert resp.json() == {"detail": "agent_disabled"}
    # And the fake was never invoked — proves we short-circuited cleanly.
    assert fake.calls == []


def test_endpoint_400_when_model_not_allowed(authed_client):
    client, _fake = authed_client
    resp = client.post(
        "/agent/conversations/test-conv-3/turn",
        json={
            "surface":  "dock",
            "messages": [{"role": "user", "content": "hola"}],
            "model":    "gpt-4",   # not in the allowlist
        },
    )
    assert resp.status_code == 400
    assert resp.json() == {"detail": "model_not_allowed"}


# ── Audit writes — per-turn row in agent_conversations ─────────────────


def test_endpoint_audits_each_completed_turn(authed_client):
    """Every MessageEnd produces a row in agent_conversations with the
    turn's usage + cost + tenant_id + surface + conversation_id."""
    import btc_api
    client, fake = authed_client

    fake.queue_turn(
        FakeTurnBuilder()
        .text("Tu portafolio está en NORMAL.")
        .end_turn()
        .usage(input_tokens=150, output_tokens=12, cache_read=120)
        .build()
    )
    resp = client.post(
        "/agent/conversations/test-conv-audit-1/turn",
        json={
            "surface":  "kill_switch",
            "messages": [{"role": "user", "content": "estado"}],
        },
    )
    assert resp.status_code == 200
    # Consume the body so the StreamingResponse closes and the audit
    # wrapper runs the persistence step.
    _ = resp.text

    con = btc_api.get_db()
    try:
        rows = con.execute(
            "SELECT tenant_id, surface, conversation_id, role, model, "
            "input_tokens, output_tokens, cache_read_input_tokens, "
            "cost_usd, refused "
            "FROM agent_conversations WHERE conversation_id = ?",
            ("test-conv-audit-1",),
        ).fetchall()
    finally:
        con.close()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["tenant_id"] == 1
    assert row["surface"] == "kill_switch"
    assert row["role"] == "assistant"
    assert row["model"] == "claude-sonnet-4-6"
    assert row["input_tokens"] == 150
    assert row["output_tokens"] == 12
    assert row["cache_read_input_tokens"] == 120
    assert row["cost_usd"] is not None and row["cost_usd"] > 0
    assert row["refused"] == 0


def test_endpoint_audits_error_turns(authed_client):
    """Error events also produce an audit row (role='error', the reason
    is the content_summary). Lets us track error rate post-rollout."""
    import btc_api
    client, fake = authed_client

    fake.queue_error(RuntimeError("simulated 429"))
    resp = client.post(
        "/agent/conversations/test-conv-audit-err/turn",
        json={
            "surface":  "dock",
            "messages": [{"role": "user", "content": "hola"}],
        },
    )
    assert resp.status_code == 200
    _ = resp.text

    con = btc_api.get_db()
    try:
        rows = con.execute(
            "SELECT role, content_json, refused FROM agent_conversations "
            "WHERE conversation_id = ?",
            ("test-conv-audit-err",),
        ).fetchall()
    finally:
        con.close()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["role"] == "error"
    # content_json carries the reason enum value, JSON-encoded.
    assert json.loads(row["content_json"]) == "upstream"
    assert row["refused"] == 0  # upstream is not a refusal


def test_endpoint_503_when_api_key_missing_via_direct_curl(authed_client, monkeypatch):
    """PR #404 review issue 1 (BLOCKER): FastAPI resolves Depends() BEFORE
    the handler body. Without the status guard inside get_anthropic_client,
    a direct POST with ANTHROPIC_API_KEY missing would 500 with a
    KeyError stack trace — re-leaking the env-var name that #381 closed.

    This test reverts the dependency_overrides on get_anthropic_client so
    the real resolver runs, then unsets the env var. Must 503 with the
    closed-enum reason, NOT 500.
    """
    import btc_api
    from api.agent.clients import get_anthropic_client
    client, _fake = authed_client
    # Revert the test fake so the real resolver runs.
    btc_api.app.dependency_overrides.pop(get_anthropic_client, None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    resp = client.post(
        "/agent/conversations/bypass-test/turn",
        json={"surface": "dock", "messages": [{"role": "user", "content": "x"}]},
    )
    assert resp.status_code == 503
    assert resp.json() == {"detail": "agent_disabled"}
    # And the body must not leak the env-var name on this path either.
    for forbidden in ("ANTHROPIC_API_KEY", ".env", "restart"):
        assert forbidden not in resp.text, (
            f"/agent/conversations bypass-test leaked {forbidden!r}: {resp.text!r}"
        )


def test_endpoint_400_when_conversation_id_invalid(authed_client):
    """PR #404 review issue 3: conversation_id must be alphanumeric +
    _-, max 128 chars. Blocks pollution and minor DoS."""
    client, fake = authed_client
    # No queue — request should 422 on path validation before hitting the loop.
    # Note: control characters (\n, \r, \0) are rejected by httpx URL
    # validation before they reach the server, so we don't test them
    # at the endpoint level — that's a separate layer of defense.
    cases = [
        "a" * 200,           # too long
        "with/slash",        # path separator
        "with space",        # whitespace (httpx encodes; FastAPI Path() rejects)
        "",                  # empty
    ]
    for bad in cases:
        resp = client.post(
            f"/agent/conversations/{bad}/turn",
            json={"surface": "dock", "messages": [{"role": "user", "content": "x"}]},
        )
        # FastAPI returns 422 on path validation failures; 404 if the path
        # itself doesn't match the route (empty string case). Either is
        # acceptable — what matters is we never reach the loop.
        assert resp.status_code in (404, 422), (
            f"conversation_id={bad!r} should be rejected, got {resp.status_code}"
        )
    assert fake.calls == []  # zero turns opened across all attempts


def test_endpoint_audits_isolated_per_tenant(authed_client, monkeypatch):
    """Two turns from two tenants → two rows, each scoped to its tenant.
    Multi-tenant invariant for the audit row itself (pre-reg §8)."""
    import btc_api
    from auth.dependencies import get_current_tenant_id

    # First turn as tenant=1
    client, fake = authed_client
    fake.queue_turn(
        FakeTurnBuilder().text("turn-1").end_turn().usage(input_tokens=10).build()
    )
    resp = client.post(
        "/agent/conversations/iso-conv/turn",
        json={
            "surface":  "dock",
            "messages": [{"role": "user", "content": "msg-1"}],
        },
    )
    assert resp.status_code == 200
    _ = resp.text

    # Swap the override to tenant=2 for the second turn.
    btc_api.app.dependency_overrides[get_current_tenant_id] = lambda: 2
    fake.queue_turn(
        FakeTurnBuilder().text("turn-2").end_turn().usage(input_tokens=20).build()
    )
    resp = client.post(
        "/agent/conversations/iso-conv-tenant2/turn",
        json={
            "surface":  "dock",
            "messages": [{"role": "user", "content": "msg-2"}],
        },
    )
    assert resp.status_code == 200
    _ = resp.text

    con = btc_api.get_db()
    try:
        rows = con.execute(
            "SELECT tenant_id, conversation_id, input_tokens "
            "FROM agent_conversations ORDER BY id ASC"
        ).fetchall()
    finally:
        con.close()
    assert len(rows) == 2
    assert dict(rows[0])["tenant_id"] == 1
    assert dict(rows[0])["conversation_id"] == "iso-conv"
    assert dict(rows[0])["input_tokens"] == 10
    assert dict(rows[1])["tenant_id"] == 2
    assert dict(rows[1])["conversation_id"] == "iso-conv-tenant2"
    assert dict(rows[1])["input_tokens"] == 20
