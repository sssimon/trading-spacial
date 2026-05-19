"""GET /agent/status — no-leak contract + state precedence.

Phase 0 of the production-grade copilot rewrite (epic #400). The status
endpoint is the single source of truth the frontend reads to decide
whether to render the copilot UI. Its body MUST NOT leak env-var names,
.env paths, or any operator-only configuration detail — those strings
would let an unauthenticated visitor map the server's deployment.
"""
from __future__ import annotations

import pytest


# Strings that must NEVER appear in the response body of /agent/status,
# /agent/chat 503, or any other agent-related public surface. Lifted
# verbatim from pre-reg §11.7.
_FORBIDDEN_LEAK_STRINGS = (
    "ANTHROPIC_API_KEY",
    ".env",
    "/.env",
    "restart",
    "configure",
    "config.json",
    "Set it in",
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Same DB-isolation pattern as tests/test_health_dashboard.py."""
    import btc_api
    from fastapi.testclient import TestClient
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    return TestClient(btc_api.app)


# ── /agent/status: enabled when ANTHROPIC_API_KEY is set ────────────────


def test_agent_status_enabled_when_api_key_set(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-test-key")
    resp = client.get("/agent/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"enabled": True, "reason": "ok"}


# ── /agent/status: disabled when ANTHROPIC_API_KEY is missing ───────────


def test_agent_status_disabled_when_api_key_missing(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = client.get("/agent/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"enabled": False, "reason": "agent_disabled"}


def test_agent_status_disabled_when_api_key_empty(client, monkeypatch):
    """Empty string is treated identically to missing — see config.py."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    resp = client.get("/agent/status")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "reason": "agent_disabled"}


def test_agent_status_disabled_when_api_key_whitespace(client, monkeypatch):
    """Whitespace-only is treated as missing — operator typo guard."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    resp = client.get("/agent/status")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "reason": "agent_disabled"}


# ── /agent/status: disabled when cfg.agent.enabled is False ─────────────


def test_agent_status_disabled_when_cfg_disabled(client, monkeypatch):
    """Operator-driven disable wins even if ANTHROPIC_API_KEY is set.

    The router resolves load_config via the local reference imported in
    api/agent/config.py (`from api.config import load_config`). That's the
    only patch point that affects the flow; patching btc_api.load_config
    would be a no-op for this code path.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-test-key")
    import api.agent.config as agent_cfg
    monkeypatch.setattr(
        agent_cfg, "load_config",
        lambda: {"agent": {"enabled": False}},
    )
    resp = client.get("/agent/status")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "reason": "agent_disabled"}


# ── No-leak contract (the load-bearing test for #381) ───────────────────


def test_agent_status_body_never_leaks_env_var_names_or_paths(client, monkeypatch):
    """The response body MUST NOT contain any of the forbidden leak strings
    regardless of the state. This is the regression test for #381."""
    for key_value in (None, "", "sk-ant-real-key"):
        if key_value is None:
            monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        else:
            monkeypatch.setenv("ANTHROPIC_API_KEY", key_value)
        resp = client.get("/agent/status")
        body_text = resp.text
        for forbidden in _FORBIDDEN_LEAK_STRINGS:
            assert forbidden not in body_text, (
                f"/agent/status leaked forbidden string {forbidden!r} "
                f"with ANTHROPIC_API_KEY={key_value!r}. Body: {body_text!r}"
            )


def test_agent_chat_503_body_no_longer_leaks(client, monkeypatch):
    """The legacy /agent/chat endpoint also stops leaking — pre-reg §3.3."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = client.post(
        "/agent/chat",
        json={"system": "test", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 503
    body_text = resp.text
    for forbidden in _FORBIDDEN_LEAK_STRINGS:
        assert forbidden not in body_text, (
            f"/agent/chat 503 leaked forbidden string {forbidden!r}. "
            f"Body: {body_text!r}"
        )
    # And it carries the closed-enum reason instead of the prose leak.
    assert resp.json() == {"detail": "agent_disabled"}


def test_agent_chat_503_when_cfg_disabled_even_with_api_key(client, monkeypatch):
    """Closes the cfg-vs-env consistency gap flagged in PR #402 review:
    if the operator sets cfg.agent.enabled=False but leaves the env var
    populated, /agent/chat must 503 (same way /agent/status does) instead
    of silently letting the request through to Anthropic.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-but-real-looking")
    import api.agent.config as agent_cfg
    monkeypatch.setattr(
        agent_cfg, "load_config",
        lambda: {"agent": {"enabled": False}},
    )
    resp = client.post(
        "/agent/chat",
        json={"system": "test", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 503
    assert resp.json() == {"detail": "agent_disabled"}


# ── Closed-enum invariant ───────────────────────────────────────────────


def test_agent_status_reason_field_is_closed_enum(client, monkeypatch):
    """`reason` must be one of the documented values. Anything else is a
    regression — likely an operator-only string sneaking through."""
    allowed = {"ok", "agent_disabled"}
    for key_value in (None, "", "sk-ant-real-key"):
        if key_value is None:
            monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        else:
            monkeypatch.setenv("ANTHROPIC_API_KEY", key_value)
        resp = client.get("/agent/status")
        reason = resp.json()["reason"]
        assert reason in allowed, (
            f"Unknown agent status reason {reason!r} — closed enum is {allowed}"
        )


# ── Unauthenticated access ──────────────────────────────────────────────


def test_agent_status_does_not_require_auth(client, monkeypatch):
    """The frontend reads /agent/status before login completes in some
    flows; the endpoint must respond without an auth cookie."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    # No login, no cookies — should still succeed.
    resp = client.get("/agent/status")
    assert resp.status_code == 200
