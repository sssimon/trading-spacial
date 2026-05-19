"""Phase 5 of epic #400 — metrics endpoint + router 429/503 + pickups.

Covers:
  - GET /agent/metrics requires admin role (403 for viewer)
  - GET /agent/metrics returns the documented shape
  - POST /agent/conversations/{id}/turn 503s when breaker is tripped
  - POST /agent/conversations/{id}/turn 429s when tenant quota exceeded
  - sse_serialize emits a keepalive frame after the idle window
  - TurnAuditWrapper records a cancellation row when the iterator ends
    without a terminal MessageEnd/ErrorEvent
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    yield db_path


def _fake_user(*, id: int = 1, role: str = "admin"):
    from auth.models import User
    return User(
        id=id, email=f"u{id}@example.com", role=role, is_active=True,
        created_at="2026-05-19T00:00:00+00:00",
        password_changed_at="2026-05-19T00:00:00+00:00",
    )


@pytest.fixture
def admin_client(tmp_db, monkeypatch):
    import btc_api
    from fastapi.testclient import TestClient
    from auth.dependencies import get_current_tenant_id, get_current_user

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-test-key")
    monkeypatch.setenv("AGENT_PROPOSAL_SECRET", "test-only-secret")
    btc_api.app.dependency_overrides[get_current_tenant_id] = lambda: 1
    btc_api.app.dependency_overrides[get_current_user] = lambda: _fake_user(id=1, role="admin")
    try:
        yield TestClient(btc_api.app)
    finally:
        btc_api.app.dependency_overrides.pop(get_current_tenant_id, None)
        btc_api.app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def viewer_client(tmp_db, monkeypatch):
    import btc_api
    from fastapi.testclient import TestClient
    from auth.dependencies import get_current_tenant_id, get_current_user

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-test-key")
    btc_api.app.dependency_overrides[get_current_tenant_id] = lambda: 2
    btc_api.app.dependency_overrides[get_current_user] = lambda: _fake_user(id=2, role="viewer")
    try:
        yield TestClient(btc_api.app)
    finally:
        btc_api.app.dependency_overrides.pop(get_current_tenant_id, None)
        btc_api.app.dependency_overrides.pop(get_current_user, None)


def _seed_turn(*, tenant_id: int = 1, cost_usd: float = 0.10, role: str = "assistant",
               hours_ago: float = 1, refused: bool = False,
               content_summary: str | None = None):
    import btc_api
    import json as _json
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    con = btc_api.get_db()
    try:
        con.execute(
            "INSERT INTO agent_conversations "
            "(tenant_id, surface, conversation_id, ts, role, model, "
            " input_tokens, output_tokens, cache_read_input_tokens, "
            " cache_creation_input_tokens, latency_ms, cost_usd, "
            " content_json, refused) "
            "VALUES (?, 'dock', 'c1', ?, ?, 'claude-sonnet-4-6', "
            "        100, 50, 0, 0, 1000, ?, ?, ?)",
            (tenant_id, ts, role, cost_usd,
             _json.dumps(content_summary) if content_summary else None,
             1 if refused else 0),
        )
        con.commit()
    finally:
        con.close()


# ── GET /agent/metrics ─────────────────────────────────────────────


def test_metrics_requires_admin(viewer_client):
    """Viewer-role gets 403; the closed-enum detail mentions 'admin'
    but doesn't leak the cap, env-var name, etc."""
    resp = viewer_client.get("/agent/metrics")
    assert resp.status_code == 403
    body = resp.json()
    # require_role's detail is "role 'admin' required" — admin keyword
    # is fine to leak (it's the closed-enum role name), but no secrets.
    assert "admin" in body.get("detail", "")
    assert "ANTHROPIC_API_KEY" not in str(body)


def test_metrics_returns_documented_shape(admin_client):
    """Empty DB → all-zero metrics with the documented shape."""
    resp = admin_client.get("/agent/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "breaker", "today", "top_tenants", "error_breakdown_24h",
    }
    assert set(body["breaker"].keys()) == {"tripped", "reason", "global_24h_usd"}
    assert body["breaker"]["tripped"] is False
    assert body["breaker"]["reason"] == "ok"
    assert body["breaker"]["global_24h_usd"] == 0.0
    assert body["today"] == {
        "turn_count": 0, "error_count": 0, "refused_count": 0, "total_usd": 0.0,
    }
    assert body["top_tenants"] == []
    assert body["error_breakdown_24h"] == []


def test_metrics_aggregates_today_correctly(admin_client):
    """Seed: 2 assistant turns (cost $0.05 + $0.10), 1 error, 1 refused.
    Metrics reflect each."""
    _seed_turn(tenant_id=1, cost_usd=0.05, role="assistant", hours_ago=1)
    _seed_turn(tenant_id=1, cost_usd=0.10, role="assistant", hours_ago=2)
    _seed_turn(tenant_id=1, cost_usd=0.0,  role="error", hours_ago=3,
                content_summary="upstream")
    _seed_turn(tenant_id=2, cost_usd=0.0,  role="error", hours_ago=4,
                refused=True, content_summary="too_many_tool_hops")

    resp = admin_client.get("/agent/metrics")
    assert resp.status_code == 200
    body = resp.json()

    assert body["today"]["turn_count"] == 4
    assert body["today"]["error_count"] == 2
    assert body["today"]["refused_count"] == 1
    assert abs(body["today"]["total_usd"] - 0.15) < 1e-9

    # top_tenants sorted desc by usd_24h
    assert len(body["top_tenants"]) == 2
    assert body["top_tenants"][0]["tenant_id"] == 1
    assert body["top_tenants"][1]["tenant_id"] == 2

    reasons = {e["reason"] for e in body["error_breakdown_24h"]}
    assert reasons == {"upstream", "too_many_tool_hops"}


def test_metrics_breaker_field_reflects_status(admin_client, monkeypatch):
    """When breaker is tripped, the field reflects it without raising."""
    import api.agent.config as agent_cfg
    monkeypatch.setattr(
        agent_cfg, "load_config",
        lambda: {"agent": {"enabled": True, "breaker_open": True}},
    )
    resp = admin_client.get("/agent/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["breaker"]["tripped"] is True
    assert body["breaker"]["reason"] == "breaker_open"


# ── /turn pre-flight quota + breaker ──────────────────────────────


def test_turn_503_when_breaker_open(admin_client, monkeypatch):
    """POST /agent/conversations/.../turn returns 503 with detail=
    'breaker_open' when the global breaker is tripped. The path lives
    in get_agent_status, so this also locks the integration."""
    import api.agent.config as agent_cfg
    monkeypatch.setattr(
        agent_cfg, "load_config",
        lambda: {"agent": {"enabled": True, "breaker_open": True}},
    )
    resp = admin_client.post(
        "/agent/conversations/c1/turn",
        json={"surface": "dock", "messages": [{"role": "user", "content": "hola"}]},
    )
    assert resp.status_code == 503
    assert resp.json() == {"detail": "breaker_open"}


def test_turn_429_when_tenant_quota_exceeded(admin_client):
    """Seed tenant 1 at cap → POST /turn returns 429 quota_exceeded.

    Overrides get_anthropic_client because the test runner does not
    have the anthropic SDK installed; the real Depends would 503
    before the quota check runs. The fake never actually has to do
    anything — the 429 fires inside the handler, before the loop runs.
    """
    import btc_api
    from api.agent.clients import get_anthropic_client
    from api.agent import quotas as _quotas

    btc_api.app.dependency_overrides[get_anthropic_client] = lambda: object()
    try:
        today = _quotas._today_iso()
        month = _quotas._this_month_iso()
        con = btc_api.get_db()
        try:
            con.execute(
                """INSERT INTO agent_quotas
                   (tenant_id, daily_usd_used, daily_usd_cap, daily_window_start,
                    monthly_usd_used, monthly_window_start)
                   VALUES (1, 1.00, 1.00, ?, 1.00, ?)""",
                (today, month),
            )
            con.commit()
        finally:
            con.close()

        resp = admin_client.post(
            "/agent/conversations/c1/turn",
            json={"surface": "dock", "messages": [{"role": "user", "content": "hola"}]},
        )
        assert resp.status_code == 429
        assert resp.json() == {"detail": "quota_exceeded"}
    finally:
        btc_api.app.dependency_overrides.pop(get_anthropic_client, None)


# ── sse_serialize keepalive ────────────────────────────────────────


def test_sse_serialize_emits_keepalive_after_idle(tmp_db):
    """A slow producer (idle for >= keepalive_seconds between events)
    must get a keepalive frame inserted. Use a tiny timeout so the test
    doesn't actually wait 30s."""
    from api.agent.streaming import sse_serialize
    from api.agent.loop import TextDelta, MessageEnd

    async def _slow_events():
        # Frame 1: immediate
        yield TextDelta(text="hi")
        # Frame 2: after 200ms idle → keepalive will fire if cadence < 200ms
        await asyncio.sleep(0.20)
        yield MessageEnd(
            usage={"input_tokens": 0, "output_tokens": 0,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            stop_reason="end_turn",
            cost_usd=0.0,
        )

    async def _drive():
        frames: list[bytes] = []
        async for f in sse_serialize(_slow_events(), keepalive_seconds=0.05):
            frames.append(f)
        return frames

    frames = asyncio.run(_drive())
    rendered = b"".join(frames).decode("utf-8")
    # Includes the text_delta + at least one keepalive + the message_end.
    assert '"type": "text_delta"' in rendered
    assert '"type": "keepalive"' in rendered
    assert '"type": "message_end"' in rendered


def test_sse_serialize_no_keepalive_when_events_flow(tmp_db):
    """Back-to-back events with no idle gap → no keepalive frame."""
    from api.agent.streaming import sse_serialize
    from api.agent.loop import TextDelta, MessageEnd

    async def _fast_events():
        yield TextDelta(text="a")
        yield TextDelta(text="b")
        yield MessageEnd(
            usage={"input_tokens": 0, "output_tokens": 0,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            stop_reason="end_turn",
            cost_usd=0.0,
        )

    async def _drive():
        frames: list[bytes] = []
        async for f in sse_serialize(_fast_events(), keepalive_seconds=10.0):
            frames.append(f)
        return frames

    frames = asyncio.run(_drive())
    rendered = b"".join(frames).decode("utf-8")
    assert '"type": "keepalive"' not in rendered


# ── TurnAuditWrapper cancellation row ──────────────────────────────


def test_audit_wrapper_records_cancelled_when_iterator_ends_early(tmp_db):
    """If the producer ends without emitting MessageEnd/ErrorEvent
    (cancellation, disconnect, abrupt close), the wrapper records a
    synthetic 'cancelled' error row so the audit table reflects the
    failure mode."""
    import btc_api
    from api.agent.audit import TurnAuditWrapper

    async def _empty_events():
        # Empty body — yield NOTHING, then end (StopAsyncIteration).
        if False:  # pragma: no cover — ensures async generator
            yield None
        return

    wrapper = TurnAuditWrapper(
        _empty_events(),
        tenant_id=1, surface="dock", conversation_id="conv-cancel",
        model="claude-sonnet-4-6",
    )

    async def _drive():
        try:
            async for _ in wrapper:
                pass
        except StopAsyncIteration:
            pass

    asyncio.run(_drive())

    # Audit row exists with role='error' and content_summary='cancelled'.
    con = btc_api.get_db()
    try:
        row = con.execute(
            "SELECT role, content_json FROM agent_conversations "
            "WHERE conversation_id = 'conv-cancel'",
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    d = dict(row)
    assert d["role"] == "error"
    import json as _json
    assert _json.loads(d["content_json"]) == "cancelled"


def test_audit_wrapper_does_not_double_record_on_normal_end(tmp_db):
    """A clean MessageEnd → exactly ONE row (the assistant audit), NOT
    one + cancellation. Belt-and-suspenders against the new aclose
    path firing redundantly."""
    import btc_api
    from api.agent.audit import TurnAuditWrapper
    from api.agent.loop import MessageEnd

    async def _normal_events():
        yield MessageEnd(
            usage={"input_tokens": 0, "output_tokens": 0,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            stop_reason="end_turn",
            cost_usd=0.05,
        )

    wrapper = TurnAuditWrapper(
        _normal_events(),
        tenant_id=1, surface="dock", conversation_id="conv-normal",
        model="claude-sonnet-4-6",
    )

    async def _drive():
        async for _ in wrapper:
            pass
        # Simulate FastAPI calling aclose on response teardown.
        await wrapper.aclose()

    asyncio.run(_drive())

    con = btc_api.get_db()
    try:
        rows = con.execute(
            "SELECT role FROM agent_conversations WHERE conversation_id = 'conv-normal'",
        ).fetchall()
    finally:
        con.close()
    assert len(rows) == 1
    assert dict(rows[0])["role"] == "assistant"
